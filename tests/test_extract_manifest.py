import io
import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins/kinescope-download/skills/kinescope-video-download/scripts/extract_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("extract_manifest", MODULE_PATH)
extract_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract_manifest)


class ExtractManifestTests(unittest.TestCase):
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
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(extract_manifest.trusted_https_url(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(extract_manifest.trusted_https_url(url))

    def test_private_page_url_is_rejected_before_any_request(self):
        with patch.object(extract_manifest, "build_opener") as build_opener:
            status = extract_manifest.main(["http://127.0.0.1:8080/", "/tmp/manifest"])
        self.assertEqual(status, 1)
        build_opener.assert_not_called()

    def test_positional_url_with_query_is_rejected_before_any_request(self):
        signed_url = "https://kinescope.io/video?token=private"
        stderr = io.StringIO()
        with patch.object(extract_manifest, "build_opener") as build_opener:
            with patch("sys.stderr", new=stderr):
                status = extract_manifest.main([signed_url, "/tmp/manifest"])
        self.assertEqual(status, 1)
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
                return b'"https://cdn.kinescope.io/master.m3u8"'

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
                        status = extract_manifest.main(["--url-stdin", str(output)])
            self.assertEqual(status, 0)
            self.assertEqual(opener.request_url, signed_url)
            self.assertEqual(output.read_text(encoding="utf-8"), "https://cdn.kinescope.io/master.m3u8\n")
            self.assertNotIn("token=private", stdout.getvalue() + stderr.getvalue())

    def test_network_error_text_is_not_echoed_when_it_contains_signed_url(self):
        signed_url = "https://kinescope.io/video?token=private"

        class Opener:
            def open(self, *_args, **_kwargs):
                raise OSError(f"403 for {signed_url}")

        stderr = io.StringIO()
        with patch.object(extract_manifest, "build_opener", return_value=Opener()):
            with patch("sys.stdin", new=io.StringIO(signed_url + "\n")):
                with patch("sys.stderr", new=stderr):
                    status = extract_manifest.main(["--url-stdin", "/tmp/manifest"])
        self.assertEqual(status, 1)
        self.assertNotIn("token=private", stderr.getvalue())

    def test_sensitive_or_credentialed_referrers_are_rejected_before_any_request(self):
        for referrer in (
            "https://user:pass@example.com/course",
            "https://example.com/course?access_token=secret",
            "https://example.com/course?api_key=secret",
            "https://example.com/course?key=secret",
            "https://example.com/course?accessKey=secret",
            "file:///tmp/private",
        ):
            with self.subTest(referrer=referrer):
                with patch.object(extract_manifest, "build_opener") as build_opener:
                    with patch("sys.stderr", new_callable=io.StringIO):
                        status = extract_manifest.main(
                            ["https://kinescope.io/video", "/tmp/manifest", referrer]
                        )
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
        handler = extract_manifest.TrustedRedirects()
        request = object()
        with self.assertRaisesRegex(ValueError, "Redirect destination"):
            handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/")

    def test_redirect_to_lookalike_domain_is_rejected(self):
        handler = extract_manifest.TrustedRedirects()
        with self.assertRaisesRegex(ValueError, "Redirect destination"):
            handler.redirect_request(object(), None, 302, "Found", {}, "https://kinescope.io.attacker.invalid/")

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
                status = extract_manifest.main(["https://kinescope.io/video", "/tmp/manifest"])
        self.assertEqual(status, 1)
        self.assertEqual(response.read_limit, extract_manifest.MAX_PAGE_BYTES + 1)

    def test_one_manifest_is_saved_without_printing_the_signed_url(self):
        signed_url = "https://cdn.kinescope.io/master.m3u8?token=private"

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
                    status = extract_manifest.main(["https://kinescope.io/video", str(output)])
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
                return b'"https://cdn.kinescope.io/master.m3u8"'

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "manifest.txt"
            output.write_text("keep this file\n", encoding="utf-8")
            with patch.object(extract_manifest, "build_opener", return_value=Opener()) as build_opener:
                with patch("sys.stderr", new_callable=io.StringIO):
                    status = extract_manifest.main(["https://kinescope.io/video", str(output)])
            self.assertEqual(status, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep this file\n")
            build_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
