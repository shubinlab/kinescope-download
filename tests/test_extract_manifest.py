import io
import importlib.util
import stat
import sys
import tempfile
import unittest
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.request import HTTPCookieProcessor
from urllib.request import Request
from urllib.request import Request

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins/kinescope-download/skills/kinescope-video-download/scripts/extract_manifest.py"
)
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("extract_manifest", MODULE_PATH)
extract_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract_manifest)


class ExtractManifestTests(unittest.TestCase):
    def setUp(self):
        self.cookie_export_patch = patch.object(extract_manifest, "export_browser_cookies", return_value=CookieJar())
        self.cookie_export = self.cookie_export_patch.start()
        self.addCleanup(self.cookie_export_patch.stop)

    def test_manifest_helper_uses_shared_browser_cookie_exporter(self):
        self.assertTrue(hasattr(extract_manifest, "export_browser_cookies"))

    def test_only_https_kinescope_hosts_and_default_port_are_trusted(self):
        accepted = (
            "https://kinescope.io/video",
            "https://player.kinescope.io/video",
            "https://cdn.kinescopecdn.net/master.m3u8",
        )
        rejected = (
            "http://kinescope.io/video",
            "https://kinescope.io.attacker.invalid/video",
            "https://127.0.0.1/video",
            "https://kinescope.io:8443/video",
            "https://user@kinescope.io/video",
            "https://attacker.kinescope.io/video",
            "https://attacker.kinescopecdn.net/master.m3u8",
            "https://edge-evil-1.kinescopecdn.net/master.m3u8",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(extract_manifest.trusted_https_url(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(extract_manifest.trusted_https_url(url))

    def test_manifest_download_gets_a_cookie_file_for_the_exact_manifest_host(self):
        manifest_url = "https://edge-msk-10.kinescopecdn.net/master.m3u8?sign=fixture"
        page_cookies = CookieJar()
        media_cookies = CookieJar()
        media_cookies.set_cookie(Cookie(
            version=0, name="cdn_session", value="cdn-sentinel", port=None, port_specified=False,
            domain=".kinescopecdn.net", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True, secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False,
        ))
        media_cookies.set_cookie(Cookie(
            version=0, name="ontico_session", value="ontico-sentinel", port=None, port_specified=False,
            domain="conf.ontico.ru", domain_specified=False, domain_initial_dot=False,
            path="/", path_specified=True, secure=False, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False,
        ))
        self.cookie_export.side_effect = [page_cookies, media_cookies]

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://kinescope.io/video"
            def read(self, _limit): return f'<script>"{manifest_url}"</script>'.encode()

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.txt"
            cookies = Path(str(manifest) + ".cookies")
            with patch.object(extract_manifest, "build_opener", return_value=Opener()):
                with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\nhttps://conf.ontico.ru/card/605\n")):
                    with patch("sys.stdout", new=io.StringIO()):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(manifest)])

            self.assertEqual(status, 0)
            self.assertEqual(stat.S_IMODE(cookies.stat().st_mode), 0o600)
            self.assertTrue(cookies.read_text(encoding="utf-8").startswith("# Netscape HTTP Cookie File"))
            saved = MozillaCookieJar(str(cookies))
            saved.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual(
                [(cookie.domain, cookie.domain_specified, cookie.name) for cookie in saved],
                [("edge-msk-10.kinescopecdn.net", False, "cdn_session")],
            )
            request = Request("https://edge-msk-10.kinescopecdn.net/master.m3u8")
            saved.add_cookie_header(request)
            self.assertIn("cdn_session=cdn-sentinel", request.get_header("Cookie"))
            other = Request("https://edge-evil-1.kinescopecdn.net/master.m3u8")
            saved.add_cookie_header(other)
            self.assertIsNone(other.get_header("Cookie"))

    def test_private_page_url_is_rejected_before_any_request(self):
        with patch.object(extract_manifest, "build_opener") as build_opener:
            with patch("sys.stdin", new=io.StringIO("http://127.0.0.1:8080/\n\n")):
                status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", "/tmp/manifest"])
        self.assertEqual(status, 1)
        build_opener.assert_not_called()

    def test_helper_rejects_positional_url_without_echoing_it(self):
        signed_url = "https://kinescope.io/video?token=private"
        stderr = io.StringIO()
        with patch.object(extract_manifest, "build_opener") as build_opener:
            with patch("sys.stderr", new=stderr):
                status = extract_manifest.main([signed_url, "/tmp/manifest"])
        self.assertEqual(status, 2)
        self.assertNotIn("token=private", stderr.getvalue())
        build_opener.assert_not_called()

    def test_signed_page_url_can_be_read_from_stdin_without_argv_or_output_disclosure(self):
        signed_url = "https://kinescope.io/video?token=private"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return b'"https://cdn.kinescopecdn.net/master.m3u8"'

        class Opener:
            def open(self, request, **_kwargs):
                self.request_url = request.full_url
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            opener = Opener()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(extract_manifest, "build_opener", return_value=opener):
                with patch("sys.stdin", new=io.StringIO(signed_url + "\n")):
                    with patch("sys.stdout", new=stdout), patch("sys.stderr", new=stderr):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(output)])
            self.assertEqual(status, 0)
            self.assertEqual(opener.request_url, signed_url)
            self.assertEqual(output.read_text(encoding="utf-8"), "https://cdn.kinescopecdn.net/master.m3u8\n")
            self.assertNotIn("token=private", stdout.getvalue() + stderr.getvalue())
            self.cookie_export.assert_any_call(signed_url, "brave+gnomekeyring", ("kinescope.io",))
            self.cookie_export.assert_any_call("https://cdn.kinescopecdn.net/master.m3u8", "brave+gnomekeyring", ("cdn.kinescopecdn.net",))
            self.assertTrue(Path(str(output) + ".cookies").is_file())

    def test_manifest_fetch_uses_the_filtered_browser_cookie_jar(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def geturl(self): return "https://kinescope.io/video"
            def read(self, _limit): return b'"https://cdn.kinescopecdn.net/master.m3u8"'

        class Opener:
            def open(self, *_args, **_kwargs): return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            jar = CookieJar()
            self.cookie_export.return_value = jar
            with patch.object(extract_manifest, "build_opener", return_value=Opener()) as builder:
                with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\n\n")):
                    with patch("sys.stdout", new=io.StringIO()):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(output)])
            self.assertEqual(status, 0)
            cookie_processor = builder.call_args.args[0]
            self.assertIsInstance(cookie_processor, HTTPCookieProcessor)
            self.assertIs(cookie_processor.cookiejar, jar)

    def test_referrer_query_is_sanitized_from_stdin_to_private_file(self):
        page_url = "https://kinescope.io/video?token=page-secret"
        referrer = "https://course.example/watch?access_token=ref-secret#fragment"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return b'"https://cdn.kinescopecdn.net/master.m3u8"'

        class Opener:
            def open(self, request, **_kwargs):
                self.referer = request.get_header("Referer")
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            safe_referer = Path(temp) / "referer.txt"
            opener = Opener()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(extract_manifest, "build_opener", return_value=opener):
                with patch("sys.stdin", new=io.StringIO(page_url + "\n" + referrer + "\n")):
                    with patch("sys.stdout", new=stdout), patch("sys.stderr", new=stderr):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(output), str(safe_referer)])
            self.assertEqual(status, 0)
            self.assertEqual(opener.referer, "https://course.example/watch")
            self.assertEqual(safe_referer.read_text(encoding="utf-8"), "https://course.example/watch\n")
            self.assertEqual(stat.S_IMODE(safe_referer.stat().st_mode), 0o600)
            self.assertNotIn("page-secret", stdout.getvalue() + stderr.getvalue())
            self.assertNotIn("ref-secret", stdout.getvalue() + stderr.getvalue())

    def test_network_error_text_is_not_echoed_when_it_contains_signed_url(self):
        signed_url = "https://kinescope.io/video?token=private"

        class Opener:
            def open(self, *_args, **_kwargs):
                raise OSError(f"403 for {signed_url}")

        stderr = io.StringIO()
        with patch.object(extract_manifest, "build_opener", return_value=Opener()):
            with patch("sys.stdin", new=io.StringIO(signed_url + "\n")):
                with patch("sys.stderr", new=stderr):
                    status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", "/tmp/manifest"])
        self.assertEqual(status, 1)
        self.assertNotIn("token=private", stderr.getvalue())

    def test_sensitive_or_credentialed_referrers_are_rejected_before_any_request(self):
        for referrer in (
            "https://user:pass@example.com/course",
            "file:///tmp/private",
        ):
            with self.subTest(referrer=referrer):
                with patch.object(extract_manifest, "build_opener") as build_opener:
                    with patch("sys.stderr", new_callable=io.StringIO):
                        with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\n" + referrer + "\n")):
                            status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", "/tmp/manifest"])
                self.assertEqual(status, 1)
                build_opener.assert_not_called()

    def test_referrer_fragment_is_removed_before_request(self):
        self.assertEqual(
            extract_manifest.prepare_referrer("https://example.com/course?unit=1#private"),
            "https://example.com/course",
        )

    def test_arbitrary_referrer_query_parameters_are_removed(self):
        self.assertEqual(
            extract_manifest.prepare_referrer("https://example.com/course?opaque=value"),
            "https://example.com/course",
        )

    def test_redirect_to_private_host_is_rejected(self):
        handler = extract_manifest.TrustedRedirects("kinescope.io")
        request = object()
        with self.assertRaisesRegex(ValueError, "Redirect destination"):
            handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/")

    def test_redirect_to_lookalike_domain_is_rejected(self):
        handler = extract_manifest.TrustedRedirects("kinescope.io")
        with self.assertRaisesRegex(ValueError, "Redirect destination"):
            handler.redirect_request(object(), None, 302, "Found", {}, "https://kinescope.io.attacker.invalid/")
        with self.assertRaisesRegex(ValueError, "Redirect destination"):
            handler.redirect_request(object(), None, 302, "Found", {}, "https://edge.kinescopecdn.net/")

    def test_page_read_is_limited_to_one_byte_over_cap(self):
        class Response:
            def __init__(self):
                self.read_limit = None

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, limit):
                self.read_limit = limit
                return b"x" * limit

        response = Response()

        class Opener:
            def open(self, *_args, **_kwargs):
                return response

        with patch.object(extract_manifest, "build_opener", return_value=Opener()):
            with patch("sys.stderr", new_callable=io.StringIO):
                with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\n\n")):
                    status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", "/tmp/manifest"])
        self.assertEqual(status, 1)
        self.assertEqual(response.read_limit, extract_manifest.MAX_PAGE_BYTES + 1)

    def test_one_manifest_is_saved_without_printing_the_signed_url(self):
        signed_url = "https://cdn.kinescopecdn.net/master.m3u8?token=private"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return f'<script>"{signed_url}"</script>'.encode()

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            stdout = io.StringIO()
            with patch.object(extract_manifest, "build_opener", return_value=Opener()):
                with patch("sys.stdout", new=stdout):
                    with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\n\n")):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(output)])
            self.assertEqual(status, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), signed_url + "\n")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertNotIn(signed_url, stdout.getvalue())

    def test_existing_output_file_is_never_overwritten(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return b'"https://cdn.kinescopecdn.net/master.m3u8"'

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            output.write_text("keep this file\n", encoding="utf-8")
            with patch.object(extract_manifest, "build_opener", return_value=Opener()) as build_opener:
                with patch("sys.stderr", new_callable=io.StringIO):
                    with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\n\n")):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(output)])
            self.assertEqual(status, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep this file\n")
            build_opener.assert_not_called()

    def test_output_alias_paths_are_rejected_before_network_access(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return b'"https://cdn.kinescopecdn.net/master.m3u8"'

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested").mkdir()
            manifest = root / "manifest.txt"
            alias = root / "nested" / ".." / "manifest.txt"
            with patch.object(extract_manifest, "build_opener", return_value=Opener()) as build_opener:
                with patch("sys.stderr", new_callable=io.StringIO):
                    with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\nhttps://course.example/card\n")):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(manifest), str(alias)])

        self.assertEqual(status, 1)
        build_opener.assert_not_called()

    def test_manifest_is_removed_if_referrer_file_creation_fails(self):
        signed_url = "https://cdn.kinescopecdn.net/master.m3u8?token=private"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def geturl(self):
                return "https://kinescope.io/video"

            def read(self, _limit):
                return f'<script>"{signed_url}"</script>'.encode()

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.txt"
            missing_referrer = root / "missing" / "referrer.txt"
            stderr = io.StringIO()
            with patch.object(extract_manifest, "build_opener", return_value=Opener()):
                with patch("sys.stdin", new=io.StringIO("https://kinescope.io/video\nhttps://course.example/card\n")):
                    with patch("sys.stderr", new=stderr):
                        status = extract_manifest.main(["--url-stdin", "brave+gnomekeyring", str(manifest), str(missing_referrer)])

            self.assertEqual(status, 1)
            self.assertFalse(manifest.exists())
            self.assertFalse(Path(str(manifest) + ".cookies").exists())
            self.assertNotIn(signed_url, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
