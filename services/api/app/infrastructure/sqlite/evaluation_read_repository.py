"""SQLite development-only, scoped reader for persisted evaluation snapshots.

This adapter does not create tables or write data. Deployment migrations,
snapshot ingestion, authenticated HTTP, PG RLS, and publishing remain separate
work. The snapshot table is assumed to be populated by a trusted server flow.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from app.application.evaluation_read_service import AuthoritativeEvalSnapshot
from app.domain.evaluation import EvalCase
from app.domain.evaluation_batch import EvalSubmission, replay_eval_batch
from app.domain.evaluation_execution_binding import EvalExecutionRecord, EvalRunScope
from app.domain.evaluation_suite_integrity import EvalSuiteManifest

from .connection import connect

_READ_ROLES = frozenset({"owner", "admin", "member", "viewer"})
_MAX_JSON_BYTES = 1_048_576


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_json_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_nonfinite_json(value)
    return parsed


def _decode(text: object) -> object:
    if not isinstance(text, str):
        raise ValueError("invalid snapshot payload size")
    try:
        if len(text.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError("invalid snapshot payload size")
        decoded = json.loads(
            text,
            object_pairs_hook=_unique_json_fields,
            parse_constant=_reject_nonfinite_json,
            parse_float=_finite_json_float,
        )
        # JSON escapes can decode to surrogates even when the source is valid UTF-8.
        pending = [decoded]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                value.encode("utf-8")
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return decoded
    except UnicodeEncodeError as exc:
        raise ValueError("invalid snapshot Unicode") from exc
    except RecursionError as exc:
        # Some Python JSON decoders raise this on excessive nesting.
        raise ValueError("snapshot nesting too deep") from exc


def _shape(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError("invalid persisted evaluation shape")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return tuple(value)


def _manifest(text: object) -> EvalSuiteManifest:
    raw = _shape(_decode(text), {"suite_id", "version", "cases"})
    if not isinstance(raw["cases"], list):
        raise ValueError("cases must be a JSON array")
    cases = tuple(
        EvalCase(
            **{
                "case_id": entry["case_id"],
                "expected_answer": entry["expected_answer"],
                "required_evidence": _strings(entry["required_evidence"]),
            }
        )
        for value in raw["cases"]
        for entry in [_shape(value, {"case_id", "expected_answer", "required_evidence"})]
    )
    return EvalSuiteManifest(raw["suite_id"], raw["version"], cases)


def _submissions(text: object) -> tuple[EvalSubmission, ...]:
    raw = _decode(text)
    if not isinstance(raw, list):
        raise ValueError("submissions must be a JSON array")
    return tuple(
        EvalSubmission(entry["case_id"], entry["answer"], _strings(entry["evidence"]))
        for value in raw
        for entry in [_shape(value, {"case_id", "answer", "evidence"})]
    )


class SqliteEvalReadStore:
    """S2 staging adapter implementing EvalReadStore without any write methods.

    The connection factory must be server-owned and return a fresh connection
    for each call. The second membership check and all snapshot joins execute
    inside one SQLite BEGIN read transaction. Not a PostgreSQL/RLS adapter.
    """

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]] = connect,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory

    def member_role(self, authenticated_user_id: str, workspace_id: str) -> str | None:
        if not _nonblank(authenticated_user_id) or not _nonblank(workspace_id):
            return None
        with self._connection_factory() as connection:
            row = connection.execute(
                """SELECT role FROM workspace_members
                   WHERE user_id=? AND workspace_id=?""",
                (authenticated_user_id, workspace_id),
            ).fetchone()
        role = row["role"] if row else None
        return role if role in _READ_ROLES else None

    def load_snapshot(
        self, authenticated_user_id: str, workspace_id: str, run_id: str
    ) -> AuthoritativeEvalSnapshot | None:
        if not all(_nonblank(value) for value in (authenticated_user_id, workspace_id, run_id)):
            return None
        with self._connection_factory() as connection:
            connection.execute("BEGIN")
            # Establish the transaction snapshot with a fresh membership read.
            member = connection.execute(
                """SELECT role FROM workspace_members
                   WHERE user_id=? AND workspace_id=?""",
                (authenticated_user_id, workspace_id),
            ).fetchone()
            if member is None or member["role"] not in _READ_ROLES:
                return None
            row = connection.execute(
                """SELECT r.id AS run_id, r.workspace_id, r.agent_id, v.version,
                          e.manifest_json, e.submissions_json, e.manifest_fingerprint
                   FROM evaluation_snapshots e
                   JOIN runs r ON r.id=e.run_id AND r.workspace_id=e.workspace_id
                   JOIN agents a ON a.id=r.agent_id AND a.workspace_id=r.workspace_id
                   JOIN agent_versions v
                     ON v.id=r.agent_version_id AND v.agent_id=a.id
                   WHERE r.id=? AND r.workspace_id=?
                     AND r.status IN ('SUCCEEDED', 'PARTIAL', 'FAILED')""",
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                return None
            try:
                scope = EvalRunScope(
                    row["workspace_id"], row["agent_id"], row["version"], row["run_id"]
                )
                manifest = _manifest(row["manifest_json"])
                fingerprint = row["manifest_fingerprint"]
                if not isinstance(fingerprint, str) or fingerprint != manifest.fingerprint:
                    raise ValueError("suite fingerprint mismatch")
                submissions = _submissions(row["submissions_json"])
                record = EvalExecutionRecord(scope, fingerprint, submissions)
                replay_eval_batch(manifest, submissions)
                return AuthoritativeEvalSnapshot(scope, manifest, record)
            except (TypeError, ValueError, KeyError) as exc:
                # Fail closed; never expose invalid persisted contents as evidence.
                raise LookupError("evaluation run not found") from exc
