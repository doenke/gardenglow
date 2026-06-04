import unittest


class AppImportTest(unittest.TestCase):
    def test_create_app_imports_without_syntax_errors(self):
        from app import create_app

        self.assertTrue(callable(create_app))


if __name__ == '__main__':
    unittest.main()
