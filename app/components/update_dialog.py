"""发现新版本提醒弹窗。

在软件启动后仪表盘首次显示时异步检查 GitHub Releases，
若有新版本（远程版本严格大于当前版本）且今天尚未提醒过该版本，
则弹出带模糊背景的更新提醒对话框，展示：
  - 标题：发现新版本
  - 新版本号
  - 新版本更新日志（Markdown 原文/纯文本）
  - 两个按钮：「前往下载」（打开浏览器） / 「稍后再说」（关闭）
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QLabel, QPushButton, QScrollArea,
)

from app.components.blur_popup import (
    show_blur_custom, _play_system_sound,
    _dialog_text_color, _dialog_sub_text_color,
    _is_dark,
)
from app.services import log_service
from app.services.update_checker import (
    open_releases_page, mark_reminded,
)


class UpdateAvailableDialog(QDialog):
    """发现新版本提醒对话框。

    通过 show_blur_custom 调用以获得模糊背景。
    """

    def __init__(self, parent: QWidget, info: dict, current_version: str):
        super().__init__(parent)
        self._info = info
        self._current_version = current_version
        self.setWindowTitle("发现新版本")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setMinimumHeight(360)
        # 使用统一弹窗样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(self)
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)

        # 标题
        title_lbl = QLabel("✨ 发现新版本")
        title_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {_dialog_text_color()};"
        )
        title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_lbl)

        # 版本号行
        remote_ver = info.get("version", "")
        ver_text = f"最新版本：<b>{remote_ver}</b>    当前版本：{current_version}"
        ver_lbl = QLabel(ver_text)
        ver_lbl.setTextFormat(Qt.RichText)
        ver_lbl.setAlignment(Qt.AlignCenter)
        ver_lbl.setStyleSheet(
            f"font-size: 14px; color: {_dialog_text_color()}; padding: 4px 0;"
        )
        layout.addWidget(ver_lbl)

        # 发布时间（可选）
        published_at = info.get("published_at", "")
        if published_at:
            # 形如 2026-06-01T12:00:00Z -> 2026-06-01
            date_str = published_at.split("T")[0] if "T" in published_at else published_at
            date_lbl = QLabel(f"发布日期：{date_str}")
            date_lbl.setAlignment(Qt.AlignCenter)
            date_lbl.setStyleSheet(
                f"font-size: 12px; color: {_dialog_sub_text_color()};"
            )
            layout.addWidget(date_lbl)

        # 分隔标签
        notes_title = QLabel("更新日志")
        notes_title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {_dialog_text_color()};"
        )
        layout.addWidget(notes_title)

        # 更新日志（可滚动）
        notes = (info.get("notes") or "暂无更新日志").strip()
        if not notes:
            notes = "暂无更新日志"

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        scroll.setMinimumHeight(140)

        notes_container = QWidget()
        notes_container.setStyleSheet("background: transparent;")
        notes_lay = QVBoxLayout(notes_container)
        notes_lay.setContentsMargins(0, 0, 0, 0)
        notes_lay.setSpacing(0)

        notes_lbl = QLabel(notes)
        notes_lbl.setWordWrap(True)
        notes_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        notes_lbl.setContextMenuPolicy(Qt.NoContextMenu)
        notes_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        notes_lbl.setStyleSheet(
            f"font-size: 13px; color: {_dialog_sub_text_color()}; line-height: 160%;"
        )
        notes_lay.addWidget(notes_lbl)
        notes_lay.addStretch(1)

        scroll.setWidget(notes_container)
        layout.addWidget(scroll, 1)

        # 按钮行
        btn_lay = QVBoxLayout()
        btn_lay.setSpacing(8)

        _dark = _is_dark()
        _dl_bg = "#4A90E2" if _dark else "#2A74DA"
        _dl_hover = "#3A7FD2" if _dark else "#2568C3"
        _dl_pressed = "#2A6FB8" if _dark else "#1F56A3"
        _later_color = "#CCCCCC" if _dark else "#555555"
        _later_border = "rgba(255,255,255,0.20)" if _dark else "#CCCCCC"
        _later_hover = "rgba(255,255,255,0.06)" if _dark else "rgba(0,0,0,0.04)"

        btn_download = QPushButton("前往下载")
        btn_download.setCursor(Qt.PointingHandCursor)
        btn_download.setStyleSheet(f"""
            QPushButton {{
                color: #FFFFFF;
                background-color: {_dl_bg};
                border: none;
                border-radius: 6px;
                padding: 11px 24px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {_dl_hover}; }}
            QPushButton:pressed {{ background-color: {_dl_pressed}; }}
        """)
        btn_download.clicked.connect(self._on_download_clicked)
        btn_lay.addWidget(btn_download)

        btn_later = QPushButton("稍后再说")
        btn_later.setCursor(Qt.PointingHandCursor)
        btn_later.setStyleSheet(f"""
            QPushButton {{
                color: {_later_color};
                background-color: transparent;
                border: 1px solid {_later_border};
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {_later_hover}; }}
        """)
        btn_later.clicked.connect(self._on_later_clicked)
        btn_lay.addWidget(btn_later)

        layout.addLayout(btn_lay)

    def _on_later_clicked(self):
        try:
            log_service.log_ui_action("更新提醒", "点击稍后再说，关闭弹窗")
        except Exception:
            pass
        self.reject()

    def _on_download_clicked(self):
        try:
            open_releases_page()
        except Exception:
            pass
        try:
            log_service.log_ui_action("更新提醒", "点击前往下载")
        except Exception:
            pass
        self.accept()


def show_update_available(parent: QWidget, info: dict, current_version: str):
    """显示更新提醒弹窗，并记录今日已提醒。"""
    try:
        _play_system_sound()
    except Exception:
        pass
    dlg = UpdateAvailableDialog(parent, info, current_version)
    # 标记今日已提醒，避免重复弹窗
    try:
        mark_reminded(info.get("version", ""))
    except Exception:
        pass
    try:
        log_service.log_ui_action(
            "更新提醒",
            f"新版本 {info.get('version', '?')}（当前 {current_version}）",
        )
    except Exception:
        pass
    show_blur_custom(parent, dlg)
