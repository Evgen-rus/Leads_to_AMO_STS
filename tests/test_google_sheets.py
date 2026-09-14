import unittest

from lead_hub.google_sheets import find_result_column


class GoogleSheetsTest(unittest.TestCase):
    def test_result_column_uses_header_then_k_fallback(self):
        self.assertEqual(find_result_column(["ID", "Ссылка_AmoCRM"], "Ссылка_AmoCRM"), 1)
        self.assertEqual(find_result_column(["ID", "", "Телефон"], "Ссылка_AmoCRM"), 10)


if __name__ == "__main__":
    unittest.main()
