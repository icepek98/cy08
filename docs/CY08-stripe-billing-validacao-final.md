# CY08 — Validação final do backend Stripe Billing

## Validação das Edge Functions — 2026-10-21, 17:39

### Estado verificado

- Raiz: `/home/usuario/Desktop/cy08`.
- A pasta não é um repositório Git; não foi possível obter status/diff nem assegurar ausência de alterações locais anteriores. A busca local não encontrou `AGENTS.md`.
- Ferramentas: Node.js `v20.18.0`, npm `10.8.2`, Python `3.11.2` e `psql 15.19`; Deno e Supabase CLI indisponíveis.
- `package.json`/`package-lock.json` existem e `node_modules/` existe, mas não há Deno config/import map na raiz ou nas funções, nem `node_modules/.bin/tsc`. Não foram instalados pacotes nem ferramentas.
- As duas Edge Functions, as migrations 20261013/20261014, o teste contratual Python, o roteiro SQL, `supabase/config.toml` e este relatório foram inspecionados.

### Resultados executados

| Comando | Resultado | O que valida |
|---|---|---|
| `.venv/bin/python -m unittest tests.test_stripe_billing_backend_contract` | 23 testes passaram | Asserções contratuais estáticas para handlers, autenticação, assinatura, identificadores inválidos, licença, idempotência/lease e erros |
| `.venv/bin/python -m unittest discover -s tests` | 572 testes passaram, 0 falharam, 0 ignorados | Suíte Python completa; não compila nem executa Deno |

Não foi executado `deno check`: o executável Deno não está instalado. A CLI Supabase também está indisponível, então não foi iniciada função Edge local nem runtime PostgREST/Auth. Não houve tentativa de instalação.

### Conclusões por inspeção estática, não por runtime

- Checkout exige POST JSON vazio e bearer; consulta `auth.getUser` com o token e deriva tenant via `resolve_tenant_for_billing` usando o ID autenticado. `supabase/config.toml` exige JWT na função.
- A resposta de Checkout fornece apenas `checkout_url`; na criação de sessão, só retorna sucesso após a RPC confirmar persistência. O Checkout não aplica/grava estado de licença.
- O webhook valida o corpo bruto por `constructEventAsync` antes de acessar o objeto, rejeita assinatura inválida e payloads suportados malformados. O endpoint é o único com `verify_jwt = false`.
- O banco deduplica `event_id`; eventos já processados/pending são reconhecidos sem nova projeção, e leases/fencing impedem finalizações obsoletas. A RPC projeta snapshot e conclui evento na mesma transação.
- Falhas de persistência/Stripe retornam HTTP 503 no webhook para permitir retry; a criação de Checkout retorna erros não-2xx, e a idempotência/reserva SQL mantém parâmetros estáveis entre tentativas. Esses contratos foram inspecionados e cobertos parcialmente por asserções, mas não foram exercitados via HTTP/runtime nesta etapa.

### Defeitos e alterações

Não foi demonstrado novo defeito reproduzível nesta etapa; nenhum código foi alterado. O relatório foi atualizado. As correções anteriores de validação de IDs, timestamps e referências de Subscription permanecem no código e cobertas pelos testes contratuais já executados.

### Pendências e riscos

- Compilação/type-check Deno e compatibilidade real dos imports `npm:stripe@23.0.0` e `https://esm.sh/@supabase/supabase-js@2` continuam não verificados.
- Execução local das funções, cliente PostgREST, assinatura válida/inválida com requests, resposta HTTP e retries reais continuam não verificados.
- Stripe Sandbox e Supabase local não foram acessados; nenhum evento ou sessão real foi criado.
- A execução PostgreSQL isolada das migrations/roteiro foi registrada na seção de 17:30 deste mesmo dia; não foi repetida nesta etapa focada nas Edge Functions.
- Testes HTTP para autenticação, webhook, falha intermediária, replay duplicado e retry permanecem dependentes de harness local Deno/Supabase.

Gate 0 continua aberto; P-01–P-06 permanecem pendentes. Nenhum acesso remoto, deploy, pagamento, alteração de secrets/configuração ou commit ocorreu.

## Continuação de validação — 2026-10-21, 17:30

### Estado e ferramentas

- Raiz confirmada: `/home/usuario/Desktop/cy08`.
- A pasta não pertence a um repositório Git; `git status`/`git diff` não podem demonstrar que o workspace está limpo nem identificar alterações preexistentes.
- Não foi encontrado `AGENTS.md` no projeto.
- Node.js `v20.18.0`, npm `10.8.2`, Python `3.11.2` e PostgreSQL `15.19` disponíveis.
- Deno e Supabase CLI não estão no `PATH`; `initdb`/`pg_ctl` também não estão no `PATH`, mas os binários do PostgreSQL 15 foram encontrados em `/usr/lib/postgresql/15/bin/`.
- Nenhuma ferramenta ou dependência foi instalada.

### Reexecução de testes

| Comando | Resultado |
|---|---|
| `.venv/bin/python -m unittest tests.test_stripe_billing_backend_contract tests.test_tenant_stripe_billing_migration tests.test_tenant_license_restriction_migration tests.test_tenant_installations_migration tests.test_tenant_authorization tests.test_license_contract` | 63 testes aprovados |
| `.venv/bin/python -m unittest discover -s tests` | 572 testes aprovados, 0 falhas, 0 ignorados |

### PostgreSQL isolado

Nesta continuação, os testes SQL foram reproduzidos. Um cluster PostgreSQL `15.19` foi inicializado em diretório temporário privado (`umask 077`, diretórios `0700`), com `listen_addresses=''` e socket Unix dentro do diretório temporário. Foi usado um banco descartável local, com roles Supabase e `auth.users`/`auth.role()` mínimos de teste. Foram aplicadas em ordem as migrations locais `20261000` a `20261014` existentes (`20261000`–`20261014`, incluindo os passos 20261010–20261012); em seguida, `tests/sql/test_stripe_checkout_reservations.sql` concluiu com `BEGIN`, `SET`, `DO`, `ROLLBACK`. O servidor foi parado e o diretório removido pelo cleanup.

O roteiro exercita reserva nova/ocupada/retry/reuso, fencing antigo e atual, incompatibilidade de Price, subscription vinculada, evento duplicado, projeção com invoice paga, retenção sem concessão para invoice não paga, preservação de licença `revoked`, e RLS/grants/`SECURITY DEFINER`/`search_path`. O cluster usa fixtures locais e não equivale ao ambiente Supabase completo.

O roteiro **não** comprova expiração real da sessão Stripe, cancelamento Stripe com projeção de licença, retries HTTP ou concorrência entre processos/conexões. Esses cenários permanecem pendentes, não inferidos pelo teste.

### Compilação, integração e escopo

- `deno check` e execução das Edge Functions não foram possíveis porque Deno não está disponível.
- Supabase local/PostgREST/Auth não foi executado porque Supabase CLI não está disponível.
- Nenhum serviço Stripe/Supabase remoto, credencial, cobrança ou checkout real foi acessado.
- Não foi identificado defeito adicional nesta inspeção que justificasse nova alteração de código. Os guardas de ID/timestamp e referências de Invoice documentados na seção anterior continuam cobertos pelos testes contratuais.

Gate 0 continua aberto; P-01–P-06 continuam pendentes. Não houve deploy, aplicação remota de migration, alteração de configuração, cobrança ou commit.

## Continuação da auditoria local — 2026-10-21, 17:10 (registro anterior)

Esta seção registra a verificação incremental das Edge Functions no workspace atual; as evidências abaixo não substituem nem atualizam retroativamente os resultados da auditoria anterior.

### Estado inicial e ferramentas

- Raiz confirmada: `/home/usuario/Desktop/cy08`.
- `git status --short`, `git diff` e `git diff --check` não puderam ser usados: a pasta não pertence a um repositório Git. Não é possível verificar diff, histórico ou alterações preexistentes; a falha do Git não significa workspace limpo.
- Busca de `AGENTS.md` no projeto não encontrou arquivos.
- Versões disponíveis: Node.js `v20.18.0`, npm `10.8.2`, Python `3.11.2` e cliente `psql 15.19`.
- Indisponíveis: Deno, Supabase CLI e `tsc`. Não foram instaladas ferramentas ou dependências.
- `package.json` declara `supabase` como dependência de desenvolvimento, mas a CLI não está no `PATH`; nenhum comando npm/npx foi executado.

### Defeito encontrado e correção

O webhook validava IDs Stripe somente pelo prefixo (`cus_`, `sub_`, `in_`). Um ID malformado como `sub_` podia avançar para a RPC e ser rejeitado pelo banco como erro de processamento, levando a HTTP 503/retry em vez de HTTP 400. Uma Invoice com uma referência não nula, mas malformada, podia ser tratada como Invoice sem vínculo e reconhecida como irrelevante (HTTP 200). Além disso, `event.created` era convertido para ISO sem validar inteiro, sinal ou faixa de data representável; um timestamp malformado podia causar exceção antes do registro idempotente.

Correção local em `supabase/functions/stripe-webhook/index.ts`:

- valida o sufixo dos IDs como não vazio e alfanumérico;
- rejeita como 400 referência de Subscription malformada quando o campo está presente;
- rejeita timestamp de evento não inteiro, negativo ou não representável como data JS antes de chamar RPCs.

Foram adicionadas asserções contratuais correspondentes em `tests/test_stripe_billing_backend_contract.py` e atualizada a documentação do endpoint. Não foram alteradas migrations, decisões de licenciamento, grants, secrets ou configuração de produção.

### Validação executada nesta continuação

| Comando | Resultado real | Tipo |
|---|---|---|
| `.venv/bin/python -m unittest tests.test_stripe_billing_backend_contract tests.test_tenant_stripe_billing_migration tests.test_tenant_license_restriction_migration tests.test_tenant_installations_migration tests.test_tenant_authorization tests.test_license_contract` | 63 testes passaram antes da correção, como baseline | Testes Python contratuais/locais |
| `.venv/bin/python -m unittest tests.test_stripe_billing_backend_contract` | 23 testes passaram após a correção | Testes Python contratuais estáticos |
| `.venv/bin/python -m unittest discover -s tests` | 572 testes passaram, 0 falharam, 0 ignorados | Suíte Python local, após a correção |

### Não executado nesta continuação

- `deno check`/compilação e runtime Edge: bloqueados por Deno indisponível; não há prova de compilação TypeScript nesta etapa.
- Supabase local ou integração real com PostgREST/Auth: CLI indisponível.
- Teste SQL/PostgreSQL isolado: o cliente PostgreSQL está disponível, mas nenhum cluster isolado foi iniciado nesta continuação. O resultado PostgreSQL 15.19 registrado na seção histórica deste relatório não é apresentado como recém-reproduzido.
- Testes Stripe, incluindo assinatura de webhook, Checkout, recusas e retries: não executados; nenhum serviço externo ou credencial foi acessado.
- Concorrência multi-processo: permanece sem validação.

### Arquivos alterados nesta continuação

- `supabase/functions/stripe-webhook/index.ts`
- `tests/test_stripe_billing_backend_contract.py`
- `docs/CY08-stripe-billing-backend.md`
- `docs/CY08-stripe-billing-validacao-final.md`

O veredito permanece **parcialmente validado; não pronto para produção**. Gate 0 permanece aberto e P-01–P-06 continuam pendentes. Não houve acesso remoto, deploy, cobrança, alteração de banco ou commit.

## Veredito

**Parcialmente validado; não pronto para produção.** Os testes Python disponíveis passaram e as migrations de Billing envolvidas, incluindo as novas migrations locais `20261013` e `20261014`, foram aplicadas e exercitadas em um PostgreSQL 15.19 temporário e isolado. Não foi possível compilar ou executar as Edge Functions porque Deno não está instalado. O cliente Supabase carregado por URL também não foi executado no runtime Edge.

As correções locais endereçam dois defeitos técnicos reproduzíveis:

1. A criação de Checkout combinava uma chave de idempotência fixa com um `integration_identifier` aleatório por request, portanto um retry poderia reenviar parâmetros diferentes sob a mesma chave.
2. O webhook reconhecia como evento irrelevante (HTTP 200) alguns eventos suportados com ID ou Customer ausente/malformado.

Foi adicionada uma reserva privada por tenant para estabilizar os parâmetros Stripe, serializar tentativas e reusar uma sessão ainda válida. Eventos suportados malformados agora retornam HTTP 400. Nenhum estado de licença foi ampliado para resolver decisões de produto pendentes.

**Gate 0 continua aberto. P-01–P-06 continuam pendentes.** Não houve acesso remoto, chamada à Stripe, deploy, commit ou alteração de migrations históricas.

## Estado inicial e limitações de proveniência

O diretório de trabalho era `/home/usuario/Desktop/cy08`. Os comandos `git status --short`, `git diff --` e `git diff --check` falharam porque o diretório não pertence a um repositório Git. Assim, não há como verificar diff, histórico ou separar mudanças atuais de alterações preexistentes do usuário. Fiz apenas alterações localizadas nos arquivos enumerados adiante; não removi nem reverti arquivos.

Não foi encontrado `AGENTS.md`. O relatório [`CY08-auditoria-integracao-stripe-billing.md`](./CY08-auditoria-integracao-stripe-billing.md) foi lido como contexto histórico; suas observações sobre ausência de Checkout/webhook antecedem a implementação local atual e não são uma descrição do estado de código após esta tarefa.

## Arquivos examinados

- `supabase/functions/stripe-checkout/index.ts`
- `supabase/functions/stripe-webhook/index.ts`
- `supabase/config.toml`
- `supabase/migrations/20261001_create_tenant_identity.sql`
- `supabase/migrations/20261004_create_tenant_licenses.sql`
- `supabase/migrations/20261005_create_tenant_installations.sql`
- `supabase/migrations/20261006_add_revoked_tenant_license_status.sql`
- `supabase/migrations/20261007_authenticated_tenant_onboarding.sql`
- `supabase/migrations/20261008_create_tenant_stripe_billing.sql`
- `supabase/migrations/20261009_preserve_restricted_tenant_licenses.sql`
- `supabase/migrations/20261013_create_stripe_webhook_inbox.sql`
- `supabase/migrations/20261014_reserve_stripe_checkout_sessions.sql`
- `tests/test_stripe_billing_backend_contract.py`
- `tests/sql/test_stripe_checkout_reservations.sql`
- `tests/test_tenant_stripe_billing_migration.py`
- `tests/test_tenant_license_restriction_migration.py`
- `tests/test_tenant_installations_migration.py`
- `tests/test_tenant_authorization.py`
- `tests/test_license_contract.py`
- `docs/CY08-stripe-billing-backend.md`
- `docs/CY08-auditoria-integracao-stripe-billing.md`
- `package.json`, `supabase/config.toml` e a skill local Stripe de Billing/segurança.

As migrations históricas `20261008`–`20261013` não foram editadas. A migration `20261014` é nova e local.

## Correções realizadas

### Reserva e idempotência do Checkout

[`20261014_reserve_stripe_checkout_sessions.sql`](../supabase/migrations/20261014_reserve_stripe_checkout_sessions.sql) adiciona uma reserva privada por tenant:

- valida novamente no banco a associação Customer, ausência de subscription já vinculada e licença `trialing` ainda válida;
- serializa tentativas por locks de linhas de Billing/licença, com ordem compatível com o fluxo de aplicação do webhook;
- mantém lease de 90 segundos com token de fencing;
- persiste a chave de idempotência, o `integration_identifier` aleatório de oito letras, Price, URLs e expiração usados na chamada Stripe;
- permite retry com os mesmos parâmetros, impede criação simultânea sob leases concorrentes e reusa a URL de sessão até a expiração;
- define a expiração Stripe para 23 horas, para que a sessão não permaneça aberta após a reserva;
- grava ID/URL da sessão somente se o worker ainda possuir o lease válido;
- mantém RLS habilitado, revoga acesso direto à tabela e concede execução das RPCs somente a `service_role`.

[`stripe-checkout/index.ts`](../supabase/functions/stripe-checkout/index.ts) agora usa os parâmetros persistidos retornados pela RPC. Uma resposta de persistência inesperada ou falha ao concluir a reserva não se transforma em sucesso.

### Validação de estrutura do webhook

Em [`stripe-webhook/index.ts`](../supabase/functions/stripe-webhook/index.ts), eventos suportados com ID do evento, Customer, ID da Subscription ou ID da Invoice malformado agora retornam HTTP 400. Uma Invoice estruturalmente válida sem vínculo a Subscription continua reconhecida como irrelevante e retorna HTTP 200, sem projetar licença.

## Contratos RPC verificados

| Chamada | Assinatura/retorno SQL | Forma tratada no TypeScript |
|---|---|---|
| `resolve_tenant_for_billing` | `(uuid) → uuid` escalar | String UUID; rejeita valor diferente de string |
| `resolve_tenant_license` | `(uuid) → TABLE(...)` | Array; exige exatamente uma linha do tenant resolvido |
| `get_tenant_stripe_checkout_state` | `(uuid) → TABLE(...)` | Array de zero ou uma linha |
| `register_tenant_stripe_customer` | `(uuid,text) → void` | Ignora `data`; exige ausência de `error` |
| `begin_tenant_stripe_checkout` | `(uuid,text,text,text,text,text,text) → TABLE(...)` | Array de uma linha; valida os campos e o `outcome` |
| `complete_tenant_stripe_checkout` | `(uuid,text,uuid,text,text) → boolean` | Exige `data === true`; senão retorna erro transitório |
| `begin_stripe_webhook_event` | `(text,text,timestamptz,text,text,text) → TABLE(...)` | Array de uma linha; valida `outcome`, token e geração |
| `apply_stripe_webhook_snapshot` | parâmetros tipados em `20261013` → `text` escalar | String; somente status reconhecidos retornam HTTP 200 |

As definições SQL foram aplicadas no PostgreSQL isolado e os formatos escalar/tabela foram exercitados diretamente. A interpretação pelo cliente `@supabase/supabase-js` real no runtime Edge continua sem teste, pois Deno e o pacote de runtime não estão disponíveis localmente; não se apresenta o teste SQL como substituto desse teste de integração.

## Segurança e preservação de política

- Checkout autentica o Bearer por `auth.getUser`, deriva o tenant com o ID do usuário validado e não aceita corpo contendo tenant ou Price.
- O Price vem somente de `STRIPE_PRICE_ID` no servidor e é comparado à allowlist de um único Price no webhook. Não houve chamada Stripe para verificar produto, recorrência ou configuração dessa variável.
- Customer é associado por RPC privilegiada e a constraint `UNIQUE` global impede vínculo a tenants diferentes.
- O webhook valida assinatura Stripe sobre o corpo bruto antes de confiar nos dados. IDs não confiáveis não são usados para projetar estado sem recuperar a Subscription canônica.
- A projeção de `active` exige a Invoice atual confirmada como paga e vinculada ao mesmo Customer/Subscription.
- A migration `20261013` mantém as tabelas privadas, usa RPCs `SECURITY DEFINER` com `search_path = ''` e concede execução apenas a `service_role`. A migration `20261014` aplica o mesmo padrão às RPCs de reserva.
- Os logs observados gravam apenas códigos/tipos de erro e mensagens estáticas; não registram corpo bruto, chave, token ou URL Stripe.
- `blocked` e `revoked` continuam fail-closed; tentativas de alteração do estado restrito viram `pending_policy`.
- `past_due` permanece sem conceder acesso. Transições não aprovadas seguem bloqueadas/pendentes; nenhuma decisão P-01–P-06 foi inferida.
- Não foi encontrado código desktop chamando a rota `stripe-checkout`; portanto, a integração Edge/backend ainda não está comprovadamente conectada ao fluxo de UI.

## Validação PostgreSQL isolada

Ambiente: **PostgreSQL 15.19 (Debian)**, inicializado em diretório temporário privado com `listen_addresses=''`, socket Unix em diretório privado e sem conexão TCP. O cluster foi parado e o diretório temporário removido ao final. A fixture de `auth.role()`, roles Supabase e tabela legada `licencas` foi criada somente para o harness local; não representa um ambiente Supabase completo.

Foram aplicadas no cluster descartável as migrations locais:

- `20261001_create_tenant_identity.sql`
- `20261004_create_tenant_licenses.sql`
- `20261005_create_tenant_installations.sql`
- `20261006_add_revoked_tenant_license_status.sql`
- `20261007_authenticated_tenant_onboarding.sql`
- `20261008_create_tenant_stripe_billing.sql`
- `20261009_preserve_restricted_tenant_licenses.sql`
- `20261013_create_stripe_webhook_inbox.sql`
- `20261014_reserve_stripe_checkout_sessions.sql`

O roteiro [`test_stripe_checkout_reservations.sql`](../tests/sql/test_stripe_checkout_reservations.sql) passou. Ele verificou diretamente:

- início, ocupação e retomada de reserva após expiração do lease;
- preservação da chave, identificador, URLs e expiração entre retries;
- rejeição de finalização por lease antigo e finalização pelo lease atual;
- reuso da sessão, incompatibilidade de Price e bloqueio após subscription vinculada;
- formas de retorno escalar e de conjunto para as RPCs de tenant/licença/Billing/webhook;
- projeção de `active` com Invoice paga, idempotência de evento processado e retenção em `pending_policy` para Invoice não paga;
- preservação de licença `revoked`;
- RLS, ausência de acesso direto às reservas, grants das RPCs, `SECURITY DEFINER` e `search_path` vazio.

Os fixtures foram envolvidos em transação e revertidos. O cluster foi encerrado. Não houve alteração de banco compartilhado. O roteiro não simula Stripe, Supabase Auth real nem concorrência de múltiplos processos; a resposta `busy` e o fencing foram exercitados em SQL, mas um teste de stress concorrente entre conexões permanece pendente.

## Testes executados

1. Focados:

   ```text
   .venv/bin/python -m unittest tests.test_stripe_billing_backend_contract tests.test_tenant_stripe_billing_migration tests.test_tenant_license_restriction_migration tests.test_tenant_installations_migration tests.test_tenant_authorization tests.test_license_contract
   ```

   **63 testes passaram.**

2. Suíte completa, repetida após as correções:

   ```text
   .venv/bin/python -m unittest discover -s tests
   ```

   **572 testes passaram, 0 falharam, 0 ignorados.**

3. PostgreSQL:

   ```text
   PostgreSQL 15.19 isolado: migrations selecionadas + tests/sql/test_stripe_checkout_reservations.sql
   ```

   **Execução SQL passou**, incluindo rollback dos fixtures e encerramento do cluster.

Os testes Python contratuais são verificações estáticas; não foram contados como execução Deno, integração Stripe ou teste de banco. O roteiro SQL é execução real de PostgreSQL local, mas não de Supabase remoto.

## Validações não executadas e riscos residuais

- **Deno não instalado**: Edge Functions não foram compiladas nem executadas; a importação de `stripe@23.0.0`, a versão da API e os tipos exatos do runtime Edge precisam de validação Deno.
- **Supabase CLI não encontrado no PATH**: não foi executado Supabase local, nem confirmados os formatos HTTP do cliente PostgREST/SDK real.
- **Stripe não acessado**: não foram testados assinatura válida/inválida, retries HTTP, sessão expirada/completa, Checkout sandbox, Price configurado ou chamadas reais da API.
- **Sem desktop caller**: a função existe localmente, mas não se comprovou botão/fluxo de UI que a invoque nem persistência de sessão Supabase no cliente.
- **Sem teste concorrente entre processos/conexões**: o teste SQL confirma o lease ocupado e fencing, mas não é um stress test de simultaneidade.
- **Sessões concluídas com webhook atrasado**: a reserva bloqueia/reusa a sessão durante sua validade de 23 horas. Se o evento de subscription não for reconciliado antes da expiração, nova tentativa poderá iniciar outro Checkout; monitoramento e reconciliação operacional devem cobrir eventos atrasados antes de produção.
- `pending_policy`/`pending_mapping` são persistidos, mas não há console ou rotina administrativa de revisão/reprocessamento.
- O handler não cobre `charge.dispute.created`, `charge.refunded` ou `radar.early_fraud_warning.created`; a orientação Stripe exige avaliar e implementar tratamento dos eventos de risco aplicáveis antes de declarar uma assinatura pronta para produção.
- A versão de Supabase JS vem de `https://esm.sh/@supabase/supabase-js@2`, sem versão exata fixada nesta implementação. Fixar e validar a versão do cliente faz parte do preparo de runtime.
- O estado remoto e o histórico remoto de migrations não foram consultados. A migration `20261014` é somente local e nenhuma sincronização/aplicação remota foi tentada.
- Stripe Tax não foi habilitado. Antes de cobrar clientes, validar obrigações fiscais, jurisdições, registros e configuração tributária aplicável.

## Arquivos criados ou alterados

**Criados**

- [`supabase/migrations/20261014_reserve_stripe_checkout_sessions.sql`](../supabase/migrations/20261014_reserve_stripe_checkout_sessions.sql)
- [`tests/sql/test_stripe_checkout_reservations.sql`](../tests/sql/test_stripe_checkout_reservations.sql)
- Este relatório.

**Alterados**

- [`supabase/functions/stripe-checkout/index.ts`](../supabase/functions/stripe-checkout/index.ts)
- [`supabase/functions/stripe-webhook/index.ts`](../supabase/functions/stripe-webhook/index.ts)
- [`tests/test_stripe_billing_backend_contract.py`](../tests/test_stripe_billing_backend_contract.py)
- [`docs/CY08-stripe-billing-backend.md`](./CY08-stripe-billing-backend.md)

Não foram alteradas migrations históricas, configuração de conta Stripe, dependências, migrations remotas ou dados compartilhados.

## Próximos passos antes de produção

1. Deliberar P-01–P-06 formalmente; manter Gate 0 aberto até aprovação verificável.
2. Revisar e aprovar migrations `20261013` e `20261014`, conferir dependências e estado remoto por procedimento read-only autorizado; não aplicar migrations automaticamente.
3. Fixar a versão exata do Supabase JS e executar `deno check`/runtime com dependências aprovadas.
4. Implementar e testar a chamada no desktop, preservando autenticação, tratamento de `checkout_url` e não concessão de entitlement pelo retorno.
5. Testar webhook em Stripe Sandbox e Supabase local isolado: assinatura, retries, sessões concluídas/expiradas, concorrência entre conexões, eventos fora de ordem, RPCs transitórios e reprocessamento administrativo.
6. Configurar chaves separadas por ambiente em secret manager; preferir restricted API key com permissões mínimas.
7. Deliberar a cobertura de disputas, reembolsos e alertas de fraude, além de impostos/registro fiscal, antes de cobrança de produção.
8. Implantar somente após aprovação humana explícita e com plano de validação/rollback por migration e Edge Function.

**Confirmações:** nenhum acesso remoto a Supabase/Stripe/Xero; nenhuma cobrança ou Checkout real; nenhum deploy; nenhum commit. Gate 0 permanece aberto e P-01–P-06 permanecem pendentes.
