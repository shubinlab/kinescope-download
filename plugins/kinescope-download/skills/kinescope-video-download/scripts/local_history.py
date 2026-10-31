#!/usr/bin/env python3
"""Opt-in, local-only, allowlisted Kinescope download history."""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import fcntl
import json
import os
import pathlib
import re
import stat
import sys
import uuid


EVENT_SCHEMA_VERSION = 1
SETTINGS_SCHEMA_VERSION = 1
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 2048
SUMMARY_RUNS = 20
VERSION_PATTERN = re.compile(r"\A\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
YTDLP_VERSION_PATTERN = re.compile(r"\A(?:unknown|\d{4}\.\d{2}\.\d{2}(?:\.\d{1,9})?)\Z")

CLIENTS = {"codex", "claude", "other"}
SOURCES = {"share_hls", "api_original", "unknown"}
CONCURRENCY = {"4", "8", "16", "unknown"}
OUTCOMES = {"success", "failed", "cancelled"}
FAILURE_STAGES = {"none", "extract", "download", "merge", "probe", "decode"}
FAILURE_CLASSES = {
    "none", "http_403", "http_429", "timeout", "fragment", "merge", "probe", "decode", "auth", "other"
}
RETRY_BUCKETS = {"none", "one", "two_or_more"}
MEDIA_DURATION_BUCKETS = {"under_30m", "30m_to_2h", "2h_to_4h", "over_4h", "unknown"}
TRANSFER_TIME_BUCKETS = {"under_1m", "1m_to_5m", "5m_to_20m", "20m_to_60m", "over_60m", "unknown"}
SIZE_BUCKETS = {"under_100mb", "100mb_to_1gb", "1gb_to_4gb", "over_4gb", "unknown"}
RESOLUTION_BUCKETS = {"480p_or_less", "720p", "1080p", "1440p", "2160p", "unknown"}
CHECK_RESULTS = {"pass", "fail", "skipped"}

START_FIELDS = {
    "schema_version", "event_id", "run_id", "event", "day", "plugin_version", "yt_dlp_version",
    "client", "source", "concurrency"
}
FINISH_FIELDS = START_FIELDS | {
    "outcome", "failure_stage", "failure_class", "retry_bucket", "transfer_time_bucket", "media_duration_bucket", "size_bucket",
    "resolution_bucket", "probe", "decode"
}


class HistoryError(Exception):
    """A safe, user-facing history error without private path contents."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Do not echo rejected argument values, which could contain secrets."""

    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "local history: invalid arguments; use fixed choices only\n")


def _state_directory(*, create: bool = False) -> pathlib.Path:
    root = pathlib.Path.home() / ".local" / "state"
    try:
        root_info = root.lstat()
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise HistoryError("XDG_STATE_HOME must be a real directory, not a symlink")
    except FileNotFoundError:
        if not create:
            return root / "kinescope-download"
    except OSError as error:
        raise HistoryError("Could not inspect XDG_STATE_HOME") from error
    target = root / "kinescope-download"
    if not create and not target.exists():
        return target
    try:
        if create:
            target.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = target.lstat()
    except OSError as error:
        raise HistoryError("Could not prepare the private local history directory") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise HistoryError("Local history directory must be a user-owned real directory")
    try:
        os.chmod(target, 0o700)
    except OSError as error:
        raise HistoryError("Could not protect the local history directory") from error
    return target


def _check_regular_file(path: pathlib.Path, *, missing_ok: bool = True) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise HistoryError("Required local history file is missing")
    except OSError as error:
        raise HistoryError("Could not inspect a local history file") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise HistoryError("Local history file must be a user-owned regular file")
    try:
        os.chmod(path, 0o600)
    except OSError as error:
        raise HistoryError("Could not protect a local history file") from error
    return True


def _settings_path() -> pathlib.Path:
    return _state_directory() / "settings.json"


def _history_path() -> pathlib.Path:
    return _state_directory() / "events.jsonl"


@contextlib.contextmanager
def _history_lock():
    directory = _state_directory(create=True)
    path = directory / "events.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            os.close(descriptor)
            descriptor = None
            raise HistoryError("Local history lock failed safety checks")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except HistoryError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise HistoryError("Could not lock local history") from error
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_settings(enabled: bool) -> None:
    directory = _state_directory(create=True)
    with _history_lock():
        path = directory / "settings.json"
        _check_regular_file(path)
        _is_enabled()
        payload = json.dumps({"schema_version": SETTINGS_SCHEMA_VERSION, "enabled": enabled}, separators=(",", ":")).encode() + b"\n"
        temporary = directory / f".settings-{uuid.uuid4()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise HistoryError("Could not update local history settings") from error


def _is_enabled() -> bool:
    path = _settings_path()
    if not _check_regular_file(path):
        return False
    try:
        raw = path.read_bytes()
        if len(raw) > 256:
            raise HistoryError("Local history settings exceed the size limit; existing data was preserved")
        settings = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HistoryError("Local history settings are invalid; existing data was preserved") from error
    except OSError as error:
        raise HistoryError("Could not read local history settings") from error
    if not isinstance(settings, dict) or set(settings) != {"schema_version", "enabled"}:
        raise HistoryError("Local history settings have an unknown format; existing data was preserved")
    if type(settings.get("schema_version")) is not int:
        raise HistoryError("Local history settings have an invalid version; existing data was preserved")
    if settings["schema_version"] != SETTINGS_SCHEMA_VERSION:
        raise HistoryError("Local history settings use an unsupported version; existing data was preserved")
    if not isinstance(settings.get("enabled"), bool):
        raise HistoryError("Local history setting is invalid; existing data was preserved")
    return settings["enabled"]


def _new_run_id() -> str:
    return str(uuid.uuid4())


def _validate_event(event: object) -> dict | None:
    if (
        not isinstance(event, dict)
        or type(event.get("schema_version")) is not int
    ):
        return None
    if event["schema_version"] != EVENT_SCHEMA_VERSION:
        raise HistoryError("Local history contains an unsupported event version; existing data was preserved")
    kind = event.get("event")
    expected = START_FIELDS if kind == "started" else FINISH_FIELDS if kind == "finished" else None
    if expected is None or set(event) != expected:
        return None
    if any(not isinstance(event.get(key), str) for key in ("event_id", "run_id", "event", "day", "plugin_version", "yt_dlp_version", "client", "source", "concurrency")):
        return None
    try:
        uuid.UUID(event["run_id"])
        day = dt.date.fromisoformat(event["day"])
    except (ValueError, TypeError, KeyError):
        return None
    if day.isoformat() != event["day"] or event["event_id"] != f"{event['run_id']}:{'start' if kind == 'started' else 'finish'}":
        return None
    if not isinstance(event["plugin_version"], str) or not VERSION_PATTERN.fullmatch(event["plugin_version"]):
        return None
    if not isinstance(event["yt_dlp_version"], str) or not YTDLP_VERSION_PATTERN.fullmatch(event["yt_dlp_version"]):
        return None
    if event["client"] not in CLIENTS or event["source"] not in SOURCES or event["concurrency"] not in CONCURRENCY:
        return None
    if kind == "finished":
        if any(
            not isinstance(event.get(key), str)
            for key in (
                "outcome", "failure_stage", "failure_class", "retry_bucket", "transfer_time_bucket",
                "media_duration_bucket", "size_bucket", "resolution_bucket", "probe", "decode",
            )
        ):
            return None
        if (
            event["outcome"] not in OUTCOMES
            or event["failure_stage"] not in FAILURE_STAGES
            or event["failure_class"] not in FAILURE_CLASSES
            or event["retry_bucket"] not in RETRY_BUCKETS
            or event["transfer_time_bucket"] not in TRANSFER_TIME_BUCKETS
            or event["media_duration_bucket"] not in MEDIA_DURATION_BUCKETS
            or event["size_bucket"] not in SIZE_BUCKETS
            or event["resolution_bucket"] not in RESOLUTION_BUCKETS
            or event["probe"] not in CHECK_RESULTS
            or event["decode"] not in CHECK_RESULTS
        ):
            return None
    return event


def _read_complete_events(data: bytes) -> tuple[list[dict], bytes]:
    lines = data.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        tail = lines.pop()
        data = data[: -len(tail)]
    events = []
    for line in lines:
        if len(line) > MAX_LINE_BYTES:
            continue
        try:
            candidate = _validate_event(json.loads(line))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            candidate = None
        if candidate is not None:
            events.append(candidate)
    return events, data


def _append_event(event: dict) -> bool:
    data = (json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    if len(data) > MAX_LINE_BYTES:
        raise HistoryError("Local history event exceeds the size limit")
    path = _history_path()
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    with _history_lock():
        if not _is_enabled():
            raise HistoryError("Local history is disabled")
        if event["event"] == "finished" and not path.exists():
            raise HistoryError("Finish event does not match a recorded start")
        created = not path.exists()
        try:
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "r+b", buffering=0) as log:
                info = os.fstat(log.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise HistoryError("Local history file failed safety checks")
                if info.st_size > MAX_LOG_BYTES:
                    raise HistoryError("Local history file exceeds the size limit")
                os.fchmod(log.fileno(), 0o600)
                log.seek(0)
                previous = log.read(MAX_LOG_BYTES + 1)
                if len(previous) > MAX_LOG_BYTES:
                    raise HistoryError("Local history file exceeds the size limit")
                events, complete = _read_complete_events(previous)
                if any(item["event_id"] == event["event_id"] for item in events):
                    return False
                if event["event"] == "finished":
                    start = next((item for item in events if item["event_id"] == f"{event['run_id']}:start"), None)
                    if start is None or any(start[key] != event[key] for key in ("client", "source", "plugin_version", "yt_dlp_version")):
                        raise HistoryError("Finish event does not match a recorded start")
                if len(complete) != len(previous):
                    log.seek(len(complete))
                    log.truncate()
                    os.fsync(log.fileno())
                if len(complete) + len(data) > MAX_LOG_BYTES:
                    raise HistoryError("Local history reached 16 MiB; existing entries were preserved")
                log.seek(0, os.SEEK_END)
                written = log.write(data)
                if written != len(data):
                    raise HistoryError("Local history append was incomplete")
                os.fsync(log.fileno())
                if created:
                    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                return True
        except HistoryError:
            raise
        except OSError as error:
            raise HistoryError("Could not append to local history") from error


def _validate_common(args: argparse.Namespace, event_kind: str) -> dict:
    if not VERSION_PATTERN.fullmatch(args.plugin_version):
        raise HistoryError("Plugin version must use a semantic version")
    run_id = getattr(args, "run_id", None) or _new_run_id()
    try:
        run_id = str(uuid.UUID(run_id))
    except (ValueError, AttributeError) as error:
        raise HistoryError("Run ID must be a UUID") from error
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": f"{run_id}:{'start' if event_kind == 'started' else 'finish'}",
        "run_id": run_id,
        "event": event_kind,
        "day": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "plugin_version": args.plugin_version,
        "yt_dlp_version": args.yt_dlp_version,
        "client": args.client,
        "source": args.source,
        "concurrency": args.concurrency,
    }


def _start(args: argparse.Namespace) -> int:
    if not _is_enabled():
        print('{"enabled":false}')
        return 0
    event = _validate_event(_validate_common(args, "started"))
    assert event is not None
    _append_event(event)
    print(json.dumps({"enabled": True, "run_id": event["run_id"]}, separators=(",", ":")))
    return 0


def _finish(args: argparse.Namespace) -> int:
    if not _is_enabled():
        print('{"enabled":false,"recorded":false}')
        return 0
    common = _validate_common(args, "finished")
    event = _validate_event({
        **common,
        "outcome": args.outcome,
        "failure_stage": args.failure_stage,
        "failure_class": args.failure_class,
        "retry_bucket": args.retry_bucket,
        "transfer_time_bucket": args.transfer_time_bucket,
        "media_duration_bucket": args.media_duration_bucket,
        "size_bucket": args.size_bucket,
        "resolution_bucket": args.resolution_bucket,
        "probe": args.probe,
        "decode": args.decode,
    })
    if event is None:
        raise HistoryError("Local history values failed validation")
    recorded = _append_event(event)
    print(json.dumps({"enabled": True, "recorded": recorded}, separators=(",", ":")))
    return 0


def _read_history_unlocked(path: pathlib.Path) -> list[dict]:
    if not _check_regular_file(path):
        return []
    try:
        info = path.stat()
        if info.st_size > MAX_LOG_BYTES:
            raise HistoryError("Local history exceeds the reader size limit")
        data = path.read_bytes()
    except OSError as error:
        raise HistoryError("Could not read local history") from error
    events, _ = _read_complete_events(data)
    return events


def _valid_history(path: pathlib.Path) -> list[dict]:
    with _history_lock():
        return _read_history_unlocked(path)


def _summary() -> int:
    if not _is_enabled():
        print("Local download history is disabled.")
        return 0
    with _history_lock():
        if not _is_enabled():
            print("Local download history is disabled.")
            return 0
        events = _read_history_unlocked(_history_path())
    starts = {event["run_id"]: event for event in events if event["event"] == "started"}
    finishes = {event["run_id"]: event for event in events if event["event"] == "finished"}
    completed = [
        finish for run_id, finish in finishes.items()
        if run_id in starts
        and all(starts[run_id][key] == finish[key] for key in ("client", "source", "plugin_version", "yt_dlp_version"))
    ][-SUMMARY_RUNS:]
    unclosed = sum(run_id not in finishes for run_id in starts)
    if not completed and not unclosed:
        print("Local download history is enabled; no attempts recorded yet.")
        return 0
    outcomes = collections.Counter(event["outcome"] for event in completed)
    failures = collections.Counter(
        event["failure_class"] for event in completed if event["outcome"] == "failed"
    )
    failure_stages = collections.Counter(
        event["failure_stage"] for event in completed if event["outcome"] == "failed"
    )
    concurrency = collections.Counter(
        f"{event['concurrency']}:{event['outcome']}" for event in completed
    )
    clients = collections.Counter(event["client"] for event in completed)
    versions = collections.Counter(f"{event['plugin_version']}/{event['yt_dlp_version']}" for event in completed)
    transfer_times = collections.Counter(event["transfer_time_bucket"] for event in completed)
    retries = collections.Counter(event["retry_bucket"] for event in completed)
    verification = collections.Counter(f"probe={event['probe']},decode={event['decode']}" for event in completed)
    print(f"Local history: {len(completed)} recent finished attempt(s), {unclosed} open/unclosed attempt(s).")
    print("Outcomes: " + ", ".join(f"{key}={outcomes.get(key, 0)}" for key in ("success", "failed", "cancelled")))
    if failures:
        print("Failure classes: " + ", ".join(f"{key}={value}" for key, value in sorted(failures.items())))
        print("Failure stages: " + ", ".join(f"{key}={value}" for key, value in sorted(failure_stages.items())))
    if concurrency:
        print("Concurrency/outcome: " + ", ".join(f"{key}={value}" for key, value in sorted(concurrency.items())))
    if completed:
        print("Clients: " + ", ".join(f"{key}={value}" for key, value in sorted(clients.items())))
        print("Plugin/yt-dlp versions: " + ", ".join(f"{key}={value}" for key, value in sorted(versions.items())))
        print("Transfer-time buckets: " + ", ".join(f"{key}={value}" for key, value in sorted(transfer_times.items())))
        print("Retry buckets: " + ", ".join(f"{key}={value}" for key, value in sorted(retries.items())))
        print("Verification: " + ", ".join(f"{key}={value}" for key, value in sorted(verification.items())))
    profiles: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for event in completed:
        profile = "/".join((
            event["source"], event["size_bucket"], event["media_duration_bucket"], event["resolution_bucket"],
            event["client"], event["plugin_version"], event["yt_dlp_version"],
        ))
        result = "/".join((
            event["concurrency"], event["outcome"], event["transfer_time_bucket"],
            f"probe-{event['probe']}", f"decode-{event['decode']}",
        ))
        profiles[profile][result] += 1
    comparable = []
    for profile, results in sorted(profiles.items()):
        total = sum(results.values())
        concurrency_values = {result.split("/", 1)[0] for result in results}
        if total >= 5 and len(concurrency_values) > 1:
            comparable.append((profile, results))
    if comparable:
        print("Comparable profiles (5+ attempts, multiple concurrency values):")
        for profile, results in comparable:
            print(f"  {profile}: " + ", ".join(f"{key}={count}" for key, count in sorted(results.items())))
    else:
        print("No comparable profile has five attempts across multiple concurrency values; keep tuning evidence-gated.")
    print("This summary contains only fixed categories; it has no URL, title, path, or raw error text.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)
    commands.add_parser("enable", help="Enable local history on this machine")
    commands.add_parser("disable", help="Disable future local history writes")
    commands.add_parser("status", help="Show whether local history is enabled")
    commands.add_parser("summary", help="Print a bounded, category-only summary")
    clear = commands.add_parser("clear", help="Delete local history after explicit confirmation")
    clear.add_argument("--confirm", action="store_true", help="Confirm deletion of local history")

    start = commands.add_parser("start", help="Record a safe start event and return a random run ID")
    start.add_argument("--plugin-version", required=True)
    start.add_argument("--yt-dlp-version", default="unknown")
    start.add_argument("--client", choices=sorted(CLIENTS), required=True)
    start.add_argument("--source", choices=sorted(SOURCES), required=True)
    start.add_argument("--concurrency", choices=sorted(CONCURRENCY), default="unknown")

    finish = commands.add_parser("finish", help="Record the allowlisted outcome of a run")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--plugin-version", required=True)
    finish.add_argument("--yt-dlp-version", default="unknown")
    finish.add_argument("--client", choices=sorted(CLIENTS), required=True)
    finish.add_argument("--source", choices=sorted(SOURCES), required=True)
    finish.add_argument("--concurrency", choices=sorted(CONCURRENCY), default="unknown")
    finish.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    finish.add_argument("--failure-stage", choices=sorted(FAILURE_STAGES), default="none")
    finish.add_argument("--failure-class", choices=sorted(FAILURE_CLASSES), default="none")
    finish.add_argument("--retry-bucket", choices=sorted(RETRY_BUCKETS), default="none")
    finish.add_argument("--transfer-time-bucket", choices=sorted(TRANSFER_TIME_BUCKETS), default="unknown")
    finish.add_argument("--media-duration-bucket", choices=sorted(MEDIA_DURATION_BUCKETS), default="unknown")
    finish.add_argument("--size-bucket", choices=sorted(SIZE_BUCKETS), default="unknown")
    finish.add_argument("--resolution-bucket", choices=sorted(RESOLUTION_BUCKETS), default="unknown")
    finish.add_argument("--probe", choices=sorted(CHECK_RESULTS), default="skipped")
    finish.add_argument("--decode", choices=sorted(CHECK_RESULTS), default="skipped")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "enable":
            _write_settings(True)
            print(f"Local download history enabled at {_history_path()}.")
            return 0
        if args.command == "disable":
            _write_settings(False)
            print("Local download history disabled; existing records were kept.")
            return 0
        if args.command == "status":
            print(f"{'enabled' if _is_enabled() else 'disabled'}: {_history_path()}")
            return 0
        if args.command == "summary":
            return _summary()
        if args.command == "clear":
            if not args.confirm:
                raise HistoryError("Pass --confirm to delete local history")
            path = _history_path()
            with _history_lock():
                if _check_regular_file(path):
                    path.unlink()
                    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            print("Local download history cleared.")
            return 0
        if args.command == "start":
            return _start(args)
        if args.command == "finish":
            return _finish(args)
    except HistoryError as error:
        print(f"Local history: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
