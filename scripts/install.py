#!/usr/bin/env python3
"""Install the Kinescope Download plugin in Codex and/or Claude Code."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN = "kinescope-download"
MARKETPLACE = "kinescope-download"
MARKETPLACE_SOURCE = "shubinlab/kinescope-download"
MEDIA_TOOLS = ("yt-dlp", "ffmpeg", "ffprobe")


class InstallError(RuntimeError):
    pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the Kinescope Download plugin in Codex and/or Claude Code."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--codex", action="store_true", help="Install in Codex only.")
    selection.add_argument("--claude", action="store_true", help="Install in Claude Code only.")
    selection.add_argument("--all", action="store_true", help="Install in both clients; both must be present.")
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
        if not shutil.which("brew"):
            raise InstallError("Homebrew is required to install missing macOS media tools. Install Homebrew, then retry.")
        return [["brew", "install", *packages]]
    if system != "Linux":
        raise InstallError(f"Automatic media dependency installation is unsupported on {system or 'this OS'}.")

    if shutil.which("omarchy"):
        return [["omarchy", "pkg", "add", *packages]]

    manager = next((name for name in ("apt-get", "dnf", "pacman", "zypper") if shutil.which(name)), None)
    if not manager:
        raise InstallError("No supported Linux package manager found. Install yt-dlp and FFmpeg manually.")

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
            raise InstallError("Neither `codex` nor `claude` is installed. Install a client, then rerun install.sh.")

    missing = [name for name in requested if not shutil.which(name)]
    if missing:
        raise InstallError("Requested client not found: " + ", ".join(missing))
    return requested


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if not args.dry_run and hasattr(os, "geteuid") and os.geteuid() == 0:
            raise InstallError("Run install.sh as your normal user, not with sudo; package managers elevate only when needed.")
        clients = selected_clients(args)
        # Read all current client state and dependency-manager support before changing either client's settings.
        states = {client: inspect_client(client) for client in clients}
        missing_tools = [tool for tool in MEDIA_TOOLS if not shutil.which(tool)]
        if args.with_deps and missing_tools:
            media_plan(media_packages_for_missing(missing_tools))
        for client in clients:
            install_client(client, args.dry_run, states[client])
        install_media_tools(args.with_deps, args.dry_run)
    except InstallError as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
