CREATE TABLE public.licencas (
    chave_licenca text NOT NULL,
    ativo boolean NOT NULL DEFAULT true,
    data_expiracao date
);

CREATE INDEX licencas_chave_licenca_idx
    ON public.licencas (chave_licenca);
