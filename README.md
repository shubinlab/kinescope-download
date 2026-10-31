<div align="center">
  <img src="assets/logo.svg" width="420" alt="Kinescope Download">
  <p><strong>The careful, fast path from a Kinescope player link to a verified MP4.</strong></p>
  <p>
    <a href="https://github.com/shubinlab/kinescope-download/actions"><img src="https://img.shields.io/github/actions/workflow/status/shubinlab/kinescope-download/ci.yml?branch=main&label=checks" alt="Checks"></a>
    <img src="https://img.shields.io/badge/Linux-supported-4c9a6a" alt="Linux supported">
    <img src="https://img.shields.io/badge/macOS-supported-6376d8" alt="macOS supported">
    <img src="https://img.shields.io/badge/Codex-plugin-15b8a6" alt="Codex plugin">
    <img src="https://img.shields.io/badge/Claude%20Code-plugin-d98b52" alt="Claude Code plugin">
  </p>
</div>

---

Kinescope Download packages one shared skill for **Codex** and **Claude Code**. It checks the available streams, prefers the highest authorized source, uses bounded parallel HLS downloads, and verifies the final MP4.

## Install

### Omarchy Linux

This repository installs the Kinescope plugin and, with `--with-deps`, missing media tools. It does not install or sign in to Codex or Claude Code. Install one of those client CLIs separately using the [official Codex setup](https://github.com/openai/codex#installing-and-running-codex-cli) or [Claude Code setup](https://code.claude.com/docs/en/setup). Git and Python 3 must also be on `PATH`; if either is missing on Omarchy, use its package helper:

```bash
omarchy pkg add git python
```

On Omarchy, `--with-deps` installs only missing `yt-dlp` and FFmpeg packages through `omarchy pkg add`.

### macOS

Install Codex or Claude Code separately. With Homebrew, choose one:

```bash
brew install --cask codex
# or
brew install --cask claude-code
```

Git and Python 3 must also be on `PATH`; if either is missing, install them with `brew install git python`. On macOS, Homebrew is required for media dependencies only when a media tool is missing and you pass `--with-deps`.

### Other Linux distributions

The same installer supports apt-get (Debian/Ubuntu), dnf, pacman, and zypper when Omarchy is not present. It does not bootstrap a package manager or add third-party package sources. Package installation is opt-in through `--with-deps`.

### Install behavior

On either platform, clone the repository and run the checked-out installer:

```bash
git clone --depth 1 https://github.com/shubinlab/kinescope-download.git kinescope-download && kinescope-download/install.sh --with-deps
```

It does not pipe a remote script into a shell. Run as your regular user, not with `sudo`. By default it installs into every supported client found on `PATH`; use `--codex` or `--claude` for one client, and `--all` only when both are installed. `--with-deps` explicitly authorizes installation of missing media tools. The installer checks whether `yt-dlp`, `ffmpeg`, and `ffprobe` are discoverable; it does not check versions or run health probes.

To preview planned client and package changes, clone the repository and run `kinescope-download/install.sh --with-deps --dry-run`. Installation steps are sequential, not transactional; if a later step fails, earlier setup may remain. The installer checks current state on retry and avoids duplicate client registrations.

### Select one client

```bash
./install.sh --codex
./install.sh --claude
```

Or install both explicitly:

```bash
./install.sh --all
```

The default `./install.sh` installs into whichever supported clients are already on `PATH`. The script never runs as root; package managers use `sudo` only when you explicitly pass `--with-deps`.

### Install manually

Codex:

```bash
codex plugin marketplace add shubinlab/kinescope-download
codex plugin add kinescope-download@kinescope-download
```

Claude Code:

```bash
claude plugin marketplace add shubinlab/kinescope-download
claude plugin install kinescope-download@kinescope-download --scope user
```

In Claude Code, invoke `/kinescope-download:kinescope-video-download`. Codex can select the packaged skill when a Kinescope download request matches it.

## Requirements

- Git to clone the repository.
- Python 3 to run the cross-platform installer.
- Codex CLI with plugin marketplace support and/or Claude Code with plugin commands.
- `yt-dlp` and FFmpeg (`ffprobe` is included with FFmpeg) to download and verify media.

The client CLI must already be installed; this repository installs the plugin, not Codex or Claude Code itself.

The plugin can be installed without media tools. If they are missing, it reports that; `--with-deps` installs them using a supported package manager. `--dry-run` does not install packages or change client configuration.

## What it does

1. Guides the use of one unambiguous authorized original API asset when a token is already available locally, including container inspection, stream-copy remux when possible, and HLS fallback if remuxing is unsupported.
2. Selects the highest available video and audio streams for the specific video, then merges them into MP4.
3. Starts HLS fragment concurrency at 16 and reduces it if the service returns rate limits or fragment errors.
4. Recovers a common 403 caused by HTML-escaped signed manifests without printing or storing signed URLs in logs.
5. Uses quiet output for agent runs and checks MP4 metadata plus a full decode before reporting verified output.
6. Changes VPN routing only when requested, and keeps other destinations such as ChatGPT on the VPN.

Quality is per-video. A 4K rendition is used only when the authorized stream actually provides one; format IDs are never hardcoded between videos.

## Safety and privacy

- Use this only for videos you own or are authorized to save.
- The plugin does not bypass DRM, retrieve decryption keys, or install DRM circumvention tools.
- Keep API tokens, browser cookies, and signed URLs local; do not paste them into chat or publish raw downloader errors. The 403 helper stores the extracted manifest in a mode-`0600` temporary file and does not print it.
- Signed or token-bearing share and API URLs should be passed on stdin; do not put them in command text, shell history, environment variables, or process arguments.
- The plugin does not silently choose among ambiguous API assets or transcode a non-MP4 original; it reports the format and asks before a lossy conversion.
- Installation adds a marketplace and the plugin to the selected client. It does not add hooks, MCP servers, daemons, or startup tasks.
- VPN changes are opt-in. Direct egress is not claimed from a route table or socket address alone; the skill requires interface-aware packet evidence during transfer.

## Local download history

The plugin can keep an opt-in, local download history shared by Codex and Claude on the same machine. It is disabled by default for new users. From a repository checkout, enable or inspect it with:

```bash
python3 plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py enable
python3 plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py summary
python3 plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py status
python3 plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py disable
python3 plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py clear --confirm
```

History is stored under `~/.local/state/kinescope-download/` for a stable per-user path shared by Codex and Claude on the same machine, with a private directory and file permissions. It records only fixed categories: day, random run/event IDs, client and plugin/tool versions, source type, outcome/failure class, concurrency/retry buckets, coarse transfer-time/media-duration/size/resolution buckets, and probe/decode results. It never records URLs, video IDs, titles, paths, referrers, credentials, transcripts, commands, or raw errors. Complete entries are append-only; the reader shows a summary of the latest 20 attempts and labels starts without finishes as open/unclosed because they may still be running. At 16 MiB, new writes stop rather than deleting old entries. Clearing removes saved attempts but leaves collection enabled; run `disable` before `clear --confirm` to stop future writes too.

The logger itself makes no network requests and does not upload history. The state directory may still be part of a user-configured sync or network filesystem. The plugin keeps history across client restarts and plugin updates but does not back it up; it can be lost if the home directory or disk is lost. When the skill reads the sanitized summary, those metrics become model context and may be sent to the selected Codex or Claude model provider. Marketplace users can find the installed script under the plugin path shown by `codex plugin list --json` (`source.path`) or `claude plugin list --json` (`installPath`), followed by `skills/kinescope-video-download/scripts/local_history.py`.

Kinescope is a trademark of its owner. Kinescope Download is an independent community project and is not affiliated with or endorsed by Kinescope.

## Update or uninstall

Update through Codex:

```bash
codex plugin marketplace upgrade kinescope-download
```

Update through Claude Code:

```bash
claude plugin update kinescope-download@kinescope-download
```

To uninstall, remove the plugin first, then its marketplace:

```bash
codex plugin remove kinescope-download@kinescope-download
codex plugin marketplace remove kinescope-download
claude plugin uninstall kinescope-download@kinescope-download --scope user
claude plugin marketplace remove kinescope-download --scope user
```

Uninstalling the plugin does not remove `yt-dlp`, FFmpeg, downloaded videos, or unrelated marketplaces.

## Development checks

```bash
python3 -m unittest discover -s tests -v
claude plugin validate --strict plugins/kinescope-download
claude plugin validate --strict .
```

For the exact package design, see [`docs/design.md`](docs/design.md). For workflow rules and format research, see the packaged [Kinescope skill](plugins/kinescope-download/skills/kinescope-video-download/SKILL.md).
