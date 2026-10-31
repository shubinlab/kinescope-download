# Kinescope manifest 403 recovery

Use this when ordinary `yt-dlp -F` extraction fails with 403 or when the player is known to require an embedding-page referrer. The share URL and any referrer are credentials: enter them at hidden prompts in an interactive Bash terminal and pipe them through stdin; do not put literal URLs in command text, environment variables, process arguments, logs, or shell history. If no controlling terminal is available, stop instead of falling back to argv. The helper rejects positional URLs, accepts stdin URLs over HTTPS on Kinescope hosts, checks redirects, caps page reads at 8 MiB, refuses existing output paths before network access, and creates private mode-`0600` manifest and sanitized referrer files. It strips referrer query/fragment before using it and reports network errors without exception text that might contain a request URL.

Set `OUTPUT_DIR` to the destination directory. If the player requires an embedding-page referrer and you know it, enter it at the prompt; otherwise leave the prompt blank.

```bash
(
set +x
set -euo pipefail
manifest_dir=$(mktemp -d "${TMPDIR:-/tmp}/kinescope-manifest.XXXXXX")
manifest_file="$manifest_dir/manifest.txt"
referer_file="$manifest_dir/referer.txt"
referer_config="$manifest_dir/yt-dlp.conf"
trap 'rm -f -- "$manifest_file" "$referer_file" "$referer_config"; rmdir "$manifest_dir"' EXIT
chmod 700 "$manifest_dir"
(umask 077; : > "$referer_config")

# Replace the placeholder with the installed skill directory (the folder containing SKILL.md).
helper="<installed-skill-directory>/scripts/extract_manifest.py"
if ! (
  unset KINESCOPE_URL KINESCOPE_REFERRER REFERRER_URL safe_referrer
  IFS= read -r -s -p 'Kinescope share/embed URL: ' KINESCOPE_URL < /dev/tty || {
    printf 'A controlling terminal is required; stopping without exposing the URL.\n' >&2
    exit 1
  }
  printf '\n' >&2
  IFS= read -r -s -p 'Embedding page URL if required (Enter to skip): ' KINESCOPE_REFERRER < /dev/tty || {
    printf 'A controlling terminal is required; stopping without exposing the URL.\n' >&2
    exit 1
  }
  printf '\n' >&2
  printf '%s\n' "$KINESCOPE_URL"
  printf '%s\n' "$KINESCOPE_REFERRER"
) | env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer python3 "$helper" --url-stdin "$manifest_file" "$referer_file"; then
  exit 1
fi

if [[ -s $referer_file ]]; then
  unset safe_referrer
  safe_referrer=$(<"$referer_file")
  (
    umask 077
    printf '%s\n%s\n' '--referer' "$safe_referrer" > "$referer_config"
  )
  unset safe_referrer
fi

env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer yt-dlp --quiet --no-progress --no-playlist \
  --config-locations "$referer_config" \
  --format 'bestvideo+bestaudio/best' \
  --merge-output-format mp4 \
  --concurrent-fragments 16 \
  --no-overwrites \
  --print after_move:filepath \
  --output "$OUTPUT_DIR/%(title)s-%(id)s.%(ext)s" \
  --batch-file "$manifest_file"
)
```

Run this block in Bash. Do not use `set -x`, verbose output, or paste either URL into the conversation. Both URLs travel through stdin; the helper writes only the sanitized referrer to a private config file so neither is exposed in yt-dlp's argv. The block downloads directly with the dynamic best-video/audio selector and removes its temporary files when done. Network access still necessarily sends the share URL to Kinescope. If access is denied, the host is unexpected, or the recovered manifest still returns 403, stop and diagnose access/referrer/DRM requirements instead of trying decryption tools.
