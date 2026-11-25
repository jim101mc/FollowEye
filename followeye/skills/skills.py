from __future__ import annotations

import ntpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .analysis import AnalysisContext, AnalysisResult, SkillId

_SENSITIVE_LABEL = re.compile(
    r"\b(password|passcode|pin|cvv|cvc|security code|card number|iban)\b", re.IGNORECASE
)


class Desktop(Protocol):
    def set_field(self, ui_field, value: str) -> bool: ...
    def open_path(self, path: str) -> None: ...
    def reveal_path(self, path: str) -> None: ...
    def copy_text(self, text: str) -> None: ...
    def search_files(self, keywords: list[str], limit: int) -> list[str]: ...


@dataclass
class SuggestionAction:
    label: str
    run: Callable[[], str | None]  # returns an optional status message


@dataclass
class Suggestion:
    skill: SkillId
    title: str
    body: str
    source: str
    actions: list[SuggestionAction]
    details: list[str] = field(default_factory=list)
    confidence: float = 0.0
    features: list[float] | None = None  # set when the local model produced it, for learning

    @property
    def primary(self) -> SuggestionAction | None:
        return self.actions[0] if self.actions else None


SKILL_LABELS = {SkillId.FORM_FILL: "Form", SkillId.FILE_FIND: "Files", SkillId.CONTEXT: "Context"}


def build_suggestion(result: AnalysisResult, ctx: AnalysisContext, desktop: Desktop) -> Suggestion | None:
    builder = {
        SkillId.FORM_FILL: _form_fill,
        SkillId.FILE_FIND: _file_find,
        SkillId.CONTEXT: _context,
    }.get(result.skill)
    return builder(result, ctx, desktop) if builder else None


def _form_fill(result: AnalysisResult, ctx: AnalysisContext, desktop: Desktop) -> Suggestion | None:
    by_id = {f.field_id: f for f in ctx.fields}
    fills = []
    for field_id, value in result.fills:
        ui_field = by_id.get(field_id)
        if ui_field is None or ui_field.value.strip() or not value.strip():
            continue
        if _SENSITIVE_LABEL.search(ui_field.label):
            continue
        fills.append((ui_field, value))
    if not fills:
        return None

    def fill_all() -> str:
        done = sum(desktop.set_field(f, v) for f, v in fills)
        if done == len(fills):
            return f"Filled {done} field{'s' if done != 1 else ''}."
        return f"Filled {done} of {len(fills)} fields; the form changed or blocked the rest."

    def copy_all() -> str:
        desktop.copy_text("\n".join(f"{f.label}: {v}" for f, v in fills))
        return "Copied the values."

    count = len(fills)
    return Suggestion(
        skill=result.skill,
        title=result.title or "Fill this form?",
        body=result.body,
        source=result.source,
        confidence=result.confidence,
        details=[f"{f.label or f.field_id}: {v}" for f, v in fills],
        actions=[
            SuggestionAction(f"Fill {count} field{'s' if count != 1 else ''}", fill_all),
            SuggestionAction("Copy values", copy_all),
        ],
    )


def _file_find(result: AnalysisResult, ctx: AnalysisContext, desktop: Desktop) -> Suggestion | None:
    if not result.file_keywords:
        return None
    paths = desktop.search_files(result.file_keywords, limit=3)
    if not paths:
        return None

    def opener(path: str) -> Callable[[], str]:
        def run() -> str:
            desktop.open_path(path)
            return f"Opened {ntpath.basename(path)}."
        return run

    actions = [SuggestionAction(f"Open {ntpath.basename(p)}", opener(p)) for p in paths]

    def reveal() -> str:
        desktop.reveal_path(paths[0])
        return "Opened the folder."

    actions.append(SuggestionAction("Show in folder", reveal))
    return Suggestion(
        skill=result.skill,
        title=result.title or "Related files",
        body=result.body,
        source=result.source,
        confidence=result.confidence,
        details=paths,
        actions=actions,
    )


def _context(result: AnalysisResult, ctx: AnalysisContext, desktop: Desktop) -> Suggestion | None:
    if not result.body.strip():
        return None

    def copy() -> str:
        desktop.copy_text(result.body)
        return "Copied."

    return Suggestion(
        skill=result.skill,
        title=result.title or "Here's some context",
        body=result.body,
        source=result.source,
        confidence=result.confidence,
        actions=[SuggestionAction("Copy", copy)],
    )
