#!/usr/bin/env python3
"""Resolve an authorized Ontico DevNavigator card to its Kinescope player."""

from __future__ import annotations

import json
import os
import re
import sys
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener
import browser_cookies


ONTICO_HOST = "conf.ontico.ru"
MAX_URL_CHARS = 16_384
MAX_API_BYTES = 1024 * 1024
REQUEST_TIMEOUT = 20
KINE_SCOPE_ID = re.compile(r"[A-Za-z0-9_-]{6,64}\Z")


class AdapterError(ValueError):
    """A safe, user-facing Ontico resolution failure."""


def parse_card_url(value: str) -> tuple[int, int, str]:
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise AdapterError("Use an HTTPS Ontico DevNavigator card URL.")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as error:
        raise AdapterError("Use an HTTPS Ontico DevNavigator card URL.") from error
    match = re.fullmatch(r"/devnavigator/roadmaps/([1-9][0-9]{0,9})/card/([1-9][0-9]{0,9})", parsed.path)
    if (
        parsed.scheme.lower() != "https"
        or host != ONTICO_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not match
    ):
        raise AdapterError("Use an HTTPS Ontico DevNavigator card URL without query or fragment data.")
    roadmap_id, card_id = map(int, match.groups())
    safe_url = f"https://{ONTICO_HOST}/devnavigator/roadmaps/{roadmap_id}/card/{card_id}"
    return roadmap_id, card_id, safe_url


def is_ontico_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower().rstrip(".") == ONTICO_HOST
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def validate_output_paths(player_path: str | os.PathLike, referrer_path: str | os.PathLike) -> tuple[str, str]:
    player_path = os.fspath(player_path)
    referrer_path = os.fspath(referrer_path)
    if os.path.realpath(player_path) == os.path.realpath(referrer_path):
        raise AdapterError("Player URL and Referer output files must be different.")
    if os.path.lexists(player_path) or os.path.lexists(referrer_path):
        raise AdapterError("Output file already exists; refusing to overwrite it.")
    return player_path, referrer_path


def export_browser_cookies(page_url: str, browser_spec: str) -> CookieJar:
    try:
        return browser_cookies.export_browser_cookies(
            page_url,
            browser_spec,
            (ONTICO_HOST,),
            require_matching_cookie=True,
        )
    except browser_cookies.CookieError as error:
        raise AdapterError("Could not read Ontico browser cookies.") from error


class OnticoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        current = urlsplit(request.full_url)
        target = urlsplit(new_url)
        if (
            not is_ontico_origin(new_url)
            or target.path != current.path
            or target.query
            or target.fragment
        ):
            raise ValueError("Ontico redirect left the authorized host.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_card_player_id(page_url: str, card_id: int, cookies: CookieJar) -> str:
    request = Request(
        f"https://{ONTICO_HOST}/api/v1/dev-navigator/cards/{card_id}",
        headers={"Accept": "application/json", "Referer": page_url, "User-Agent": "Mozilla/5.0"},
    )
    opener = build_opener(HTTPCookieProcessor(cookies), OnticoRedirects())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            final = urlsplit(response.geturl())
            expected_path = f"/api/v1/dev-navigator/cards/{card_id}"
            if not is_ontico_origin(response.geturl()) or final.path != expected_path or final.query or final.fragment:
                raise AdapterError("Ontico card API redirected outside its host.")
            body = response.read(MAX_API_BYTES + 1)
    except AdapterError:
        raise
    except (OSError, ValueError) as error:
        raise AdapterError("Could not read the Ontico card. Check the browser session and access.") from error
    if len(body) > MAX_API_BYTES:
        raise AdapterError("Ontico card response exceeded the safety limit.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError("Ontico returned an invalid card response.") from error
    result = payload.get("result") if isinstance(payload, dict) else None
    status = payload.get("status") if isinstance(payload, dict) else None
    if (
        not isinstance(status, dict)
        or status.get("name") != "success"
        or not isinstance(result, dict)
        or type(result.get("id")) is not int
        or result["id"] != card_id
    ):
        raise AdapterError("Ontico did not return the requested card.")
    player_id = result.get("kinescope_id")
    if not isinstance(player_id, str) or not KINE_SCOPE_ID.fullmatch(player_id):
        raise AdapterError("The Ontico card has no supported Kinescope player ID.")
    return player_id


def write_outputs(player_url: str, referrer: str, player_path: str | os.PathLike, referrer_path: str | os.PathLike) -> None:
    player_path, referrer_path = validate_output_paths(player_path, referrer_path)

    created = []
    try:
        for path, value in ((player_path, player_url), (referrer_path, referrer)):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(value + "\n")
    except OSError as error:
        for path in created:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise AdapterError("Could not create private Ontico resolution files.") from error


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4 or argv[0] != "--url-stdin":
        print("Usage: extract_ontico_card.py --url-stdin BROWSER PLAYER_URL_FILE REFERER_FILE", file=sys.stderr)
        return 2
    _, browser_spec, player_path, referrer_path = argv
    page_url = sys.stdin.readline(MAX_URL_CHARS + 2).rstrip("\r\n")
    try:
        if len(page_url) > MAX_URL_CHARS:
            raise AdapterError("Ontico card URL exceeds the input limit.")
        _, card_id, safe_url = parse_card_url(page_url)
        validate_output_paths(player_path, referrer_path)
        cookies = export_browser_cookies(safe_url, browser_spec)
        try:
            player_id = fetch_card_player_id(safe_url, card_id, cookies)
        finally:
            cookies.clear()
        write_outputs(f"https://kinescope.io/{player_id}", safe_url, player_path, referrer_path)
    except (AdapterError, OSError, ValueError):
        print("Could not resolve the authorized Ontico video; check the link, browser session, and access.", file=sys.stderr)
        return 1
    print("Ontico card resolved; private player and Referer files were saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
