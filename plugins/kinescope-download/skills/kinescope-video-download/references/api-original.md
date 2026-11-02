# Authorized API original asset

Use this path only for an account owner with a local Kinescope API token and legitimate workspace access. Never ask the user to paste a token or put its value in command text, process arguments, logs, or history.

Use Kinescope's documented `GET /v1/videos/:video_id` API. Choose `download_link` only when the response identifies one unambiguous original. Treat the returned link as a short-lived credential; keep the API response private and pass the link to yt-dlp through stdin with `--batch-file -`.

Before downloading, validate the link against the exact Kinescope host allowlist in `browser_cookies.py`. Read browser cookies for that request host and write a host-only cookie file (mode `0600`) inside the private temporary directory. Pass it to yt-dlp with `--cookies`; do not send the full browser jar to a signed link on an unlisted host.

Check the completed file with `ffprobe`. Accept MP4 only when the container and streams are supported. If the original is not MP4, try a stream-copy remux. If remuxing fails, use the authorized HLS route. Do not silently transcode or choose among ambiguous assets; ask before a lossy conversion.
