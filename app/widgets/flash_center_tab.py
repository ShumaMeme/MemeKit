import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QThread, Signal

from PySide6.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QTextEdit, QVBoxLayout, QWidget
)
from qfluentwidgets import (
    CardWidget, CheckBox, ComboBox, FluentIcon, InfoBar, InfoBarPosition,
    LineEdit, MessageBox, PrimaryPushButton, ProgressBar, PushButton,
    SmoothScrollArea, isDarkTheme, ThemeColor,
)

from app.services import adb_service
from app.logic import SideloadFlashLogic, MiFlashLogic
from app.widgets.misc_tools.partition_flash_dialog import _PartitionFlashDialog
from app.widgets.misc_tools.payload_extract_dialog import _PayloadExtractDialog
from app.widgets.misc_tools.ops_extract_dialog import _OpsExtractDialog
from app.widgets.misc_tools.workers import resolve_bin
from app.components.blur_popup import show_blur_custom
from app.components.glass_style import apply_banner_style, refresh_banner_style


# ---------------------------------------------------------------------------
# 设备状态监听器（后台线程）
# ---------------------------------------------------------------------------
class _DeviceWatcher(QThread):
    """后台轮询设备变化（Flash菜单）。
    使用 QThread 内置 finished 信号 + 实例变量传递结果，
    彻底避免 Cython 编译后自定义 Signal 的兼容性问题。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        self._paused = False
        self._mode = ""
        self._serial = ""

    def stop(self):
        self._stop = True

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def run(self):
        from app.services import adb_service
        while not self._stop:
            try:
                if not self._paused:
                    mode, serial = adb_service.detect_connection_mode()
                    self._mode = str(mode or "")
                    self._serial = str(serial or "")
                else:
                    self._mode = ""
                    self._serial = ""
            except Exception:
                self._mode = ""
                self._serial = ""
            for _ in range(20):
                if self._stop:
                    break
                time.sleep(0.1)


class _FlashWatchTickThread(QThread):
    """后台线程：执行轻量级设备状态检测（Flash菜单专用）。"""

    def __init__(self, target_serial: str = "", parent=None):
        super().__init__(parent)
        self._state = None
        self._target_serial = str(target_serial or "")

    def run(self):
        try:
            from app.services import adb_service
            # 优先检测指定 serial 的设备状态，避免多设备时取到错误设备
            if self._target_serial:
                summary = adb_service.connection_summary(serial=self._target_serial)
                mode = summary.get("mode", "") or "none"
                serial = summary.get("serial", "") or self._target_serial
            else:
                mode, serial = adb_service.detect_connection_mode()
            self._state = f"{mode}:{serial}"
        except Exception:
            self._state = None


# ---------------------------------------------------------------------------
# 刷机工作线程
# ---------------------------------------------------------------------------
class _FlashWorker(QThread):
    log_signal = Signal(str)
    result_ready = Signal(bool, str)
    progress_signal = Signal(int, int, int)

    def __init__(self, mode: int, path: str, parent_tab=None, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.path = path
        self.parent_tab = parent_tab
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self.mode == 0:
                self._flash_sideload()
            elif self.mode == 1:
                self._flash_miflash()
        except Exception as e:
            self.log_signal.emit(f"刷机异常: {e}")
            self.result_ready.emit(False, str(e))

    def _flash_sideload(self):
        self.log_signal.emit("=" * 50)
        self.log_signal.emit("ADB Sideload 模式")
        self.log_signal.emit("=" * 50)
        try:
            logic = SideloadFlashLogic(log_callback=self.log_signal.emit)
            success = logic.flash_ota(self.path)
            if success:
                self.result_ready.emit(True, "OTA 包刷入完成")
            else:
                self.result_ready.emit(False, "OTA 包刷入失败")
        except Exception as e:
            self.log_signal.emit(f"Sideload 刷机异常: {e}")
            self.result_ready.emit(False, str(e))

    def _flash_miflash(self):
        self.log_signal.emit("=" * 50)
        self.log_signal.emit("小米线刷脚本模式")
        self.log_signal.emit("=" * 50)
        try:
            logic = MiFlashLogic(log_callback=self.log_signal.emit)
            scripts = logic.list_available_scripts(self.path)
            if scripts:
                self.log_signal.emit(f"检测到 {len(scripts)} 个脚本: {', '.join(scripts)}")
            prefer_script = None
            try:
                wipe = False
                if self.parent_tab and hasattr(self.parent_tab, 'wipe_check'):
                    wipe = bool(self.parent_tab.wipe_check.isChecked())
                prefer_script = 'flash_all.bat' if wipe else 'flash_all_except_storage.bat'
                if not (Path(self.path) / prefer_script).exists():
                    prefer_script = None
            except Exception:
                prefer_script = None
            if prefer_script:
                self.log_signal.emit(f"已根据选项选择脚本: {prefer_script}")
            success = logic.execute_flash_script(self.path, script_name=prefer_script)
            if success:
                self.result_ready.emit(True, "线刷脚本执行完成")
            else:
                self.result_ready.emit(False, "线刷脚本执行失败")
        except Exception as e:
            self.log_signal.emit(f"小米线刷异常: {e}")
            self.result_ready.emit(False, str(e))


# ---------------------------------------------------------------------------
# Flash菜单 Tab
# ---------------------------------------------------------------------------
class FlashCenterTab(QWidget):
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self._source_path: str = ""
        self._watcher_worker = None
        self._flash_worker = None
        # 当前仪表盘选中的设备 serial（多设备协同）
        self._current_serial = ""

        # 解析 adb/fastboot 路径
        adb_bin = getattr(adb_service, 'ADB_BIN', None)
        fastboot_bin = getattr(adb_service, 'FASTBOOT_BIN', None)
        self.adb_path = resolve_bin(adb_bin if adb_bin else None, 'adb')
        self.fastboot_path = resolve_bin(fastboot_bin if fastboot_bin else None, 'fastboot')

        try:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.cleanup)
        except Exception:
            pass

        self._init_ui()
        QTimer.singleShot(0, self.refresh_status)
        self._start_device_watcher()

    # ---- UI 构建 ----
    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        outer.addWidget(scroll)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(24)

        # ---- Banner ----
        self._build_banner(layout)

        # ---- 刷机模式选择 ----
        self._build_mode_card(layout)

        # ---- 设备状态 ----
        self._build_status_card(layout)

        # ---- 选项 + 工具卡片（三列） ----
        self._build_options_and_tools(layout)

        # ---- 操作按钮 ----
        self._build_action_card(layout)

        # ---- 日志区域 ----
        self._build_log_card(layout)

        # 信号连接
        self.log_signal.connect(self.log.append)

    def _build_banner(self, layout):
        banner_w = QWidget(self)
        self.banner_w = banner_w
        try:
            banner_w.setProperty("banner", "true")
            banner_w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        banner_w.setFixedHeight(110)
        apply_banner_style(banner_w)

        banner = QHBoxLayout(banner_w)
        banner.setContentsMargins(20, 20, 20, 20)
        banner.setSpacing(16)

        icon_lbl = QLabel("", banner_w)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl._fluent_icon = FluentIcon.SPEED_HIGH
        try:
            _ico = FluentIcon.SPEED_HIGH.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
            icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        title = QLabel("Flash菜单", banner_w)
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        sub = QLabel("智能一键刷写 · 分区刷入 · 固件提取", banner_w)
        sub.setStyleSheet("font-size: 13px;")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        banner.addWidget(icon_lbl)
        banner.addLayout(title_col)
        banner.addStretch(1)
        layout.addWidget(banner_w)

    def _build_mode_card(self, layout):
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(10)

        # 标题行
        h_title = QHBoxLayout()
        h_title.setSpacing(8)
        icon = QLabel("📦")
        icon.setStyleSheet("font-size:16px;")
        title = QLabel("刷写模式")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        # 模式选择 + 路径
        src_row = QHBoxLayout()
        src_row.setSpacing(10)
        self.combo_mode = ComboBox()
        self.combo_mode.addItems(["ADB Sideload", "小米线刷脚本"])
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        self.path_edit = LineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("选择 OTA 升级包 (.zip)")
        try:
            self.path_edit.setClearButtonEnabled(False)
        except Exception:
            pass

        self.btn_pick = PushButton("选择文件")
        self.btn_pick.clicked.connect(self._pick_source)

        src_row.addWidget(QLabel("模式:"))
        src_row.addWidget(self.combo_mode, 1)
        src_row.addWidget(self.path_edit, 3)
        src_row.addWidget(self.btn_pick)
        v.addLayout(src_row)

        layout.addWidget(card)

    def _build_status_card(self, layout):
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)

        h_title = QHBoxLayout()
        h_title.setSpacing(8)
        icon = QLabel("🔌")
        icon.setStyleSheet("font-size:16px;")
        title = QLabel("设备状态")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        status_row = QHBoxLayout()
        self.status_conn = QLabel("设备：未连接")
        self.status_mode = QLabel("模式：未知")
        self.status_serial = QLabel("序列号：-")
        self.status_serial.setStyleSheet("font-size:13px; color:#808080;")
        self.refresh_btn = PushButton("刷新状态")
        self.refresh_btn.clicked.connect(self._on_refresh_status_clicked)
        status_row.addWidget(self.status_conn)
        status_row.addSpacing(16)
        status_row.addWidget(self.status_mode)
        status_row.addSpacing(16)
        status_row.addWidget(self.status_serial)
        status_row.addStretch(1)
        status_row.addWidget(self.refresh_btn)
        v.addLayout(status_row)

        layout.addWidget(card)

    def set_current_serial(self, serial: str):
        """接收仪表盘广播的设备 serial，刷新设备状态显示。"""
        new_serial = str(serial or "").strip()
        if new_serial == self._current_serial:
            return
        self._current_serial = new_serial
        # 切换设备后立即刷新状态卡片
        try:
            self.refresh_status()
        except Exception:
            pass

    def _build_options_and_tools(self, layout):
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        # 左列：选项
        card_opt = CardWidget(self)
        v_opt = QVBoxLayout(card_opt)
        v_opt.setContentsMargins(16, 12, 16, 14)
        v_opt.setSpacing(10)
        h_opt = QHBoxLayout()
        h_opt.setSpacing(8)
        h_opt_icon = QLabel("⚙️")
        h_opt_icon.setStyleSheet("font-size:16px;")
        h_opt_title = QLabel("刷写设置")
        h_opt_title.setStyleSheet("font-size:15px; font-weight:600;")
        h_opt.addWidget(h_opt_icon)
        h_opt.addWidget(h_opt_title)
        h_opt.addStretch(1)
        v_opt.addLayout(h_opt)

        # 使用 qfluentwidgets CheckBox：自动显示对号、跟随主题色、毛玻璃透明
        self.wipe_check = CheckBox("清除数据（出厂重置）")
        self.wipe_check.setChecked(False)
        self.keep_root_check = CheckBox("保留 ROOT 权限")
        try:
            self.keep_root_check.setToolTip("勾选此项将跳过刷入 boot.img")
        except Exception:
            pass
        v_opt.addWidget(self.wipe_check)
        v_opt.addWidget(self.keep_root_check)
        v_opt.addStretch(1)
        grid.addWidget(card_opt, 0, 0)

        # 中列：单分区刷入
        card_part = CardWidget(self)
        v_part = QVBoxLayout(card_part)
        v_part.setContentsMargins(16, 12, 16, 14)
        v_part.setSpacing(10)
        h_part = QHBoxLayout()
        h_part.setSpacing(8)
        h_part_icon = QLabel("💾")
        h_part_icon.setStyleSheet("font-size:16px;")
        h_part_title = QLabel("单分区刷入")
        h_part_title.setStyleSheet("font-size:15px; font-weight:600;")
        h_part.addWidget(h_part_icon)
        h_part.addWidget(h_part_title)
        h_part.addStretch(1)
        v_part.addLayout(h_part)

        part_desc = QLabel("选择镜像并刷入指定分区\n（可选槽位 / 模式）")
        part_desc.setStyleSheet("font-size:12px;")
        part_desc.setWordWrap(True)
        v_part.addWidget(part_desc)

        self.btn_partition = PushButton("打开分区刷入")
        self.btn_partition.clicked.connect(self._open_partition_flash)
        v_part.addWidget(self.btn_partition)
        v_part.addStretch(1)
        grid.addWidget(card_part, 0, 1)

        # 右列：固件提取（包含 Payload.bin 和 OPS 解包）
        card_fw = CardWidget(self)
        v_fw = QVBoxLayout(card_fw)
        v_fw.setContentsMargins(16, 12, 16, 14)
        v_fw.setSpacing(10)
        h_fw = QHBoxLayout()
        h_fw.setSpacing(8)
        h_fw_icon = QLabel("📦")
        h_fw_icon.setStyleSheet("font-size:16px;")
        h_fw_title = QLabel("固件提取")
        h_fw_title.setStyleSheet("font-size:15px; font-weight:600;")
        h_fw.addWidget(h_fw_icon)
        h_fw.addWidget(h_fw_title)
        h_fw.addStretch(1)
        v_fw.addLayout(h_fw)

        fw_desc = QLabel("Payload.bin / OPS 固件解包\n支持全量和指定分区")
        fw_desc.setStyleSheet("font-size:12px;")
        fw_desc.setWordWrap(True)
        v_fw.addWidget(fw_desc)

        self.btn_payload = PushButton("打开 Payload 解包")
        self.btn_payload.clicked.connect(self._open_payload_extract)
        v_fw.addWidget(self.btn_payload)

        self.btn_ops = PushButton("打开 OPS 解包")
        self.btn_ops.clicked.connect(self._open_ops_extract)
        v_fw.addWidget(self.btn_ops)
        v_fw.addStretch(1)
        grid.addWidget(card_fw, 0, 2)

        layout.addLayout(grid)

    def _build_action_card(self, layout):
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(10)

        h_title = QHBoxLayout()
        h_title.setSpacing(8)
        icon = QLabel("▶️")
        icon.setStyleSheet("font-size:16px;")
        title = QLabel("操作")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        self.run_btn = PrimaryPushButton("开始刷写")
        self.cancel_btn = PushButton("取消刷写")
        self.save_log_btn = PushButton("清空日志窗口")
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(self.save_log_btn)
        run_row.addStretch(1)
        v.addLayout(run_row)

        self.run_btn.clicked.connect(self.start_flash)
        self.cancel_btn.clicked.connect(self.cancel)
        self.save_log_btn.clicked.connect(self.clear_log)

        layout.addWidget(card)

    def _build_log_card(self, layout):
        card = CardWidget(self)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(10)

        h_title = QHBoxLayout()
        h_title.setSpacing(8)
        icon = QLabel("📝")
        icon.setStyleSheet("font-size:16px;")
        title = QLabel("执行日志")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setContextMenuPolicy(Qt.NoContextMenu)
        self.log.setMinimumHeight(200)
        try:
            self.log.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        except Exception:
            pass
        self._refresh_log_theme()
        log_view = SmoothScrollArea(self)
        log_view.setWidget(self.log)
        log_view.setWidgetResizable(True)
        v.addWidget(log_view)

        # 进度条
        self.progress_bar = ProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        progress_text_layout = QHBoxLayout()
        self.progress_label = QLabel("当前进度：0%")
        self.progress_label.setStyleSheet("font-size:12px;")
        self.total_progress_label = QLabel("总进度：0%")
        self.total_progress_label.setStyleSheet("font-size:12px;")
        progress_text_layout.addWidget(self.progress_label)
        progress_text_layout.addSpacing(16)
        progress_text_layout.addWidget(self.total_progress_label)
        progress_text_layout.addStretch(1)

        v.addWidget(self.progress_bar)
        v.addLayout(progress_text_layout)

        layout.addWidget(card)

    # ---- 模式切换 ----
    def _on_mode_changed(self, index: int):
        if index == 0:
            self.path_edit.setPlaceholderText("选择 OTA 升级包 (.zip)")
            self.btn_pick.setText("选择文件")
        elif index == 1:
            self.path_edit.setPlaceholderText("选择线刷包目录（包含 flash_all.bat）")
            self.btn_pick.setText("选择目录")
        self.path_edit.clear()
        self._source_path = ""
        try:
            from app.services import log_service
            mode_name = self.combo_mode.currentText() if hasattr(self, 'combo_mode') else f"模式{index}"
            log_service.log_ui_action("Flash菜单-切换刷写模式", mode_name)
        except Exception:
            pass

    def _pick_source(self):
        mode = self.combo_mode.currentIndex()
        if mode == 0:
            path, _ = QFileDialog.getOpenFileName(self, "选择 OTA 包", "", "OTA 包 (*.zip);;All (*.*)")
        elif mode == 1:
            path = QFileDialog.getExistingDirectory(self, "选择小米线刷包目录")
        if path:
            self._source_path = path
            self.path_edit.setText(path)
            try:
                from app.services import log_service
                log_service.log_file_event("选择", path)
            except Exception:
                pass

    # ---- 设备监听 ----
    def _start_device_watcher(self):
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._on_watch_tick)
        try:
            from app.components.hidden_settings import get_poll_interval
            interval = get_poll_interval("flash_watcher", 3000)
        except Exception:
            interval = 3000
        self._watch_timer.start(interval)
        self._last_watch_state = ""
        self._watch_tick_thread = None

    def _on_watch_tick(self):
        if not self.isVisible():
            return
        old = self._watch_tick_thread
        if old is not None:
            if old.isRunning():
                return
            try:
                old.finished.disconnect(self._on_watch_tick_finished)
            except Exception:
                pass
        self._watch_tick_thread = _FlashWatchTickThread(self._current_serial, self)
        self._watch_tick_thread.finished.connect(self._on_watch_tick_finished, Qt.QueuedConnection)
        self._watch_tick_thread.start()

    def _on_watch_tick_finished(self):
        t = self._watch_tick_thread
        if t is None:
            return
        cur = t._state
        if cur is None:
            return
        if cur != self._last_watch_state:
            self._last_watch_state = cur
            self.refresh_status()

    def _stop_device_watcher(self):
        try:
            if hasattr(self, '_watch_timer') and self._watch_timer is not None:
                self._watch_timer.stop()
                self._watch_timer.deleteLater()
                self._watch_timer = None
        except Exception:
            pass
        # 兼容旧 _watcher_worker 清理
        if getattr(self, '_watcher_worker', None):
            try:
                self._watcher_worker.stop()
            except Exception:
                pass
            try:
                if self._watcher_worker.isRunning():
                    self._watcher_worker.quit()
                    self._watcher_worker.wait(100)
            except Exception:
                pass
            try:
                self._watcher_worker.deleteLater()
            except Exception:
                pass
            self._watcher_worker = None

    def _on_refresh_status_clicked(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-刷新状态")
        except Exception:
            pass
        self.refresh_status()

    def refresh_status(self):
        summary = adb_service.connection_summary(serial=self._current_serial)
        self.status_conn.setText(summary.get("status_conn", "设备：未连接"))
        self.status_mode.setText(summary.get("status_mode", "模式：未知"))
        # 显示当前选中设备的序列号
        try:
            cur_serial = str(summary.get("serial", "") or "").strip()
            if not cur_serial and self._current_serial:
                cur_serial = self._current_serial
            self.status_serial.setText(f"序列号：{cur_serial or '-'}")
        except Exception:
            pass

    # ---- 刷机主流程 ----
    def start_flash(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-开始刷写", self.combo_mode.currentText())
        except Exception:
            pass
        if self._flash_worker and self._flash_worker.isRunning():
            self._toast_warning("提示", "刷机正在进行中...")
            return

        mode = self.combo_mode.currentIndex()
        path = self.path_edit.text().strip()
        if not path:
            self._toast_warning("提示", "请先选择文件或目录。")
            return

        if mode == 0:
            if not os.path.isfile(path):
                self._toast_warning("提示", "选择的路径不是有效的文件。")
                return
        elif mode == 1:
            if not os.path.isdir(path):
                self._toast_warning("提示", "选择的路径不是有效的文件夹。")
                return

        # 设备模式检查（小米线刷）
        if mode == 1:
            try:
                # 优先使用仪表盘选中的设备
                if self._current_serial:
                    summary = adb_service.connection_summary(serial=self._current_serial)
                    device_mode = summary.get("mode", "")
                    serial = self._current_serial
                else:
                    device_mode, serial = adb_service.detect_connection_mode()
                if device_mode not in ['bootloader', 'fastbootd']:
                    self._toast_warning(
                        "提示",
                        "当前设备不在 Bootloader/Fastbootd 模式，线刷脚本可能会失败\n你仍然可以继续"
                    )
            except Exception:
                pass

        mode_names = ["ADB Sideload", "小米线刷脚本"]
        msg_box = MessageBox(
            "确认刷机",
            f"即将开始 {mode_names[mode]}，请确认：\n\n"
            f"📁 路径：{path}\n"
            f"\n\n⚠️ 刷机有风险，请确保已备份重要数据！\n"
            f"是否继续？",
            self
        )
        msg_box.yesButton.setText("开始刷写")
        msg_box.cancelButton.setText("取消")
        if show_blur_custom(self.window(), msg_box) != MessageBox.Accepted:
            return

        self.log.clear()
        self._set_controls_enabled(False)

        self._flash_worker = _FlashWorker(mode, path, parent_tab=self, parent=self)

        if self._watcher_worker:
            self._watcher_worker.pause()

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("当前进度：0%")
        self.total_progress_label.setText("总进度：0%")

        self._flash_worker.log_signal.connect(self.append_log)
        self._flash_worker.progress_signal.connect(self._on_progress_update)
        self._flash_worker.result_ready.connect(self._on_flash_finished)
        self._flash_worker.start()
        self.append_log("刷机线程已启动...")

    def _set_controls_enabled(self, enabled: bool):
        self.run_btn.setEnabled(enabled)
        self.combo_mode.setEnabled(enabled)
        self.path_edit.setEnabled(enabled)
        self.btn_pick.setEnabled(enabled)

    def _on_progress_update(self, current_step: int, total_steps: int, percentage: int):
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(f"当前步骤：{current_step}/{total_steps}")
        self.total_progress_label.setText(f"总进度：{percentage}%")

    def _on_flash_finished(self, success: bool, message: str):
        self.progress_bar.setVisible(False)
        if self._watcher_worker:
            self._watcher_worker.resume()
        if self._flash_worker:
            self._flash_worker.quit()
            self._flash_worker.wait(100)
            self._flash_worker.deleteLater()
            self._flash_worker = None
        self._set_controls_enabled(True)
        if success:
            self.append_log(f"\n✅ {message}")
            self._toast_success("成功", message)
            try:
                from app.services import log_service
                log_service.log_operation("刷机", success=True, detail=message)
            except Exception:
                pass
        else:
            self.append_log(f"\n❌ {message}")
            self._toast_warning("失败", message)
            try:
                from app.services import log_service
                log_service.log_operation("刷机", success=False, detail=message)
            except Exception:
                pass

    def cancel(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-取消任务")
        except Exception:
            pass
        try:
            self._set_controls_enabled(True)
        except Exception:
            pass
        self.append_log("已请求取消当前任务")

    def clear_log(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-清空日志窗口")
        except Exception:
            pass
        self.log.clear()
        self._toast_info("提示", "日志窗口已清空")

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_log_theme()

    def _refresh_log_theme(self):
        try:
            from qfluentwidgets import isDarkTheme
            dark = isDarkTheme()
        except Exception:
            dark = False
        if dark:
            self.log.setStyleSheet(
                "background-color: rgba(30, 30, 35, 0.50); color: #E6E1E5; "
                "border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 8px; padding: 10px;"
            )
        else:
            self.log.setStyleSheet(
                "background-color: rgba(255, 255, 255, 0.55); color: #1f2329; "
                "border: 1px solid rgba(42, 116, 218, 0.12); border-radius: 8px; padding: 10px;"
            )

    def refresh_theme(self):
        """主题切换时刷新所有主题依赖的样式。"""
        self._refresh_log_theme()
        if hasattr(self, 'banner_w'):
            refresh_banner_style(self.banner_w)

    def cleanup(self):
        self._stop_device_watcher()
        if self._flash_worker and self._flash_worker.isRunning():
            if self._flash_worker:
                self._flash_worker.cancel()
            self._flash_worker.quit()
            self._flash_worker.wait(100)
        if self._flash_worker:
            self._flash_worker.deleteLater()
            self._flash_worker = None
            self._flash_worker = None

    def closeEvent(self, event):
        try:
            self.cleanup()
        except Exception:
            pass
        return super().closeEvent(event)

    def append_log(self, text: str):
        try:
            from app.services import log_service
            log_service.get_logger("OPS").info(str(text))
        except Exception:
            pass
        self.log_signal.emit(text)

    # ---- 分区刷入 / Payload 处理 ----
    def _open_partition_flash(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-单分区刷入")
        except Exception:
            pass
        dlg = _PartitionFlashDialog(self.fastboot_path, self)
        show_blur_custom(self.window(), dlg)

    def _open_payload_extract(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-Payload提取")
        except Exception:
            pass
        dlg = _PayloadExtractDialog(self)
        show_blur_custom(self.window(), dlg)

    def _open_ops_extract(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("Flash菜单-OPS解包")
        except Exception:
            pass
        dlg = _OpsExtractDialog(self)
        show_blur_custom(self.window(), dlg)

    # ---- Toast 辅助 ----
    def _toast_success(self, title: str, content: str, ms: int = 2500):
        InfoBar.success(title, content, parent=self, position=InfoBarPosition.TOP, duration=ms, isClosable=True)

    def _toast_warning(self, title: str, content: str):
        try:
            InfoBar.warning(title, content, parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)
        except Exception:
            pass

    def _toast_info(self, title: str, content: str, ms: int = 2500):
        InfoBar.info(title, content, parent=self, position=InfoBarPosition.TOP, duration=ms, isClosable=True)