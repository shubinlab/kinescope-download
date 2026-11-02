# Authorized Video Download Workflow Design

## Intent

Improve the Kinescope Download plugin so it handles direct links supported by yt-dlp and videos in authorized Ontico DevNavigator cards. The verified Ontico path required a signed-in page session, reading the card's actual Kinescope player ID, the Ontico card as Referer, and recovery of an HTML-escaped HLS manifest. The available rendition was 720p30.

## Goals

- Add a focused adapter for `conf.ontico.ru/devnavigator/roadmaps/{roadmap_id}/card/{card_id}` that reads the card's `kinescope_id` from the authenticated card endpoint.
- Keep generic direct URL support through yt-dlp's own extractors for other sites; do not promise support for every website.
- Use the configured local browser cookie store by default for each authorized source/player request. Cookie handling must stay local and host-scoped; cookies for `conf.ontico.ru` must never be sent to Kinescope.
- Keep the Ontico card URL as the sanitized Referer for the Kinescope player request.
- Keep the manifest recovery helper restricted to Kinescope HTTPS hosts, with bounded reads, redirect checks, private outputs, and no signed-URL disclosure.
- Fix helper failure cleanup and aliased-output-path handling found by the Red Team.
- Install and test the packaged skill, repeat the Ontico download from the installed artifact, then publish the reviewed release.

## Non-goals

- Promise automatic support for every website or build a generic scraper for authenticated pages.
- Add adapters for sites beyond the specifically requested Ontico integration without demonstrated demand.
- Bypass DRM, recover decryption keys, or weaken site access controls.
- Add new runtime dependencies, daemons, MCP servers, or a new downloader executable.
- Include account data, cookies, signed URLs, private page contents, or video metadata from this user's account in the repository.

## Approaches

1. **Generic authenticated page scraper:** fetch and parse arbitrary login-gated pages and player APIs. Rejected because it expands the SSRF and credential surface, relies on unstable site internals, and cannot honestly cover arbitrary sites.
2. **Focused Ontico adapter plus yt-dlp direct extractors:** accept only numeric Ontico card paths on the exact HTTPS host, fetch only the matching card API endpoint with the local cookie jar, extract one validated Kinescope ID, and keep the card URL as Referer. Continue using yt-dlp directly for other supported URLs; keep the manifest recovery helper Kinescope-only. Chosen after the user's clarification and the expert review; this directly covers the demonstrated source without becoming a universal scraper.
3. **Browser-only player discovery:** have the agent inspect each card interactively and copy the player ID. Rejected as the primary workflow because browser access may be unavailable and it is less repeatable than the read-only card endpoint, but it remains a manual fallback.

## Implementation calls

- Add one shared cookie helper that reads browser cookies through yt-dlp's Python cookie API and returns only cookies matching a validated exact request host, all in memory. If system Python cannot import yt-dlp, use the interpreter behind the installed yt-dlp executable and return only filtered cookies over a captured local pipe. It must not write the full browser jar or construct Cookie headers.
- The Ontico card helper accepts its URL through stdin, calls only the fixed card API endpoint, validates the returned Kinescope player ID, and writes private player-URL and Referer files. The manifest helper accepts only `kinescope.io` or `player.kinescope.io` player pages and the currently verified manifest hosts, including `edge-msk-10.kinescopecdn.net`; new hosts require live evidence before addition. It writes a host-only cookie file for yt-dlp. Neither helper prints cookie values or response bodies.
- The fixed Ontico request is `GET /api/v1/dev-navigator/cards/{card_id}`. Require `status.name == "success"`, `result.id` to match the requested card, and one `result.kinescope_id` matching a bounded URL-safe ID pattern. Do not accept an API-supplied host or URL.
- Use Brave browser cookies for the Ontico API request, the Kinescope manifest route, and direct yt-dlp-supported source requests by default. Filter cookies separately for Ontico and Kinescope. Kinescope's recovered download uses a mode-`0600` Netscape jar with host-only cookie domains for the validated manifest host rather than the full browser jar. Never manually copy a Cookie header or forward cookies across hosts.
- Use the shared cookie helper for both the Ontico API adapter and Kinescope manifest recovery. It validates the requested host, filters cookies in memory, and keeps cookie values out of files, argv, logs, and outputs.
- The manifest recovery helper stays Kinescope-only. It receives a Kinescope-filtered cookie jar and the sanitized page Referer; Ontico cookies are never sent to Kinescope.
- Treat the Ontico card API path as an observed, undocumented integration. Validate every response and stop on contract drift instead of probing alternate endpoints.
- Update the shared skill and README to describe supported yt-dlp extractors and the Ontico card workflow, including automatic local browser-cookie use and truthful limits.
- Preserve private stdin/config handling for source and Referer URLs. Never put credentials, signed URLs, or cookie values in argv, shell history, logs, or package files.
- Do not broaden the Kinescope manifest helper's host allowlist to arbitrary sites.
- Reject output paths that resolve to the same location. If creation of the optional Referer file fails after the manifest was created, remove files created by that invocation.
- Bump all versioned Codex package manifests to 1.7.0; keep Claude's manifest and marketplace structure consistent.
- Add regression tests for the cleanup and path-alias cases, and package assertions for the generalized workflow and its boundaries.
- Publish the reviewed changes to the authorized GitHub repository and release tag. Then update the local Codex and Claude installations and verify the installed package bytes/version.

## Acceptance

- Full Python test suite, Bash syntax, Codex manifest checks, strict Claude plugin/marketplace validation, `git diff --check`, and a secret/content scan pass.
- Red Team covers shell/URL injection, signed URL and cookie leakage, private/lookalike redirects, ambiguous manifests, path aliases, partial-file cleanup, overwrite safety, auth failure, and DRM refusal.
- The locally installed packaged skill discovers and downloads at least two authorized Ontico card videos separately using Brave cookies and each actual Referer; no credentials or signed links appear in outputs or artifacts.
- The final MP4 is the highest available rendition, probes correctly, and fully decodes.
- GitHub main, tag, and release point to the reviewed commit; local Codex and Claude versions and skill files match that release.
