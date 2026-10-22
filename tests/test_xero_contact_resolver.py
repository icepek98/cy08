import unittest

from xero.contact_resolver import (
    XeroContactResolutionError,
    resolve_xero_contact_id,
)
from xero.contacts_adapter import XeroContactAdapterError


class XeroContactResolverTests(unittest.TestCase):
    def test_exact_name_returns_contact_id(self):
        result = resolve_xero_contact_id(
            "ACME LTD",
            [{"ContactID": "contact-1", "Name": "ACME LTD"}],
        )

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.contact_id, "contact-1")
        self.assertEqual(result.match_count, 1)

    def test_name_comparison_is_case_insensitive(self):
        result = resolve_xero_contact_id(
            "acme ltd",
            [{"ContactID": "contact-1", "Name": "ACME LTD"}],
        )

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.contact_id, "contact-1")

    def test_extra_whitespace_is_collapsed_and_trimmed(self):
        result = resolve_xero_contact_id(
            " ACME   LTD ",
            [{"ContactID": "contact-1", "Name": "Acme Ltd"}],
        )

        self.assertEqual(result.status, "matched")
        self.assertEqual(result.contact_id, "contact-1")

    def test_no_match_returns_explicit_not_found_result(self):
        result = resolve_xero_contact_id("ACME LTD", [])

        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.contact_id)
        self.assertEqual(result.match_count, 0)

    def test_duplicate_normalized_names_return_ambiguity(self):
        result = resolve_xero_contact_id(
            "ACME LTD",
            [
                {"ContactID": "contact-1", "Name": "Acme Ltd"},
                {"ContactID": "contact-2", "Name": " ACME   LTD "},
            ],
        )

        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.contact_id)
        self.assertEqual(result.match_count, 2)

    def test_similar_but_different_names_do_not_match(self):
        result = resolve_xero_contact_id(
            "ACME LTD",
            [{"ContactID": "contact-1", "Name": "ACME LIMITED"}],
        )

        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.contact_id)

    def test_accents_and_punctuation_are_not_removed(self):
        for source_name in ("Cafe Ltd", "ACME-LTD"):
            with self.subTest(source_name=source_name):
                result = resolve_xero_contact_id(
                    "Café Ltd" if source_name == "Cafe Ltd" else "ACME LTD",
                    [{"ContactID": "contact-1", "Name": source_name}],
                )

                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.contact_id)

    def test_contact_without_contact_id_raises_existing_adapter_error(self):
        with self.assertRaises(XeroContactAdapterError):
            resolve_xero_contact_id(
                "ACME LTD",
                [{"Name": "ACME LTD"}],
            )

    def test_contact_without_name_raises_existing_adapter_error(self):
        with self.assertRaises(XeroContactAdapterError):
            resolve_xero_contact_id(
                "ACME LTD",
                [{"ContactID": "contact-1"}],
            )

    def test_empty_fieldquo_name_is_rejected(self):
        with self.assertRaises(XeroContactResolutionError):
            resolve_xero_contact_id("   ", [])


if __name__ == "__main__":
    unittest.main()
