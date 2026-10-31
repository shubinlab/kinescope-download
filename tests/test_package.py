import json
import os
import pty
import re
import select
import shlex
import struct
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "kinescope-download"
SKILL = PLUGIN / "skills" / "kinescope-video-download" / "SKILL.md"


class PackageTests(unittest.TestCase):
    def read_json(self, path):
        self.assertTrue(path.is_file(), f"missing required manifest: {path.relative_to(ROOT)}")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_codex_marketplace_points_inside_plugin_package(self):
        marketplace = self.read_json(ROOT / ".agents/plugins/marketplace.json")
        entry = next(item for item in marketplace["plugins"] if item["name"] == "kinescope-download")
        self.assertEqual(marketplace["name"], "kinescope-download")
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual(entry["source"]["path"], "./plugins/kinescope-download")
        manifest = self.read_json(PLUGIN / "plugin.json")
        self.assertEqual(manifest["name"], entry["name"])
        self.assertEqual(manifest["version"], entry["version"])
        self.assertTrue((PLUGIN / "skills/kinescope-video-download/SKILL.md").is_file())

    def test_claude_marketplace_points_to_valid_plugin(self):
        marketplace = self.read_json(ROOT / ".claude-plugin/marketplace.json")
        entry = next(item for item in marketplace["plugins"] if item["name"] == "kinescope-download")
        self.assertEqual(marketplace["name"], "kinescope-download")
        self.assertEqual(entry["source"], "./plugins/kinescope-download")
        self.assertNotIn("version", entry)
        manifest = self.read_json(PLUGIN / ".claude-plugin/plugin.json")
        self.assertEqual(manifest["name"], entry["name"])
        self.assertTrue(SKILL.is_file())

    def test_current_plugin_release_versions_are_consistent(self):
        codex_plugin = self.read_json(PLUGIN / "plugin.json")
        codex_marketplace = self.read_json(ROOT / ".agents/plugins/marketplace.json")
        claude_plugin = self.read_json(PLUGIN / ".claude-plugin/plugin.json")
        claude_marketplace = self.read_json(ROOT / ".claude-plugin/marketplace.json")
        codex_entry = next(item for item in codex_marketplace["plugins"] if item["name"] == "kinescope-download")
        claude_entry = next(item for item in claude_marketplace["plugins"] if item["name"] == "kinescope-download")

        self.assertEqual(codex_plugin["version"], "1.2.0")
        self.assertEqual(codex_entry["version"], codex_plugin["version"])
        self.assertEqual(claude_plugin["version"], codex_plugin["version"])
        self.assertNotIn("version", claude_entry)
        self.assertEqual(claude_entry["source"], "./plugins/kinescope-download")

    def test_skill_references_resolve_and_stay_inside_package(self):
        self.assertTrue(SKILL.is_file(), "missing packaged Kinescope skill")
        skill = SKILL.read_text(encoding="utf-8")
        references = re.findall(r"\]\((references/[^)]+)\)", skill)
        self.assertGreaterEqual(len(references), 2)
        for reference in references:
            resolved = (SKILL.parent / reference).resolve()
            self.assertTrue(resolved.is_relative_to(PLUGIN.resolve()))
            self.assertTrue(resolved.is_file(), f"broken skill reference: {reference}")

    def test_403_helper_reference_has_a_resolvable_directory_instruction(self):
        recovery = (SKILL.parent / "references/403-recovery.md").read_text(encoding="utf-8")
        self.assertIn("folder containing SKILL.md", recovery)
        self.assertIn("scripts/extract_manifest.py", recovery)

    def test_skill_documents_secure_share_url_input_and_original_asset_checks(self):
        skill = SKILL.read_text(encoding="utf-8")
        recovery = (SKILL.parent / "references/403-recovery.md").read_text(encoding="utf-8")
        api_reference = (SKILL.parent / "references/api-original.md").read_text(encoding="utf-8")
        for phrase in (
            "unambiguous original",
            "stream-copy remux",
            "lossy conversion",
            "Treat the returned link as a short-lived credential",
        ):
            self.assertIn(phrase, api_reference)
        self.assertIn("--batch-file -", skill)
        self.assertIn("--url-stdin", recovery)
        self.assertIn("env -u KINESCOPE_URL", recovery)
        self.assertIn("yt-dlp.conf", recovery)
        self.assertIn("Embedding page URL", recovery)
        self.assertIn('--config-locations "$referer_config"', recovery)
        self.assertIn("unset safe_referrer", recovery)
        self.assertIn("set +x\nset -euo pipefail", recovery)
        self.assertEqual(recovery.count("env -u KINESCOPE_URL -u KINESCOPE_REFERRER -u REFERRER_URL -u safe_referrer yt-dlp"), 1)
        self.assertIn("set +x", recovery)
        self.assertNotIn('--referer "$KINESCOPE_REFERRER"', recovery)
        self.assertIn("shell history", recovery)

    def test_default_download_is_quiet_and_uses_stdin_for_share_urls(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("--quiet --no-progress --no-playlist", skill)
        self.assertIn("--batch-file -", skill)
        self.assertIn("set +x", skill)
        self.assertIn("after_move:filepath", skill)
        self.assertIn("ffprobe` once", skill)
        self.assertIn("record its exit code", skill)
        for reference in ("references/api-original.md", "references/playlist-status.md", "references/vpn-routing.md"):
            self.assertIn(reference, skill)

    @unittest.skipUnless(hasattr(pty, "fork"), "requires a POSIX pseudo-terminal")
    def test_default_download_prompt_hides_url_from_argv_environment_and_xtrace(self):
        skill = SKILL.read_text(encoding="utf-8")
        block = skill.split("```bash\n", 1)[1].split("\n```", 1)[0]
        secret = "https://kinescope.io/share?access_token=stdin-sentinel"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bindir = root / "bin"
            bindir.mkdir()
            capture = root / "capture.json"
            output = root / "%(title)s-%(id)s.%(ext)s"
            block = block.replace("'/path/to/output/%(title)s-%(id)s.%(ext)s'", shlex.quote(str(output)))
            fake_ytdlp = bindir / "yt-dlp"
            fake_ytdlp.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "url = sys.stdin.readline().rstrip('\\n')\n"
                "json.dump({'argv': sys.argv[1:], 'url': url, 'env': {key: os.environ.get(key) for key in "
                "('KINESCOPE_URL', 'KINESCOPE_REFERRER', 'REFERRER_URL', 'safe_referrer')}}, "
                "open(os.environ['CAPTURE'], 'w'))\n",
                encoding="utf-8",
            )
            fake_ytdlp.chmod(0o700)
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{bindir}:{environment['PATH']}",
                "CAPTURE": str(capture),
                "KINESCOPE_URL": "old-url-sentinel",
                "KINESCOPE_REFERRER": "old-referrer-sentinel",
                "REFERRER_URL": "old-referrer-env-sentinel",
                "safe_referrer": "old-safe-referrer-sentinel",
            })
            pid, master_fd = pty.fork()
            if pid == 0:
                os.execvpe("bash", ["bash", "-c", "set -x\n" + block], environment)

            output_bytes = bytearray()
            sent = False
            deadline = time.monotonic() + 15
            status = None
            try:
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([master_fd], [], [], 0.1)
                    if readable:
                        try:
                            chunk = os.read(master_fd, 4096)
                        except OSError:
                            chunk = b""
                        output_bytes.extend(chunk)
                        if not sent and b"Kinescope share/embed URL:" in output_bytes:
                            time.sleep(0.2)
                            os.write(master_fd, secret.encode() + b"\n")
                            sent = True
                    waited_pid, child_status = os.waitpid(pid, os.WNOHANG)
                    if waited_pid == pid:
                        status = child_status
                        break
                self.assertTrue(sent, "URL prompt did not appear in the pseudo-terminal")
                self.assertIsNotNone(status, "download command did not exit")
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            finally:
                if status is None:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                os.close(master_fd)

            output_text = output_bytes.decode(errors="replace")
            self.assertNotIn(secret, output_text)
            captured = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(captured["url"], secret)
            self.assertNotIn(secret, captured["argv"])
            self.assertEqual(captured["env"], {
                "KINESCOPE_URL": None,
                "KINESCOPE_REFERRER": None,
                "REFERRER_URL": None,
                "safe_referrer": None,
            })

    def test_local_history_is_opt_in_and_documented(self):
        skill = SKILL.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        history = PLUGIN / "skills/kinescope-video-download/scripts/local_history.py"
        self.assertTrue(history.is_file())
        self.assertIn("History is opt-in", skill)
        self.assertIn("clear --confirm", skill)
        self.assertIn("disabled by default", readme)
        self.assertIn("may be sent to the selected Codex or Claude model provider", readme)
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/local_history.py", workflow)
        script = history.read_text(encoding="utf-8")
        for forbidden in ("urllib.request", "requests.", "socket.", "subprocess."):
            self.assertNotIn(forbidden, script)

    def test_skill_has_no_host_paths_or_signed_manifest_examples(self):
        self.assertTrue(SKILL.is_file(), "missing packaged Kinescope skill")
        files = [path for path in PLUGIN.rglob("*") if path.is_file() and ".git" not in path.parts]
        text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in files)
        self.assertNotIn("/home/totem/", text)
        self.assertNotRegex(text, r"https?://kinescope\.io/[^\s\"']+master\.m3u8\?[^\s\"']*(?:sign|token)=")
        self.assertNotRegex(text, r"gho_[A-Za-z0-9]{20,}")
        self.assertNotIn("/opt/adguardvpn_cli", text)
        self.assertIn("dscacheutil", text)

    def test_installer_does_not_execute_a_remote_shell_payload(self):
        shell = (ROOT / "install.sh").read_text(encoding="utf-8")
        python = (ROOT / "scripts/install.py").read_text(encoding="utf-8")
        self.assertNotRegex(shell + python, r"curl[^\n]*\|\s*(?:bash|sh)")
        self.assertNotIn("shell=True", python)

    def test_readme_documents_supported_clients_and_optional_dependencies(self):
        readme = ROOT / "README.md"
        self.assertTrue(readme.is_file(), "missing user-facing README")
        text = readme.read_text(encoding="utf-8")
        for required in ("Linux", "macOS", "Codex", "Claude Code", "--with-deps", "DRM"):
            self.assertIn(required, text)
        self.assertIn("./install.sh", text)

    def test_readme_documents_one_command_github_install_and_prerequisites(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "git clone --depth 1 https://github.com/shubinlab/kinescope-download.git kinescope-download && kinescope-download/install.sh --with-deps",
            text,
        )
        self.assertIn("Git, Python 3, and at least one supported client CLI", text)
        self.assertIn("does not install those prerequisites", text)
        self.assertIn("executes the checked-out `install.sh`", text)
        self.assertIn("does not pipe a remote script into a shell", text)
        self.assertIn("--dry-run", text)

    def test_brand_asset_is_valid_svg(self):
        logo = ROOT / "assets" / "logo.svg"
        self.assertTrue(logo.is_file(), "missing logo asset")
        root = ET.parse(logo).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("viewBox", root.attrib)

    def test_both_clients_use_a_square_png_icon(self):
        icon = PLUGIN / "assets/icon.png"
        self.assertTrue(icon.is_file(), "missing square plugin icon")
        data = icon.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (1024, 1024))
        claude_manifest = self.read_json(PLUGIN / ".claude-plugin/plugin.json")
        codex_manifest = self.read_json(PLUGIN / "plugin.json")
        self.assertEqual(claude_manifest["icon"], "./assets/icon.png")
        self.assertEqual(codex_manifest["extensions"]["com.openai"]["interface"]["composerIcon"], "./assets/icon.png")

    def test_ci_actions_are_sha_pinned_and_dependabot_updates_them(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"uses: actions/checkout@[0-9a-f]{40} # v7\.0\.1")
        self.assertRegex(workflow, r"uses: actions/setup-python@[0-9a-f]{40} # v7\.1\.0")

        dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "github-actions"', dependabot)
        self.assertIn('directory: "/"', dependabot)
        self.assertIn('interval: "weekly"', dependabot)


if __name__ == "__main__":
    unittest.main()
