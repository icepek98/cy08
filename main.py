import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog

from automation import AutomationExecutor
from desktop_pdf_controller import (
    classify_selected_file,
)
from installation_identity import InstallationIdentityStore
from installation_registration_contract import InstallationRegistrationRequest
from onboarding_client import OnboardingClient
from onboarding_contract import OnboardingResult
from fieldquo.pdf_invoice_preview import (
    PDFInvoicePreview,
    PDFInvoicePreviewError,
    PDFInvoicePreviewProcessor,
    format_pdf_invoice_preview,
)
from report import summarize_results
from supabase_auth import AuthSession, SupabaseAuthClient, SupabaseAuthError


class LicenseApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FieldQuo")
        self.geometry("760x640")
        self.minsize(640, 420)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(11, weight=1)

        self.email_var = ctk.StringVar()
        self.password_var = ctk.StringVar()
        self.demo_mode_var = ctk.BooleanVar(value=False)
        self.selected_file = None
        self.selected_file_kind = None
        self.cancel_event = None
        self.auth_session: AuthSession | None = None
        self.onboarding_client = OnboardingClient()
        self.auth_client = SupabaseAuthClient()
        self.pdf_preview_processor = PDFInvoicePreviewProcessor()

        title = ctk.CTkLabel(
            self,
            text="FieldQuo — Conta e Automação Local",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        self.email_entry = ctk.CTkEntry(
            self,
            textvariable=self.email_var,
            placeholder_text="E-mail da conta",
            width=560,
            height=38,
            border_width=1,
        )
        self.email_entry.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")

        self.password_entry = ctk.CTkEntry(
            self,
            textvariable=self.password_var,
            placeholder_text="Senha",
            show="*",
            height=38,
        )
        self.password_entry.grid(row=2, column=0, padx=20, pady=(0, 8), sticky="ew")

        auth_actions = ctk.CTkFrame(self)
        auth_actions.grid(row=3, column=0, padx=20, pady=(0, 8), sticky="ew")
        auth_actions.grid_columnconfigure((0, 1, 2), weight=1)
        self.sign_in_button = ctk.CTkButton(
            auth_actions, text="Entrar", command=self.sign_in
        )
        self.sign_in_button.grid(row=0, column=0, padx=6, pady=8, sticky="ew")
        self.sign_up_button = ctk.CTkButton(
            auth_actions, text="Criar conta", command=self.sign_up
        )
        self.sign_up_button.grid(row=0, column=1, padx=6, pady=8, sticky="ew")
        self.account_status_label = ctk.CTkLabel(
            auth_actions, text="Não autenticado", anchor="w"
        )
        self.account_status_label.grid(
            row=0, column=2, padx=8, pady=8, sticky="ew"
        )

        self.demo_mode_checkbox = ctk.CTkCheckBox(
            self,
            text="Modo demonstração local — somente 127.0.0.1:8765",
            variable=self.demo_mode_var,
        )
        self.demo_mode_checkbox.grid(row=4, column=0, padx=20, pady=(0, 8), sticky="w")

        actions_frame = ctk.CTkFrame(self)
        actions_frame.grid(row=5, column=0, padx=20, pady=(0, 8), sticky="ew")
        actions_frame.grid_columnconfigure(0, weight=1)
        actions_frame.grid_columnconfigure(1, weight=0)

        self.file_label = ctk.CTkLabel(
            actions_frame,
            text="Nenhum arquivo selecionado",
            anchor="w",
            width=400,
        )
        self.file_label.grid(row=0, column=0, padx=(15, 10), pady=15, sticky="ew")

        self.select_file_button = ctk.CTkButton(
            actions_frame,
            text="Selecionar arquivo",
            command=self.select_file,
            width=170,
            height=36,
        )
        self.select_file_button.grid(row=0, column=1, padx=(0, 15), pady=15, sticky="e")

        self.connect_xero_button = ctk.CTkButton(
            self,
            text="Conectar Xero",
            command=self.connect_xero,
            width=200,
            height=36,
        )
        self.connect_xero_button.grid(row=6, column=0, padx=20, pady=(0, 8), sticky="ew")

        self.start_button = ctk.CTkButton(
            self,
            text="Iniciar Automação",
            command=self.run_license_and_automation,
            width=200,
            height=42,
            fg_color="#2FA572",
            hover_color="#248460",
        )
        self.start_button.grid(row=7, column=0, padx=20, pady=(0, 8), sticky="ew")

        self.cancel_button = ctk.CTkButton(
            self,
            text="Cancelar",
            command=self.cancel_automation,
            width=200,
            height=36,
            state="disabled",
        )
        self.cancel_button.grid(row=8, column=0, padx=20, pady=(0, 8), sticky="ew")

        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=9, column=0, padx=20, pady=(0, 8), sticky="ew")

        log_label = ctk.CTkLabel(self, text="Log / Status")
        log_label.grid(row=10, column=0, padx=20, sticky="w")

        self.log_box = ctk.CTkTextbox(self, height=150, state="disabled")
        self.log_box.grid(row=11, column=0, padx=20, pady=(5, 20), sticky="nsew")
        self.grid_rowconfigure(11, weight=1)

    def sign_up(self):
        email, password = self.email_var.get().strip(), self.password_var.get()
        if not email or not password:
            self.log("[ERROR] Informe e-mail e senha para criar a conta.")
            return
        self._set_auth_controls("disabled")
        threading.Thread(
            target=self._sign_up_worker, args=(email, password), daemon=True
        ).start()

    def _sign_up_worker(self, email: str, password: str):
        try:
            result = self.auth_client.sign_up(email, password)
            message = (
                "[INFO] Conta criada. Confirme o e-mail e depois entre."
                if result.confirmation_required
                else "[INFO] Conta criada. Entre para concluir o onboarding."
            )
            self.after(0, lambda: self.log(message))
        except SupabaseAuthError as exc:
            message = str(exc)
            self.after(0, lambda: self.log(f"[ERROR] {message}"))
        finally:
            self.after(0, self._set_auth_controls, "normal")

    def sign_in(self):
        email, password = self.email_var.get().strip(), self.password_var.get()
        if not email or not password:
            self.log("[ERROR] Informe e-mail e senha para entrar.")
            return
        self.auth_session = None
        self.account_status_label.configure(text="Autenticando...")
        self._set_auth_controls("disabled")
        threading.Thread(
            target=self._sign_in_worker, args=(email, password), daemon=True
        ).start()

    def _sign_in_worker(self, email: str, password: str):
        try:
            session = self.auth_client.sign_in(email, password)
            installation_id = InstallationIdentityStore().get_installation_id()
            result = self.onboarding_client.register_installation(
                session,
                InstallationRegistrationRequest(installation_id),
            )
            self.after(0, self._set_onboarding_status, session, result)
        except Exception as exc:
            message = str(exc)
            self.after(
                0, self._set_sign_in_failed, message
            )
        finally:
            self.after(0, self._set_auth_controls, "normal")

    def _set_sign_in_failed(self, message: str):
        self.auth_session = None
        self.account_status_label.configure(text="Não autenticado")
        self.log(f"[ERROR] Onboarding indisponível: {message}")

    def _set_onboarding_status(self, session: AuthSession, result: OnboardingResult):
        self.auth_session = session
        self.account_status_label.configure(
            text="Acesso permitido" if result.access_allowed else "Acesso bloqueado"
        )
        if result.access_allowed:
            self.log(f"[OK] Conta associada ao tenant {result.tenant_id}.")
        else:
            self.log(f"[ERROR] Acesso bloqueado: {result.block_reason}.")
        self.log(f"[INFO] Estado da licença: {result.license_status.value}.")
        if result.trial_started_at and result.trial_ends_at:
            self.log(
                "[INFO] Trial: %s até %s (horário informado pelo servidor)."
                % (result.trial_started_at.isoformat(), result.trial_ends_at.isoformat())
            )

    def _set_auth_controls(self, state: str):
        self.sign_in_button.configure(state=state)
        self.sign_up_button.configure(state=state)

    def select_file(self):
        file_path = filedialog.askopenfilename(
            title="Selecionar arquivo local",
            filetypes=[
                ("Arquivos suportados", "*.csv *.xlsx *.pdf"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx"),
                ("PDF", "*.pdf"),
            ],
        )
        if file_path:
            path = Path(file_path)
            file_kind = classify_selected_file(path)
            if file_kind not in {"csv", "xlsx", "pdf"}:
                self.selected_file = None
                self.selected_file_kind = None
                self.file_label.configure(text="Nenhum arquivo selecionado")
                self.log("Erro: selecione um arquivo .csv, .xlsx ou .pdf.")
                return

            self.selected_file = str(path)
            self.selected_file_kind = file_kind
            self.file_label.configure(text=str(path))
            self.log("Arquivo selecionado: %s" % path)
        else:
            self.selected_file = None
            self.selected_file_kind = None
            self.file_label.configure(text="Nenhum arquivo selecionado")

    def log(self, message: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def connect_xero(self):
        try:
            opened = webbrowser.open("http://127.0.0.1:8765/xero/connect")
        except (webbrowser.Error, OSError):
            opened = False

        if not opened:
            self.log(
                "[ERROR] Não foi possível abrir o fluxo Xero. "
                "Verifique se o serviço local está ativo em 127.0.0.1:8765."
            )

    def run_license_and_automation(self):
        demo_mode = self.demo_mode_var.get()
        selected_file_kind = self.selected_file_kind

        if selected_file_kind != "pdf" and not demo_mode and not self.auth_session:
            self.log("[ERROR] Entre na sua conta para verificar a licença.")
            return

        self.start_button.configure(state="disabled")
        self.cancel_event = threading.Event()
        self.cancel_button.configure(state="normal")
        self.progress_bar.set(0)
        if selected_file_kind == "pdf":
            self.log(
                "[INFO] PDF em modo de pré-visualização local; "
                "nenhuma licença ou serviço externo será consultado."
            )
        elif demo_mode:
            self.log("[INFO] Modo demonstração local ativado.")
        else:
            self.log("[INFO] Revalidando o acesso com o servidor...")
        selected_file = self.selected_file
        cancel_event = self.cancel_event
        auth_session = self.auth_session

        thread = threading.Thread(
            target=self._process_license_and_automation,
            args=(
                selected_file,
                selected_file_kind,
                cancel_event,
                demo_mode,
                auth_session,
            ),
            daemon=True,
        )
        thread.start()

    def cancel_automation(self):
        if self.cancel_event and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.log("[INFO] Cancelamento solicitado.")
            self.log("[INFO] Encerrando automação com segurança...")

    def _process_license_and_automation(
        self,
        selected_file: str | None,
        selected_file_kind: str | None,
        cancel_event: threading.Event,
        demo_mode: bool,
        auth_session: AuthSession | None = None,
    ):
        try:
            if selected_file_kind == "pdf":
                if not selected_file:
                    raise PDFInvoicePreviewError("Selecione um arquivo PDF.")
                self.after(
                    0,
                    lambda: self.log(
                        "[INFO] Extraindo texto localmente para pré-visualização..."
                    ),
                )
                result = self.pdf_preview_processor.process_pdf(selected_file)
                if cancel_event.is_set():
                    self.after(0, lambda: self.log("[INFO] Pré-visualização cancelada."))
                    return
                self.after(0, self._show_pdf_preview, result)
                return

            if demo_mode:
                self.after(
                    0,
                    lambda: self.log(
                        "[INFO] Destino fixo: http://127.0.0.1:8765"
                    ),
                )
            else:
                if auth_session is None:
                    self.after(
                        0,
                        lambda: self.log(
                            "[ERROR] Sessão ausente. Entre novamente para continuar."
                        ),
                    )
                    return
                installation_id = InstallationIdentityStore().get_installation_id()
                result = self.onboarding_client.register_installation(
                    auth_session,
                    InstallationRegistrationRequest(installation_id),
                )
                self.after(0, self._set_onboarding_status, auth_session, result)
                if not result.access_allowed:
                    return

            if cancel_event.is_set():
                self.after(0, lambda: self.log("[INFO] Automação cancelada."))
                return
            self.after(0, lambda: self.log("[INFO] Iniciando automação..."))
            executor = AutomationExecutor(file_path=selected_file)
            results = executor.run(
                progress_callback=self._on_progress,
                cancel_event=cancel_event,
            )
            summary = summarize_results(results)
            if cancel_event.is_set():
                self.after(0, lambda: self.log("[OK] Automação cancelada."))
            elif summary["errors"]:
                self.after(0, lambda: self.log("[OK] Automação finalizada com erros."))
            else:
                self.after(0, lambda: self.log("[OK] Automação concluída."))
            self.after(
                0,
                lambda: self._log_summary(summary),
            )
        except Exception as exc:
            message = str(exc)
            self.after(0, lambda: self.log(f"[ERROR] {message}"))
        finally:
            self.after(0, self._restore_button)

    def _show_pdf_preview(self, result: PDFInvoicePreview):
        for line in format_pdf_invoice_preview(result):
            self.log(line)

    def _on_progress(self, current: int, total: int, result: dict[str, object]):
        self.after(0, self._update_progress, current, total, result)

    def _update_progress(self, current: int, total: int, result: dict[str, object]):
        self.progress_bar.set(current / total)
        status = result["status"]
        client_name = result["client_name"]
        if status == "SUCCESS":
            self.log(f"[OK] {current}/{total} - {client_name}")
        elif status == "CANCELLED":
            self.log(f"[INFO] {current}/{total} - {client_name} cancelado")
        else:
            self.log(f"[ERROR] {current}/{total} - {client_name}: {result['message']}")

    def _log_summary(self, summary: dict[str, int]):
        self.log(f"[INFO] Sucesso: {summary['success']}")
        self.log(f"[INFO] Erros: {summary['errors']}")
        self.log(f"[INFO] Cancelados: {summary['cancelled']}")
        self.log("[INFO] Relatório: automation_results.csv")

    def _restore_button(self):
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.cancel_event = None


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = LicenseApp()
    app.mainloop()
