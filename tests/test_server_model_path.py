import sys
import importlib
import unittest
from unittest.mock import patch

class TestGetModelCacheDir(unittest.TestCase):

    def test_returns_none_when_not_bundled(self):
        """In dev mode (no _MEIPASS), should return None so SentenceTransformer uses default cache."""
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
        import server
        importlib.reload(server)
        result = server.get_model_cache_dir()
        self.assertIsNone(result)

    def test_returns_bundled_path_when_meipass_set(self):
        """In PyInstaller bundle (_MEIPASS is set), should return path inside bundle."""
        with patch.object(sys, "_MEIPASS", "/tmp/fake_bundle", create=True):
            import server
            importlib.reload(server)
            result = server.get_model_cache_dir()
            self.assertEqual(result, "/tmp/fake_bundle/sentence_transformers_cache")
