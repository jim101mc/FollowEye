from cryptography.fernet import Fernet

from followeye.config import Settings, load_settings, save_settings
from followeye.modes import Mode, ProgramEntry
from followeye.storage.vault import EncryptedJsonFile, HistoryLog, Vault


def test_history_is_encrypted_and_editable(tmp_path):
    vault = Vault(Fernet.generate_key())
    log = HistoryLog(tmp_path / "history.log", vault, retention_days=30)
    a = log.append({"title": "secret form data"})
    b = log.append({"title": "other"})
    assert b"secret form data" not in (tmp_path / "history.log").read_bytes()
    log.update(a, outcome="accepted")
    assert [r["outcome"] for r in log.records() if r["id"] == a] == ["accepted"]
    log.delete({b})
    assert [r["id"] for r in log.records()] == [a]
    log.clear()
    assert log.records() == []


def test_history_retention(tmp_path):
    vault = Vault(Fernet.generate_key())
    log = HistoryLog(tmp_path / "h.log", vault, retention_days=1)
    log.append({"n": 1})
    import time

    assert log.purge_old(now=time.time() + 2 * 86400) == 1
    assert log.records() == []


def test_other_key_cannot_read(tmp_path):
    path = tmp_path / "p.bin"
    EncryptedJsonFile(path, Vault(Fernet.generate_key())).save({"name": "Ada"})
    assert EncryptedJsonFile(path, Vault(Fernet.generate_key())).load(default={}) == {}


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings()
    s.modes.append(Mode(name="Reading", programs=[ProgramEntry(exe="AcroRd32.exe")]))
    s.active_mode_id = s.modes[-1].id
    s.hotkeys.pause = "Ctrl+Alt+P"
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.active_mode().name == "Reading"
    assert loaded.active_mode().programs[0].exe == "acrord32.exe"
    assert loaded.hotkeys.pause == "Ctrl+Alt+P"


def test_invalid_settings_are_backed_up(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load_settings(path).active_mode_id == "work"
    assert (tmp_path / "settings.invalid.json").exists()
