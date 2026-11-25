from __future__ import annotations

import math
import time
from collections import deque
from enum import StrEnum
from typing import Any

import numpy as np

from ..modes import Routing, Trigger
from .analysis import AnalysisContext, AnalysisResult, SkillId

_SKILLS = [SkillId.FORM_FILL, SkillId.FILE_FIND, SkillId.CONTEXT]
_TRIGGERS = list(Trigger)


class Route(StrEnum):
    USE_LOCAL = "use_local"
    ESCALATE = "escalate"
    DROP = "drop"


def decision_features(result: AnalysisResult, ctx: AnalysisContext) -> list[float]:
    return [
        1.0,
        result.confidence,
        math.log1p(len(ctx.screen_text)) / 10.0,
        1.0 if ctx.fields else 0.0,
        *[1.0 if result.skill is s else 0.0 for s in _SKILLS],
        *[1.0 if ctx.trigger is t else 0.0 for t in _TRIGGERS],
    ]


class EscalationLearner:
    """Logistic regression predicting whether the user accepts a suggestion from the local model.

    Trained on the user's own accept/dismiss feedback; consulted only once it has enough data.
    """

    MIN_SAMPLES = 50
    RETRAIN_EVERY = 20

    def __init__(self, samples: list[tuple[list[float], int]] | None = None):
        self.samples = list(samples or [])
        self.weights: np.ndarray | None = None
        self._since_fit = 0
        self.fit()

    def add(self, features: list[float], accepted: bool) -> None:
        self.samples.append((features, int(accepted)))
        self.samples = self.samples[-2000:]
        self._since_fit += 1
        if self._since_fit >= self.RETRAIN_EVERY or self.weights is None:
            self.fit()

    def fit(self, l2: float = 0.1, steps: int = 400, lr: float = 0.5) -> None:
        self._since_fit = 0
        labels = {y for _, y in self.samples}
        if len(self.samples) < self.MIN_SAMPLES or len(labels) < 2:
            self.weights = None
            return
        x = np.array([f for f, _ in self.samples], dtype=np.float64)
        y = np.array([lbl for _, lbl in self.samples], dtype=np.float64)
        w = np.zeros(x.shape[1])
        for _ in range(steps):
            p = 1.0 / (1.0 + np.exp(-(x @ w)))
            grad = x.T @ (p - y) / len(y) + l2 * np.r_[0.0, w[1:]]
            w -= lr * grad
        self.weights = w

    def predict(self, features: list[float]) -> float | None:
        if self.weights is None or len(features) != len(self.weights):
            return None
        return float(1.0 / (1.0 + math.exp(-float(np.dot(self.weights, features)))))

    def to_dict(self) -> dict[str, Any]:
        return {"samples": self.samples}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EscalationLearner:
        samples = [(list(f), int(y)) for f, y in (data or {}).get("samples", [])]
        return cls(samples)


class HourlyBudget:
    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self._calls: deque[float] = deque()

    def try_spend(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        while self._calls and now - self._calls[0] >= 3600:
            self._calls.popleft()
        if len(self._calls) >= self.per_hour:
            return False
        self._calls.append(now)
        return True


class DecisionLayer:
    """Hybrid routing: hard rules first, then the learned acceptance estimate."""

    def __init__(self, escalation_confidence: float, learner: EscalationLearner, budget: HourlyBudget):
        self.escalation_confidence = escalation_confidence
        self.learner = learner
        self.budget = budget

    def first_stage(self, routing: Routing, has_local: bool, has_cloud: bool) -> str | None:
        """Which model to ask first: 'local', 'cloud' or None."""
        if routing is Routing.CLOUD_PREFERRED and has_cloud:
            return "cloud"
        if has_local:
            return "local"
        if routing is not Routing.LOCAL_ONLY and has_cloud:
            return "cloud"
        return None

    def after_local(
        self,
        result: AnalysisResult | None,
        ctx: AnalysisContext,
        routing: Routing,
        has_cloud: bool,
    ) -> Route:
        can_escalate = has_cloud and routing is not Routing.LOCAL_ONLY
        if result is None:
            return Route.ESCALATE if can_escalate else Route.DROP
        if result.skill is SkillId.NONE and not result.needs_deeper_reasoning:
            return Route.DROP
        if not can_escalate:
            return Route.USE_LOCAL
        weak = result.needs_deeper_reasoning or result.confidence < self.escalation_confidence
        if not weak:
            p_accept = self.learner.predict(decision_features(result, ctx))
            weak = p_accept is not None and p_accept < 0.5
        return Route.ESCALATE if weak else Route.USE_LOCAL

    def allow_cloud_call(self) -> bool:
        return self.budget.try_spend()
