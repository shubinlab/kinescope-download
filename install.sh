#!/bin/sh
set -eu

PLUGIN=kinescope-download
MARKETPLACE=kinescope-download
SOURCE=shubinlab/kinescope-download
MEDIA_TOOLS="yt-dlp ffmpeg ffprobe"
temporary_installer=
trap '[ -z "$temporary_installer" ] || rm -f "$temporary_installer"' EXIT HUP INT TERM

fail() {
  printf 'Install failed: %s\n' "$1" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    'Usage: install.sh [--codex|--claude|--all] [--dry-run]' \
    '' \
    'Checks and installs missing prerequisites, a selected Codex/Claude CLI, and' \
    'the Kinescope plugin. Sign-in stays with you. Run as your normal user.'
}

selection=
dry_run=0
for argument in "$@"; do
  case "$argument" in
    --help|-h) usage; exit 0 ;;
    --codex|--claude|--all)
      [ -z "$selection" ] || fail 'choose only one of --codex, --claude, or --all.'
      selection=$argument
      ;;
    --with-deps) : ;; # kept as a compatibility flag; missing tools install by default
    --dry-run) dry_run=1 ;;
    *) fail "unknown option: $argument" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || fail 'run as your normal user, not with sudo.'

system=$(uname -s)
os_id=
os_like=
os_variant=
os_name=$system
os_version=
if [ "$system" = Linux ] && [ -r /etc/os-release ]; then
  os_id=$(sed -n 's/^ID=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  os_like=$(sed -n 's/^ID_LIKE=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  os_variant=$(sed -n 's/^VARIANT_ID=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  os_name=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n 1 | tr -d '"')
  os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | head -n 1 | tr -d '"')
elif [ "$system" = Darwin ]; then
  os_name="macOS $(sw_vers -productVersion 2>/dev/null || printf unknown)"
fi
architecture=$(uname -m)

is_immutable_linux() {
  case "$os_id" in
    opensuse-microos|opensuse-aeon|opensuse-kalpa|nixos|steamos|bazzite|aurora|bluefin|ublue|ublue-os|ucore|fedora-silverblue|fedora-kinoite|fedora-sericea|fedora-onyx|fedora-coreos|fedora-iot) return 0 ;;
  esac
  case "$os_variant" in silverblue|kinoite|sericea|onyx|coreos|iot|microos|microos-desktop|aeon|kalpa) return 0 ;; esac
  return 1
}

manager=
immutable=0
linux_manager() {
  case "$os_id" in
    omarchy) command -v omarchy >/dev/null 2>&1 && manager=omarchy ;;
    debian|ubuntu|linuxmint|pop|elementary) manager=apt-get ;;
    fedora|rhel|centos|rocky|almalinux|ol) manager=dnf ;;
    arch|manjaro|endeavouros|garuda|artix) manager=pacman ;;
    opensuse|opensuse-leap|opensuse-tumbleweed|suse|sles|sled) manager=zypper ;;
  esac
  if [ -z "$manager" ]; then
    for family in $os_like; do
      case "$family" in
        debian|ubuntu) manager=apt-get; break ;;
        fedora|rhel) manager=dnf; break ;;
        arch) manager=pacman; break ;;
        suse) manager=zypper; break ;;
      esac
    done
  fi
  if [ -z "$manager" ]; then
    found=
    for candidate in apt-get dnf pacman zypper; do
      if command -v "$candidate" >/dev/null 2>&1; then
        [ -z "$found" ] || return 1
        found=$candidate
      fi
    done
    manager=$found
  fi
  [ -n "$manager" ] && command -v "$manager" >/dev/null 2>&1
}

have_git() { command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; }
have_tool() { command -v "$1" >/dev/null 2>&1; }

install_packages() {
  packages=$*
  [ -n "$packages" ] || return 0
  [ "$dry_run" -eq 0 ] || { printf 'Would install packages: %s\n' "$packages"; return 0; }
  case "$manager" in
    omarchy) omarchy pkg add $packages ;;
    apt-get) sudo apt-get update && sudo apt-get install -y $packages ;;
    dnf) sudo dnf install -y $packages ;;
    pacman) sudo pacman -S --needed --noconfirm $packages ;;
    zypper) sudo zypper --non-interactive install $packages ;;
    brew) brew install $packages ;;
    *) fail 'no supported package manager is available for this system.' ;;
  esac
}

if [ "$system" = Darwin ]; then
  manager=
  have_tool brew && manager=brew
elif [ "$system" = Linux ]; then
  is_immutable_linux && immutable=1
  linux_manager || manager=
else
  fail "unsupported operating system: $system"
fi

printf 'Platform: %s %s (%s); package manager: %s\n' "$os_name" "$os_version" "$architecture" "${manager:-not installed}"

if ! have_git; then
  [ "$immutable" -eq 0 ] || fail "$os_name is immutable; install Git using its documented layering or toolbox workflow."
  [ -n "$manager" ] || fail 'Git is missing. Install Homebrew or Git, then rerun this script.'
  if [ "$manager" = pacman ] || [ "$manager" = omarchy ]; then git_package=git; else git_package=git; fi
  if [ "$manager" != brew ] && [ "$manager" != omarchy ] && ! have_tool sudo; then
    fail 'sudo is required to install Git.'
  fi
  install_packages "$git_package"
fi

clients=
case "$selection" in
  --codex) clients=codex ;;
  --claude) clients=claude ;;
  --all) clients='codex claude' ;;
  *)
    for candidate in codex claude; do
      have_tool "$candidate" && clients="$clients $candidate"
    done
    [ -n "$clients" ] || clients='codex claude'
    ;;
esac

install_client_cli() {
  client=$1
  have_tool "$client" && return 0
  if [ "$dry_run" -eq 1 ]; then
    printf 'Would install missing %s CLI with its official installer.\n' "$client"
    return 0
  fi
  printf 'Installing %s with its official installer…\n' "$client"
  have_tool curl || fail 'curl is required to install a missing client CLI.'
  installer=$(mktemp "${TMPDIR:-/tmp}/kinescope-client.XXXXXX") || fail 'could not create temporary installer file.'
  temporary_installer=$installer
  case "$client" in
    codex) installer_url=https://chatgpt.com/codex/install.sh; allowed_host=releases.openai.com ;;
    claude) installer_url=https://claude.ai/install.sh; allowed_host=downloads.claude.ai ;;
  esac
  final_url=$(curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' --tlsv1.2 --max-filesize 4194304 --write-out '%{url_effective}' "$installer_url" --output "$installer") || fail "could not download the official $client installer."
  case "$final_url" in "https://$allowed_host/"*|"https://$allowed_host") : ;; *) fail "the $client installer redirected to an untrusted host." ;; esac
  [ "$(wc -c < "$installer" | tr -d ' ')" -le 4194304 ] || fail "the $client installer is unexpectedly large."
  head -n 1 "$installer" | grep -q '^#!' || fail "the official $client installer is not a shell script."
  case "$client" in codex) CODEX_NON_INTERACTIVE=true sh "$installer" ;; claude) bash "$installer" ;; esac
  rm -f "$installer"
  temporary_installer=
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  have_tool "$client" || fail "$client installed, but is not on PATH; restart the terminal and rerun."
}

marketplace_present() {
  client=$1
  if [ "$client" = codex ]; then
    output=$(codex plugin marketplace list --json 2>/dev/null) || fail 'could not inspect Codex marketplace state.'
    printf '%s\n' "$output" | grep -Eq '"marketplaces"[[:space:]]*:' || fail 'Codex returned an unexpected marketplace-list format.'
  else
    output=$(claude plugin marketplace list --json 2>/dev/null) || fail 'could not inspect Claude marketplace state.'
    printf '%s\n' "$output" | grep -Eq '^[[:space:]]*\[' || fail 'Claude returned an unexpected marketplace-list format.'
  fi
  if printf '%s\n' "$output" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"kinescope-download"'; then
    if [ "$client" = codex ]; then
      printf '%s\n' "$output" | grep -Fq 'https://github.com/shubinlab/kinescope-download.git' || fail 'Codex marketplace name kinescope-download is registered to a different source.'
    else
      printf '%s\n' "$output" | grep -Eq '"repo"[[:space:]]*:[[:space:]]*"shubinlab/kinescope-download"' || fail 'Claude marketplace name kinescope-download is registered to a different source.'
    fi
    return 0
  fi
  if printf '%s\n' "$output" | grep -Fq 'shubinlab/kinescope-download'; then
    fail "$client has the Kinescope repository registered under an unexpected marketplace name."
  fi
  return 1
}

plugin_present() {
  client=$1
  if [ "$client" = codex ]; then
    output=$(codex plugin list --json --marketplace "$MARKETPLACE" 2>/dev/null) || fail 'could not inspect Codex plugin state.'
    installed=$(printf '%s\n' "$output" | sed -n 's/.*"installed"[[:space:]]*:[[:space:]]*\(.*\),[[:space:]]*"available"[[:space:]]*:.*/\1/p')
    [ -n "$installed" ] || installed=$(printf '%s\n' "$output" | sed -n '/"installed"[[:space:]]*:/,/^[[:space:]]*"available"[[:space:]]*:/p')
    [ -n "$installed" ] || fail 'Codex returned an unexpected plugin-list format.'
  else
    output=$(claude plugin list --json 2>/dev/null) || fail 'could not inspect Claude plugin state.'
    printf '%s\n' "$output" | grep -Eq '^[[:space:]]*\[' || fail 'Claude returned an unexpected plugin-list format.'
    installed=$output
  fi
  if printf '%s\n' "$installed" | grep -Fq 'kinescope-download@kinescope-download'; then
    return 0
  fi
  if printf '%s\n' "$installed" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"kinescope-download"'; then
    fail "$client already has kinescope-download from another or unknown marketplace."
  fi
  return 1
}

for client in $clients; do install_client_cli "$client"; done

missing_tools=
for tool in $MEDIA_TOOLS; do
  have_tool "$tool" || missing_tools="$missing_tools $tool"
done
media_packages=
if [ -n "$missing_tools" ]; then
  [ "$immutable" -eq 0 ] || fail "$os_name is immutable; install media tools using its documented layering or toolbox workflow."
  [ -n "$manager" ] || fail 'no supported package manager is available to install missing media tools.'
  if [ "$manager" != brew ] && [ "$manager" != omarchy ] && ! have_tool sudo; then fail 'sudo is required to install media tools.'; fi
  case "$missing_tools" in *yt-dlp*) media_packages="$media_packages yt-dlp" ;; esac
  case "$missing_tools" in *ffmpeg*|*ffprobe*) media_packages="$media_packages ffmpeg" ;; esac
fi

if [ "$dry_run" -eq 1 ]; then
  for tool in git codex claude $MEDIA_TOOLS; do
    if [ "$tool" = git ]; then have_git && state=present || state=missing
    else have_tool "$tool" && state=present || state=missing
    fi
    printf '  %s: %s\n' "$tool" "$state"
  done
  for client in $clients; do printf '%s: would inspect/register marketplace and install plugin if missing.\n' "$client"; done
  printf '%s\n' 'Dry run: no changes made.'
  exit 0
fi

codex_state=skip
claude_state=skip
for client in $clients; do
  if plugin_present "$client"; then plugin_state=installed; else plugin_state=absent; fi
  case "$client" in codex) codex_state=$plugin_state ;; claude) claude_state=$plugin_state ;; esac
done

if [ -n "$media_packages" ]; then
  install_packages "$media_packages"
  if [ "$dry_run" -eq 0 ]; then
    for tool in $MEDIA_TOOLS; do have_tool "$tool" || fail "$tool is still missing after package installation."; done
  fi
fi

for client in $clients; do
  if ! marketplace_present "$client"; then
    printf '%s: adding plugin marketplace…\n' "$client"
    if [ "$client" = codex ]; then codex plugin marketplace add "$SOURCE"
    else claude plugin marketplace add "$SOURCE" --scope user
    fi
  fi
  case "$client" in codex) plugin_state=$codex_state ;; claude) plugin_state=$claude_state ;; esac
  if [ "$plugin_state" = installed ]; then
    printf '%s: plugin already installed.\n' "$client"
  else
    printf '%s: installing plugin…\n' "$client"
    if [ "$client" = codex ]; then codex plugin add "$PLUGIN@$MARKETPLACE"
    else claude plugin install "$PLUGIN@$MARKETPLACE" --scope user
    fi
    plugin_present "$client" || fail "$client did not confirm plugin installation."
  fi
done
