import os
from datetime import date

import requests


class LicenseValidator:
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_ANON_KEY", "")

    def validate_license(self, license_key: str):
        if not license_key or not license_key.strip():
            return False, "Erro: a chave de licença está vazia."

        if not self.supabase_url:
            return False, "Erro: configure a variável SUPABASE_URL no ambiente."

        if not self.supabase_key:
            return False, "Erro: configure SUPABASE_ANON_KEY no ambiente."

        url = f"{self.supabase_url}/rest/v1/licencas"
        params = {
            "select": "chave_licenca,ativo,data_expiracao",
            "chave_licenca": f"eq.{license_key.strip()}",
            "ativo": "is.true",
        }
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            return False, f"Erro ao consultar Supabase ({type(exc).__name__})."

        try:
            data = response.json()
        except ValueError:
            return False, "Resposta inválida do Supabase."

        if not isinstance(data, list) or not data:
            return False, "Licença não encontrada ou inativa."

        row = data[0]
        if not isinstance(row, dict) or row.get("ativo") is not True:
            return False, "Licença inativa."

        expiration = row.get("data_expiracao")
        if expiration:
            try:
                if date.fromisoformat(expiration) < date.today():
                    return False, "Licença expirada."
            except (TypeError, ValueError):
                return False, "Data de expiração inválida na base de dados."

        return True, "Licença validada com sucesso."
