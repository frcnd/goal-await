"""Bounded, fail-closed transcript evidence for same-chat heartbeat recovery.

This module only reads Codex session JSONL files.  It does not write state, log
transcript content, call the native app, or decide that a goal is complete.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any


MAX_TAIL_BYTES = 8 * 1024 * 1024
MAX_META_BYTES = 1024 * 1024
_THREAD_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_PRIMARY_TRIGGERS = {"composer", "automation_heartbeat_scheduled", "automation_heartbeat_run_now"}
_HEARTBEAT_TRIGGERS = {"automation_heartbeat_scheduled", "automation_heartbeat_run_now"}
_MAX_OUTPUT_MESSAGE = "Incomplete response returned, reason: max_output_tokens"
_STREAM_MESSAGES = {
    "stream disconnected before completion",
    "response stream disconnected before completion",
}


def _thread(value: Any) -> str:
    if not isinstance(value, str) or not _THREAD_ID.fullmatch(value):
        raise ValueError("a real thread id is required")
    return value.lower()


def _workspace_key(value: Any) -> str:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError("a workspace path is required")
    path = os.path.abspath(os.path.expanduser(os.fspath(value)))
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.realpath(path))


def _object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label} schema")
    return value


def _metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.readline(MAX_META_BYTES + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_META_BYTES:
        raise ValueError("session metadata is missing or incomplete")
    record = _object(raw, "session metadata")
    payload = record.get("payload")
    if record.get("type") != "session_meta" or not isinstance(payload, dict):
        raise ValueError("session metadata schema is unsupported")
    return payload


def _tail(path: Path) -> list[dict[str, Any]]:
    before = path.stat()
    size = before.st_size
    if size <= 0:
        raise ValueError("session transcript is empty")
    with path.open("rb") as stream:
        start = max(0, size - MAX_TAIL_BYTES)
        aligned = start == 0
        if not aligned:
            stream.seek(start - 1)
            aligned = stream.read(1) == b"\n"
        stream.seek(start)
        raw = stream.read(MAX_TAIL_BYTES)
    after = path.stat()
    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise ValueError("transcript changed while evidence was read")
    if not raw.endswith(b"\n"):
        raise ValueError("transcript ends with partial final JSON")
    if not aligned:
        boundary = raw.find(b"\n")
        if boundary < 0:
            raise ValueError("bounded transcript tail has no complete event")
        raw = raw[boundary + 1 :]
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line:
            raise ValueError("transcript contains an empty event")
        records.append(_object(line, "transcript event"))
    if not records:
        raise ValueError("bounded transcript tail has no complete event")
    return records


def _check_meta(meta: dict[str, Any], thread_id: str, workspace: str) -> None:
    if str(meta.get("id", "")).lower() != thread_id:
        raise ValueError("session metadata belongs to a different chat")
    if _workspace_key(meta.get("cwd")) != workspace:
        raise ValueError("session metadata belongs to a different workspace")
    if not isinstance(meta.get("cli_version"), str) or not meta["cli_version"].strip():
        raise ValueError("session metadata has no CLI version")


def _locate(thread_id: str, codex_home: Any) -> Path:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    root = home / "sessions"
    if not root.is_dir():
        raise ValueError("Codex sessions directory was not found")
    suffix = re.compile(r"(?:^|[-_])" + re.escape(thread_id) + r"\.jsonl$")
    matches = sorted(
        path for path in root.rglob(f"*{thread_id}.jsonl")
        if path.is_file() and suffix.search(path.name.lower())
    )
    if not matches:
        raise ValueError("no transcript matches this chat")
    if len(matches) != 1:
        raise ValueError("multiple transcripts match this chat")
    return matches[0].resolve()


def _payload(record: dict[str, Any]) -> dict[str, Any] | None:
    value = record.get("payload")
    return value if isinstance(value, dict) else None


def _trigger(payload: dict[str, Any]) -> str | None:
    raw = payload.get("turn_trigger")
    if isinstance(raw, str):
        value = raw.strip()
        if value:
            return value
    if isinstance(raw, dict):
        for key in ("type", "kind", "name"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    attribution = payload.get("turn_attribution")
    if isinstance(attribution, dict):
        value = attribution.get("turn_trigger")
        if isinstance(value, str):
            return value.strip() or None
    return None


def _events(events: list[dict[str, Any]], kind: str) -> list[tuple[int, dict[str, Any]]]:
    found = []
    for index, record in enumerate(events):
        payload = _payload(record)
        if payload is not None and (
            payload.get("type") == kind or record.get("type") == kind
        ):
            found.append((index, payload))
    return found


def _primary_starts(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], str | None]]:
    starts = []
    for index, payload in _events(events, "task_started"):
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("task_started has no turn id")
        trigger = _trigger(payload)
        if trigger == "guardian_review":
            continue
        starts.append((index, payload, trigger))
    return starts


def _context(
    events: list[dict[str, Any]], turn_id: str, *, after: int, before: int,
) -> dict[str, Any] | None:
    found = None
    for index, payload in _events(events, "turn_context"):
        if index <= after or index >= before:
            continue
        if payload.get("turn_id") == turn_id:
            found = payload
    return found


def _event_time(record: dict[str, Any], payload: dict[str, Any]) -> float | None:
    value: Any = payload.get("completed_at")
    if value is None:
        value = payload.get("completed_at_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) / 1000
    if value is None:
        value = record.get("timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def _since(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    return None


def _error_message(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
    elif isinstance(error, str):
        message = error
    else:
        message = None
    return message if isinstance(message, str) and message else None


def _failure_kind(message: str) -> str | None:
    folded = message.casefold()
    if folded == _MAX_OUTPUT_MESSAGE.casefold():
        return "max_output_tokens"
    if folded in {value.casefold() for value in _STREAM_MESSAGES}:
        return "stream_disconnected_before_completion"
    return None


def _text_items(value: Any) -> list[str]:
    texts = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return texts


def _current_input(events: list[dict[str, Any]], start: int, turn_id: str) -> list[str]:
    explicit: list[str] = []
    fallback: list[str] = []
    for index, record in enumerate(events):
        if index <= start:
            continue
        payload = _payload(record)
        if payload is None:
            continue
        if record.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
            meta = payload.get("internal_chat_message_metadata_passthrough")
            if isinstance(meta, dict) and meta.get("turn_id") == turn_id:
                content = payload.get("content")
                item_meta = meta.get("content_item_metadata")
                if isinstance(content, list) and isinstance(item_meta, list):
                    for position, item in enumerate(content):
                        if position >= len(item_meta) or not isinstance(item, dict):
                            continue
                        provenance = item_meta[position]
                        if not isinstance(provenance, dict):
                            continue
                        source = provenance.get("provenance")
                        if isinstance(source, dict) and source.get("type") == "user":
                            text = item.get("text")
                            if isinstance(text, str):
                                explicit.append(text)
        if payload.get("type") == "item_completed" and payload.get("turn_id") == turn_id:
            item = payload.get("item")
            if isinstance(item, dict) and str(item.get("type", "")).casefold() == "usermessage":
                fallback.extend(_text_items(item.get("content")))
    return explicit if explicit else fallback


def _aborted(events: list[dict[str, Any]], start: int, turn_id: str) -> bool:
    for index, record in enumerate(events):
        if index <= start:
            continue
        payload = _payload(record)
        if payload is None:
            continue
        meta = payload.get("internal_chat_message_metadata_passthrough")
        event_turn = payload.get("turn_id")
        if not isinstance(event_turn, str) and isinstance(meta, dict):
            event_turn = meta.get("turn_id")
        if event_turn != turn_id:
            continue
        kind = str(payload.get("type", "")).casefold()
        if any(word in kind for word in ("abort", "interrupt", "cancel")):
            return True
        if isinstance(meta, dict):
            kinds = meta.get("content_item_kinds")
            if isinstance(kinds, list) and any(
                isinstance(value, str) and any(word in value.casefold() for word in ("abort", "interrupt", "cancel"))
                for value in kinds
            ):
                return True
    return False


def capture(thread_id: str, workspace: Any, codex_home: Any = None) -> dict[str, Any]:
    """Pin the active user-facing turn and model from one exact transcript."""
    thread_id = _thread(thread_id)
    workspace_key = _workspace_key(workspace)
    path = _locate(thread_id, codex_home)
    meta = _metadata(path)
    _check_meta(meta, thread_id, workspace_key)
    events = _tail(path)
    starts = _primary_starts(events)
    if not starts:
        raise ValueError("transcript has no supported active turn")
    start_index, start, trigger = starts[-1]
    if trigger not in _PRIMARY_TRIGGERS:
        raise ValueError("latest turn trigger is unsupported")
    if any(payload.get("turn_id") == start["turn_id"] for _, payload in _events(events, "task_complete")):
        raise ValueError("transcript has no currently active turn")
    context = _context(events, start["turn_id"], after=start_index, before=len(events))
    if not context or not isinstance(context.get("model"), str) or not context["model"].strip():
        raise ValueError("active turn context has no model")
    if context.get("cwd") is not None and _workspace_key(context["cwd"]) != workspace_key:
        raise ValueError("active turn context belongs to a different workspace")
    return {
        "transcript_path": str(path),
        "model": context["model"],
        "turn_id": start["turn_id"],
        "cli_version": meta["cli_version"],
        "interrupted": _aborted(events, start_index, start["turn_id"]),
        "prior_interrupted": (trigger in _HEARTBEAT_TRIGGERS and len(starts) > 1
                              and _aborted(events, starts[-2][0], starts[-2][1]["turn_id"])),
    }


def _no(reason: str, **detail: Any) -> dict[str, Any]:
    return {"eligible": False, "reason": reason, **detail}


def inspect(
    snapshot: dict[str, Any], *, thread_id: str, workspace: Any,
    automation_id: str, since: Any,
) -> dict[str, Any]:
    """Return true only when the transcript proves this heartbeat continues a
    pinned model after one recognized recoverable failure since ``since``.
    """
    try:
        thread_id = _thread(thread_id)
        workspace_key = _workspace_key(workspace)
        if not isinstance(snapshot, dict):
            return _no("evidence snapshot is invalid")
        path_value = snapshot.get("transcript_path")
        pinned_model = snapshot.get("model")
        snapshot_turn = snapshot.get("turn_id")
        if not isinstance(path_value, str) or not path_value:
            return _no("evidence snapshot has no transcript path")
        if not isinstance(pinned_model, str) or not pinned_model.strip():
            return _no("evidence snapshot has no pinned model")
        if not isinstance(snapshot_turn, str) or not snapshot_turn:
            return _no("evidence snapshot has no pinned turn")
        if not isinstance(automation_id, str) or not automation_id:
            return _no("automation id is required")
        since_time = _since(since)
        if since_time is None:
            return _no("recovery cutoff is invalid")
        path = Path(path_value)
        if not path.is_file():
            return _no("pinned transcript is missing")
        meta = _metadata(path)
        _check_meta(meta, thread_id, workspace_key)
        events = _tail(path)
        for record in events:
            payload = _payload(record)
            if payload and payload.get("thread_id") not in (None, thread_id):
                return _no("transcript contains evidence for a different chat")
        starts = _primary_starts(events)
    except (OSError, ValueError):
        return _no("transcript evidence is missing or malformed")

    if not starts:
        return _no("transcript has no user-facing turn")
    current_index, current, trigger = starts[-1]
    current_turn = current["turn_id"]
    if trigger not in _HEARTBEAT_TRIGGERS:
        return _no("latest turn was not started by a goal heartbeat", current_turn_id=current_turn)

    completions = _events(events, "task_complete")
    current_completions = [(index, value) for index, value in completions if value.get("turn_id") == current_turn]
    if current_completions:
        return _no("current heartbeat turn already completed", current_turn_id=current_turn)
    if _aborted(events, current_index, current_turn):
        return _no("current heartbeat turn was interrupted", current_turn_id=current_turn)

    current_context = _context(events, current_turn, after=current_index, before=len(events))
    if not current_context or not isinstance(current_context.get("model"), str):
        return _no("current turn context is missing", current_turn_id=current_turn)
    if current_context["model"] != pinned_model:
        return _no("current turn model does not match the pinned model", current_turn_id=current_turn)
    if current_context.get("cwd") is None or _workspace_key(current_context.get("cwd")) != workspace_key:
        return _no("current turn context belongs to a different workspace", current_turn_id=current_turn)

    current_input = _current_input(events, current_index, current_turn)
    if not current_input:
        return _no("current heartbeat user input is missing", current_turn_id=current_turn)
    marker = f"<automation_id>{automation_id}</automation_id>"
    if marker not in "\n".join(current_input):
        return _no("current user input does not match the automation", current_turn_id=current_turn)

    if len(starts) < 2:
        return _no("prior turn is missing", current_turn_id=current_turn)
    previous_index, previous, _ = starts[-2]
    previous_turn = previous["turn_id"]
    if _aborted(events, previous_index, previous_turn):
        return _no("prior turn was interrupted", current_turn_id=current_turn)
    previous_completions = [
        (index, value) for index, value in completions
        if value.get("turn_id") == previous_turn and previous_index < index < current_index
    ]
    if len(previous_completions) != 1:
        return _no("prior turn has no unique terminal completion", current_turn_id=current_turn)
    previous_completion_index, previous_completion = previous_completions[0]
    previous_context = _context(
        events, previous_turn, after=previous_index, before=previous_completion_index,
    )
    if not previous_context or previous_context.get("model") != pinned_model:
        return _no("prior turn model does not match the pinned model", current_turn_id=current_turn)
    if previous_context.get("cwd") is None or _workspace_key(previous_context.get("cwd")) != workspace_key:
        return _no("prior turn context belongs to a different workspace", current_turn_id=current_turn)
    completed_at = _event_time(events[previous_completion_index], previous_completion)
    if completed_at is None:
        return _no("prior completion time is missing", current_turn_id=current_turn)
    if completed_at < since_time:
        return _no("prior failure predates the recovery cutoff", current_turn_id=current_turn)
    message = _error_message(previous_completion)
    failure_kind = _failure_kind(message) if message else None
    if failure_kind is None:
        return _no("prior terminal error is not recoverable", current_turn_id=current_turn)
    return {
        "eligible": True,
        "reason": "recoverable prior turn and matching heartbeat",
        "current_turn_id": current_turn,
        "failed_turn_id": previous_turn,
        "failure_kind": failure_kind,
        "model": pinned_model,
    }
