"""关于作者对话框：带头像、音效、模糊背景的弹窗。

使用标准 QDialog（系统标题栏），与 about.py 保持一致，
避免 FramelessWindowHint + WA_TranslucentBackground 在 Windows 上
触发 DWM 合成层导致的显示延迟（不跟手）。
"""
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QFont, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QSizePolicy,
)
from app import get_project_root

try:
    from qfluentwidgets import isDarkTheme
except Exception:
    def isDarkTheme():
        return False


class _AboutAuthorDialog(QDialog):
    """关于作者对话框：标准 QDialog，避免无边框半透明窗口的显示延迟。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于作者")
        self.setModal(True)
        self.setFixedSize(640, 440)
        # 使用统一弹窗样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(self)
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_body(), 1)

    def _build_body(self) -> QWidget:
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(40, 32, 40, 28)
        root.setSpacing(0)

        # 主题感知颜色
        dark = isDarkTheme()
        title_color = "#E6E1E5" if dark else "#1D1B20"
        sub_color = "#9AA0A6" if dark else "#888888"
        desc_color = "#BDBDBD" if dark else "#555555"
        sep_color = "rgba(255, 255, 255, 0.08)" if dark else "#E8E8E8"
        link_color = "#4A90E2" if dark else "#2A74DA"
        close_btn_color = "#CCCCCC" if dark else "#555555"
        close_btn_bg = "rgba(255, 255, 255, 0.08)" if dark else "#F5F5F5"
        close_btn_hover = "rgba(255, 255, 255, 0.14)" if dark else "#EAEAEA"
        visit_btn_bg = "#4A90E2" if dark else "#2A74DA"
        visit_btn_hover = "#3A7FD2" if dark else "#2568C3"

        # ---- 顶部：头像 + 标题区（水平对齐） ----
        top_row = QHBoxLayout()
        top_row.setSpacing(24)
        top_row.setAlignment(Qt.AlignTop)

        avatar_label = self._build_avatar()
        top_row.addWidget(avatar_label, 0, Qt.AlignTop)

        # 标题区：给顶部加 4px 偏移，让大标题文字的视觉顶部与头像圆形顶部对齐
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 4, 0, 0)
        title_col.setSpacing(0)

        title = QLabel("关于作者")
        title.setFont(QFont("Microsoft YaHei", 24, QFont.Bold))
        title.setStyleSheet(f"color: {title_color}; background: transparent;")
        title_col.addWidget(title)
        title_col.addSpacing(6)

        sub = QLabel("爱来自数码Meme")
        sub.setStyleSheet(f"font-size: 15px; color: {sub_color}; background: transparent;")
        title_col.addWidget(sub)
        title_col.addSpacing(16)

        desc = QLabel("联系作者？请看下面👇")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 14px; color: {desc_color}; line-height: 1.6; background: transparent;")
        title_col.addWidget(desc)
        title_col.addStretch(1)

        top_row.addLayout(title_col, 1)
        root.addLayout(top_row)

        root.addSpacing(20)

        # ---- 分隔线 ----
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {sep_color};")
        root.addWidget(sep)
        root.addSpacing(16)

        # ---- 联系方式 ----
        contacts = [
            ("抖音：", "数码Meme"),
            ("哔哩哔哩：", "数码Meme"),
            ("🐧QQ：", "207594803"),
            ("GitHub：", "https://github.com/ShumaMeme/MemeKit"),
        ]
        grid = QVBoxLayout()
        grid.setSpacing(8)
        for label_text, value in contacts:
            row = QHBoxLayout()
            row.setSpacing(12)

            lbl = QLabel(label_text)
            lbl.setFixedWidth(90)
            lbl.setStyleSheet(f"font-size: 14px; color: {sub_color}; background: transparent;")
            row.addWidget(lbl)

            is_url = value.startswith("http")
            if is_url:
                display = value
                link_html = f'<a href="{value}" style="color:{link_color}; text-decoration:none;">{display}</a>'
            else:
                link_html = f'<a href="#" style="color:{link_color}; text-decoration:none;">{value}</a>'

            link = QLabel(link_html)
            link.setOpenExternalLinks(False)
            link.setTextInteractionFlags(Qt.TextBrowserInteraction)
            link.setContextMenuPolicy(Qt.NoContextMenu)
            link.setStyleSheet(f"font-size: 14px; color: {link_color}; background: transparent;")

            def _make_handler(v):
                return lambda _u: self._on_link_clicked(v)

            link.linkActivated.connect(_make_handler(value))
            link.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(link, 1)
            grid.addLayout(row)

        root.addLayout(grid)
        root.addStretch(1)

        # ---- 按钮行 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)
        btn_row.addStretch(1)

        close_btn = QPushButton("关闭")
        close_btn.setFixedSize(110, 40)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {close_btn_color}; background-color: {close_btn_bg};
                border: none; border-radius: 8px; font-size: 15px;
            }}
            QPushButton:hover {{ background-color: {close_btn_hover}; }}
        """)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        visit_btn = QPushButton("访问主页")
        visit_btn.setFixedSize(110, 40)
        visit_btn.setStyleSheet(f"""
            QPushButton {{
                color: #FFFFFF; background-color: {visit_btn_bg};
                border: none; border-radius: 8px; font-size: 15px;
            }}
            QPushButton:hover {{ background-color: {visit_btn_hover}; }}
        """)
        visit_btn.clicked.connect(lambda: webbrowser.open(
            "https://www.douyin.com/user/MS4wLjABAAAA3GF2zfQDuDBml_CcyI7mI-yI9QoXboNSUxIABci7p5Dn3CBrAuLdSp5h791lWl4T"
        ))
        btn_row.addWidget(visit_btn)

        root.addLayout(btn_row)
        return body

    def _build_avatar(self) -> QWidget:
        avatar_size = 110
        root = get_project_root()
        avatar_path = root / "数码Meme.png"

        image = QImage(avatar_size, avatar_size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        clip = QPainterPath()
        clip.addEllipse(0, 0, avatar_size, avatar_size)
        painter.setClipPath(clip)

        if avatar_path.exists():
            src = QPixmap(str(avatar_path))
            if not src.isNull():
                s = min(src.width(), src.height())
                x = (src.width() - s) // 2
                y = (src.height() - s) // 2
                cropped = src.copy(x, y, s, s)
                scaled = cropped.scaled(
                    avatar_size, avatar_size,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
                painter.drawPixmap(0, 0, scaled)
        painter.end()

        pixmap = QPixmap.fromImage(image)
        label = QLabel()
        label.setPixmap(pixmap)
        label.setFixedSize(avatar_size, avatar_size)
        return label

    def _on_link_clicked(self, value: str):
        if "@" in value:
            webbrowser.open(f"mailto:{value}")
        elif value.startswith("http"):
            webbrowser.open(value)
        else:
            webbrowser.open(f"https://{value}")


def show_about_author(parent):
    """显示带模糊背景的关于作者对话框。"""
    from app.components.blur_popup import _make_blur_overlay, _play_system_sound
    _play_system_sound()
    blur = _make_blur_overlay(parent)
    try:
        dlg = _AboutAuthorDialog(parent)
        dlg.exec()
    except Exception:
        pass
    if blur is not None:
        blur.dispose()
