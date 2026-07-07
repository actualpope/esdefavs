from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    system: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GameEntry:
    id: str
    system: str
    title: str
    source_path: str
    relative_rom_path: str
    resolved_rom_path: str
    entry_type: str = "game"
    favorite: bool = True
    alternative_emulator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameEntry":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


@dataclass
class SystemHealth:
    system: str
    gamelist: str
    rom_root: str
    parsed: bool = False
    storage_available: bool = False
    favorites_seen: int = 0
    favorites_included: int = 0
    removal_safe: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Manifest:
    schema_version: int
    generated_at: str
    source: dict[str, str]
    scan_health: dict[str, Any]
    entries: list[GameEntry] = field(default_factory=list)
    systems: dict[str, SystemHealth] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source": self.source,
            "scan_health": self.scan_health,
            "entries": [entry.to_dict() for entry in self.entries],
            "systems": {name: health.to_dict() for name, health in self.systems.items()},
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        return cls(
            schema_version=int(data["schema_version"]),
            generated_at=str(data["generated_at"]),
            source=dict(data.get("source", {})),
            scan_health=dict(data.get("scan_health", {})),
            entries=[GameEntry.from_dict(item) for item in data.get("entries", [])],
            systems={
                name: SystemHealth(**health)
                for name, health in data.get("systems", {}).items()
            },
            diagnostics=[Diagnostic(**item) for item in data.get("diagnostics", [])],
        )


@dataclass
class Plan:
    additions: list[GameEntry] = field(default_factory=list)
    removals: list[GameEntry] = field(default_factory=list)
    changes: list[tuple[GameEntry, GameEntry]] = field(default_factory=list)
    blocked_removals: list[tuple[GameEntry, str]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.additions or self.removals or self.changes)

