from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..config import ModelSettings
from ..models.base import ModelError, ModelProvider
from ..modes import Routing
from ..skills.analysis import AnalysisContext, AnalysisResult, UiField, build_request, parse_result
from ..skills.decision import DecisionLayer, Route, decision_features
from ..skills.skills import Desktop, Suggestion, build_suggestion
from .dedup import Deduplicator, thumbnail
from .triggers import TriggerEvent

log = logging.getLogger(__name__)

MIN_UIA_TEXT = 200


@dataclass
class ScreenReader:
    """Platform hooks the pipeline needs; swapped for fakes in tests."""

    window_rect: Callable[[int], tuple[int, int, int, int] | None]
    grab: Callable[[tuple[int, int, int, int]], np.ndarray | None]
    ui_snapshot: Callable[[int], tuple[str, list[UiField]]]
    ocr: Callable[[np.ndarray], str]
    to_png: Callable[[np.ndarray], bytes]


class Pipeline:
    """Capture -> dedup -> local model -> decision -> (cloud model) -> suggestion, for one trigger."""

    def __init__(
        self,
        reader: ScreenReader,
        desktop: Desktop,
        decision: DecisionLayer,
        model_settings: ModelSettings,
        local: ModelProvider | None,
        cloud: ModelProvider | None,
        profile: Callable[[], dict[str, str]],
        show_threshold: float,
    ):
        self.reader = reader
        self.desktop = desktop
        self.decision = decision
        self.settings = model_settings
        self.local = local
        self.cloud = cloud
        self.profile = profile
        self.show_threshold = show_threshold
        self.dedup = Deduplicator()
        self.last_error: str | None = None

    def handle(self, event: TriggerEvent) -> Suggestion | None:
        hwnd = event.window.hwnd
        rect = self.reader.window_rect(hwnd)
        if rect is None:
            return None
        pixels = self.reader.grab(rect)
        if pixels is None:
            return None
        thumb = thumbnail(pixels)
        if not self.dedup.should_capture(hwnd, thumb, event.at, event.trigger):
            return None
        text, fields = self.reader.ui_snapshot(hwnd)
        if len(text) < MIN_UIA_TEXT:
            text = self.reader.ocr(pixels) or text
        content_hash = hashlib.sha256(
            "\x00".join([text, event.clipboard or "", *(f"{f.label}={f.value}" for f in fields)]).encode()
        ).hexdigest()
        if not self.dedup.should_analyze(hwnd, content_hash, event.at):
            return None
        self.dedup.mark(hwnd, thumb, content_hash, event.at)

        ctx = AnalysisContext(
            trigger=event.trigger,
            window=event.window,
            screen_text=text,
            fields=fields,
            clipboard=event.clipboard,
            profile=self.profile(),
        )
        result, features = self._analyze(ctx, pixels, event.behaviour.routing)
        if result is None or result.confidence < self.show_threshold:
            return None
        suggestion = build_suggestion(result, ctx, self.desktop)
        if suggestion is not None:
            suggestion.features = features
        return suggestion

    def _analyze(
        self, ctx: AnalysisContext, pixels: np.ndarray, routing: Routing
    ) -> tuple[AnalysisResult | None, list[float] | None]:
        first = self.decision.first_stage(routing, self.local is not None, self.cloud is not None)
        if first is None:
            return None, None
        if first == "cloud":
            return self._run_cloud(ctx, pixels), None
        local = self._run(self.local, ctx, pixels, cloud=False)
        route = self.decision.after_local(local, ctx, routing, self.cloud is not None)
        if route is Route.DROP:
            return None, None
        if route is Route.ESCALATE:
            escalated = self._run_cloud(ctx, pixels)
            if escalated is not None:
                return escalated, None
        if local is None:
            return None, None
        return local, decision_features(local, ctx)

    def _run_cloud(self, ctx: AnalysisContext, pixels: np.ndarray) -> AnalysisResult | None:
        if self.cloud is None or not self.decision.allow_cloud_call():
            return None
        return self._run(self.cloud, ctx, pixels, cloud=True)

    def _run(self, provider: ModelProvider, ctx: AnalysisContext, pixels: np.ndarray, cloud: bool) -> AnalysisResult | None:
        send_image = provider.supports_images and (not cloud or self.settings.send_screenshots_to_cloud)
        if send_image and ctx.image_png is None:
            ctx.image_png = self.reader.to_png(pixels)
        request = build_request(
            ctx,
            include_profile=not cloud or self.settings.share_profile_with_cloud,
            include_image=send_image,
        )
        try:
            data = provider.complete_json(request)
        except ModelError as e:
            self.last_error = f"{provider.display_name}: {e}"
            log.warning("%s", self.last_error)
            return None
        self.last_error = None
        return parse_result(data, "cloud" if cloud else "local")
