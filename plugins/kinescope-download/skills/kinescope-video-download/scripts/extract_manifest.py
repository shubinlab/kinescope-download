#!/usr/bin/env python3
"""Safely extract one public Kinescope master manifest from a share page."""

from __future__ import annotations

import html
import os
import re
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

from browser_cookies import CookieError, export_browser_cookies, write_host_cookie_file


MAX_PAGE_BYTES = 8 * 1024 * 1024
KINESCOPE_PAGE_HOSTS = ("kinescope.io", "player.kinescope.io")
KINESCOPE_MANIFEST_HOSTS = (
    "kinescope.io", "player.kinescope.io", "kinescopecdn.net",
    "cdn.kinescopecdn.net", "edge-msk-10.kinescopecdn.net",
)


def trusted_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme.lower() == "https"
            and bool(host)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and host in KINESCOPE_MANIFEST_HOSTS
        )
    except ValueError:
        return False


def trusted_page_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() == "https"
            and parsed.hostname in KINESCOPE_PAGE_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def prepare_referrer(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError
        parsed.port  # force validation of a malformed port before using the URL
        # Do not send query credentials with a caller-supplied page URL. Keep the
        # origin/path needed by embed referrer checks, but remove query/fragment.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError as error:
        raise ValueError("Referrer must be HTTP(S) without credentials or sensitive query parameters") from error


class TrustedRedirects(HTTPRedirectHandler):
    def __init__(self, request_host: str):
        super().__init__()
        self.request_host = request_host

    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            host = urlsplit(new_url).hostname
        except ValueError:
            host = None
        if not trusted_page_url(new_url) or host != self.request_host:
            raise ValueError("Redirect destination changed the Kinescope page host")
        return super().redirect_request(request, response, code, message, headers, new_url)


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "--url-stdin" or len(argv) not in (3, 4):
        print("Usage: extract_manifest.py --url-stdin BROWSER_SPEC MANIFEST_FILE [REFERRER_OUTPUT_FILE]", file=sys.stderr)
        return 2
    _, browser_spec, output_path, *referrer_output = argv
    referrer_output_path = referrer_output[0] if referrer_output else None
    cookie_path = output_path + ".cookies"
    page_url = sys.stdin.readline(16_385).rstrip("\r\n")
    if len(page_url) > 16_384 or "\n" in page_url or "\r" in page_url:
        print("Page URL input is too long or malformed.", file=sys.stderr)
        return 1
    raw_referrer = sys.stdin.readline(16_385).rstrip("\r\n")
    if len(raw_referrer) > 16_384 or "\n" in raw_referrer or "\r" in raw_referrer:
        print("Referrer input is too long or malformed.", file=sys.stderr)
        return 1

    referrer_url = raw_referrer
    if not trusted_page_url(page_url):
        print("Page URL must use HTTPS on a recognized Kinescope player host.", file=sys.stderr)
        return 1
    try:
        referrer_url = prepare_referrer(referrer_url)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    output_paths = [output_path, cookie_path] + ([referrer_output_path] if referrer_output_path else [])
    canonical_paths = [os.path.realpath(path) for path in output_paths]
    if len(set(canonical_paths)) != len(canonical_paths):
        print("Manifest, cookie, and Referer output files must be different.", file=sys.stderr)
        return 1
    if any(os.path.lexists(path) for path in output_paths):
        print("Output file already exists; refusing to overwrite it.", file=sys.stderr)
        return 1

    try:
        page_host = urlsplit(page_url).hostname
        cookies = export_browser_cookies(page_url, browser_spec, (page_host,))
    except CookieError:
        print("Could not load browser cookies for the Kinescope page safely.", file=sys.stderr)
        return 1

    headers = {"User-Agent": "Mozilla/5.0"}
    if referrer_url:
        headers["Referer"] = referrer_url
    request = Request(page_url, headers=headers)
    opener = build_opener(HTTPCookieProcessor(cookies), TrustedRedirects(page_host))
    try:
        with opener.open(request, timeout=30) as response:
            final_url = response.geturl()
            if not trusted_page_url(final_url) or urlsplit(final_url).hostname != page_host:
                raise ValueError("Final response URL changed the Kinescope page host")
            page = response.read(MAX_PAGE_BYTES + 1)
    except (OSError, ValueError):
        # Network exception strings can contain the request URL, including its
        # query credentials. Keep diagnostics useful without echoing that URL.
        print("Could not fetch the Kinescope page safely; check access and try again.", file=sys.stderr)
        return 1
    finally:
        cookies.clear()

    if len(page) > MAX_PAGE_BYTES:
        print(f"Page exceeds the {MAX_PAGE_BYTES // (1024 * 1024)} MiB safety limit.", file=sys.stderr)
        return 1

    text = page.decode("utf-8", errors="replace")
    text = html.unescape(text).replace(r"\u0026", "&").replace(r"\/", "/")
    matches = re.findall(r"[^\s\"'<>]*master\.m3u8(?:\?[^\s\"'<>]*)?", text)
    manifests = list(dict.fromkeys(urljoin(page_url, item.rstrip(",;)]}")) for item in matches))
    manifests = [item for item in manifests if trusted_https_url(item)]
    if len(manifests) != 1:
        print(f"Expected one recognized Kinescope master manifest; found {len(manifests)}", file=sys.stderr)
        return 1

    manifest_url = manifests[0]
    manifest_host = urlsplit(manifest_url).hostname
    try:
        manifest_cookies = export_browser_cookies(manifest_url, browser_spec, (manifest_host,))
    except CookieError:
        print("Could not load browser cookies for the Kinescope manifest host safely.", file=sys.stderr)
        return 1

    created_paths = []
    try:
        write_host_cookie_file(manifest_cookies, manifest_url, cookie_path)
        created_paths.append(cookie_path)
        descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created_paths.append(output_path)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(manifest_url + "\n")
        if referrer_output_path:
            descriptor = os.open(referrer_output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_paths.append(referrer_output_path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                if referrer_url:
                    output.write(referrer_url + "\n")
    except (CookieError, FileExistsError):
        print("Output file already exists; refusing to overwrite it.", file=sys.stderr)
        for path in created_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        return 1
    except OSError:
        print("Could not create the private output files.", file=sys.stderr)
        for path in created_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        return 1
    print("Kinescope manifest and host-only cookie file saved privately")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
