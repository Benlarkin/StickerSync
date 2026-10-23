"""Remembers which TikTok stickers have already been handled."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Status = Literal["uploaded", "failed", "skipped", "removed"]


@dataclass
class Record:
    status: Status
    name: str
    discord_id: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    detail: str | None = None


class State:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, Record] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for tid, rec in raw.get("stickers", {}).items():
                self.records[tid] = Record(**rec)

    def is_done(self, tiktok_id: str) -> bool:
        """Uploaded, or deliberately skipped -- failures are retried next run."""
        rec = self.records.get(tiktok_id)
        return rec is not None and rec.status in ("uploaded", "skipped", "removed")

    def mark(self, tiktok_id: str, status: Status, name: str, **kwargs) -> None:
        self.records[tiktok_id] = Record(status=status, name=name, **kwargs)
        self.save()

    def uploaded(self) -> dict[str, Record]:
        """Uploaded records, oldest first."""
        items = [(k, r) for k, r in self.records.items() if r.status == "uploaded"]
        return dict(sorted(items, key=lambda kv: kv[1].updated_at))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "stickers": {k: asdict(v) for k, v in self.records.items()},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash never corrupts state
