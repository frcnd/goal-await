"""Offline evidence tests using compact .167.0-shaped JSONL fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
TEST_WORK = REPO / ".test-work"
TEST_WORK.mkdir(exist_ok=True)
SCRIPT = REPO / "skill/goal-await/scripts/recovery_evidence.py"
CHAT = "11111111-1111-1111-1111-111111111111"
MODEL = "pinned/model"
MAX_OUTPUT = "Incomplete response returned, reason: max_output_tokens"

spec = importlib.util.spec_from_file_location("recovery_evidence", SCRIPT)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class RecoveryEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_WORK)
        self.root = Path(self.temp.name)
        self.home = self.root / ".codex"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.session = self.home / "sessions/2026/11/07" / f"rollout-now-{CHAT}.jsonl"
        self.session.parent.mkdir(parents=True)
        self.records = [
            self.record("session_meta", {
                "id": CHAT, "cwd": str(self.workspace), "cli_version": "0.167.0-alpha.3.2",
            }, "2026-11-07T00:00:00Z"),
        ]
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def record(kind, payload, timestamp="2026-11-07T00:00:10Z"):
        return {"timestamp": timestamp, "type": kind, "payload": payload}

    def write(self, partial=None):
        with self.session.open("w", encoding="utf-8", newline="\n") as stream:
            for item in self.records:
                stream.write(json.dumps(item, separators=(",", ":")) + "\n")
            if partial is not None:
                stream.write(partial)

    def start(self, turn, trigger, *, attribution=False):
        payload = {"type": "task_started", "turn_id": turn, "started_at": 1000}
        if attribution:
            payload["turn_attribution"] = {"turn_id": turn, "turn_trigger": trigger}
        else:
            payload["turn_trigger"] = trigger
        self.records.append(self.record("event_msg", payload))

    def context(self, turn, model=MODEL):
        self.records.append(self.record("turn_context", {
            "turn_id": turn, "model": model, "cwd": str(self.workspace),
        }))

    def complete(self, turn, error=None, completed_at=1010):
        payload = {"type": "task_complete", "turn_id": turn, "completed_at": completed_at}
        if error is not None:
            payload["error"] = {"message": error, "codex_error_info": "other"}
        self.records.append(self.record("event_msg", payload, "2026-11-07T00:00:15Z"))

    def user(self, turn, text, *, event_only=False):
        meta = {
            "turn_id": turn,
            "content_item_kinds": ["user.text"],
            "content_item_metadata": [{"provenance": {"type": "user"}}],
        }
        if not event_only:
            self.records.append(self.record("response_item", {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
                "internal_chat_message_metadata_passthrough": meta,
            }))
        self.records.append(self.record("event_msg", {
            "type": "item_completed", "turn_id": turn,
            "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]},
        }))

    def heartbeat(self, *, error=MAX_OUTPUT, attribution=False, event_only=False,
                  trigger="automation_heartbeat_scheduled"):
        self.start("old-turn", "composer")
        self.context("old-turn")
        self.write()
        snapshot = evidence.capture(CHAT, self.workspace, self.home)
        self.complete("old-turn", error)
        self.start("heartbeat", trigger, attribution=attribution)
        self.context("heartbeat")
        self.user("heartbeat", "Continue <automation_id>auto-1</automation_id>", event_only=event_only)
        self.write()
        return snapshot

    def inspect(self, snapshot, since=1000):
        return evidence.inspect(snapshot, thread_id=CHAT, workspace=self.workspace,
                                automation_id="auto-1", since=since)

    def test_capture_pins_active_turn_and_ignores_nested_review(self):
        self.start("main", "composer")
        self.context("main")
        self.start("review", "guardian_review")
        self.context("review", "codex-auto-review")
        self.write()
        snapshot = evidence.capture(CHAT, self.workspace, self.home)
        self.assertEqual(snapshot["turn_id"], "main")
        self.assertEqual(snapshot["model"], MODEL)
        self.assertEqual(snapshot["cli_version"], "0.167.0-alpha.3.2")
        self.assertEqual(Path(snapshot["transcript_path"]), self.session.resolve())

    def test_eligible_max_output_tokens(self):
        result = self.inspect(self.heartbeat())
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["failed_turn_id"], "old-turn")
        self.assertEqual(result["failure_kind"], "max_output_tokens")
        self.assertEqual(result["model"], MODEL)

    def test_known_stream_failure_is_eligible_case_insensitively(self):
        snapshot = self.heartbeat(
            error="RESPONSE STREAM DISCONNECTED BEFORE COMPLETION", attribution=True, event_only=True,
        )
        result = self.inspect(snapshot)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["failure_kind"], "stream_disconnected_before_completion")

    def test_run_now_heartbeat_trigger_is_eligible(self):
        snapshot = self.heartbeat(
            attribution=True, event_only=True, trigger="automation_heartbeat_run_now",
        )
        result = self.inspect(snapshot)
        self.assertTrue(result["eligible"], result)

    def test_current_completion_race_is_rejected(self):
        snapshot = self.heartbeat()
        self.complete("heartbeat")
        self.write()
        result = self.inspect(snapshot)
        self.assertFalse(result["eligible"])
        self.assertIn("already completed", result["reason"])

    def test_new_manual_turn_is_rejected(self):
        snapshot = self.heartbeat()
        self.start("manual", "composer")
        self.context("manual")
        self.write()
        result = self.inspect(snapshot)
        self.assertFalse(result["eligible"])
        self.assertIn("heartbeat", result["reason"])

    def test_interrupted_prior_turn_is_rejected_despite_recoverable_error(self):
        snapshot = self.heartbeat()
        self.records.insert(4, self.record("response_item", {
            "type": "message", "role": "user", "content": [],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "old-turn", "content_item_kinds": ["generic.turn_aborted"],
            },
        }))
        self.write()
        self.assertIn("prior turn was interrupted", self.inspect(snapshot)["reason"])

    def test_interruption_and_model_mismatch_are_rejected(self):
        snapshot = self.heartbeat()
        self.records.append(self.record("response_item", {
            "type": "message", "role": "user", "content": [],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "heartbeat", "content_item_kinds": ["generic.turn_aborted"],
            },
        }))
        self.write()
        self.assertIn("interrupted", self.inspect(snapshot)["reason"])

        self.records = self.records[:6] + [self.record("turn_context", {
            "turn_id": "heartbeat", "model": "different/model", "cwd": str(self.workspace),
        })] + self.records[7:-1]
        self.write()
        self.assertIn("model", self.inspect(snapshot)["reason"])

    def test_unknown_error_and_old_completion_are_rejected(self):
        snapshot = self.heartbeat(error="rate limit exceeded")
        self.assertIn("not recoverable", self.inspect(snapshot)["reason"])

        self.records.clear()
        self.records.append(self.record("session_meta", {
            "id": CHAT, "cwd": str(self.workspace), "cli_version": "0.167.0-alpha.3.2",
        }))
        snapshot = self.heartbeat()
        self.assertIn("predates", self.inspect(snapshot, since=2000)["reason"])

    def test_missing_marker_partial_tail_and_foreign_meta_fail_closed(self):
        snapshot = self.heartbeat()
        self.write(partial='{"timestamp":"2026-11-07T00:01:00Z","type":')
        self.assertIn("missing or malformed", self.inspect(snapshot)["reason"])

        self.records.clear()
        self.records.append(self.record("session_meta", {
            "id": CHAT, "cwd": str(self.workspace), "cli_version": "0.167.0-alpha.3.2",
        }))
        self.start("old-turn", "composer")
        self.context("old-turn")
        self.complete("old-turn", MAX_OUTPUT)
        self.start("heartbeat", "automation_heartbeat_scheduled")
        self.context("heartbeat")
        self.user("heartbeat", "Continue <automation_id>other</automation_id>")
        self.write()
        snapshot = evidence.capture(CHAT, self.workspace, self.home)
        self.assertIn("does not match", self.inspect(snapshot)["reason"])

        self.records[0]["payload"]["cwd"] = str(self.root)
        self.write()
        with self.assertRaises(ValueError):
            evidence.capture(CHAT, self.workspace, self.home)

    def test_filename_substring_does_not_match_thread(self):
        wrong = self.home / "sessions/2026/11/07" / f"rollout-extra-{CHAT}-suffix.jsonl"
        wrong.write_text(json.dumps(self.records[0]) + "\n", encoding="utf-8")
        self.session.unlink()
        with self.assertRaises(ValueError):
            evidence.capture(CHAT, self.workspace, self.home)


if __name__ == "__main__":
    unittest.main()
