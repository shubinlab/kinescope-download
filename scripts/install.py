#!/usr/bin/env python3
"""Install the Kinescope Download plugin in Codex and/or Claude Code."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path
from urllib.parse import urlparse


PLUGIN = "kinescope-download"
MARKETPLACE = "kinescope-download"
MARKETPLACE_SOURCE = "shubinlab/kinescope-download"
CLIENT_INSTALLERS = {
    "codex": ("https://chatgpt.com/codex/install.sh", "/bin/sh"),
    "claude": ("https://claude.ai/install.sh", "/bin/bash"),
}
CLIENT_INSTALLER_HOSTS = {
    "codex": {"chatgpt.com", "releases.openai.com"},
    "claude": {"claude.ai", "downloads.claude.ai"},
}
MEDIA_TOOLS = ("yt-dlp", "ffmpeg", "ffprobe")
VERSION_COMMANDS = {
    "git": ("--version",),
    "codex": ("--version",),
    "claude": ("--version",),
    "yt-dlp": ("--version",),
    "ffmpeg": ("-version",),
    "ffprobe": ("-version",),
}
LINUX_PACKAGE_MANAGERS = {
    "debian": "apt-get",
    "ubuntu": "apt-get",
    "linuxmint": "apt-get",
    "pop": "apt-get",
    "elementary": "apt-get",
    "fedora": "dnf",
    "rhel": "dnf",
    "centos": "dnf",
    "rocky": "dnf",
    "almalinux": "dnf",
    "ol": "dnf",
    "arch": "pacman",
    "manjaro": "pacman",
    "endeavouros": "pacman",
    "garuda": "pacman",
    "artix": "pacman",
    "opensuse": "zypper",
    "opensuse-leap": "zypper",
    "opensuse-tumbleweed": "zypper",
    "suse": "zypper",
    "sles": "zypper",
    "sled": "zypper",
}
IMMUTABLE_LINUX_IDS = {
    "opensuse-microos", "opensuse-aeon", "opensuse-kalpa",
    "nixos", "steamos", "bazzite", "aurora", "bluefin", "ublue", "ublue-os", "ucore",
    "fedora-silverblue", "fedora-kinoite", "fedora-sericea", "fedora-onyx",
    "fedora-coreos", "fedora-iot",
}
IMMUTABLE_LINUX_VARIANTS = {
    "silverblue", "kinoite", "sericea", "onyx", "coreos", "iot",
    "microos", "microos-desktop", "aeon", "kalpa",
}


class InstallError(RuntimeError):
    pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the Kinescope Download plugin in Codex and/or Claude Code."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--codex", action="store_true", help="Install in Codex only; add its CLI if missing.")
    selection.add_argument("--claude", action="store_true", help="Install in Claude Code only; add its CLI if missing.")
    selection.add_argument("--all", action="store_true", help="Install in both clients; add either CLI if missing.")
    parser.add_argument(
        "--with-deps",
        action="store_true",
        help="Install missing yt-dlp and FFmpeg using the platform package manager.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without installing.")
    return parser.parse_args(argv)


def run(
    command: list[str], *, dry_run: bool = False, read_only: bool = False
) -> subprocess.CompletedProcess[str] | None:
    if dry_run and not read_only:
        print("Would run: " + " ".join(command))
        return None
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise InstallError(
            f"Command failed ({result.returncode}): {' '.join(command)}. "
            "Child output was suppressed to avoid logging credentials or signed URLs."
        )
    return result


def json_result(result: subprocess.CompletedProcess[str] | None, label: str):
    if result is None:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InstallError(f"{label} did not return valid JSON; refusing to guess its state.") from error


def validate_list_result(data, label: str, expected_kind: str):
    """Accept the list-shaped JSON emitted by current client CLIs, fail closed otherwise."""
    identity_keys = ("name",) if expected_kind == "marketplaces" else ("name", "id", "pluginId")

    def records_are_identifiable(records):
        return all(
            isinstance(item, dict)
            and any(isinstance(item.get(key), str) and item[key] for key in identity_keys)
            for item in records
        )

    if isinstance(data, list):
        if not records_are_identifiable(data):
            raise InstallError(f"{label} returned an unexpected JSON shape; refusing to guess its state.")
        return data
    if isinstance(data, dict):
        allowed_keys = (
            ("marketplaces",)
            if expected_kind == "marketplaces"
            else ("plugins", "installed", "available")
        )
        records_found = False
        records = []
        for key in allowed_keys:
            if key in data:
                records_found = True
                group = data[key]
                if not isinstance(group, list) or not records_are_identifiable(group):
                    raise InstallError(f"{label} returned an unexpected JSON shape; refusing to guess its state.")
                records.extend(group)
        if records_found:
            return records
        # Some CLI versions emit a single record for a single-result query.
        if any(isinstance(data.get(key), str) and data[key] for key in identity_keys):
            return [data]
    raise InstallError(f"{label} returned an unexpected JSON shape; refusing to guess its state.")


def walk_records(data):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from walk_records(value)
    elif isinstance(data, list):
        for value in data:
            yield from walk_records(value)


def has_marketplace(data) -> bool:
    return any(record.get("name") == MARKETPLACE for record in walk_records(data))


def plugin_state(data) -> tuple[bool, str | None]:
    plugin_id = f"{PLUGIN}@{MARKETPLACE}"
    same_marketplace = False
    for record in walk_records(data):
        name = record.get("name")
        identifier = record.get("id") or record.get("pluginId")
        source = record.get("marketplace") or record.get("marketplaceName")
        if isinstance(source, dict):
            source = source.get("name")
        if identifier == plugin_id:
            same_marketplace = True
        elif name == PLUGIN and source == MARKETPLACE:
            same_marketplace = True
        elif name == PLUGIN or (isinstance(identifier, str) and identifier.startswith(f"{PLUGIN}@")):
            return False, f"{PLUGIN} is already installed from a different or unknown marketplace ({source or identifier or 'source unavailable'})."
        if name == plugin_id:
            same_marketplace = True
    return same_marketplace, None


def client_command(client: str, *args: str) -> list[str]:
    return [client, *args]


def package_manager_command(system: str | None = None) -> str | None:
    system = system or platform.system()
    if system == "Darwin":
        return "brew" if shutil.which("brew") else None
    if system != "Linux":
        return None
    release = linux_release_info()
    distro_id = release.get("ID", "").lower()
    if distro_id == "omarchy":
        return "omarchy" if shutil.which("omarchy") else None
    variant = release.get("VARIANT_ID", "").lower()
    if distro_id in IMMUTABLE_LINUX_IDS or variant in IMMUTABLE_LINUX_VARIANTS:
        return None

    manager = LINUX_PACKAGE_MANAGERS.get(distro_id)
    if not manager:
        for family in release.get("ID_LIKE", "").lower().split():
            manager = LINUX_PACKAGE_MANAGERS.get(family)
            if manager:
                break
    if manager:
        return manager if shutil.which(manager) else None

    available = [name for name in ("apt-get", "dnf", "pacman", "zypper") if shutil.which(name)]
    return available[0] if len(available) == 1 else None


def linux_release_info() -> dict[str, str]:
    reader = getattr(platform, "freedesktop_os_release", None)
    if reader:
        try:
            return reader()
        except (OSError, ValueError):
            pass
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key:
            continue
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if parsed else ""
    return values


def platform_summary() -> str:
    system = platform.system()
    machine = platform.machine() or "unknown architecture"
    if system == "Darwin":
        version = platform.mac_ver()[0]
        name = f"macOS {version}" if version else "macOS (version unavailable)"
    elif system == "Linux":
        release = linux_release_info()
        name = release.get("PRETTY_NAME") or release.get("NAME") or "Linux"
        version = release.get("VERSION_ID", "")
        if version and version not in name:
            name = f"{name} {version}"
    else:
        name = system or "Unknown OS"
    name = " ".join("".join(char if char.isprintable() else " " for char in name).split())[:100]
    manager = package_manager_command(system)
    manager_label = {
        "omarchy": "Omarchy pkg",
        "brew": "Homebrew",
    }.get(manager, manager or "not detected")
    return f"{name} ({machine}); package manager: {manager_label}"


def command_version(name: str) -> str:
    if name == "python3":
        return platform.python_version()
    executable = shutil.which(name)
    if not executable:
        return "missing"
    try:
        result = subprocess.run(
            [executable, *VERSION_COMMANDS.get(name, ("--version",))],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "present (version unavailable)"
    output = result.stdout or result.stderr
    if result.returncode or not output.strip():
        return "present (version unavailable)"
    version = re.search(r"(?<![A-Za-z0-9])n?\d+(?:\.\d+){1,3}", output.splitlines()[0])
    return version.group(0) if version else "present (version unavailable)"


def print_environment_summary() -> None:
    print(f"Platform: {platform_summary()}")
    print("Detected versions:")
    for name in ("git", "python3", "codex", "claude", *MEDIA_TOOLS):
        print(f"  {name}: {command_version(name)}")


def plugin_install_command(client: str) -> list[str]:
    if client == "codex":
        return client_command(client, "plugin", "add", f"{PLUGIN}@{MARKETPLACE}")
    return client_command(client, "plugin", "install", f"{PLUGIN}@{MARKETPLACE}", "--scope", "user")


def list_marketplaces(client: str, dry_run: bool):
    if client == "codex":
        command = client_command(client, "plugin", "marketplace", "list", "--json")
    else:
        command = client_command(client, "plugin", "marketplace", "list", "--json")
    label = f"{client} marketplace list"
    return validate_list_result(
        json_result(run(command, dry_run=dry_run, read_only=True), label), label, "marketplaces"
    )


def list_installed_plugins(client: str, dry_run: bool):
    command = client_command(client, "plugin", "list", "--json")
    label = f"{client} plugin list"
    return validate_list_result(
        json_result(run(command, dry_run=dry_run, read_only=True), label), label, "plugins"
    )


def inspect_client(client: str) -> dict[str, bool]:
    marketplaces = list_marketplaces(client, False)
    marketplace_ready = has_marketplace(marketplaces)
    installed_plugins = list_installed_plugins(client, False)
    plugin_installed, conflict = plugin_state(installed_plugins)
    if conflict:
        raise InstallError(f"{client}: {conflict}")
    return {"marketplace": marketplace_ready, "plugin": plugin_installed}


def install_client(client: str, dry_run: bool, state: dict[str, bool]) -> None:
    marketplace_ready = state["marketplace"]
    if marketplace_ready:
        print(f"{client}: marketplace already registered")
    else:
        print(f"{client}: registering marketplace {MARKETPLACE_SOURCE}")
        if client == "codex":
            add_marketplace = client_command(client, "plugin", "marketplace", "add", MARKETPLACE_SOURCE)
        else:
            add_marketplace = client_command(
                client, "plugin", "marketplace", "add", MARKETPLACE_SOURCE, "--scope", "user"
            )
        run(add_marketplace, dry_run=dry_run)

    if state["plugin"]:
        print(f"{client}: {PLUGIN} already installed")
        return

    print(f"{client}: installing {PLUGIN}@{MARKETPLACE} for this user")
    install_command = plugin_install_command(client)
    run(install_command, dry_run=dry_run)

    if not dry_run:
        installed_after = list_installed_plugins(client, False)
        plugin_installed, conflict = plugin_state(installed_after)
        if conflict:
            raise InstallError(f"{client}: {conflict}")
        if not plugin_installed:
            raise InstallError(f"{client} did not report {PLUGIN}@{MARKETPLACE} after installation.")
        print(f"{client}: {PLUGIN} is installed")


def media_plan(packages: list[str]) -> list[list[str]]:
    system = platform.system()
    if system == "Darwin":
        manager = package_manager_command(system)
        if not manager:
            raise InstallError("Homebrew is required to install missing macOS media tools. Install Homebrew, then retry.")
        return [[manager, "install", *packages]]
    if system != "Linux":
        raise InstallError(f"Automatic media dependency installation is unsupported on {system or 'this OS'}.")

    manager = package_manager_command(system)
    if manager == "omarchy":
        return [["omarchy", "pkg", "add", *packages]]

    if not manager:
        raise InstallError("No unambiguous supported package manager found for this Linux distribution. Install yt-dlp and FFmpeg manually.")

    package_names = list(packages)
    if manager == "apt-get":
        commands = [[manager, "update"], [manager, "install", "-y", *package_names]]
    elif manager == "dnf":
        commands = [[manager, "install", "-y", *package_names]]
    elif manager == "pacman":
        commands = [[manager, "-S", "--needed", "--noconfirm", *package_names]]
    else:
        commands = [[manager, "--non-interactive", "install", *package_names]]
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            raise InstallError(f"{manager} needs elevated permissions; install these tools manually or make sudo available.")
        commands = [["sudo", *command] for command in commands]
    return commands


def media_packages_for_missing(missing_tools: list[str]) -> list[str]:
    packages = []
    if "yt-dlp" in missing_tools:
        packages.append("yt-dlp")
    if "ffmpeg" in missing_tools or "ffprobe" in missing_tools:
        packages.append("ffmpeg")
    return packages


def install_media_tools(with_deps: bool, dry_run: bool) -> None:
    missing_tools = [tool for tool in MEDIA_TOOLS if not shutil.which(tool)]
    if not missing_tools:
        print("Media tools present: yt-dlp, ffmpeg, ffprobe")
        return
    if not with_deps:
        print("Missing media tools: " + ", ".join(missing_tools))
        print("Install them separately or rerun with --with-deps to use the platform package manager.")
        return

    packages = media_packages_for_missing(missing_tools)
    commands = media_plan(packages)
    for command in commands:
        run(command, dry_run=dry_run)
    if dry_run:
        return
    still_missing = [tool for tool in MEDIA_TOOLS if not shutil.which(tool)]
    if still_missing:
        raise InstallError("Media tools still missing after package install: " + ", ".join(still_missing))
    print("Media tools installed: yt-dlp, ffmpeg, ffprobe")
    for tool in MEDIA_TOOLS:
        print(f"  {tool}: {command_version(tool)}")


def selected_clients(args: argparse.Namespace) -> list[str]:
    requested = []
    if args.codex:
        requested = ["codex"]
    elif args.claude:
        requested = ["claude"]
    elif args.all:
        requested = ["codex", "claude"]
    else:
        requested = [name for name in ("codex", "claude") if shutil.which(name)]
        if not requested:
            if not sys.stdin.isatty():
                raise InstallError(
                    "No supported client CLI is installed. Rerun with --codex, --claude, or --all to install one."
                )
            print("No supported client CLI is installed. Which one should install.sh add?")
            print("  1) Codex CLI")
            print("  2) Claude Code")
            print("  3) Both")
            try:
                choice = input("Choose 1, 2, or 3 [1]: ").strip() or "1"
            except EOFError as error:
                raise InstallError("No client was selected; rerun with --codex, --claude, or --all.") from error
            choices = {"1": ["codex"], "2": ["claude"], "3": ["codex", "claude"]}
            if choice not in choices:
                raise InstallError("Choose 1, 2, or 3, then rerun install.sh.")
            requested = choices[choice]
    return requested


def install_client_cli(client: str, dry_run: bool) -> None:
    installer_url, shell = CLIENT_INSTALLERS[client]
    if dry_run:
        print(f"Would install missing {client} CLI using its official installer.")
        return
    print(f"Installing missing {client} CLI using its official installer…")
    try:
        with urlopen(installer_url, timeout=30) as response:
            final_url = response.geturl()
            final_parts = urlparse(final_url)
            if final_parts.scheme != "https" or final_parts.hostname not in CLIENT_INSTALLER_HOSTS[client]:
                raise InstallError(f"The official {client} installer redirected outside its trusted HTTPS hosts.")
            installer = response.read(4 * 1024 * 1024 + 1)
    except (OSError, URLError) as error:
        raise InstallError(f"Could not download the official {client} installer.") from error
    if not installer or len(installer) > 4 * 1024 * 1024:
        raise InstallError(f"The official {client} installer response was empty or unexpectedly large.")
    if not installer.lstrip().startswith(b"#!"):
        raise InstallError(f"The official {client} installer response was not a shell script.")
    if not shutil.which(Path(shell).name):
        raise InstallError(f"{Path(shell).name} is required to install {client}.")
    with tempfile.TemporaryDirectory(prefix=f"kinescope-{client}-installer-") as directory:
        installer_path = Path(directory) / "install.sh"
        installer_path.write_bytes(installer)
        environment = os.environ.copy()
        if client == "codex":
            environment["CODEX_NON_INTERACTIVE"] = "true"
        try:
            result = subprocess.run(
                [shell, str(installer_path)],
                check=False,
                env=environment,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallError(f"The official {client} installer could not complete.") from error
    if result.returncode:
        raise InstallError(f"The official {client} installer failed; no plugin settings were changed.")

    local_bin = Path.home() / ".local" / "bin"
    if local_bin.is_dir() and str(local_bin) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = f"{local_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    if not shutil.which(client):
        raise InstallError(
            f"{client} was installed, but is not on this process's PATH. Start a new terminal and rerun install.sh."
        )
    print(f"{client}: installed ({command_version(client)})")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if not args.dry_run and hasattr(os, "geteuid") and os.geteuid() == 0:
            raise InstallError("Run install.sh as your normal user, not with sudo; package managers elevate only when needed.")
        print_environment_summary()
        clients = selected_clients(args)
        missing_clients = [client for client in clients if not shutil.which(client)]
        missing_tools = [tool for tool in MEDIA_TOOLS if not shutil.which(tool)]
        if args.with_deps and missing_tools:
            media_plan(media_packages_for_missing(missing_tools))
        for client in missing_clients:
            install_client_cli(client, args.dry_run)
        if args.dry_run and missing_clients:
            for client in missing_clients:
                print(f"{client}: would register marketplace {MARKETPLACE} and install the plugin after CLI setup")
            install_media_tools(args.with_deps, args.dry_run)
            return 0
        # Read all current client state and dependency-manager support before changing either client's settings.
        states = {client: inspect_client(client) for client in clients}
        install_media_tools(args.with_deps, args.dry_run)
        for client in clients:
            install_client(client, args.dry_run, states[client])
    except InstallError as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
