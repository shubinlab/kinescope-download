import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def executable(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kinescope-single-installer-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state"
        self.log = self.root / "commands.log"
        package_manager = (
            "#!/bin/sh\nset -eu\n"
            "printf 'packages: %s\\n' \"$*\" >> \"$KINESCOPE_TEST_LOG\"\n"
            "for package in \"$@\"; do\n"
            "  case \"$package\" in\n"
            "    git) printf '#!/bin/sh\\nprintf \\\"git version 2.0\\\\n\\\"\\n' > \"$KINESCOPE_TEST_BIN/git\"; chmod +x \"$KINESCOPE_TEST_BIN/git\" ;;\n"
            "    yt-dlp) printf '#!/bin/sh\\nexit 0\\n' > \"$KINESCOPE_TEST_BIN/yt-dlp\"; chmod +x \"$KINESCOPE_TEST_BIN/yt-dlp\" ;;\n"
            "    ffmpeg) printf '#!/bin/sh\\nexit 0\\n' > \"$KINESCOPE_TEST_BIN/ffmpeg\"; chmod +x \"$KINESCOPE_TEST_BIN/ffmpeg\"; cp \"$KINESCOPE_TEST_BIN/ffmpeg\" \"$KINESCOPE_TEST_BIN/ffprobe\" ;;\n"
            "  esac\n"
            "done\n"
        )
        for manager in ("brew", "apt-get", "dnf", "pacman", "zypper", "omarchy"):
            executable(self.bin / manager, package_manager)
        executable(self.bin / "sudo", "#!/bin/sh\nexec \"$@\"\n")
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
            "HOME": str(self.root),
            "KINESCOPE_TEST_BIN": str(self.bin),
            "KINESCOPE_TEST_STATE": str(self.state),
            "KINESCOPE_TEST_LOG": str(self.log),
        })
        self.make_codex()

    def make_codex(self):
        executable(self.bin / "codex", r'''#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$KINESCOPE_TEST_LOG"
case "$*" in
  "plugin marketplace list --json")
    if [ "${KINESCOPE_TEST_MARKETPLACE_ERROR:-}" = 1 ]; then exit 8; fi
    if [ -f "$KINESCOPE_TEST_STATE.marketplace" ]; then
      if [ "${KINESCOPE_TEST_MARKETPLACE_WRONG:-}" = 1 ]; then
        printf '{"marketplaces":[{"name":"kinescope-download","marketplaceSource":{"source":"https://github.com/other/repo.git"}}]}\n'
      else
        printf '{"marketplaces":[{"name":"kinescope-download","marketplaceSource":{"source":"https://github.com/shubinlab/kinescope-download.git"}}]}\n'
      fi
    else
      printf '{"marketplaces":[]}\n'
    fi
    ;;
  "plugin list --json --marketplace kinescope-download")
    if [ "${KINESCOPE_TEST_CONFLICT:-}" = 1 ]; then
      printf '{"installed":[{"name":"kinescope-download","marketplaceName":"other"}],"available":[]}\n'
    elif [ -f "$KINESCOPE_TEST_STATE.plugin" ]; then
      printf '{"installed":[{"pluginId":"kinescope-download@kinescope-download"}],"available":[]}\n'
    elif [ "${KINESCOPE_TEST_AVAILABLE_ONLY:-}" = 1 ]; then
      printf '{"installed":[],"available":[{"pluginId":"kinescope-download@kinescope-download"}]}\n'
    else
      printf '{"installed":[],"available":[]}\n'
    fi
    ;;
  "plugin marketplace add shubinlab/kinescope-download")
    : > "$KINESCOPE_TEST_STATE.marketplace"
    ;;
  "plugin add kinescope-download@kinescope-download")
    : > "$KINESCOPE_TEST_STATE.plugin"
    ;;
  *) printf 'unexpected codex call: %s\n' "$*" >&2; exit 64 ;;
esac
''')

    def run_installer(self, *args, env=None):
        return subprocess.run(
            [str(ROOT / "install.sh"), *args], cwd=ROOT,
            env=env or self.env, text=True, capture_output=True, check=False,
        )

    def calls(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_help_needs_no_python_or_network(self):
        env = self.env.copy()
        env["PATH"] = ""
        result = self.run_installer("--help", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assertEqual(self.calls(), "")

    def test_no_client_defaults_to_installing_both_without_a_prompt(self):
        (self.bin / "codex").unlink()
        result = self.run_installer("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would install missing codex CLI", result.stdout)
        self.assertIn("Would install missing claude CLI", result.stdout)
        self.assertNotIn("No client CLI found", result.stdout)

    def test_bad_or_conflicting_flags_fail_before_mutation(self):
        for args in (("--unknown",), ("--codex", "--all")):
            with self.subTest(args=args):
                result = self.run_installer(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), "")

    def test_codex_marketplace_and_plugin_install_are_idempotent(self):
        first = self.run_installer("--codex")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_installer("--codex")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls().count("plugin marketplace add"), 1)
        self.assertEqual(self.calls().count("plugin add"), 1)

    def test_conflicting_plugin_source_stops_before_marketplace_changes(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_CONFLICT"] = "1"
        result = self.run_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another or unknown marketplace", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_marketplace_name_owned_by_another_repo_fails_closed(self):
        Path(f"{self.state}.marketplace").touch()
        env = self.env.copy()
        env["KINESCOPE_TEST_MARKETPLACE_WRONG"] = "1"
        result = self.run_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registered to a different source", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_marketplace_list_error_does_not_trigger_add_fallback(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_MARKETPLACE_ERROR"] = "1"
        result = self.run_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not inspect Codex marketplace state", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_available_codex_plugin_is_not_mistaken_for_installed(self):
        Path(f"{self.state}.marketplace").touch()
        env = self.env.copy()
        env["KINESCOPE_TEST_AVAILABLE_ONLY"] = "1"
        result = self.run_installer("--codex", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls().count("plugin add"), 1, result.stdout + "\n" + self.calls())

    def test_dry_run_reports_tools_and_does_not_touch_client_state(self):
        result = self.run_installer("--codex", "--with-deps", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dry run: no changes made", result.stdout)
        self.assertEqual(self.calls(), "")

    def test_missing_media_tools_are_installed_by_default_on_detected_omarchy(self):
        script_dir = self.root / "omarchy-checkout"
        script_dir.mkdir()
        release = self.root / "omarchy-os-release"
        release.write_text('ID=omarchy\nID_LIKE=arch\nPRETTY_NAME="Omarchy Test"\n', encoding="utf-8")
        source = (ROOT / "install.sh").read_text(encoding="utf-8").replace("/etc/os-release", f'"{release}"')
        executable(script_dir / "install.sh", source)
        executable(self.bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
        executable(self.bin / "omarchy", "#!/bin/sh\n"
            "set -eu\n"
            "printf '%s\\n' \"$*\" >> \"$KINESCOPE_TEST_LOG\"\n"
            "for tool in \"$@\"; do\n"
            "  case \"$tool\" in\n"
            "    yt-dlp|ffmpeg) printf '#!/bin/sh\\nexit 0\\n' > \"$KINESCOPE_TEST_BIN/$tool\"; chmod +x \"$KINESCOPE_TEST_BIN/$tool\" ;;\n"
            "  esac\n"
            "done\n"
            "if [ -f \"$KINESCOPE_TEST_BIN/ffmpeg\" ]; then cp \"$KINESCOPE_TEST_BIN/ffmpeg\" \"$KINESCOPE_TEST_BIN/ffprobe\"; fi\n")
        for tool in ("dirname", "id", "uname", "sed", "head", "tr", "grep", "wc", "cp", "chmod", "git"):
            path = shutil.which(tool)
            if path and not (self.bin / tool).exists():
                (self.bin / tool).symlink_to(path)
        env = self.env.copy()
        env["PATH"] = str(self.bin)
        result = subprocess.run(
            [str(script_dir / "install.sh"), "--codex"],
            cwd=script_dir, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Omarchy Test", result.stdout)
        self.assertIn("pkg add yt-dlp ffmpeg", self.calls())
        for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
            self.assertTrue((self.bin / tool).is_file(), tool)

    def test_missing_codex_uses_official_installer_before_plugin_install(self):
        codex_fixture = self.root / "codex-fixture"
        codex_fixture.write_text((self.bin / "codex").read_text(encoding="utf-8"), encoding="utf-8")
        (self.bin / "codex").unlink()
        executable(self.bin / "curl", f'''#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then destination=$2; shift 2; else shift; fi
done
printf '%s\\n' '#!/bin/sh' 'cp {shlex.quote(str(codex_fixture))} "$KINESCOPE_TEST_BIN/codex"' 'chmod +x "$KINESCOPE_TEST_BIN/codex"' > "$destination"
printf '%s' 'https://releases.openai.com/codex/install.sh'
''')
        result = self.run_installer("--codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installing codex with its official installer", result.stdout)
        self.assertIn("plugin marketplace add", self.calls(), result.stdout)
        self.assertIn("plugin add", self.calls(), result.stdout)

    def test_untrusted_client_installer_redirect_is_rejected_before_execution(self):
        (self.bin / "codex").unlink()
        sentinel = self.root / "executed"
        executable(self.bin / "curl", f'''#!/bin/sh
set -eu
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then destination=$2; shift 2; else shift; fi
done
printf '%s\\n' '#!/bin/sh' 'touch {shlex.quote(str(sentinel))}' > "$destination"
printf '%s' 'https://example.invalid/install.sh'
''')
        result = self.run_installer("--codex")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("untrusted host", result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.calls(), "")

    def test_linux_bootstrap_selects_package_manager_from_os_release(self):
        script_dir = self.root / "linux-checkout"
        script_dir.mkdir()
        release = self.root / "os-release"
        release.write_text('ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu Test"\nVERSION_ID="26.04"\n', encoding="utf-8")
        script = (ROOT / "install.sh").read_text(encoding="utf-8").replace("/etc/os-release", f'"{release}"')
        executable(script_dir / "install.sh", script)
        fake_git = self.bin / "git"
        executable(fake_git, "#!/bin/sh\nexit 1\n")
        executable(self.bin / "apt-get", "#!/bin/sh\nexit 0\n")
        executable(self.bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
        executable(self.bin / "sudo", "#!/bin/sh\nexit 99\n")
        env = self.env.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}/usr/bin:/bin"
        result = subprocess.run(
            [str(script_dir / "install.sh"), "--codex", "--dry-run"],
            cwd=script_dir, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ubuntu Test 26.04", result.stdout)
        self.assertIn("package manager: apt-get", result.stdout)
        self.assertIn("Would install packages: git", result.stdout)
        self.assertIn("git: missing", result.stdout)

    def test_known_immutable_linux_fails_before_package_manager_mutation(self):
        script_dir = self.root / "immutable-checkout"
        script_dir.mkdir()
        release = self.root / "immutable-os-release"
        release.write_text('ID=ublue-os\nID_LIKE=fedora\nPRETTY_NAME="Universal Blue"\n', encoding="utf-8")
        script = (ROOT / "install.sh").read_text(encoding="utf-8").replace("/etc/os-release", f'"{release}"')
        executable(script_dir / "install.sh", script)
        executable(self.bin / "git", "#!/bin/sh\nexit 1\n")
        executable(self.bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
        executable(self.bin / "dnf", "#!/bin/sh\nprintf 'dnf called\\n' >> \"$KINESCOPE_TEST_LOG\"\nexit 0\n")
        env = self.env.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}/usr/bin:/bin"
        result = subprocess.run(
            [str(script_dir / "install.sh"), "--dry-run"],
            cwd=script_dir, env=env, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("immutable", result.stderr)
        self.assertNotIn("dnf called", self.calls())


if __name__ == "__main__":
    unittest.main()
