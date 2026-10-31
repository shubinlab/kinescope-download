import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SingleInstallerTests(unittest.TestCase):
    def test_public_entrypoint_contains_the_install_flow_without_python_handoff(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("scripts/install.py", script)
        self.assertNotIn("exec python", script)
        self.assertIn("plugin marketplace", script)
        self.assertIn("plugin install", script)

    def test_readme_uses_one_invocation_of_the_published_script(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("curl -fsSL https://shubin.co/k | sh", readme)
        self.assertNotIn("| sh -s --", readme)
        self.assertNotIn("shared Python installer", readme)

    def test_help_runs_without_python(self):
        result = subprocess.run(
            [str(ROOT / "install.sh"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
