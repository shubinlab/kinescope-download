# Authorized API original asset

Use this path only for an account owner who has legitimate workspace access and an API token already available in a local environment variable. Never ask the user to paste a token or place its value in command text, process arguments, logs, or history.

Use Kinescope's documented `GET /v1/videos/:video_id` API to locate the downloadable asset. Choose a `download_link` only when the API identifies one unambiguous original. Treat the returned link as a short-lived credential and pass it to yt-dlp through stdin with `--batch-file -`; do not expose it in command text, environment variables, process arguments, logs, or reports. Do not print the API response.

Inspect the completed file with `ffprobe`. Accept it as MP4 only when the container and streams are supported. If it is not MP4, try a stream-copy remux. If remuxing fails, use the authorized HLS route. Do not silently transcode or guess among ambiguous assets; report the available format and ask before a lossy conversion.
