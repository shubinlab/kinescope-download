import importlib.util
import io
import json
import os
import sys
import stat
import subprocess
import tempfile
import types
import unittest
from functools import wraps
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins/kinescope-download/skills/kinescope-video-download/scripts/browser_cookies.py"
)
browser_cookies = None
if SCRIPT.is_file():
    spec = importlib.util.spec_from_file_location("browser_cookies", SCRIPT)
    browser_cookies = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser_cookies)


def requires_api(*names):
    def decorate(test):
        @wraps(test)
        def run(self, *args, **kwargs):
            missing = [name for name in names if browser_cookies is None or not hasattr(browser_cookies, name)]
            self.assertFalse(missing, f"missing cookie helper API: {', '.join(missing)}")
            return test(self, *args, **kwargs)
        return run
    return decorate


def make_cookie(domain, name, value, secure=False, path="/"):
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."), path=path, path_specified=True,
        secure=secure, expires=None, discard=True, comment=None, comment_url=None,
        rest={}, rfc2109=False,
    )


class BrowserCookieTests(unittest.TestCase):
    def test_cookie_helper_is_packaged(self):
        self.assertTrue(SCRIPT.is_file(), "missing shared browser-cookie helper")

    @requires_api("validate_request_url", "CookieError")
    def test_request_url_must_match_https_allowlist(self):
        self.assertEqual(
            browser_cookies.validate_request_url("https://conf.ontico.ru/card", ("conf.ontico.ru",)).hostname,
            "conf.ontico.ru",
        )
        self.assertEqual(
            browser_cookies.validate_request_url("https://edge-msk-10.kinescopecdn.net/master.m3u8", ("edge-msk-10.kinescopecdn.net",)).hostname,
            "edge-msk-10.kinescopecdn.net",
        )
        for url in (
            "http://conf.ontico.ru/card",
            "https://conf.ontico.ru.attacker.invalid/card",
            "https://user@conf.ontico.ru/card",
            "https://conf.ontico.ru:8443/card",
            "https://evil.conf.ontico.ru/card",
            "https://evil.invalid/card",
        ):
            with self.subTest(url=url), self.assertRaises(browser_cookies.CookieError):
                browser_cookies.validate_request_url(url, ("conf.ontico.ru",))
        with self.assertRaises(browser_cookies.CookieError):
            browser_cookies.validate_request_url("https://evil.invalid/card", ("evil.invalid",))

    @requires_api("export_browser_cookies", "CookieError")
    def test_export_uses_yt_dlp_cookie_api_and_filters_to_request_host(self):
        source_jar = CookieJar()
        source_jar.set_cookie(make_cookie("conf.ontico.ru", "ontico_session", "ontico-cookie-sentinel"))
        source_jar.set_cookie(make_cookie(".kinescope.io", "kinescope_session", "kinescope-cookie-sentinel", secure=True))
        source_jar.set_cookie(make_cookie("example.invalid", "other", "other-cookie-sentinel"))
        captured = {}
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")

        def extract(browser, profile=None, logger=None, *, keyring=None, container=None):
            captured.update(browser=browser, keyring=keyring, profile=profile, logger=logger)
            return source_jar

        fake_module.extract_cookies_from_browser = extract
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            jar = browser_cookies.export_browser_cookies(
                "https://conf.ontico.ru/card", "brave+gnomekeyring", ("conf.ontico.ru",), require_matching_cookie=True,
            )

        self.assertEqual(captured["browser"], "brave")
        self.assertEqual(captured["keyring"], "GNOMEKEYRING")
        self.assertEqual([cookie.name for cookie in jar], ["ontico_session"])
        self.assertEqual(list(source_jar), [])
        self.assertNotIn("kinescope-cookie-sentinel", repr(list(jar)))
        self.assertNotIn("other-cookie-sentinel", repr(list(jar)))

        ontico_request = Request("https://conf.ontico.ru/api/card")
        jar.add_cookie_header(ontico_request)
        kinescope_request = Request("https://kinescope.io/video")
        jar.add_cookie_header(kinescope_request)
        self.assertIn("ontico_session=ontico-cookie-sentinel", ontico_request.get_header("Cookie"))
        self.assertIsNone(kinescope_request.get_header("Cookie"))

    @requires_api("export_browser_cookies", "CookieError")
    def test_kinescope_jar_excludes_ontico_cookies(self):
        source_jar = CookieJar()
        source_jar.set_cookie(make_cookie("conf.ontico.ru", "ontico_session", "ontico-sentinel"))
        source_jar.set_cookie(make_cookie(".kinescope.io", "kinescope_session", "kinescope-sentinel", secure=True))
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")
        fake_module.extract_cookies_from_browser = lambda *args, **kwargs: source_jar
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            jar = browser_cookies.export_browser_cookies(
                "https://kinescope.io/video", "brave", ("kinescope.io",),
            )

        self.assertEqual([cookie.name for cookie in jar], ["kinescope_session"])
        request = Request("https://kinescope.io/master.m3u8")
        jar.add_cookie_header(request)
        self.assertIn("kinescope_session=kinescope-sentinel", request.get_header("Cookie"))
        self.assertNotIn("ontico-sentinel", request.get_header("Cookie"))

    @requires_api("write_host_cookie_file", "CookieError")
    def test_cookie_file_is_host_only_private_path_matched_and_does_not_overwrite(self):
        jar = CookieJar()
        jar.set_cookie(make_cookie(".kinescopecdn.net", "cdn_session", "cdn-sentinel", secure=True))
        jar.set_cookie(make_cookie(".kinescopecdn.net", "wrong_path", "path-sentinel", secure=True, path="/private"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cookies.txt"
            browser_cookies.write_host_cookie_file(jar, "https://edge-msk-10.kinescopecdn.net/master.m3u8", path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            saved = MozillaCookieJar(str(path))
            saved.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual([(cookie.domain, cookie.domain_specified, cookie.name) for cookie in saved], [("edge-msk-10.kinescopecdn.net", False, "cdn_session")])
            exact = Request("https://edge-msk-10.kinescopecdn.net/master.m3u8")
            saved.add_cookie_header(exact)
            sibling = Request("https://edge-evil-2.kinescopecdn.net/master.m3u8")
            saved.add_cookie_header(sibling)
            self.assertIn("cdn_session=cdn-sentinel", exact.get_header("Cookie"))
            self.assertIsNone(sibling.get_header("Cookie"))
            with self.assertRaises(browser_cookies.CookieError):
                browser_cookies.write_host_cookie_file(jar, "https://edge-msk-10.kinescopecdn.net/master.m3u8", path)

    @requires_api("export_browser_cookies", "CookieError")
    def test_no_matching_cookie_fails_only_when_required(self):
        empty_jar = CookieJar()
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")
        fake_module.extract_cookies_from_browser = lambda *args, **kwargs: empty_jar
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            self.assertEqual(
                list(browser_cookies.export_browser_cookies("https://kinescope.io/video", "brave", ("kinescope.io",))),
                [],
            )
            with self.assertRaises(browser_cookies.CookieError):
                browser_cookies.export_browser_cookies(
                    "https://conf.ontico.ru/card", "brave", ("conf.ontico.ru",), require_matching_cookie=True,
                )

    @requires_api("export_browser_cookies", "CookieError")
    def test_invalid_url_and_browser_spec_fail_before_cookie_api(self):
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")
        fake_module.extract_cookies_from_browser = lambda *args, **kwargs: self.fail("browser cookie API should not run")
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            with self.assertRaises(browser_cookies.CookieError):
                browser_cookies.export_browser_cookies("https://evil.invalid/card", "brave", ("conf.ontico.ru",))
            with self.assertRaises(browser_cookies.CookieError):
                browser_cookies.export_browser_cookies("https://conf.ontico.ru/card", "--bad", ("conf.ontico.ru",))

    @requires_api("export_browser_cookies", "CookieError")
    def test_cookie_api_errors_are_sanitized(self):
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")

        def fail(*_args, **_kwargs):
            raise RuntimeError("cookie-secret-sentinel")

        fake_module.extract_cookies_from_browser = fail
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            with self.assertRaises(browser_cookies.CookieError) as error:
                browser_cookies.export_browser_cookies("https://conf.ontico.ru/card", "brave", ("conf.ontico.ru",))
        self.assertNotIn("cookie-secret-sentinel", str(error.exception))

    @requires_api("export_browser_cookies", "CookieError", "shutil", "subprocess")
    def test_homebrew_style_fallback_uses_ytdlp_interpreter_and_returns_only_filtered_cookies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interpreter = Path(sys.executable)
            executable = root / "yt-dlp"
            executable.write_text(f"#!{interpreter}\n", encoding="utf-8")
            executable.chmod(0o700)
            captured = {}
            real_run = subprocess.run

            def child(args, **kwargs):
                if args[1:3] == ["-I", "-S"]:
                    return real_run(args, **kwargs)
                captured["args"] = args
                captured["input"] = kwargs["input"]
                captured["env"] = kwargs.get("env")
                output = [{
                    "version": 0, "name": "ontico", "value": "ontico-sentinel", "port": None,
                    "port_specified": False, "domain": "conf.ontico.ru", "domain_specified": False,
                    "domain_initial_dot": False, "path": "/", "path_specified": True,
                    "secure": False, "expires": None, "discard": True, "comment": None,
                    "comment_url": None, "rest": {}, "rfc2109": False,
                }]
                return browser_cookies.subprocess.CompletedProcess(args, 0, stdout=json.dumps(output), stderr="")

            with patch.dict(sys.modules, {"yt_dlp": None, "yt_dlp.cookies": None}):
                with patch.object(browser_cookies.shutil, "which", return_value=str(executable)):
                    with patch.object(browser_cookies.subprocess, "run", side_effect=child):
                        with patch.dict(os.environ, {"TOKEN_SENTINEL": "must-not-inherit"}):
                            try:
                                jar = browser_cookies.export_browser_cookies(
                                    "https://conf.ontico.ru/card", "brave+gnomekeyring", ("conf.ontico.ru",),
                                )
                            except browser_cookies.CookieError:
                                jar = None

            self.assertIsNotNone(jar, "should fall back to yt-dlp's bundled Python interpreter")
            self.assertEqual(captured["args"][0], str(interpreter))
            self.assertNotIn("https://conf.ontico.ru/card", captured["args"])
            self.assertEqual(json.loads(captured["input"])["request_url"], "https://conf.ontico.ru/")
            self.assertIsNotNone(captured["env"])
            self.assertNotIn("TOKEN_SENTINEL", captured["env"])
            self.assertEqual([cookie.name for cookie in jar], ["ontico"])
            self.assertNotIn("other-sentinel", repr(list(jar)))

    @requires_api("export_browser_cookies", "CookieError", "shutil", "subprocess")
    def test_fallback_rejects_python_named_shell_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interpreter = root / "python3.15"
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o700)
            executable = root / "yt-dlp"
            executable.write_text(f"#!{interpreter}\n", encoding="utf-8")
            executable.chmod(0o700)
            real_run = subprocess.run
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                return real_run(args, **kwargs)

            with patch.dict(sys.modules, {"yt_dlp": None, "yt_dlp.cookies": None}):
                with patch.object(browser_cookies.shutil, "which", return_value=str(executable)):
                    with patch.object(browser_cookies.subprocess, "run", side_effect=run):
                        with self.assertRaises(browser_cookies.CookieError):
                            browser_cookies.export_browser_cookies(
                                "https://conf.ontico.ru/card", "brave", ("conf.ontico.ru",),
                            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1:3], ["-I", "-S"])

    @requires_api("export_browser_cookies", "CookieError", "shutil", "subprocess")
    def test_fallback_rejects_shell_launcher_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "yt-dlp"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o700)
            fake_package = types.ModuleType("yt_dlp")
            with patch.dict(sys.modules, {"yt_dlp": None, "yt_dlp.cookies": None}):
                with patch.object(browser_cookies.shutil, "which", return_value=str(executable)):
                    with patch.object(browser_cookies.subprocess, "run") as run:
                        with self.assertRaises(browser_cookies.CookieError):
                            browser_cookies.export_browser_cookies("https://conf.ontico.ru/card", "brave", ("conf.ontico.ru",))
            run.assert_not_called()

    @requires_api("export_browser_cookies", "CookieError")
    def test_cookie_count_is_bounded(self):
        source_jar = CookieJar()
        for i in range(browser_cookies.MAX_COOKIE_COUNT + 1):
            source_jar.set_cookie(make_cookie("conf.ontico.ru", f"sid{i}", "x"))
        fake_package = types.ModuleType("yt_dlp")
        fake_module = types.ModuleType("yt_dlp.cookies")
        fake_module.extract_cookies_from_browser = lambda *args, **kwargs: source_jar
        fake_package.cookies = fake_module
        with patch.dict(sys.modules, {"yt_dlp": fake_package, "yt_dlp.cookies": fake_module}):
            with self.assertRaises(browser_cookies.CookieError):
                browser_cookies.export_browser_cookies("https://conf.ontico.ru/card", "brave", ("conf.ontico.ru",))

    @requires_api("_write_cookie_json", "MAX_COOKIE_JSON_BYTES")
    def test_cookie_json_writer_stops_before_emitting_an_oversized_record(self):
        jar = CookieJar()
        jar.set_cookie(make_cookie("conf.ontico.ru", "sid", "x" * (browser_cookies.MAX_COOKIE_JSON_BYTES + 100)))
        output = io.StringIO()
        with patch("sys.stdout", new=output):
            result = browser_cookies._write_cookie_json(jar)
        self.assertFalse(result)
        self.assertEqual(output.getvalue(), "[")


if __name__ == "__main__":
    unittest.main()
