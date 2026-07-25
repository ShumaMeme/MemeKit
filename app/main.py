
from PySide6.QtWidgets import QApplication, QStackedWidget
from PySide6.QtCore import QCoreApplication, QSettings, QTimer
from PySide6.QtGui import QIcon
import sys
import traceback
import faulthandler
import time

from app import get_project_root
from app.services import log_service
from app.version import VERSION

# 修复 qfluentwidgets StackedWidget 信号断开警告
# 原因：__onAniFinished 中 finished.disconnect() 在信号未连接时触发 RuntimeWarning
try:
    from qfluentwidgets.components.widgets.stacked_widget import StackedWidget

    def _patched_onAniFinished(self):
        try:
            self._ani.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        QStackedWidget.setCurrentIndex(self, self._nextIndex)
        self.aniFinished.emit()

    StackedWidget._StackedWidget__onAniFinished = _patched_onAniFinished
except Exception:
    pass

from app.ui.fluent_main_window import FluentMainWindow
from app.ui.startup_splash import StartupSplash
from app.ui.disclaimer import DisclaimerDialog
from app.ui.theme import apply_runtime_overlay
from qfluentwidgets import Theme, setTheme, setThemeColor


def main():
    # 在 PyInstaller --windowed 模式下，sys.stderr 可能为 None
    # 需要先检查再启用 faulthandler
    if sys.stderr is not None:
        try:
            faulthandler.enable()
        except Exception:
            pass

    # 初始化运行日志（默认启用，记录软件全生命周期）
    try:
        log_service.init_logging(True)
        log_service.install_excepthook()
        # 注册 atexit 兜底：即使 aboutToQuit 信号未触发，也确保退出日志落盘
        import atexit
        atexit.register(log_service.log_app_exit, "normal")
        atexit.register(log_service.flush_logs)
        log_service.log_app_start(VERSION)
    except Exception:
        pass

    app = QApplication(sys.argv)

    app.setApplicationName("MemeKit")
    QCoreApplication.setOrganizationName("MemeKit")
    QCoreApplication.setOrganizationDomain("memekit.local")
    root_dir = get_project_root()
    icon_path = root_dir / "android-chrome-512x512.png"  # 启动画面背景图
    app_icon_path = root_dir / "memekit.ico"  # 窗口/任务栏图标
    app_icon = None
    if app_icon_path.exists():
        app_icon = QIcon(str(app_icon_path))
        app.setWindowIcon(app_icon)

    # 使用 QFluentWidgets 主题（跟随系统：system/light/dark）
    settings = QSettings()
    mode = settings.value("theme/mode", "system")
    if mode == "light":
        setTheme(Theme.LIGHT)
    elif mode == "dark":
        setTheme(Theme.DARK)
    else:
        # 跟随系统：检测当前系统主题
        from app.ui.theme import detect_windows_theme
        sys_theme = detect_windows_theme()
        setTheme(Theme.DARK if sys_theme == "dark" else Theme.LIGHT)
        mode = sys_theme
    setThemeColor('#2A74DA')

    # 应用运行时覆盖，修正浅/深色模式下的字体可读性
    apply_runtime_overlay(app, fallback_dark=(mode == "dark"))

    # 监听主题变化，实时重新应用覆盖样式（确保浅/深色切换时侧边栏/标题栏背景正确）
    try:
        from qfluentwidgets import qconfig
        def _on_theme_changed_global(*_args):
            apply_runtime_overlay(app)
        qconfig.themeChanged.connect(_on_theme_changed_global)
    except Exception:
        pass

    # ADB 服务延迟启动：先显示启动画面，再在事件循环中启动 ADB，
    # 避免 ADB 响应慢时用户看到空白等待
    try:
        from app.services import adb_service
        QTimer.singleShot(200, adb_service.adb_start_server)
    except Exception:
        pass

    # ========== 步骤1：显示免责声明（首次启动时） ==========
    disclaimer_accepted = settings.value("disclaimer/accepted", False, type=bool)
    
    if not disclaimer_accepted:
        dlg = DisclaimerDialog(icon_path=str(icon_path) if icon_path.exists() else "")
        if app_icon is not None:
            try:
                dlg.setWindowIcon(app_icon)
            except Exception:
                pass
        dlg.center_on_screen()
        dlg.start_pulse()
        dlg.show()

        # 等待用户选择
        result = {"accepted": False}
        
        def on_accepted():
            result["accepted"] = True
        
        def on_rejected():
            result["accepted"] = False
        
        dlg.accepted.connect(on_accepted)
        dlg.rejected.connect(on_rejected)

        # 阻塞等待弹窗关闭
        while dlg.isVisible():
            app.processEvents()

        if not result["accepted"]:
            # 用户拒绝，退出软件
            try:
                log_service.log_ui_action("拒绝免责声明", "用户拒绝，软件退出")
            except Exception:
                pass
            try:
                log_service.log_app_exit("disclaimer_rejected")
            except Exception:
                pass
            sys.exit(0)

        # 用户同意，保存状态
        settings.setValue("disclaimer/accepted", True)
        settings.sync()
        try:
            log_service.log_ui_action("接受免责声明", "用户同意免责声明")
        except Exception:
            pass

    # ========== 步骤2：显示启动画面并后台加载主界面 ==========
    splash = None
    window = None

    # 隐藏设置：是否关闭启动闪屏
    _skip_splash = False
    try:
        from app.components.hidden_settings import is_splash_disabled
        _skip_splash = is_splash_disabled()
    except Exception:
        pass

    try:
        # 确保启动画面图片存在
        if not icon_path.exists():
            icon_path = root_dir / "android-chrome-512x512.png"

        splash = StartupSplash(icon_path=str(icon_path) if icon_path.exists() else "", light=True)
        if app_icon is not None:
            try:
                splash.setWindowIcon(app_icon)
            except Exception:
                pass

        splash.set_status("正在部署运行环境")
        splash.center_on_screen()
        splash.raise_()
        if _skip_splash:
            # 不显示闪屏，仅保留对象用于流程兼容
            splash_shown_at = time.time()
        else:
            splash.show()
            # 强制处理事件循环，确保启动画面立即渲染
            app.processEvents()
            # 记录启动画面显示时刻（用于计算 1.0 秒强制显示时长）
            splash_shown_at = time.time()

            QTimer.singleShot(100, lambda: splash.set_status("正在加载依赖") if splash else None)
            QTimer.singleShot(300, lambda: splash.set_status("正在加载UI界面") if splash else None)

        # 延迟初始化：先创建窗口和连接信号，再同步加载所有 TAB
        # 这样 initialized 信号在 connect 之后才发射，确保 _finish_startup 能被正确触发
        window = FluentMainWindow(defer_init=True)
        window.hide()
        
    except Exception:
        try:
            if splash is not None:
                splash.close()
        except Exception:
            pass
        traceback.print_exc()
        raise

    if app_icon is not None:
        try:
            window.setWindowIcon(app_icon)
        except Exception:
            pass

    # 设置主窗口大小和位置
    try:
        scr = app.primaryScreen()
        geo = scr.availableGeometry() if scr else None
        if geo:
            w = int(min(1280, max(1000, geo.width() * 0.80)))
            h = int(min(1000, max(900, geo.height() * 0.85)))
            window.resize(w, h)
            x = geo.x() + (geo.width() - w) // 2
            y = geo.y() + (geo.height() - h) // 2
            window.move(x, y)
        else:
            window.resize(1280, 1000)
    except Exception:
        window.resize(1280, 1000)

    # 启动流程控制
    _loading_done = False
    _startup_finished = False

    def _try_finish():
        """当 loading 完成 且 2.5s 已过，才显示主窗口并关闭启动画面。"""
        nonlocal _startup_finished
        if _startup_finished:
            return

        if not _loading_done:
            return

        if splash_shown_at is not None and not _skip_splash:
            elapsed = time.time() - splash_shown_at
            if elapsed < 1.0:
                # 还没到 1.0 秒，稍后再检查
                QTimer.singleShot(int((1.0 - elapsed) * 1000) + 50, _try_finish)
                return

        _startup_finished = True

        try:
            if splash is not None:
                try:
                    splash.set_status("正在启动")
                except Exception:
                    pass
        except Exception:
            pass

        # 显示主窗口
        try:
            scr2 = app.primaryScreen()
            geo2 = scr2.availableGeometry() if scr2 else None
            if geo2:
                w2 = int(min(1280, max(1000, geo2.width() * 0.80)))
                h2 = int(min(1000, max(900, geo2.height() * 0.85)))
                window.resize(w2, h2)
                x2 = geo2.x() + (geo2.width() - w2) // 2
                y2 = geo2.y() + (geo2.height() - h2) // 2
                window.move(x2, y2)
            window.show()
            # 窗口显示后重新设置图标，防止任务栏图标消失
            if app_icon is not None:
                try:
                    window.setWindowIcon(app_icon)
                except Exception:
                    pass
            # 窗口显示后主动刷新仪表盘，确保设备信息正确显示
            try:
                info_tab = getattr(window, 'info_tab', None)
                if info_tab is not None:
                    QTimer.singleShot(150, info_tab.refresh)
            except Exception:
                pass
        except Exception:
            pass

        # 关闭启动画面
        try:
            if splash is not None:
                try:
                    splash.fade_out_and_close(duration_ms=220)
                except Exception:
                    splash.close()
        except Exception:
            pass

    def _on_loading_done():
        """loading 完成回调：标记完成并尝试结束启动画面。"""
        nonlocal _loading_done
        _loading_done = True
        _try_finish()

    try:
        window.initialized.connect(_on_loading_done)
    except Exception:
        pass

    # 信号已连接，通过 QTimer 延迟调度同步加载，让启动画面先渲染出来
    # 延迟 100ms 确保启动画面和状态更新先渲染，避免加载阻塞 UI 导致卡顿
    try:
        QTimer.singleShot(100, window.init_pages)
    except Exception:
        traceback.print_exc()

    # 兜底：即使 loading 永远不完成，2 秒后也强制结束
    QTimer.singleShot(2000, lambda: _on_loading_done() if not _loading_done else None)

    # 主窗口已就绪，进入事件循环
    try:
        def _on_quit():
            try:
                log_service.log_app_exit("normal")
                log_service.flush_logs()
            except Exception:
                pass
        app.aboutToQuit.connect(_on_quit)
    except Exception:
        pass

    exit_code = app.exec()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()