# Design

## Goal

Ship one public Git repository that installs the authorized Kinescope download workflow in Codex and Claude Code on Linux and macOS.

## Package shape

- The repository root is a marketplace for Codex and Claude Code.
- `plugins/kinescope-download/` is the installable plugin package. It contains one shared skill, its focused references, a portable Codex `plugin.json`, and Claude Code's `.claude-plugin/plugin.json`.
- `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json` expose that same plugin to their respective clients.
- `install.sh` is the complete POSIX-shell installer and the only user-facing runtime entry point. It detects the platform, installs missing Git/client/media tools through the selected native or official installer, and configures Codex/Claude plugin marketplaces without a Python handoff. Authentication stays user-controlled.
- `README.md` is the human entrypoint; CLI installers and documented manual commands must produce the same user-scope installation.

## Runtime and safety boundaries

- The plugin is a skill-only package. It does not run an MCP server, background process, hook, or privileged command.
- `yt-dlp` and FFmpeg are external media tools. The plugin must not install them silently; its installer offers dependency installation behind explicit opt-in.
- Downloads remain limited to media the user owns or is authorized to save. DRM/key circumvention and logging signed URLs or account credentials are out of scope.
- The 403 helper fetches only HTTPS pages on allowlisted Kinescope domains, validates redirects and manifest hosts, and caps page reads at 8 MiB.
- VPN changes stay domain-scoped, preserve ChatGPT's route, and require interface-aware evidence before claiming direct egress.
- The shell installer supports POSIX sh on macOS and common Linux package managers. Unknown systems and known immutable variants fail with manual instructions instead of guessing.

## Red-team review focus

- Repeated installation must not duplicate marketplaces or report success after a failed client command.
- When no client is present, interactive runs ask which one to install and noninteractive runs require `--codex`, `--claude`, or `--all`; installing a CLI must never authenticate the user.
- Paths with spaces, hostile shell arguments, malformed JSON, and valid-but-wrong client JSON shapes must not cause word splitting, arbitrary command execution, or partial setup.
- The 403 helper must reject local/private/lookalike destinations before fetching, validate redirects, limit response size, and keep signed manifest URLs out of output.
- Missing media dependencies install automatically through a positively identified supported package manager; immutable Linux systems fail closed before package changes.
- Marketplace paths must stay inside the package. Official Codex and Claude installer scripts may be downloaded only over HTTPS to a temporary file and executed only after HTTPS redirect, response-size, and shebang checks; never execute them through a remote shell pipe.
