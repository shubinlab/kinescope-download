---
name: kinescope-video-download
description: Use when downloading Kinescope video links to MP4, selecting the best available stream, diagnosing yt-dlp HLS or HTTP 403 failures, or keeping Kinescope traffic outside a VPN while preserving other VPN routes.
---

# Kinescope Video Download

Download only videos the user owns or is authorized to save. Prefer Kinescope's authenticated asset download when the user has legitimate workspace access; otherwise use the public/player HLS stream that the user can already access. Do not defeat DRM, recover decryption keys, or use tools whose purpose is DRM circumvention.

## Local download history

History is opt-in and shared by Codex and Claude on this machine. At the start of a download task, run `summary` with the absolute `local_history.py` path beside this loaded SKILL.md; do not resolve it relative to the user's working directory. If history is enabled, use its fixed-category summary as context; do not read `events.jsonl` directly or change concurrency based on one attempt. Consider a different concurrency only after at least five comparable attempts across more than one concurrency value. At the start of each actual download attempt, call `start` and retain its run ID plus the exact client, source, plugin version, and yt-dlp version. After success, failure, or cancellation, call `finish` once with that run ID and the same retained metadata; only concurrency may change after retries. Record coarse transfer-time, media-duration, size, and resolution buckets, probe/decode outcomes, retry bucket, and concurrency. Never pass a URL, title, video ID, path, referrer, raw command, or error text to the history script.

If a nonempty summary is used, briefly tell the user that recent local history informed the workflow; those values become part of the current model context. If history is disabled or empty, do not mention it. If reading or writing history fails, continue the video task without history and tell the user the entry was not saved. The logger writes only to local state and makes no network calls. `status`, `summary`, `disable`, and `clear --confirm` are available through `local_history.py`. Clearing removes saved attempts but leaves collection enabled; run `disable` before `clear --confirm` to stop future writes too. Starts without finishes are labeled open/unclosed because they may still be active.

## Choose the source and downloader

1. Check installed `yt-dlp`, `ffmpeg`, and `ffprobe` versions. Do not assume a Kinescope-specific extractor exists or hardcode a format ID from another video.
2. For an account owner with an existing local API token and legitimate workspace access, read [references/api-original.md](references/api-original.md). Otherwise use the share/embed HLS route. Never ask the user to paste a token.
3. For a share or embed URL, use yt-dlp's dynamic `bestvideo+bestaudio/best` selector. A separate `-F` request is only needed when the selector fails, the user asks for the format table, or you need to diagnose availability. Always feed the URL to yt-dlp over stdin with `--batch-file -`; do not put it in command text, environment variables, argv, history, or logs. Kinescope pages may use yt-dlp's generic extractor and expose a signed HLS manifest through player metadata.
4. If extraction fails with 403, use the local manifest extraction and secure temporary-file procedure in [references/403-recovery.md](references/403-recovery.md). It handles HTML entities such as `&amp;` and JavaScript escapes such as `\u0026`. Preserve the page's authorized referrer when required. Treat signed manifest URLs as short-lived credentials: keep them out of chat, logs, skill files, shell history, and final output.
5. Do not add browser cookies by default. If the share requires a signed-in session, use existing browser cookies only when the user has authorized that access; keep cookies local and never print them.

If the user asks whether a webinar page is live or recorded, read [references/playlist-status.md](references/playlist-status.md). Do not infer the event's origin from a playlist marker alone.

## Download efficiently

Use yt-dlp's HLS fragment concurrency with a bounded starting value, then adjust only from observed throughput and errors:

In an interactive Bash terminal, read the URL without echoing it and pass it to yt-dlp through stdin:

```bash
if ! (
  set +x
  unset KINESCOPE_URL KINESCOPE_REFERRER REFERRER_URL safe_referrer
  IFS= read -r -s -p 'Kinescope share/embed URL: ' KINESCOPE_URL < /dev/tty || exit 1
  printf '\n' >&2
  printf '%s\n' "$KINESCOPE_URL"
) | env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer yt-dlp --quiet --no-progress --no-playlist \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --print after_move:filepath \
  --output '/path/to/output/%(title)s-%(id)s.%(ext)s' \
  --batch-file -; then
  exit 1
fi
```

This gives yt-dlp a private, non-echoed URL without exposing it in shell history, environment variables, or process arguments. Use `--quiet --no-progress` by default so captured runs produce the final path and errors without one progress line per fragment; show live progress only when the user asks. For an embedding-page referrer, omit it unless required. When required, use the private prompt/config-file flow in [references/403-recovery.md](references/403-recovery.md); never pass a credential-bearing referrer as an argument.

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
