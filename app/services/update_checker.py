from PySide6.QtCore import QThread, Signal, QSettings
import json
import re
import ssl
import urllib.request


# GitHub Releases 页面地址
GITHUB_RELEASES_URL = "https://github.com/ShumaMeme/MemeKit/releases"
# GitHub API：最新 release
GITHUB_API_LATEST = "https://api.github.com/repos/ShumaMeme/MemeKit/releases/latest"


def _normalize_version(v: str) -> tuple:
    """把 'V4.0.0' / 'v4.0.1' / '4.0.1' 归一化成 (4, 0, 1) 元组，便于比较。"""
    if not v:
        return (0, 0, 0)
    s = str(v).strip().lstrip("Vv")
    nums = re.findall(r"\d+", s)
    return tuple(int(x) for x in nums[:4]) if nums else (0, 0, 0)


def is_newer(remote: str, current: str) -> bool:
    """判断 remote 是否严格大于 current。"""
    return _normalize_version(remote) > _normalize_version(current)


def _last_remind_key() -> str:
    """返回今天用于记忆的日期字符串 YYYY-MM-DD。"""
    import time
    return time.strftime("%Y-%m-%d")


def should_remind_today(version: str) -> bool:
    """判断今天是否应该弹窗提醒某个版本。

    记忆规则：同一个版本每天最多弹窗一次。
    """
    try:
        s = QSettings()
        last_date = s.value("update/last_remind_date", "")
        last_version = s.value("update/last_remind_version", "")
        # 同版本且同一天：已提醒过
        if last_date == _last_remind_key() and last_version == version:
            return False
        return True
    except Exception:
        return True


def mark_reminded(version: str):
    """记录今天已为某版本弹窗提醒。"""
    try:
        s = QSettings()
        s.setValue("update/last_remind_date", _last_remind_key())
        s.setValue("update/last_remind_version", version)
    except Exception:
        pass


def open_releases_page():
    """在系统默认浏览器打开 Releases 页面。"""
    try:
        import webbrowser
        webbrowser.open(GITHUB_RELEASES_URL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 强制更新：版本过低时禁止使用软件
# ---------------------------------------------------------------------------
def is_force_update_disabled() -> bool:
    """隐藏设置：是否关闭了强制更新。"""
    try:
        from app.components.hidden_settings import get_hidden_setting, _to_bool
        return _to_bool(get_hidden_setting("force_update_disabled", False))
    except Exception:
        return False


def mark_force_update_required(remote_version: str):
    """记录需要强制更新（带记忆功能）。

    下次启动时即使检测不到新版本（网络故障等），只要此标记存在，
    就继续禁止用户使用软件，直到用户更新版本清除标记。
    """
    try:
        s = QSettings()
        s.setValue("update/force_update_required", "true")
        s.setValue("update/force_update_version", str(remote_version))
        s.sync()
    except Exception:
        pass


def clear_force_update_required():
    """清除强制更新标记（用户更新到新版本后调用）。"""
    try:
        s = QSettings()
        s.remove("update/force_update_required")
        s.remove("update/force_update_version")
        s.sync()
    except Exception:
        pass


def is_force_update_required() -> bool:
    """检查是否曾经检测到需要强制更新（记忆功能）。"""
    try:
        s = QSettings()
        val = s.value("update/force_update_required", "false")
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")
    except Exception:
        return False


def get_force_update_version() -> str:
    """获取触发强制更新的远程版本号。"""
    try:
        s = QSettings()
        return str(s.value("update/force_update_version", ""))
    except Exception:
        return ""


def check_and_clear_force_update_flag(current_version: str) -> bool:
    """如果当前版本已达到或超过强制更新版本，清除标记。

    返回 True 表示标记已被清除（用户已更新）。
    """
    try:
        required_version = get_force_update_version()
        if required_version and not is_newer(required_version, current_version):
            clear_force_update_required()
            return True
    except Exception:
        pass
    return False


class GitHubReleaseWorker(QThread):
    """从 GitHub API 获取最新 release 信息。

    成功时通过 result_ready 发射:
        {
            "version": "V4.0.1",
            "name": "V4.0.1 - 修复...",
            "notes": "## 更新内容\n...",
            "html_url": "https://github.com/...",
            "published_at": "2026-06-01T12:00:00Z",
        }
    失败时 error 非空。
    """
    result_ready = Signal(dict, str)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                GITHUB_API_LATEST,
                headers={
                    "User-Agent": "MemeKit/1.0",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = resp.read()
            if self._cancelled:
                return
            obj = json.loads(data.decode("utf-8", errors="ignore"))
            tag = (obj.get("tag_name") or "").strip()
            if not tag:
                self.result_ready.emit({}, "未获取到版本号")
                return
            info = {
                "version": tag,
                "name": (obj.get("name") or tag).strip(),
                "notes": (obj.get("body") or "").strip(),
                "html_url": (obj.get("html_url") or GITHUB_RELEASES_URL).strip(),
                "published_at": (obj.get("published_at") or "").strip(),
            }
            self.result_ready.emit(info, "")
        except Exception as e:
            if not self._cancelled:
                self.result_ready.emit({}, str(e))


class UpdateCheckerWorker(QThread):
    result_ready = Signal(dict, str)

    def __init__(self, url: str, current_version: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.current_version = current_version

    def run(self):
        try:
            if not self.url:
                self.result_ready.emit({}, "未配置更新地址")
                return
            url = self.url
            try:
                if "github.com" in url and "/blob/" in url:
                    parts = url.split("github.com/")[-1]
                    user_repo, rest = parts.split("/blob/", 1)
                    url = f"https://raw.githubusercontent.com/{user_repo}/{rest}"
                elif "gitee.com" in url and "/blob/" in url:
                    parts = url.split("gitee.com/")[-1]
                    user_repo, rest = parts.split("/blob/", 1)
                    url = f"https://gitee.com/{user_repo}/raw/{rest}"
            except Exception:
                pass
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={"User-Agent": "MemeKit/1.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = resp.read()
            text = data.decode("utf-8", errors="ignore").strip()
            obj = None
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    obj = parsed
            except Exception:
                obj = None
            if obj is None:
                kv = {}
                try:
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            k, v = line.split('=', 1)
                            kv[k.strip()] = v.strip()
                    if 'version' in kv:
                        obj = {
                            'version': kv.get('version', ''),
                            'url': kv.get('url', ''),
                            'notes': kv.get('notes', '')
                        }
                except Exception:
                    obj = None
            if obj is None:
                first = None
                for line in text.splitlines():
                    t = line.strip()
                    if t:
                        first = t
                        break
                if first:
                    obj = {'version': first}
            if not isinstance(obj, dict) or 'version' not in obj:
                self.result_ready.emit({}, "返回数据缺少版本信息")
                return
            self.result_ready.emit(obj, "")
        except Exception as e:
            self.result_ready.emit({}, str(e))