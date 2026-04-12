"""Write-ahead log for storage operations.

Appends one JSON record per write to a JSONL file. Each record is grouped by
a transaction id so related writes (sync pulls, multi-step ingests) can be
identified and later replayed or audited.

This is the first step toward rollback-on-failure. For now it is an
append-only audit trail. Nothing reads the WAL back yet; that lands when the
first concrete need for replay or rollback shows up.

Usage:
    wal = WAL(data_dir / "wal.jsonl")
    with wal.transaction("sync") as tx:
        tx.log("concept_add", {"name": "X"})
        tx.log("edge_add", {"src": "X", "dst": "Y"})
    # Transaction-end record written automatically, including outcome.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass
class Transaction:
    """Scoped handle used inside a `WAL.transaction()` block."""

    wal: WAL
    tx_id: str
    kind: str

    def log(self, op: str, params: dict) -> None:
        self.wal._append({
            "tx_id": self.tx_id,
            "kind": self.kind,
            "event": "op",
            "op": op,
            "params": params,
        })


class WAL:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    @contextmanager
    def transaction(self, kind: str) -> Iterator[Transaction]:
        """Open a transaction scope. Writes a start record, then an end record
        with outcome=ok or outcome=error depending on whether the block raised."""
        tx_id = uuid.uuid4().hex[:12]
        self._append({"tx_id": tx_id, "kind": kind, "event": "begin"})
        try:
            yield Transaction(wal=self, tx_id=tx_id, kind=kind)
        except BaseException as exc:
            self._append({
                "tx_id": tx_id,
                "kind": kind,
                "event": "end",
                "outcome": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            raise
        else:
            self._append({"tx_id": tx_id, "kind": kind, "event": "end", "outcome": "ok"})

    def log(self, op: str, params: dict, *, kind: str = "single") -> str:
        """Append a single standalone operation outside a transaction scope.
        Returns the generated tx_id so callers can correlate later."""
        tx_id = uuid.uuid4().hex[:12]
        self._append({
            "tx_id": tx_id,
            "kind": kind,
            "event": "op",
            "op": op,
            "params": params,
        })
        return tx_id

    def read_all(self) -> list[dict]:
        """Return every record in the log. Intended for tests and debugging,
        not for hot-path reads."""
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
