from pathlib import Path
import unittest
from uuid import UUID

from installation_registration_contract import (
    InstallationRegistrationContractError,
    InstallationRegistrationRequest,
)


class InstallationRegistrationContractTests(unittest.TestCase):
    INSTALLATION_ID = UUID("ff1610d3-b36a-4c80-83a0-8c2ff785fe9e")

    def test_payload_contains_only_the_installation_id(self):
        request = InstallationRegistrationRequest(self.INSTALLATION_ID)

        self.assertEqual(
            request.to_payload(),
            {"installation_id": "ff1610d3-b36a-4c80-83a0-8c2ff785fe9e"},
        )

    def test_strict_payload_round_trip(self):
        request = InstallationRegistrationRequest.from_payload(
            {"installation_id": str(self.INSTALLATION_ID)}
        )

        self.assertEqual(request.installation_id, self.INSTALLATION_ID)

    def test_request_constructor_rejects_non_uuid_values(self):
        with self.assertRaises(InstallationRegistrationContractError):
            InstallationRegistrationRequest("not-a-uuid")

    def test_rejects_arbitrary_tenant_and_unexpected_payload_fields(self):
        with self.assertRaises(InstallationRegistrationContractError):
            InstallationRegistrationRequest.from_payload(
                {
                    "installation_id": str(self.INSTALLATION_ID),
                    "tenant_id": "66d204ae-87bc-4ea5-8a37-a3d444ec7f34",
                }
            )

    def test_rejects_non_uuid_or_non_string_installation_id(self):
        for value in ("not-a-uuid", 123, None):
            with self.subTest(value=value):
                with self.assertRaises(InstallationRegistrationContractError):
                    InstallationRegistrationRequest.from_payload(
                        {"installation_id": value}
                    )

    def test_client_contract_modules_have_no_service_role_or_network_client(self):
        project_root = Path(__file__).resolve().parents[1]
        module_paths = (
            project_root / "installation_identity.py",
            project_root / "installation_registration_contract.py",
        )
        for module_path in module_paths:
            source = module_path.read_text(encoding="utf-8").lower()
            with self.subTest(module=module_path.name):
                self.assertNotIn("service_role", source)
                self.assertNotIn("requests", source)
                self.assertNotIn("urllib", source)
                self.assertNotIn("httpx", source)


if __name__ == "__main__":
    unittest.main()
