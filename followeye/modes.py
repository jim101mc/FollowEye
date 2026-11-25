from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class Trigger(StrEnum):
    WINDOW_SWITCH = "window_switch"
    TYPING_START = "typing_start"
    TYPING_STOP = "typing_stop"
    SCROLL = "scroll"
    IDLE_DWELL = "idle_dwell"
    CLIPBOARD = "clipboard"


TRIGGER_LABELS: dict[Trigger, str] = {
    Trigger.WINDOW_SWITCH: "Window switch (gaze stays on a new window)",
    Trigger.TYPING_START: "Typing starts",
    Trigger.TYPING_STOP: "Typing stops",
    Trigger.SCROLL: "Scrolling",
    Trigger.IDLE_DWELL: "Idle dwell (looking without interacting)",
    Trigger.CLIPBOARD: "Clipboard change",
}

# Only these triggers have a user-adjustable threshold, in seconds.
DEFAULT_THRESHOLDS: dict[Trigger, float] = {
    Trigger.WINDOW_SWITCH: 0.5,
    Trigger.TYPING_STOP: 2.0,
    Trigger.IDLE_DWELL: 5.0,
}


class Routing(StrEnum):
    LOCAL_ONLY = "local_only"
    LOCAL_THEN_CLOUD = "local_then_cloud"
    CLOUD_PREFERRED = "cloud_preferred"


ROUTING_LABELS: dict[Routing, str] = {
    Routing.LOCAL_ONLY: "Local model only (nothing leaves this PC)",
    Routing.LOCAL_THEN_CLOUD: "Local first, escalate to cloud when needed",
    Routing.CLOUD_PREFERRED: "Cloud model preferred (skip local)",
}


class TriggerSetting(BaseModel):
    enabled: bool = True
    threshold: float | None = None


def _default_triggers() -> dict[Trigger, TriggerSetting]:
    return {t: TriggerSetting(threshold=DEFAULT_THRESHOLDS.get(t)) for t in Trigger}


class Behaviour(BaseModel):
    triggers: dict[Trigger, TriggerSetting] = Field(default_factory=_default_triggers)
    routing: Routing = Routing.LOCAL_THEN_CLOUD

    def enabled(self, trigger: Trigger) -> bool:
        setting = self.triggers.get(trigger)
        return setting is not None and setting.enabled

    def threshold(self, trigger: Trigger) -> float:
        setting = self.triggers.get(trigger)
        if setting is not None and setting.threshold is not None:
            return setting.threshold
        return DEFAULT_THRESHOLDS.get(trigger, 0.0)


class ProgramEntry(BaseModel):
    exe: str
    name: str = ""
    override: Behaviour | None = None

    def model_post_init(self, _context) -> None:
        self.exe = self.exe.lower()
        if not self.name:
            self.name = self.exe


class RuleKind(StrEnum):
    DOMAIN = "domain"
    TITLE = "title"


class RuleAction(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


BROWSER_EXES = frozenset(
    {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    exe: str
    title: str
    url: str | None = None
    is_foreground: bool = True

    @property
    def is_browser(self) -> bool:
        return self.exe in BROWSER_EXES

    @property
    def host(self) -> str | None:
        if not self.url:
            return None
        url = self.url if "://" in self.url else "http://" + self.url
        return urlparse(url).hostname


def _normalize_domain(pattern: str) -> str:
    pattern = pattern.strip().lower()
    if "://" in pattern:
        pattern = urlparse(pattern).hostname or ""
    pattern = pattern.split("/")[0]
    return pattern.removeprefix("www.")


class WindowRule(BaseModel):
    kind: RuleKind
    pattern: str
    action: RuleAction = RuleAction.INCLUDE

    def matches(self, window: WindowInfo) -> bool:
        if not self.pattern.strip():
            return False
        if self.kind is RuleKind.DOMAIN:
            host = window.host if window.is_browser else None
            if not host:
                return False
            domain = _normalize_domain(self.pattern)
            return host == domain or host.endswith("." + domain)
        pattern = self.pattern.lower()
        title = window.title.lower()
        if any(ch in pattern for ch in "*?["):
            return fnmatch.fnmatchcase(title, pattern)
        return pattern in title


class Mode(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    preset: str | None = None
    locked: bool = False
    observe: bool = True
    watch_all: bool = False
    foreground_only: bool = False
    programs: list[ProgramEntry] = Field(default_factory=list)
    rules: list[WindowRule] = Field(default_factory=list)
    behaviour: Behaviour = Field(default_factory=Behaviour)

    def program(self, exe: str) -> ProgramEntry | None:
        exe = exe.lower()
        return next((p for p in self.programs if p.exe == exe), None)

    @property
    def needs_url(self) -> bool:
        return any(r.kind is RuleKind.DOMAIN for r in self.rules)


class Blocklist(BaseModel):
    programs: list[ProgramEntry] = Field(default_factory=list)
    rules: list[WindowRule] = Field(default_factory=list)

    def blocks(self, window: WindowInfo) -> bool:
        exe = window.exe.lower()
        if any(p.exe == exe for p in self.programs):
            return True
        return any(r.matches(window) for r in self.rules)

    @property
    def needs_url(self) -> bool:
        return any(r.kind is RuleKind.DOMAIN for r in self.rules)


def resolve_behaviour(mode: Mode, blocklist: Blocklist, window: WindowInfo) -> Behaviour | None:
    """Return the behaviour that applies to `window`, or None if it must not be watched."""
    if not mode.observe or blocklist.blocks(window):
        return None
    if mode.foreground_only and not window.is_foreground:
        return None
    if any(r.action is RuleAction.EXCLUDE and r.matches(window) for r in mode.rules):
        return None
    program = mode.program(window.exe)
    if program is not None:
        return program.override or mode.behaviour
    if mode.watch_all or any(r.action is RuleAction.INCLUDE and r.matches(window) for r in mode.rules):
        return mode.behaviour
    return None


WORK_PROGRAMS = [
    ("code.exe", "Visual Studio Code"),
    ("devenv.exe", "Visual Studio"),
    ("idea64.exe", "IntelliJ IDEA"),
    ("pycharm64.exe", "PyCharm"),
    ("chrome.exe", "Google Chrome"),
    ("msedge.exe", "Microsoft Edge"),
    ("firefox.exe", "Firefox"),
    ("winword.exe", "Word"),
    ("excel.exe", "Excel"),
    ("powerpnt.exe", "PowerPoint"),
    ("outlook.exe", "Outlook"),
    ("onenote.exe", "OneNote"),
    ("windowsterminal.exe", "Windows Terminal"),
    ("powershell.exe", "PowerShell"),
    ("pwsh.exe", "PowerShell 7"),
    ("cmd.exe", "Command Prompt"),
    ("notepad.exe", "Notepad"),
    ("notepad++.exe", "Notepad++"),
]

DEFAULT_BLOCKED_PROGRAMS = [
    ("keepass.exe", "KeePass"),
    ("keepassxc.exe", "KeePassXC"),
    ("1password.exe", "1Password"),
    ("bitwarden.exe", "Bitwarden"),
]


def preset_modes() -> list[Mode]:
    return [
        Mode(id="full", name="Full", preset="full", watch_all=True),
        Mode(
            id="work",
            name="Work",
            preset="work",
            programs=[ProgramEntry(exe=e, name=n) for e, n in WORK_PROGRAMS],
        ),
        Mode(id="focus", name="Focus", preset="focus", watch_all=True, foreground_only=True),
        Mode(id="off", name="Off", preset="off", locked=True, observe=False),
    ]


def default_blocklist() -> Blocklist:
    return Blocklist(programs=[ProgramEntry(exe=e, name=n) for e, n in DEFAULT_BLOCKED_PROGRAMS])
