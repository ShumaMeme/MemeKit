from PySide6.QtWidgets import QApplication
from typing import Literal

try:
    import winreg  # Windows-only
except Exception:  # pragma: no cover
    winreg = None

try:
    from qfluentwidgets import isDarkTheme as _is_dark_theme
except Exception:  # pragma: no cover
    _is_dark_theme = None

ThemeMode = Literal["system", "light", "dark"]

_DARK_OVERLAY = """
/* 仅设置文字颜色，不设置 background-color，让毛玻璃半透明背景生效。
   CardWidget 的透明度由 apply_card_glass_alpha() 控制，
   QDialog 的背景由 dialog_stylesheet() 控制，
   QTextBrowser 的背景由 glass_widgets_qss() / log_widget 控制。 */
CardWidget { color: #E6E1E5; }
QDialog { color: #ffffff; }
QLabel,
QTextEdit,
QPlainTextEdit,
QTextBrowser,
QLineEdit,
QListWidget,
QListView,
QTreeWidget,
QTreeView,
QTableWidget,
QTableView {
    color: #ffffff;
}
/* FluentWindow 侧边栏与标题栏在深色模式下必须有不透明背景 */
QFrame#navigationInterface, QFrame#viewLayout, QWidget#navigationInterface {
    background-color: #1E1E1E;
}
FluentTitleBar, QWidget#titleBar {
    background-color: #1E1E1E;
}
"""

_LIGHT_OVERLAY = """
/* 注意：不要给 QWidget 全局设置 background-color，否则会污染 splash/弹窗的子 QLabel，
   导致透明窗口出现"白色横条"。背景色由各控件自身或更具体的 QSS 规则管理。
   CardWidget/QDialog/QTextBrowser 不设 background-color，让毛玻璃半透明样式生效。 */
QWidget { color: #1D1B20; }
CardWidget { color: #1D1B20; }
QLabel,
QTextEdit,
QPlainTextEdit,
QLineEdit,
QListWidget,
QListView,
QTreeWidget,
QTreeView,
QTableWidget,
QTableView {
    color: #1D1B20;
}
QTextBrowser {
    color: #1f2329;
    selection-background-color: #E3F0FF;
    selection-color: #1f2329;
}
QDialog { color: #1D1B20; }
/* FluentWindow 侧边栏与标题栏在浅色模式下必须有不透明背景，否则会与 DWM 材质叠加导致不可见 */
QFrame#navigationInterface, QFrame#viewLayout, QWidget#navigationInterface {
    background-color: #F3F3F3;
}
FluentTitleBar, QWidget#titleBar {
    background-color: #F3F3F3;
}
"""


def detect_windows_theme() -> Literal["light", "dark"]:
    """Read Windows AppsUseLightTheme: 1=light, 0=dark. Default to light if unavailable."""
    try:
        if winreg is None:
            return "light"
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize"
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if int(val) == 1 else "dark"
    except Exception:
        return "light"


def apply_runtime_overlay(app: QApplication | None, fallback_dark: bool = False):
    """Apply a lightweight stylesheet overlay so text colors match the current theme."""
    if app is None:
        return
    try:
        is_dark = bool(_is_dark_theme()) if _is_dark_theme else fallback_dark
    except Exception:
        is_dark = fallback_dark
    app.setStyleSheet(_DARK_OVERLAY if is_dark else _LIGHT_OVERLAY)
