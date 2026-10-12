"""Append-only SQLite evaluation snapshots for staging, not production.

No route calls this adapter. The caller must supply a trusted database
connection and server-derived evaluation data inside an explicit transaction.
Construction of an EvalExecutionRecord does not authenticate its provenance.
"""

from __future__ import annotations

import json
import sqlite3

from app.domain.evaluation_batch import replay_eval_batch
from app.domain.evaluation_execution_binding import EvalExecutionRecord
from app.domain.evaluation_suite_integrity import EvalSuiteManifest

_MAX_JSON_BYTES = 1_048_576


def _encoded_json(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("evaluation snapshot exceeds size limit")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid evaluation snapshot Unicode") from exc
    return encoded


def append_terminal_eval_snapshot(
    connection: sqlite3.Connection,
    manifest: EvalSuiteManifest,
    record: EvalExecutionRecord,
) -> None:
    """Insert once; identity check, replay and write use caller's transaction.

    Not an ingestion endpoint, signed evidence, or release authorization.
    A future trusted worker must bind inputs to immutable Run events.
    """
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("SQLite connection required")
    if not connection.in_transaction:
        raise RuntimeError("explicit transaction required")
    if not isinstance(manifest, EvalSuiteManifest):
        raise TypeError("manifest must be EvalSuiteManifest")
    if not isinstance(record, EvalExecutionRecord):
        raise TypeError("record must be EvalExecutionRecord")
    digest = manifest.fingerprint
    if record.manifest_fingerprint != digest:
        raise ValueError("manifest fingerprint mismatch")
    replay_eval_batch(manifest, record.submissions)

    scope = record.scope
    row = connection.execute(
        """SELECT 1 FROM runs r
           JOIN agents a ON a.id=r.agent_id AND a.workspace_id=r.workspace_id
           JOIN agent_versions v ON v.id=r.agent_version_id AND v.agent_id=a.id
           WHERE r.id=? AND r.workspace_id=? AND r.agent_id=?
             AND v.version=? AND r.status IN ('SUCCEEDED', 'PARTIAL', 'FAILED')""",
        (scope.run_id, scope.workspace_id, scope.agent_id, scope.agent_version),
    ).fetchone()
    if row is None:
        raise LookupError("terminal evaluation run not found")

    manifest_json = _encoded_json(
        {
            "suite_id": manifest.suite_id,
            "version": manifest.version,
            "cases": [
                {
                    "case_id": case.case_id,
                    "expected_answer": case.expected_answer,
                    "required_evidence": list(case.required_evidence),
                }
                for case in manifest.cases
            ],
        }
    )
    submissions_json = _encoded_json(
        [
            {"case_id": item.case_id, "answer": item.answer, "evidence": list(item.evidence)}
            for item in record.submissions
        ]
    )
    try:
        connection.execute(
            """INSERT INTO evaluation_snapshots
               (run_id, workspace_id, manifest_json, submissions_json, manifest_fingerprint)
               VALUES (?, ?, ?, ?, ?)""",
            (scope.run_id, scope.workspace_id, manifest_json, submissions_json, digest),
        )
    except sqlite3.IntegrityError as exc:
        # Never replace the prior snapshot or expose SQL internals to callers.
        raise ValueError("evaluation snapshot insert rejected") from exc
