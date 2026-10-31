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

1. Check the installed `yt-dlp`, `ffmpeg`, and `ffprobe` versions. Inspect available formats before choosing a quality. Do not assume a Kinescope-specific extractor exists or hardcode a format ID from another video.
2. For an account owner, use Kinescope's documented `GET /v1/videos/:video_id` API only when an API token is already available in a local environment variable. Prefer an authorized original asset's `download_link` only when the API identifies one unambiguous original. Treat that signed link as a credential: pass it to a downloader through stdin (for yt-dlp, `--batch-file -`), never through command text, shell history, environment variables, or process arguments. Inspect the downloaded file with `ffprobe`: accept it as MP4 only when the container and streams are supported; if it is not MP4, try a stream-copy remux, then use the HLS route if remuxing fails. Do not silently transcode or choose among ambiguous assets; report the available format and ask before a lossy conversion. Never ask the user to paste a token, print it, or log signed links.
3. For a share or embed URL, try `yt-dlp --list-formats URL` first when the URL has no credential-bearing query. For a signed or token-bearing share URL, read it through a hidden prompt and pass it to yt-dlp with `--batch-file -` on stdin so it does not appear in command history or process arguments. Kinescope pages can expose a signed HLS manifest through JSON-LD/player metadata even when yt-dlp labels the site as `generic`. Select the highest listed video and audio formats and merge to MP4.
4. If extraction fails with 403, use the local manifest extraction and secure temporary-file procedure in [references/403-recovery.md](references/403-recovery.md). It handles HTML entities such as `&amp;` and JavaScript escapes such as `\u0026`. Preserve the page's authorized referrer when required. Treat signed manifest URLs as short-lived credentials: keep them out of chat, logs, skill files, shell history, and final output.
5. Do not add browser cookies by default. If the share requires a signed-in session, use existing browser cookies only when the user has authorized that access; keep cookies local and never print them.

If the user asks whether a webinar page is live or playing a recording, do not infer that from the page, chat, viewer count, or duration. Use an explicit player status if available. If the media playlist can be inspected locally without exposing its signed URL, `#EXT-X-PLAYLIST-TYPE:VOD` declares a VOD playlist and `#EXT-X-ENDLIST` means no additional segments will be added. These markers describe the playlist, not whether the program was originally prerecorded or broadcast live. If the playlist is unavailable, unsafe to inspect, or lacks clear status, report that the live/recording origin is unknown.

## Download efficiently

Use yt-dlp's HLS fragment concurrency with a bounded starting value, then adjust only from observed throughput and errors:

```bash
yt-dlp --no-playlist \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --referer 'EMBED_PAGE_URL' \
  --output '/path/to/output/%(title)s-%(id)s.%(ext)s' \
  'KINESCOPE_URL'
```

Pass `--referer` only when the player requires it; use the actual embedding page URL when known. If it is not embedded, omit the option or use the direct Kinescope page URL only when that is the observed referrer. For the secure stdin pattern and 403 recovery, read [references/403-recovery.md](references/403-recovery.md); do not put signed or token-bearing URLs in command text, shell history, argv, or logs.

For captured output on fragment-heavy HLS, do not combine `--progress` with `--newline`: yt-dlp writes each progress update as a new line. Use `--no-progress` when only completion and the final path are needed. If ongoing progress is useful, omit `--newline` and use `--progress-delta 5` only when the installed yt-dlp supports it.

If the inspected format table shows separate video/audio IDs that the selector misses, use those IDs for this video only. If the host returns 429s, fragment failures, or slower throughput at 16, retry with fewer concurrent fragments (8, then 4). Keep yt-dlp's resumable partial state; do not delete partial files to restart a transfer.

## Keep VPN routing narrow

Change VPN routing only when the user asks for Kinescope to bypass the VPN. First inspect the active VPN and both exclusion lists. Never disable the VPN, change its global mode, rewrite routes/firewall rules, or add ChatGPT domains to exclusions as a shortcut.

For AdGuard VPN, inspect its mode and lists before changing them. Resolve the installed CLI instead of assuming a Linux or macOS binary path:

```bash
command -v adguardvpn-cli
adguardvpn-cli status
adguardvpn-cli site-exclusions mode
adguardvpn-cli site-exclusions show --for-mode general
adguardvpn-cli site-exclusions show --for-mode selective
```

If the CLI is not on `PATH`, use only a documented path for that platform. If the installed client has no supported exclusion interface, stop before modifying routes or firewall rules. In `general` mode, add only the exact Kinescope playback/CDN hostnames observed in this video's manifest to the general exclusions list. The share page host is often `kinescope.io`; inspect the manifest for redirected or segment hosts before deciding whether another exact hostname is needed. Do not add broad wildcard domains without evidence. In `selective` mode, sites in the selective list go through the VPN; leave Kinescope out of that list when the user requests direct routing, removing an exact matching Kinescope entry only if present. If a wildcard rule covers the needed host, do not remove that broader rule automatically; explain the effect and ask before changing it. Do not switch modes. For other VPN products or unclear mode semantics, inspect supported split tunneling before changing anything.

For example, after confirming the active mode and current list, the narrow General-mode mutation is:

```bash
adguardvpn-cli site-exclusions add kinescope.io --for-mode general
```

Add another exact hostname only if it appears in this video's manifest or redirects. In Selective mode, remove only a matching exact entry when direct routing is requested:

```bash
adguardvpn-cli site-exclusions remove kinescope.io --for-mode selective
```

After an exclusion change, verify that the VPN is connected and that a resolved ChatGPT address still routes through the VPN interface. Use the native route lookup for the OS:

```bash
# Linux
chatgpt_ip=$(getent ahostsv4 chatgpt.com | awk 'NR==1 {print $1}')
ip route get "$chatgpt_ip"

# macOS
chatgpt_ip=$(dscacheutil -q host -a name chatgpt.com | awk '/ip_address/ {print $2; exit}')
route -n get "$chatgpt_ip"
```

Use `ss` to identify the active Kinescope connection and remote IP, but treat its local source address as suggestive only: policy routing or VPN interception can still send packets through TUN. To claim direct egress, capture the active Kinescope transfer on both the physical and TUN interfaces and confirm the video payload uses the physical path. For example, a privileged `tcpdump -i any` capture labels the interface for each matching packet. A few initial packets through TUN may be part of SNI inspection; the bulk transfer must use the physical interface. Verify ChatGPT's resolved address is routed through the VPN and, when packet capture is available, confirm the active ChatGPT request on the VPN interface.

```bash
# Linux
ip -brief address
ss -tnp state established
sudo timeout 15 tcpdump -n -i any 'host KINESCOPE_IP and tcp port 443'

# macOS: run captures separately using the physical and VPN interface names from `route -n get` and `ifconfig -l`.
sudo tcpdump -n -i PHYSICAL_INTERFACE 'host KINESCOPE_IP and tcp port 443'
sudo tcpdump -n -i VPN_INTERFACE 'host KINESCOPE_IP and tcp port 443'
```

Replace `KINESCOPE_IP` with the current resolved stream/CDN address and run the capture while downloading. `ip route get` alone cannot prove a domain/SNI exclusion. If privileged packet capture is unavailable, report direct routing as unverified; keep the VPN connected and do not claim that the download bypassed it. A global TCP/SNI inspection setting may restart the VPN; do not toggle it automatically. If it is required, explain the expected restart and obtain approval before changing it.

## Validate the deliverable

Confirm that yt-dlp finished merging and that the final MP4 exists. Use `ffprobe` to check the container, video resolution/frame rate, audio stream, duration, and file size. For a full integrity check, decode all streams:

```bash
ffmpeg -v error -i '/path/to/output.mp4' -map 0 -f null -
```

Do not call an HLS manifest, `.part`, format fragment, or incomplete decode a finished MP4. Report the chosen resolution, frame rate, codecs, duration, output path, and any routing limitation. Never include expiring signed URLs or account credentials in the report.

## Package research

For current findings on Kinescope-specific projects, general HLS alternatives, official API support, and why the default is yt-dlp, read [references/research.md](references/research.md). Recheck project activity and package versions before installing or recommending a package; popularity counts and releases change.
