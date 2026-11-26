from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler


def self_test(report_path: str) -> int:
    """Checks the packaged app's native parts load; writes 'ok' or the error to report_path."""
    import traceback

    try:
        import numpy as np

        from .gaze.landmarks import FaceLandmarks

        landmarks = FaceLandmarks()
        assert landmarks.detect(np.zeros((480, 640, 3), np.uint8), 0.0) is None
        landmarks.close()
        if sys.platform == "win32":
            from .capture.ocr import WindowsOcr

            WindowsOcr()
        result, code = "ok", 0
    except Exception:  # report every failure, whatever it is
        result, code = traceback.format_exc(), 1
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(result)
    return code


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        return self_test(sys.argv[2])

    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .config import data_dir

    home = data_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(home / "followeye.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
    if sys.platform == "win32":
        import ctypes

        # Own taskbar identity, so Windows shows FollowEye's icon instead of the generic Python one.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FollowEye.FollowEye")
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("FollowEye")
    from .ui.icons import eye_icon

    qapp.setWindowIcon(eye_icon("active"))
    qapp.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(home / "followeye.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "FollowEye", "FollowEye is already running – look for the eye icon in the tray.")
        return 0

    from .app import FollowEyeApp

    app = FollowEyeApp(qapp)
    app.start()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
