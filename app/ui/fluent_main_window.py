from PySide6.QtWidgets import QApplication, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import QTimer, QSettings, Qt, Signal, QEvent
from PySide6.QtGui import QPainter, QColor, QPixmap
import time
import traceback
import importlib
import webbrowser
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon, MessageBox

from app.services.update_checker import UpdateCheckerWorker
from app.services import log_service
from app.version import VERSION


# ---------------------------------------------------------------------------
# 懒加载：Tab 模块在首次使用时才导入，大幅加速启动
# ---------------------------------------------------------------------------
_TAB_REGISTRY = {
    "info_tab":           ("app.widgets.device_info_tab",     "DeviceInfoTab"),
    "root_tab":           ("app.widgets.root_tab",             "RootTab"),
    "quick_commands_tab": ("app.widgets.quick_commands_tab",   "QuickCommandsTab"),
    "font_backup_tab":    ("app.widgets.font_backup_tab",      "FontBackupTab"),
    "font_restore_tab":   ("app.widgets.font_restore_tab",     "FontRestoreTab"),
    "flash_center_tab":   ("app.widgets.flash_center_tab",     "FlashCenterTab"),
    "scrcpy_tab":         ("app.widgets.scrcpy_tab",           "ScrcpyTab"),
    "software_tab":       ("app.widgets.software_manager_tab", "SoftwareManagerTab"),
    "file_tab":           ("app.widgets.file_manager_tab",     "FileManagerTab"),
    "settings_tab":       ("app.widgets.settings_tab",         "SettingsTab"),
}

_tab_class_cache = {}

def _get_tab_class(attr_name: str):
    """懒加载并缓存 Tab 类，避免启动时导入所有模块。"""
    if attr_name in _tab_class_cache:
        return _tab_class_cache[attr_name]
    info = _TAB_REGISTRY.get(attr_name)
    if info is None:
        raise ImportError(f"Unknown tab: {attr_name}")
    module_path, class_name = info
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    _tab_class_cache[attr_name] = cls
    return cls


class FluentMainWindow(FluentWindow):
    initialized = Signal()

    # route_key → 中文标题（静态查找表，避免每次 _update_title 重建列表）
    _ROUTE_TITLE_MAP = {
        "device_info": "仪表盘", "root": "一键Root",
        "quick_commands": "快捷指令", "font_backup": "备份字库",
        "font_restore": "还原字库", "flash_center": "Flash菜单",
        "scrcpy": "投屏中心", "software_manager": "软件管理",
        "file_manager": "文件管理", "settings": "设置",
    }

    def __init__(self, parent: QWidget | None = None, *, eager_load: bool = True, defer_init: bool = False):
        super().__init__(parent)
        self._startup_upd_worker = None
        self._init_queue = []
        self._init_queue_i = 0
        self._closing = False
        self._eager_load = bool(eager_load)
        self._defer_init = bool(defer_init)
        self._flash_center_confirmed = False
        self._prev_route_key = ""
        self._settings_click_count = 0
        self._settings_click_timer = 0.0
        # 修复：确保主窗口始终为 Qt.Window 类型，防止任务栏图标消失
        try:
            from PySide6.QtCore import Qt as _Qt
            self.setWindowFlags(_Qt.WindowType.Window)
        except Exception:
            pass
        # 初始化运行日志（根据隐藏设置决定是否启用）
        try:
            from app.components.hidden_settings import is_logging_enabled
            log_service.init_logging(is_logging_enabled())
        except Exception:
            log_service.init_logging(False)
        try:
            self.setWindowTitle("MemeKit")
        except Exception:
            self.setWindowTitle("MemeKit")
        # 监听导航切换事件，以便更新标题栏和Flash菜单确认
        try:
            self.stackedWidget.currentChanged.connect(self._on_nav_changed)
        except Exception:
            pass
        # 按系统版本只启用一个材质效果，避免 GPU 合成层叠加开销
        # Win11 用 Mica，Win10 回退 Acrylic
        # 性能模式下关闭所有材质效果，降低 GPU 占用
        _perf_mode = False
        try:
            from app.components.hidden_settings import is_performance_mode
            _perf_mode = is_performance_mode()
        except Exception:
            pass
        _is_win11 = False
        try:
            import sys
            _is_win11 = sys.getwindowsversion().build >= 22000
        except Exception:
            pass
        # 性能模式下必须显式关闭 Mica/Acrylic 与 WA_TranslucentBackground。
        # 关键：FluentWidget 基类 __init__ 已无条件调用 setMicaEffectEnabled(True)，
        # 仅设置 Qt widget 属性无法移除 DWM 层面已启用的 Mica 材质，必须再次调用
        # setMicaEffectEnabled(False) 才能真正关闭。
        # 优化：全窗口只保留一个材质效果（Win11=Mica, Win10=Acrylic），
        # 不再在导航栏/标题栏上重复开启，避免 GPU 合成层叠加导致切换/滚动卡顿。
        if _perf_mode:
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            except Exception:
                pass
            # 显式移除基类已启用的 DWM 材质效果
            try:
                if _is_win11:
                    self.setMicaEffectEnabled(False)
                else:
                    self.setAcrylicEffectEnabled(False)
            except Exception:
                pass
        else:
            # 非性能模式：Win11 用 Mica，Win10 用 Acrylic
            if _is_win11:
                # 必须显式调用：setWindowFlags 可能已重建 HWND，
                # 基类 __init__ 中设置的 Mica 效果可能已丢失
                try:
                    self.setMicaEffectEnabled(True)
                except Exception:
                    pass
            else:
                # Win10: 基类启用的 Mica 在 Win10 无效，先关闭再启用 Acrylic
                try:
                    self.setMicaEffectEnabled(False)
                except Exception:
                    pass
                try:
                    self.setAcrylicEffectEnabled(True)
                except Exception:
                    pass
        try:
            self.setResizeEnabled(True)
        except Exception:
            pass
        try:
            self.setMinimumSize(1422, 822)
        except Exception:
            pass
        try:
            self.resize(877, 1422)
        except Exception:
            pass
        # Tabs init strategy:
        # - defer_init=True: caller will call init_pages() after connecting signals
        # - eager_load=True: build all tabs synchronously (splash will cover startup)
        # - eager_load=False: build incrementally to keep the event loop responsive
        try:
            if not self._defer_init:
                if self._eager_load:
                    self._init_pages_sync()
                else:
                    QTimer.singleShot(0, self._init_pages_async)
        except Exception:
            traceback.print_exc()
            try:
                self.initialized.emit()
            except Exception:
                pass
        # 导航栏/标题栏不再叠加额外 DWM 材质层：主窗口已有单一 Mica/Acrylic，
        # paintEvent 绘制的毛玻璃 pixmap 通过透明 QSS 透出，无需重复开启 DWM 合成
        # 仅为标题栏设置透明背景（轻量属性，不涉及 DWM 调用）
        try:
            self.setTitleBarTransparent(not _perf_mode)
        except Exception:
            pass

        # 应用主题背景：性能模式用纯色/QSS 背景，非性能模式绘制毛玻璃光斑 pixmap
        # （配合 Mica/Acrylic 材质）
        self._apply_theme_background()

        # 监听主题变化，更新侧边栏/标题栏背景 + 刷新全部主题依赖样式
        try:
            from qfluentwidgets import qconfig

            def _update_theme_bg(*_args):
                try:
                    from app.components.glass_style import clear_qss_cache
                    clear_qss_cache()
                except Exception:
                    pass
                self._apply_theme_background()
                self._refresh_all_tabs_theme()

            qconfig.themeChanged.connect(_update_theme_bg)
        except Exception:
            pass

        # TAB 切换动画策略：
        # - 性能模式：禁用（减少 GPU 合成层和重绘开销）
        # - 用户在隐藏设置中手动关闭：禁用
        # - 其他情况（默认）：启用（已通过其他优化将 Tab 切换耗时降到 ~3ms，
        #   动画期间双页渲染的额外开销可接受）
        try:
            from app.components.hidden_settings import is_tab_animation_disabled, is_performance_mode
            _perf = is_performance_mode()
            _user_disabled = is_tab_animation_disabled()
            if _perf or _user_disabled:
                self.stackedWidget.setAnimationEnabled(False)
        except Exception:
            try:
                self.stackedWidget.setAnimationEnabled(False)
            except Exception:
                pass
        # 标题栏 DWM 材质已移除：主窗口单一材质层 + paintEvent 毛玻璃 pixmap
        # 已足够提供视觉效果，避免标题栏重复叠加 Acrylic+Mica 造成 GPU 合成开销

        # 延迟到窗口显示后执行更新检查（含强制更新逻辑）
        try:
            QTimer.singleShot(200, self._check_update_on_launch)
        except Exception:
            pass

        # 兜底：程序异常退出/未触发 closeEvent 时也要停掉启动更新线程
        try:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self._on_about_to_quit)
        except Exception:
            pass

    def init_pages(self):
        """公开方法：同步初始化所有 TAB 页面。

        调用方应先连接 initialized 信号，再调用此方法。
        所有 TAB 在启动画面期间一次性加载完成，用户不会看到图标逐个出现。
        """
        self._init_pages_sync()

    def _apply_theme_background(self):
        """设置侧边栏/标题栏/功能区域背景（性能优化版）。

        非性能模式：nav/titlebar 使用透明 QSS + paintEvent 绘制单一毛玻璃 pixmap，
        替代旧版的 eventFilter 逐控件拦截方案（减少每次重绘的函数调用开销）。
        性能模式：纯色 QSS 背景，无 pixmap 绘制。
        """
        try:
            from qfluentwidgets import isDarkTheme as _isDark
            from app.components.glass_style import (
                get_glass_pixmap, apply_card_glass_alpha,
                apply_combo_glass_alpha, _is_performance_mode, glass_widgets_qss
            )

            _dark = _isDark()
            _perf = _is_performance_mode()
            _nav_bg = "#1E1E1E" if _dark else "#F3F3F3"
            _win_bg = "#1E1E1E" if _dark else "#FFFFFF"
            _content_bg = "#1A1A1E" if _dark else "#F0F5FF"

            self._theme_nav_bg = _nav_bg
            self._theme_win_bg = _win_bg
            self._theme_content_bg = _content_bg
            self._theme_content_is_glass = not _perf

            # CardWidget / ComboBox 毛玻璃透明度（monkey-patch）
            apply_card_glass_alpha()
            apply_combo_glass_alpha()

            # ★ 创建毛玻璃背景 pixmap（缓存，paintEvent 使用）
            if not _perf:
                try:
                    w = max(self.width(), 800)
                    h = max(self.height(), 600)
                    self._glass_pixmap = get_glass_pixmap(_dark, w, h)
                except Exception:
                    self._glass_pixmap = None
            else:
                self._glass_pixmap = None

            # 1. 侧边栏：透明 QSS，让 paintEvent 的毛玻璃 pixmap 透出
            try:
                nav = self.navigationInterface
                nav.setStyleSheet(
                    "NavigationInterface, NavigationPanel { background: transparent; border: none; }")
                nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            except Exception:
                pass

            # 2. 标题栏：透明 QSS，让 paintEvent 的毛玻璃 pixmap 透出
            try:
                tb = getattr(self, 'titleBar', None)
                if tb is not None:
                    tb.setStyleSheet(
                        "FluentTitleBar { background: transparent; border: none; }")
                    tb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            except Exception:
                pass

            # 3. 功能区域 QSS
            try:
                sw = self.stackedWidget
                widgets_qss = glass_widgets_qss()
                if _perf:
                    sw.setStyleSheet(
                        f"QStackedWidget {{ background-color: {_content_bg}; }}" + widgets_qss)
                    sw.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                else:
                    sw.setStyleSheet(
                        "QStackedWidget { background: transparent; }" + widgets_qss)
                    sw.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            except Exception:
                pass

            # 4. 主窗口 QPalette 兜底
            try:
                self.setAutoFillBackground(_perf)
                from PySide6.QtGui import QPalette
                pal = self.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor(_win_bg))
                self.setPalette(pal)
            except Exception:
                pass

            # 触发重绘
            try:
                self.navigationInterface.update()
                tb = getattr(self, 'titleBar', None)
                if tb is not None:
                    tb.update()
                self.stackedWidget.update()
                self.update()
            except Exception:
                pass

        except Exception:
            pass

    def eventFilter(self, obj, event):
        """事件过滤器（毛玻璃背景已改为 paintEvent 绘制，此处不再拦截）。"""
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        """窗口大小变化处理。"""
        try:
            super().resizeEvent(event)
        except Exception:
            pass

    def paintEvent(self, event):
        """绘制毛玻璃背景 pixmap（非性能模式）。

        在子控件绘制之前画出整张光斑背景，nav/titlebar/stackedWidget 的
        透明 QSS 让 pixmap 透出，替代旧版 eventFilter 逐控件绘制方案。
        """
        try:
            _pm = getattr(self, '_glass_pixmap', None)
            if _pm is not None and not _pm.isNull():
                painter = QPainter(self)
                painter.drawPixmap(0, 0, self.width(), self.height(), _pm)
                painter.end()
        except Exception:
            pass
        try:
            super().paintEvent(event)
        except Exception:
            pass

    def _refresh_all_tabs_theme(self):
        """主题切换时刷新所有 Tab 的 banner 图标和主题依赖样式。"""
        try:
            from qfluentwidgets import isDarkTheme, ThemeColor
            from PySide6.QtWidgets import QLabel
            _dark = isDarkTheme()

            # 刷新所有带 _fluent_icon 属性的 QLabel
            for label in self.stackedWidget.findChildren(QLabel):
                try:
                    icon = getattr(label, '_fluent_icon', None)
                    if icon is not None:
                        sz = max(label.width(), label.height(), 48)
                        try:
                            _ico = icon.icon(ThemeColor.LIGHT_1 if _dark else ThemeColor.DARK_1)
                        except Exception:
                            _ico = icon.icon()
                        label.setPixmap(_ico.pixmap(sz, sz))
                except Exception:
                    pass

            # 调用各 Tab 的 refresh_theme() 方法
            for i in range(self.stackedWidget.count()):
                try:
                    w = self.stackedWidget.widget(i)
                    if hasattr(w, 'refresh_theme'):
                        w.refresh_theme()
                except Exception:
                    pass
        except Exception:
            pass

    def _on_about_to_quit(self):
        try:
            t = getattr(self, '_startup_upd_worker', None)
            if t is not None and t.isRunning():
                try:
                    t.quit()
                except Exception:
                    pass
                try:
                    t.wait(100)
                except Exception:
                    pass
                try:
                    if t.isRunning():
                        t.terminate()
                except Exception:
                    pass
        except Exception:
            pass
        # 清理 eventFilter：移除所有已安装的事件过滤器，避免退出时
        # qfluentwidgets navigation_panel 的 eventFilter 报 KeyboardInterrupt
        try:
            for obj in [self.navigationInterface, getattr(self, 'titleBar', None), self.stackedWidget]:
                if obj is not None:
                    try:
                        obj.removeEventFilter(self)
                    except Exception:
                        pass
        except Exception:
            pass

    @staticmethod
    def _filter_hidden_tabs(queue):
        """根据隐藏设置过滤掉被关闭的 TAB。仪表盘和设置始终保留。"""
        try:
            from app.components.hidden_settings import is_tab_hidden
            return [item for item in queue if not is_tab_hidden(item[0])]
        except Exception:
            return queue

    def _install_settings_click_counter(self):
        """为设置导航项连接 clicked 信号，连续点击20次触发隐藏设置。"""
        try:
            from app.components.hidden_settings import show_hidden_settings
            nav = self.navigationInterface
            # qfluentwidgets NavigationPanel: items 字典 + widget(routeKey) 方法
            settings_widget = None
            try:
                if hasattr(nav, 'widget') and callable(nav.widget):
                    settings_widget = nav.widget("settings")
                elif hasattr(nav, 'items') and "settings" in nav.items:
                    settings_widget = nav.items["settings"].widget
            except Exception:
                pass
            if settings_widget is None:
                return
            self._settings_nav_widget = settings_widget
            self._show_hidden_settings_fn = show_hidden_settings
            # NavigationWidget 有 clicked 信号（参数 triggerByUser: bool）
            if hasattr(settings_widget, 'clicked'):
                settings_widget.clicked.connect(self._on_settings_nav_clicked)
        except Exception:
            traceback.print_exc()

    def _on_settings_nav_clicked(self, *args):
        """设置导航项被点击时累计计数，达到触发次数后弹出隐藏设置（带密码验证）。"""
        try:
            from app.components.hidden_settings import HIDDEN_SETTINGS_TRIGGER_COUNT
            trigger_count = HIDDEN_SETTINGS_TRIGGER_COUNT
        except Exception:
            trigger_count = 10
        try:
            now = time.time()
            if now - self._settings_click_timer > 5.0:
                self._settings_click_count = 0
            self._settings_click_timer = now
            self._settings_click_count += 1
            # 过半时给提示，避免用户以为没反应
            half = trigger_count // 2
            if half > 0 and self._settings_click_count == half:
                try:
                    from qfluentwidgets import InfoBar, InfoBarPosition
                    InfoBar.info("提示", f"再点击 {trigger_count - self._settings_click_count} 次进入隐藏设置",
                                 parent=self, position=InfoBarPosition.TOP, duration=1500, isClosable=True)
                except Exception:
                    pass
            if self._settings_click_count >= trigger_count:
                self._settings_click_count = 0
                fn = getattr(self, '_show_hidden_settings_fn', None)
                if fn:
                    QTimer.singleShot(0, lambda: fn(self))
        except Exception:
            pass

    def _connect_device_selected_signal(self):
        """将仪表盘的设备选择信号广播给所有支持 set_current_serial 的 TAB。

        实现：用户在仪表盘切换设备时，一键Root、文件管理、软件管理等 TAB
        会接收到选中的 serial，并刷新该设备的数据，避免始终操作第一台设备。
        """
        try:
            info_tab = getattr(self, 'info_tab', None)
            if info_tab is None:
                return
            # 目标 tab 列表：凡是实现了 set_current_serial 方法的 tab 都连接
            target_tabs = [
                'root_tab',
                'file_tab',
                'software_tab',
                'scrcpy_tab',
                'flash_center_tab',
                'quick_commands_tab',
                'font_backup_tab',
                'font_restore_tab',
            ]
            for attr in target_tabs:
                tab = getattr(self, attr, None)
                if tab is None:
                    continue
                slot = getattr(tab, 'set_current_serial', None)
                if slot is None or not callable(slot):
                    continue
                try:
                    info_tab.device_selected.connect(slot)
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()

    def _init_pages_async(self):
        try:
            self._init_queue = [
                ("info_tab",           "device_info",      FluentIcon.INFO,                                                  "仪表盘",   NavigationItemPosition.TOP),
                ("root_tab",           "root",             getattr(FluentIcon, "IOT", FluentIcon.INFO),                      "一键Root", NavigationItemPosition.TOP),
                ("quick_commands_tab", "quick_commands",   getattr(FluentIcon, "COMMAND_PROMPT", FluentIcon.FOLDER),         "快捷指令", NavigationItemPosition.TOP),
                ("font_backup_tab",    "font_backup",      getattr(FluentIcon, "SAVE", FluentIcon.FOLDER),                  "备份字库", NavigationItemPosition.TOP),
                ("font_restore_tab",   "font_restore",     FluentIcon.PLAY,                                                  "还原字库", NavigationItemPosition.TOP),
                ("flash_center_tab",   "flash_center",     getattr(FluentIcon, "SPEED_HIGH", FluentIcon.SPEED_HIGH),         "Flash菜单", NavigationItemPosition.TOP),
                ("scrcpy_tab",         "scrcpy",           getattr(FluentIcon, "VIDEO", FluentIcon.PLAY),                    "投屏中心", NavigationItemPosition.TOP),
                ("software_tab",       "software_manager", getattr(FluentIcon, "APPLICATION", FluentIcon.BASKETBALL),        "软件管理", NavigationItemPosition.TOP),
                ("file_tab",           "file_manager",     FluentIcon.FOLDER,                                                "文件管理", NavigationItemPosition.TOP),
                ("settings_tab",       "settings",         FluentIcon.SETTING,                                               "设置",     NavigationItemPosition.BOTTOM),
            ]
            self._init_queue = self._filter_hidden_tabs(self._init_queue)
            self._init_queue_i = 0
            self._init_pages_step()
        except Exception:
            traceback.print_exc()
            try:
                self.initialized.emit()
            except Exception:
                pass

    def _init_pages_sync(self):
        try:
            queue = [
                ("info_tab",           "device_info",      FluentIcon.INFO,                                                  "仪表盘",   NavigationItemPosition.TOP),
                ("root_tab",           "root",             getattr(FluentIcon, "IOT", FluentIcon.INFO),                      "一键Root", NavigationItemPosition.TOP),
                ("quick_commands_tab", "quick_commands",   getattr(FluentIcon, "COMMAND_PROMPT", FluentIcon.FOLDER),         "快捷指令", NavigationItemPosition.TOP),
                ("font_backup_tab",    "font_backup",      getattr(FluentIcon, "SAVE", FluentIcon.FOLDER),                  "备份字库", NavigationItemPosition.TOP),
                ("font_restore_tab",   "font_restore",     FluentIcon.PLAY,                                                  "还原字库", NavigationItemPosition.TOP),
                ("flash_center_tab",   "flash_center",     getattr(FluentIcon, "SPEED_HIGH", FluentIcon.SPEED_HIGH),         "Flash菜单", NavigationItemPosition.TOP),
                ("scrcpy_tab",         "scrcpy",           getattr(FluentIcon, "VIDEO", FluentIcon.PLAY),                    "投屏中心", NavigationItemPosition.TOP),
                ("software_tab",       "software_manager", getattr(FluentIcon, "APPLICATION", FluentIcon.BASKETBALL),        "软件管理", NavigationItemPosition.TOP),
                ("file_tab",           "file_manager",     FluentIcon.FOLDER,                                                "文件管理", NavigationItemPosition.TOP),
                ("settings_tab",       "settings",         FluentIcon.SETTING,                                               "设置",     NavigationItemPosition.BOTTOM),
            ]

            queue = self._filter_hidden_tabs(queue)

            for attr, obj_name, icon, title, pos in queue:
                if getattr(self, '_closing', False):
                    break
                _t_init = time.perf_counter()
                try:
                    cls = _get_tab_class(attr)
                    w = cls()
                    try:
                        w.setObjectName(obj_name)
                    except Exception:
                        pass
                    try:
                        setattr(self, attr, w)
                    except Exception:
                        pass
                    try:
                        if pos == NavigationItemPosition.BOTTOM:
                            self.addSubInterface(w, icon, title, position=NavigationItemPosition.BOTTOM)
                        else:
                            self.addSubInterface(w, icon, title)
                    except Exception:
                        traceback.print_exc()
                except Exception:
                    traceback.print_exc()
                # 性能分析：各 Tab 初始化耗时
                _elapsed_ms = (time.perf_counter() - _t_init) * 1000
                try:
                    import sys as _sys
                    if _sys.stderr:
                        _sys.stderr.write(f"[PERF] Tab初始化 {title}: {_elapsed_ms:.1f}ms\n")
                except Exception:
                    pass
                # 每加载一个 TAB 后处理事件，保持启动画面响应
                QApplication.processEvents()

            try:
                if getattr(self, 'info_tab', None) is not None:
                    self.navigationInterface.setCurrentItem(self.info_tab)
                self._update_title()
            except Exception:
                traceback.print_exc()

            # 连接仪表盘的设备选择信号到各 TAB，实现多设备协同
            self._connect_device_selected_signal()
            # 安装设置项点击计数器（隐藏设置入口）
            self._install_settings_click_counter()
            # 启动时手动刷新所有 Tab 的主题样式
            # 修复"跟随系统"模式下 setTheme() 在主窗口创建前调用导致 themeChanged
            # 信号丢失、各 Tab 的 refresh_theme() 未被初始调用的问题
            try:
                self._refresh_all_tabs_theme()
            except Exception:
                pass
        finally:
            try:
                self.initialized.emit()
            except Exception:
                pass

    def _init_pages_step(self):
        try:
            try:
                if getattr(self, '_closing', False):
                    return
            except Exception:
                pass
            if self._init_queue_i >= len(self._init_queue):
                try:
                    if getattr(self, 'info_tab', None) is not None:
                        self.navigationInterface.setCurrentItem(self.info_tab)
                except Exception:
                    traceback.print_exc()
                # 连接仪表盘的设备选择信号到各 TAB，实现多设备协同
                self._connect_device_selected_signal()
                # 安装设置项点击计数器（隐藏设置入口）
                self._install_settings_click_counter()
                # 启动时手动刷新所有 Tab 的主题样式（同 _init_pages_sync）
                try:
                    self._refresh_all_tabs_theme()
                except Exception:
                    pass
                try:
                    self.initialized.emit()
                except Exception:
                    pass
                return

            attr, obj_name, icon, title, pos = self._init_queue[self._init_queue_i]
            self._init_queue_i += 1

            _t_init = time.perf_counter()
            w = None
            try:
                cls = _get_tab_class(attr)
                w = cls()
            except Exception:
                traceback.print_exc()

            if w is not None:
                try:
                    w.setObjectName(obj_name)
                except Exception:
                    pass
                try:
                    setattr(self, attr, w)
                except Exception:
                    pass

                try:
                    if pos == NavigationItemPosition.BOTTOM:
                        self.addSubInterface(w, icon, title, position=NavigationItemPosition.BOTTOM)
                    else:
                        self.addSubInterface(w, icon, title)
                except Exception:
                    traceback.print_exc()
            # 性能分析：各 Tab 初始化耗时
            _elapsed_ms = (time.perf_counter() - _t_init) * 1000
            try:
                import sys as _sys
                if _sys.stderr:
                    _sys.stderr.write(f"[PERF] Tab初始化 {title}: {_elapsed_ms:.1f}ms\n")
            except Exception:
                pass

            try:
                QTimer.singleShot(0, self._init_pages_step)
            except Exception:
                self._init_pages_step()
        except Exception:
            traceback.print_exc()
            try:
                QTimer.singleShot(0, self._init_pages_step)
            except Exception:
                pass

    def _on_nav_changed(self, index):
        """导航切换回调：更新标题 + Flash菜单拦截确认。"""
        _t0 = time.perf_counter()
        self._update_title()
        try:
            current_widget = self.stackedWidget.currentWidget()
            if current_widget is None:
                return
            route_key = current_widget.objectName()
            # 记录 Tab 切换
            try:
                from app.services import log_service
                log_service.log_ui_tab_switch(self._ROUTE_TITLE_MAP.get(route_key, route_key))
            except Exception:
                pass
            # 性能分析：Tab 切换耗时
            _elapsed_ms = (time.perf_counter() - _t0) * 1000
            try:
                import sys as _sys
                if _sys.stderr:
                    _sys.stderr.write(f"[PERF] Tab切换→{route_key}: {_elapsed_ms:.1f}ms\n")
            except Exception:
                pass
            # 检查是否导航到Flash菜单
            if route_key == "flash_center" and not self._flash_center_confirmed:
                # 隐藏设置可关闭Flash菜单强制弹窗
                try:
                    from app.components.hidden_settings import is_flash_center_popup_disabled
                    popup_disabled = is_flash_center_popup_disabled()
                except Exception:
                    popup_disabled = False
                if popup_disabled:
                    self._flash_center_confirmed = True
                else:
                    # 拦截：显示确认弹窗
                    self._show_flash_center_warning()
            elif route_key != "flash_center":
                # 离开Flash菜单时重置状态
                self._flash_center_confirmed = False
            self._prev_route_key = route_key
        except Exception:
            traceback.print_exc()

    def _update_title(self, *args):
        try:
            current_widget = self.stackedWidget.currentWidget()
            if not current_widget:
                return

            route_key = current_widget.objectName()
            tab_title = self._ROUTE_TITLE_MAP.get(route_key)
            title = f"MemeKit - {tab_title}" if tab_title else "MemeKit"

            self.setWindowTitle(title)

            # 更新 QFluentWidgets 自定义标题栏的标题
            if hasattr(self, 'titleBar') and hasattr(self.titleBar, 'titleLabel'):
                self.titleBar.titleLabel.setText(title)
        except Exception:
            traceback.print_exc()

    def _show_flash_center_warning(self):
        """显示刷机风险确认弹窗（模糊背景，样式与检查更新弹窗保持一致）。"""
        from app.components.blur_popup import _make_blur_overlay, _play_system_sound
        from qfluentwidgets import isDarkTheme as _isDark

        blur = _make_blur_overlay(self)

        # 播放系统提示音
        _play_system_sound()

        dlg = QDialog(self.window())
        dlg.setWindowTitle("刷机风险确认")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        # 使用统一弹窗样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(dlg)
            dlg.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

        # 主题感知颜色
        dark = _isDark()
        title_color = "#E6E1E5" if dark else "#1D1B20"
        content_color = "#CCCCCC" if dark else "#333333"
        cancel_text_color = "#CCCCCC" if dark else "#333333"
        cancel_bg = "rgba(255, 255, 255, 0.08)" if dark else "#E0E0E0"
        cancel_hover = "rgba(255, 255, 255, 0.14)" if dark else "#D0D0D0"

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 标题
        title_lbl = QLabel("\u26a0\ufe0f \u8b66\u544a\uff1a\u5237\u673a\u5b58\u5728\u4e0d\u53ef\u9006\u98ce\u9669")
        title_lbl.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {title_color}; background: transparent;")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        # 内容
        content_text = (
            "1. \u5237\u673a\u5c06\u6e05\u7a7a\u672c\u673a\u6240\u6709\u6570\u636e\uff0c\u8bf7\u60a8\u81ea\u884c\u63d0\u524d\u5907\u4efd\uff1b\n\n"
            "2. \u64cd\u4f5c\u5931\u8bef\u4f1a\u5bfc\u81f4\u8bbe\u5907\u65e0\u6cd5\u5f00\u673a\u3001\u786c\u4ef6\u529f\u80fd\u6545\u969c\uff1b\n\n"
            "3. \u5237\u673a\u9020\u6210\u7684\u8bbe\u5907\u635f\u574f\u3001\u6570\u636e\u4e22\u5931\u3001\u4fdd\u4fee\u5931\u6548\u7b49\u95ee\u9898\uff0c\n"
            "    \u5168\u90e8\u7531\u60a8\u81ea\u884c\u627f\u62c5\u8d23\u4efb\u3002\n\n"
            "\u6211\u5df2\u77e5\u6653\u98ce\u9669\u5e76\u81ea\u613f\u5237\u673a"
        )
        content_lbl = QLabel(content_text)
        content_lbl.setWordWrap(True)
        content_lbl.setStyleSheet(f"font-size: 14px; color: {content_color}; padding: 8px 0; background: transparent;")
        layout.addWidget(content_lbl)

        # 按钮行
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(16)

        btn_cancel = QPushButton("\u53d6\u6d88")
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                color: {cancel_text_color};
                background-color: {cancel_bg};
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cancel_hover};
            }}
        """)
        btn_cancel.clicked.connect(dlg.reject)

        btn_confirm = QPushButton("\u786e\u8ba4")
        btn_confirm.setStyleSheet("""
            QPushButton {
                color: #FFFFFF;
                background-color: #D32F2F;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #B71C1C;
            }
        """)
        btn_confirm.clicked.connect(dlg.accept)

        btn_lay.addStretch(1)
        btn_lay.addWidget(btn_cancel)
        btn_lay.addWidget(btn_confirm)
        btn_lay.addStretch(1)
        layout.addLayout(btn_lay)

        result = dlg.exec()
        if blur is not None:
            blur.dispose()

        if result == QDialog.Accepted:
            self._flash_center_confirmed = True
            try:
                from app.services import log_service
                log_service.log_ui_action("刷机风险确认", "用户同意刷机风险")
            except Exception:
                pass
            # 重新导航到Flash菜单
            try:
                flash_tab = getattr(self, 'flash_center_tab', None)
                if flash_tab is not None:
                    self.switchTo(flash_tab)
            except Exception:
                pass
        else:
            self._flash_center_confirmed = False
            try:
                from app.services import log_service
                log_service.log_ui_action("刷机风险确认", "用户取消，返回仪表盘")
            except Exception:
                pass
            # 返回仪表盘
            try:
                info_tab = getattr(self, 'info_tab', None)
                if info_tab is not None:
                    self.switchTo(info_tab)
            except Exception:
                pass

    def _check_update_on_launch(self):
        """启动时检查更新，含强制更新逻辑。

        流程：
        1. 如果当前版本已达到/超过记忆的强制更新版本，清除标记
        2. 检查强制更新是否被隐藏设置关闭
        3. 检查记忆标记：上次检测到需要强制更新则直接弹窗（即使本次网络故障）
        4. 联网检查远程版本，若大于当前版本则标记并弹强制更新弹窗
        """
        try:
            from app.services.update_checker import (
                check_and_clear_force_update_flag,
                is_force_update_disabled,
                is_force_update_required,
                is_newer,
            )

            # 1. 当前版本已达标则清除标记
            check_and_clear_force_update_flag(VERSION)

            # 2. 强制更新被隐藏设置关闭时，走普通更新检查
            force_disabled = is_force_update_disabled()

            # 3. 记忆标记：上次检测到需要强制更新
            if not force_disabled and is_force_update_required():
                try:
                    from app.components.force_update_dialog import show_force_update_from_memory
                    show_force_update_from_memory(self)
                    return
                except Exception:
                    pass

            # 4. 联网检查
            settings = QSettings()
            url = settings.value("update/url", "") or ""
            if not url:
                return
            try:
                from app.services import log_service
                log_service.log_ui_action("启动检查更新(自定义URL)")
            except Exception:
                pass
            self._startup_upd_worker = UpdateCheckerWorker(url, VERSION, parent=self)
            self._startup_upd_worker.result_ready.connect(self._on_startup_update_finished)
            self._startup_upd_worker.result_ready.connect(self._startup_upd_worker.quit)
            self._startup_upd_worker.result_ready.connect(self._startup_upd_worker.deleteLater)
            self._startup_upd_worker.start()
        except Exception:
            pass

    def _on_startup_update_finished(self, info: dict, err: str):
        try:
            if err:
                try:
                    from app.services import log_service
                    log_service.log_error("启动检查更新", f"请求失败: {err}")
                except Exception:
                    pass
                return
            latest = str(info.get("version", "")).strip()
            download = info.get("url", "") or ""
            notes = info.get("notes", "") or ""
            cur = str(VERSION)
            if latest and is_newer(latest, cur):
                try:
                    from app.services import log_service
                    log_service.get_logger("UPDATE").info(
                        f"发现新版本 {latest}（当前 {cur}），弹出提醒"
                    )
                except Exception:
                    pass

                # 检查是否需要强制更新
                try:
                    from app.services.update_checker import is_force_update_disabled
                    if not is_force_update_disabled():
                        # 强制更新：标记 + 10秒倒计时弹窗
                        from app.components.force_update_dialog import show_force_update_dialog
                        show_force_update_dialog(self, latest, cur, download, notes)
                        return
                except Exception:
                    pass

                # 普通更新提醒
                msg = f"\u53d1\u73b0\u65b0\u7248\u672c\uff1a{latest}\n\u5f53\u524d\u7248\u672c\uff1a{cur}"
                if notes:
                    msg += f"\n\n\u66f4\u65b0\u5185\u5bb9\uff1a\n{notes}"
                box = MessageBox("\u53d1\u73b0\u66f4\u65b0", msg, self)
                try:
                    box.cancelButton.hide()
                    box.setClosableOnMaskClicked(False)
                    box.setWindowFlag(Qt.WindowCloseButtonHint, False)
                except Exception:
                    pass
                if box.exec():
                    if download:
                        try:
                            webbrowser.open(download)
                        except Exception:
                            pass
            else:
                try:
                    from app.services import log_service
                    log_service.get_logger("UPDATE").info(
                        f"检查更新完成：已是最新版本 {cur}（远程 {latest}）"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self._closing = True
        except Exception:
            pass
        try:
            from app.services import log_service
            log_service.log_ui_action("关闭主窗口", "用户关闭软件")
        except Exception:
            pass
        # 轻量清理：仅停止动画和线程，不阻塞退出
        for w in [
            getattr(self, 'file_tab', None),
            getattr(self, 'root_tab', None),
            getattr(self, 'software_tab', None),
            getattr(self, 'info_tab', None),
            getattr(self, 'settings_tab', None),
            getattr(self, 'flash_center_tab', None),
            getattr(self, 'scrcpy_tab', None),
            getattr(self, 'font_backup_tab', None),
            getattr(self, 'font_restore_tab', None),
            getattr(self, 'quick_commands_tab', None),
        ]:
            try:
                if w and hasattr(w, 'cleanup'):
                    w.cleanup()
            except Exception:
                pass
        # 清理启动更新线程（短超时，不阻塞）
        try:
            t = getattr(self, '_startup_upd_worker', None)
            if t is not None and t.isRunning():
                try:
                    t.quit()
                    t.wait(200)
                except Exception:
                    pass
        except Exception:
            pass
        return super().closeEvent(event)