import os

from PySide6.QtGui import QIcon, QColor
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QProgressBar, QAbstractItemView,
    QDialog
)
from qfluentwidgets import (CardWidget, PrimaryPushButton, PushButton, InfoBar, InfoBarPosition, FluentIcon, LineEdit, SmoothScrollArea, BodyLabel, RoundMenu, Action, isDarkTheme)

from app.services import adb_service
from app.components.blur_popup import show_blur_custom, show_blur_menu
from app.components.glass_style import apply_banner_style, refresh_banner_style


class _ListWorker(QThread):
    result_ready = Signal(list, str)

    def __init__(self, path: str, serial: str = "", parent=None):
        super().__init__(parent)
        self.path = path or '/storage/emulated/0'
        self.serial = str(serial or "")

    def run(self):
        try:
            items, err = adb_service.list_dir(self.path, serial=self.serial)
            self.result_ready.emit(items or [], err or '')
        except Exception as e:
            self.result_ready.emit([], str(e))


class _DevLabelThread(QThread):
    """后台线程：执行设备检测（connection_summary + list_all_devices），避免阻塞 UI。"""
    result_ready = Signal(str, bool, list)  # serial, connected, online_devices

    def __init__(self, serial: str = "", parent=None):
        super().__init__(parent)
        self._serial = str(serial or "")

    def run(self):
        try:
            summary = adb_service.connection_summary(serial=self._serial)
            connected = bool(summary.get("connected", False))
        except Exception:
            connected = False
        try:
            online = adb_service.list_all_devices() or []
        except Exception:
            online = []
        self.result_ready.emit(self._serial, connected, online)


class _RenameDialog(QDialog):
    """重命名输入弹窗（QDialog + QFluentWidgets 组件，适配深色/浅色主题）。"""
    def __init__(self, title: str, label: str, default_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        # 使用统一弹窗样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(self)
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self.label = BodyLabel(label, self)
        layout.addWidget(self.label)

        self.edit = LineEdit(self)
        try:
            self.edit.setText(default_text or "")
            self.edit.selectAll()
        except Exception:
            pass
        layout.addWidget(self.edit)

        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_cancel = PushButton("取消", self)
        btn_ok = PrimaryPushButton("确定", self)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._on_ok)
        btn_lay.addWidget(btn_cancel)
        btn_lay.addWidget(btn_ok)
        layout.addLayout(btn_lay)

    def _on_ok(self):
        self.accept()

    def text(self) -> str:
        try:
            return str(self.edit.text() or '').strip()
        except Exception:
            return ''


class _PropsDialog(QDialog):
    """文件属性弹窗（QDialog + QFluentWidgets 组件，统一深色/浅色主题）。"""
    def __init__(self, title: str, lines: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        # 使用统一弹窗样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(self)
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        for line in (lines or []):
            lbl = BodyLabel(str(line), self)
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        layout.addSpacing(8)
        btn_ok = PrimaryPushButton("关闭", self)
        btn_ok.clicked.connect(self.accept)
        layout.addWidget(btn_ok, alignment=Qt.AlignCenter)


class _TransferWorker(QThread):
    result_ready = Signal(bool, str)

    def __init__(self, mode: str, src, dst, serial: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode  # 'pull' | 'push'
        self.src = src
        self.dst = dst
        self.serial = str(serial or "")

    def run(self):
        try:
            ok = True; msg = ''
            if self.mode == 'pull':
                ok, msg = adb_service.pull_path(self.src, self.dst, serial=self.serial)
            elif self.mode == 'push':
                # 支持多文件
                if isinstance(self.src, (list, tuple)):
                    for p in self.src:
                        ok, msg = adb_service.push_path(p, self.dst, serial=self.serial)
                        if not ok:
                            break
                else:
                    ok, msg = adb_service.push_path(self.src, self.dst, serial=self.serial)
            elif self.mode == 'copy':
                # src: remote path; dst: remote dir
                ok, msg = adb_service.copy_path(self.src, self.dst, serial=self.serial)
            elif self.mode == 'move':
                ok, msg = adb_service.move_path(self.src, self.dst, serial=self.serial)
            elif self.mode == 'rename':
                # dst: new name
                ok, msg = adb_service.rename_path(self.src, self.dst, serial=self.serial)
            else:
                ok, msg = False, '未知的传输模式'
            self.result_ready.emit(ok, msg or '')
        except Exception as e:
            self.result_ready.emit(False, str(e))


class _StreamTransferWorker(QThread):
    progress = Signal(int)  # percent 0-100
    result_ready = Signal(bool, str)

    def __init__(self, mode: str, src: str, dst: str, total_bytes: int | None = None, serial: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode  # 'pull'|'push'
        self.src = src
        self.dst = dst
        self.total = total_bytes or 0
        self.serial = str(serial or "")
        self._stopped = False

    def run(self):
        try:
            self.progress.emit(-1)
            if self._stopped:
                self.result_ready.emit(False, "已取消")
                return

            ok = False
            msg = ""
            if self.mode == 'pull':
                ok, msg = adb_service.pull_path(self.src, self.dst, serial=self.serial)
            else:
                ok, msg = adb_service.push_path(self.src, self.dst, serial=self.serial)

            if self._stopped:
                self.result_ready.emit(False, "已取消")
                return

            if ok:
                self.progress.emit(100)
                self.result_ready.emit(True, msg or '')
            else:
                self.result_ready.emit(False, msg or '传输失败')
                
        except Exception as e:
            self.result_ready.emit(False, str(e))

    def stop(self):
        self._stopped = True
        return


class _BatchPushWorker(QThread):
    """批量推送多个文件到手机的线程，逐个执行并报告进度。"""
    progress = Signal(int)
    log = Signal(str)
    result_ready = Signal(bool, str)

    def __init__(self, files: list, remote_dir: str, serial: str = "", parent=None):
        super().__init__(parent)
        self.files = list(files or [])
        self.remote_dir = remote_dir or '/storage/emulated/0'
        self.serial = str(serial or "")

    def run(self):
        total = len(self.files)
        if total == 0:
            self.result_ready.emit(False, "没有文件")
            return
        success_count = 0
        fail_count = 0
        for idx, p in enumerate(self.files, 1):
            try:
                self.log.emit(f'正在推送 {idx}/{total}: {os.path.basename(p)}')
                ok, msg = adb_service.push_path(p, self.remote_dir, serial=self.serial)
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                    self.log.emit(f'失败: {os.path.basename(p)} - {msg}')
            except Exception as e:
                fail_count += 1
                self.log.emit(f'异常: {os.path.basename(p)} - {e}')
            # 整体进度百分比
            pct = int(idx * 100 / total)
            self.progress.emit(pct)
        summary = f'完成：成功 {success_count}，失败 {fail_count}'
        self.progress.emit(100)
        self.result_ready.emit(fail_count == 0, summary)


class FileManagerTab(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._tx_worker = None
        self._dev_label_thread = None
        self._dev_label_refresh_pending = False  # 设备切换后是否需要刷新文件列表
        self._clipboard = {"mode": None, "paths": []}  # mode: 'copy'|'cut'
        self._cwd = '/storage/emulated/0'
        self._current_serial = ""
        self._build_ui()

    def set_current_serial(self, serial: str):
        """接收仪表盘广播的设备 serial，刷新文件列表。"""
        new_serial = str(serial or "").strip()
        if new_serial == self._current_serial:
            return
        self._current_serial = new_serial
        self._update_dev_label()
        # 设备切换后刷新当前目录
        try:
            self._refresh()
        except Exception:
            pass

    def _update_dev_label(self, refresh_after: bool = False):
        """根据当前 serial 更新 banner 上的设备显示（后台线程执行 ADB 调用）。

        若当前设备已断开，自动切换到第一台可用设备，避免标签显示旧设备。
        颜色根据深色/浅色主题动态切换，确保可读性。

        Args:
            refresh_after: 若为 True，则在检测到设备切换后额外触发一次 _refresh。
        """
        _dark = isDarkTheme()
        _gray = "#9CA3AF" if _dark else "#808080"
        serial = self._current_serial
        if not serial:
            self.dev_label.setText("未选择设备")
            self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_gray};")
            return
        # 确保线程不重叠执行：若已有线程在跑，则合并刷新标记后跳过
        old = self._dev_label_thread
        if old is not None:
            if old.isRunning():
                if refresh_after:
                    self._dev_label_refresh_pending = True
                return
            try:
                old.result_ready.disconnect(self._on_dev_label_result)
            except Exception:
                pass
        self._dev_label_refresh_pending = refresh_after
        self._dev_label_thread = _DevLabelThread(serial=serial, parent=self)
        self._dev_label_thread.result_ready.connect(self._on_dev_label_result, Qt.QueuedConnection)
        self._dev_label_thread.start()

    def _on_dev_label_result(self, serial: str, connected: bool, online: list):
        """在主线程中根据后台线程的结果更新设备标签（由 QueuedConnection 保证）。"""
        _dark = isDarkTheme()
        _gray = "#9CA3AF" if _dark else "#808080"
        _green = "#23C343" if _dark else "#00b42a"
        _red = "#FF6B6B" if _dark else "#f53f3f"
        switched = False
        try:
            if connected:
                self.dev_label.setText(f"{serial}（已连接）")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_green};")
            else:
                # 当前设备已断开，尝试切换到第一台可用设备
                if online:
                    new_serial = online[0]
                    if new_serial != serial:
                        self._current_serial = new_serial
                        switched = True
                        self.dev_label.setText(f"{new_serial}（已连接）")
                        self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_green};")
                        # 设备切换后，若标记了刷新则触发文件列表刷新
                        if self._dev_label_refresh_pending:
                            self._dev_label_refresh_pending = False
                            try:
                                self._refresh()
                            except Exception:
                                pass
                        return
                # 没有其他可用设备，显示未连接
                self.dev_label.setText(f"{serial}（未连接）")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_red};")
        except Exception:
            try:
                self.dev_label.setText(self._current_serial or "未选择设备")
            except Exception:
                pass
        # 未发生设备切换时，清除刷新标记
        if not switched:
            self._dev_label_refresh_pending = False

    def _is_worker_running(self) -> bool:
        """安全检查列表 worker 是否在运行。"""
        w = self._worker
        if w is None:
            return False
        try:
            return w.isRunning()
        except RuntimeError:
            self._worker = None
            return False

    def _is_tx_worker_running(self) -> bool:
        """安全检查传输 worker 是否在运行，避免 C++ 对象已删除导致崩溃。"""
        w = self._tx_worker
        if w is None:
            return False
        try:
            return w.isRunning()
        except RuntimeError:
            self._tx_worker = None
            return False

    def _build_ui(self):
        outer = QVBoxLayout(self)
        try:
            outer.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        try:
            self.scroll.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        except Exception:
            pass
        outer.addWidget(self.scroll)

        container = QWidget()
        try:
            container.setStyleSheet("background: transparent;")
        except Exception:
            pass
        self.scroll.setWidget(container)

        root = QVBoxLayout(container)
        try:
            root.setContentsMargins(20, 20, 20, 20)
        except Exception:
            pass
        try:
            root.setSpacing(24)
        except Exception:
            pass

        self._build_banner(root)
        
        # 主要工作区
        main_h_layout = QHBoxLayout()
        main_h_layout.setSpacing(24)
        main_h_layout.setContentsMargins(0, 0, 0, 0)

        left_col = QVBoxLayout()
        left_col.setSpacing(24)
        left_col.setContentsMargins(0, 0, 0, 0)
        self._build_browser_card(left_col)

        right_col = QVBoxLayout()
        right_col.setSpacing(24)
        right_col.setContentsMargins(0, 0, 0, 0)
        self._build_action_card(right_col)
        self._build_progress_card(right_col)
        self._build_info_card(right_col)
        
        left_w = QWidget()
        left_w.setLayout(left_col)
        right_w = QWidget()
        right_w.setLayout(right_col)
        
        main_h_layout.addWidget(left_w, 7)
        main_h_layout.addWidget(right_w, 3)
        root.addLayout(main_h_layout)

        # signals
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)
        self.btn_go.clicked.connect(self._open_entered)
        self.btn_up.clicked.connect(self._go_up)
        self.btn_home.clicked.connect(self._go_home)
        self.btn_pull.clicked.connect(self._pull_selected)
        self.btn_import_files.clicked.connect(self._import_files)
        self.btn_import_folder.clicked.connect(self._import_folder)
        self.grid.itemDoubleClicked.connect(self._enter_item)
        try:
            self.grid.customContextMenuRequested.connect(self._on_ctx_menu_widget)
        except Exception:
            pass
        # 监听主题切换，实时更新文字颜色
        try:
            from qfluentwidgets import qconfig
            qconfig.themeChanged.connect(self._on_theme_changed)
        except Exception:
            pass

    def _build_banner(self, parent_lay):
        banner_w = QWidget()
        self.banner_w = banner_w
        try:
            banner_w.setProperty("banner", "true")
            banner_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        banner_w.setFixedHeight(110)
        apply_banner_style(banner_w)
        # Banner 背景由 glass_widgets_qss() 的 QWidget[banner="true"] 规则控制
        banner = QHBoxLayout(banner_w)
        banner.setContentsMargins(20, 20, 20, 20)
        banner.setSpacing(16)

        icon_lbl = QLabel()
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setPixmap(FluentIcon.FOLDER.icon().pixmap(48, 48))
        icon_lbl._fluent_icon = FluentIcon.FOLDER
        
        title_col = QVBoxLayout()
        title_col.setContentsMargins(0,0,0,0)
        title_col.setSpacing(4)
        t = QLabel("文件管理")
        t.setStyleSheet("font-size: 22px; font-weight: 600;")
        s = QLabel("包含基础功能的手机端文件管理工具")
        s.setStyleSheet("font-size: 14px;")
        title_col.addWidget(t)
        title_col.addWidget(s)
        
        banner.addWidget(icon_lbl)
        banner.addLayout(title_col)
        banner.addStretch(1)

        # 当前设备显示卡片
        dev_col = QVBoxLayout()
        dev_col.setContentsMargins(0, 0, 0, 0)
        dev_col.setSpacing(2)
        dev_title = QLabel("当前设备")
        dev_title.setStyleSheet("font-size: 12px; color: #9CA3AF;")
        dev_title.setAlignment(Qt.AlignRight)
        self.dev_label = QLabel("未选择设备")
        self.dev_label.setStyleSheet("font-size: 14px; font-weight: 500;")
        self.dev_label.setAlignment(Qt.AlignRight)
        dev_col.addWidget(dev_title)
        dev_col.addWidget(self.dev_label)
        banner.addLayout(dev_col)

        parent_lay.addWidget(banner_w)
        
    def _build_browser_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)

        head = QHBoxLayout()
        icon = QLabel("📂")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("文件浏览")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.btn_up = PushButton(FluentIcon.UP, '上级')
        self.btn_up.setFixedHeight(36)
        # 路径显示框：使用 qfluentwidgets LineEdit 默认主题样式
        # 不设自定义 setStyleSheet，让 qfluentwidgets 根据 isDarkTheme() 自动适配
        # 这样与 font_backup_tab / font_restore_tab 的 LineEdit 样式保持一致
        self.path_edit = LineEdit(self)
        self.path_edit.setText(self._cwd or "")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.setFixedHeight(36)
        self.path_edit.setPlaceholderText("输入或显示当前路径")
        self.btn_go = PrimaryPushButton('打开')
        self.btn_go.setFixedHeight(36)
        self.btn_refresh = PushButton(FluentIcon.SYNC, '刷新')
        self.btn_refresh.setFixedHeight(36)
        self.btn_home = PushButton(FluentIcon.HOME, '重置路径')
        self.btn_home.setFixedHeight(36)
        path_row.addWidget(self.btn_up)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.btn_go)
        path_row.addWidget(self.btn_refresh)
        path_row.addWidget(self.btn_home)
        lay.addLayout(path_row)

        # 图标网格视图
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWrapping(True)
        self.grid.setGridSize(QSize(120, 120))
        self.grid.setIconSize(QSize(48, 48))
        self.grid.setSpacing(12)
        self.grid.setAcceptDrops(False)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.setTextElideMode(Qt.ElideRight)
        self.grid.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                border-radius: 8px;
                padding: 8px 4px;
                text-align: center;
            }
            QListWidget::item:hover {
                background-color: rgba(22, 119, 255, 0.08);
            }
            QListWidget::item:selected {
                background-color: rgba(22, 119, 255, 0.18);
                border: 1px solid #1677ff;
            }
        """)
        lay.addWidget(self.grid, 1)
        parent_lay.addWidget(card, 1)
        
    def _build_action_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)
        
        head = QHBoxLayout()
        icon = QLabel("🛠️")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("快捷操作")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        
        self.btn_pull = PrimaryPushButton(FluentIcon.DOWNLOAD, '拉取选中项到本地')
        self.btn_pull.setFixedHeight(36)
        lay.addWidget(self.btn_pull)

        # 导入：两个独立按钮，避免模态菜单嵌套模态对话框导致 worker 启动异常
        btn_row = QHBoxLayout()
        self.btn_import_files = PushButton(FluentIcon.DOCUMENT, '导入文件')
        self.btn_import_files.setFixedHeight(36)
        self.btn_import_folder = PushButton(FluentIcon.FOLDER, '导入文件夹')
        self.btn_import_folder.setFixedHeight(36)
        btn_row.addWidget(self.btn_import_files)
        btn_row.addWidget(self.btn_import_folder)
        lay.addLayout(btn_row)

        parent_lay.addWidget(card)
        
    def _build_progress_card(self, parent_lay):
        # 状态标签：始终可见，用于显示操作状态（如"共 N 项"、"操作已完成"等）
        self.status_label = BodyLabel('准备就绪')
        _hint_color = "#9CA3AF" if isDarkTheme() else "#4e5969"
        self.status_label.setStyleSheet(f"color:{_hint_color}; font-size:13px; padding: 4px 0;")
        parent_lay.addWidget(self.status_label)

        # 传输进度卡片：仅在拉取/推送文件时显示
        self.prog_wrap = CardWidget()
        lay = QVBoxLayout(self.prog_wrap)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        
        head = QHBoxLayout()
        icon = QLabel("⏳")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("传输进度")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        self.prog_label = QLabel('0%')
        _prog_color = "#4A90E2" if isDarkTheme() else "#1677ff"
        self.prog_label.setStyleSheet(f"font-weight:bold; color:{_prog_color};")
        head.addWidget(self.prog_label)
        lay.addLayout(head)
        
        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setFixedHeight(6)
        self.prog_bar.setStyleSheet(
            "QProgressBar{border:none;border-radius:3px;background:rgba(0,0,0,0.05);}"
            "QProgressBar::chunk{border-radius:3px;background:#1677ff;}"
        )
        lay.addWidget(self.prog_bar)
        
        self.prog_wrap.setVisible(False)
        parent_lay.addWidget(self.prog_wrap)
        
    def _build_info_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)
        
        head = QHBoxLayout()
        icon = QLabel("💡")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("使用提示")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        
        content = BodyLabel(
            "1. 右键文件或目录可以执行高级操作，如复制、移动、删除等。\n\n"
            "2. 双击文件夹可以进入该目录。\n\n"
            "3. 拉取/推送超大文件时，UI 可能会有轻微卡顿。\n\n"
            "4. Android 11+ 设备部分目录(如 Android/data) 权限受限，可能无法访问。"
        )
        content.setWordWrap(True)
        _hint_color = "#9CA3AF" if isDarkTheme() else "#4e5969"
        content.setStyleSheet(f"color:{_hint_color}; font-size:14px; line-height: 1.6;")
        self._guide_content = content
        lay.addWidget(content)
        lay.addStretch(1)

        parent_lay.addWidget(card, 1)
        

    def _on_refresh_clicked(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-刷新")
        except Exception:
            pass
        self._refresh()

    def _refresh(self):
        # start worker to list
        path = self.path_edit.text().strip() or '/storage/emulated/0'
        # 同步刷新当前设备状态显示
        try:
            self._update_dev_label()
        except Exception:
            pass
        # 若已有线程在跑，断开信号并等待退出，避免旧结果覆盖新结果
        try:
            if self._is_worker_running():
                try:
                    self._worker.result_ready.disconnect()
                except Exception:
                    pass
                self._worker.quit()
                self._worker.wait(500)
        except Exception:
            pass
        self._cwd = path
        self._set_status('正在加载…')
        self._worker = _ListWorker(path, serial=self._current_serial, parent=self)
        # 强制使用排队连接，确保在主线程更新 UI
        self._worker.result_ready.connect(self._on_list_finished, Qt.QueuedConnection)

        self._worker.result_ready.connect(self._worker.quit)
        self._worker.result_ready.connect(self._worker.deleteLater)
        self._worker.start()

    def _cleanup_list_worker(self):
        self._worker = None

    def _on_list_finished(self, items: list, err: str):
        self._worker = None
        if err:
            QTimer.singleShot(0, lambda: self._set_status(f'列目录失败：{err}'))
            return
        try:
            self.grid.clear()
        except Exception:
            pass
        # 根据当前主题选择文字颜色：深色模式用紫色，浅色模式用深灰
        try:
            text_color = "#B388FF" if isDarkTheme() else "#1f1f1f"
        except Exception:
            text_color = "#1f1f1f"
        for it in items:
            name = it.get('name', '')
            size = it.get('size', '')
            typ = it.get('type', '')
            is_dir = (typ or '').lower() == 'dir'
            # 图标：文件夹用文件夹图标，文件按扩展名匹配类型图标
            ico = self._icon_for_file(name, is_dir)
            # 显示文本：文件夹只显示名称；文件显示名称+大小
            if is_dir:
                disp_text = name
            else:
                disp_text = f"{name}\n{self._fmt_size(size)}"
            item = QListWidgetItem(ico, disp_text)
            # 设置文字颜色，避免选中后变白不可读
            item.setForeground(QColor(text_color))
            # 存储元数据
            item.setData(Qt.UserRole, {'name': name, 'type': typ, 'size': size, 'is_dir': is_dir})
            item.setToolTip(f"{name}\n类型: {'文件夹' if is_dir else '文件'}")
            self.grid.addItem(item)
        QTimer.singleShot(0, lambda: self._set_status(f'共 {len(items)} 项'))

    def _icon_for_file(self, name: str, is_dir: bool):
        """根据文件名扩展名返回对应的 FluentIcon 图标。"""
        try:
            if is_dir:
                return FluentIcon.FOLDER.icon()
            ext = (os.path.splitext(name)[1] or '').lower().lstrip('.')
            # 视频
            if ext in ('mp4', 'avi', 'mkv', 'mov', 'flv', 'wmv', 'webm', 'm4v', '3gp', 'mpeg', 'mpg'):
                return FluentIcon.VIDEO.icon()
            # 图片
            if ext in ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'ico', 'tiff', 'tif', 'svg'):
                return FluentIcon.PHOTO.icon()
            # 音频
            if ext in ('mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a', 'wma', 'opus', 'amr', 'mid', 'midi'):
                return FluentIcon.MUSIC.icon()
            # APK / 应用包
            if ext == 'apk':
                return FluentIcon.APPLICATION.icon()
            # 代码
            if ext in ('py', 'js', 'ts', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'go', 'rs', 'rb', 'php', 'sh', 'bat', 'ps1', 'html', 'css', 'xml', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg'):
                return FluentIcon.CODE.icon()
            # 压缩包
            if ext in ('zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso'):
                try:
                    return FluentIcon.ARCHIVE.icon()
                except Exception:
                    return FluentIcon.DOCUMENT.icon()
            # 字体
            if ext in ('ttf', 'otf', 'woff', 'woff2'):
                return FluentIcon.FONT.icon()
            # 日历/邮件等特殊文件
            if ext in ('eml', 'msg'):
                return FluentIcon.MAIL.icon()
            # 默认文档图标
            return FluentIcon.DOCUMENT.icon()
        except Exception:
            try:
                return FluentIcon.DOCUMENT.icon()
            except Exception:
                return QIcon()

    def _on_theme_changed(self):
        """主题切换时，重新加载列表以更新图标和文字颜色（最可靠方式）。"""
        try:
            self._refresh()
        except Exception:
            pass

    def _open_entered(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-进入路径", self.path_edit.text().strip())
        except Exception:
            pass
        self._refresh()

    def _go_up(self):
        p = self.path_edit.text().strip() or '/storage/emulated/0'
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-上一级", f"{p}")
        except Exception:
            pass
        if p == '/':
            return
        parent = os.path.dirname(p.rstrip('/'))
        if not parent:
            parent = '/'
        self.path_edit.setText(parent)
        self._refresh()

    def _go_home(self):
        """重置到默认路径 /storage/emulated/0"""
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-首页", "/storage/emulated/0")
        except Exception:
            pass
        self.path_edit.setText('/storage/emulated/0')
        self._refresh()

    def _enter_item(self, item: QListWidgetItem):
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        name = data.get('name', '')
        is_dir = data.get('is_dir', False)
        if not name or not is_dir:
            return
        newp = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-进入文件夹", newp)
        except Exception:
            pass
        self.path_edit.setText(newp)
        self._refresh()

    def _pull_selected(self):
        items = self.grid.selectedItems()
        if not items:
            QTimer.singleShot(0, lambda: self._set_status('请选择文件'))
            return
        # 收集所有选中项
        selected = []
        for it in items:
            data = it.data(Qt.UserRole) or {}
            name = data.get('name', '')
            is_dir = data.get('is_dir', False)
            if name:
                remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
                selected.append((name, remote, is_dir))
        try:
            from app.services import log_service
            names = [s[0] for s in selected]
            log_service.log_ui_action("文件管理-拉取文件", f"{len(names)} 项: {', '.join(names)}")
        except Exception:
            pass
        if not selected:
            return

        # 单项选择：弹保存对话框；多项选择：选目录批量拉取
        if len(selected) == 1:
            name, remote, is_dir = selected[0]
            if is_dir:
                local_dir = QFileDialog.getExistingDirectory(self, '选择保存文件夹')
                if not local_dir:
                    return
                local = os.path.join(local_dir, name)
            else:
                local, _ = QFileDialog.getSaveFileName(self, '保存到本地', name)
                if not local:
                    return
            try:
                from app.services import log_service
                log_service.log_file_event("拉取到本地", f"{remote} -> {local}")
            except Exception:
                pass
            self._start_stream_transfer('pull', remote, local, self._probe_total(remote))
        else:
            local_dir = QFileDialog.getExistingDirectory(self, f'选择保存文件夹（共 {len(selected)} 项）')
            if not local_dir:
                return
            try:
                from app.services import log_service
                names = [s[0] for s in selected]
                log_service.log_file_event("批量拉取", f"{len(selected)} 项 [{', '.join(names)}] -> {local_dir}")
            except Exception:
                pass
            # 逐个拉取
            self._pull_batch(selected, local_dir)

    def _pull_batch(self, selected, local_dir):
        """批量拉取多项到本地目录"""
        total = len(selected)
        for idx, (name, remote, is_dir) in enumerate(selected, 1):
            self._set_status(f'正在拉取 {idx}/{total}: {name}')
            local = os.path.join(local_dir, name)
            # 同步执行，避免并发
            self._start_stream_transfer('pull', remote, local, self._probe_total(remote))
            # 简单串行：等待当前完成。实际由 _on_stream_finished 触发刷新
        self._set_status(f'批量拉取已提交，共 {total} 项')

    def refresh_theme(self):
        """主题切换时刷新文字颜色和路径框样式。"""
        try:
            if hasattr(self, 'banner_w'):
                refresh_banner_style(self.banner_w)
            _dark = isDarkTheme()
            _hint = "#9CA3AF" if _dark else "#4e5969"
            if hasattr(self, 'status_label'):
                self.status_label.setStyleSheet(f"color:{_hint}; font-size:13px; padding: 4px 0;")
            if hasattr(self, '_guide_content'):
                self._guide_content.setStyleSheet(f"color:{_hint}; font-size:14px; line-height: 1.6;")
            # path_edit 不再需要手动刷新样式，使用 qfluentwidgets LineEdit 默认主题样式
        except Exception:
            pass

    def cleanup(self):
        try:
            if self._is_worker_running():
                try:
                    self._worker.result_ready.disconnect()
                except Exception:
                    pass
                self._worker.quit()
                self._worker.wait(1000)
        except Exception:
            pass
        try:
            if self._is_tx_worker_running():
                if hasattr(self._tx_worker, 'stop'):
                    self._tx_worker.stop()
                self._tx_worker.quit()
                self._tx_worker.wait(2000)
        except Exception:
            pass
        try:
            t = self._dev_label_thread
            if t is not None and t.isRunning():
                t.quit()
                t.wait(1500)
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self.cleanup()
        except Exception:
            pass
        return super().closeEvent(event)

    def contextMenuEvent(self, event):
        try:
            # 仅当右键发生在网格区域时弹出
            gp = event.globalPos()
            vp = self.grid.viewport()
            vp_rect = vp.rect()
            vp_pos = vp.mapFromGlobal(gp)
            if vp_rect.contains(vp_pos):
                self._on_ctx_menu(vp_pos)
                return
        except Exception:
            pass
        return super().contextMenuEvent(event)

    def showEvent(self, event):
        # 首次显示时自动刷新文件列表
        try:
            if not getattr(self, '_first_loaded', False):
                self._first_loaded = True
                QTimer.singleShot(100, self._refresh)
            else:
                # 后续显示：检查设备是否变化，若变化则刷新
                old_serial = self._current_serial
                QTimer.singleShot(100, lambda: self._check_device_changed(old_serial))
        except Exception:
            pass
        return super().showEvent(event)

    def _check_device_changed(self, old_serial: str):
        """显示时检查当前设备是否仍在线，若断开则切换到可用设备并刷新。

        通过 _update_dev_label 后台线程执行 ADB 检测，避免阻塞 UI。
        设备切换后由 _on_dev_label_result 自动触发文件列表刷新。
        """
        if not old_serial:
            return
        self._update_dev_label(refresh_after=True)

    def _fmt_size(self, val) -> str:
        try:
            s = int(val) if isinstance(val, (int,)) or str(val).isdigit() else -1
        except Exception:
            s = -1
        if s < 0:
            return '-'
        units = ['KB', 'MB', 'GB', 'TB']
        # 以 KB 起步
        size = s / 1024.0
        unit_idx = 0
        while size >= 1024.0 and unit_idx < len(units) - 1:
            size /= 1024.0
            unit_idx += 1
        # 显示到一位小数（>=10 则取整）
        if size >= 10:
            return f"{int(size)} {units[unit_idx]}"
        return f"{size:.1f} {units[unit_idx]}"

    def _on_ctx_menu(self, pos):
        item = self.grid.itemAt(pos)
        menu = RoundMenu(parent=self)
        act_import_files = Action(FluentIcon.SEND, '导入文件到手机', menu)
        act_import_dir = Action(FluentIcon.FOLDER, '导入文件夹到手机', menu)
        act_refresh = Action(FluentIcon.SYNC, '刷新', menu)
        act_paste = Action(FluentIcon.PASTE, '粘贴', menu)
        act_import_files.triggered.connect(self._import_files)
        act_import_dir.triggered.connect(self._import_folder)
        act_refresh.triggered.connect(self._refresh)
        act_paste.triggered.connect(self._paste_items)

        if not item:
            # 空白处右键：只显示粘贴、导入、刷新
            menu.addAction(act_paste)
            menu.addSeparator()
            menu.addAction(act_import_files)
            menu.addAction(act_import_dir)
            menu.addSeparator()
            menu.addAction(act_refresh)
            show_blur_menu(self, menu, self.grid.viewport().mapToGlobal(pos))
            return

        data = item.data(Qt.UserRole) or {}
        name = data.get('name', '')
        is_dir = data.get('is_dir', False)
        typ = '文件夹' if is_dir else '文件'
        act_open = Action(FluentIcon.FOLDER, '打开', menu)
        act_export = Action(FluentIcon.DOWNLOAD, '导出', menu)
        act_copy = Action(FluentIcon.COPY, '复制', menu)
        act_cut = Action(FluentIcon.CUT, '剪切', menu)
        act_rename = Action(FluentIcon.EDIT, '重命名', menu)
        act_delete = Action(FluentIcon.DELETE, '删除', menu)
        act_props = Action(FluentIcon.INFO, '属性', menu)
        act_open.setEnabled(is_dir)
        act_open.triggered.connect(lambda: self._enter_item(item))
        act_export.triggered.connect(lambda: self._export_item(name, typ))
        act_copy.triggered.connect(lambda: self._clipboard_set('copy', name))
        act_cut.triggered.connect(lambda: self._clipboard_set('cut', name))
        act_rename.triggered.connect(lambda: self._rename_item(name))
        act_delete.triggered.connect(lambda: self._delete_item(name))
        act_props.triggered.connect(lambda: self._show_props(name))
        menu.addAction(act_open)
        menu.addAction(act_export)
        menu.addSeparator()
        menu.addAction(act_copy)
        menu.addAction(act_cut)
        menu.addAction(act_paste)
        menu.addSeparator()
        menu.addAction(act_rename)
        menu.addAction(act_delete)
        menu.addAction(act_props)
        menu.addSeparator()
        menu.addAction(act_import_files)
        menu.addAction(act_import_dir)
        menu.addSeparator()
        menu.addAction(act_refresh)
        show_blur_menu(self, menu, self.grid.viewport().mapToGlobal(pos))

    def _on_ctx_menu_widget(self, pos):
        self._on_ctx_menu(pos)

    def _export_item(self, name: str, typ: str):
        remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        if typ == '文件夹':
            local_dir = QFileDialog.getExistingDirectory(self, '选择导出位置')
            if not local_dir:
                return
            dest = os.path.join(local_dir, os.path.basename(name))
            try:
                from app.services import log_service
                log_service.log_file_event("导出文件夹", f"{remote} -> {dest}")
            except Exception:
                pass
            self._start_stream_transfer('pull', remote, dest, self._probe_total(remote))
        else:
            local, _ = QFileDialog.getSaveFileName(self, '导出文件到本地', name)
            if not local:
                return
            try:
                from app.services import log_service
                log_service.log_file_event("导出文件", f"{remote} -> {local}")
            except Exception:
                pass
            self._start_stream_transfer('pull', remote, local, self._probe_total(remote))

    def _import_files(self):
        """按钮：导入文件（使用 Windows 原生文件选择器）"""
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-导入文件")
        except Exception:
            pass
        files, _ = QFileDialog.getOpenFileNames(self, '选择要导入的文件')
        if not files:
            return
        try:
            from app.services import log_service
            names = [os.path.basename(f) for f in files]
            log_service.log_file_event("导入文件", f"{len(files)} 个 [{', '.join(names)}] -> {self._cwd}")
        except Exception:
            pass
        if self._is_tx_worker_running():
            InfoBar.info('提示', '正在进行传输，请稍候...', parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return
        self._set_status(f'已选择 {len(files)} 个文件，准备传输...')
        worker = _BatchPushWorker(files, self._cwd, serial=self._current_serial, parent=self)
        self._tx_worker = worker
        self._progress_reset()
        worker.progress.connect(self._on_stream_progress, Qt.QueuedConnection)
        worker.log.connect(lambda msg: self._set_status(msg))
        worker.result_ready.connect(self._on_stream_finished, Qt.QueuedConnection)
        worker.result_ready.connect(worker.quit)
        worker.result_ready.connect(worker.deleteLater)
        worker.start()

    def _import_folder(self):
        """按钮：导入文件夹（使用 Windows 原生文件夹选择器）"""
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-导入文件夹")
        except Exception:
            pass
        folder = QFileDialog.getExistingDirectory(self, '选择要导入的文件夹')
        if not folder:
            return
        try:
            from app.services import log_service
            log_service.log_file_event("导入文件夹", f"{folder} -> {self._cwd}")
        except Exception:
            pass
        if self._is_tx_worker_running():
            InfoBar.info('提示', '正在进行传输，请稍候...', parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return
        self._set_status('已选择文件夹，准备传输...')
        worker = _BatchPushWorker([folder], self._cwd, serial=self._current_serial, parent=self)
        self._tx_worker = worker
        self._progress_reset()
        worker.progress.connect(self._on_stream_progress, Qt.QueuedConnection)
        worker.log.connect(lambda msg: self._set_status(msg))
        worker.result_ready.connect(self._on_stream_finished, Qt.QueuedConnection)
        worker.result_ready.connect(worker.quit)
        worker.result_ready.connect(worker.deleteLater)
        worker.start()

    def _start_transfer(self, mode: str, src, dst):
        # 防并发：如有正在执行的传输，先结束
        try:
            if self._is_tx_worker_running():
                InfoBar.info('提示', '正在进行传输，请稍候...', parent=self, position=InfoBarPosition.TOP, isClosable=True)
                return
        except Exception:
            pass
        self._tx_worker = _TransferWorker(mode, src, dst, serial=self._current_serial, parent=self)
        self._tx_worker.result_ready.connect(self._on_transfer_finished, Qt.QueuedConnection)
        self._tx_worker.result_ready.connect(self._tx_worker.quit)
        self._tx_worker.result_ready.connect(self._tx_worker.deleteLater)
        self._tx_worker.start()

    def _cleanup_tx_worker(self):
        self._tx_worker = None

    def _on_transfer_finished(self, ok: bool, msg: str):
        self._tx_worker = None
        if ok:
            QTimer.singleShot(0, lambda: self._set_status('操作已完成'))
            # 完成后刷新列表（例如导入后显示新文件）
            self._refresh()
            # 剪切模式粘贴后清空剪切板
            if self._clipboard.get('mode') == 'cut':
                self._clipboard = {"mode": None, "paths": []}
        else:
            QTimer.singleShot(0, lambda: self._set_status(msg or '操作失败'))

    # ---------- Clipboard & Operations ----------
    def _clipboard_set(self, mode: str, name: str):
        remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        self._clipboard = {"mode": mode, "paths": [remote]}
        try:
            from app.services import log_service
            log_service.log_file_event("复制" if mode == 'copy' else "剪切", remote)
        except Exception:
            pass
        QTimer.singleShot(0, lambda: self._set_status('已复制' if mode=='copy' else '已剪切'))

    def _paste_items(self):
        mode = self._clipboard.get('mode')
        paths = self._clipboard.get('paths') or []
        if not mode or not paths:
            self._set_status('剪贴板为空')
            return
        src = paths[0]
        dst_dir = self._cwd
        try:
            from app.services import log_service
            log_service.log_file_event("粘贴", f"{src} -> {dst_dir}")
        except Exception:
            pass
        if mode == 'copy':
            self._start_transfer('copy', src, dst_dir)
        elif mode == 'cut':
            self._start_transfer('move', src, dst_dir)

    def _rename_item(self, name: str):
        dlg = _RenameDialog('重命名', '请输入新名称：', name, self)
        if not show_blur_custom(self.window(), dlg):
            return
        new_name = dlg.text()
        if not new_name or new_name == name:
            return
        remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        try:
            from app.services import log_service
            log_service.log_file_event("重命名", f"{name} -> {new_name}")
        except Exception:
            pass
        self._start_transfer('rename', remote, new_name)

    def _show_props(self, name: str):
        try:
            from app.services import log_service
            log_service.log_ui_action("文件管理-查看属性", name)
        except Exception:
            pass
        remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        info = {}
        try:
            info = adb_service.stat_path(remote, serial=self._current_serial) or {}
        except Exception as e:
            InfoBar.error('错误', f'获取属性失败：{e}', parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return
        def _fallback_type() -> str:
            try:
                return '目录' if adb_service.is_dir(remote, serial=self._current_serial) else '文件'
            except Exception:
                return '-'
        ftype = info.get('type') or _fallback_type()
        raw_size = info.get('size', '-')
        size_disp = self._fmt_size(raw_size)
        mtime = info.get('mtime', info.get('raw_mtime', '-'))
        perm = info.get('perm', '-')
        user = info.get('user', '-')
        group = info.get('group', '-')
        detail_lines = [
            f'名称：{name}',
            f'路径：{remote}',
            f'类型：{ftype}',
            f'大小：{size_disp}',
            f'权限：{perm}',
            f'所有者：{user}:{group}',
            f'修改时间：{mtime}',
        ]
        raw_ls = info.get('raw_ls'); raw_du = info.get('raw_du')
        if raw_ls:
            detail_lines.append(f'ls -ld：{raw_ls.strip()}')
        if raw_du:
            detail_lines.append(f'du -s：{raw_du.strip()}')
        msg = '\n'.join(detail_lines)
        dlg = _PropsDialog('属性', detail_lines, self)
        show_blur_custom(self.window(), dlg)

    def _delete_item(self, name: str):
        remote = (self._cwd.rstrip('/') + '/' + name) if self._cwd != '/' else ('/' + name)
        try:
            from app.services import log_service
            log_service.log_file_event("删除", remote)
        except Exception:
            pass
        # 无模态弹窗，直接执行删除（如需确认我可再加）
        ok, msg = adb_service.delete_path(remote, serial=self._current_serial)
        if ok:
            self._set_status('已删除')
            self._refresh()
        else:
            self._set_status(msg or '删除失败')

    def _probe_total(self, remote: str) -> int:
        try:
            info = adb_service.stat_path(remote, serial=self._current_serial)
            sz = int(info.get('size', '0')) if info.get('size') else 0
            if sz > 0:
                return sz
        except Exception:
            pass
        # 目录时尝试 du -s（近似，以KB为单位）
        try:
            out = adb_service._adb_shell(["du", "-s", remote], timeout=20, serial=self._current_serial)
            # format: "<KB>\t<path>"
            kb = int((out.strip().split() or ['0'])[0])
            return kb * 1024
        except Exception:
            return 0

    def _start_stream_transfer(self, mode: str, src: str, dst: str, total: int | None = None):
        # 防并发
        try:
            if self._is_tx_worker_running():
                InfoBar.info('提示', '正在进行传输，请稍候...', parent=self, position=InfoBarPosition.TOP, isClosable=True)
                return
        except Exception:
            pass
        worker = _StreamTransferWorker(mode, src, dst, total or 0, serial=self._current_serial, parent=self)
        self._tx_worker = worker
        # inline progress
        self._progress_reset()
        
        # Connect signals to slots using QueuedConnection
        worker.progress.connect(self._on_stream_progress, Qt.QueuedConnection)
        worker.result_ready.connect(self._on_stream_finished, Qt.QueuedConnection)
        
        worker.result_ready.connect(worker.quit)
        worker.result_ready.connect(worker.deleteLater)
        worker.start()

    def _on_stream_progress(self, pct: int):
        self._progress_update(pct)

    def _on_stream_finished(self, ok: bool, msg: str):
        self._progress_complete(ok, msg)
        self._on_transfer_finished(ok, msg)

    def _progress_reset(self):
        def _do():
            try:
                self.prog_bar.setValue(0)
                self.prog_label.setText('0%')
                # 初始未知总量：设置为不确定模式，待收到百分比再恢复
                self.prog_bar.setMaximum(0)
                self.prog_wrap.setVisible(True)
            except Exception:
                pass
        QTimer.singleShot(0, _do)

    def _progress_update(self, percent: int):
        try:
            if percent is None or int(percent) < 0:
                # 不确定模式
                self.prog_bar.setMaximum(0)
                self.prog_label.setText('进行中...')
                self.prog_wrap.setVisible(True)
                return
            # 切回确定模式
            if self.prog_bar.maximum() != 100:
                self.prog_bar.setMaximum(100)
            p = max(0, min(100, int(percent)))
            self.prog_bar.setValue(p)
            self.prog_label.setText(f'{p}%')
        except Exception:
            pass

    def _progress_complete(self, ok: bool, msg: str):
        def _do():
            try:
                # 确保切回确定模式再设置数值
                if self.prog_bar.maximum() != 100:
                    self.prog_bar.setMaximum(100)
                self.prog_bar.setValue(100 if ok else 0)
                if ok:
                    self.prog_label.setText('100%')
                if not ok and msg:
                    self.status_label.setText(msg)
                # 停留片刻再隐藏，便于用户看到结束状态
                QTimer.singleShot(1200, lambda: self.prog_wrap.setVisible(False))
            except Exception:
                pass
        QTimer.singleShot(0, _do)

    def _set_status(self, text: str):
        try:
            self.status_label.setText(text or '')
        except Exception:
            pass
