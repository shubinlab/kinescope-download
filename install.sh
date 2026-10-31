#!/bin/sh
set -eu

PLUGIN_SOURCE=https://codeload.github.com/shubinlab/kinescope-download/tar.gz/refs/tags/v1.4.0
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)

fail() {
  printf 'Install failed: %s\n' "$1" >&2
  exit 1
}

case " ${*:-} " in
  *" --help "*|*" -h "*)
    cat <<'HELP'
Usage: install.sh [--codex|--claude|--all] [--with-deps] [--dry-run]

Bootstraps Git and Python 3 when missing, installs a selected client CLI when
needed, then installs the Kinescope plugin. Run as your normal user.
HELP
    exit 0
    ;;
esac

validate_args() {
  selection=
  for argument in "$@"; do
    case "$argument" in
      --codex|--claude|--all)
        [ -z "$selection" ] || fail 'choose only one of --codex, --claude, or --all.'
        selection=$argument
        ;;
      --with-deps|--dry-run) ;;
      *) fail "unknown option: $argument" ;;
    esac
  done
}

validate_args "$@"

if [ "$(id -u)" -eq 0 ]; then
  fail 'run as your normal user, not with sudo; the package manager will elevate only when required.'
fi

download() {
  url=$1
  destination=$2
  if command -v curl >/dev/null 2>&1; then
    final_url=$(curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' --tlsv1.2 --max-redirs 0 --write-out '%{url_effective}' "$url" --output "$destination")
    case "$final_url" in https://codeload.github.com/*) : ;; *) fail 'source archive download finished outside the trusted GitHub archive host.' ;; esac
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --max-redirect=0 "$url" --output-document="$destination"
  else
    fail 'curl or wget is required to fetch the installer archive.'
  fi
}

# A copy fetched from GitHub can bootstrap the full source archive without Git.
if [ ! -f "$SCRIPT_DIR/scripts/install.py" ]; then
  command -v tar >/dev/null 2>&1 || fail 'tar is required to unpack the installer archive.'
  temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/kinescope-install.XXXXXX") || fail 'could not create a temporary directory.'
  trap 'rm -rf "$temporary_root"' EXIT HUP INT TERM
  archive="$temporary_root/source.tar.gz"
  download "$PLUGIN_SOURCE" "$archive" || fail 'could not download the GitHub source archive.'
  tar -xzf "$archive" -C "$temporary_root" || fail 'could not unpack the GitHub source archive.'
  source_dir=
  for candidate in "$temporary_root"/kinescope-download-*; do
    if [ -f "$candidate/scripts/install.py" ]; then
      source_dir=$candidate
      break
    fi
  done
  [ -n "$source_dir" ] || fail 'the downloaded archive did not contain the installer.'
  sh "$source_dir/install.sh" "$@"
  exit $?
fi

has_python3() {
  command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info.major != 3)' >/dev/null 2>&1
}

has_git() {
  command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1
}

read_linux_release() {
  linux_id=
  linux_like=
  linux_variant=
  if [ -r /etc/os-release ]; then
    linux_id=$(sed -n 's/^ID=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
    linux_like=$(sed -n 's/^ID_LIKE=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
    linux_variant=$(sed -n 's/^VARIANT_ID=//p' /etc/os-release | head -n 1 | tr -d '"' | tr '[:upper:]' '[:lower:]')
  fi
}

is_immutable_linux() {
  case "${linux_id:-}" in
    opensuse-microos|opensuse-aeon|opensuse-kalpa|nixos|steamos|bazzite|aurora|bluefin|ublue|ublue-os|ucore|fedora-silverblue|fedora-kinoite|fedora-sericea|fedora-onyx|fedora-coreos|fedora-iot) return 0 ;;
  esac
  case "${linux_variant:-}" in
    silverblue|kinoite|sericea|onyx|coreos|iot|microos|microos-desktop|aeon|kalpa) return 0 ;;
  esac
  return 1
}

linux_manager() {
  read_linux_release
  case "$linux_id" in
    omarchy) command -v omarchy >/dev/null 2>&1 && { printf omarchy; return; } ;;
    debian|ubuntu|linuxmint|pop|elementary) command -v apt-get >/dev/null 2>&1 && { printf apt-get; return; } ;;
    fedora|rhel|centos|rocky|almalinux|ol) command -v dnf >/dev/null 2>&1 && { printf dnf; return; } ;;
    arch|manjaro|endeavouros|garuda|artix) command -v pacman >/dev/null 2>&1 && { printf pacman; return; } ;;
    opensuse|opensuse-leap|opensuse-tumbleweed|suse|sles|sled) command -v zypper >/dev/null 2>&1 && { printf zypper; return; } ;;
  esac
  for family in $linux_like; do
    case "$family" in
      debian|ubuntu) command -v apt-get >/dev/null 2>&1 && { printf apt-get; return; } ;;
      fedora|rhel) command -v dnf >/dev/null 2>&1 && { printf dnf; return; } ;;
      arch) command -v pacman >/dev/null 2>&1 && { printf pacman; return; } ;;
      suse) command -v zypper >/dev/null 2>&1 && { printf zypper; return; } ;;
    esac
  done
  available=
  for candidate in apt-get dnf pacman zypper; do
    if command -v "$candidate" >/dev/null 2>&1; then
      available="$available $candidate"
    fi
  done
  set -- $available
  [ "$#" -eq 1 ] && { printf '%s' "$1"; return; }
  return 1
}

report_bootstrap_dry_run() {
  system=$(uname -s)
  need_git=0
  need_python=0
  has_git || need_git=1
  has_python3 || need_python=1
  if [ "$system" = Darwin ]; then
    if ! command -v brew >/dev/null 2>&1; then
      printf '%s\n' 'Homebrew is missing; the installer cannot bootstrap Git or Python 3 on macOS.'
    elif [ "$need_git" -eq 1 ] && [ "$need_python" -eq 1 ]; then
      printf '%s\n' 'Would run: brew install git python'
    elif [ "$need_git" -eq 1 ]; then
      printf '%s\n' 'Would run: brew install git'
    else
      printf '%s\n' 'Would run: brew install python'
    fi
  elif [ "$system" = Linux ]; then
    read_linux_release
    if is_immutable_linux; then
      printf 'Bootstrap stopped: %s is an immutable Linux system.\n' "${linux_id:-Linux}"
    else
      manager=$(linux_manager) || {
        printf '%s\n' 'No unambiguous supported Linux package manager was detected.'
        return
      }
      packages=
      [ "$need_git" -eq 1 ] && packages=git
      if [ "$need_python" -eq 1 ]; then
        if [ "$manager" = pacman ] || [ "$manager" = omarchy ]; then
          packages=${packages:+"$packages "}python
        else
          packages=${packages:+"$packages "}python3
        fi
      fi
      case "$manager" in
        omarchy) printf 'Would run: omarchy pkg add %s\n' "$packages" ;;
        apt-get) printf 'Would run: sudo apt-get update && sudo apt-get install -y %s\n' "$packages" ;;
        dnf) printf 'Would run: sudo dnf install -y %s\n' "$packages" ;;
        pacman) printf 'Would run: sudo pacman -S --needed --noconfirm %s\n' "$packages" ;;
        zypper) printf 'Would run: sudo zypper --non-interactive install %s\n' "$packages" ;;
      esac
    fi
  else
    printf 'Bootstrap package installation is unsupported on %s.\n' "$system"
  fi
  printf '%s\n' 'No bootstrap packages were installed by this dry run.'
}

install_bootstrap_tools() {
  system=$(uname -s)
  need_git=0
  need_python=0
  has_git || need_git=1
  has_python3 || need_python=1
  [ "$need_git" -eq 1 ] || [ "$need_python" -eq 1 ] || return 0
  if [ "$system" = Darwin ]; then
    command -v brew >/dev/null 2>&1 || fail 'Git or Python 3 is missing. Install Homebrew, then rerun install.sh.'
    printf '%s\n' 'Installing missing bootstrap tools with Homebrew…'
    if [ "$need_git" -eq 1 ] && [ "$need_python" -eq 1 ]; then brew install git python
    elif [ "$need_git" -eq 1 ]; then brew install git
    else brew install python
    fi
    return
  fi
  [ "$system" = Linux ] || fail "Bootstrap package installation is unsupported on $system."
  read_linux_release
  is_immutable_linux && fail "${linux_id:-Linux} is immutable; install Python 3 using the distribution's documented layering workflow."
  manager=$(linux_manager) || fail 'could not select one supported Linux package manager from the distribution ID.'
  printf 'Installing missing bootstrap tools with %s.\n' "$manager"
  set --
  [ "$need_git" -eq 1 ] && set -- "$@" git
  case "$manager" in
    omarchy) [ "$need_python" -eq 1 ] && set -- "$@" python; omarchy pkg add "$@" ;;
    apt-get)
      command -v sudo >/dev/null 2>&1 || fail 'sudo is required to install bootstrap tools.'
      [ "$need_python" -eq 1 ] && set -- "$@" python3
      sudo apt-get update && sudo apt-get install -y "$@"
      ;;
    dnf)
      command -v sudo >/dev/null 2>&1 || fail 'sudo is required to install bootstrap tools.'
      [ "$need_python" -eq 1 ] && set -- "$@" python3
      sudo dnf install -y "$@"
      ;;
    pacman)
      command -v sudo >/dev/null 2>&1 || fail 'sudo is required to install bootstrap tools.'
      [ "$need_python" -eq 1 ] && set -- "$@" python
      sudo pacman -S --needed --noconfirm "$@"
      ;;
    zypper)
      command -v sudo >/dev/null 2>&1 || fail 'sudo is required to install bootstrap tools.'
      [ "$need_python" -eq 1 ] && set -- "$@" python3
      sudo zypper --non-interactive install "$@"
      ;;
  esac
  hash -r 2>/dev/null || true
  has_python3 && has_git || fail 'Git and Python 3 are still required for the plugin marketplace and installer.'
}

if ! has_python3 || ! has_git; then
  case " $* " in
    *" --dry-run "*)
      report_bootstrap_dry_run
      if ! has_python3; then exit 0; fi
      ;;
    *) install_bootstrap_tools ;;
  esac
fi

exec python3 "$SCRIPT_DIR/scripts/install.py" "$@"
