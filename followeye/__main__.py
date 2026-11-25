from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler


def main() -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .config import data_dir

    home = data_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(home / "followeye.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
    )
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("FollowEye")
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
