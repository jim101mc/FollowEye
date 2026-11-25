# PyInstaller spec: build with `pyinstaller followeye.spec` on Windows.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("mediapipe")
hiddenimports = (
    collect_submodules("winrt")
    + collect_submodules("uiautomation")
    + ["pynput.keyboard._win32", "pynput.mouse._win32", "keyring.backends.Windows", "win32timezone"]
)

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FollowEye",
    console=False,
    icon="assets/followeye.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="FollowEye")
