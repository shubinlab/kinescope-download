#!/usr/bin/env python3
"""Safely extract one public Kinescope master manifest from a share page."""

from __future__ import annotations

import html
import os
import re
import sys
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_BYTES = 8 * 1024 * 1024
TRUSTED_DOMAINS = ("kinescope.io", "kinescopecdn.net")


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
            and any(host == domain or host.endswith("." + domain) for domain in TRUSTED_DOMAINS)
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
        sensitive = ("token", "secret", "password", "passwd", "auth", "signature", "credential", "cookie", "jwt", "session")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(marker in normalized for marker in sensitive):
                raise ValueError
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    except ValueError as error:
        raise ValueError("Referrer must be HTTP(S) without credentials or sensitive query parameters") from error


class TrustedRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not trusted_https_url(new_url):
            raise ValueError("Redirect destination is not an HTTPS Kinescope URL")
        return super().redirect_request(request, response, code, message, headers, new_url)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("Usage: extract_manifest.py KINESCOPE_URL OUTPUT_FILE [REFERRER_URL]", file=sys.stderr)
        return 2

    page_url, output_path, *referrer = argv
    referrer_url = referrer[0] if referrer else ""
    if not trusted_https_url(page_url):
        print("Page URL must use HTTPS on a recognized Kinescope host.", file=sys.stderr)
        return 1
    try:
        referrer_url = prepare_referrer(referrer_url)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if os.path.lexists(output_path):
        print("Output file already exists; refusing to overwrite it.", file=sys.stderr)
        return 1

    headers = {"User-Agent": "Mozilla/5.0"}
    if referrer_url:
        headers["Referer"] = referrer_url
    request = Request(page_url, headers=headers)
    opener = build_opener(TrustedRedirects())
    try:
        with opener.open(request, timeout=30) as response:
            final_url = response.geturl()
            if not trusted_https_url(final_url):
                raise ValueError("Final response URL is not an HTTPS Kinescope URL")
            page = response.read(MAX_PAGE_BYTES + 1)
    except (OSError, ValueError) as error:
        print(f"Could not fetch the Kinescope page safely: {error}", file=sys.stderr)
        return 1

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

    try:
        descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(manifests[0] + "\n")
    except FileExistsError:
        print("Output file already exists; refusing to overwrite it.", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Could not create the private output file: {error}", file=sys.stderr)
        return 1
    print("One Kinescope master manifest saved to the private temporary file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
