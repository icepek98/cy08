# CY08 — Backend Stripe Billing

## Estado

**Implementação local preparada para revisão; integração não pronta para produção.** O código abaixo ainda depende de uma migration local, de execução/testes com Deno e PostgreSQL isolado, de decisões P-01–P-06 e de confirmação operacional das migrations remotas. Nenhuma migration foi aplicada remotamente e nenhum Stripe API request/payment foi realizado.

## Ciclo da integração

```text
Desktop autenticado
  -> POST Supabase Edge Function /stripe-checkout
  -> verifica Supabase Auth e deriva o tenant por tenant_accounts.user_id
  -> valida licença trialing + ausência de assinatura Stripe existente
  -> cria/reutiliza Customer com idempotency key por tenant
  -> cria Checkout Session hosted com único Price ID do servidor
  -> cliente redireciona para Stripe; retorno do Checkout não autoriza acesso

Stripe
  -> POST /stripe-webhook (assinatura Stripe; sem JWT Supabase)
  -> verifica assinatura no corpo bruto
  -> registra evento único, adquire lease com geração/fencing
  -> recupera Subscription atual e, para status active, a Invoice atual
  -> verifica Customer vinculado ao tenant e Price allowlisted
  -> RPC transacional valida a transição e chama a projeção 20261008
  -> confirma evento na mesma transação ou registra pending_policy
```

O `tenant_id` pode aparecer no sistema apenas após ser derivado da identidade autenticada pelo backend. Webhook correlaciona pelo Stripe Customer armazenado; não confia em metadata do Checkout. A tabela de inbox guarda apenas IDs, tipo, timestamps, tentativas e estado, nunca o corpo bruto.

## Endpoints locais

### `POST /functions/v1/stripe-checkout`

- Exige Bearer token Supabase válido e corpo JSON vazio.
- Aceita apenas tenant resolvido por `tenant_accounts` e licença cujo status efetivo seja `trialing`.
- Recusa assinatura Stripe já associada; renovações e outros estados de licença permanecem bloqueados.
- Usa `STRIPE_PRICE_ID`, `STRIPE_SUCCESS_URL` e `STRIPE_CANCEL_URL` apenas do ambiente servidor.
- Cria Checkout em `mode=subscription`, quantidade fixa 1, hosted redirect e sem `payment_method_types` ou trial Stripe.
- Customer usa chave de idempotência por tenant. Uma reserva privada por tenant serializa tentativas concorrentes de Checkout, persiste os parâmetros usados na chamada Stripe, reutiliza a URL até a expiração da sessão em 23 horas e aplica fencing ao salvar a sessão.
- Retorna somente a URL hospedada. O sucesso de Checkout não ativa licença.

Esta primeira etapa não oferece alteração/cancelamento de assinatura nem Customer Portal. A migration de reserva precisa ser aplicada e validada no PostgreSQL isolado antes de implantar o endpoint.

### `POST /functions/v1/stripe-webhook`

- JWT Supabase desabilitado somente para esta função; a assinatura Stripe é obrigatória e validada pelo SDK no corpo bruto.
- Antes de registrar o evento, valida os timestamps Unix dos eventos suportados e a forma completa dos IDs Customer, Subscription e Invoice; uma referência de Subscription presente, mas inválida, é rejeitada como payload malformado.
- Eventos aceitos: `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid` e `invoice.payment_failed`.
- `checkout.session.completed` não é prova de pagamento e não ativa licença.
- A função recupera a Subscription atual do Stripe para cada evento. Se a subscription está `active`, também recupera a última Invoice e exige que ela esteja paga e associada à mesma subscription/customer antes de projetar acesso ativo.
- Falha temporária de rede/Stripe/Supabase retorna HTTP 503 para permitir retry; o lease expirado permite nova tentativa e a geração impede que um worker antigo conclua após novo processamento.
- Eventos repetidos já processados retornam HTTP 200; eventos com estado/política pendentes são persistidos e reconhecidos sem alterar licença.
- Eventos suportados com ID/Customer/Subscription malformado retornam HTTP 400; invoices válidas sem associação a uma Subscription são reconhecidas como irrelevantes. Os eventos suportados precisam ser configurados no Stripe Dashboard durante implantação autorizada.

Não estão implementados eventos de disputa, reembolso e alerta antecipado de fraude. A orientação Stripe para produção exige avaliar e processar esses eventos antes de declarar o produto pronto.

## Transições de licença

A nova RPC transacional só projeta:

- `active` quando a licença atual é `trialing` sem assinatura anterior, ou já é `active` com a mesma subscription; exige última Invoice Stripe confirmada como paga;
- `past_due` de `trialing`, `active` ou `past_due`, seguindo a regra A-02 de bloqueio;
- `canceled` somente para licença `active` vinculada à mesma subscription, preservando o período futuro conforme a projeção SQL preexistente.

Se uma Subscription confirmada estiver `past_due`, mas seu Price não estiver allowlisted ou o snapshot não tiver um único período utilizável, a RPC ainda move a licença elegível para `past_due` e conclui o evento atomicamente; não altera o snapshot Stripe armazenado. Isso prioriza o bloqueio A-02 e grava um código de reconciliação para a divergência.

Estados `trialing`, `unpaid`, `incomplete`, `incomplete_expired` e `paused` não são projetados. Transições de `past_due` para `active`, de `expired` para `active`, troca de assinatura, ou alteração de licença `blocked`/`revoked` ficam em `pending_policy`. O trigger de restrição não é removido nem contornado.

`pending_policy` é uma retenção segura, não uma fila com ação de suporte implementada. É necessária reconciliação/processamento administrativo futuro para recuperar esses eventos depois de uma deliberação formal. P-01–P-06 continuam pendentes.

## Migrations

- As migrations históricas `20261008`–`20261012` não foram editadas.
- Criada `20261013_create_stripe_webhook_inbox.sql`, que adiciona inbox com ID de evento único, lease por Customer com fencing, RLS, funções privilegiadas e projeção/evento na mesma chamada transacional.
- Criada `20261014_reserve_stripe_checkout_sessions.sql`, que adiciona reserva privada por tenant, lease/fencing, persistência de parâmetros de idempotência e da URL, reuso de sessão e validação transacional de elegibilidade.
- As migrations novas dependem da estrutura de `20261008` e `20261009`. A ordem/estado remotos não foi confirmada nesta etapa.
- A tabela e os leases não concedem acesso direto a `PUBLIC`, `anon`, `authenticated` ou `service_role`; somente funções `SECURITY DEFINER` com `search_path = ''` recebem `EXECUTE` de `service_role`.
- O código Edge usa a service-role somente no servidor. Não conceder essa chave à aplicação desktop.

## Configuração do servidor

Variáveis necessárias (nomes apenas; nunca versionar valores):

- `STRIPE_SECRET_KEY` — preferir restricted API key com permissões mínimas de Customers, Checkout Sessions, Subscriptions e Invoices necessárias ao backend.
- `STRIPE_WEBHOOK_SECRET` — segredo de assinatura para este endpoint.
- `STRIPE_PRICE_ID` — único Price recorrente aceito pelo backend.
- `STRIPE_SUCCESS_URL` — HTTPS com `{CHECKOUT_SESSION_ID}`.
- `STRIPE_CANCEL_URL` — HTTPS.
- `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `SUPABASE_SERVICE_ROLE_KEY` — configuração interna do Supabase Edge; service-role permanece só no servidor.

O Stripe SDK é importado na função Deno por `npm:stripe@23.0.0`; não foi adicionado ao pacote da aplicação desktop. O SDK é configurado para API `2026-09-30.endive`. O arquivo local `supabase/config.toml` mantém JWT obrigatório no Checkout e o desabilita apenas no webhook.

**Stripe Tax:** não foi habilitado `automatic_tax`; antes de cobrar clientes, confirme obrigações fiscais, país/jurisdições, registros ativos e tax codes. Habilitar o cálculo sem registro apropriado pode não coletar imposto.

## Testes e ambiente local

Testes contratuais disponíveis:

```text
.venv/bin/python -m unittest tests.test_stripe_billing_backend_contract
.venv/bin/python -m unittest tests.test_tenant_stripe_billing_migration tests.test_tenant_license_restriction_migration tests.test_tenant_installations_migration tests.test_tenant_authorization tests.test_license_contract
.venv/bin/python -m unittest discover -s tests
```

O roteiro `tests/sql/test_stripe_checkout_reservations.sql` exercita a reserva quando executado em PostgreSQL isolado. Os testes Python contratuais verificam código/configuração, mas não executam TypeScript/Deno nem SQL.

Procedimento local futuro: iniciar Supabase local isolado, aplicar migrations localmente na ordem validada, configurar apenas segredos de sandbox em ambiente não versionado, servir `stripe-checkout` e `stripe-webhook`, e usar eventos/cartões de teste. Nenhuma etapa local dessas foi executada nesta implementação.

## Retry, atomicidade e limitações

- Evento não pode ser marcado `processed` antes da chamada da projeção. A RPC de aplicação invoca a projeção 20261008 e confirma o evento na mesma transação PostgreSQL.
- Eventos fora de ordem levam à recuperação do estado atual Stripe após adquirir lease por Customer. A geração monotônica invalida conclusões atrasadas de leases anteriores.
- Falhas antes de gravar o estado do evento deixam o evento `processing`; uma entrega posterior pode retomar após expiração do lease. `attempt_count` acompanha as tentativas de aquisição.
- `pending_mapping` significa Customer sem associação local; `pending_policy` registra ID/código seguro sem guardar payload. Não há painel/admin RPC de retry.
- Os endpoints não foram executados. Deve-se testar runtime Deno, chamadas Supabase locais, compatibilidade do Stripe SDK/API version, assinatura válida/inválida, idempotência, concorrência, RLS e rollback em PostgreSQL isolado antes de deploy.

## Deploy futuro (revisão necessária; não executar nesta etapa)

1. Deliberar formalmente P-01–P-06 e atualizar a matriz de transições aprovada sem alterar `blocked`/`revoked` por suposição.
2. Confirmar remotamente, somente por consultas read-only, o estado e os dados das migrations 20261008–20261012 e a ausência de conflitos.
3. Reproduzir localmente migrations 20261008–20261014 numa instância PostgreSQL descartável; provar ACLs, transações, trigger, fencing e cenários de falha concorrente.
4. Aprovar a revisão do SQL e do backend; configurar webhook Stripe com apenas os eventos acima e ambientes/segredos separados.
5. Planejar implantação com backup/rollback compatível com os dados; não usar `supabase db push` sem aprovação explícita para cada etapa. Não sincronizar histórico automaticamente.
6. Pós-deploy autorizado: verificar catálogos, RLS/ACLs, `search_path`, objetos, histórico, webhook deliveries, retries e projeção de licença, sem testes de compra real.

Gate 0 permanece aberto. A-01–A-07 permanecem preservadas. Não há aprovação humana de P-01–P-06 nesta implementação.
