from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

from config import ROOT

log = logging.getLogger("helper.windows")

APP_USER_MODEL_ID = "Helper.Desktop"
APP_ICON_PATH = ROOT / "assets" / "helper_logo.ico"
MAIN_SCRIPT_PATH = ROOT / "main.py"


def configure_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as exc:
        log.debug("Could not set Windows AppUserModelID: %s", exc)


def _programs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def helper_shortcut_specs() -> list[tuple[Path, str]]:
    if os.name != "nt":
        return []
    specs: list[tuple[Path, str]] = []
    programs_dir = _programs_dir()
    if programs_dir is not None:
        specs.append((programs_dir / "Helper.lnk", "--dashboard"))
        specs.append((programs_dir / "Helper Chat.lnk", "--chat"))
    specs.append((Path.home() / "Desktop" / "Helper.lnk", "--dashboard"))
    specs.append((Path.home() / "Desktop" / "Helper Chat.lnk", "--chat"))
    return specs


def _pythonw_path() -> Path:
    venv_pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return venv_pythonw
    current = Path(sys.executable)
    if current.name.lower() == "python.exe":
        pythonw = current.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return current


def _stale_shortcut_paths() -> list[Path]:
    if os.name != "nt":
        return []
    stale: list[Path] = []
    programs_dir = _programs_dir()
    if programs_dir is not None:
        stale.append(programs_dir / "Helper Dashboard.lnk")
    stale.append(Path.home() / "Desktop" / "Helper Dashboard.lnk")
    return stale


def ensure_windows_shortcuts() -> list[Path]:
    if os.name != "nt":
        return []

    for stale in _stale_shortcut_paths():
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Could not remove stale shortcut %s: %s", stale, exc)

    created: list[Path] = []
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell
    except Exception as exc:
        log.warning("Could not create Helper shortcuts; pywin32 unavailable: %s", exc)
        return created

    target = _pythonw_path()
    base_arguments = f'"{MAIN_SCRIPT_PATH}"'

    for shortcut_path, launch_arg in helper_shortcut_specs():
        try:
            shortcut_path.parent.mkdir(parents=True, exist_ok=True)
            link = pythoncom.CoCreateInstance(
                shell.CLSID_ShellLink,
                None,
                pythoncom.CLSCTX_INPROC_SERVER,
                shell.IID_IShellLink,
            )
            link.SetPath(str(target))
            link.SetArguments(f"{base_arguments} {launch_arg}")
            link.SetWorkingDirectory(str(ROOT))
            if APP_ICON_PATH.exists():
                link.SetIconLocation(str(APP_ICON_PATH), 0)

            store = link.QueryInterface(propsys.IID_IPropertyStore)
            store.SetValue(
                pscon.PKEY_AppUserModel_ID,
                propsys.PROPVARIANTType(APP_USER_MODEL_ID),
            )
            store.Commit()

            persist = link.QueryInterface(pythoncom.IID_IPersistFile)
            persist.Save(str(shortcut_path), 0)
            created.append(shortcut_path)
        except Exception as exc:
            log.warning("Could not create Helper shortcut %s: %s", shortcut_path, exc)
    return created
