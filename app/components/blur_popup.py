"""全局模糊弹窗工具：统一所有弹窗的模糊背景效果。"""


import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QLabel, QPushButton,
)


def _play_system_sound():
    """播放 Windows 系统提示音。"""
    try:
        if sys.platform == 'win32':
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
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
    """获取浅色模式下的浅紫色背景。"""
    try:
        from qfluentwidgets import isDarkTheme
        if not isDarkTheme():
            return "#EDE9FE"
    except Exception:
        pass
    return "#202020"


def _get_light_card_bg():
    """获取弹窗的浅紫色背景，深色浅色模式统一。"""
    return "#F5F3FF"


def _is_dark():
    try:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())
    except Exception:
        return False


def _dialog_text_color():
    return "#1D1B20"


def _dialog_sub_text_color():
    return "#333333"


def _blur_pixmap(pixmap: QPixmap, iterations: int = 1) -> QPixmap:
    """用缩小-放大法快速模糊 pixmap。

    彻底替代 QGraphicsBlurEffect —— 后者即使 widget 被 hide 仍会持续
    触发主窗口重绘，导致弹窗关闭后整个软件卡顿。
    预渲染模糊只计算一次，用 QLabel 显示静态图片，零持续开销。
    """
    if pixmap.isNull():
        return pixmap
    w, h = pixmap.width(), pixmap.height()
    if w <= 0 or h <= 0:
        return pixmap
    scale = 0.5
    result = pixmap.scaled(int(w * scale), int(h * scale), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    for _ in range(iterations):
        sw = max(1, result.width() // 3)
        sh = max(1, result.height() // 3)
        result = result.scaled(sw, sh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return result.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class _BlurOverlay:
    """在父窗口上叠加模糊背景 + 半透明遮罩，dispose() 时自动清理。

    使用预渲染模糊图片（QLabel）替代 QGraphicsBlurEffect，
    避免后者在 widget 隐藏后仍持续触发重绘导致软件卡顿。"""

    def __init__(self, parent: QWidget):
        self._parent = parent
        w, h = parent.width(), parent.height()

        # 截取主窗口并预渲染模糊（只计算一次，不持续消耗资源）
        screenshot = parent.grab()
        blurred = _blur_pixmap(screenshot)

        # 用 QLabel 显示静态模糊图片（零持续开销）
        self._blur_view = QLabel(parent)
        self._blur_view.setPixmap(blurred)
        self._blur_view.setGeometry(0, 0, w, h)
        self._blur_view.setStyleSheet("background: transparent; border: none;")
        self._blur_view.show()
        self._blur_view.raise_()

        # 半透明遮罩
        self._overlay = QWidget(parent)
        self._overlay.setStyleSheet("background: rgba(0, 0, 0, 80);")
        self._overlay.setGeometry(0, 0, w, h)
        self._overlay.show()
        self._overlay.raise_()

    def dispose(self):
        """清理模糊背景和遮罩。
        先 setParent(None) 断开与主窗口的关联，避免参与重绘计算，
        再 hide + deleteLater。"""
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
    """创建纯 QDialog（避免 MaskDialogBase 遮罩冲突）。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    dlg.setStyleSheet(f"""
        QDialog {{
            background-color: {_get_light_card_bg()};
            border-radius: 10px;
        }}
    """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {_dialog_text_color()};")
    title_lbl.setAlignment(Qt.AlignCenter)
    layout.addWidget(title_lbl)

    content_lbl = QLabel(content)
    content_lbl.setWordWrap(True)
    content_lbl.setStyleSheet(f"font-size: 14px; color: {_dialog_sub_text_color()}; padding: 8px 0;")
    layout.addWidget(content_lbl)

    btn_layout = QVBoxLayout()
    btn_layout.setSpacing(8)

    btn_ok = QPushButton("确定")
    btn_ok.setStyleSheet("""
        QPushButton {
            color: #FFFFFF;
            background-color: #2A74DA;
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }
        QPushButton:hover {
            background-color: #2568C3;
        }
    """)
    btn_ok.clicked.connect(dlg.accept)
    btn_layout.addWidget(btn_ok)

    btn_cancel = QPushButton("取消")
    btn_cancel.setStyleSheet("""
        QPushButton {
            color: #1D1B20;
            background-color: #E8E8E8;
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }
        QPushButton:hover {
            background-color: #D0D0D0;
        }
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
    blur = _BlurOverlay(parent)

    dlg = _make_plain_dialog(parent, title, content)
    result = dlg.exec()

    blur.dispose()
    return result == QDialog.Accepted


def show_blur_info(parent: QWidget, title: str, content: str):
    """显示带模糊背景的信息提示弹窗（仅确定按钮）。"""
    _play_system_sound()
    blur = _BlurOverlay(parent)

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(400)
    dlg.setStyleSheet(f"""
        QDialog {{
            background-color: {_get_light_card_bg()};
            border-radius: 10px;
        }}
    """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {_dialog_text_color()};")
    title_lbl.setAlignment(Qt.AlignCenter)
    layout.addWidget(title_lbl)

    content_lbl = QLabel(content)
    content_lbl.setWordWrap(True)
    content_lbl.setStyleSheet(f"font-size: 14px; color: {_dialog_sub_text_color()}; padding: 8px 0;")
    layout.addWidget(content_lbl)

    btn_ok = QPushButton("确定")
    btn_ok.setStyleSheet("""
        QPushButton {
            color: #FFFFFF;
            background-color: #2A74DA;
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
        }
        QPushButton:hover {
            background-color: #2568C3;
        }
    """)
    btn_ok.clicked.connect(dlg.accept)
    layout.addWidget(btn_ok, alignment=Qt.AlignCenter)

    dlg.exec()
    blur.dispose()


def show_blur_custom(parent: QWidget, dialog) -> int:
    """显示带模糊背景的自定义弹窗。

    如果 dialog 是 MaskDialogBase 子类（自带遮罩），则跳过 _BlurOverlay，
    避免双重遮罩导致事件循环卡死。

    用法：
        dlg = MyCustomDialog(parent)
        result = show_blur_custom(parent, dlg)
        if result == QDialog.Accepted: ...

    返回 dialog.exec() 的结果。
    """
    _play_system_sound()

    # MaskDialogBase 子类自带遮罩，不需要额外模糊背景
    if _is_mask_dialog(dialog):
        return dialog.exec()

    blur = _BlurOverlay(parent)
    result = dialog.exec()
    blur.dispose()
    return result