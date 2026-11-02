import unittest
from pathlib import Path
import importlib.util
import io
import json
import stat
import tempfile
from functools import wraps
from http.cookiejar import CookieJar
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins/kinescope-download/skills/kinescope-video-download/scripts/extract_ontico_card.py"
)
SPEC = importlib.util.spec_from_file_location("extract_ontico_card", SCRIPT)
extract_ontico_card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract_ontico_card)


def requires_api(*names):
    def decorate(test):
        @wraps(test)
        def run(self, *args, **kwargs):
            missing = [name for name in names if not hasattr(extract_ontico_card, name)]
            self.assertFalse(missing, f"missing adapter API: {', '.join(missing)}")
            return test(self, *args, **kwargs)
        return run
    return decorate


class OnticoCardAdapterTests(unittest.TestCase):
    def test_adapter_script_is_packaged(self):
        self.assertTrue(SCRIPT.is_file(), "missing Ontico card adapter")

    @requires_api("parse_card_url")
    def test_only_exact_ontico_card_urls_are_accepted(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        self.assertEqual(extract_ontico_card.parse_card_url(page), (48, 605, page))
        for url in (
            "http://conf.ontico.ru/devnavigator/roadmaps/48/card/605",
            "https://conf.ontico.ru.attacker.invalid/devnavigator/roadmaps/48/card/605",
            "https://user@conf.ontico.ru/devnavigator/roadmaps/48/card/605",
            "https://conf.ontico.ru:8443/devnavigator/roadmaps/48/card/605",
            page + "?token=private",
            page + "#private",
            "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605%2fextra",
            "https://conf.ontico.ru/devnavigator/roadmaps/48/card/../605",
            "https://conf.ontico.ru/devnavigator/roadmaps/0/card/605",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                extract_ontico_card.parse_card_url(url)

    @requires_api("export_browser_cookies")
    def test_browser_export_uses_stdin_and_filters_cookies_to_ontico(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        cookies = """# Netscape HTTP Cookie File
conf.ontico.ru\tFALSE\t/\tFALSE\t0\tconfontico_sid\tontico-cookie-sentinel
.kinescope.io\tTRUE\t/\tTRUE\t0\tkinescope_session\tkinescope-cookie-sentinel
example.invalid\tFALSE\t/\tFALSE\t0\tother\tother-cookie-sentinel
"""
        captured = {}

        def export(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs["input"]
            cookie_path = Path(args[args.index("--cookies") + 1])
            cookie_path.write_text(cookies, encoding="utf-8")
            captured["cookie_path"] = cookie_path
            return extract_ontico_card.subprocess.CompletedProcess(args, 1)

        with patch.object(extract_ontico_card.subprocess, "run", side_effect=export):
            jar = extract_ontico_card.export_browser_cookies(page, "brave+gnomekeyring")

        self.assertEqual(captured["input"], page + "\n")
        self.assertNotIn(page, captured["args"])
        self.assertIn("--cookies-from-browser", captured["args"])
        self.assertEqual([cookie.domain for cookie in jar], ["conf.ontico.ru"])
        self.assertEqual([cookie.name for cookie in jar], ["confontico_sid"])
        self.assertFalse(captured["cookie_path"].exists())

    @requires_api("export_browser_cookies", "AdapterError")
    def test_browser_export_rejects_missing_ontico_cookies(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"

        def export(args, **kwargs):
            Path(args[args.index("--cookies") + 1]).write_text(
                "# Netscape HTTP Cookie File\nexample.invalid\tFALSE\t/\tFALSE\t0\tother\tx\n",
                encoding="utf-8",
            )
            return extract_ontico_card.subprocess.CompletedProcess(args, 1)

        with patch.object(extract_ontico_card.subprocess, "run", side_effect=export):
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.export_browser_cookies(page, "brave+gnomekeyring")

    @requires_api("fetch_card_player_id", "REQUEST_TIMEOUT")
    def test_card_lookup_uses_fixed_endpoint_and_stripped_card_referer(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        expected = "gQEr4JuC4nPsJhHCytEdV3"
        response_body = json.dumps({
            "status": {"name": "success"},
            "result": {"id": 605, "kinescope_id": expected},
        }).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://conf.ontico.ru/api/v1/dev-navigator/cards/605"
            def read(self, limit):
                self.limit = limit
                return response_body

        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        opener = Opener()
        jar = CookieJar()
        with patch.object(extract_ontico_card, "build_opener", return_value=opener):
            player_id = extract_ontico_card.fetch_card_player_id(page, 605, jar)

        self.assertEqual(player_id, expected)
        self.assertEqual(opener.request.full_url, "https://conf.ontico.ru/api/v1/dev-navigator/cards/605")
        self.assertEqual(opener.request.get_header("Referer"), page)
        self.assertEqual(opener.timeout, extract_ontico_card.REQUEST_TIMEOUT)

    @requires_api("fetch_card_player_id", "AdapterError")
    def test_card_lookup_fails_closed_for_bad_json_status_id_or_player_id(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        bodies = (
            b"not json",
            json.dumps({"status": {"name": "error"}, "result": {"id": 605, "kinescope_id": "gQEr4JuC4nPsJhHCytEdV3"}}).encode(),
            json.dumps({"status": {"name": "success"}, "result": {"id": 606, "kinescope_id": "gQEr4JuC4nPsJhHCytEdV3"}}).encode(),
            json.dumps({"status": {"name": "success"}, "result": {"id": 605, "kinescope_id": "https://attacker.invalid/video"}}).encode(),
            json.dumps({"status": {"name": "success"}, "result": {"id": 605}}).encode(),
        )

        for body in bodies:
            class Response:
                def __enter__(self): return self
                def __exit__(self, *_): return None
                def geturl(self): return "https://conf.ontico.ru/api/v1/dev-navigator/cards/605"
                def read(self, limit): return body

            class Opener:
                def open(self, *_args, **_kwargs): return Response()

            with self.subTest(body=body), patch.object(extract_ontico_card, "build_opener", return_value=Opener()):
                with self.assertRaises(extract_ontico_card.AdapterError):
                    extract_ontico_card.fetch_card_player_id(page, 605, CookieJar())

    @requires_api("fetch_card_player_id", "AdapterError", "MAX_API_BYTES")
    def test_card_lookup_caps_api_response(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://conf.ontico.ru/api/v1/dev-navigator/cards/605"
            def read(self, limit):
                self.limit = limit
                return b"x" * limit

        class Opener:
            def __init__(self): self.response = Response()
            def open(self, *_args, **_kwargs): return self.response

        opener = Opener()
        with patch.object(extract_ontico_card, "build_opener", return_value=opener):
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.fetch_card_player_id(page, 605, CookieJar())
        self.assertEqual(opener.response.limit, extract_ontico_card.MAX_API_BYTES + 1)

    @requires_api("fetch_card_player_id", "AdapterError")
    def test_card_lookup_rejects_nondefault_port_on_final_response(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        body = json.dumps({"status": {"name": "success"}, "result": {"id": 605, "kinescope_id": "Abcdef0123456789"}}).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://conf.ontico.ru:8443/api/v1/dev-navigator/cards/605"
            def read(self, _limit): return body

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with patch.object(extract_ontico_card, "build_opener", return_value=Opener()):
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.fetch_card_player_id(page, 605, CookieJar())

    @requires_api("fetch_card_player_id", "AdapterError")
    def test_card_lookup_rejects_final_response_on_other_ontico_path(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        body = json.dumps({"status": {"name": "success"}, "result": {"id": 605, "kinescope_id": "Abcdef0123456789"}}).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://conf.ontico.ru/api/v1/other"
            def read(self, _limit): return body

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with patch.object(extract_ontico_card, "build_opener", return_value=Opener()):
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.fetch_card_player_id(page, 605, CookieJar())

    @requires_api("OnticoRedirects")
    def test_redirects_must_stay_on_exact_https_ontico_origin(self):
        handler = extract_ontico_card.OnticoRedirects()
        from urllib.request import Request
        request = Request("https://conf.ontico.ru/api/v1/dev-navigator/cards/605")
        for url in (
            "http://conf.ontico.ru/api/next",
            "https://conf.ontico.ru.attacker.invalid/api/next",
            "https://evil.invalid/api/next",
            "https://conf.ontico.ru:8443/api/next",
            "https://conf.ontico.ru/api/v1/other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, url)

    @requires_api("write_outputs", "AdapterError")
    def test_output_files_are_private_and_aliases_or_partial_failures_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested").mkdir()
            manifest = root / "player-url.txt"
            referrer = root / "referrer.txt"
            extract_ontico_card.write_outputs(
                "https://kinescope.io/Abcdef0123456789",
                "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605",
                manifest,
                referrer,
            )
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(referrer.stat().st_mode), 0o600)
            self.assertEqual(manifest.read_text(encoding="utf-8"), "https://kinescope.io/Abcdef0123456789\n")
            self.assertEqual(referrer.read_text(encoding="utf-8"), "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605\n")

            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.write_outputs("https://kinescope.io/Abcdef0123456789", "https://conf.ontico.ru/card", manifest, root / "nested" / ".." / "player-url.txt")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            player = root / "player-url.txt"
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.write_outputs(
                    "https://kinescope.io/Abcdef0123456789",
                    "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605",
                    player,
                    root / "missing" / "referrer.txt",
                )
            self.assertFalse(player.exists())

    @requires_api("write_outputs", "AdapterError")
    def test_output_file_collision_never_overwrites_existing_content(self):
        with tempfile.TemporaryDirectory() as temp:
            player = Path(temp) / "player.txt"
            referer = Path(temp) / "referer.txt"
            player.write_text("keep this file\n", encoding="utf-8")
            with self.assertRaises(extract_ontico_card.AdapterError):
                extract_ontico_card.write_outputs(
                    "https://kinescope.io/Abcdef0123456789",
                    "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605",
                    player,
                    referer,
                )
            self.assertEqual(player.read_text(encoding="utf-8"), "keep this file\n")
            self.assertFalse(referer.exists())

    @requires_api("main")
    def test_alias_output_paths_are_rejected_before_cookie_export(self):
        page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested").mkdir()
            player = root / "player.txt"
            alias = root / "nested" / ".." / "player.txt"
            with patch.object(extract_ontico_card, "export_browser_cookies", return_value=CookieJar()) as export:
                with patch.object(extract_ontico_card, "fetch_card_player_id", return_value="Abcdef0123456789"):
                    with patch("sys.stdin", new=io.StringIO(page + "\n")):
                        with patch("sys.stderr", new=io.StringIO()):
                            status = extract_ontico_card.main(["--url-stdin", "brave+gnomekeyring", str(player), str(alias)])

        self.assertEqual(status, 1)
        export.assert_not_called()

    @requires_api("main")
    def test_main_rejects_invalid_page_before_cookie_export(self):
        with patch.object(extract_ontico_card, "export_browser_cookies") as export:
            with patch("sys.stdin", new=io.StringIO("https://attacker.invalid/card/605\n")):
                with patch("sys.stderr", new=io.StringIO()):
                    status = extract_ontico_card.main(["--url-stdin", "brave+gnomekeyring", "player", "referrer"])
        self.assertEqual(status, 1)
        export.assert_not_called()

    @requires_api("export_browser_cookies")
    def test_main_hides_private_urls_and_cookie_export_errors(self):
        secret_page = "https://conf.ontico.ru/devnavigator/roadmaps/48/card/605"
        stderr = io.StringIO()
        with patch.object(extract_ontico_card, "export_browser_cookies", side_effect=OSError("cookie-secret " + secret_page)):
            with patch("sys.stdin", new=io.StringIO(secret_page + "\n")):
                with patch("sys.stderr", new=stderr):
                    status = extract_ontico_card.main(["--url-stdin", "brave+gnomekeyring", "player", "referrer"])
        self.assertEqual(status, 1)
        self.assertNotIn("cookie-secret", stderr.getvalue())
        self.assertNotIn(secret_page, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
