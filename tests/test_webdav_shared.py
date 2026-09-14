"""Ensure desktop and Web share the extracted original WebDAV implementation."""
import ast
from pathlib import Path
import unittest


class SharedWebDAVTest(unittest.TestCase):
    def test_desktop_imports_shared_client(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'ui/other_settings.py').read_text(encoding='utf-8'))
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == 'webdav_client'
                            and any(a.name == 'WebDAVClient' for a in n.names) for n in tree.body))
        self.assertFalse(any(isinstance(n, ast.ClassDef) and n.name == 'WebDAVClient' for n in tree.body))
        client = ast.parse((root / 'webdav_client.py').read_text(encoding='utf-8'))
        cls = next(n for n in client.body if isinstance(n, ast.ClassDef))
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertTrue({'list_directory', 'upload_file', 'download_file', 'backup',
                         'ensure_directory_exists', 'create_directory'}.issubset(methods))


if __name__ == '__main__':
    unittest.main()
