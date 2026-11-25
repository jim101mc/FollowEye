from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AppInfo:
    exe: str  # lower-case basename, how modes match programs
    name: str
    path: str


class AppCatalog(Protocol):
    def installed(self) -> list[AppInfo]: ...
    def running(self) -> list[AppInfo]: ...
