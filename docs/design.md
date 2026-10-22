# Design

## Goal

Ship one public Git repository that installs the authorized Kinescope download workflow in Codex and Claude Code on Linux and macOS.

## Package shape

- The repository root is a marketplace for Codex and Claude Code.
- `plugins/kinescope-download/` is the installable plugin package. It contains one shared skill, its focused references, a portable Codex `plugin.json`, and Claude Code's `.claude-plugin/plugin.json`.
- `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json` expose that same plugin to their respective clients.
- `install.sh` preflights the JSON state of every selected client before changing either profile, then registers and installs the plugin in whichever supported client the user selects or already has. System media tools are checked separately and installed only through an explicit `--with-deps` option.
- `README.md` is the human entrypoint; CLI installers and documented manual commands must produce the same user-scope installation.

## Runtime and safety boundaries

- The plugin is a skill-only package. It does not run an MCP server, background process, hook, or privileged command.
- `yt-dlp` and FFmpeg are external media tools. The plugin must not install them silently; its installer offers dependency installation behind explicit opt-in.
- Downloads remain limited to media the user owns or is authorized to save. DRM/key circumvention and logging signed URLs or account credentials are out of scope.
- The 403 helper fetches only HTTPS pages on allowlisted Kinescope domains, validates redirects and manifest hosts, and caps page reads at 8 MiB.
- VPN changes stay domain-scoped, preserve ChatGPT's route, and require interface-aware evidence before claiming direct egress.
- The shell installer supports Bash 3.2+ on macOS and common Linux package managers. Unknown systems fail with manual instructions instead of guessing.

## Red-team review focus

- Repeated installation must not duplicate marketplaces or report success after a failed client command.
- Missing one client must not block installation into the other; missing both must fail clearly.
- Paths with spaces, hostile shell arguments, malformed JSON, and valid-but-wrong client JSON shapes must not cause word splitting, arbitrary command execution, or partial setup.
- The 403 helper must reject local/private/lookalike destinations before fetching, validate redirects, limit response size, and keep signed manifest URLs out of output.
- Dependencies must never be installed unless `--with-deps` was explicitly supplied; unknown Linux package managers must stop safely.
- Marketplace paths must stay inside the package, and installers must never fetch and execute a remote shell script.
