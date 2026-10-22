# Pré-visualização local de PDF

A pré-visualização extrai texto localmente usando o executável Poppler
`pdftotext`. O projeto não incorpora uma biblioteca Python de PDF. Instale
Poppler para o sistema operacional do usuário e disponibilize `pdftotext` no
`PATH` antes de executar o aplicativo. No ambiente de desenvolvimento atual,
Poppler está instalado e `pdftotext` está disponível.

Somente PDFs com camada textual são suportados. OCR não está habilitado; PDFs
digitalizados sem texto extraível são recusados. O texto bruto não é exibido nem
registrado pela tela de preview.

O preview usa as regras determinísticas já existentes de
`parse_fieldquo_invoice_text`, mostra campos ausentes e ambiguidades, e não
valida licença, chama serviços de emissão, acessa Supabase ou Xero, nem cria
invoices. Mesmo quando os campos mínimos do documento são reconhecidos, a
interface informa que nenhuma invoice foi enviada. A futura emissão ainda
depende de OAuth de escrita, resolução segura do contato, configuração fiscal
do tenant e mapeamento explícito dos argumentos obrigatórios do serviço.
