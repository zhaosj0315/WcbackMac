import os
import platform
from pathlib import Path


SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == "windows"
IS_MACOS = SYSTEM == "darwin"
IS_LINUX = SYSTEM == "linux"


def preferred_font() -> str:
    if IS_WINDOWS:
        return "Microsoft YaHei"
    if IS_MACOS:
        return "PingFang SC"
    return "Noto Sans CJK SC"


def mac_wechat_roots() -> list[str]:
    home = Path.home()
    candidates = [
        home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Library" / "Application Support" / "com.tencent.xinWeChat",
        home / "Library" / "Containers" / "com.tencent.WeChat" / "Data" / "Library" / "Application Support" / "com.tencent.WeChat",
        home / "Library" / "Application Support" / "com.tencent.xinWeChat",
        home / "Library" / "Application Support" / "WeChat",
    ]
    return [str(path) for path in candidates if path.exists()]


def windows_wechat_root() -> str:
    if not IS_WINDOWS:
        return "."

    import winreg

    is_w_dir = False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, "FileSavePath")
        winreg.CloseKey(key)
        w_dir = value
        is_w_dir = True
    except Exception:
        w_dir = "MyDocument:"

    if not is_w_dir:
        try:
            user_profile = os.environ.get("USERPROFILE")
            path_3ebffe94 = os.path.join(
                user_profile,
                "AppData",
                "Roaming",
                "Tencent",
                "WeChat",
                "All Users",
                "config",
                "3ebffe94.ini",
            )
            with open(path_3ebffe94, "r", encoding="utf-8") as f:
                w_dir = f.read()
            is_w_dir = True
        except Exception:
            w_dir = "MyDocument:"

    if w_dir == "MyDocument:":
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            documents_path = winreg.QueryValueEx(key, "Personal")[0]
            winreg.CloseKey(key)
            documents_paths = os.path.split(documents_path)
            if "%" in documents_paths[0]:
                w_dir = os.environ.get(documents_paths[0].replace("%", ""))
                w_dir = os.path.join(w_dir, os.path.join(*documents_paths[1:]))
            else:
                w_dir = documents_path
        except Exception:
            profile = os.environ.get("USERPROFILE")
            w_dir = os.path.join(profile, "Documents")

    return os.path.join(w_dir, "WeChat Files")


def default_wechat_root() -> str:
    if IS_WINDOWS:
        return windows_wechat_root()
    if IS_MACOS:
        roots = mac_wechat_roots()
        return roots[0] if roots else str(Path.home())
    return str(Path.home())
