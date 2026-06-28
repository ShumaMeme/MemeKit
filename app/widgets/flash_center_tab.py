import os
import subprocess
import webbrowser
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, QObject, QThread, Signal

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QTextEdit, QVBoxLayout, QWidget
)
from qfluentwidgets import (
    CardWidget, ComboBox, FluentIcon, InfoBar, InfoBarPosition,
    LineEdit, MessageBox, PrimaryPushButton, ProgressBar, PushButton,
    SmoothScrollArea, isDarkTheme, ThemeColor,
)

from app.services import adb_service
from app.logic import SideloadFlashLogic, MiFlashLogic
from app.widgets.misc_tools.partition_flash_dialog import _PartitionFlashDialog
from app.widgets.misc_tools.payload_extract_dialog import _PayloadExtractDialog
from app.widgets.misc_tools.workers import resolve_bin
from app.components.blur_popup import show_blur_custom


# ---------------------------------------------------------------------------
# 设备状态监听器（后台线程）
# ---------------------------------------------------------------------------
class _DeviceWatcher(QThread):
    """后台轮询设备变化（刷机中心）。
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
    """后台线程：执行轻量级设备状态检测（刷机中心专用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None

    def run(self):
        try:
            from app.services import adb_service
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

    def __init__(self, mode: int, path: str, config_path: Optional[str] = None, parent_tab=None, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.path = path
        self.config_path = config_path
        self.parent_tab = parent_tab
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self.mode == 0:
                self._flash_scattered()
            elif self.mode == 1:
                self._flash_sideload()
            elif self.mode == 2:
                self._flash_miflash()
        except Exception as e:
            self.log_signal.emit(f"刷机异常: {e}")
            self.result_ready.emit(False, str(e))

    def _flash_scattered(self):
        if not self.parent_tab:
            self.result_ready.emit(False, "内部错误：无法访问刷机逻辑")
            return
        self.log_signal.emit("散包刷机模式启动...")
        try:
            images = self.parent_tab._scan_images(self.path)
            count = len(images)
            self.log_signal.emit(f"镜像目录: {self.path}")
            self.log_signal.emit(f"扫描到 {count} 个镜像文件")
            if count == 0:
                self.result_ready.emit(False, "未找到任何 .img 镜像文件")
                return
            if not self.config_path:
                self.result_ready.emit(False, "未选择配置文件")
                return
            self.log_signal.emit(f"加载配置: {self.config_path}")
            plan = self.parent_tab._parse_config(Path(self.config_path))
            if not plan:
                self.result_ready.emit(False, "配置文件解析失败")
                return
            self.log_signal.emit(f"配置解析成功: 设备={','.join(plan.get('devices') or [])}, 步骤数={len(plan['steps'])}")
            self.parent_tab._run_flash_plan_in_thread(
                plan, self.path, self.log_signal.emit,
                progress_callback=lambda c, t, p: self.progress_signal.emit(c, t, p)
            )
            self.result_ready.emit(True, "散包刷机完成")
        except Exception as e:
            self.log_signal.emit(f"散包刷机异常: {e}")
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
# 刷机中心 Tab
# ---------------------------------------------------------------------------
class FlashCenterTab(QWidget):
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self._source_path: str = ""
        self._config_path: Optional[Path] = None
        self._images_dir: Optional[Path] = None
        self._images: Dict[str, Path] = {}
        self._watcher_worker = None
        self._watcher_worker = None
        self._flash_worker = None
        self._flash_worker = None

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
        container.setStyleSheet("QWidget {background: transparent;}")
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)

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
        banner_w.setFixedHeight(90)
        banner_w.setStyleSheet("background: transparent;")
        try:
            banner_w.setAttribute(Qt.WA_TranslucentBackground, True)
        except Exception:
            pass

        banner = QHBoxLayout(banner_w)
        banner.setContentsMargins(24, 12, 24, 12)
        banner.setSpacing(16)

        icon_lbl = QLabel("", banner_w)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignCenter)
        try:
            _ico = FluentIcon.SPEED_HIGH.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
            icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)
        title = QLabel("刷机中心", banner_w)
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        sub = QLabel("智能一键刷机 · 分区刷入 · Payload 处理", banner_w)
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
        title = QLabel("刷机模式")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        # 模式选择 + 路径
        src_row = QHBoxLayout()
        src_row.setSpacing(10)
        self.combo_mode = ComboBox()
        self.combo_mode.addItems(["散包刷机（文件夹）", "ADB Sideload", "小米线刷脚本"])
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)

        self.path_edit = LineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("选择刷机包文件夹路径")
        try:
            self.path_edit.setClearButtonEnabled(False)
        except Exception:
            pass

        self.btn_pick = PushButton("选择目录")
        self.btn_pick.clicked.connect(self._pick_source)

        self.config_edit = LineEdit()
        self.config_edit.setReadOnly(True)
        self.config_edit.setPlaceholderText("选择刷机配置脚本 (.txt)")
        self.btn_pick_config = PushButton("选择配置")
        self.btn_pick_config.clicked.connect(self._pick_config)

        src_row.addWidget(QLabel("模式:"))
        src_row.addWidget(self.combo_mode, 1)
        src_row.addWidget(self.path_edit, 3)
        src_row.addWidget(self.btn_pick)
        src_row.addSpacing(12)
        src_row.addWidget(QLabel("配置:"))
        src_row.addWidget(self.config_edit, 2)
        src_row.addWidget(self.btn_pick_config)
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
        self.refresh_btn = PushButton("刷新状态")
        self.refresh_btn.clicked.connect(self.refresh_status)
        status_row.addWidget(self.status_conn)
        status_row.addSpacing(16)
        status_row.addWidget(self.status_mode)
        status_row.addStretch(1)
        status_row.addWidget(self.refresh_btn)
        v.addLayout(status_row)

        layout.addWidget(card)

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
        h_opt_title = QLabel("刷机选项")
        h_opt_title.setStyleSheet("font-size:15px; font-weight:600;")
        h_opt.addWidget(h_opt_icon)
        h_opt.addWidget(h_opt_title)
        h_opt.addStretch(1)
        v_opt.addLayout(h_opt)

        self.wipe_check = QCheckBox("清除数据（出厂重置）")
        self.wipe_check.setChecked(False)
        self.keep_root_check = QCheckBox("保留 ROOT 权限")
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
        part_desc.setStyleSheet("font-size:12px; color:#808080;")
        part_desc.setWordWrap(True)
        v_part.addWidget(part_desc)

        self.btn_partition = PushButton("打开分区刷入")
        self.btn_partition.clicked.connect(self._open_partition_flash)
        v_part.addWidget(self.btn_partition)
        v_part.addStretch(1)
        grid.addWidget(card_part, 0, 1)

        # 右列：Payload 处理
        card_pay = CardWidget(self)
        v_pay = QVBoxLayout(card_pay)
        v_pay.setContentsMargins(16, 12, 16, 14)
        v_pay.setSpacing(10)
        h_pay = QHBoxLayout()
        h_pay.setSpacing(8)
        h_pay_icon = QLabel("🧩")
        h_pay_icon.setStyleSheet("font-size:16px;")
        h_pay_title = QLabel("Payload 处理")
        h_pay_title.setStyleSheet("font-size:15px; font-weight:600;")
        h_pay.addWidget(h_pay_icon)
        h_pay.addWidget(h_pay_title)
        h_pay.addStretch(1)
        v_pay.addLayout(h_pay)

        pay_desc = QLabel("提取 payload.bin 镜像\n支持全量和指定分区")
        pay_desc.setStyleSheet("font-size:12px; color:#808080;")
        pay_desc.setWordWrap(True)
        v_pay.addWidget(pay_desc)

        self.btn_payload = PushButton("打开 Payload 处理")
        self.btn_payload.clicked.connect(self._open_payload_extract)
        v_pay.addWidget(self.btn_payload)
        v_pay.addStretch(1)
        grid.addWidget(card_pay, 0, 2)

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
        self.run_btn = PrimaryPushButton("开始刷机")
        self.cancel_btn = PushButton("取消刷机")
        self.save_log_btn = PushButton("清空日志窗口")
        self.btn_cfg_repo = PushButton("配置文件仓库")
        self.btn_cfg_repo.clicked.connect(self._open_cfg_repo)
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(self.save_log_btn)
        run_row.addStretch(1)
        run_row.addWidget(self.btn_cfg_repo)
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
        title = QLabel("刷机日志")
        title.setStyleSheet("font-size:15px; font-weight:600;")
        h_title.addWidget(icon)
        h_title.addWidget(title)
        h_title.addStretch(1)
        v.addLayout(h_title)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(200)
        try:
            self.log.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            try:
                from qfluentwidgets import isDarkTheme
                dark = isDarkTheme()
            except Exception:
                dark = False
            if dark:
                self.log.setStyleSheet("background: transparent;")
            else:
                self.log.setStyleSheet("background-color: #F5F3FF; color: #1f2329; border: 1px solid #DDD6FE; border-radius: 8px; padding: 10px;")
        except Exception:
            pass
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
            self.path_edit.setPlaceholderText("选择刷机包文件夹路径")
            self.btn_pick.setText("选择目录")
        elif index == 1:
            self.path_edit.setPlaceholderText("选择 OTA 升级包 (.zip)")
            self.btn_pick.setText("选择文件")
        elif index == 2:
            self.path_edit.setPlaceholderText("选择线刷包目录（包含 flash_all.bat）")
            self.btn_pick.setText("选择目录")
        self.path_edit.clear()
        self._source_path = ""

    def _pick_source(self):
        mode = self.combo_mode.currentIndex()
        if mode == 0:
            path = QFileDialog.getExistingDirectory(self, "选择刷机包目录")
        elif mode == 1:
            path, _ = QFileDialog.getOpenFileName(self, "选择 OTA 包", "", "OTA 包 (*.zip);;All (*.*)")
        elif mode == 2:
            path = QFileDialog.getExistingDirectory(self, "选择小米线刷包目录")
        if path:
            self._source_path = path
            self.path_edit.setText(path)

    def _pick_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择刷机配置脚本", "", "配置脚本 (*.txt);;所有文件 (*.*)")
        if path:
            self._config_path = Path(path)
            self.config_edit.setText(path)
            self.append_log(f"已选择配置文件: {path}")

    def _open_cfg_repo(self):
        url = "https://gitee.com/gyah/Tobatools-config-file"
        try:
            webbrowser.open(url)
        except Exception:
            self._toast_warning("打开失败", "无法打开链接，请手动复制到浏览器访问")

    # ---- 设备监听 ----
    def _start_device_watcher(self):
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._on_watch_tick)
        self._watch_timer.start(3000)
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
        self._watch_tick_thread = _FlashWatchTickThread(self)
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

    def refresh_status(self):
        summary = adb_service.connection_summary()
        self.status_conn.setText(summary.get("status_conn", "设备：未连接"))
        self.status_mode.setText(summary.get("status_mode", "模式：未知"))

    # ---- 刷机主流程 ----
    def start_flash(self):
        if self._flash_worker and self._flash_worker.isRunning():
            self._toast_warning("提示", "刷机正在进行中...")
            return

        mode = self.combo_mode.currentIndex()
        path = self.path_edit.text().strip()
        if not path:
            self._toast_warning("提示", "请先选择文件或目录。")
            return

        if mode in [0, 2]:
            if not os.path.isdir(path):
                self._toast_warning("提示", "选择的路径不是有效的文件夹。")
                return
        elif mode == 1:
            if not os.path.isfile(path):
                self._toast_warning("提示", "选择的路径不是有效的文件。")
                return

        config_path = None
        if mode == 0:
            if not self._config_path:
                self._toast_warning("提示", "请先选择刷机配置文件！")
                return
            config_path = str(self._config_path)

        # 设备模式检查
        if mode == 0:
            device_mode, serial = adb_service.detect_connection_mode()
            if device_mode not in ['bootloader', 'fastbootd']:
                self._toast_warning(
                    "提示",
                    "设备不在 Bootloader/Fastbootd 模式，无法开始刷机\n请先重启到 fastboot / fastbootd"
                )
                return
        elif mode == 2:
            try:
                device_mode, serial = adb_service.detect_connection_mode()
                if device_mode not in ['bootloader', 'fastbootd']:
                    self._toast_warning(
                        "提示",
                        "当前设备不在 Bootloader/Fastbootd 模式，线刷脚本可能会失败\n你仍然可以继续"
                    )
            except Exception:
                pass

        mode_names = ["散包刷机", "ADB Sideload", "小米线刷脚本"]
        msg_box = MessageBox(
            "确认刷机",
            f"即将开始 {mode_names[mode]}，请确认：\n\n"
            f"📁 路径：{path}\n"
            f"{f'📄 配置：{config_path}' if config_path else ''}"
            f"\n\n⚠️ 刷机有风险，请确保已备份重要数据！\n"
            f"是否继续？",
            self
        )
        msg_box.yesButton.setText("开始刷机")
        msg_box.cancelButton.setText("取消")
        if show_blur_custom(self.window(), msg_box) != MessageBox.Accepted:
            return

        self.log.clear()
        self._set_controls_enabled(False)

        self._flash_worker = _FlashWorker(mode, path, config_path, parent_tab=self, parent=self)

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
        self.btn_pick_config.setEnabled(enabled)
        self.config_edit.setEnabled(enabled)

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
            self._flash_worker = None
        self._set_controls_enabled(True)
        if success:
            self.append_log(f"\n✅ {message}")
            self._toast_success("成功", message)
        else:
            self.append_log(f"\n❌ {message}")
            self._toast_warning("失败", message)

    def cancel(self):
        try:
            self._set_controls_enabled(True)
        except Exception:
            pass
        self.append_log("已请求取消当前任务")

    def clear_log(self):
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
            self.log.setStyleSheet("background: transparent; color: #E6E1E5;")
        else:
            self.log.setStyleSheet("background-color: #F5F3FF; color: #1f2329; border: 1px solid #DDD6FE; border-radius: 8px; padding: 10px;")

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
        self.log_signal.emit(text)

    # ---- 分区刷入 / Payload 处理 ----
    def _open_partition_flash(self):
        dlg = _PartitionFlashDialog(self.fastboot_path, self)
        show_blur_custom(self.window(), dlg)

    def _open_payload_extract(self):
        dlg = _PayloadExtractDialog(self)
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

    # ---- 子进程辅助 ----
    def _popen_kwargs_silent(self) -> dict:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
        return {}

    def _resolve_fastboot(self) -> str:
        fb = adb_service.FASTBOOT_BIN
        if fb and fb.exists():
            return str(fb)
        return self.fastboot_path or 'fastboot'

    def _run_fastboot(self, args: List[str], desc: str = "") -> tuple:
        fb = self._resolve_fastboot()
        cmd = [fb] + args
        try:
            if desc:
                self.append_log(f"执行: {desc}")
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', timeout=120,
                **self._popen_kwargs_silent()
            )
            output = result.stdout.strip()
            if output:
                for line in output.split('\n'):
                    if line.strip():
                        self.append_log(line.strip())
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            self.append_log(f"超时: {desc}")
            return False, ""
        except Exception as e:
            self.append_log(f"执行失败: {e}")
            return False, ""

    # ---- 散包刷机核心逻辑 ----
    def _scan_images(self, folder: str) -> Dict[str, Path]:
        images: Dict[str, Path] = {}
        try:
            for p in Path(folder).glob('*.img'):
                images[p.name.lower()] = p
        except Exception:
            pass
        return images

    def _parse_config(self, config_path: Path) -> Optional[dict]:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            devices: List[str] = []
            steps = []
            current_mode = None
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('device:'):
                    v = line.split(':', 1)[1].strip()
                    if v:
                        devices.append(v)
                    continue
                if line == 'bootloader':
                    current_mode = 'bootloader'
                    steps.append({'type': 'mode', 'mode': 'bootloader'})
                    continue
                if line == 'fastbootd':
                    current_mode = 'fastbootd'
                    steps.append({'type': 'mode', 'mode': 'fastbootd'})
                    continue
                if line == 'system':
                    steps.append({'type': 'reboot', 'target': 'system'})
                    continue
                if line == 'set-a':
                    steps.append({'type': 'set_slot', 'slot': 'a'})
                    continue
                if line == 'set-b':
                    steps.append({'type': 'set_slot', 'slot': 'b'})
                    continue
                if line == 'wipe-data':
                    continue
                if line.startswith('-'):
                    line = line[1:]
                    parts = line.split()
                    if not parts:
                        continue
                    partition = parts[0]
                    if len(parts) > 1:
                        if parts[1] == 'disable':
                            steps.append({'type': 'flash', 'partition': partition, 'disable_avb': True, 'mode': current_mode})
                        elif parts[1] == 'del':
                            steps.append({'type': 'delete_logical', 'partition': partition, 'mode': current_mode})
                        elif parts[1] == 'add' and len(parts) > 2:
                            steps.append({'type': 'create_logical', 'partition': partition, 'size': parts[2], 'mode': current_mode})
                    else:
                        steps.append({'type': 'flash', 'partition': partition, 'disable_avb': False, 'mode': current_mode})
            if not devices:
                self.append_log("错误: 配置文件缺少 device: 字段")
                return None
            return {'devices': devices, 'steps': steps}
        except Exception as e:
            self.append_log(f"解析配置文件失败: {e}")
            return None

    def _run_flash_plan_in_thread(self, plan: dict, images_dir: str, log_func, progress_callback=None):
        self._images_dir = Path(images_dir)
        self._images = self._scan_images(images_dir)
        log_func("=" * 50)
        log_func("开始执行刷机计划")
        log_func("=" * 50)
        total_steps = len(plan['steps'])

        fb = self._resolve_fastboot()
        try:
            result = subprocess.run(
                [fb, 'getvar', 'product'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=5, **self._popen_kwargs_silent()
            )
            output = result.stdout.lower()
            device_product = ""
            for line in output.split('\n'):
                if 'product:' in line:
                    device_product = line.split(':', 1)[-1].strip()
                    break
            expected_devices = [d.strip() for d in (plan.get('devices') or []) if d and d.strip()]
            if not expected_devices:
                raise Exception("配置文件缺少 device: 字段")
            ok = any(d.lower() in device_product for d in expected_devices)
            if not ok:
                raise Exception(f"设备型号不匹配：期望任一 {expected_devices}, 实际 {device_product}")
            log_func(f"设备验证成功: {device_product} (命中: {expected_devices})")
        except Exception as e:
            log_func(f"❌ 设备验证失败: {e}")
            raise

        for i, step in enumerate(plan['steps'], 1):
            step_type = step['type']
            if progress_callback:
                percentage = int((i / total_steps) * 100)
                progress_callback(i, total_steps, percentage)

            if step_type == 'mode':
                target_mode = step['mode']
                log_func(f"切换到 {target_mode} 模式")
                try:
                    result = subprocess.run(
                        [fb, 'getvar', 'is-userspace'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=5, **self._popen_kwargs_silent()
                    )
                    current_mode = 'fastbootd' if 'yes' in result.stdout.lower() else 'bootloader'
                except Exception:
                    current_mode = 'unknown'
                if current_mode == target_mode:
                    log_func(f"  已在 {target_mode} 模式")
                    continue
                if target_mode == 'fastbootd':
                    log_func("  正在重启到 fastbootd...")
                    try:
                        subprocess.run([fb, 'reboot', 'fastboot'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       timeout=10, **self._popen_kwargs_silent())
                    except subprocess.TimeoutExpired:
                        log_func("  设备正在重启...")
                    except Exception as e:
                        log_func(f"  重启命令执行异常: {e}")
                    wait_seconds = 15
                    for remaining in range(wait_seconds, 0, -1):
                        log_func(f"  等待设备重启... {remaining} 秒")
                        time.sleep(1)
                    log_func("  ✅ 已切换到 fastbootd 模式")
                elif target_mode == 'bootloader':
                    log_func("  正在重启到 bootloader...")
                    try:
                        subprocess.run([fb, 'reboot-bootloader'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       timeout=10, **self._popen_kwargs_silent())
                    except subprocess.TimeoutExpired:
                        log_func("  设备正在重启...")
                    except Exception as e:
                        log_func(f"  重启命令执行异常: {e}")
                    wait_seconds = 10
                    for remaining in range(wait_seconds, 0, -1):
                        log_func(f"  等待设备重启... {remaining} 秒")
                        time.sleep(1)
                    log_func("  ✅ 已切换到 bootloader 模式")

            elif step_type == 'flash':
                partition = step['partition']
                disable_avb = step.get('disable_avb', False)
                log_func(f"刷写 {partition}")

                if partition.endswith('_ab'):
                    is_ab = True
                    base_partition = partition[:-3]
                elif partition.endswith('_a') or partition.endswith('_b'):
                    is_ab = False
                    base_partition = partition[:-2]
                else:
                    is_ab = False
                    base_partition = partition

                img_name = f"{base_partition}.img"
                img_path = self._images.get(img_name.lower())
                if not img_path:
                    log_func(f"警告: 未找到 {img_name}，跳过")
                    continue

                if is_ab:
                    for slot in ['a', 'b']:
                        slot_partition = f"{base_partition}_{slot}"
                        cmd = [fb, 'flash', slot_partition, str(img_path)]
                        if disable_avb:
                            cmd.extend(['--disable-verity', '--disable-verification'])
                        try:
                            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                    text=True, timeout=120, **self._popen_kwargs_silent())
                            out = result.stdout.strip()
                            if out:
                                for line in out.split('\n'):
                                    if line.strip():
                                        log_func(line.strip())
                            if result.returncode != 0:
                                raise Exception(f"刷写 {slot_partition} 失败")
                        except Exception as e:
                            log_func(f"❌ {e}")
                            raise
                else:
                    cmd = [fb, 'flash', partition, str(img_path)]
                    if disable_avb:
                        cmd.extend(['--disable-verity', '--disable-verification'])
                    try:
                        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                text=True, timeout=120, **self._popen_kwargs_silent())
                        out = result.stdout.strip()
                        if out:
                            for line in out.split('\n'):
                                if line.strip():
                                    log_func(line.strip())
                        if result.returncode != 0:
                            raise Exception(f"刷写 {partition} 失败")
                    except Exception as e:
                        log_func(f"❌ {e}")
                        raise

            elif step_type == 'delete_logical':
                partition = step['partition']
                targets = [partition, f"{partition}_a", f"{partition}_b", f"{partition}_a-cow", f"{partition}_b-cow"]
                log_func(f"删除逻辑分区: {partition}")
                for target in targets:
                    try:
                        result = subprocess.run(
                            [fb, 'delete-logical-partition', target],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=30, **self._popen_kwargs_silent()
                        )
                        out = result.stdout.strip()
                        if out and ('not find' not in out.lower() and 'not exist' not in out.lower()):
                            log_func(out)
                    except Exception:
                        pass

            elif step_type == 'create_logical':
                partition = step['partition']
                size = step['size']
                log_func(f"创建逻辑分区: {partition} ({size})")
                try:
                    result = subprocess.run(
                        [fb, 'create-logical-partition', partition, size],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=30, **self._popen_kwargs_silent()
                    )
                    out = result.stdout.strip()
                    if out:
                        log_func(out)
                    if result.returncode != 0:
                        raise Exception(f"创建 {partition} 失败")
                except Exception as e:
                    log_func(f"❌ {e}")
                    raise

            elif step_type == 'set_slot':
                slot = step['slot']
                log_func(f"设置活动槽位: {slot}")
                try:
                    result = subprocess.run(
                        [fb, 'set_active', slot],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=30, **self._popen_kwargs_silent()
                    )
                    out = result.stdout.strip()
                    if out:
                        log_func(out)
                    log_func(f"活动槽位已设置为: {slot}")
                except Exception as e:
                    log_func(f"警告: 设置活动槽位失败: {e}")