# Design

## Goal

Ship one public Git repository that installs an authorized Kinescope and Ontico DevNavigator download workflow in Codex and Claude Code on Linux and macOS. Direct video pages supported by yt-dlp remain available through its built-in extractors.

## Package shape

- The repository root is a marketplace for Codex and Claude Code.
- `plugins/kinescope-download/` is the installable plugin package. It contains one shared skill, its focused references, a portable Codex `plugin.json`, and Claude Code's `.claude-plugin/plugin.json`.
- `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json` expose that same plugin to their respective clients.
- `install.sh` is the complete POSIX-shell installer and the only user-facing runtime entry point. It detects the platform, installs missing Git/client/media tools through the selected native or official installer, and configures Codex/Claude plugin marketplaces without a Python handoff. Authentication stays user-controlled.
- `README.md` is the human entrypoint; CLI installers and documented manual commands must produce the same user-scope installation.

## Runtime and safety boundaries

- The plugin is a skill-only package. It does not run an MCP server, background process, hook, or privileged command.
- `yt-dlp` and FFmpeg are external media tools. The installer installs missing tools by default through a detected native package manager and reports the package names before invoking it.
- Downloads remain limited to media the user owns or is authorized to save. DRM/key circumvention and logging signed URLs, browser cookies, or account credentials are out of scope.
- The shared cookie helper reads the local browser store through yt-dlp's Python API, filters cookies in memory to the validated request host, and never writes the full browser jar. If the system Python cannot import yt-dlp, it uses the interpreter behind the installed yt-dlp executable and returns only filtered cookies through a captured local pipe.
- The Ontico adapter accepts only HTTPS DevNavigator card URLs on `conf.ontico.ru`, reads one card endpoint, and fails closed if the response changes. yt-dlp and the recovery helper receive only cookies matching their request hosts.
- The 403 helper fetches pages only from the exact Kinescope player hosts, permits only known manifest hosts (including the verified Moscow edge), keeps redirects on the original page host, and caps page reads at 8 MiB.
- VPN changes stay domain-scoped, preserve ChatGPT's route, and require interface-aware evidence before claiming direct egress.
- The shell installer supports POSIX sh on macOS and common Linux package managers. Unknown systems and known immutable variants fail with manual instructions instead of guessing.

## Red-team review focus

- Repeated installation must not duplicate marketplaces or report success after a failed client command.
- When no client is present, install both by default; `--codex` or `--claude` selects one. Installing a CLI must never authenticate the user.
- Paths with spaces, hostile shell arguments, malformed JSON, and valid-but-wrong client JSON shapes must not cause word splitting, arbitrary command execution, or partial setup.
- The 403 helper must reject private/lookalike and unlisted subdomains before fetching, limit response size, and keep signed manifest URLs out of output. Its final yt-dlp download uses a host-only cookie file for the validated manifest host.
- The shared cookie helper must reject untrusted URL/browser specs and exclude cookies that do not match the specific request host.
- The Ontico adapter must reject lookalike hosts, malformed card paths, external redirects, oversized/error API responses, mismatched IDs, and untrusted player IDs; cookies stay local and domain-matched.
- Missing media dependencies install automatically through a positively identified supported package manager; immutable Linux systems fail closed before package changes.
- Marketplace paths must stay inside the package. Official Codex and Claude installer scripts may be downloaded only over HTTPS to a temporary file and executed only after HTTPS redirect, response-size, and shebang checks; never execute them through a remote shell pipe.
