import sys
import os
import unittest
from unittest.mock import patch


class TestResourcePath(unittest.TestCase):

    def test_dev_mode_uses_file_dir(self):
        """Without _MEIPASS, resolves relative to app.py's directory."""
        import app
        had_meipass = hasattr(sys, "_MEIPASS")
        saved = getattr(sys, "_MEIPASS", None)
        if had_meipass:
            del sys._MEIPASS
        try:
            result = app.resource_path("ui/index.html")
            expected = os.path.join(os.path.dirname(os.path.abspath(app.__file__)), "ui/index.html")
            self.assertEqual(result, expected)
        finally:
            if had_meipass:
                sys._MEIPASS = saved

    def test_bundle_mode_uses_meipass(self):
        """With _MEIPASS set, resolves relative to bundle temp dir."""
        import app
        with patch.object(sys, "_MEIPASS", "/tmp/bundle", create=True):
            result = app.resource_path("ui/index.html")
            self.assertEqual(result, "/tmp/bundle/ui/index.html")


class TestErrorHtml(unittest.TestCase):

    def test_contains_message(self):
        import app
        html = app.error_html("something went wrong")
        self.assertIn("something went wrong", html)

    def test_is_valid_html_string(self):
        import app
        html = app.error_html("err")
        self.assertIn("<html", html)
        self.assertIn("</html>", html)

    def test_escapes_html_in_message(self):
        import app
        html = app.error_html("<script>alert('xss')</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_escapes_ampersand_in_message(self):
        import app
        html = app.error_html("foo & bar")
        self.assertNotIn("foo & bar", html)
        self.assertIn("&amp;", html)


class TestWaitForServer(unittest.TestCase):

    def setUp(self):
        import app
        app.server_error = None

    def test_returns_true_when_health_responds(self):
        import app
        with patch("app.httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            result = app.wait_for_server(timeout=5, interval=0.1)
        self.assertTrue(result)

    def test_returns_false_on_timeout(self):
        import app
        with patch("app.httpx.get", side_effect=Exception("refused")):
            result = app.wait_for_server(timeout=0.3, interval=0.1)
        self.assertFalse(result)

    def test_returns_false_immediately_when_server_error_set(self):
        import app
        app.server_error = RuntimeError("boom")
        with patch("app.httpx.get", side_effect=Exception("refused")):
            result = app.wait_for_server(timeout=10, interval=0.1)
        self.assertFalse(result)
        app.server_error = None
