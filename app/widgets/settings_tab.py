from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication, QFileDialog
)
from qfluentwidgets import (
    InfoBar, InfoBarPosition, FluentIcon, Theme, setTheme,
    SettingCardGroup, PushSettingCard, SettingCard, ComboBox,
    SmoothScrollArea, SwitchButton
)

from app import get_project_root
from app.ui.about import show_about_with_blur
from app.ui.about_author import show_about_author
from app.version import VERSION
from app.components.hidden_settings import (
    is_performance_mode, set_hidden_setting, is_blur_disabled,
    is_tab_animation_disabled, is_dialog_animation_disabled,
    is_low_polling_enabled,
)
from app.services import log_service
from app.components.glass_style import apply_banner_style, refresh_banner_style


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
            container.setStyleSheet("background: transparent;")
        except Exception:
            pass
        self._scroll.setWidget(container)

        self._content_layout = QVBoxLayout(container)
        try:
            self._content_layout.setContentsMargins(20, 20, 20, 20)
            self._content_layout.setSpacing(24)
        except Exception:
            pass

        # 顶部渐变 Banner（保持不变）
        from PySide6.QtWidgets import QWidget as _W
        banner_w = _W(self)
        self.banner_w = banner_w
        try:
            banner_w.setProperty("banner", "true")
            banner_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        try:
            banner_w.setFixedHeight(110)
        except Exception:
            pass
        apply_banner_style(banner_w)
        # Banner 背景交由 Fluent 主题控制
        from PySide6.QtWidgets import QHBoxLayout as _H, QLabel as _L, QVBoxLayout as _V
        banner = _H(banner_w)
        banner.setContentsMargins(20, 20, 20, 20)
        banner.setSpacing(16)
        self._icon_lbl = _L("", banner_w)
        try:
            self._icon_lbl.setStyleSheet("background: transparent;")
            self._icon_lbl.setFixedSize(48, 48)
            self._icon_lbl.setAlignment(Qt.AlignCenter)
            self._icon_lbl._fluent_icon = FluentIcon.SETTING
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

        # --- 性能模式 / 运行日志 ---
        # 性能优化：延迟到首次 showEvent 时创建（6个 SettingCard+SwitchButton ~250ms）
        self._did_deferred_build = False

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

        self.card_feedback_group = PushSettingCard(
            "复制群号",
            FluentIcon.CHAT if hasattr(FluentIcon, "CHAT") else FluentIcon.PEOPLE,
            "MemeKit官方反馈群",
            "群号: 1036959002",
            self.group_about
        )
        self.card_feedback_group.clicked.connect(self._copy_feedback_group)

        self.card_about = PushSettingCard(
            "查看",
            FluentIcon.INFO,
            "更新日志",
            f"当前版本: {VERSION}",
            self.group_about
        )
        self.card_about.clicked.connect(self._show_about)

        self.card_update = PushSettingCard(
            "前往",
            FluentIcon.SYNC if hasattr(FluentIcon, "SYNC") else FluentIcon.UPDATE,
            "检查更新",
            f"当前版本: {VERSION}，点击前往 GitHub Releases 页面查看最新版本",
            self.group_about
        )
        self.card_update.clicked.connect(self._open_releases_page)

        self.group_about.addSettingCard(self.card_author)
        self.group_about.addSettingCard(self.card_feedback_group)
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
            from qfluentwidgets import isDarkTheme, ThemeColor
            _dark = isDarkTheme()
            try:
                _ico = FluentIcon.SETTING.icon(ThemeColor.LIGHT_1 if _dark else ThemeColor.DARK_1)
            except Exception:
                _ico = FluentIcon.SETTING.icon()
            self._icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

    def showEvent(self, event):
        """显示时刷新图标，确保主题切换后图标正确更新。

        性能优化：首次显示时才创建性能模式组和运行日志组（~250ms 开销），
        避免启动阶段阻塞。插入到 group_about 之前保持视觉顺序。
        """
        super().showEvent(event)
        self._refresh_icon()
        if not getattr(self, '_did_deferred_build', False):
            self._did_deferred_build = True
            import time as _time
            import sys as _sys
            _t0 = _time.perf_counter()
            try:
                # 找到 group_about 在布局中的位置，将延迟创建的组插入到它之前
                target_idx = -1
                for i in range(self._content_layout.count()):
                    item = self._content_layout.itemAt(i)
                    if item and item.widget() is getattr(self, 'group_about', None):
                        target_idx = i
                        break
                if target_idx < 0:
                    target_idx = self._content_layout.count() - 1  # 插入到 stretch 前
                # 构建并插入性能模式组
                self._build_performance_group()
                self._content_layout.insertWidget(target_idx, self.group_performance)
                target_idx += 1
                # 构建并插入运行日志组
                self._build_logging_group()
                self._content_layout.insertWidget(target_idx, self.group_logging)
            except Exception:
                pass
            # 性能分析：延迟构建耗时
            try:
                _elapsed_ms = (_time.perf_counter() - _t0) * 1000
                if _sys.stderr:
                    _sys.stderr.write(f"[PERF] 设置-延迟构建组: {_elapsed_ms:.1f}ms\n")
            except Exception:
                pass

    def refresh_theme(self):
        """主题切换时刷新图标。"""
        self._refresh_icon()
        if hasattr(self, 'banner_w'):
            refresh_banner_style(self.banner_w)

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
        try:
            from app.services import log_service
            log_service.log_ui_action("设置-工具检测")
        except Exception:
            pass
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

    # ============================================================
    # 性能模式组
    # ============================================================
    def _build_performance_group(self):
        self.group_performance = SettingCardGroup("性能模式", self)

        # 总开关
        self.card_perf = SettingCard(
            FluentIcon.SPEED_HIGH,
            "性能模式（总开关）",
            "一键关闭所有材质/动画/特效并降低轮询频率（重启后完全生效）",
            self.group_performance
        )
        self.sw_perf = SwitchButton()
        self.sw_perf.setChecked(is_performance_mode())
        self.sw_perf.checkedChanged.connect(self._on_performance_toggled)
        self.card_perf.hBoxLayout.addWidget(self.sw_perf)
        self.card_perf.hBoxLayout.addSpacing(16)
        self.group_performance.addSettingCard(self.card_perf)

        # 关闭弹窗模糊背景
        self.card_blur = SettingCard(
            FluentIcon.BLUR if hasattr(FluentIcon, "BLUR") else FluentIcon.PALETTE,
            "关闭弹窗模糊背景",
            "弹窗不再显示模糊背景效果，提升低配电脑流畅度",
            self.group_performance
        )
        self.sw_blur = SwitchButton()
        self.sw_blur.setChecked(is_blur_disabled())
        self.sw_blur.checkedChanged.connect(
            lambda v: self._on_sub_perf_toggled("blur_disabled", v, "弹窗模糊")
        )
        self.card_blur.hBoxLayout.addWidget(self.sw_blur)
        self.card_blur.hBoxLayout.addSpacing(16)
        self.group_performance.addSettingCard(self.card_blur)

        # 关闭 TAB 切换动画
        self.card_tab_ani = SettingCard(
            FluentIcon.SYNC,
            "关闭 TAB 切换动画",
            "切换功能页时不再淡入淡出，提升低配电脑响应速度（重启生效）",
            self.group_performance
        )
        self.sw_tab_ani = SwitchButton()
        self.sw_tab_ani.setChecked(is_tab_animation_disabled())
        self.sw_tab_ani.checkedChanged.connect(
            lambda v: self._on_sub_perf_toggled("disable_tab_animation", v, "TAB 切换动画")
        )
        self.card_tab_ani.hBoxLayout.addWidget(self.sw_tab_ani)
        self.card_tab_ani.hBoxLayout.addSpacing(16)
        self.group_performance.addSettingCard(self.card_tab_ani)

        # 关闭弹窗动画
        self.card_dlg_ani = SettingCard(
            FluentIcon.SEND,
            "关闭弹窗动画",
            "启动闪屏、免责声明等弹窗不再淡入淡出（重启生效）",
            self.group_performance
        )
        self.sw_dlg_ani = SwitchButton()
        self.sw_dlg_ani.setChecked(is_dialog_animation_disabled())
        self.sw_dlg_ani.checkedChanged.connect(
            lambda v: self._on_sub_perf_toggled("disable_dialog_animation", v, "弹窗动画")
        )
        self.card_dlg_ani.hBoxLayout.addWidget(self.sw_dlg_ani)
        self.card_dlg_ani.hBoxLayout.addSpacing(16)
        self.group_performance.addSettingCard(self.card_dlg_ani)

        # 低频后台轮询
        self.card_poll = SettingCard(
            FluentIcon.SPEED_OFF if hasattr(FluentIcon, "SPEED_OFF") else FluentIcon.SPEED_HIGH,
            "低频后台轮询",
            "把 root/投屏/软件管理/Flash菜单的后台轮询从 2-3 秒延长到 5-8 秒，降低 CPU 占用",
            self.group_performance
        )
        self.sw_poll = SwitchButton()
        self.sw_poll.setChecked(is_low_polling_enabled())
        self.sw_poll.checkedChanged.connect(
            lambda v: self._on_sub_perf_toggled("low_polling", v, "低频轮询")
        )
        self.card_poll.hBoxLayout.addWidget(self.sw_poll)
        self.card_poll.hBoxLayout.addSpacing(16)
        self.group_performance.addSettingCard(self.card_poll)

        self._content_layout.addWidget(self.group_performance)

    def _on_sub_perf_toggled(self, key: str, enabled: bool, name: str):
        set_hidden_setting(key, enabled)
        action = "已关闭" if enabled else "已开启"
        InfoBar.info(
            f"{name}{action}",
            f"{'关闭' if enabled else '开启'}{name}，重启软件后完全生效。",
            parent=self, position=InfoBarPosition.TOP, duration=2500, isClosable=True,
        )
        try:
            log_service.log_ui_action(f"切换{name}", action)
        except Exception:
            pass

    def _on_performance_toggled(self, enabled: bool):
        set_hidden_setting("performance_mode", enabled)
        # 同步更新子开关显示状态（屏蔽信号避免重复提示）
        sub_states = {
            'sw_blur': is_blur_disabled(),
            'sw_tab_ani': is_tab_animation_disabled(),
            'sw_dlg_ani': is_dialog_animation_disabled(),
            'sw_poll': is_low_polling_enabled(),
        }
        for attr, state in sub_states.items():
            sw = getattr(self, attr, None)
            if sw is None:
                continue
            sw.blockSignals(True)
            try:
                sw.setChecked(state)
            finally:
                sw.blockSignals(False)
        if enabled:
            InfoBar.info(
                "性能模式已开启",
                "已关闭 Mica/Acrylic 材质、弹窗模糊、TAB 动画、弹窗动画，并降低后台轮询频率。重启后完全生效。",
                parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True,
            )
        else:
            InfoBar.success(
                "性能模式已关闭",
                "重启软件后将恢复所有材质和特效（不影响下方细粒度开关）。",
                parent=self, position=InfoBarPosition.TOP, duration=2500, isClosable=True,
            )
        try:
            log_service.log_ui_action("切换性能模式", "开启" if enabled else "关闭")
        except Exception:
            pass

    # ============================================================
    # 运行日志组
    # ============================================================
    def _build_logging_group(self):
        self.group_logging = SettingCardGroup("运行日志", self)

        # 保存日志
        self.card_export = PushSettingCard(
            "保存日志",
            FluentIcon.SAVE if hasattr(FluentIcon, "SAVE") else FluentIcon.FOLDER,
            "一键保存运行日志",
            "将当前记录的运行日志导出为文本文件，方便反馈问题",
            self.group_logging
        )
        self.card_export.clicked.connect(self._export_logs)
        self.group_logging.addSettingCard(self.card_export)

        self._content_layout.addWidget(self.group_logging)

    def _export_logs(self):
        try:
            log_service.log_ui_action("设置-保存运行日志")
        except Exception:
            pass
        if not log_service.is_logging_enabled():
            InfoBar.warning("提示", "日志记录未启用，没有日志可导出。", parent=self,
                            position=InfoBarPosition.TOP, isClosable=True)
            return
        logs = log_service.get_memory_logs()
        if not logs:
            InfoBar.warning("提示", "当前没有日志记录。", parent=self,
                            position=InfoBarPosition.TOP, isClosable=True)
            return
        import time as _time
        default_name = f"MemeKit_log_{_time.strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存运行日志", default_name, "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not file_path:
            return
        ok, msg = log_service.export_logs(file_path)
        if ok:
            InfoBar.success("成功", msg, parent=self,
                            position=InfoBarPosition.TOP, isClosable=True)
            try:
                log_service.log_file_event("保存", file_path)
            except Exception:
                pass
        else:
            InfoBar.error("失败", msg, parent=self,
                          position=InfoBarPosition.TOP, isClosable=True)

    def _show_about(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("设置-关于")
        except Exception:
            pass
        show_about_with_blur(self.window())

    def _show_about_author(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("设置-关于作者")
        except Exception:
            pass
        show_about_author(self.window())

    def _copy_feedback_group(self):
        """复制 MemeKit 官方反馈群群号到剪贴板。"""
        try:
            log_service.log_ui_action("设置-复制反馈群号")
        except Exception:
            pass
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("1036959002")
        InfoBar.success(
            "已复制",
            "群号 1036959002 已复制到剪贴板",
            parent=self,
            position=InfoBarPosition.TOP,
            isClosable=True,
        )

    def _open_releases_page(self):
        """点击检查更新：直接在系统默认浏览器打开 GitHub Releases 页面。"""
        from app.services.update_checker import open_releases_page
        open_releases_page()
        InfoBar.info(
            "正在打开",
            "已为你打开 GitHub Releases 页面，请在浏览器中查看最新版本。",
            parent=self, position=InfoBarPosition.TOP, duration=2500, isClosable=True,
        )
        try:
            from app.services import log_service
            log_service.log_ui_action("检查更新", "打开 GitHub Releases 页面")
        except Exception:
            pass

    def cleanup(self):
        pass