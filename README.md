# CY08 — FieldQuo

Plataforma Python para automação desktop, processamento de documentos
financeiros, integração com Xero e componentes de autenticação,
licenciamento e cobrança.

## Estado do projeto

- Código versionado no Git.
- Integração contínua de desenvolvimento ainda a configurar.
- Última validação registrada: 572 testes e 125 subtestes aprovados.
- A aprovação dos testes automatizados não substitui a validação
  das integrações com serviços externos em ambiente real.

## Principais componentes

### Aplicação desktop e automação

- `main.py`: ponto de entrada candidato da aplicação desktop.
- `desktop_pdf_controller.py`: controle relacionado ao fluxo de PDF.
- `automation.py`: automação.
- `rpa/`: componentes de automação com navegador.

### FieldQuo e processamento de faturas

- `fieldquo/`: leitura, interpretação, extração de texto,
  análise e pré-visualização de documentos PDF.
- `xero/`: API, autenticação OAuth, contatos, preparação
  de faturas e integração com Xero.

### Autenticação, empresas e licenciamento

- `auth.py` e `supabase_auth.py`: componentes de autenticação.
- `tenant_identity.py`: identidade de empresas.
- `tenant_authorization.py`: autorização por empresa.
- `tenant_fiscal_config.py`: configuração fiscal.
- `license_contract.py`: contrato de licenciamento.
- `onboarding_client.py` e `onboarding_contract.py`:
  componentes de onboarding.

### Backend e cobrança

- `supabase/functions/`: funções de onboarding e integração Stripe.
- `supabase/migrations/`: migrações SQL do banco de dados.
- `docs/`: documentação técnica e relatórios de validação.

## Requisitos

- Python 3.11 ou compatível com as dependências do projeto.
- Git.
- Dependências Python descritas em `requirements.txt`.
- Node.js/npm e Deno, quando necessários para os componentes
  correspondentes.
- Credenciais e configurações externas somente para os recursos
  que dependem desses serviços.

## Instalação local

Na raiz do projeto:

```bash
cd ~/Desktop/cy08

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Para automações que utilizem navegadores Playwright, instale
também o navegador necessário:

```bash
.venv/bin/python -m playwright install
```

A instalação de navegadores pode variar conforme o sistema operacional.

## Executar os testes

Execute a suíte completa:

```bash
cd ~/Desktop/cy08
.venv/bin/python -m pytest -q
```

Testes específicos da integração Xero:

```bash
.venv/bin/python -m pytest \
  tests/test_xero_invoice_creation_orchestrator.py \
  tests/test_xero_api.py -q
```

Testes relacionados ao fluxo de preparação de PDF:

```bash
.venv/bin/python -m pytest \
  tests/test_pdf_invoice_workflow_preparation.py \
  tests/test_pdf_invoice_dry_run.py -q
```

## Configuração e segurança

Antes de executar funcionalidades que dependam de serviços externos:

1. Consulte a documentação em `docs/`,
   `FIELDQUO_ONBOARDING.md`, `PDF_PREVIEW.md` e
   `LICENSE_TRIAL.md`.
2. Identifique as variáveis de ambiente exigidas pelo código
   e pelas funções do backend.
3. Configure credenciais nos mecanismos apropriados para cada
   ambiente. Não coloque tokens, senhas ou chaves privadas no Git.
4. Valide autenticação, autorização, isolamento entre empresas,
   licenciamento e tratamento de erros.
5. Teste as integrações externas em ambiente de desenvolvimento
   antes de qualquer utilização em produção.

Não presuma que a existência de uma função ou migração significa
que o serviço externo já está configurado ou implantado.

## Banco de dados e Supabase

As migrações SQL estão em `supabase/migrations/`.

Leia e revise as migrações antes de aplicá-las a qualquer banco
de dados. Confirme o projeto de destino, faça backup quando
apropriado e valide o resultado em ambiente isolado.

Os metadados locais do Supabase não devem ser versionados.

## Desenvolvimento

Confira o estado do repositório:

```bash
git status
git log -5 --oneline
```

Após realizar alterações, execute os testes relevantes e a suíte
completa. Revise o diff antes de criar um commit.

## Licença

Consulte `LICENSE_TRIAL.md` para as condições de licenciamento
documentadas no projeto.
