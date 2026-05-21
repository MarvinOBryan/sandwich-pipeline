"""Previs sequencer state: dataclasses + Maya `fileInfo` persistence."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import maya.cmds as mc

log = logging.getLogger(__name__)

FILEINFO_KEY = "previs_sequencer_state"
SCHEMA_VERSION = 1
DEFAULT_SHOT_DURATION = 72  # frames; 3 seconds @ 24fps


def next_shot_id() -> str:
    return f"shot_{uuid.uuid4().hex[:8]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_name(index: int) -> str:
    return f"SHOT_{(index + 1) * 10:03d}"


@dataclass
class PrevisShot:
    id: str
    primary: str = ""
    alternates: list[str] = field(default_factory=list)
    duration_frames: int = DEFAULT_SHOT_DURATION
    shotgrid_code: str | None = None
    published_primary: str | None = None
    published_animation_hash: str | None = None

    @property
    def all_cameras(self) -> list[str]:
        return (
            [self.primary, *self.alternates] if self.primary else list(self.alternates)
        )


@dataclass
class PrevisState:
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utcnow_iso)
    last_published_at: str | None = None
    notes: str = ""
    shots: list[PrevisShot] = field(default_factory=list)

    @classmethod
    def empty(cls) -> PrevisState:
        return cls()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PrevisState:
        shots_raw = raw.get("shots") or []
        shots = [
            PrevisShot(
                id=str(s.get("id") or next_shot_id()),
                primary=str(s.get("primary") or ""),
                alternates=list(s.get("alternates") or []),
                duration_frames=int(s.get("duration_frames") or DEFAULT_SHOT_DURATION),
                shotgrid_code=s.get("shotgrid_code"),
                published_primary=s.get("published_primary"),
                published_animation_hash=s.get("published_animation_hash"),
            )
            for s in shots_raw
        ]
        metadata = raw.get("metadata") or {}
        return cls(
            schema_version=int(raw.get("schema_version") or SCHEMA_VERSION),
            created_at=str(metadata.get("created_at") or utcnow_iso()),
            last_published_at=metadata.get("last_published_at"),
            notes=str(metadata.get("notes") or ""),
            shots=shots,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metadata": {
                "created_at": self.created_at,
                "last_published_at": self.last_published_at,
                "notes": self.notes,
            },
            "shots": [
                {
                    "id": s.id,
                    "primary": s.primary,
                    "alternates": list(s.alternates),
                    "duration_frames": s.duration_frames,
                    "shotgrid_code": s.shotgrid_code,
                    "published_primary": s.published_primary,
                    "published_animation_hash": s.published_animation_hash,
                }
                for s in self.shots
            ],
        }

    def find_shot(self, shot_id: str) -> PrevisShot | None:
        return next((s for s in self.shots if s.id == shot_id), None)


def read_state() -> PrevisState | None:
    info = mc.fileInfo(FILEINFO_KEY, query=True)
    if not info:
        return None
    raw = info[0] if isinstance(info, (list, tuple)) else info
    if not isinstance(raw, str):
        return None
    try:
        return PrevisState.from_dict(json.loads(raw))
    except (json.JSONDecodeError, KeyError, ValueError):
        log.warning("Previs sequencer state in fileInfo is malformed; ignoring.")
        return None


def write_state(state: PrevisState) -> None:
    mc.fileInfo(FILEINFO_KEY, json.dumps(state.to_dict()))
