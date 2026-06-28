from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication
)
from qfluentwidgets import (
    TitleLabel, PushButton, PrimaryPushButton, InfoBar, InfoBarPosition,
    FluentIcon, Theme, setTheme, MessageBox,
    SettingCardGroup, PushSettingCard, SettingCard, CaptionLabel, ComboBox, SmoothScrollArea
)

from app import get_project_root
from app.ui.about import show_about_with_blur
from app.ui.about_author import show_about_author
from app.version import VERSION
from app.components.blur_popup import show_blur_custom, _play_system_sound


# ---------------------------------------------------------------------------
# 旋转图标组件：通过 paintEvent 实现真正的旋转动画
# ---------------------------------------------------------------------------
class _SpinnerWidget(QWidget):
    """使用 QPainter.rotate() 在 paintEvent 中绘制旋转图标。"""

    def __init__(self, icon_size: int = 36, parent=None):
        super().__init__(parent)
        self._angle = 0.0
        self._icon_size = icon_size
        try:
            self._pixmap = FluentIcon.SYNC.icon().pixmap(icon_size, icon_size)
        except Exception:
            self._pixmap = QPixmap(icon_size, icon_size)
            self._pixmap.fill(Qt.transparent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.setInterval(25)
        self._timer.start()

    def _rotate(self):
        self._angle = (self._angle + 9) % 360
        self.update()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        p.translate(cx, cy)
        p.rotate(self._angle)
        p.drawPixmap(
            int(-self._icon_size / 2),
            int(-self._icon_size / 2),
            self._pixmap,
        )
        p.end()


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        try:
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        except Exception:
            pass

        self._scroll = SmoothScrollArea(self)
        self._scroll.setWidgetResizable(True)
        try:
            self._scroll.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        except Exception:
            pass
        layout.addWidget(self._scroll)

        container = QWidget()
        try:
            container.setStyleSheet("QWidget {background: transparent;}")
        except Exception:
            pass
        self._scroll.setWidget(container)

        self._content_layout = QVBoxLayout(container)
        try:
            self._content_layout.setContentsMargins(24, 24, 24, 24)
            self._content_layout.setSpacing(12)
        except Exception:
            pass

        # 顶部渐变 Banner（保持不变）
        from PySide6.QtWidgets import QWidget as _W
        banner_w = _W(self)
        try:
            banner_w.setFixedHeight(110)
        except Exception:
            pass
        # Banner 背景交由 Fluent 主题控制
        from PySide6.QtWidgets import QHBoxLayout as _H, QLabel as _L, QVBoxLayout as _V
        banner = _H(banner_w)
        banner.setContentsMargins(24, 18, 24, 18)
        banner.setSpacing(16)
        self._icon_lbl = _L("", banner_w)
        try:
            self._icon_lbl.setStyleSheet("background: transparent;")
            self._icon_lbl.setFixedSize(48, 48)
            self._icon_lbl.setAlignment(Qt.AlignCenter)
        except Exception:
            pass
        title_col = _V(); title_col.setContentsMargins(0,0,0,0); title_col.setSpacing(4)
        t = _L("设置中心", banner_w)
        try:
            t.setStyleSheet("font-size: 22px; font-weight: 600;")
        except Exception:
            pass
        s = _L("主题与工具检测", banner_w)
        try:
            s.setStyleSheet("font-size: 14px;")
        except Exception:
            pass
        title_col.addWidget(t); title_col.addWidget(s)
        banner.addWidget(self._icon_lbl); banner.addLayout(title_col); banner.addStretch(1)
        self._content_layout.addWidget(banner_w)

        # --- 外观设置 ---
        self.group_appearance = SettingCardGroup("外观", self)

        # 主题模式：使用 SettingCard + ComboBox
        self.card_theme = SettingCard(
            FluentIcon.BRUSH,
            "主题模式",
            "切换应用显示主题（浅色/深色/跟随系统）",
            self.group_appearance
        )
        self.combo_theme = ComboBox()
        self.combo_theme.addItems(["跟随系统", "浅色", "深色"])
        self.combo_theme.setMinimumWidth(120)
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)

        # 将 ComboBox 添加到卡片右侧
        self.card_theme.hBoxLayout.addWidget(self.combo_theme)
        self.card_theme.hBoxLayout.addSpacing(16)

        self.group_appearance.addSettingCard(self.card_theme)
        self._content_layout.addWidget(self.group_appearance)

        # --- 工具 ---
        self.group_tools = SettingCardGroup("工具", self)

        self.card_check_tools = PushSettingCard(
            "开始检测",
            FluentIcon.DEVELOPER_TOOLS if hasattr(FluentIcon, "DEVELOPER_TOOLS") else FluentIcon.toolbox,
            "工具检测",
            "检查 ADB、Fastboot、7z 等依赖工具是否就绪",
            self.group_tools
        )
        self.card_check_tools.clicked.connect(self._check_bin)
        self.group_tools.addSettingCard(self.card_check_tools)
        self._content_layout.addWidget(self.group_tools)

        # --- 关于 ---
        self.group_about = SettingCardGroup("关于", self)

        self.card_author = PushSettingCard(
            "查看",
            FluentIcon.USER if hasattr(FluentIcon, "USER") else FluentIcon.PEOPLE,
            "关于作者",
            "了解开发者信息",
            self.group_about
        )
        self.card_author.clicked.connect(self._show_about_author)

        self.card_about = PushSettingCard(
            "查看",
            FluentIcon.INFO,
            "更新日志",
            f"当前版本: {VERSION}",
            self.group_about
        )
        self.card_about.clicked.connect(self._show_about)

        self.card_update = PushSettingCard(
            "检查",
            FluentIcon.SYNC if hasattr(FluentIcon, "SYNC") else FluentIcon.UPDATE,
            "检查更新",
            "获取最新版本信息",
            self.group_about
        )
        self.card_update.clicked.connect(self._check_update)

        self.group_about.addSettingCard(self.card_author)
        self.group_about.addSettingCard(self.card_about)
        self.group_about.addSettingCard(self.card_update)
        self._content_layout.addWidget(self.group_about)

        self._content_layout.addStretch(1)

        # 刷新图标以适应当前主题
        self._refresh_icon()

        # Load Settings
        self._load_settings()

    def _refresh_icon(self):
        """刷新图标以适应当前主题"""
        try:
            _ico = FluentIcon.SETTING.icon()
            self._icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

    def showEvent(self, event):
        """显示时刷新图标，确保主题切换后图标正确更新"""
        super().showEvent(event)
        self._refresh_icon()

    def _load_settings(self):
        settings = QSettings()

        # Theme
        mode = settings.value("theme/mode", "system")
        if mode == "light":
            self.combo_theme.setCurrentIndex(1)
        elif mode == "dark":
            self.combo_theme.setCurrentIndex(2)
        else:
            self.combo_theme.setCurrentIndex(0)

    def _on_theme_changed(self, index):
        modes = {0: "system", 1: "light", 2: "dark"}
        mode = modes.get(index, "system")

        settings = QSettings()
        settings.setValue("theme/mode", mode)

        # Apply theme
        if mode == "light":
            setTheme(Theme.LIGHT)
        elif mode == "dark":
            setTheme(Theme.DARK)
        else:
            from app.ui.theme import detect_windows_theme
            sys_theme = detect_windows_theme()
            setTheme(Theme.DARK if sys_theme == "dark" else Theme.LIGHT)
            mode = sys_theme
        # 同步字体/对比度覆盖
        from app.ui.theme import apply_runtime_overlay
        app = QApplication.instance()
        if app is not None:
            apply_runtime_overlay(app, fallback_dark=(mode == "dark"))
        # 刷新所有 LogWidget 的主题样式
        from app.components.log_widget import LogWidget
        for widget in app.allWidgets():
            if isinstance(widget, LogWidget):
                try:
                    widget.refresh_theme()
                except Exception:
                    pass

    def _check_bin(self):
        base = get_project_root()
        candidates = [base / 'bin', Path.cwd() / 'bin']
        names = {
            'adb': ['adb.exe', 'adb'],
            'fastboot': ['fastboot.exe', 'fastboot'],
            '7z': ['7z.exe', '7za.exe', '7z'],
            'payload-dumper': ['payload-dumper-go.exe', 'payload-dumper.exe', 'payload-dumper-go']
        }
        found = {}
        for tool, files in names.items():
            ok = False
            for folder in candidates:
                for fn in files:
                    if (folder / fn).exists():
                        ok = True
                        break
                if ok:
                    break
            found[tool] = ok
        missing = [k for k, v in found.items() if not v]
        if not missing:
            InfoBar.success("检测完成", "所有工具已就绪", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        else:
            InfoBar.warning("缺少工具", "未找到：" + ", ".join(missing), parent=self, position=InfoBarPosition.TOP, isClosable=True)

    def _show_about(self):
        show_about_with_blur(self.window())

    def _show_about_author(self):
        show_about_author(self.window())

    def _check_update(self):
        """真实模糊背景 + 旋转图标 + 音效 + 弹窗关闭后模糊才消失。"""

        from app.components.blur_popup import _BlurOverlay
        blur = _BlurOverlay(self.window())

        # ---- 居中卡片 + 旋转图标 ----
        card = QWidget(blur._overlay)
        card.setFixedSize(220, 140)
        card.setStyleSheet(
            "background: rgba(255, 255, 255, 30); border-radius: 16px;"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setAlignment(Qt.AlignCenter)
        card_lay.setSpacing(12)

        spinner = _SpinnerWidget(36, card)
        spinner.setFixedSize(48, 48)
        card_lay.addWidget(spinner, alignment=Qt.AlignCenter)

        text_lbl = QLabel("正在检查更新…")
        text_lbl.setAlignment(Qt.AlignCenter)
        text_lbl.setStyleSheet(
            "color: #FFFFFF; font-size: 14px; font-weight: 500; background: transparent;"
        )
        card_lay.addWidget(text_lbl)

        card.move(
            (blur._overlay.width() - 220) // 2,
            (blur._overlay.height() - 140) // 2,
        )
        card.show()

        def _show_result():
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
            spinner.stop()
            card.hide()
            card.deleteLater()

            dlg = QDialog(self.window())
            dlg.setWindowTitle("检查更新")
            dlg.setModal(True)
            dlg.setMinimumWidth(380)
            dlg.setStyleSheet("""
                QDialog {
                    background-color: #F5F3FF;
                    border-radius: 10px;
                }
            """)

            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(14)

            title_lbl = QLabel("检查更新")
            title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #1D1B20;")
            title_lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(title_lbl)

            content_lbl = QLabel("想要啥功能呢🤔？请告诉我\n\n👾By 数码Meme")
            content_lbl.setWordWrap(True)
            content_lbl.setStyleSheet("font-size: 14px; color: #333333; padding: 8px 0;")
            content_lbl.setAlignment(Qt.AlignCenter)
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

            _play_system_sound()
            dlg.exec()
            blur.dispose()

        QTimer.singleShot(1000, _show_result)

    def cleanup(self):
        pass