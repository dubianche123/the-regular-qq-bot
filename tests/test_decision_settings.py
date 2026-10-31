"""判断 API 本地配置：校验、落盘权限与安全视图。"""
import json
import stat
import tempfile
import unittest
from pathlib import Path

from regular import decision_settings


class DecisionSettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = Path(self.dir) / decision_settings.FILENAME

    def test_absent_file_public_defaults_and_no_key(self):
        self.assertIsNone(decision_settings.load_settings(self.dir))
        view = decision_settings.public_settings(self.dir)
        self.assertEqual(view, {"endpoint": decision_settings.DEFAULT_ENDPOINT,
                                "model": decision_settings.DEFAULT_MODEL,
                                "key_configured": False})
        self.assertEqual(decision_settings.DEFAULT_ENDPOINT,
                         "https://api.openai.com/v1/decisions")
        self.assertEqual(decision_settings.DEFAULT_MODEL, "gpt-6-luna")

    def test_save_roundtrip_0600_and_public_view_has_no_key(self):
        decision_settings.save_settings(
            self.dir, endpoint=" https://api.openai.com/v1/decisions ",
            model="gpt-6-luna", api_key="sk-test-abc")
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved, {"endpoint": "https://api.openai.com/v1/decisions",
                                 "model": "gpt-6-luna", "api_key": "sk-test-abc"})
        loaded = decision_settings.load_settings(self.dir)
        self.assertEqual(loaded["api_key"], "sk-test-abc")
        view = decision_settings.public_settings(self.dir)
        self.assertTrue(view["key_configured"])
        self.assertNotIn("api_key", view)
        self.assertNotIn("sk-test-abc", json.dumps(view))

    def test_corrupt_file_reads_as_unconfigured(self):
        self.path.write_text("{broken json", encoding="utf-8")
        self.assertIsNone(decision_settings.load_settings(self.dir))
        self.assertFalse(decision_settings.public_settings(self.dir)["key_configured"])

    def test_loopback_http_allowed_but_everything_else_is_https(self):
        for url in ("http://localhost:8080/decisions", "http://127.0.0.1:9000/x",
                    "http://[::1]:8080/x"):
            e, _m, _k = decision_settings._validate(url, "m", "k")
            self.assertEqual(e, url)
        for url in ("http://api.openai.com/v1/decisions",
                    "https://user:pw@api.openai.com/v1/decisions",
                    "https://api.openai.com/v1/decisions?x=1",
                    "https://api.openai.com/v1/decisions#frag",
                    "ftp://api.openai.com/x", "notaurl", "https:///nohost",
                    "//api.openai.com/x"):
            with self.assertRaises(ValueError, msg=url):
                decision_settings._validate(url, "m", "k")

    def test_bad_fields_rejected_with_generic_error(self):
        cases = [
            dict(api_key=""), dict(api_key="   "), dict(api_key="a\nb"),
            dict(api_key="x" * 5000), dict(model=""), dict(model="m" * 300),
            dict(endpoint=""), dict(endpoint="e" * 3000),
            dict(model="m\nx"), dict(endpoint="https://a.com\nx"),
        ]
        base = dict(endpoint="https://api.openai.com/v1/decisions",
                    model="gpt-6-luna", api_key="sk-ok")
        for bad in cases:
            kw = dict(base); kw.update(bad)
            with self.assertRaises(ValueError, msg=bad):
                decision_settings.save_settings(self.dir, **kw)
        self.assertFalse(self.path.exists())   # 一次都没写成

    def test_errors_never_echo_submitted_values(self):
        try:
            decision_settings.save_settings(
                self.dir, endpoint="https://user:secretpw@x.io",
                model="m", api_key="SECRET_KEY_VALUE")
        except ValueError as error:
            self.assertNotIn("SECRET_KEY_VALUE", str(error))
            self.assertNotIn("secretpw", str(error))
        else:
            self.fail("expected ValueError")


if __name__ == "__main__":
    unittest.main()
