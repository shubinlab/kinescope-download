import contextlib
import io
import json
import os
import subprocess
import stat
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins/kinescope-download/skills/kinescope-video-download/scripts/local_history.py"
)

import importlib.util

SPEC = importlib.util.spec_from_file_location("local_history", MODULE_PATH)
local_history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_history)


class LocalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.temp_path)})
        self.env.start()
        self.state = self.temp_path / ".local" / "state" / "kinescope-download"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def call(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = local_history.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def enable(self):
        code, _, error = self.call(["enable"])
        self.assertEqual((code, error), (0, ""))

    def start(self, concurrency="16"):
        code, output, error = self.call([
            "start", "--plugin-version", "1.1.0", "--yt-dlp-version", "2026.08.19",
            "--client", "codex", "--source", "share_hls", "--concurrency", concurrency,
        ])
        self.assertEqual((code, error), (0, ""))
        return json.loads(output)["run_id"]

    def finish(self, run_id, outcome="success", concurrency="16"):
        return self.call([
            "finish", "--run-id", run_id, "--plugin-version", "1.1.0",
            "--yt-dlp-version", "2026.08.19", "--client", "codex", "--source", "share_hls",
            "--concurrency", concurrency, "--outcome", outcome,
            "--failure-stage", "none", "--failure-class", "none", "--retry-bucket", "none",
            "--transfer-time-bucket", "1m_to_5m", "--media-duration-bucket", "2h_to_4h",
            "--size-bucket", "1gb_to_4gb",
            "--resolution-bucket", "1080p", "--probe", "pass", "--decode", "pass",
        ])

    def test_history_is_disabled_by_default_without_creating_state(self):
        code, output, error = self.call(["summary"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("disabled", output)
        self.assertFalse(self.state.exists())

    def test_enable_and_status_print_resolved_history_path(self):
        code, output, error = self.call(["enable"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn(str(self.state / "events.jsonl"), output)
        code, output, error = self.call(["status"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("enabled", output)
        self.assertIn(str(self.state / "events.jsonl"), output)

    def test_opt_in_writes_only_allowlisted_fields_with_private_permissions(self):
        self.enable()
        run_id = self.start()
        code, output, error = self.finish(run_id)
        self.assertEqual((code, error), (0, ""))
        self.assertIn('"recorded":true', output)

        lines = (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        events = [json.loads(line) for line in lines]
        self.assertTrue(all(local_history._validate_event(event) is not None for event in events))
        text = "\n".join(lines)
        for forbidden in ("video_id", "title", "output_path", "http", "cookie", "token", "/home/"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "events.jsonl").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.state / "settings.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.state / "events.lock").stat().st_mode), 0o600)

        code, summary, error = self.call(["summary"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("success=1", summary)
        self.assertIn("No comparable profile has five attempts", summary)
        self.assertNotIn(run_id, summary)

    def test_disable_keeps_existing_history_and_stops_future_writes(self):
        self.enable()
        run_id = self.start()
        self.finish(run_id)
        before = (self.state / "events.jsonl").read_bytes()
        code, _, error = self.call(["disable"])
        self.assertEqual((code, error), (0, ""))
        code, output, error = self.call(["start", "--plugin-version", "1.1.0", "--client", "codex", "--source", "share_hls"])
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output), {"enabled": False})
        self.assertEqual((self.state / "events.jsonl").read_bytes(), before)

    def test_finish_is_idempotent_for_a_run_and_event_id(self):
        self.enable()
        run_id = self.start()
        first = self.finish(run_id)
        second = self.finish(run_id)
        self.assertIn('"recorded":true', first[1])
        self.assertIn('"recorded":false', second[1])
        self.assertEqual(len((self.state / "events.jsonl").read_text().splitlines()), 2)

    def test_finish_requires_a_matching_start_event(self):
        self.enable()
        unknown_run = str(uuid.uuid4())
        code, _, error = self.finish(unknown_run)
        self.assertEqual(code, 1)
        self.assertIn("does not match a recorded start", error)
        self.assertFalse((self.state / "events.jsonl").exists())

    def test_untrusted_free_text_is_rejected_by_enum_parser(self):
        self.enable()
        sentinel = "https://example.invalid/video?token=do-not-echo"
        stderr = io.StringIO()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(stderr):
                local_history.main([
                    "start", "--plugin-version", "1.1.0", "--client", sentinel,
                    "--source", "share_hls",
                ])
        self.assertNotIn("do-not-echo", stderr.getvalue())
        self.assertFalse((self.state / "events.jsonl").exists())

    def test_partial_tail_is_repaired_without_printing_or_discarding_complete_events(self):
        self.enable()
        first_run = self.start()
        with (self.state / "events.jsonl").open("ab") as output:
            output.write(b'{"token":"private-tail')
        second_run = self.start()
        self.assertNotEqual(first_run, second_run)
        lines = (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(local_history._validate_event(json.loads(line)) for line in lines))

    def test_symlink_history_file_is_refused(self):
        self.enable()
        target = self.temp_path / "external.jsonl"
        target.write_text("preserve this\n", encoding="utf-8")
        (self.state / "events.jsonl").symlink_to(target)
        with self.assertRaises(local_history.HistoryError):
            local_history._append_event({
                "schema_version": 1,
                "event_id": f"{uuid.uuid4()}:start",
                "run_id": str(uuid.uuid4()),
                "event": "started",
                "day": "2026-10-31",
                "plugin_version": "1.1.0",
                "yt_dlp_version": "unknown",
                "client": "codex",
                "source": "share_hls",
                "concurrency": "16",
            })
        self.assertEqual(target.read_text(encoding="utf-8"), "preserve this\n")

    def test_hardlinked_history_file_is_refused(self):
        self.enable()
        target = self.temp_path / "external.jsonl"
        target.write_text("preserve this\n", encoding="utf-8")
        os.link(target, self.state / "events.jsonl")
        with self.assertRaises(local_history.HistoryError):
            local_history._append_event({
                "schema_version": 1,
                "event_id": f"{uuid.uuid4()}:start",
                "run_id": str(uuid.uuid4()),
                "event": "started",
                "day": "2026-10-31",
                "plugin_version": "1.1.0",
                "yt_dlp_version": "unknown",
                "client": "codex",
                "source": "share_hls",
                "concurrency": "16",
            })
        self.assertEqual(target.read_text(encoding="utf-8"), "preserve this\n")

    def test_concurrent_appends_remain_valid_and_complete(self):
        self.enable()
        events = []
        for _ in range(32):
            run_id = str(uuid.uuid4())
            events.append({
                "schema_version": 1,
                "event_id": f"{run_id}:start",
                "run_id": run_id,
                "event": "started",
                "day": "2026-10-31",
                "plugin_version": "1.1.0",
                "yt_dlp_version": "2026.08.19",
                "client": "codex",
                "source": "share_hls",
                "concurrency": "16",
            })
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(local_history._append_event, events))
        self.assertEqual(results, [True] * 32)
        lines = (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 32)
        self.assertTrue(all(local_history._validate_event(json.loads(line)) for line in lines))

    def test_concurrent_cli_processes_share_the_same_lock(self):
        self.enable()
        command = [
            sys.executable, str(MODULE_PATH), "start", "--plugin-version", "1.1.0",
            "--yt-dlp-version", "2026.08.19", "--client", "claude", "--source", "share_hls",
            "--concurrency", "16",
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: subprocess.run(command, text=True, capture_output=True, check=True), range(8)))
        self.assertEqual(len({json.loads(result.stdout)["run_id"] for result in results}), 8)
        lines = (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 8)
        self.assertTrue(all(local_history._validate_event(json.loads(line)) for line in lines))

    def test_clear_requires_explicit_confirmation(self):
        self.enable()
        self.start()
        code, _, error = self.call(["clear"])
        self.assertEqual(code, 1)
        self.assertIn("--confirm", error)
        self.assertTrue((self.state / "events.jsonl").exists())
        self.call(["clear", "--confirm"])
        self.assertFalse((self.state / "events.jsonl").exists())
        self.assertTrue(json.loads((self.state / "settings.json").read_text())["enabled"])

    def test_valid_json_with_wrong_types_is_skipped_without_breaking_summary(self):
        self.enable()
        run_id = self.start()
        self.finish(run_id)
        log = self.state / "events.jsonl"
        event = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        event["client"] = ["https://example.invalid/?token=private"]
        with log.open("ab") as output:
            output.write((json.dumps(event) + "\n").encode())
        code, summary, error = self.call(["summary"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("success=1", summary)
        self.assertNotIn("private", summary)

    def test_comparable_profiles_are_summarized_only_after_five_across_concurrency_values(self):
        self.enable()
        for concurrency in ("4", "4", "4", "8", "8"):
            run_id = self.start(concurrency)
            self.finish(run_id, concurrency=concurrency)
        code, summary, error = self.call(["summary"])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Comparable profiles (5+ attempts, multiple concurrency values)", summary)
        self.assertIn("4/success/1m_to_5m", summary)
        self.assertIn("8/success/1m_to_5m", summary)

    def test_unknown_settings_schema_fails_with_preservation_notice(self):
        self.enable()
        run_id = self.start()
        self.finish(run_id)
        settings = self.state / "settings.json"
        settings.write_text('{"schema_version":99,"enabled":true}\n', encoding="utf-8")
        code, _, error = self.call(["summary"])
        self.assertEqual(code, 1)
        self.assertIn("unsupported version", error)
        original_settings = settings.read_bytes()
        for command in ("enable", "disable"):
            code, _, error = self.call([command])
            self.assertEqual(code, 1)
            self.assertIn("unsupported version", error)
            self.assertEqual(settings.read_bytes(), original_settings)
        self.assertEqual(len((self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()), 2)

    def test_unknown_event_schema_fails_with_preservation_notice(self):
        self.enable()
        run_id = self.start()
        self.finish(run_id)
        log = self.state / "events.jsonl"
        with log.open("ab") as output:
            output.write(b'{"schema_version":99,"future":"data"}\n')
        code, _, error = self.call(["summary"])
        self.assertEqual(code, 1)
        self.assertIn("unsupported event version", error)
        self.assertIn(b'"schema_version":99', log.read_bytes())

    def test_capacity_stop_preserves_previous_records_and_summary(self):
        self.enable()
        original_limit = local_history.MAX_LOG_BYTES
        try:
            first_run = self.start()
            self.finish(first_run)
            before_second = (self.state / "events.jsonl").stat().st_size
            second_run = self.start()
            after_second_start = (self.state / "events.jsonl").stat().st_size
            self.assertGreater(after_second_start, before_second)
            local_history.MAX_LOG_BYTES = after_second_start + 10
            code, _, error = self.finish(second_run)
            self.assertEqual(code, 1)
            self.assertIn("existing entries were preserved", error)
            self.assertLessEqual((self.state / "events.jsonl").stat().st_size, after_second_start + 10)
            code, summary, error = self.call(["summary"])
            self.assertEqual((code, error), (0, ""))
            self.assertIn("success=1", summary)
            self.assertIn("open/unclosed", summary)
        finally:
            local_history.MAX_LOG_BYTES = original_limit


if __name__ == "__main__":
    unittest.main()
