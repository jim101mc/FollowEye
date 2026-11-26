from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .modes import Blocklist, Mode, default_blocklist, preset_modes

log = logging.getLogger(__name__)


def data_dir() -> Path:
    base = os.environ.get("FOLLOWEYE_HOME") or os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"), "FollowEye"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


class HotkeySettings(BaseModel):
    pause: str = "Ctrl+Shift+F"
    accept: str = "Ctrl+Shift+Enter"
    dismiss: str = "Ctrl+Shift+Backspace"


HOTKEY_LABELS = {
    "pause": "Pause / resume observation",
    "accept": "Accept suggestion (while shown)",
    "dismiss": "Dismiss suggestion (while shown)",
}


class WidgetCorner(StrEnum):
    BOTTOM_RIGHT = "bottom_right"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    TOP_LEFT = "top_left"


class NotificationSettings(BaseModel):
    corner: WidgetCorner = WidgetCorner.BOTTOM_RIGHT
    auto_hide_seconds: int = 12
    cooldown_seconds: int = 20
    show_threshold: float = 0.6


class ProviderConfig(BaseModel):
    provider_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class ModelSettings(BaseModel):
    local: ProviderConfig = Field(default_factory=ProviderConfig)
    cloud: ProviderConfig = Field(default_factory=ProviderConfig)
    escalation_confidence: float = 0.7
    send_screenshots_to_cloud: bool = False
    share_profile_with_cloud: bool = False
    max_cloud_requests_per_hour: int = 60


class Calibration(BaseModel):
    coefficients: list[list[float]]
    feature_mean: list[float]
    feature_std: list[float]
    screen: tuple[int, int, int, int]  # physical pixels: left, top, width, height
    mean_error_px: float = 0.0  # on the calibration dots themselves (optimistic)
    test_error_px: float | None = None  # on new dots not used for training
    test_bias_px: tuple[float, float] | None = None  # average (right, down) offset on the test dots
    test_jitter_px: float | None = None
    version: int = 1  # files written before versioning are version 1


class GazeSettings(BaseModel):
    enabled: bool = True
    camera_index: int = 0
    smoothing: float = 0.5  # 0 = raw, 1 = heavy
    calibration: Calibration | None = None


class PrivacySettings(BaseModel):
    blocklist: Blocklist = Field(default_factory=default_blocklist)
    logging_enabled: bool = True
    retention_days: int = 30


class Settings(BaseModel):
    version: int = 1
    hotkeys: HotkeySettings = Field(default_factory=HotkeySettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    gaze: GazeSettings = Field(default_factory=GazeSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    modes: list[Mode] = Field(default_factory=preset_modes)
    active_mode_id: str = "work"

    def active_mode(self) -> Mode:
        mode = self.mode(self.active_mode_id)
        return mode if mode is not None else self.modes[0]

    def mode(self, mode_id: str) -> Mode | None:
        return next((m for m in self.modes if m.id == mode_id), None)


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    try:
        settings = Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError):
        backup = path.with_suffix(".invalid.json")
        path.replace(backup)
        log.warning("Settings file was invalid; moved to %s and using defaults", backup)
        return Settings()
    if not settings.modes:
        settings.modes = preset_modes()
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
