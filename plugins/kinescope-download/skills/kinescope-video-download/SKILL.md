---
name: kinescope-video-download
description: Use when downloading authorized Kinescope videos, resolving Ontico DevNavigator card URLs, using yt-dlp-supported video pages, diagnosing HLS or HTTP 403 failures, or routing Kinescope traffic around a VPN.
---

# Kinescope and Ontico Video Download

Download only videos the user owns or is authorized to save. Use yt-dlp for supported video pages, the Ontico adapter for authenticated DevNavigator cards, and Kinescope's authorized original or HLS routes. Use the user's local browser cookies by default for every source-page, format-probe, and download request. Keep cookies local and rely on domain-scoped matching. Never defeat DRM, recover decryption keys, or use circumvention tools.

## Local download history

History is opt-in and shared by Codex and Claude on this machine. At the start of a download task, run `summary` with the absolute `local_history.py` path beside this loaded SKILL.md; do not resolve it relative to the user's working directory. If history is enabled, use its fixed-category summary as context; do not read `events.jsonl` directly or change concurrency based on one attempt. Consider a different concurrency only after at least five comparable attempts across more than one concurrency value. At the start of each actual download attempt, call `start` and retain its run ID plus the exact client, source, plugin version, and yt-dlp version. After success, failure, or cancellation, call `finish` once with that run ID and the same retained metadata; only concurrency may change after retries. Record coarse transfer-time, media-duration, size, and resolution buckets, probe/decode outcomes, retry bucket, and concurrency. Never pass a URL, title, video ID, path, referrer, raw command, or error text to the history script.

If a nonempty summary is used, briefly tell the user that recent local history informed the workflow; those values become part of the current model context. If history is disabled or empty, do not mention it. If reading or writing history fails, continue the video task without history and tell the user the entry was not saved. The logger writes only to local state and makes no network calls. `status`, `summary`, `disable`, and `clear --confirm` are available through `local_history.py`. Clearing removes saved attempts but leaves collection enabled; run `disable` before `clear --confirm` to stop future writes too. Starts without finishes are labeled open/unclosed because they may still be active.

## Choose the source and downloader

1. Check installed `yt-dlp`, `ffmpeg`, and `ffprobe` versions. Inspect formats only when needed; never reuse a format ID from another video.
2. For an account owner with a local API token, read [references/api-original.md](references/api-original.md). Never ask the user to paste a token.
3. If the input matches an Ontico DevNavigator card URL, use [the Ontico adapter](#ontico-devnavigator-cards) to resolve its player URL, then follow the Kinescope manifest flow below. Do not reuse a player ID from another page or log.
4. For Kinescope share or player URLs, use the Kinescope-only manifest flow in [references/403-recovery.md](references/403-recovery.md). It applies the actual page Referer and gives yt-dlp a host-only cookie file for the validated manifest host.
5. For direct pages on other sites, use yt-dlp's extractors. They cover yt-dlp-supported pages; unsupported login-gated pages need an authorized player URL from the user or browser.
6. Use the user's browser cookies by default. For direct yt-dlp sources, use `--cookies-from-browser`; on this Linux Brave setup, use `brave+gnomekeyring`, and on KWallet systems choose the matching yt-dlp keyring spec. On macOS, use `brave`. For Ontico and Kinescope helpers, use their host-filtered cookie jars. The Python helper uses yt-dlp's cookie API in memory and, when system Python cannot import yt-dlp, uses the interpreter behind the installed yt-dlp executable and captures only filtered cookies locally. If browser cookies cannot be read, stop; do not ask for raw cookies or try to decrypt the browser key yourself.
7. Use yt-dlp's dynamic `bestvideo+bestaudio/best` selector. Feed source URLs through stdin or a private batch file, not command text, environment variables, argv, history, or logs. Signed manifests are short-lived credentials; keep them out of chat, logs, shell history, skill files, and final output.

When a direct, non-Kinescope source needs a format check, run a cookie-enabled `-F` probe. Keep the URL on stdin and suppress raw errors that could echo a signed link. For Kinescope, inspect the manifest file produced by the recovery helper with its host-filtered cookie file.

```bash
BROWSER_SPEC=brave+gnomekeyring # Linux Brave with GNOME Keyring; use the local yt-dlp spec on other hosts.
if ! (
  set +x
  unset KINESCOPE_URL KINESCOPE_REFERRER REFERRER_URL safe_referrer
  IFS= read -r -s -p 'Authorized video or share URL: ' KINESCOPE_URL < /dev/tty || exit 1
  printf '\n' >&2
  printf '%s\n' "$KINESCOPE_URL"
) | env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer yt-dlp --quiet --no-warnings \
  --cookies-from-browser "$BROWSER_SPEC" \
  --no-playlist --batch-file - -F 2>/dev/null; then
  printf 'Format probe failed; check the authorized page and Referer.\n' >&2
fi
```

For an embedded player, use the actual parent page as Referer when the embed expects it. If a direct player URL returns 403, retry once with the observed parent page in a private config file. The Ontico adapter supplies the card URL as Referer. Do not guess a Referer or copy query credentials into the config.

If the user asks whether a webinar page is live or recorded, read [references/playlist-status.md](references/playlist-status.md). Do not infer the event's origin from a playlist marker alone.

### Ontico DevNavigator cards

Accept only `https://conf.ontico.ru/devnavigator/roadmaps/<number>/card/<number>` URLs. Read the card URL at a hidden prompt. Keep the player URL, Referer, and yt-dlp config in a `0700` temporary directory. Invoke the adapter with the card URL on stdin:

```bash
BROWSER_SPEC=brave+gnomekeyring # Linux Brave with GNOME Keyring; use the local yt-dlp spec on other hosts.
OUTPUT_DIR='/path/to/output'
set +x
set -euo pipefail
mkdir -p "$OUTPUT_DIR"
helper="<installed-skill-directory>/scripts/extract_ontico_card.py"
manifest_helper="<installed-skill-directory>/scripts/extract_manifest.py"
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ontico-video.XXXXXX")
chmod 700 "$temp_dir"
player_url_file="$temp_dir/player-url.txt"
referer_file="$temp_dir/referer.txt"
manifest_file="$temp_dir/manifest.txt"
cookie_file="$manifest_file.cookies"
yt_dlp_config="$temp_dir/yt-dlp.conf"
trap 'rm -f -- "$player_url_file" "$referer_file" "$manifest_file" "$cookie_file" "$yt_dlp_config"; rmdir "$temp_dir"' EXIT

if ! (
  set +x
  IFS= read -r -s -p 'Ontico card URL: ' ONTICO_CARD_URL < /dev/tty || exit 1
  printf '\n' >&2
  printf '%s\n' "$ONTICO_CARD_URL"
) | python3 "$helper" --url-stdin "$BROWSER_SPEC" "$player_url_file" "$referer_file"; then
  exit 1
fi

safe_referrer=$(<"$referer_file")
(umask 077; printf '%s\n%s\n' '--referer' "$safe_referrer" > "$yt_dlp_config")
unset safe_referrer

if ! (cat "$player_url_file"; cat "$referer_file") | python3 "$manifest_helper" --url-stdin "$BROWSER_SPEC" "$manifest_file"; then
  exit 1
fi

yt-dlp --quiet --no-progress --no-playlist \
  --cookies "$cookie_file" \
  --config-locations "$yt_dlp_config" \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --print after_move:filepath \
  --output "$OUTPUT_DIR/%(title)s-%(id)s.%(ext)s" \
  --batch-file "$manifest_file"
```

The adapter reads Brave cookies through yt-dlp's Python cookie API and keeps only cookies matching `conf.ontico.ru` for the card API request. If the system Python cannot import yt-dlp, it uses yt-dlp's own interpreter and captures only filtered cookies. It calls one observed card endpoint and validates the returned player ID. The endpoint is undocumented and may be outdated; on an auth error or changed response, stop and ask the user to check the card in Ontico. Do not probe alternate endpoints.

The manifest helper accepts only `kinescope.io` or `player.kinescope.io` pages and exact manifest hosts, including the observed `edge-msk-10.kinescopecdn.net`. It stores the manifest and a host-only cookie file with mode `0600`; the final yt-dlp download reads only that file. The adapter stores its player URL and Referer with mode `0600` and refuses existing or aliased paths.

## Download efficiently

For direct, non-Kinescope URLs supported by yt-dlp, use its HLS fragment concurrency with a bounded starting value, then adjust only from observed throughput and errors. Use the Kinescope manifest flow above for Kinescope URLs.

In an interactive Bash terminal, read the URL without echoing it and pass it to yt-dlp through stdin. Set `BROWSER_SPEC` to the local browser/keyring form; for Linux Brave with GNOME Keyring use `brave+gnomekeyring`.

```bash
BROWSER_SPEC=brave+gnomekeyring # Linux Brave with GNOME Keyring; use the local yt-dlp spec on other hosts.
OUTPUT_DIR='/path/to/output'
mkdir -p "$OUTPUT_DIR"
if ! (
  set +x
  unset KINESCOPE_URL KINESCOPE_REFERRER REFERRER_URL safe_referrer
  IFS= read -r -s -p 'Authorized video or share URL: ' KINESCOPE_URL < /dev/tty || exit 1
  printf '\n' >&2
  printf '%s\n' "$KINESCOPE_URL"
) | env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer yt-dlp --quiet --no-progress --no-playlist \
  --cookies-from-browser "$BROWSER_SPEC" \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --print after_move:filepath \
  --output "$OUTPUT_DIR/%(title)s-%(id)s.%(ext)s" \
  --batch-file -; then
  exit 1
fi
```

This gives yt-dlp a private, non-echoed URL without exposing it in shell history, environment variables, or process arguments. Use `--quiet --no-progress` by default. For an embedding-page Referer, omit it unless required; when needed, keep it in a private config file. Never pass a signed or credential-bearing Referer as an argument. yt-dlp applies browser cookies by domain, so cookies from the page's host are not sent to another host.

If a format table shows separate video/audio IDs that the selector misses, use those IDs for this video only. If the host returns 429s or fragment failures, reduce concurrency (8, then 4). Keep yt-dlp's resumable partial state; do not delete partial files to restart a transfer.

## VPN routing

Only when the user explicitly asks to route Kinescope outside a VPN, read [references/vpn-routing.md](references/vpn-routing.md). Do not change VPN routing for ordinary downloads, and do not claim direct egress without interface-aware packet evidence.

## Validate the deliverable

Confirm that yt-dlp finished merging and that the final MP4 exists. Use `ffprobe` once to check the container, video resolution/frame rate, audio stream, duration, and file size. For a full integrity check, decode all streams once:

```bash
ffmpeg -v error -i '/path/to/output.mp4' -map 0 -f null -
```

Wait for that decode process to finish and record its exit code; report a decode pass only for exit code 0. Do not start another full decode merely because progress output was suppressed or a completed tool result was not printed. Do not call an HLS manifest, `.part`, format fragment, or incomplete decode a finished MP4. Report the chosen resolution, frame rate, codecs, duration, output path, and any routing limitation. Never include expiring signed URLs or account credentials in the report.

## Package research

For dependency or package research requests, read [references/research.md](references/research.md) and recheck current project activity before recommending a package.
