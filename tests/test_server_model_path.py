import sys
import unittest
from unittest.mock import patch


class TestGetModelCacheDir(unittest.TestCase):

    def test_returns_none_when_not_bundled(self):
        """In dev mode (no _MEIPASS), should return None so SentenceTransformer uses default cache."""
        import server
        had_meipass = hasattr(sys, "_MEIPASS")
        saved = getattr(sys, "_MEIPASS", None)
        if had_meipass:
            del sys._MEIPASS
        try:
            result = server.get_model_cache_dir()
            self.assertIsNone(result)
        finally:
            if had_meipass:
                sys._MEIPASS = saved

    def test_returns_bundled_path_when_meipass_set(self):
        """In PyInstaller bundle (_MEIPASS is set), should return path inside bundle."""
        import server
        with patch.object(sys, "_MEIPASS", "/tmp/fake_bundle", create=True):
            result = server.get_model_cache_dir()
            self.assertEqual(result, "/tmp/fake_bundle/sentence_transformers_cache")
