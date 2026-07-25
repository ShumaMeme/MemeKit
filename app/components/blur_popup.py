"""全局弹窗遮罩工具：统一所有弹窗的半透明遮罩效果。

性能优化（相比旧实现）：
- 移除 parent.grab()：截取整个主窗口 pixmap 开销大（尤其大窗口）
- 移除 _blur_pixmap：4级金字塔模糊缩放，CPU 密集
- 移除 create_glass_background 合成：5层径向渐变叠加
- 改为纯 QWidget + rgba 半透明遮罩，零 pixmap 开销
"""


import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QLabel, QPushButton,
)


def _is_blur_disabled() -> bool:
    """隐藏设置：是否关闭了弹窗遮罩效果（性能模式下也自动关闭）。"""
    try:
        from app.components.hidden_settings import is_blur_disabled, is_performance_mode
        return is_blur_disabled() or is_performance_mode()
    except Exception:
        return False


def _make_blur_overlay(parent: QWidget, with_mask: bool = True):
    """创建半透明遮罩覆盖层。若隐藏设置关闭了遮罩效果则返回 None。

    with_mask=False 时不创建遮罩（用于 MaskDialogBase 子类，它们自带遮罩）。
    """
    if _is_blur_disabled():
        return None
    return _BlurOverlay(parent, with_mask=with_mask)


def _play_system_sound():
    """播放 Windows 系统提示音（异步，不阻塞 UI 线程）。

    性能优化：winsound.MessageBeep() 是同步调用，在某些系统上需要
    10-50ms 等待音频子系统响应，导致弹窗出现延迟。改为线程播放，
    让弹窗立即显示。
    """
    try:
        if sys.platform == 'win32':
            import threading
            import winsound
            def _beep():
                try:
                    winsound.MessageBeep(winsound.MB_OK)
                except Exception:
                    pass
            threading.Thread(target=_beep, daemon=True).start()
    except Exception:
        pass


def _is_mask_dialog(dialog) -> bool:
    """检查窗口是否为 qfluentwidgets 的 MaskDialogBase 子类。"""
    try:
        from qfluentwidgets import MaskDialogBase
        return isinstance(dialog, MaskDialogBase)
    except Exception:
        # 通过鸭子类型检测：有 _mask 属性且是 QWidget
        return hasattr(dialog, '_mask') and isinstance(getattr(dialog, '_mask', None), QWidget)


def _get_light_bg():
    """获取浅色模式下的浅蓝色背景。"""
    try:
        from qfluentwidgets import isDarkTheme
        if not isDarkTheme():
            return "#E3F0FF"
    except Exception:
        pass
    return "#202020"


def _get_light_card_bg():
    """获取弹窗背景色（跟随主题）。"""
    if _is_dark():
        return "rgba(45, 47, 58, 0.35)"
    return "#F0F5FF"


def _is_dark():
    try:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())
    except Exception:
        return False


def _dialog_text_color():
    return "#E6E1E5" if _is_dark() else "#1D1B20"


def _dialog_sub_text_color():
    return "#CCCCCC" if _is_dark() else "#333333"


class _BlurOverlay:
    """在父窗口上叠加半透明遮罩，dispose() 时自动清理。

    性能优化：移除 parent.grab() + _blur_pixmap（4级金字塔模糊）+
    create_glass_background（5层径向渐变），改为纯 QWidget + rgba 遮罩。
    零 pixmap 创建/绘制/内存开销，弹窗显示/关闭零延迟。
    """

    def __init__(self, parent: QWidget, with_mask: bool = True):
        self._parent = parent
        w, h = parent.width(), parent.height()

        # 半透明遮罩（纯 QWidget + rgba，零 pixmap 开销）
        self._blur_view = None  # 不再创建模糊 QLabel
        self._overlay = None
        if with_mask:
            self._overlay = QWidget(parent)
            _mask_alpha = 80 if _is_dark() else 100
            self._overlay.setStyleSheet(f"background: rgba(0, 0, 0, {_mask_alpha});")
            self._overlay.setGeometry(0, 0, w, h)
            self._overlay.show()
            self._overlay.raise_()

    def dispose(self):
        """清理遮罩。先 setParent(None) 断开与主窗口的关联，避免参与重绘计算。"""
        try:
            if hasattr(self, '_blur_view') and self._blur_view:
                try:
                    self._blur_view.setParent(None)
                except Exception:
                    pass
                self._blur_view.hide()
                self._blur_view.deleteLater()
                self._blur_view = None
        except Exception:
            pass
        try:
            if hasattr(self, '_overlay') and self._overlay:
                try:
                    self._overlay.setParent(None)
                except Exception:
                    pass
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
        except Exception:
            pass


def _make_plain_dialog(parent: QWidget, title: str, content: str) -> QDialog:
    """创建标准 QDialog（保留系统标题栏）。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    # 保留系统标题栏，使用 dialog_styles 的样式
    try:
        from app.components.dialog_styles import dialog_stylesheet
        dlg.setStyleSheet(dialog_stylesheet())
    except Exception:
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {_get_light_card_bg()};
            }}
        """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    content_lbl = QLabel(content)
    content_lbl.setWordWrap(True)
    content_lbl.setStyleSheet(f"font-size: 14px; color: {_dialog_sub_text_color()}; padding: 8px 0;")
    layout.addWidget(content_lbl)

    btn_layout = QVBoxLayout()
    btn_layout.setSpacing(8)

    _dark = _is_dark()
    _ok_bg = "#4A90E2" if _dark else "#2A74DA"
    _ok_hover = "#3A7FD2" if _dark else "#2568C3"
    _cancel_color = "#CCCCCC" if _dark else "#1D1B20"
    _cancel_bg = "rgba(255,255,255,0.08)" if _dark else "#E8E8E8"
    _cancel_hover = "rgba(255,255,255,0.14)" if _dark else "#D0D0D0"

    btn_ok = QPushButton("确定")
    btn_ok.setStyleSheet(f"""
        QPushButton {{
            color: #FFFFFF;
            background-color: {_ok_bg};
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }}
        QPushButton:hover {{
            background-color: {_ok_hover};
        }}
    """)
    btn_ok.clicked.connect(dlg.accept)
    btn_layout.addWidget(btn_ok)

    btn_cancel = QPushButton("取消")
    btn_cancel.setStyleSheet(f"""
        QPushButton {{
            color: {_cancel_color};
            background-color: {_cancel_bg};
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }}
        QPushButton:hover {{
            background-color: {_cancel_hover};
        }}
    """)
    btn_cancel.clicked.connect(dlg.reject)
    btn_layout.addWidget(btn_cancel)

    layout.addLayout(btn_layout)
    return dlg


def show_blur_dialog(parent: QWidget, title: str, content: str) -> bool:
    """显示带模糊背景的确认弹窗（确定/取消）。

    返回 True 表示用户点击了确定，False 表示取消。
    """
    _play_system_sound()
    blur = _make_blur_overlay(parent)

    dlg = _make_plain_dialog(parent, title, content)
    result = dlg.exec()

    if blur is not None:
        blur.dispose()
    return result == QDialog.Accepted


def show_blur_info(parent: QWidget, title: str, content: str):
    """显示带模糊背景的信息提示弹窗（仅确定按钮）。"""
    _play_system_sound()
    blur = _make_blur_overlay(parent)

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(400)
    # 保留系统标题栏，使用 dialog_styles 的样式
    try:
        from app.components.dialog_styles import dialog_stylesheet
        dlg.setStyleSheet(dialog_stylesheet())
    except Exception:
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {_get_light_card_bg()};
            }}
        """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    content_lbl = QLabel(content)
    content_lbl.setWordWrap(True)
    content_lbl.setStyleSheet(f"font-size: 14px; color: {_dialog_sub_text_color()}; padding: 8px 0; background: transparent;")
    layout.addWidget(content_lbl)

    _dark = _is_dark()
    _ok_bg = "#4A90E2" if _dark else "#2A74DA"
    _ok_hover = "#3A7FD2" if _dark else "#2568C3"
    btn_ok = QPushButton("确定")
    btn_ok.setStyleSheet(f"""
        QPushButton {{
            color: #FFFFFF;
            background-color: {_ok_bg};
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }}
        QPushButton:hover {{
            background-color: {_ok_hover};
        }}
    """)
    btn_ok.clicked.connect(dlg.accept)
    layout.addWidget(btn_ok, alignment=Qt.AlignCenter)

    # 居中到父窗口
    if parent is not None:
        try:
            pg = parent.geometry()
            dlg.move(pg.center() - dlg.rect().center())
        except Exception:
            pass

    dlg.exec()
    if blur is not None:
        blur.dispose()


def show_blur_custom(parent: QWidget, dialog) -> int:
    """显示带模糊背景的自定义弹窗。

    如果 dialog 是 MaskDialogBase 子类（自带遮罩），则创建仅含毛玻璃高光的
    overlay（不含半透明遮罩，避免双重遮罩），让 MaskDialogBase 弹窗也有
    毛玻璃高光效果。

    用法：
        dlg = MyCustomDialog(parent)
        result = show_blur_custom(parent, dlg)
        if result == QDialog.Accepted: ...

    返回 dialog.exec() 的结果。
    """
    import time as _time
    import sys as _sys
    _t0 = _time.perf_counter()
    _play_system_sound()

    # MaskDialogBase 子类自带遮罩，创建仅高光 overlay（跳过 dark mask 避免双重遮罩）
    if _is_mask_dialog(dialog):
        blur = _make_blur_overlay(parent, with_mask=False)
        _t_pre_exec = _time.perf_counter()
        _pre_ms = (_t_pre_exec - _t0) * 1000
        try:
            if _sys.stderr:
                _sys.stderr.write(f"[PERF] 弹窗显示前(含遮罩): {_pre_ms:.1f}ms\n")
        except Exception:
            pass
        result = dialog.exec()
        if blur is not None:
            blur.dispose()
        return result

    blur = _make_blur_overlay(parent)
    _t_pre_exec = _time.perf_counter()
    _pre_ms = (_t_pre_exec - _t0) * 1000
    try:
        if _sys.stderr:
            _sys.stderr.write(f"[PERF] 弹窗显示前(含遮罩): {_pre_ms:.1f}ms\n")
    except Exception:
        pass
    result = dialog.exec()
    if blur is not None:
        blur.dispose()
    return result


def show_blur_menu(parent: QWidget, menu, pos):
    """显示带毛玻璃高光背景的 RoundMenu。

    在菜单显示前在 parent 上创建毛玻璃高光覆盖层。
    菜单关闭后自动清理覆盖层。

    注意：RoundMenu 内部使用 MenuActionListWidget(QListWidget) +
    QStyledItemDelegate 渲染菜单项，QSS 的 color 属性对 delegate 无效。
    因此不设置自定义 QSS，让 qfluentwidgets 的 FluentStyleSheet.MENU
    自动适配深色/浅色主题（文字颜色由 QPalette 控制）。

    用法：
        menu = RoundMenu(parent=self)
        menu.addAction(...)
        show_blur_menu(self, menu, global_pos)
    """
    _play_system_sound()

    # 创建毛玻璃高光覆盖层（不含遮罩，避免与菜单阴影重叠）
    blur = _make_blur_overlay(parent, with_mask=False)

    menu.exec(pos)

    if blur is not None:
        blur.dispose()