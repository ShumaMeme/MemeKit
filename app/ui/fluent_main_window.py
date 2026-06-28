from PySide6.QtWidgets import QApplication, QWidget, QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import QThread, QTimer, QSettings, Qt, Signal
import traceback
import importlib
import webbrowser
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon, MessageBox

from app.services.update_checker import UpdateCheckerWorker
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

    def __init__(self, parent: QWidget | None = None, *, eager_load: bool = True, defer_init: bool = False):
        super().__init__(parent)
        self._startup_upd_thread = None
        self._startup_upd_worker = None
        self._init_queue = []
        self._init_queue_i = 0
        self._closing = False
        self._eager_load = bool(eager_load)
        self._defer_init = bool(defer_init)
        self._flash_center_confirmed = False
        self._prev_route_key = ""
        try:
            self.setWindowTitle("MemeKit")
        except Exception:
            pass
        # 监听导航切换事件，以便更新标题栏和刷机中心确认
        try:
            self.stackedWidget.currentChanged.connect(self._on_nav_changed)
        except Exception:
            pass
        # Windows 11: Mica; Windows 10: Acrylic（回退）。两者同时打开由系统自行选择可用材质
        try:
            self.setMicaEffectEnabled(True)
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
        # 让左侧导航也使用亚克力材质（Win11下配合 Mica 更统一）
        try:
            self.navigationInterface.setAcrylicEnabled(True)
        except Exception:
            pass
        # 尝试为自定义标题栏开启材质/透明
        try:
            self.setTitleBarTransparent(True)
        except Exception:
            pass
        try:
            tb = getattr(self, 'titleBar', None)
            if tb is not None:
                try:
                    tb.setAcrylicEnabled(True)
                except Exception:
                    pass
                try:
                    tb.setMicaEffectEnabled(True)
                except Exception:
                    pass
        except Exception:
            pass

        # 延迟到窗口显示后执行一次强制更新检查
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

    def _on_about_to_quit(self):
        try:
            t = getattr(self, '_startup_upd_thread', None)
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

    def _init_pages_async(self):
        try:
            self._init_queue = [
                ("info_tab",           "device_info",      FluentIcon.INFO,                                                  "仪表盘",   NavigationItemPosition.TOP),
                ("root_tab",           "root",             getattr(FluentIcon, "IOT", FluentIcon.INFO),                      "一键Root", NavigationItemPosition.TOP),
                ("quick_commands_tab", "quick_commands",   getattr(FluentIcon, "COMMAND_PROMPT", FluentIcon.FOLDER),         "快捷指令", NavigationItemPosition.TOP),
                ("font_backup_tab",    "font_backup",      getattr(FluentIcon, "SAVE", FluentIcon.FOLDER),                  "备份字库", NavigationItemPosition.TOP),
                ("font_restore_tab",   "font_restore",     FluentIcon.PLAY,                                                  "还原字库", NavigationItemPosition.TOP),
                ("flash_center_tab",   "flash_center",     getattr(FluentIcon, "SPEED_HIGH", FluentIcon.SPEED_HIGH),         "刷机中心", NavigationItemPosition.TOP),
                ("scrcpy_tab",         "scrcpy",           getattr(FluentIcon, "VIDEO", FluentIcon.PLAY),                    "投屏中心", NavigationItemPosition.TOP),
                ("software_tab",       "software_manager", getattr(FluentIcon, "APPLICATION", FluentIcon.BASKETBALL),        "软件管理", NavigationItemPosition.TOP),
                ("file_tab",           "file_manager",     FluentIcon.FOLDER,                                                "文件管理", NavigationItemPosition.TOP),
                ("settings_tab",       "settings",         FluentIcon.SETTING,                                               "设置",     NavigationItemPosition.BOTTOM),
            ]
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
                ("flash_center_tab",   "flash_center",     getattr(FluentIcon, "SPEED_HIGH", FluentIcon.SPEED_HIGH),         "刷机中心", NavigationItemPosition.TOP),
                ("scrcpy_tab",         "scrcpy",           getattr(FluentIcon, "VIDEO", FluentIcon.PLAY),                    "投屏中心", NavigationItemPosition.TOP),
                ("software_tab",       "software_manager", getattr(FluentIcon, "APPLICATION", FluentIcon.BASKETBALL),        "软件管理", NavigationItemPosition.TOP),
                ("file_tab",           "file_manager",     FluentIcon.FOLDER,                                                "文件管理", NavigationItemPosition.TOP),
                ("settings_tab",       "settings",         FluentIcon.SETTING,                                               "设置",     NavigationItemPosition.BOTTOM),
            ]

            for attr, obj_name, icon, title, pos in queue:
                if getattr(self, '_closing', False):
                    break
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
                # 每加载一个 TAB 后处理事件，保持启动画面响应
                QApplication.processEvents()

            try:
                if getattr(self, 'info_tab', None) is not None:
                    self.navigationInterface.setCurrentItem(self.info_tab)
                self._update_title()
            except Exception:
                traceback.print_exc()
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
                try:
                    self.initialized.emit()
                except Exception:
                    pass
                return

            attr, obj_name, icon, title, pos = self._init_queue[self._init_queue_i]
            self._init_queue_i += 1

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
        """导航切换回调：更新标题 + 刷机中心拦截确认。"""
        self._update_title()
        try:
            current_widget = self.stackedWidget.currentWidget()
            if current_widget is None:
                return
            route_key = current_widget.objectName()
            # 检查是否导航到刷机中心
            if route_key == "flash_center" and not self._flash_center_confirmed:
                # 拦截：显示确认弹窗
                self._show_flash_center_warning()
            elif route_key != "flash_center":
                # 离开刷机中心时重置状态
                self._flash_center_confirmed = False
            self._prev_route_key = route_key
        except Exception:
            traceback.print_exc()

    def _update_title(self, *args):
        try:
            # 获取当前选中的组件对象名称（即路由键）
            current_widget = self.stackedWidget.currentWidget()
            if not current_widget:
                return
                
            route_key = current_widget.objectName()
            
            # 由于 sync 模式下 self._init_queue 可能为空，我们需要重建一个查找表
            queue = getattr(self, '_init_queue', [])
            if not queue:
                queue = [
                    ("info_tab",           "device_info",      None, "仪表盘",   None),
                    ("root_tab",           "root",             None, "一键Root", None),
                    ("quick_commands_tab", "quick_commands",   None, "快捷指令", None),
                    ("font_backup_tab",    "font_backup",      None, "备份字库", None),
                    ("font_restore_tab",   "font_restore",     None, "还原字库", None),
                    ("flash_center_tab",   "flash_center",     None, "刷机中心", None),
                    ("scrcpy_tab",         "scrcpy",           None, "投屏中心", None),
                    ("software_tab",       "software_manager", None, "软件管理", None),
                    ("file_tab",           "file_manager",     None, "文件管理", None),
                    ("settings_tab",       "settings",         None, "设置",     None),
                ]

            title = "MemeKit"
            for queue_item in queue:
                # 队列项格式: (attr, obj_name, icon, title, pos)
                if queue_item[1] == route_key:
                    title = f"MemeKit - {queue_item[3]}"
                    break
            
            # 更新窗口原生的标题
            self.setWindowTitle(title)
            
            # 更新 QFluentWidgets 自定义标题栏的标题
            if hasattr(self, 'titleBar') and hasattr(self.titleBar, 'titleLabel'):
                self.titleBar.titleLabel.setText(title)
        except Exception:
            traceback.print_exc()

    def _show_flash_center_warning(self):
        """显示刷机风险确认弹窗（模糊背景，样式与检查更新弹窗保持一致）。"""
        from app.components.blur_popup import _BlurOverlay, _play_system_sound
        
        blur = _BlurOverlay(self)
        
        # 播放系统提示音
        _play_system_sound()

        dlg = QDialog(self.window())
        dlg.setWindowTitle("刷机风险确认")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet("""
            QDialog {
                background-color: #FFFFFF;
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 标题
        title_lbl = QLabel("\u26a0\ufe0f \u8b66\u544a\uff1a\u5237\u673a\u5b58\u5728\u4e0d\u53ef\u9006\u98ce\u9669")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #1D1B20;")
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
        content_lbl.setStyleSheet("font-size: 14px; color: #333333; padding: 8px 0;")
        layout.addWidget(content_lbl)

        # 按钮行
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(16)

        btn_cancel = QPushButton("\u53d6\u6d88")
        btn_cancel.setStyleSheet("""
            QPushButton {
                color: #333333;
                background-color: #E0E0E0;
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
        blur.dispose()

        if result == QDialog.Accepted:
            self._flash_center_confirmed = True
            # 重新导航到刷机中心
            try:
                flash_tab = getattr(self, 'flash_center_tab', None)
                if flash_tab is not None:
                    self.switchTo(flash_tab)
            except Exception:
                pass
        else:
            self._flash_center_confirmed = False
            # 返回仪表盘
            try:
                info_tab = getattr(self, 'info_tab', None)
                if info_tab is not None:
                    self.switchTo(info_tab)
            except Exception:
                pass

    def _check_update_on_launch(self):
        try:
            settings = QSettings()
            url = settings.value("update/url", "") or ""
            if not url:
                return
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
                return
            latest = str(info.get("version", "")).strip()
            download = info.get("url", "") or ""
            notes = info.get("notes", "") or ""
            cur = str(VERSION)
            if latest and latest > cur:
                msg = f"\u53d1\u73b0\u65b0\u7248\u672c\uff1a{latest}\n\u5f53\u524d\u7248\u672c\uff1a{cur}"
                if notes:
                    msg += f"\n\n\u66f4\u65b0\u5185\u5bb9\uff1a\n{notes}"
                box = MessageBox("\u53d1\u73b0\u66f4\u65b0", msg, self)
                try:
                    # 仅保留一个按钮，移除取消；禁止遮罩关闭
                    box.cancelButton.hide()
                    box.setClosableOnMaskClicked(False)
                    # 禁止窗口右上角关闭
                    box.setWindowFlag(Qt.WindowCloseButtonHint, False)
                except Exception:
                    pass
                if box.exec():  # 模态
                    if download:
                        try:
                            webbrowser.open(download)
                        except Exception:
                            pass
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self._closing = True
        except Exception:
            pass
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
        # 清理启动更新线程（不阻塞关闭）
        try:
            t = getattr(self, '_startup_upd_thread', None)
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
        return super().closeEvent(event)