#!/usr/bin/env python3
"""Read browser cookies with yt-dlp and keep only cookies for one allowed host."""

from __future__ import annotations

import re
import tempfile
import json
import os
import shlex
import shutil
import subprocess
import sys
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request


BROWSER_SPEC = re.compile(
    r"(?P<browser>brave|chrome|chromium|edge|firefox|opera|safari|vivaldi|whale)"
    r"(?:\+(?P<keyring>basictext|gnomekeyring|kwallet|kwallet5|kwallet6))?\Z"
)
TRUSTED_DOMAIN_ROOTS = ("ontico.ru", "kinescope.io", "kinescopecdn.net")
KNOWN_REQUEST_HOSTS = (
    "conf.ontico.ru", "kinescope.io", "player.kinescope.io",
    "kinescopecdn.net", "cdn.kinescopecdn.net", "edge-msk-10.kinescopecdn.net",
)
MAX_COOKIE_COUNT = 2048
MAX_COOKIE_JSON_BYTES = 2 * 1024 * 1024
BROWSER_ENV_KEYS = (
    "HOME", "PATH", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID",
    "DISPLAY", "WAYLAND_DISPLAY", "LANG", "LC_ALL",
)


class CookieError(ValueError):
    """A safe, user-facing browser-cookie failure."""


def is_known_request_host(host: str) -> bool:
    return host.lower().rstrip(".") in KNOWN_REQUEST_HOSTS


class _QuietLogger:
    def debug(self, *_args, **_kwargs):
        pass

    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _cookie_record(cookie) -> dict:
    fields = (
        "version", "name", "value", "port", "port_specified", "domain", "domain_specified",
        "domain_initial_dot", "path", "path_specified", "secure", "expires", "discard",
        "comment", "comment_url", "rfc2109",
    )
    return {**{field: getattr(cookie, field) for field in fields}, "rest": cookie._rest}


def _write_cookie_json(cookies: CookieJar) -> bool:
    written = 2
    count = 0
    sys.stdout.write("[")
    for cookie in cookies:
        if count >= MAX_COOKIE_COUNT:
            return False
        encoded = json.dumps(_cookie_record(cookie), separators=(",", ":"))
        size = len(encoded.encode("utf-8")) + (1 if count else 0)
        if written + size > MAX_COOKIE_JSON_BYTES:
            return False
        if count:
            sys.stdout.write(",")
        sys.stdout.write(encoded)
        written += size
        count += 1
    sys.stdout.write("]")
    return True


def _child_export() -> int:
    try:
        payload = sys.stdin.read(8193)
        if len(payload) > 8192:
            raise CookieError("Cookie request metadata is too large.")
        data = json.loads(payload)
        browser = BROWSER_SPEC.fullmatch(data.get("browser_spec", ""))
        domains = tuple(data.get("allowed_domains", ()))
        parsed = validate_request_url(data.get("request_url", ""), domains)
        if not browser:
            raise CookieError("Browser spec is invalid.")
        from yt_dlp.cookies import extract_cookies_from_browser

        source = extract_cookies_from_browser(
            browser.group("browser"),
            logger=_QuietLogger(),
            keyring=browser.group("keyring").upper() if browser.group("keyring") else None,
        )
        scoped = _filter_cookie_jar(source, parsed.hostname.lower().rstrip("."), domains)
        try:
            if not _write_cookie_json(scoped):
                print("Cookie response exceeded the safety limit.", file=sys.stderr)
                return 1
        finally:
            scoped.clear()
        return 0
    except Exception:
        print("Could not export filtered browser cookies safely.", file=sys.stderr)
        return 1


def validate_request_url(value: str, allowed_domains: tuple[str, ...]):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as error:
        raise CookieError("Request URL is malformed.") from error
    domains = tuple(domain.lower().rstrip(".") for domain in allowed_domains)
    allowed = len(domains) == 1 and domains[0] == host and is_known_request_host(host)
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not allowed
        or host not in domains
    ):
        raise CookieError("Request URL is outside the allowed HTTPS domains.")
    return parsed


def _yt_dlp_interpreter() -> str:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise CookieError("yt-dlp is unavailable to read browser cookies.")
    try:
        path = Path(executable).resolve()
        with path.open("rb") as file:
            first_line = file.readline(512).decode("utf-8", errors="replace").strip()
    except OSError as error:
        raise CookieError("Could not locate yt-dlp's Python interpreter.") from error
    if not first_line.startswith("#!"):
        raise CookieError("Could not locate yt-dlp's Python interpreter.")
    try:
        shebang = shlex.split(first_line[2:])
    except ValueError as error:
        raise CookieError("Could not locate yt-dlp's Python interpreter.") from error
    if not shebang:
        raise CookieError("Could not locate yt-dlp's Python interpreter.")
    if os.path.basename(shebang[0]) == "env":
        if len(shebang) != 2 or not re.fullmatch(r"python3(?:\.\d+)?", os.path.basename(shebang[1])):
            raise CookieError("yt-dlp uses an unsupported Python launcher.")
        interpreter = shutil.which(shebang[1])
    else:
        if len(shebang) != 1 or not re.fullmatch(r"python3(?:\.\d+)?", os.path.basename(shebang[0])):
            raise CookieError("yt-dlp uses an unsupported Python launcher.")
        interpreter = shebang[0]
    if not interpreter or not os.path.isabs(interpreter) or not os.path.isfile(interpreter) or not os.access(interpreter, os.X_OK):
        raise CookieError("Could not locate yt-dlp's Python interpreter.")
    return interpreter


def _browser_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in BROWSER_ENV_KEYS if key in os.environ}


def _export_with_yt_dlp_interpreter(browser_name, keyring, host, domains) -> CookieJar:
    payload = json.dumps({
        "browser_spec": browser_name + ("+" + keyring.lower() if keyring else ""),
        "keyring": keyring,
        "request_url": f"https://{host}/",
        "allowed_domains": domains,
    })
    try:
        with tempfile.TemporaryDirectory(prefix="yt-dlp-cookie-api-") as work_dir:
            os.chmod(work_dir, 0o700)
            probe = subprocess.run(
                [
                    _yt_dlp_interpreter(), "-I", "-S", "-c",
                    "import sys; print('python3' if sys.version_info.major == 3 else 'invalid')",
                ],
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                cwd=work_dir,
                env=_browser_environment(),
                check=False,
            )
            if probe.returncode != 0 or probe.stdout.strip() != "python3":
                raise CookieError("yt-dlp's executable does not point to a Python 3 interpreter.")
            result = subprocess.run(
                [_yt_dlp_interpreter(), str(Path(__file__).resolve()), "--child-export"],
                input=payload,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=60,
                cwd=work_dir,
                env=_browser_environment(),
                check=False,
            )
        if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAX_COOKIE_JSON_BYTES:
            raise CookieError("Could not read cookies with yt-dlp's Python interpreter.")
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) > MAX_COOKIE_COUNT:
            raise CookieError("yt-dlp returned an invalid cookie response.")
        jar = CookieJar()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("rest"), dict):
                raise ValueError
            jar.set_cookie(Cookie(
                version=row["version"], name=row["name"], value=row["value"],
                port=row["port"], port_specified=row["port_specified"],
                domain=row["domain"], domain_specified=row["domain_specified"],
                domain_initial_dot=row["domain_initial_dot"], path=row["path"],
                path_specified=row["path_specified"], secure=row["secure"],
                expires=row["expires"], discard=row["discard"], comment=row["comment"],
                comment_url=row["comment_url"], rest=row["rest"], rfc2109=row["rfc2109"],
            ))
        return jar
    except CookieError:
        raise
    except Exception as error:
        raise CookieError("Could not read filtered cookies with yt-dlp's Python interpreter.") from error


def _filter_cookie_jar(source: CookieJar, host: str, allowed_domains: tuple[str, ...]) -> CookieJar:
    scoped = CookieJar()
    try:
        for cookie in source:
            cookie_domain = cookie.domain.lower().lstrip(".").rstrip(".")
            domain_allowed = any(
                cookie_domain == domain or cookie_domain.endswith("." + domain)
                for domain in TRUSTED_DOMAIN_ROOTS
            )
            if domain_allowed and (host == cookie_domain or host.endswith("." + cookie_domain)):
                scoped.set_cookie(cookie)
                if len(scoped) > MAX_COOKIE_COUNT:
                    raise CookieError("Too many matching browser cookies.")
    except Exception as error:
        scoped.clear()
        try:
            source.clear()
        except Exception:
            pass
        if isinstance(error, CookieError):
            raise
        raise CookieError("Could not filter browser cookies safely.") from error
    try:
        source.clear()
    except Exception as error:
        scoped.clear()
        raise CookieError("Could not clear the unfiltered browser cookies.") from error
    return scoped


def write_host_cookie_file(cookies: CookieJar, request_url: str, path: str | os.PathLike) -> None:
    try:
        parsed = urlsplit(request_url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as error:
        cookies.clear()
        raise CookieError("Cookie output request URL is malformed.") from error
    validate_request_url(request_url, (host,))
    path = os.fspath(path)
    if os.path.lexists(path):
        cookies.clear()
        raise CookieError("Cookie output file already exists; refusing to overwrite it.")

    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        created = True
        host_only = MozillaCookieJar(path)
        for cookie in cookies:
            domain = cookie.domain.lower().lstrip(".").rstrip(".")
            if not (host == domain or host.endswith("." + domain)):
                continue
            candidate = Cookie(
                version=cookie.version, name=cookie.name, value=cookie.value,
                port=cookie.port, port_specified=cookie.port_specified,
                domain=host, domain_specified=False, domain_initial_dot=False,
                path=cookie.path, path_specified=cookie.path_specified,
                secure=cookie.secure, expires=cookie.expires, discard=cookie.discard,
                comment=cookie.comment, comment_url=cookie.comment_url,
                rest=cookie._rest, rfc2109=cookie.rfc2109,
            )
            probe = CookieJar()
            probe.set_cookie(candidate)
            request = Request(request_url)
            probe.add_cookie_header(request)
            if request.get_header("Cookie"):
                host_only.set_cookie(candidate)
        cookies.clear()
        host_only.save(ignore_discard=True, ignore_expires=True)
        os.chmod(path, 0o600)
    except Exception as error:
        try:
            cookies.clear()
        except Exception:
            pass
        if created:
            try:
                os.unlink(path)
            except OSError:
                pass
        if isinstance(error, CookieError):
            raise
        raise CookieError("Could not create the private host-only cookie file.") from error


def export_browser_cookies(
    request_url: str,
    browser_spec: str,
    allowed_domains: tuple[str, ...],
    *,
    require_matching_cookie: bool = False,
) -> CookieJar:
    parsed = validate_request_url(request_url, allowed_domains)
    browser = BROWSER_SPEC.fullmatch(browser_spec)
    if not browser:
        raise CookieError("Choose a supported browser and keyring spec.")
    browser_name = browser.group("browser")
    keyring = browser.group("keyring").upper() if browser.group("keyring") else None
    host = parsed.hostname.lower().rstrip(".")
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        source = _export_with_yt_dlp_interpreter(browser_name, keyring, host, allowed_domains)
    else:
        try:
            source = extract_cookies_from_browser(browser_name, logger=_QuietLogger(), keyring=keyring)
        except Exception as error:
            raise CookieError("Could not read cookies from the selected browser.") from error
    scoped = _filter_cookie_jar(source, host, allowed_domains)
    if require_matching_cookie and not list(scoped):
        raise CookieError("No browser cookies match the authorized request host.")
    return scoped


if __name__ == "__main__":
    if sys.argv[1:] == ["--child-export"]:
        raise SystemExit(_child_export())
    print("This helper is loaded by the video download skill.", file=sys.stderr)
    raise SystemExit(2)
