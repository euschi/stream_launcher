# PyInstaller: due eseguibili nella stessa cartella, con librerie e dati in comune.
#   pyinstaller --noconfirm windows/StreamLauncher.spec   (dalla radice del repo)
# - StreamLauncher.exe      con finestra: menu Start, tray, servizi avviati
# - stream-launcher-cli.exe di console: il prompt aspetta che finisca, quindi
#   funzionano il menu interattivo e i comandi che chiedono input.
import os

root = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(root, "stream_launcher", "__main__.py")],
    pathex=[root],
    hiddenimports=["PySide6.QtSvg"],
    datas=[(os.path.join(root, "config"), "config"), (os.path.join(root, "templates"), "templates")],
)
pyz = PYZ(a.pure)
icon = os.path.join(SPECPATH, "icon.ico")


def exe(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, icon=icon, console=console)


coll = COLLECT(
    exe("StreamLauncher", console=False),
    exe("stream-launcher-cli", console=True),
    a.binaries,
    a.datas,
    name="StreamLauncher",
)
