"""SQLite implementation of :class:`InvestigationRepository`."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.image import ReferenceImage
from app.domain.investigation import (
    Investigation,
    InvestigationResult,
    InvestigationStatus,
    Note,
    ProgressEvent,
)
from app.infrastructure.persistence.repository import InvestigationRepository

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    hints_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    fingerprint TEXT,
    error TEXT,
    pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS investigation_results (
    investigation_id TEXT PRIMARY KEY REFERENCES investigations(id) ON DELETE CASCADE,
    completed_at TEXT,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reference_images (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    stored_path TEXT,
    embeddings_json TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    level TEXT NOT NULL,
    score REAL NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (investigation_id, id)
);
CREATE TABLE IF NOT EXISTS evidence (
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL,
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    strength TEXT NOT NULL,
    source_url TEXT,
    data_json TEXT NOT NULL,
    PRIMARY KEY (investigation_id, candidate_id, id)
);
CREATE TABLE IF NOT EXISTS provider_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    result_count INTEGER NOT NULL,
    cache_hit INTEGER NOT NULL,
    error TEXT,
    detail TEXT,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (investigation_id, seq)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (investigation_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_inv_created ON investigations(created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(investigation_id, type);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteInvestigationRepository(InvestigationRepository):
    def __init__(self, path: Path | str):
        self._lock = threading.RLock()
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            if target != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            self._conn.commit()
        if target != ":memory:":
            try:
                Path(target).chmod(0o600)
            except OSError:  # pragma: no cover
                pass

    # ------------------------------------------------------------------ helpers
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _row_to_investigation(self, row: sqlite3.Row, *, with_result: bool) -> Investigation:
        inv_id = row["id"]
        inv = Investigation(
            id=inv_id,
            status=InvestigationStatus(row["status"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            hints=IdentityHints.model_validate_json(row["hints_json"]),
            options=InvestigationOptions.model_validate_json(row["options_json"]),
            fingerprint=row["fingerprint"],
            error=row["error"],
            pinned=bool(row["pinned"]),
        )
        inv.reference_images = self.get_reference_images(inv_id)
        inv.tags = [r["tag"] for r in self._query("SELECT tag FROM tags WHERE investigation_id=? ORDER BY tag", (inv_id,))]
        inv.notes = [
            Note(id=r["id"], text=r["text"], created_at=_dt(r["created_at"]))
            for r in self._query("SELECT * FROM notes WHERE investigation_id=? ORDER BY id", (inv_id,))
        ]
        if with_result:
            inv.result = self.get_result(inv_id)
        return inv

    # ------------------------------------------------------------ investigations
    def create(self, investigation: Investigation) -> None:
        self._exec(
            "INSERT INTO investigations (id, status, title, created_at, updated_at, hints_json, options_json,"
            " fingerprint, error, pinned) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                investigation.id,
                investigation.status.value,
                investigation.title,
                investigation.created_at.isoformat(),
                investigation.updated_at.isoformat(),
                investigation.hints.model_dump_json(),
                investigation.options.model_dump_json(),
                investigation.fingerprint,
                investigation.error,
                int(investigation.pinned),
            ),
        )

    def get(self, investigation_id: str, *, with_result: bool = True) -> Investigation | None:
        rows = self._query("SELECT * FROM investigations WHERE id=?", (investigation_id,))
        return self._row_to_investigation(rows[0], with_result=with_result) if rows else None

    def list_investigations(self, *, limit: int = 50, tag: str | None = None, query: str | None = None) -> list[Investigation]:
        sql = "SELECT i.* FROM investigations i"
        params: list[object] = []
        where = []
        if tag:
            sql += " JOIN tags t ON t.investigation_id = i.id"
            where.append("t.tag = ?")
            params.append(tag.lower())
        if query:
            where.append("(i.title LIKE ? OR i.hints_json LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY i.pinned DESC, i.created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [self._row_to_investigation(r, with_result=False) for r in self._query(sql, tuple(params))]

    def update_meta(self, investigation: Investigation) -> None:
        self._exec(
            "UPDATE investigations SET status=?, title=?, updated_at=?, hints_json=?, options_json=?, fingerprint=?,"
            " error=?, pinned=? WHERE id=?",
            (
                investigation.status.value,
                investigation.title,
                _now(),
                investigation.hints.model_dump_json(),
                investigation.options.model_dump_json(),
                investigation.fingerprint,
                investigation.error,
                int(investigation.pinned),
                investigation.id,
            ),
        )

    def set_status(self, investigation_id: str, status: InvestigationStatus, error: str | None = None) -> None:
        self._exec(
            "UPDATE investigations SET status=?, error=?, updated_at=? WHERE id=?",
            (status.value, error, _now(), investigation_id),
        )

    def delete(self, investigation_id: str) -> bool:
        return self._exec("DELETE FROM investigations WHERE id=?", (investigation_id,)).rowcount > 0

    def mark_interrupted(self) -> int:
        return self._exec(
            "UPDATE investigations SET status=?, error=?, updated_at=? WHERE status IN (?, ?)",
            (
                InvestigationStatus.INTERRUPTED.value,
                "The application stopped while this investigation was running.",
                _now(),
                InvestigationStatus.QUEUED.value,
                InvestigationStatus.RUNNING.value,
            ),
        ).rowcount

    def candidate_summary(self, investigation_id: str) -> tuple[int, str | None, str | None]:
        rows = self._query(
            "SELECT display_name, level, (SELECT COUNT(*) FROM candidates WHERE investigation_id=?) AS n "
            "FROM candidates WHERE investigation_id=? ORDER BY rank LIMIT 1", (investigation_id, investigation_id)
        )
        if not rows:
            return 0, None, None
        return int(rows[0]["n"]), rows[0]["display_name"], rows[0]["level"]

    # --------------------------------------------------------------------- results
    def save_result(self, result: InvestigationResult) -> None:
        inv_id = result.investigation_id
        with self._lock:
            conn = self._conn
            conn.execute(
                "INSERT OR REPLACE INTO investigation_results (investigation_id, completed_at, result_json) VALUES (?,?,?)",
                (inv_id, result.completed_at.isoformat() if result.completed_at else None, result.model_dump_json()),
            )
            conn.execute("DELETE FROM candidates WHERE investigation_id=?", (inv_id,))
            conn.execute("DELETE FROM evidence WHERE investigation_id=?", (inv_id,))
            conn.execute("DELETE FROM provider_runs WHERE investigation_id=?", (inv_id,))
            for cand in result.candidates:
                conn.execute(
                    "INSERT INTO candidates (investigation_id, id, rank, display_name, level, score, data_json)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (inv_id, cand.id, cand.rank, cand.display_name, cand.assessment.level.value,
                     cand.assessment.score, cand.model_dump_json()),
                )
                for ev in cand.evidence:
                    conn.execute(
                        "INSERT OR REPLACE INTO evidence (investigation_id, candidate_id, id, type, strength, source_url,"
                        " data_json) VALUES (?,?,?,?,?,?,?)",
                        (inv_id, cand.id, ev.id, ev.type.value, ev.strength.value, ev.source_url, ev.model_dump_json()),
                    )
            for run in result.provider_runs:
                conn.execute(
                    "INSERT INTO provider_runs (investigation_id, provider, stage, outcome, duration_ms, result_count,"
                    " cache_hit, error, detail, started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (inv_id, run.provider, run.stage, run.outcome.value, run.duration_ms, run.result_count,
                     int(run.cache_hit), run.error, run.detail, run.started_at.isoformat()),
                )
            conn.commit()

    def get_result(self, investigation_id: str) -> InvestigationResult | None:
        rows = self._query("SELECT result_json FROM investigation_results WHERE investigation_id=?", (investigation_id,))
        return InvestigationResult.model_validate_json(rows[0]["result_json"]) if rows else None

    # ------------------------------------------------------------ reference images
    def save_reference_image(self, image: ReferenceImage) -> None:
        embeddings = {f.id: f.embedding for f in image.faces if f.embedding is not None}
        existing = self._query("SELECT embeddings_json FROM reference_images WHERE id=?", (image.id,))
        if not embeddings and existing and existing[0]["embeddings_json"]:
            embeddings_json = existing[0]["embeddings_json"]  # metadata-only update keeps embeddings
        else:
            embeddings_json = json.dumps(embeddings) if embeddings else None
        self._exec(
            "INSERT OR REPLACE INTO reference_images (id, investigation_id, created_at, meta_json, stored_path,"
            " embeddings_json) VALUES (?,?,?,?,?,?)",
            (image.id, image.investigation_id, image.created_at.isoformat(), image.model_dump_json(),
             image.stored_path, embeddings_json),
        )

    def _row_to_reference(self, row: sqlite3.Row, with_embeddings: bool) -> ReferenceImage:
        ref = ReferenceImage.model_validate_json(row["meta_json"])
        ref.created_at = _dt(row["created_at"]) or ref.created_at  # the column is authoritative
        ref.stored_path = row["stored_path"]
        ref.image_available = bool(row["stored_path"])
        if with_embeddings and row["embeddings_json"]:
            embeddings = json.loads(row["embeddings_json"])
            for face in ref.faces:
                face.embedding = embeddings.get(face.id)
        return ref

    def get_reference_images(self, investigation_id: str, *, with_embeddings: bool = False) -> list[ReferenceImage]:
        rows = self._query(
            "SELECT * FROM reference_images WHERE investigation_id=? ORDER BY created_at", (investigation_id,)
        )
        return [self._row_to_reference(r, with_embeddings) for r in rows]

    def delete_reference_image(self, investigation_id: str, image_id: str) -> ReferenceImage | None:
        rows = self._query(
            "SELECT * FROM reference_images WHERE id=? AND investigation_id=?", (image_id, investigation_id)
        )
        if not rows:
            return None
        ref = self._row_to_reference(rows[0], False)
        self._exec("DELETE FROM reference_images WHERE id=?", (image_id,))
        return ref

    # ---------------------------------------------------------------------- events
    def append_event(self, event: ProgressEvent) -> ProgressEvent:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE investigation_id=?", (event.investigation_id,)
            ).fetchone()
            event.seq = int(row[0])
            self._conn.execute(
                "INSERT INTO events (investigation_id, seq, type, message, data_json, at) VALUES (?,?,?,?,?,?)",
                (event.investigation_id, event.seq, event.type.value, event.message,
                 json.dumps(event.data, default=str), event.at.isoformat()),
            )
            self._conn.commit()
        return event

    def list_events(self, investigation_id: str, after_seq: int = 0) -> list[ProgressEvent]:
        rows = self._query(
            "SELECT * FROM events WHERE investigation_id=? AND seq>? ORDER BY seq", (investigation_id, after_seq)
        )
        return [
            ProgressEvent(
                seq=r["seq"], investigation_id=investigation_id, type=r["type"], message=r["message"],
                data=json.loads(r["data_json"]), at=_dt(r["at"]),
            )
            for r in rows
        ]

    def clear_events(self, investigation_id: str) -> None:
        self._exec("DELETE FROM events WHERE investigation_id=?", (investigation_id,))

    # ---------------------------------------------------------------- notes/tags
    def add_note(self, investigation_id: str, text: str) -> Note:
        created = _now()
        cur = self._exec(
            "INSERT INTO notes (investigation_id, text, created_at) VALUES (?,?,?)", (investigation_id, text, created)
        )
        return Note(id=int(cur.lastrowid or 0), text=text, created_at=_dt(created))

    def delete_note(self, investigation_id: str, note_id: int) -> bool:
        return self._exec(
            "DELETE FROM notes WHERE id=? AND investigation_id=?", (note_id, investigation_id)
        ).rowcount > 0

    def _tags(self, investigation_id: str) -> list[str]:
        return [r["tag"] for r in self._query("SELECT tag FROM tags WHERE investigation_id=? ORDER BY tag", (investigation_id,))]

    def add_tag(self, investigation_id: str, tag: str) -> list[str]:
        self._exec("INSERT OR IGNORE INTO tags (investigation_id, tag) VALUES (?,?)", (investigation_id, tag))
        return self._tags(investigation_id)

    def remove_tag(self, investigation_id: str, tag: str) -> list[str]:
        self._exec("DELETE FROM tags WHERE investigation_id=? AND tag=?", (investigation_id, tag))
        return self._tags(investigation_id)

    def all_tags(self) -> dict[str, int]:
        return {r["tag"]: r["n"] for r in self._query("SELECT tag, COUNT(*) AS n FROM tags GROUP BY tag ORDER BY n DESC")}

    # ------------------------------------------------------------------ retention
    def reference_images_created_before(self, cutoff: datetime) -> list[ReferenceImage]:
        rows = self._query("SELECT * FROM reference_images WHERE created_at < ?", (cutoff.isoformat(),))
        return [self._row_to_reference(r, False) for r in rows]

    def purge_reference_image_data(self, image_id: str, *, file: bool, embeddings: bool) -> None:
        rows = self._query("SELECT meta_json FROM reference_images WHERE id=?", (image_id,))
        if not rows:
            return
        if file:
            meta = ReferenceImage.model_validate_json(rows[0]["meta_json"])
            meta.image_available = False
            for face in meta.faces:
                face.thumbnail = None  # derived from the reference photo
            self._exec(
                "UPDATE reference_images SET stored_path=NULL, meta_json=? WHERE id=?", (meta.model_dump_json(), image_id)
            )
        if embeddings:
            self._exec("UPDATE reference_images SET embeddings_json=NULL WHERE id=?", (image_id,))

    def results_completed_before(self, cutoff: datetime) -> list[str]:
        rows = self._query(
            "SELECT investigation_id FROM investigation_results WHERE completed_at IS NOT NULL AND completed_at < ?",
            (cutoff.isoformat(),),
        )
        return [r["investigation_id"] for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def create_repository(database_url: str) -> InvestigationRepository:
    if database_url.startswith("sqlite:///"):
        return SQLiteInvestigationRepository(database_url[len("sqlite:///"):] or ":memory:")
    if database_url in ("sqlite://", "sqlite:///:memory:", ":memory:"):
        return SQLiteInvestigationRepository(":memory:")
    raise ValueError(
        "Unsupported DATABASE_URL. Only sqlite:///path is implemented; add a PostgreSQL "
        "InvestigationRepository implementation to support other backends."
    )
