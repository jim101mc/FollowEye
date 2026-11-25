import numpy as np
import pytest

from followeye.config import ModelSettings
from followeye.engine.pipeline import Pipeline, ScreenReader
from followeye.engine.triggers import TriggerEvent
from followeye.models.base import ModelError, ModelProvider, extract_json
from followeye.modes import Behaviour, Routing, Trigger, WindowInfo
from followeye.skills.analysis import AnalysisContext, AnalysisResult, SkillId, UiField, build_request, parse_result
from followeye.skills.decision import DecisionLayer, EscalationLearner, HourlyBudget, Route, decision_features

WINDOW = WindowInfo(hwnd=7, exe="chrome.exe", title="Sign up")


def ctx(**kw):
    return AnalysisContext(trigger=Trigger.IDLE_DWELL, window=WINDOW, screen_text="text", **kw)


def result(skill=SkillId.CONTEXT, confidence=0.9, **kw):
    return AnalysisResult(skill=skill, confidence=confidence, title="t", body="b", **kw)


def layer(threshold=0.7, budget=10, learner=None):
    return DecisionLayer(threshold, learner or EscalationLearner(), HourlyBudget(budget))


# --- decision rules -------------------------------------------------------

def test_rules():
    d = layer()
    assert d.after_local(result(confidence=0.9), ctx(), Routing.LOCAL_THEN_CLOUD, True) is Route.USE_LOCAL
    assert d.after_local(result(confidence=0.4), ctx(), Routing.LOCAL_THEN_CLOUD, True) is Route.ESCALATE
    assert d.after_local(result(confidence=0.4), ctx(), Routing.LOCAL_ONLY, True) is Route.USE_LOCAL
    assert d.after_local(result(confidence=0.4), ctx(), Routing.LOCAL_THEN_CLOUD, False) is Route.USE_LOCAL
    assert d.after_local(None, ctx(), Routing.LOCAL_THEN_CLOUD, True) is Route.ESCALATE
    assert d.after_local(None, ctx(), Routing.LOCAL_ONLY, True) is Route.DROP
    assert d.after_local(result(SkillId.NONE), ctx(), Routing.LOCAL_THEN_CLOUD, True) is Route.DROP
    deep = result(confidence=0.95, needs_deeper_reasoning=True)
    assert d.after_local(deep, ctx(), Routing.LOCAL_THEN_CLOUD, True) is Route.ESCALATE


def test_first_stage():
    d = layer()
    assert d.first_stage(Routing.CLOUD_PREFERRED, True, True) == "cloud"
    assert d.first_stage(Routing.CLOUD_PREFERRED, True, False) == "local"
    assert d.first_stage(Routing.LOCAL_ONLY, False, True) is None
    assert d.first_stage(Routing.LOCAL_THEN_CLOUD, False, True) == "cloud"


def test_learner_escalates_patterns_the_user_dismisses():
    learner = EscalationLearner()
    c = ctx()
    for _ in range(40):
        learner.add(decision_features(result(SkillId.FILE_FIND, 0.9), c), accepted=False)
        learner.add(decision_features(result(SkillId.CONTEXT, 0.9), c), accepted=True)
    assert learner.predict(decision_features(result(SkillId.FILE_FIND, 0.9), c)) < 0.5
    assert learner.predict(decision_features(result(SkillId.CONTEXT, 0.9), c)) > 0.5
    d = layer(learner=learner)
    assert d.after_local(result(SkillId.FILE_FIND, 0.9), c, Routing.LOCAL_THEN_CLOUD, True) is Route.ESCALATE
    restored = EscalationLearner.from_dict(learner.to_dict())
    assert restored.predict(decision_features(result(SkillId.CONTEXT, 0.9), c)) > 0.5


def test_learner_waits_for_enough_data():
    learner = EscalationLearner()
    learner.add(decision_features(result(), ctx()), accepted=False)
    assert learner.predict(decision_features(result(), ctx())) is None


def test_hourly_budget():
    b = HourlyBudget(2)
    assert b.try_spend(0) and b.try_spend(1)
    assert not b.try_spend(2)
    assert b.try_spend(3601)


# --- prompt / parsing ----------------------------------------------------

def test_profile_only_reaches_cloud_when_allowed():
    c = ctx(profile={"name": "Ada Lovelace"})
    assert "Ada Lovelace" in build_request(c, include_profile=True, include_image=False).prompt
    assert "Ada Lovelace" not in build_request(c, include_profile=False, include_image=False).prompt


def test_parse_result_is_defensive():
    r = parse_result({"skill": "bogus", "confidence": "7", "fills": [{"x": 1}, {"field_id": "f1", "value": "v"}]}, "local")
    assert r.skill is SkillId.NONE
    assert r.confidence == 1.0
    assert r.fills == [("f1", "v")]


def test_extract_json_from_chatty_output():
    assert extract_json('Sure! ```json\n{"a": {"b": 1}}\n``` done') == {"a": {"b": 1}}
    with pytest.raises(ModelError):
        extract_json("no json here {")


# --- pipeline end to end with fakes ----------------------------------------

class FakeProvider(ModelProvider):
    provider_id = "fake"
    display_name = "Fake"

    def __init__(self, replies):
        super().__init__({}, lambda _k: None)
        self.replies = list(replies)
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeDesktop:
    def __init__(self):
        self.filled = []
        self.copied = []

    def set_field(self, ui_field, value):
        self.filled.append((ui_field.label, value))
        return True

    def open_path(self, path):
        pass

    def reveal_path(self, path):
        pass

    def copy_text(self, text):
        self.copied.append(text)

    def search_files(self, keywords, limit):
        return [r"C:\Users\me\Documents\Q3 report.docx"]


def reader(fields=None, text="Some long screen text " * 20):
    return ScreenReader(
        window_rect=lambda h: (0, 0, 800, 600),
        grab=lambda rect: np.zeros((600, 800, 4), np.uint8),
        ui_snapshot=lambda h: (text, fields or []),
        ocr=lambda px: "",
        to_png=lambda px: b"png",
    )


def reply(skill, confidence, **kw):
    base = {"skill": skill, "confidence": confidence, "title": "T", "body": "B", "fills": [],
            "file_keywords": [], "needs_deeper_reasoning": False}
    return {**base, **kw}


def event(routing=Routing.LOCAL_THEN_CLOUD, at=0.0, trigger=Trigger.IDLE_DWELL):
    return TriggerEvent(trigger, WINDOW, Behaviour(routing=routing), at)


def make_pipeline(local, cloud, fields=None, desktop=None, settings=None):
    return Pipeline(reader(fields), desktop or FakeDesktop(), layer(), settings or ModelSettings(),
                    local, cloud, profile=lambda: {"email": "ada@example.com"}, show_threshold=0.6)


def test_form_fill_from_local_model():
    fields = [UiField("f1", "text", "Email", ""), UiField("f2", "text", "Password", ""),
              UiField("f3", "text", "City", "Paris")]
    local = FakeProvider([reply("form_fill", 0.9, fills=[
        {"field_id": "f1", "value": "ada@example.com"},
        {"field_id": "f2", "value": "hunter2"},       # password: dropped
        {"field_id": "f3", "value": "London"},        # already filled: dropped
        {"field_id": "f9", "value": "x"},             # unknown: dropped
    ])])
    desktop = FakeDesktop()
    s = make_pipeline(local, None, fields, desktop).handle(event())
    assert s.source == "local" and s.features is not None
    assert s.details == ["Email: ada@example.com"]
    assert s.primary.run() == "Filled 1 field."
    assert desktop.filled == [("Email", "ada@example.com")]
    assert "ada@example.com" in local.requests[0].prompt  # profile goes to local model


def test_low_confidence_escalates_to_cloud_without_profile_or_image():
    local = FakeProvider([reply("context", 0.3)])
    cloud = FakeProvider([reply("context", 0.95, body="Cloud answer")])
    s = make_pipeline(local, cloud).handle(event())
    assert s.source == "cloud" and s.body == "Cloud answer" and s.features is None
    assert "ada@example.com" not in cloud.requests[0].prompt
    assert cloud.requests[0].image_png is None


def test_local_only_never_calls_cloud():
    local = FakeProvider([reply("context", 0.3)])
    cloud = FakeProvider([])
    assert make_pipeline(local, cloud).handle(event(Routing.LOCAL_ONLY)) is None  # below show threshold
    assert cloud.requests == []


def test_failed_local_falls_back_to_cloud():
    local = FakeProvider([ModelError("down")])
    cloud = FakeProvider([reply("file_find", 0.9, file_keywords=["Q3 report"])])
    s = make_pipeline(local, cloud).handle(event())
    assert s.details == [r"C:\Users\me\Documents\Q3 report.docx"]
    assert s.actions[0].label == "Open Q3 report.docx"


def test_unchanged_window_is_not_reanalysed():
    local = FakeProvider([reply("none", 0.9)])
    p = make_pipeline(local, None)
    assert p.handle(event(at=0.0)) is None
    assert p.handle(event(at=5.0)) is None
    assert len(local.requests) == 1
