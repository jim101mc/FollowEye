from followeye.modes import (
    Behaviour,
    Blocklist,
    Mode,
    ProgramEntry,
    Routing,
    RuleAction,
    RuleKind,
    Trigger,
    TriggerSetting,
    WindowInfo,
    WindowRule,
    preset_modes,
    resolve_behaviour,
)


def win(exe="code.exe", title="main.py - VS Code", url=None, fg=True, hwnd=1):
    return WindowInfo(hwnd=hwnd, exe=exe, title=title, url=url, is_foreground=fg)


def presets():
    return {m.id: m for m in preset_modes()}


def test_full_watches_everything_except_blocklist():
    full = presets()["full"]
    block = Blocklist(programs=[ProgramEntry(exe="KeePass.exe")])
    assert resolve_behaviour(full, block, win("notepad.exe")) is full.behaviour
    assert resolve_behaviour(full, block, win("keepass.exe")) is None


def test_work_only_watches_listed_programs():
    work = presets()["work"]
    assert resolve_behaviour(work, Blocklist(), win("excel.exe")) is not None
    assert resolve_behaviour(work, Blocklist(), win("steam.exe")) is None


def test_focus_only_watches_foreground():
    focus = presets()["focus"]
    assert resolve_behaviour(focus, Blocklist(), win(fg=True)) is not None
    assert resolve_behaviour(focus, Blocklist(), win(fg=False)) is None


def test_off_watches_nothing():
    assert resolve_behaviour(presets()["off"], Blocklist(), win()) is None


def test_program_override_wins_over_mode_default():
    custom = Behaviour(routing=Routing.LOCAL_ONLY)
    mode = Mode(name="m", programs=[ProgramEntry(exe="excel.exe", override=custom)])
    assert resolve_behaviour(mode, Blocklist(), win("excel.exe")).routing is Routing.LOCAL_ONLY


def test_domain_rules_on_browsers():
    mode = Mode(
        name="m",
        programs=[ProgramEntry(exe="chrome.exe")],
        rules=[WindowRule(kind=RuleKind.DOMAIN, pattern="mybank.com", action=RuleAction.EXCLUDE)],
    )
    assert resolve_behaviour(mode, Blocklist(), win("chrome.exe", url="github.com/x")) is not None
    assert resolve_behaviour(mode, Blocklist(), win("chrome.exe", url="https://login.mybank.com/a")) is None
    include = Mode(name="i", rules=[WindowRule(kind=RuleKind.DOMAIN, pattern="https://www.github.com/")])
    assert resolve_behaviour(include, Blocklist(), win("msedge.exe", url="github.com/foo")) is not None
    assert resolve_behaviour(include, Blocklist(), win("msedge.exe", url="notgithub.com")) is None
    # Domain rules never match non-browsers, even if the title looks like a URL.
    assert resolve_behaviour(include, Blocklist(), win("notepad.exe", url="github.com")) is None


def test_title_rules_substring_and_glob():
    mode = Mode(name="m", rules=[WindowRule(kind=RuleKind.TITLE, pattern="invoice")])
    assert resolve_behaviour(mode, Blocklist(), win("acrobat.exe", title="Invoice 42.pdf")) is not None
    glob = WindowRule(kind=RuleKind.TITLE, pattern="*.pdf - adobe*")
    assert glob.matches(win(title="report.pdf - Adobe Acrobat"))
    assert not glob.matches(win(title="report.docx - Word"))


def test_blocklist_rules():
    block = Blocklist(rules=[WindowRule(kind=RuleKind.DOMAIN, pattern="bank.com")])
    full = presets()["full"]
    assert resolve_behaviour(full, block, win("chrome.exe", url="online.bank.com")) is None


def test_behaviour_thresholds_and_roundtrip():
    b = Behaviour()
    assert b.threshold(Trigger.IDLE_DWELL) == 5.0
    b.triggers[Trigger.IDLE_DWELL] = TriggerSetting(enabled=False, threshold=9)
    restored = Behaviour.model_validate_json(b.model_dump_json())
    assert restored.threshold(Trigger.IDLE_DWELL) == 9
    assert not restored.enabled(Trigger.IDLE_DWELL)


def test_program_exe_is_normalised():
    assert ProgramEntry(exe="EXCEL.EXE").exe == "excel.exe"
