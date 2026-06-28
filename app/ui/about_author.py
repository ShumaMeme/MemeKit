"""关于作者对话框：带头像、音效、模糊背景的现代化弹窗。"""

import webbrowser
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, QPoint, QPropertyAnimation
from PySide6.QtGui import (
    QPixmap, QPainter, QPainterPath, QMouseEvent, QFont, QImage,
)
from app import get_project_root


class _AboutAuthorDialog(QDialog):
    """自定义无边框关于作者对话框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._closing = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(500, 340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        container = QWidget(self)
        container.setObjectName("aboutContainer")
        try:
            from qfluentwidgets import isDarkTheme
            is_dark = isDarkTheme()
        except Exception:
            is_dark = False
        bg_color = "#F5F3FF"
        container.setStyleSheet(f"""
            #aboutContainer {{
                background-color: {bg_color};
                border-radius: 16px;
            }}
        """)
        outer.addWidget(container)

        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_title_bar())
        root_layout.addWidget(self._build_body(), 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(300)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._opacity.setOpacity(0.0)
            self._fade_in.start()
        except Exception:
            pass

    def _fade_out_and_reject(self):
        if self._closing:
            return
        self._closing = True
        try:
            anim = QPropertyAnimation(self._opacity, b"opacity", self)
            anim.setDuration(220)
            try:
                anim.setStartValue(float(self._opacity.opacity()))
            except Exception:
                anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(self.reject)
            self._fade_out = anim
            anim.start()
        except Exception:
            try:
                self.reject()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 蓝色标题栏
    # ------------------------------------------------------------------
    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("""
            background-color: #2A74DA;
            border-top-left-radius: 16px;
            border-top-right-radius: 16px;
        """)
        bar.mousePressEvent = self._title_bar_press
        bar.mouseMoveEvent = self._title_bar_move

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(0)

        root = get_project_root()
        icon_path = root / "app_icon.png"
        icon_label = QLabel()
        if icon_path.exists():
            icon_pix = QPixmap(str(icon_path)).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(icon_pix)
        lay.addWidget(icon_label)
        lay.addSpacing(8)

        title = QLabel("关于作者")
        title.setStyleSheet("color: #FFFFFF; font-size: 15px; font-weight: 600; background: transparent;")
        lay.addWidget(title)
        lay.addStretch(1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #FFFFFF;
                border: none; border-radius: 6px; font-size: 16px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.2); }
        """)
        close_btn.clicked.connect(self._fade_out_and_reject)
        lay.addWidget(close_btn)
        return bar

    def _title_bar_press(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_move(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    # ------------------------------------------------------------------
    # 主体内容：左右布局，头像与标题顶部对齐
    # ------------------------------------------------------------------
    def _build_body(self) -> QWidget:
        body_widget = QWidget()
        body = QHBoxLayout(body_widget)
        body.setContentsMargins(24, 20, 24, 18)
        body.setSpacing(0)

        avatar_label = self._build_avatar()
        body.addWidget(avatar_label, 0, Qt.AlignTop)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(24, 0, 0, 0)
        info_layout.setSpacing(0)
        info_layout.addLayout(self._build_info())
        body.addLayout(info_layout, 1)

        return body_widget

    # ------------------------------------------------------------------
    # 头像
    # ------------------------------------------------------------------
    def _build_avatar(self) -> QWidget:
        avatar_size = 90
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

    # ------------------------------------------------------------------
    # 右侧信息区
    # ------------------------------------------------------------------
    def _build_info(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("关于作者")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setStyleSheet("color: #1D1B20; background: transparent;")
        layout.addWidget(title)
        layout.addSpacing(6)

        sub = QLabel("爱来自数码Meme")
        sub.setStyleSheet("font-size: 13px; color: #888888; background: transparent;")
        layout.addWidget(sub)
        layout.addSpacing(14)

        desc = QLabel("联系作者？请看下面👇")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 13px; color: #555555; line-height: 1.6; background: transparent;")
        layout.addWidget(desc)
        layout.addSpacing(14)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #E8E8E8;")
        layout.addWidget(sep)
        layout.addSpacing(14)

        contacts = [
            ("抖音:", "数码Meme"),
            ("哔哩哔哩:", "数码Meme"),
            ("🐧QQ:", "207594803"),
            ("GitHub:", "暂未上线，敬请期待"),
        ]
        for label_text, value in contacts:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(60)
            lbl.setStyleSheet("font-size: 13px; color: #888888; background: transparent;")
            row.addWidget(lbl)

            link = QLabel(f'<a href="#" style="color:#2A74DA; text-decoration:none;">{value}</a>')
            link.setOpenExternalLinks(False)

            def _make_handler(v):
                return lambda _u: self._on_link_clicked(v)

            link.linkActivated.connect(_make_handler(value))
            link.setStyleSheet("font-size: 13px; background: transparent;")
            row.addWidget(link)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addSpacing(8)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)
        btn_row.addStretch(1)

        close_btn = QPushButton("关闭")
        close_btn.setFixedSize(100, 36)
        close_btn.setStyleSheet("""
            QPushButton {
                color: #555555; background-color: #F5F5F5;
                border: none; border-radius: 8px; font-size: 14px;
            }
            QPushButton:hover { background-color: #EAEAEA; }
        """)
        close_btn.clicked.connect(self._fade_out_and_reject)
        btn_row.addWidget(close_btn)

        visit_btn = QPushButton("访问主页")
        visit_btn.setFixedSize(100, 36)
        visit_btn.setStyleSheet("""
            QPushButton {
                color: #FFFFFF; background-color: #2A74DA;
                border: none; border-radius: 8px; font-size: 14px;
            }
            QPushButton:hover { background-color: #2568C3; }
        """)
        visit_btn.clicked.connect(lambda: webbrowser.open("https://www.douyin.com/user/MS4wLjABAAAA3GF2zfQDuDBml_CcyI7mI-yI9QoXboNSUxIABci7p5Dn3CBrAuLdSp5h791lWl4T"))
        btn_row.addWidget(visit_btn)

        layout.addLayout(btn_row)
        return layout

    def _on_link_clicked(self, value: str):
        if "@" in value:
            webbrowser.open(f"mailto:{value}")
        elif "github.com" in value:
            webbrowser.open(f"https://{value}" if not value.startswith("http") else value)
        else:
            url = f"https://{value}" if not value.startswith("http") else value
            webbrowser.open(url)


def show_about_author(parent):
    """显示带模糊背景的关于作者对话框。"""
    from app.components.blur_popup import _BlurOverlay, _play_system_sound
    _play_system_sound()
    blur = _BlurOverlay(parent)
    try:
        dlg = _AboutAuthorDialog(parent)
        dlg.exec()
    except Exception:
        pass
    blur.dispose()
