# CY08 — Auditoria da integração Stripe Billing

## Veredito

**Parcialmente validado; não pronto para produção.** O repositório contém uma migration com schema e RPCs privilegiadas para projetar um snapshot de assinatura Stripe sobre o vínculo de billing e a licença. Não foi encontrada uma integração de aplicação que crie sessões de Checkout, receba e autentique webhooks, deduplique eventos, impeça a aplicação de snapshots antigos ou exponha o estado de billing por uma interface autenticada.

Não foram alteradas migrations, código de produto ou testes nesta auditoria. Não houve conexão SQL remota nesta etapa, execução de SQL local, deploy ou commit. A informação de que as migrations `20261000`–`20261007` estão aplicadas e `20261008`–`20261012` pendentes é contexto fornecido anteriormente, não uma verificação remota atual.

**Gate 0 permanece aberto. A-01–A-07 permanecem preservadas. P-01–P-06 continuam pendentes.**

## Escopo, método e limites

Foram examinados localmente:

- Todas as migrations `20261000`–`20261012`, incluindo dependências e funções de licenciamento, onboarding, fiscal, Xero e Stripe.
- A função Edge `supabase/functions/fieldquo-onboarding/index.ts`.
- `main.py`, `supabase_auth.py` e `tenant_authorization.py`, além da busca de referências Stripe nos módulos de aplicação e testes.
- Testes de migration Stripe, onboarding, autorização de tenant, licença e restrição de licença.
- `requirements.txt`, `package.json`, os relatórios CY08 3B.3.47–3B.3.49 e a skill local `stripe-best-practices`.

Não foi encontrado `AGENTS.md` nos diretórios examinados. O diretório não é um repositório Git: `git status`, `git diff` e `git log` falharam com “not a git repository”. Portanto, não é possível avaliar o histórico ou o estado anterior do workspace por Git.

Uma tentativa anterior de leitura do catálogo remoto falhou por autenticação do Supabase CLI. Em conformidade com o limite de parar a inspeção remota nessa situação, não houve nova tentativa nem acesso alternativo. Nenhum arquivo `.env` ou valor de credencial foi lido. Não houve acesso a Stripe, Xero, produção ou serviços externos nesta etapa.

As buscas por implementação de Stripe foram limitadas aos arquivos de primeira parte da aplicação, funções Supabase, migrations e testes indicados; o resultado não prova ausência de componentes externos ao workspace.

## Inventário de estado

“Presente localmente” significa apenas que há definição no arquivo indicado; não significa que o objeto exista remotamente. O estado remoto abaixo permanece **não verificado nesta etapa**.

| Objeto/superfície | Estado local | Estado remoto | Divergência/risco |
|---|---|---|---|
| `public.tenants` | Criada em `20261001`; usada pelas demais migrations. | Não verificado nesta etapa. O contexto anterior informou que existe. | Estrutura, grants e estado efetivo não foram consultados agora. |
| `public.tenant_accounts` | Criada em `20261007`; associa um usuário Supabase Auth a um tenant. | Não verificado. | Sem consulta de colunas, constraints ou RLS remotos. |
| `public.tenant_installations` | Criada em `20261005`; `20261007` e `20261012` alteram a unicidade e o registro. | Não verificado. | Dados existentes e aplicação das constraints não foram confirmados. |
| `public.tenant_licenses` / `resolve_tenant_license(uuid)` | Criadas em `20261004`; estados ampliados em `20261006`; trigger de preservação em `20261009`. | Não verificado nesta etapa. O contexto anterior informou que tabela/função existem. | Grants, definição exata e trigger instalados no banco não comprovados. |
| `public.tenant_stripe_billing` | Definida apenas em `20261008`. | Não verificado nesta etapa. O contexto anterior informou que não existe e que `20261008` está pendente. | Não é possível confirmar se houve mudança posterior ou aplicação manual. |
| `register_tenant_stripe_customer(...)` | Definida em `20261008`. | Não verificado nesta etapa; o contexto anterior informou que não existe. | ACL e owner efetivos desconhecidos. |
| `apply_confirmed_tenant_stripe_subscription(...)` | Definida em `20261008`. | Não verificado nesta etapa; o contexto anterior informou que não existe. | ACL, owner e definição efetiva desconhecidos. |
| Checkout, webhook e worker Stripe | Não encontrados na aplicação, funções Supabase ou testes pesquisados. | Não verificado. | A projeção SQL não é um fluxo de faturamento completo. |
| Histórico remoto de migrations | Arquivos locais `20261000`–`20261012` existem. | Histórico anterior informou `20261000`–`20261007` aplicadas e `20261008`–`20261012` pendentes; não reconfirmado agora. | Histórico e schema podem divergir, especialmente se houve aplicação manual. |

## Auditoria da migration Stripe `20261008`

### Modelo de dados e isolamento

- `tenant_id` é chave primária e FK para `public.tenants`, com `ON DELETE CASCADE`: no máximo um registro de billing por tenant.
- `stripe_customer_id` é `NOT NULL`, único globalmente e validado para não estar vazio ou conter espaços ASCII nas extremidades.
- `stripe_subscription_id` também é único globalmente.
- O constraint dos campos de assinatura exige que o conjunto de campos de assinatura esteja completamente vazio ou contenha IDs, preço, estado e período coerentes.
- A tabela tem RLS habilitado e acesso direto é revogado de `PUBLIC`, `anon`, `authenticated` e `service_role`.

Essas são propriedades do SQL local. Não foram compiladas ou inspecionadas em catálogos PostgreSQL.

### Funções privilegiadas

As duas funções estão definidas como `SECURITY DEFINER` com `SET search_path = ''`, qualificam objetos pelo schema e validam `auth.role() = 'service_role'`. O `EXECUTE` é revogado de `PUBLIC`, `anon`, `authenticated` e `service_role`, sendo depois concedido explicitamente a `service_role`.

O acesso ao RPC continua sendo privilegiado: a validação do papel não autentica, por si só, a origem de um evento Stripe nem prova que o snapshot recebido foi confirmado pela Stripe. É obrigatório que um backend confiável seja o único chamador e valide a origem e os dados antes de invocar a função. A existência de um role com privilégio não substitui autenticação de webhook.

### Registro do Customer e concorrência

`register_tenant_stripe_customer` bloqueia a linha do tenant, recusa tenant inexistente, permite repetição idêntica e rejeita trocar o Customer já vinculado. O `UNIQUE` global protege contra associar o mesmo Customer a outro tenant, inclusive em inserções concorrentes; erro de unicidade deve ser tratado pelo chamador.

O registro não cria nem recupera o Customer no Stripe. Não foi encontrado caller de aplicação que o invoque.

### Projeção da assinatura e estados

`apply_confirmed_tenant_stripe_subscription` resolve o tenant pelo Customer armazenado, bloqueia as linhas de billing e licença e recusa tenant sem vínculo/licença. O conjunto de estados aceito na migration é `trialing`, `active`, `past_due`, `canceled`, `unpaid`, `incomplete`, `incomplete_expired` e `paused`.

O mapeamento no SQL é:

| Status Stripe | Efeito local identificado no SQL |
|---|---|
| `trialing` | Só aceita o trial local existente e limita o período; tenta manter a licença `trialing`. |
| `active` | Define licença `active` se o período termina no futuro; caso contrário, `expired`. |
| `canceled` | Preserva `active` até o fim do período futuro; depois define `expired`. |
| `past_due` | Define `past_due`. Os validadores de onboarding existentes só autorizam `trialing` e `active`, portanto o acesso é negado nesse caminho. |
| `unpaid`, `incomplete`, `incomplete_expired`, `paused` | Mapeia para `blocked`. A decisão de produto P-05 permanece pendente. |

A tabela e a função rejeitam período inválido e status desconhecido. O teste estático afirma que o trial não pode ser estendido; ainda assim, a função atribui `p_current_period_end` a `trial_ends_at` quando recebe `trialing`, podendo encurtar o trial existente. Não se alterou esse comportamento: política de início/renovação de trial não deve ser inferida do SQL ou do Checkout e exige deliberação aplicável.

### Restrições e riscos de consistência

1. **Eventos fora de ordem não são protegidos.** A tabela não armazena versão/ordem do evento nem timestamp de atualização originado na Stripe. Repetir o mesmo snapshot pode ser idempotente quanto aos campos principais, mas um snapshot antigo do mesmo subscription pode sobrescrever estado e datas mais recentes.
2. **A função não verifica Stripe.** Ela projeta parâmetros confiáveis e só valida consistência interna. Não verifica assinatura de webhook, evento, Customer/Subscription/Price reais ou conta Stripe.
3. **Sem deduplicação/inbox.** Não há registro de IDs de evento, estado de processamento, retry ou reconciliação.
4. **Substituição de subscription é limitada, não completa.** O SQL permite substituir certos estados terminais com período encerrado; não há fluxo que sincronize, valide e audite essa substituição de ponta a ponta.
5. **Licenças `blocked` e `revoked`.** A migration `20261009` acrescenta um trigger que rejeita mudanças saindo desses estados. Com o trigger instalado, o RPC Stripe não consegue limpar a restrição; sem o trigger, o RPC pode escrever `active` em uma linha antes `blocked`. A implantação conjunta, ausência de janela e efetiva instalação do trigger precisam ser verificadas antes de habilitar qualquer caller. Nenhuma decisão de desbloqueio foi introduzida.
6. **Status pendentes de política.** `unpaid`, `incomplete`, `incomplete_expired` e `paused` são mapeados para `blocked` no código local, mas a decisão formal P-05 continua pendente. Não mudar esse mapeamento sem deliberação.
7. **RPC exige backend, ainda ausente.** Enquanto não houver um chamador confiável, Checkout, webhooks, reconciliação e aplicação de eventos, não há integração Billing operacional localmente.

## Auditoria das migrations e ordem de dependências

| Migration | Pré-requisitos/efeito local | Verificações ainda necessárias |
|---|---|---|
| `20261008_create_tenant_stripe_billing.sql` | Depende de `tenants`, `tenant_licenses` e `auth.role()`; cria tabela, RLS, RPCs e ACLs. | Confirmar estrutura e funções no remoto, ACLs reais, trigger de restrição e aplicação atômica pelo runner usado. |
| `20261009_preserve_restricted_tenant_licenses.sql` | Depende de `tenant_licenses`; instala guarda para impedir saída de `blocked`/`revoked`. | Verificar conflitos/trigger existente, execução e interação com todos os writers autorizados. |
| `20261010_enforce_global_xero_tenant_uniqueness.sql` | Depende de `xero_connections`; cria unicidade por `lower(btrim(...))`. | Detectar conflitos sobre dados reais antes da aplicação; compatibilidade da normalização já foi tratada pela migration local seguinte. |
| `20261011_align_global_xero_tenant_normalization.sql` | Depende da tabela e do índice `20261010`; cria índice canônico antes de remover o índice anterior. | Conflitos após normalização e operação da migration devem ser verificados em ambiente isolado e, depois, em processo autorizado. |
| `20261012_fix_tenant_installation_conflict.sql` | Depende de tenant, contas, instalações, licenças, Auth e RPCs introduzidas em `20261007`; substitui a função de onboarding. | Confirmar unicidade preexistente de `installation_id`, definição efetiva e todos os contratos de consumidores. |

As dependências identificadas não demonstram que as migrations possam ser aplicadas remotamente sem conflito. Em especial, não se pode confirmar duplicatas de Xero ou instalações sem leitura do banco. Não usar `db push`, `migration repair` ou aplicar qualquer migration remota nesta etapa. Se o fluxo operacional habilitar o RPC Stripe, garantir que a proteção `20261009` esteja aplicada antes de aceitar atualizações que possam alterar status de licença; não reordenar migrations históricas sem validar o mecanismo de migrations e seu histórico.

## Fluxo Stripe encontrado versus necessário

| Passo necessário | Evidência local | Estado |
|---|---|---|
| Iniciar compra com usuário autenticado | Há autenticação Supabase no app, mas nenhum início de compra Stripe localizado. | Não encontrado |
| Resolver tenant exclusivamente pela identidade autenticada | Existe tenant onboarding e código de autorização de conexão, mas sem fluxo de billing. | Parcial, não integrado ao billing |
| Criar/recuperar Customer de forma idempotente | RPC de associação ao Customer existe apenas como migration. | Parcial, sem caller nem API Stripe |
| Criar Checkout com Price ID allowlisted em servidor | Nenhuma implementação encontrada. | Não encontrado |
| Correlacionar Checkout/Customer/Subscription/tenant | Schema local armazena Customer, Subscription e Price; não há metadados/Checkout Session nem call site. | Parcial |
| Receber webhook e validar assinatura sobre corpo bruto | Nenhum endpoint Stripe encontrado. | Não encontrado |
| Deduplicar e proteger contra eventos antigos | Nenhum inbox/ledger/fencing/reconciliação encontrado. | Não encontrado |
| Consultar/verificar estado atual no Stripe antes de projetar | Nenhum backend Stripe ou SDK listado em `requirements.txt`/`package.json`. | Não encontrado |
| Atualizar billing/licença consistentemente | RPC privilegiada faz projeção em SQL, mas caller/autenticidade/ordem não são implementados. | Parcial |
| Consultar entitlement via API autorizada | Há contratos/RPCs de tenant/licença e onboarding, sem interface de billing comprovada. | Parcial |

`requirements.txt` contém `customtkinter`, `playwright`, `requests`, `pyinstaller` e `openpyxl`; `package.json` lista a dependência Supabase, não uma implementação Stripe. A ausência de dependência Stripe é apenas evidência local; não prova nada sobre serviços externos.

## Testes executados e evidência

| Comando executado | Resultado atual | O que não comprova |
|---|---|---|
| `.venv/bin/python -m unittest tests.test_tenant_stripe_billing_migration tests.test_tenant_license_restriction_migration tests.test_tenant_installations_migration tests.test_tenant_authorization tests.test_license_contract` | **40 testes passaram**. | Os testes de migration são contratuais/estáticos; não executam SQL, PostgreSQL, Stripe, Supabase ou chamadas remotas. |
| `.venv/bin/python -m unittest discover -s tests` | **549 testes passaram** em 2,504 s. | Não demonstra efeito de migrations, estado remoto, webhook, integração externa, concorrência real ou produção. |

Não foram executados testes de banco, migrations, Stripe sandbox, webhooks, compras, Supabase remoto, deploy ou migrations no banco. Os resultados históricos dos relatórios CY08 anteriores não são apresentados como execução desta auditoria.

## Achados priorizados

1. **Bloqueador de produção — fluxo Stripe incompleto.** Não há evidência local de Checkout, webhook assinado, backend de reconciliação/deduplicação ou consumidor do RPC. A migration sozinha não conclui Billing.
2. **Alto — projeção vulnerável a snapshots fora de ordem se chamada sem proteção externa.** A função não possui fencing/versionamento e sobrescreve o estado a partir de parâmetros. Um backend futuro deve recuperar/verificar o estado canônico da assinatura antes da projeção e impedir regressões por evento antigo.
3. **Alto — semântica pendente de estados e recuperação.** P-01–P-06 permanecem pendentes; não é seguro implementar transições, renovação, recuperação de `blocked`/`revoked` ou efeitos em conexões Xero por inferência.
4. **Alto — estado remoto e pré-condições não confirmados.** A autenticação da consulta remota falhou anteriormente. Não se confirmou schema, ACL, constraints, dados conflitantes ou histórico na mesma sessão de auditoria.
5. **Médio — cobertura insuficiente do SQL.** Os 40/549 testes são Python; testes estáticos não exercitam constraints, RLS, grants, concorrência ou efeito das funções no PostgreSQL.
6. **Médio — risco de janela de implantação entre 20261008 e 20261009.** A projeção Stripe e a guarda de estado restrito precisam ser tratadas como um conjunto operacional antes de aceitar chamadas de produção; o runner e sua atomicidade não foram identificados.

## Próximos passos para desbloqueio

1. Restabelecer autenticação do Supabase por mecanismo local seguro, sem compartilhar credenciais, e executar apenas consultas `SELECT` ao catálogo e aos dados necessários para confirmar pré-condições e duplicatas.
2. Obter deliberação formal e verificável para P-01–P-06 antes de codificar regras de status, recuperação, renovação ou conexões.
3. Definir e implementar um único backend confiável de Billing (Checkout com preço allowlisted, Customer idempotente, webhook de corpo bruto e assinatura verificada, deduplicação, reconciliação do estado atual, proteção contra eventos antigos, auditoria e retry).
4. Acrescentar testes PostgreSQL isolados das funções/constraints/RLS/ACLs e testes do backend com eventos repetidos e fora de ordem; manter chamadas a Stripe em sandbox e sem compras reais.
5. Documentar o runner de migrations, política de transação/rollback e plano revisável por migration. Aplicar remotamente somente após autorização explícita, pré-flight de dados e revisão do SQL.
6. Revalidar schema, grants, `search_path`, RLS, funções, estado de licença e histórico após uma implantação autorizada. Nenhuma dessas validações foi executada nesta tarefa.

## Arquivos alterados e confirmações

- Criado somente este relatório: `docs/CY08-auditoria-integracao-stripe-billing.md`.
- Nenhum código, migration, teste, configuração ou dependência foi alterado.
- Nenhum segredo ou valor de credencial foi consultado ou incluído.
- Nenhuma chamada Stripe/Xero/Supabase remoto foi realizada nesta etapa; não houve SQL remoto, deploy ou commit.
- **Gate 0: aberto. A-01–A-07: preservadas. P-01–P-06: pendentes.**
