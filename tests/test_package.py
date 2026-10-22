import json
import re
import struct
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
        manifest = self.read_json(PLUGIN / ".claude-plugin/plugin.json")
        self.assertEqual(manifest["name"], entry["name"])
        self.assertEqual(manifest["version"], entry["version"])
        self.assertTrue(SKILL.is_file())

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


if __name__ == "__main__":
    unittest.main()
