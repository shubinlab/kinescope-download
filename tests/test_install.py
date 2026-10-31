import os
import platform
import shutil
import shlex
import subprocess
import tempfile
import tarfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kinescope installer ")
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.state = self.base / "state"
        self.log = self.base / "calls.log"
        self._install_stubs()
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "KINESCOPE_TEST_STATE": str(self.state),
            "KINESCOPE_TEST_LOG": str(self.log),
            "KINESCOPE_TEST_BIN": str(self.bin),
        })

    def tearDown(self):
        self.temp.cleanup()

    def _install_stubs(self):
        client = r'''#!/bin/sh
set -eu
name="__CLIENT__"
if [ "${1:-}" = "--version" ]; then
  printf '%s\n' "$name 1.2.3"
  exit 0
fi
printf '%s' "$name" >> "$KINESCOPE_TEST_LOG"
for arg in "$@"; do printf ' %s' "$arg" >> "$KINESCOPE_TEST_LOG"; done
printf '\n' >> "$KINESCOPE_TEST_LOG"
state="$KINESCOPE_TEST_STATE.$name"
case "$name:$*" in
  *"marketplace list"*"--json"*)
    if [ "${KINESCOPE_TEST_BAD_SHAPE:-}" = "$name:marketplace" ]; then
      printf 'null\n'
    elif [ "${KINESCOPE_TEST_UNIDENTIFIABLE:-}" = "$name:marketplace" ]; then
      printf '[{"unexpected":"value"}]\n'
    elif [ "${KINESCOPE_TEST_BAD_JSON:-}" = "$name" ]; then
      printf 'not-json\\n'
    elif [ -f "$state.marketplace" ]; then
      printf '{"marketplaces":[{"name":"kinescope-download"}]}\n'
    elif [ "$name" = codex ]; then
      printf '{"marketplaces":[]}\n'
    else
      printf '[]\n'
    fi
    ;;
  codex:*"plugin list"*"--json"*|claude:*"plugin list"*"--json"*)
    if [ "${KINESCOPE_TEST_BAD_SHAPE:-}" = "$name:plugin" ]; then
      printf '"error"\n'
    elif [ "${KINESCOPE_TEST_UNIDENTIFIABLE:-}" = "$name:plugin" ]; then
      printf '[{"unexpected":"value"}]\n'
    elif [ -f "$state.plugin.correct" ]; then
      printf '[{"name":"kinescope-download","marketplace":"kinescope-download"}]\n'
    elif [ -f "$state.plugin.wrong" ]; then
      printf '[{"name":"kinescope-download","marketplace":"another-marketplace"}]\n'
    elif [ -f "$state.plugin" ]; then
      printf '[{"name":"kinescope-download","marketplace":"kinescope-download"}]\n'
    else
      printf '[]\n'
    fi
    ;;
  *"marketplace add"*)
    if [ "${KINESCOPE_TEST_FAIL_ADD:-}" = "$name" ]; then exit 7; fi
    : > "$state.marketplace"
    ;;
  codex:*"plugin add"*|claude:*"plugin install"*)
    if [ "${KINESCOPE_TEST_FAIL_INSTALL:-}" = "$name" ]; then exit 9; fi
    : > "$state.plugin"
    : > "$state.plugin.correct"
    ;;
  *) exit 64 ;;
esac
'''
        for name in ("codex", "claude"):
            write_executable(self.bin / name, client.replace("__CLIENT__", name))
        package_manager = (
            '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$KINESCOPE_TEST_LOG"\n'
            'for tool in "$@"; do\n'
            '  case "$tool" in\n'
            '    yt-dlp) printf "#!/bin/sh\\nexit 0\\n" > "$KINESCOPE_TEST_BIN/yt-dlp"; /bin/chmod 755 "$KINESCOPE_TEST_BIN/yt-dlp" ;;\n'
            '    ffmpeg) for binary in ffmpeg ffprobe; do printf "#!/bin/sh\\nexit 0\\n" > "$KINESCOPE_TEST_BIN/$binary"; /bin/chmod 755 "$KINESCOPE_TEST_BIN/$binary"; done ;;\n'
            '  esac\n'
            'done\n'
        )
        for name in ("apt-get", "dnf", "pacman", "zypper", "omarchy", "brew"):
            write_executable(self.bin / name, package_manager)
        write_executable(
            self.bin / "sudo",
            '#!/bin/sh\nprintf "sudo %s\\n" "$*" >> "$KINESCOPE_TEST_LOG"\nexec "$@"\n',
        )

    def run_installer(self, *args, cwd=None, env=None, installer_root=ROOT):
        installer = installer_root / "install.sh"
        self.assertTrue(installer.is_file(), "missing install.sh entrypoint")
        return subprocess.run(
            [str(installer), *args], cwd=cwd or ROOT, env=env or self.env,
            text=True, capture_output=True, check=False,
        )

    def run_python_installer(self, *args, env=None):
        installer = ROOT / "scripts" / "install.py"
        self.assertTrue(installer.is_file(), "missing Python installer")
        return subprocess.run(
            [os.sys.executable, str(installer), *args], cwd=ROOT,
            env=env or self.env, text=True, capture_output=True, check=False,
        )

    def calls(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_help_and_unknown_flags(self):
        help_result = self.run_installer("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        bad_result = self.run_installer("--unknown")
        self.assertNotEqual(bad_result.returncode, 0)
        self.assertEqual(self.calls(), "")

    def test_installer_reports_detected_os_package_manager_and_tool_versions(self):
        result = self.run_installer("--codex", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Platform:", result.stdout)
        self.assertIn("package manager:", result.stdout)
        self.assertIn("codex: 1.2.3", result.stdout)
        self.assertIn("python3:", result.stdout)
        self.assertIn("yt-dlp:", result.stdout)

    def test_no_client_cli_prompts_for_an_explicit_selection(self):
        from scripts import install

        args = install.parse_args([])
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with patch.object(install.shutil, "which", return_value=None):
            with patch.object(install.sys, "stdin", fake_stdin):
                with patch("builtins.input", return_value="2"):
                    with redirect_stdout(StringIO()):
                        self.assertEqual(install.selected_clients(args), ["claude"])

    def test_noninteractive_run_without_a_client_requires_an_explicit_flag(self):
        from scripts import install

        args = install.parse_args([])
        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = False
        with patch.object(install.shutil, "which", return_value=None):
            with patch.object(install.sys, "stdin", fake_stdin):
                with self.assertRaisesRegex(install.InstallError, "--codex, --claude, or --all"):
                    install.selected_clients(args)

    def test_selected_missing_cli_uses_bounded_https_official_installer(self):
        from scripts import install

        home = self.base / "isolated-home"
        local_bin = home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        payload = b"#!/bin/sh\nexit 0\n"
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload
        response.geturl.return_value = "https://chatgpt.com/codex/install.sh"

        def run_installer(command, **kwargs):
            if command[1].endswith("install.sh"):
                write_executable(local_bin / "codex", "#!/bin/sh\nprintf 'codex 2.0.0\\n'\n")
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "codex 2.0.0\n", "")

        previous_path = os.environ.get("PATH", "")
        with patch.dict(os.environ, {"PATH": previous_path}):
            with patch.object(install.Path, "home", return_value=home):
                with patch.object(install, "urlopen", return_value=response) as mocked_urlopen:
                    with patch.object(install.subprocess, "run", side_effect=run_installer):
                        with redirect_stdout(StringIO()):
                            install.install_client_cli("codex", False)
                    self.assertIn(str(local_bin), os.environ["PATH"])

        mocked_urlopen.assert_called_once_with(install.CLIENT_INSTALLERS["codex"][0], timeout=30)

    def test_selected_missing_cli_rejects_untrusted_https_redirect_host(self):
        from scripts import install

        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"#!/bin/sh\nexit 0\n"
        for final_url in ("https://example.invalid/install.sh", "http://chatgpt.com/install.sh"):
            with self.subTest(final_url=final_url):
                response.geturl.return_value = final_url
                with patch.object(install, "urlopen", return_value=response):
                    with patch.object(install.subprocess, "run") as mocked_run:
                        with self.assertRaisesRegex(install.InstallError, "trusted HTTPS hosts"):
                            install.install_client_cli("codex", False)
                mocked_run.assert_not_called()
                response.read.assert_not_called()

    def test_dry_run_does_not_fetch_missing_client_installer(self):
        from scripts import install

        with patch.object(install, "urlopen") as mocked_urlopen:
            install.install_client_cli("claude", True)
        mocked_urlopen.assert_not_called()

    def test_platform_summary_includes_linux_distribution_release_and_manager(self):
        from scripts import install

        with patch.object(install.platform, "system", return_value="Linux"):
            with patch.object(install.platform, "machine", return_value="x86_64"):
                with patch.object(install.platform, "freedesktop_os_release", return_value={
                    "NAME": "Omarchy", "VERSION_ID": "4.0.6", "PRETTY_NAME": "Omarchy"
                }, create=True):
                    with patch.object(install, "package_manager_command", return_value="omarchy"):
                        summary = install.platform_summary()
        self.assertIn("Omarchy 4.0.6", summary)
        self.assertIn("x86_64", summary)
        self.assertIn("Omarchy pkg", summary)

    def test_platform_summary_includes_macos_release_and_homebrew(self):
        from scripts import install

        with patch.object(install.platform, "system", return_value="Darwin"):
            with patch.object(install.platform, "mac_ver", return_value=("15.1", ("", "", ""), "arm64")):
                with patch.object(install.platform, "machine", return_value="arm64"):
                    with patch.object(install, "package_manager_command", return_value="brew"):
                        summary = install.platform_summary()
        self.assertIn("macOS 15.1", summary)
        self.assertIn("arm64", summary)
        self.assertIn("Homebrew", summary)

    def test_generic_linux_ignores_unrelated_omarchy_command(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"omarchy", "apt-get"} else None

        with patch.object(install.shutil, "which", side_effect=which):
            with patch.object(install, "linux_release_info", return_value={"ID": "ubuntu"}):
                self.assertEqual(install.package_manager_command("Linux"), "apt-get")

    def test_omarchy_id_uses_native_helper_when_present(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"omarchy", "pacman"} else None

        with patch.object(install.shutil, "which", side_effect=which):
            with patch.object(install, "linux_release_info", return_value={"ID": "omarchy"}):
                self.assertEqual(install.package_manager_command("Linux"), "omarchy")

    def test_fedora_id_prefers_dnf_over_earlier_apt_get_in_path(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"apt-get", "dnf"} else None

        with patch.object(install.shutil, "which", side_effect=which):
            with patch.object(install, "linux_release_info", return_value={"ID": "fedora", "ID_LIKE": "rhel fedora"}):
                self.assertEqual(install.package_manager_command("Linux"), "dnf")

    def test_arch_id_prefers_pacman_over_earlier_dnf_in_path(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"dnf", "pacman"} else None

        with patch.object(install.shutil, "which", side_effect=which):
            with patch.object(install, "linux_release_info", return_value={"ID": "arch", "ID_LIKE": "arch"}):
                self.assertEqual(install.package_manager_command("Linux"), "pacman")

    def test_opensuse_id_uses_zypper_over_earlier_pacman_in_path(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"pacman", "zypper"} else None

        with patch.object(install.shutil, "which", side_effect=which):
            with patch.object(install, "linux_release_info", return_value={"ID": "opensuse-tumbleweed"}):
                self.assertEqual(install.package_manager_command("Linux"), "zypper")

    def test_immutable_fedora_variants_do_not_use_dnf(self):
        from scripts import install

        with patch.object(install.shutil, "which", return_value="/usr/bin/dnf"):
            for variant in ("silverblue", "kinoite", "sericea", "onyx", "coreos", "iot"):
                with self.subTest(variant=variant):
                    with patch.object(install, "linux_release_info", return_value={"ID": "fedora", "VARIANT_ID": variant}):
                        self.assertIsNone(install.package_manager_command("Linux"))

    def test_immutable_opensuse_variants_do_not_use_zypper(self):
        from scripts import install

        with patch.object(install.shutil, "which", return_value="/usr/bin/zypper"):
            for distro_id, variant in (
                ("opensuse-microos", "microos"),
                ("opensuse", "microos-desktop"),
                ("opensuse", "aeon"),
                ("opensuse", "kalpa"),
            ):
                with self.subTest(distro_id=distro_id, variant=variant):
                    with patch.object(install, "linux_release_info", return_value={"ID": distro_id, "VARIANT_ID": variant}):
                        self.assertIsNone(install.package_manager_command("Linux"))

    def test_immutable_distro_id_is_excluded_without_variant_id(self):
        from scripts import install

        with patch.object(install.shutil, "which", return_value="/usr/bin/zypper"):
            with patch.object(install, "linux_release_info", return_value={"ID": "opensuse-microos"}):
                self.assertIsNone(install.package_manager_command("Linux"))

    def test_known_image_based_distro_ids_are_excluded_before_family_fallback(self):
        from scripts import install

        with patch.object(install.shutil, "which", return_value="/usr/bin/pacman"):
            for distro_id, id_like in (
                ("nixos", ""), ("steamos", "arch"), ("bazzite", "fedora"),
                ("aurora", "fedora"), ("bluefin", "fedora"), ("ublue", "fedora"),
                ("ublue-os", "fedora"), ("ucore", "fedora"),
            ):
                with self.subTest(distro_id=distro_id):
                    with patch.object(install, "linux_release_info", return_value={"ID": distro_id, "ID_LIKE": id_like}):
                        self.assertIsNone(install.package_manager_command("Linux"))

    def test_shell_and_python_share_the_same_immutable_distro_exclusions(self):
        from scripts import install

        shell = (ROOT / "install.sh").read_text(encoding="utf-8")
        for distro_id in install.IMMUTABLE_LINUX_IDS | install.IMMUTABLE_LINUX_VARIANTS:
            with self.subTest(distro_id=distro_id):
                self.assertIn(distro_id, shell)
        for distro_id in install.LINUX_PACKAGE_MANAGERS:
            with self.subTest(distro_id=distro_id):
                self.assertIn(distro_id, shell)

    def test_immutable_id_like_derivative_is_excluded_by_variant(self):
        from scripts import install

        with patch.object(install.shutil, "which", return_value="/usr/bin/dnf"):
            with patch.object(install, "linux_release_info", return_value={
                "ID": "aurora", "ID_LIKE": "fedora", "VARIANT_ID": "silverblue"
            }):
                self.assertIsNone(install.package_manager_command("Linux"))

    def test_unknown_linux_uses_only_one_unambiguous_package_manager(self):
        from scripts import install

        with patch.object(install, "linux_release_info", return_value={"ID": "customlinux"}):
            with patch.object(install.shutil, "which", side_effect=lambda name: "/usr/bin/dnf" if name == "dnf" else None):
                self.assertEqual(install.package_manager_command("Linux"), "dnf")
            with patch.object(install.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}" if name in {"dnf", "pacman"} else None):
                self.assertIsNone(install.package_manager_command("Linux"))

    def test_command_version_probe_normalizes_output_and_does_not_echo_extra_text(self):
        from scripts import install

        completed = subprocess.CompletedProcess(
            args=["codex", "--version"], returncode=0,
            stdout="codex-cli 0.166.0-build-secret-https://example.invalid/secret\n", stderr="",
        )
        with patch.object(install.shutil, "which", return_value="/fake/codex"):
            with patch.object(install.subprocess, "run", return_value=completed):
                version = install.command_version("codex")
        self.assertEqual(version, "0.166.0")

    def test_shell_metacharacters_are_rejected_as_arguments(self):
        marker = self.base / "unexpected-command-ran"
        result = self.run_installer(f"--codex;touch {marker}")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), "")

    def test_dry_run_does_not_mutate_client_or_install_packages(self):
        result = self.run_installer("--all", "--with-deps", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertNotIn("marketplace add", calls)
        self.assertNotIn("plugin add", calls)
        self.assertNotIn("plugin install", calls)
        self.assertNotIn("sudo ", calls)
        self.assertNotIn("brew ", calls)
        self.assertNotIn("apt-get ", calls)

    def test_dry_run_reads_and_reports_already_installed_state(self):
        Path(f"{self.state}.codex.marketplace").touch()
        Path(f"{self.state}.codex.plugin.correct").touch()
        result = self.run_installer("--codex", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("marketplace already registered", result.stdout)
        self.assertIn("already installed", result.stdout)
        self.assertNotIn("marketplace add", self.calls())
        self.assertNotIn("plugin add", self.calls())

    def test_dry_run_inspects_installed_plugins_before_planning_changes(self):
        result = self.run_installer("--codex", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would run: codex plugin add", result.stdout)
        self.assertIn("codex plugin list --json", self.calls())
        self.assertNotIn("marketplace add", self.calls())
        self.assertNotIn("plugin add", self.calls())

    def test_install_is_idempotent_and_leaves_media_dependencies_alone(self):
        first = self.run_installer("--all")
        self.assertEqual(first.returncode, 0, first.stderr)
        calls_after_first = self.calls()
        second = self.run_installer("--all")
        self.assertEqual(second.returncode, 0, second.stderr)
        calls_after_second = self.calls()
        self.assertIn("marketplace add", calls_after_first)
        self.assertIn("plugin add", calls_after_first)
        self.assertEqual(calls_after_second.count("marketplace add"), 2)
        self.assertEqual(calls_after_second.count("codex plugin add"), 1)
        self.assertEqual(calls_after_second.count("claude plugin install"), 1)
        self.assertNotIn("omarchy pkg add", calls_after_second)
        self.assertNotIn("apt-get install", calls_after_second)
        self.assertNotIn("brew install", calls_after_second)

    def test_shell_installer_with_deps_installs_missing_tools_and_is_idempotent(self):
        dirname = shutil.which("dirname")
        if not dirname:
            self.skipTest("dirname is required by the shell entry point")
        (self.bin / "dirname").symlink_to(dirname)
        for command in ("id", "git"):
            executable = shutil.which(command)
            if executable:
                (self.bin / command).symlink_to(executable)
        (self.bin / "python3").symlink_to(os.sys.executable)
        env = self.env.copy()
        env["PATH"] = str(self.bin)

        first = self.run_installer("--with-deps", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Media tools installed: yt-dlp, ffmpeg, ffprobe", first.stdout)
        for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
            self.assertTrue((self.bin / tool).is_file(), f"missing installed test tool: {tool}")
        calls_after_first = self.calls()

        second = self.run_installer("--with-deps", env=env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Media tools present: yt-dlp, ffmpeg, ffprobe", second.stdout)
        from scripts.install import package_manager_command
        calls_after_second = self.calls()
        manager = package_manager_command(platform.system())
        package_command = "omarchy pkg add yt-dlp ffmpeg" if manager == "omarchy" else f"{manager} install"
        self.assertIn(package_command, calls_after_first)
        self.assertEqual(calls_after_first.count("yt-dlp ffmpeg"), calls_after_second.count("yt-dlp ffmpeg"))
        self.assertEqual(calls_after_second.count("codex plugin add kinescope-download@kinescope-download"), 1, calls_after_second)

    def test_shell_bootstrap_installs_missing_git_and_python_with_omarchy_manager(self):
        bootstrap_bin = self.base / "bootstrap-bin"
        bootstrap_bin.mkdir()
        for command in ("dirname", "id", "uname", "sed", "head", "tr", "ln"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (bootstrap_bin / command).symlink_to(executable)
        shutil.copy2(self.bin / "codex", bootstrap_bin / "codex")
        write_executable(bootstrap_bin / "omarchy", f'''#!/bin/sh
set -eu
printf 'omarchy %s\\n' "$*" >> "$KINESCOPE_TEST_LOG"
test "$*" = 'pkg add git python'
ln -s {os.sys.executable!s} "$KINESCOPE_TEST_BIN/python3"
ln -s {shutil.which("git")!s} "$KINESCOPE_TEST_BIN/git"
''')
        root = self.base / "bootstrap-checkout"
        root.mkdir()
        (root / "scripts").mkdir()
        shutil.copy2(ROOT / "install.sh", root / "install.sh")
        shutil.copy2(ROOT / "scripts/install.py", root / "scripts/install.py")
        env = self.env.copy()
        env.update({
            "PATH": str(bootstrap_bin),
            "KINESCOPE_TEST_BIN": str(bootstrap_bin),
        })
        result = self.run_installer("--codex", cwd=root, env=env, installer_root=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("omarchy pkg add git python", self.calls())
        self.assertIn("codex plugin add kinescope-download@kinescope-download", self.calls())
        self.assertTrue((bootstrap_bin / "python3").is_file())
        self.assertTrue((bootstrap_bin / "git").is_file())

    def test_shell_bootstrap_dry_run_reports_missing_tools_without_installing(self):
        bootstrap_bin = self.base / "bootstrap-dry-run-bin"
        bootstrap_bin.mkdir()
        for command in ("dirname", "id", "uname", "sed", "head", "tr"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (bootstrap_bin / command).symlink_to(executable)
        write_executable(bootstrap_bin / "omarchy", "#!/bin/sh\nexit 0\n")
        root = self.base / "bootstrap-dry-run-checkout"
        root.mkdir()
        (root / "scripts").mkdir()
        shutil.copy2(ROOT / "install.sh", root / "install.sh")
        (root / "scripts/install.py").write_text("raise SystemExit('must not run during bootstrap dry-run')\n", encoding="utf-8")
        env = self.env.copy()
        env["PATH"] = str(bootstrap_bin)
        result = self.run_installer("--dry-run", cwd=root, env=env, installer_root=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would run: omarchy pkg add git python", result.stdout)
        self.assertIn("No bootstrap packages were installed", result.stdout)
        self.assertNotIn("omarchy ", self.calls())

    def test_shell_rejects_unknown_or_conflicting_flags_before_bootstrap(self):
        bootstrap_bin = self.base / "bootstrap-invalid-bin"
        bootstrap_bin.mkdir()
        for command in ("dirname",):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (bootstrap_bin / command).symlink_to(executable)
        root = self.base / "bootstrap-invalid-checkout"
        root.mkdir()
        shutil.copy2(ROOT / "install.sh", root / "install.sh")
        env = self.env.copy()
        env["PATH"] = str(bootstrap_bin)
        for args in (("--unknown",), ("--codex", "--all")):
            with self.subTest(args=args):
                result = self.run_installer(*args, cwd=root, env=env, installer_root=root)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("omarchy pkg add", self.calls())

    def test_shell_archive_bootstrap_runs_without_git_or_python(self):
        bootstrap_bin = self.base / "archive-bootstrap-bin"
        bootstrap_bin.mkdir()
        for command in ("dirname", "id", "mktemp", "tar", "gzip", "rm", "sh"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, command)
            (bootstrap_bin / command).symlink_to(executable)

        source = self.base / "fixture-archive" / "kinescope-download-1.4.0"
        (source / "scripts").mkdir(parents=True)
        (source / "scripts/install.py").write_text("# fixture archive source\n", encoding="utf-8")
        source_script = source / "install.sh"
        source_script.write_text(
            "#!/bin/sh\n"
            "printf 'archive source v1.4.0\\n'\n"
            "printf 'args: %s\\n' \"$*\"\n",
            encoding="utf-8",
        )
        archive = self.base / "fixture-source.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(source, arcname=source.name)

        curl_script = (
            "#!/bin/sh\n"
            "set -eu\n"
            "destination=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = --output ]; then destination=$2; shift 2; else shift; fi\n"
            "done\n"
            f"/bin/cp {shlex.quote(str(archive))} \"$destination\"\n"
            "printf '%s' 'https://codeload.github.com/shubinlab/kinescope-download/tar.gz/refs/tags/v1.4.0'\n"
        )
        write_executable(bootstrap_bin / "curl", curl_script)
        bootstrap_root = self.base / "raw-installer"
        bootstrap_root.mkdir()
        bootstrap_script = bootstrap_root / "install.sh"
        shutil.copy2(ROOT / "install.sh", bootstrap_script)
        env = self.env.copy()
        env["PATH"] = str(bootstrap_bin)
        result = subprocess.run(
            [str(bootstrap_script), "--codex"], cwd=bootstrap_root, env=env,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("archive source v1.4.0", result.stdout)
        self.assertIn("args: --codex", result.stdout)
        self.assertFalse((bootstrap_bin / "git").exists())
        self.assertFalse((bootstrap_bin / "python3").exists())

    def test_failed_child_output_is_suppressed_for_stdout_and_stderr(self):
        from scripts.install import InstallError, run

        sentinel = "fixture-output-sentinel"
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                script = self.base / f"emit-{stream}.py"
                script.write_text(
                    f"import sys; sys.{stream}.write({sentinel!r}); raise SystemExit(23)\n",
                    encoding="utf-8",
                )
                with self.assertRaises(InstallError) as raised:
                    run([os.sys.executable, str(script)])
                message = str(raised.exception)
                self.assertIn("Command failed (23)", message)
                self.assertIn(script.name, message)
                self.assertNotIn(sentinel, message)

    def test_one_client_can_be_selected_without_touching_the_other(self):
        result = self.run_installer("--codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("codex", calls)
        self.assertNotIn("claude", calls)

    def test_explicit_missing_client_is_planned_without_fetching_in_dry_run(self):
        env = self.env.copy()
        env["PATH"] = str(self.bin / "only-codex")
        only_codex = Path(env["PATH"])
        only_codex.mkdir()
        shutil.copy2(self.bin / "codex", only_codex / "codex")
        (only_codex / "codex").chmod(0o755)
        result = self.run_python_installer("--claude", "--dry-run", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would install missing claude CLI", result.stdout)
        self.assertEqual(self.calls(), "")

    def test_dependency_install_is_explicit_and_uses_detected_native_helper(self):
        from scripts.install import package_manager_command

        env = self.env.copy()
        env["PATH"] = str(self.bin)
        result = self.run_python_installer("--codex", "--with-deps", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        manager = package_manager_command(platform.system())
        calls = self.calls()
        expected = "omarchy pkg add" if manager == "omarchy" else f"{manager} install"
        self.assertIn(expected, calls)
        if platform.system() == "Linux" and manager != "omarchy":
            self.assertIn("sudo ", calls)
        else:
            self.assertNotIn("sudo ", calls)

    def test_missing_tools_do_not_install_without_opt_in(self):
        env = self.env.copy()
        env["PATH"] = str(self.bin)
        result = self.run_python_installer("--codex", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Missing media tools", result.stdout)
        self.assertNotIn("omarchy pkg add", self.calls())
        self.assertNotIn("sudo ", self.calls())

    def test_unknown_linux_package_manager_stops_without_sudo(self):
        env = self.env.copy()
        limited_bin = self.base / "limited-bin"
        limited_bin.mkdir()
        shutil.copy2(self.bin / "codex", limited_bin / "codex")
        (limited_bin / "codex").chmod(0o755)
        env["PATH"] = str(limited_bin)
        result = self.run_python_installer("--codex", "--with-deps", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("sudo ", self.calls())

    def test_malformed_marketplace_json_stops_before_mutation(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_BAD_JSON"] = "codex"
        result = self.run_python_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("marketplace add", self.calls())

    def test_valid_json_with_wrong_marketplace_shape_stops_before_mutation(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_BAD_SHAPE"] = "codex:marketplace"
        result = self.run_python_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected JSON shape", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_valid_json_with_wrong_plugin_shape_stops_before_mutation(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_BAD_SHAPE"] = "codex:plugin"
        result = self.run_python_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected JSON shape", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_codex_installed_and_available_plugin_arrays_are_supported(self):
        from scripts.install import plugin_state, validate_list_result

        records = validate_list_result(
            {"installed": [{"name": "another-plugin"}], "available": []},
            "codex plugin list",
            "plugins",
        )
        self.assertEqual(records, [{"name": "another-plugin"}])
        self.assertEqual(plugin_state(records), (False, None))

    def test_marketplace_list_rejects_plugin_wrapper(self):
        from scripts.install import InstallError, validate_list_result

        with self.assertRaises(InstallError):
            validate_list_result({"plugins": []}, "codex marketplace list", "marketplaces")

    def test_plugin_list_rejects_marketplace_wrapper(self):
        from scripts.install import InstallError, validate_list_result

        with self.assertRaises(InstallError):
            validate_list_result({"marketplaces": []}, "codex plugin list", "plugins")

    def test_unidentifiable_marketplace_records_stop_before_mutation(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_UNIDENTIFIABLE"] = "codex:marketplace"
        result = self.run_python_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected JSON shape", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_unidentifiable_plugin_records_stop_before_mutation(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_UNIDENTIFIABLE"] = "codex:plugin"
        result = self.run_python_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected JSON shape", result.stderr)
        self.assertNotIn("marketplace add", self.calls())

    def test_same_plugin_name_from_another_marketplace_is_not_treated_as_installed(self):
        Path(f"{self.state}.codex.plugin.wrong").touch()
        result = self.run_python_installer("--codex")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different or unknown marketplace", result.stderr)
        self.assertNotIn("marketplace add", self.calls())
        self.assertNotIn("plugin add", self.calls())

    def test_ffprobe_uses_the_ffmpeg_package(self):
        from scripts.install import media_packages_for_missing

        self.assertEqual(media_packages_for_missing(["ffprobe"]), ["ffmpeg"])
        self.assertEqual(media_packages_for_missing(["yt-dlp", "ffprobe"]), ["yt-dlp", "ffmpeg"])

    def test_macos_uses_homebrew_plan(self):
        from scripts import install

        with patch.object(install.platform, "system", return_value="Darwin"):
            with patch.object(install.shutil, "which", side_effect=lambda name: "/brew/bin/brew" if name == "brew" else None):
                self.assertEqual(install.media_plan(["yt-dlp", "ffmpeg"]), [["brew", "install", "yt-dlp", "ffmpeg"]])

    def test_unsupported_os_is_refused(self):
        from scripts import install

        with patch.object(install.platform, "system", return_value="Windows"):
            with self.assertRaises(install.InstallError):
                install.media_plan(["yt-dlp", "ffmpeg"])

    def test_apt_plan_uses_sudo_for_missing_tools(self):
        from scripts import install

        def which(name):
            return f"/usr/bin/{name}" if name in {"apt-get", "sudo"} else None

        with patch.object(install.platform, "system", return_value="Linux"):
            with patch.object(install.shutil, "which", side_effect=which):
                with patch.object(install, "linux_release_info", return_value={"ID": "ubuntu", "ID_LIKE": "debian"}):
                    with patch.object(install.os, "geteuid", return_value=1000):
                        self.assertEqual(
                            install.media_plan(["yt-dlp", "ffmpeg"]),
                            [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "yt-dlp", "ffmpeg"]],
                        )

    def test_installer_refuses_root_user_scope_changes(self):
        from scripts import install

        error_output = StringIO()
        with patch.object(install.os, "geteuid", return_value=0):
            with patch.object(install, "selected_clients", return_value=["codex"]):
                with patch.object(install, "install_client") as install_client:
                    with redirect_stderr(error_output):
                        result = install.main(["--codex"])
        self.assertNotEqual(result, 0)
        self.assertIn("normal user", error_output.getvalue())
        install_client.assert_not_called()

    def test_client_failure_stops_install(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_FAIL_ADD"] = "codex"
        result = self.run_installer("--codex", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("plugin add", self.calls())

    def test_all_clients_are_preflighted_before_any_install(self):
        env = self.env.copy()
        env["KINESCOPE_TEST_BAD_JSON"] = "claude"
        result = self.run_python_installer("--all", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("marketplace add", self.calls())

    def test_unsupported_dependency_manager_fails_before_plugin_install(self):
        env = self.env.copy()
        limited_bin = self.base / "clients-only"
        limited_bin.mkdir()
        for name in ("codex", "claude"):
            shutil.copy2(self.bin / name, limited_bin / name)
            (limited_bin / name).chmod(0o755)
        env["PATH"] = str(limited_bin)
        result = self.run_python_installer("--all", "--with-deps", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("marketplace add", self.calls())

    def test_path_with_spaces_is_supported(self):
        spaced = self.base / "plugin checkout with spaces"
        shutil.copytree(ROOT, spaced, ignore=shutil.ignore_patterns(".git", ".superpowers", "__pycache__"))
        result = self.run_installer("--codex", "--dry-run", cwd=spaced, installer_root=spaced)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
