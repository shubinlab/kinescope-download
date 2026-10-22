# Kinescope manifest 403 recovery

Use this only after the share URL is authorized and ordinary `yt-dlp -F` extraction fails with 403. The bundled helper accepts HTTPS Kinescope URLs, checks redirect destinations, caps page reads at 8 MiB, decodes common HTML/JavaScript escaping, and exclusively creates one manifest file with mode `0600` without printing the signed URL.

Set `KINESCOPE_URL` to the supplied Kinescope share/embed URL and `OUTPUT_DIR` to the destination directory. If the stream is embedded elsewhere, set `REFERRER_URL` to that actual embedding page; otherwise leave it empty.

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
python3 "$helper" "$KINESCOPE_URL" "$manifest_file" "${REFERRER_URL:-}"

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

Run this block in Bash. Do not use `set -x`, verbose output, or paste the manifest URL into the conversation. If page access is denied, multiple manifests are found, the host is unexpected, or the corrected manifest still returns 403, stop and diagnose access/referrer/DRM requirements instead of trying decryption tools.
