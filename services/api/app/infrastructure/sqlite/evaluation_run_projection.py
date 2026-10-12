"""Stage one Eval submission from persisted Run output and its event sequence.

Development-only; no public API or release authority. SQLite events are
mutable and are NOT a cryptographic attestation of trusted execution.
"""

from __future__ import annotations

import json
import sqlite3

from app.domain.evaluation_batch import EvalSubmission
from app.domain.evaluation_execution_binding import EvalExecutionRecord, EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest

from .evaluation_snapshot_writer import append_terminal_eval_snapshot

_MAX_EVENTS = 500
_MAX_TERMINAL_PAYLOAD_BYTES = 4096


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid terminal event payload")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("invalid terminal event payload")


def append_single_case_run_evaluation(
    connection: sqlite3.Connection,
    run_id: str,
    manifest: EvalSuiteManifest,
) -> EvalExecutionRecord:
    """Derive answer only from runs.output; require unambiguous event history.

    The caller owns the explicit transaction and must be trusted. No signature
    or immutable event check exists; this is only a local staging consistency
    boundary, never a publishing permission.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("SQLite connection required")
    if not connection.in_transaction:
        raise RuntimeError("explicit transaction required")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a nonblank string")
    if not isinstance(manifest, EvalSuiteManifest):
        raise TypeError("manifest must be EvalSuiteManifest")
    if len(manifest.cases) != 1:
        raise ValueError("only one evaluation case is supported")

    run = connection.execute(
        """SELECT r.workspace_id, r.agent_id, v.version, r.output
           FROM runs r
           JOIN agents a ON a.id=r.agent_id AND a.workspace_id=r.workspace_id
           JOIN agent_versions v ON v.id=r.agent_version_id AND v.agent_id=a.id
           WHERE r.id=? AND r.status='SUCCEEDED'""",
        (run_id,),
    ).fetchone()
    if run is None:
        raise LookupError("terminal evaluation run not found")
    if not isinstance(run["output"], str) or not run["output"].strip():
        raise ValueError("run has no final answer")

    events = connection.execute(
        """SELECT seq, type, CASE WHEN type='completed' THEN payload END AS terminal_payload
           FROM run_events WHERE run_id=? ORDER BY seq LIMIT ?""",
        (run_id, _MAX_EVENTS + 1),
    ).fetchall()
    if not events or len(events) > _MAX_EVENTS:
        raise ValueError("unverifiable run event history")
    if any(type(item["seq"]) is not int or item["seq"] != i for i, item in enumerate(events, 1)):
        raise ValueError("unverifiable run event history")
    if sum(item["type"] == "completed" for item in events) != 1:
        raise ValueError("unverifiable completion event")
    terminal = events[-1]
    if terminal["type"] != "completed":
        raise ValueError("unverifiable completion event")
    if not any(item["type"] == "model_step" for item in events[:-1]):
        raise ValueError("missing model execution evidence")
    body = terminal["terminal_payload"]
    if not isinstance(body, str):
        raise ValueError("invalid terminal event payload")
    try:
        if len(body.encode("utf-8")) > _MAX_TERMINAL_PAYLOAD_BYTES:
            raise ValueError("invalid terminal event payload")
        payload = json.loads(body, object_pairs_hook=_unique_keys, parse_constant=_reject_nonfinite)
    except (UnicodeEncodeError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid terminal event payload") from None
    if (
        not isinstance(payload, dict)
        or payload.keys() != {"reason", "quality_evaluated"}
        or payload["reason"] != "model_final_response"
        or payload["quality_evaluated"] is not False
    ):
        raise ValueError("invalid terminal event payload")

    # An event ID indicates a persisted tool_completed row, NOT tool accuracy,
    # document provenance, answer attribution, or an authenticated event source.
    evidence = tuple(
        f"run-event:{item['seq']}" for item in events[:-1] if item["type"] == "tool_completed"
    )
    record = EvalExecutionRecord(
        EvalRunScope(run["workspace_id"], run["agent_id"], run["version"], run_id),
        manifest.fingerprint,
        (EvalSubmission(manifest.cases[0].case_id, run["output"], evidence),),
    )
    append_terminal_eval_snapshot(connection, manifest, record)
    return record
