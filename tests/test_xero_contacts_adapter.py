import unittest

from xero.contacts_adapter import XeroContactAdapterError, adapt_xero_contacts


class XeroContactsAdapterTests(unittest.TestCase):
    def test_contact_valid_is_adapted(self):
        data = [{"ContactID": "abc-123", "Name": "João da Silva", "EmailAddress": "joao@example.com"}]

        result = adapt_xero_contacts(data)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].contact_id, "abc-123")
        self.assertEqual(result[0].name, "João da Silva")
        self.assertEqual(result[0].source_system, "xero")
        self.assertNotIn("amount", result[0].as_dict())
        self.assertNotIn("date", result[0].as_dict())
        self.assertNotIn("description", result[0].as_dict())

    def test_contact_without_name_raises(self):
        with self.assertRaises(XeroContactAdapterError):
            adapt_xero_contacts([{"ContactID": "abc-123"}])

    def test_contact_without_contact_id_raises(self):
        with self.assertRaises(XeroContactAdapterError):
            adapt_xero_contacts([{"Name": "João da Silva"}])

    def test_empty_list_returns_empty(self):
        self.assertEqual(adapt_xero_contacts([]), [])

    def test_multiple_contacts_are_preserved(self):
        data = [
            {"ContactID": "1", "Name": "A"},
            {"ContactID": "2", "Name": "B"},
        ]

        result = adapt_xero_contacts(data)

        self.assertEqual([item.name for item in result], ["A", "B"])
        self.assertEqual([item.contact_id for item in result], ["1", "2"])

    def test_no_money_or_dates_are_invented(self):
        data = [{"ContactID": "abc-123", "Name": "João"}]

        result = adapt_xero_contacts(data)
        serialized = result[0].as_dict()

        self.assertNotIn("amount", serialized)
        self.assertNotIn("date", serialized)
        self.assertNotIn("description", serialized)
        self.assertNotIn("client_id", serialized)
        self.assertNotIn("client_name", serialized)


if __name__ == "__main__":
    unittest.main()
