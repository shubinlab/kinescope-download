# Kinescope manifest 403 recovery

Use this only after the share URL is authorized and ordinary `yt-dlp -F` extraction fails with 403. Treat a share URL containing a token or signed query as a credential. Enter it at a hidden prompt in an interactive Bash terminal and pipe it through stdin; do not put the literal URL in a command, environment variable, process argument, log, or shell history. If no controlling terminal is available, stop instead of falling back to argv. The helper rejects positional URLs containing a query or fragment, accepts stdin URLs over HTTPS on Kinescope hosts, checks redirect destinations, caps page reads at 8 MiB, rejects existing output paths before network access, and exclusively creates one manifest file with mode `0600` without printing the signed URL. Network errors are reported without echoing exception text, which can contain the request URL. When sending a supplied referrer, it keeps only the origin and path; it removes the query and fragment.

Set `OUTPUT_DIR` to the destination directory. If the stream is embedded elsewhere, set `REFERRER_URL` to that actual embedding page; otherwise leave it empty.

```bash
manifest_dir=$(mktemp -d "${TMPDIR:-/tmp}/kinescope-manifest.XXXXXX")
chmod 700 "$manifest_dir"
manifest_file="$manifest_dir/manifest.txt"
trap 'rm -f -- "$manifest_file"; rmdir "$manifest_dir"' EXIT
referer_args=()
if [[ -n ${REFERRER_URL:-} ]]; then
  referer_args=(--referer "$REFERRER_URL")
fi

# Replace the placeholder with the installed skill directory (the folder containing SKILL.md).
helper="<installed-skill-directory>/scripts/extract_manifest.py"
if ! (
  unset KINESCOPE_URL
  IFS= read -r -s -p 'Kinescope share/embed URL: ' KINESCOPE_URL < /dev/tty || {
    printf 'A controlling terminal is required; stopping without exposing the URL.\n' >&2
    exit 1
  }
  printf '\n' >&2
  printf '%s\n' "$KINESCOPE_URL"
) | env -u KINESCOPE_URL python3 "$helper" --url-stdin "$manifest_file" "${REFERRER_URL:-}"; then
  exit 1
fi

yt-dlp --quiet --no-playlist -F --batch-file "$manifest_file" \
  "${referer_args[@]}"
```

Review the format table without copying the signed URL. Download with the same `--batch-file` while the temporary file still exists:

```bash
yt-dlp --quiet --no-playlist \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --output "$OUTPUT_DIR/%(title)s-%(id)s.%(ext)s" \
  --batch-file "$manifest_file" \
  "${referer_args[@]}"
```

Run this block in Bash. Do not use `set -x`, verbose output, or paste either URL into the conversation. The stdin mode keeps the share URL out of argv and command history; network access still necessarily sends it to Kinescope. If page access is denied, multiple manifests are found, the host is unexpected, or the corrected manifest still returns 403, stop and diagnose access/referrer/DRM requirements instead of trying decryption tools.
