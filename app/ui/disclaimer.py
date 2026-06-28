"""精美免责声明对话框 - 与启动窗口保持一致的玻璃拟态设计风格"""
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QPropertyAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QRadialGradient, QLinearGradient
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect, QPushButton, QHBoxLayout


class DisclaimerDialog(QWidget):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, icon_path: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._light = True
        self._pulse = 0.0
        self._pulse_dir = 1.0
        self._closing = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._pix = QPixmap(str(Path(icon_path))) if icon_path else QPixmap()

        root = QVBoxLayout(self)
        root.setContentsMargins(52, 40, 52, 36)
        root.setSpacing(22)

        # Logo
        self.logo = QLabel(self)
        self.logo.setAlignment(Qt.AlignCenter)
        if not self._pix.isNull():
            self.logo.setPixmap(self._pix.scaled(QSize(120, 120), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        root.addWidget(self.logo, 0, Qt.AlignHCenter)

        # Title
        self.title = QLabel("⚠️ 免责声明", self)
        self.title.setAlignment(Qt.AlignCenter)
        f = QFont()
        try:
            f.setPointSize(18)
            f.setBold(True)
        except Exception:
            pass
        self.title.setFont(f)
        self.title.setStyleSheet("color: rgba(18, 18, 20, 230);")
        root.addWidget(self.title)

        # Content
        content_text = (
            "本软件 MemeKit 为免费工具，仅供个人技术学习交流，无任何付费项目。\n\n"
            "获取 Root 权限、刷写字库、刷机等相关操作可能存在设备变砖、"
            "数据丢失、失去官方保修等风险，请操作前自行备份全部数据，"
            "风险由使用者自行承担，禁止用于商业用途！"
        )
        self.content = QLabel(content_text, self)
        self.content.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.content.setWordWrap(True)
        f2 = QFont()
        try:
            f2.setPointSize(13)
        except Exception:
            pass
        self.content.setFont(f2)
        self.content.setStyleSheet("color: rgba(60, 60, 65, 220); line-height: 1.8;")
        self.content.setMinimumHeight(120)
        root.addWidget(self.content)

        # Button Row
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(16)
        btn_lay.setContentsMargins(20, 0, 20, 0)

        self.btn_reject = QPushButton("我拒绝并退出", self)
        self.btn_reject.setFixedHeight(42)
        self.btn_reject.setMinimumWidth(140)
        self.btn_reject.setStyleSheet("""
            QPushButton {
                color: #555555;
                background-color: rgba(240, 240, 245, 200);
                border: 1px solid rgba(200, 200, 210, 180);
                border-radius: 10px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(230, 230, 235, 220);
                border-color: rgba(180, 180, 190, 200);
            }
            QPushButton:pressed {
                background-color: rgba(210, 210, 215, 220);
            }
        """)

        self.btn_accept = QPushButton("我已阅读并同意", self)
        self.btn_accept.setFixedHeight(42)
        self.btn_accept.setMinimumWidth(140)
        self.btn_accept.setStyleSheet("""
            QPushButton {
                color: #FFFFFF;
                background-color: #2A74DA;
                border: none;
                border-radius: 10px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2568C3;
            }
            QPushButton:pressed {
                background-color: #1F5CB0;
            }
        """)

        btn_lay.addStretch(1)
        btn_lay.addWidget(self.btn_reject)
        btn_lay.addWidget(self.btn_accept)
        btn_lay.addStretch(1)
        root.addLayout(btn_lay)

        # Opacity effect for fade animation
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(300)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)

        self.resize(540, 480)

        # Connect signals
        self.btn_reject.clicked.connect(self._on_reject)
        self.btn_accept.clicked.connect(self._on_accept)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._opacity.setOpacity(0.0)
            self._fade_in.start()
        except Exception:
            pass

    def fade_out_and_close(self, *, duration_ms: int = 220):
        if self._closing:
            return
        self._closing = True
        try:
            anim = QPropertyAnimation(self._opacity, b"opacity", self)
            anim.setDuration(int(duration_ms))
            try:
                anim.setStartValue(float(self._opacity.opacity()))
            except Exception:
                anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(self.close)
            self._fade_out = anim
            anim.start()
        except Exception:
            try:
                self.close()
            except Exception:
                pass

    def center_on_screen(self):
        try:
            scr = self.screen()
            geo = scr.availableGeometry() if scr else None
            if geo:
                x = geo.x() + (geo.width() - self.width()) // 2
                y = geo.y() + (geo.height() - self.height()) // 2
                self.move(x, y)
        except Exception:
            pass

    def _on_reject(self):
        self.rejected.emit()
        self.fade_out_and_close()

    def _on_accept(self):
        self.accepted.emit()
        self.fade_out_and_close()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        r = self.rect().adjusted(0, 0, -1, -1)
        radius = 26

        # Base glass panel
        base_grad = QLinearGradient(r.topLeft(), r.bottomRight())
        base_grad.setColorAt(0.0, QColor(248, 249, 252, 245))
        base_grad.setColorAt(0.5, QColor(244, 246, 250, 248))
        base_grad.setColorAt(1.0, QColor(236, 238, 244, 245))
        p.setPen(Qt.NoPen)
        p.setBrush(base_grad)
        p.drawRoundedRect(r, radius, radius)

        # Breathing glow
        cx = r.center().x()
        cy = int(r.top() + r.height() * 0.35)
        rr = int(min(r.width(), r.height()) * 0.75)
        a1 = int(92 + 88 * self._pulse)
        a2 = int(24 + 36 * self._pulse)
        grad = QRadialGradient(cx, cy, rr)
        grad.setColorAt(0.0, QColor(42, 116, 218, a1))
        grad.setColorAt(0.42, QColor(42, 116, 218, a2))
        grad.setColorAt(1.0, QColor(42, 116, 218, 0))
        p.setBrush(grad)
        p.drawRoundedRect(r, radius, radius)

        # Subtle vignette
        v = QRadialGradient(cx, cy, int(min(r.width(), r.height()) * 0.95))
        v.setColorAt(0.0, QColor(0, 0, 0, 0))
        v.setColorAt(1.0, QColor(0, 0, 0, 40))
        p.setBrush(v)
        p.drawRoundedRect(r, radius, radius)

        # Inner shadow
        inner = QLinearGradient(r.topLeft(), r.bottomLeft())
        inner.setColorAt(0.0, QColor(255, 255, 255, int(110 + 40 * self._pulse)))
        inner.setColorAt(0.22, QColor(255, 255, 255, 0))
        inner.setColorAt(0.82, QColor(0, 0, 0, 0))
        inner.setColorAt(1.0, QColor(0, 0, 0, 28))
        p.setBrush(inner)
        p.drawRoundedRect(r, radius, radius)

        # Glass border highlight
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor(255, 255, 255, int(140 + 40 * self._pulse)))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius - 1, radius - 1)

        p.end()

    def _tick_pulse(self):
        self._pulse += 0.018 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse = 1.0
            self._pulse_dir = -1.0
        elif self._pulse <= 0.0:
            self._pulse = 0.0
            self._pulse_dir = 1.0
        self.update()

    def start_pulse(self):
        from PySide6.QtCore import QTimer
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_timer.start(16)

    def closeEvent(self, event):
        try:
            if hasattr(self, '_pulse_timer') and self._pulse_timer:
                self._pulse_timer.stop()
                self._pulse_timer.deleteLater()
                self._pulse_timer = None
        except Exception:
            pass
        super().closeEvent(event)


# For backwards compatibility
def show_disclaimer(parent=None, icon_path=""):
    dlg = DisclaimerDialog(icon_path=icon_path, parent=parent)
    dlg.center_on_screen()
    dlg.start_pulse()
    dlg.show()
    return dlg


from PySide6.QtCore import QTimer