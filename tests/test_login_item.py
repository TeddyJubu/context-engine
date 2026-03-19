import os
import plistlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

FAKE_PLIST = Path(tempfile.mktemp(suffix=".plist"))


class TestGetOpenAtLogin(unittest.TestCase):

    def setUp(self):
        FAKE_PLIST.unlink(missing_ok=True)

    def tearDown(self):
        FAKE_PLIST.unlink(missing_ok=True)

    def test_returns_false_when_plist_absent(self):
        with patch("login_item.PLIST_PATH", FAKE_PLIST):
            import login_item
            api = login_item.Api()
            result = api.get_open_at_login()
        self.assertEqual(result, {"enabled": False})

    def test_returns_true_when_plist_present(self):
        FAKE_PLIST.write_bytes(b"placeholder")
        with patch("login_item.PLIST_PATH", FAKE_PLIST):
            import login_item
            api = login_item.Api()
            result = api.get_open_at_login()
        self.assertEqual(result, {"enabled": True})


class TestSetOpenAtLogin(unittest.TestCase):

    def setUp(self):
        FAKE_PLIST.unlink(missing_ok=True)

    def tearDown(self):
        FAKE_PLIST.unlink(missing_ok=True)

    def test_enable_writes_plist_and_calls_launchctl(self):
        with patch("login_item.PLIST_PATH", FAKE_PLIST), \
             patch("login_item.subprocess.run") as mock_run, \
             patch("login_item.os.getuid", return_value=501):
            mock_run.return_value.returncode = 0
            import login_item
            api = login_item.Api()
            result = api.set_open_at_login(True)

        self.assertEqual(result, {"ok": True})
        self.assertTrue(FAKE_PLIST.exists())
        plist = plistlib.loads(FAKE_PLIST.read_bytes())
        self.assertEqual(plist["Label"], "com.contextengine.app")
        self.assertTrue(plist["RunAtLoad"])
        mock_run.assert_called_once()
        # Verify full bootstrap command — all four args must be present
        call_kwargs = mock_run.call_args
        cmd = call_kwargs[0][0]
        self.assertEqual(cmd, ["launchctl", "bootstrap", "gui/501", str(FAKE_PLIST)])
        # Subprocess is called with capture_output/text so returncode is available
        self.assertTrue(call_kwargs[1].get("capture_output"))
        self.assertTrue(call_kwargs[1].get("text"))

    def test_enable_raises_on_launchctl_failure(self):
        with patch("login_item.PLIST_PATH", FAKE_PLIST), \
             patch("login_item.subprocess.run") as mock_run, \
             patch("login_item.os.getuid", return_value=501):
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "bootstrap failed: service already loaded"
            import login_item
            api = login_item.Api()
            with self.assertRaises(RuntimeError) as ctx:
                api.set_open_at_login(True)
        self.assertIn("bootstrap failed", str(ctx.exception))
        # Plist must be cleaned up on failure
        self.assertFalse(FAKE_PLIST.exists())

    def test_disable_calls_bootout_and_removes_plist(self):
        FAKE_PLIST.write_bytes(b"placeholder")
        with patch("login_item.PLIST_PATH", FAKE_PLIST), \
             patch("login_item.subprocess.run") as mock_run, \
             patch("login_item.os.getuid", return_value=501):
            mock_run.return_value.returncode = 0
            import login_item
            api = login_item.Api()
            result = api.set_open_at_login(False)

        self.assertEqual(result, {"ok": True})
        self.assertFalse(FAKE_PLIST.exists())
        # Verify full bootout command — domain and plist path must be present
        call_kwargs = mock_run.call_args
        cmd = call_kwargs[0][0]
        self.assertEqual(cmd, ["launchctl", "bootout", "gui/501", str(FAKE_PLIST)])
        self.assertTrue(call_kwargs[1].get("capture_output"))
        self.assertTrue(call_kwargs[1].get("text"))

    def test_disable_raises_on_launchctl_failure(self):
        FAKE_PLIST.write_bytes(b"placeholder")
        with patch("login_item.PLIST_PATH", FAKE_PLIST), \
             patch("login_item.subprocess.run") as mock_run, \
             patch("login_item.os.getuid", return_value=501):
            mock_run.return_value.returncode = 3
            mock_run.return_value.stderr = "bootout failed: no such service"
            import login_item
            api = login_item.Api()
            with self.assertRaises(RuntimeError) as ctx:
                api.set_open_at_login(False)
        self.assertIn("bootout failed", str(ctx.exception))
        # Plist is still removed even if bootout fails (best-effort cleanup)
        self.assertFalse(FAKE_PLIST.exists())
