import os
import subprocess
import time
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDialog, QFrame
)
from pathlib import Path
from qfluentwidgets import CardWidget, PushButton as FluentPushButton, PrimaryPushButton as FluentPrimaryPushButton, FluentIcon, CheckBox, ComboBox, InfoBar, InfoBarPosition, SmoothScrollArea, BodyLabel, isDarkTheme, ThemeColor

from app import get_project_root
from app.components.blur_popup import show_blur_custom
from app.components.glass_style import apply_banner_style, refresh_banner_style


def _silent_popen_kwargs() -> dict:
    try:
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
    except Exception:
        pass
    return {}


class _DevStatusThread(QThread):
    """后台线程：执行 ADB 设备状态检测，避免阻塞 UI。

    在 run() 中调用 adb_service.list_all_devices() 和 connection_summary()，
    通过 finished 信号通知主线程，结果存储在实例属性中供主线程读取。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ok = False          # 是否成功执行（无异常）
        self._devices = []        # 设备 serial 列表
        self._serial = ""         # 第一个设备 serial
        self._summary = {}        # connection_summary 返回的字典

    def run(self):
        try:
            from app.services import adb_service
            devices = adb_service.list_all_devices()
            if not devices:
                self._ok = True
                self._devices = []
                self._serial = ""
                self._summary = {}
                return
            serial = devices[0]
            summary = adb_service.connection_summary(serial=serial)
            self._ok = True
            self._devices = list(devices)
            self._serial = str(serial or "")
            self._summary = summary if isinstance(summary, dict) else {}
        except Exception:
            self._ok = False
            self._devices = []
            self._serial = ""
            self._summary = {}


class ScrcpyTab(QWidget):
    def __init__(self):
        super().__init__()
        self._proc: subprocess.Popen | None = None
        # 录屏相关状态
        self._record_proc: subprocess.Popen | None = None
        self._record_timer: QTimer | None = None
        self._record_start_time: float = 0.0
        self._record_file: str = ""
        # 停止录制时的非阻塞轮询状态（避免 wait() 阻塞 UI 线程）
        self._record_stop_timer: QTimer | None = None
        self._record_stop_count: int = 0
        self._record_stop_unexpected: bool = False
        self._record_stop_file: str = ""
        # 设备连接状态轮询定时器（参考文件管理TAB，3秒轮询一次）
        self._dev_status_timer = QTimer(self)
        self._dev_status_timer.setInterval(3000)
        self._dev_status_timer.timeout.connect(self._update_dev_status)
        # 设备状态后台线程引用（避免被 GC，并用于重叠保护与清理）
        self._dev_status_thread = None
        self._scrcpy_path = self._resolve_scrcpy()
        self._build_ui()

    def _resolve_adb(self) -> str:
        base = get_project_root()
        bin1 = (base / "bin" / "adb.exe").resolve()
        if bin1.exists():
            return str(bin1)
        bin2 = (Path.cwd() / "bin" / "adb.exe").resolve()
        if bin2.exists():
            return str(bin2)
        return "adb"

    def _list_adb_devices(self) -> list[dict]:
        adb = self._resolve_adb()
        try:
            result = subprocess.run(
                [adb, "devices", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                **_silent_popen_kwargs(),
            )
        except Exception:
            return []

        out = (result.stdout or "").splitlines()
        devices: list[dict] = []
        for line in out:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("list of devices"):
                continue
            if line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            if state != "device":
                continue
            model = ""
            device_code = ""
            for p in parts[2:]:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
                elif p.startswith("device:"):
                    device_code = p.split(":", 1)[1]
            devices.append({"serial": serial, "model": model, "device": device_code})
        return devices

    def _select_device_serial(self) -> str | None:
        devices = self._list_adb_devices()
        if len(devices) == 0:
            InfoBar.warning("提示", "未检测到可用的 ADB 设备。", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return None
        if len(devices) == 1:
            return devices[0]["serial"]

        from app.components.dialog_styles import dialog_stylesheet

        dlg = QDialog(self)
        dlg.setWindowTitle("选择投屏设备")
        dlg.setModal(True)
        dlg.setMinimumWidth(440)
        # 保留系统标题栏，使用统一弹窗样式
        try:
            from app.components.dialog_styles import setup_dialog_window
            setup_dialog_window(dlg)
        except Exception:
            pass
        dlg.setStyleSheet(dialog_stylesheet())

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        sub = QLabel("检测到多个设备，请选择要投屏的设备：", dlg)
        sub.setStyleSheet("font-size: 13px;")
        lay.addWidget(sub)

        combo = QComboBox(dlg)
        combo.setFixedHeight(36)
        for d in devices:
            label_text = d["serial"]
            if d.get("model") or d.get("device"):
                label_text += f"  ({d.get('model') or d.get('device')})"
            combo.addItem(label_text, d["serial"])
        lay.addWidget(combo)

        lay.addSpacing(8)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        btn_cancel = FluentPushButton("取消", dlg)
        btn_cancel.setFixedHeight(36)
        btn_cancel.setMinimumWidth(80)
        btn_cancel.clicked.connect(dlg.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = FluentPrimaryPushButton("确定", dlg)
        btn_ok.setFixedHeight(36)
        btn_ok.setMinimumWidth(80)
        btn_ok.clicked.connect(dlg.accept)
        btn_layout.addWidget(btn_ok)

        lay.addLayout(btn_layout)

        if show_blur_custom(self.window(), dlg) != QDialog.Accepted:
            return None
        return combo.currentData()

    def _resolve_scrcpy(self) -> str:
        base = get_project_root()
        bin1 = (base / "bin" / "scrcpy.exe").resolve()
        if bin1.exists():
            return str(bin1)
        bin2 = (Path.cwd() / "bin" / "scrcpy.exe").resolve()
        if bin2.exists():
            return str(bin2)
        return "scrcpy"  # 退回 PATH

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

        lay = QVBoxLayout(container)
        try:
            lay.setContentsMargins(20, 20, 20, 20)
        except Exception:
            pass
        try:
            lay.setSpacing(24)
        except Exception:
            pass

        # 顶部渐变 Banner（~110px）
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
        banner = QHBoxLayout(banner_w)
        banner.setContentsMargins(20, 20, 20, 20)
        banner.setSpacing(16)
        icon_lbl = QLabel("", banner_w)
        try:
            icon_lbl.setStyleSheet("background: transparent;")
            icon_lbl.setFixedSize(48, 48)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl._fluent_icon = FluentIcon.VIDEO
            try:
                _ico = FluentIcon.VIDEO.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
                icon_lbl.setPixmap(_ico.pixmap(48, 48))
            except Exception:
                pass
        except Exception:
            pass
        title_col = QVBoxLayout(); title_col.setContentsMargins(0,0,0,0); title_col.setSpacing(4)
        title = QLabel("投屏中心", banner_w)
        try:
            title.setStyleSheet("font-size: 22px; font-weight: 600;")
        except Exception:
            pass
        sub = QLabel("scrcpy 一键投屏", banner_w)
        try:
            sub.setStyleSheet("font-size: 14px;")
        except Exception:
            pass
        title_col.addWidget(title); title_col.addWidget(sub)
        banner.addWidget(icon_lbl); banner.addLayout(title_col); banner.addStretch(1)

        # 设备连接状态显示（参考文件管理TAB）
        dev_col = QVBoxLayout()
        dev_col.setContentsMargins(0, 0, 0, 0)
        dev_col.setSpacing(2)
        dev_title = QLabel("当前设备", banner_w)
        dev_title.setStyleSheet("font-size: 12px; color: #9CA3AF;")
        dev_title.setAlignment(Qt.AlignRight)
        self.dev_label = QLabel("未检测到设备", banner_w)
        self.dev_label.setStyleSheet("font-size: 14px; font-weight: 500; color: #9CA3AF;")
        self.dev_label.setAlignment(Qt.AlignRight)
        dev_col.addWidget(dev_title)
        dev_col.addWidget(self.dev_label)
        banner.addLayout(dev_col)

        lay.addWidget(banner_w)

        main_h_layout = QHBoxLayout()
        main_h_layout.setSpacing(24)
        main_h_layout.setContentsMargins(0, 0, 0, 0)

        left_col = QVBoxLayout()
        left_col.setSpacing(24)
        left_col.setContentsMargins(0, 0, 0, 0)

        self._build_config_card(left_col)
        self._build_action_card(left_col)

        right_col = QVBoxLayout()
        right_col.setSpacing(24)
        right_col.setContentsMargins(0, 0, 0, 0)
        self._build_info_card(right_col)
        
        left_w = QWidget()
        left_w.setLayout(left_col)
        right_w = QWidget()
        right_w.setLayout(right_col)
        
        main_h_layout.addWidget(left_w, 6)
        main_h_layout.addWidget(right_w, 4)
        lay.addLayout(main_h_layout)

        self.run_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.record_start_btn.clicked.connect(self._start_record)
        self.record_stop_btn.clicked.connect(self._stop_record)

    def _build_config_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(24)
        
        head = QHBoxLayout()
        icon = QLabel("⚙️")
        icon.setStyleSheet("font-size:22px;")
        title = QLabel("投屏配置")
        title.setStyleSheet("font-size:18px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(20)

        label_style = "font-size:14px; font-weight:500;"

        def _make_field(label_text, widget):
            """将标签和控件包裹成一组，内部紧凑，控件填充剩余空间。"""
            box = QHBoxLayout()
            box.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(label_style)
            box.addWidget(lbl)
            box.addWidget(widget, 1)
            return box

        # row 0
        self.max_size_cb = ComboBox()
        self.max_size_cb.addItems(["默认", "720", "1080", "1440", "2160", "4320"])
        self.max_size_cb.setFixedHeight(44)
        grid.addLayout(_make_field("分辨率:", self.max_size_cb), 0, 0)

        self.fps_cb = ComboBox()
        self.fps_cb.addItems(["默认", "30", "60", "90", "120", "144", "165"])
        self.fps_cb.setFixedHeight(44)
        grid.addLayout(_make_field("帧率:", self.fps_cb), 0, 1)

        self.bitrate_cb = ComboBox()
        self.bitrate_cb.addItems(["默认", "4M", "6M", "8M", "12M", "20M", "30M", "50M"])
        self.bitrate_cb.setFixedHeight(44)
        grid.addLayout(_make_field("码率:", self.bitrate_cb), 0, 2)

        # row 1
        self.vbuf_cb = ComboBox()
        self.vbuf_cb.addItems(["默认", "50", "100", "150", "200", "300", "500", "1000"])
        self.vbuf_cb.setFixedHeight(44)
        grid.addLayout(_make_field("视缓冲:", self.vbuf_cb), 1, 0)

        self.abuf_cb = ComboBox()
        self.abuf_cb.addItems(["默认", "50", "100", "150", "200", "300", "500", "1000"])
        self.abuf_cb.setFixedHeight(44)
        grid.addLayout(_make_field("音缓冲:", self.abuf_cb), 1, 1)

        self.enable_audio = CheckBox("启用音频")
        self.enable_audio.setStyleSheet("font-size:14px;")
        self.enable_audio.setChecked(True)
        grid.addWidget(self.enable_audio, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        
        lay.addLayout(grid)
        
        # 行为复选框区
        behaviors_lay = QGridLayout()
        behaviors_lay.setSpacing(16)
        
        cb_style = "font-size:14px;"
        
        self.fullscreen = CheckBox("启动全屏")
        self.fullscreen.setStyleSheet(cb_style)
        self.borderless = CheckBox("无边框")
        self.borderless.setStyleSheet(cb_style)
        self.always_on_top = CheckBox("置顶显示")
        self.always_on_top.setStyleSheet(cb_style)
        self.disable_screensaver = CheckBox("禁用屏保")
        self.disable_screensaver.setStyleSheet(cb_style)
        self.stay_awake = CheckBox("保持唤醒")
        self.stay_awake.setStyleSheet(cb_style)
        self.turn_screen_off = CheckBox("息屏投屏")
        self.turn_screen_off.setStyleSheet(cb_style)
        self.show_touches = CheckBox("显示触摸")
        self.show_touches.setStyleSheet(cb_style)
        self.clip_sync = CheckBox("剪切板同步")
        self.clip_sync.setStyleSheet(cb_style)
        self.clip_sync.setChecked(True)
        self.legacy_paste = CheckBox("兼容粘贴")
        self.legacy_paste.setStyleSheet(cb_style)
        self.forward_all_clicks = CheckBox("转发所有点击")
        self.forward_all_clicks.setStyleSheet(cb_style)
        self.print_fps = CheckBox("打印FPS")
        self.print_fps.setStyleSheet(cb_style)
        
        behaviors_lay.addWidget(self.fullscreen, 0, 0)
        behaviors_lay.addWidget(self.borderless, 0, 1)
        behaviors_lay.addWidget(self.always_on_top, 0, 2)
        behaviors_lay.addWidget(self.disable_screensaver, 0, 3)
        behaviors_lay.addWidget(self.stay_awake, 1, 0)
        behaviors_lay.addWidget(self.turn_screen_off, 1, 1)
        behaviors_lay.addWidget(self.show_touches, 1, 2)
        behaviors_lay.addWidget(self.clip_sync, 1, 3)
        behaviors_lay.addWidget(self.legacy_paste, 2, 0)
        behaviors_lay.addWidget(self.forward_all_clicks, 2, 1)
        behaviors_lay.addWidget(self.print_fps, 2, 2)
        
        lay.addLayout(behaviors_lay)
        lay.addStretch(1)
        sp = card.sizePolicy()
        sp.setVerticalStretch(1)
        card.setSizePolicy(sp)
        parent_lay.addWidget(card)
        
    def _build_action_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(20)
        
        head = QHBoxLayout()
        icon = QLabel("🚀")
        icon.setStyleSheet("font-size:22px;")
        title = QLabel("操作控制")
        title.setStyleSheet("font-size:18px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(20)
        self.run_btn = FluentPrimaryPushButton(FluentIcon.PLAY, "开始投屏")
        self.run_btn.setFixedHeight(44)
        self.stop_btn = FluentPushButton(FluentIcon.PAUSE, "停止投屏")
        self.stop_btn.setFixedHeight(44)
        self.stop_btn.setEnabled(False)
        btn_lay.addWidget(self.run_btn, 1)
        btn_lay.addWidget(self.stop_btn, 1)
        lay.addLayout(btn_lay)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        try:
            sep.setStyleSheet("color: rgba(128,128,128,80);")
        except Exception:
            pass
        lay.addWidget(sep)

        # 录屏区域
        rec_head = QHBoxLayout()
        rec_icon = QLabel("●")
        rec_icon.setStyleSheet("font-size:18px; color: #d9534f;")
        rec_title = QLabel("屏幕录制")
        rec_title.setStyleSheet("font-size:16px; font-weight:bold;")
        rec_head.addWidget(rec_icon)
        rec_head.addWidget(rec_title)
        rec_head.addStretch(1)
        lay.addLayout(rec_head)

        rec_btn_lay = QHBoxLayout()
        rec_btn_lay.setSpacing(20)
        # 选择录制按钮图标：优先 VIDEO，否则回退到 CIRCLE，再不行用 None
        _rec_icon = None
        for _iname in ("VIDEO", "CIRCLE", "MICROPHONE"):
            if hasattr(FluentIcon, _iname):
                _rec_icon = getattr(FluentIcon, _iname)
                break
        if _rec_icon is not None:
            self.record_start_btn = FluentPrimaryPushButton(_rec_icon, "开始录制")
        else:
            self.record_start_btn = FluentPrimaryPushButton("开始录制")
        self.record_start_btn.setFixedHeight(44)
        # 停止录制按钮图标：优先 PAUSE，否则 STOP，再不行用 None
        _stop_icon = None
        for _iname in ("STOP", "PAUSE"):
            if hasattr(FluentIcon, _iname):
                _stop_icon = getattr(FluentIcon, _iname)
                break
        if _stop_icon is not None:
            self.record_stop_btn = FluentPushButton(_stop_icon, "停止录制")
        else:
            self.record_stop_btn = FluentPushButton("停止录制")
        self.record_stop_btn.setFixedHeight(44)
        self.record_stop_btn.setEnabled(False)
        rec_btn_lay.addWidget(self.record_start_btn, 1)
        rec_btn_lay.addWidget(self.record_stop_btn, 1)
        lay.addLayout(rec_btn_lay)

        # 录制状态标签
        self.record_status_lbl = QLabel("就绪")
        self.record_status_lbl.setProperty("class", "status_label")
        self._apply_status_label_style()
        lay.addWidget(self.record_status_lbl)
        
        parent_lay.addWidget(card)
        
    def _apply_status_label_style(self, state: str = "ready"):
        """根据主题和状态动态设置录制状态标签的字体颜色。

        state: "ready"(就绪) / "recording"(录制中) / "saving"(保存中)
        深色模式使用浅色字体，浅色模式使用深色字体，确保可读性。
        """
        try:
            from qfluentwidgets import isDarkTheme
            dark = isDarkTheme()
        except Exception:
            dark = False

        if state == "recording":
            # 录制中：红色（深色模式用亮红，浅色模式用暗红）
            color = "#FF6B6B" if dark else "#d9534f"
        elif state == "saving":
            # 保存中：橙色（深色模式用亮橙，浅色模式用暗橙）
            color = "#FFB84D" if dark else "#e0821e"
        else:
            # 就绪：灰色（深色模式用浅灰，浅色模式用深灰）
            color = "#B0B3B8" if dark else "#4e5969"

        try:
            self.record_status_lbl.setStyleSheet(
                f"font-size:14px; color:{color}; padding:6px 2px;"
            )
        except Exception:
            pass

    def _build_info_card(self, parent_lay):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(20)
        
        head = QHBoxLayout()
        icon = QLabel("�")
        icon.setStyleSheet("font-size:22px;")
        title = QLabel("使用说明")
        title.setStyleSheet("font-size:18px; font-weight:bold;")
        head.addWidget(icon)
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)
        
        content = BodyLabel(
            "1. 投屏功能基于 scrcpy 实现，支持极低延迟。\n\n"
            "2. 如果有多个设备，点击“开始投屏”时会弹出选择框。\n\n"
            "3. 推荐使用有线连接，如使用无线投屏，可在“设备管理”页先连接设备。\n\n"
            "4. 投屏窗口将以独立形式弹出，不会阻塞当前界面。\n\n"
            "5. 音频转发功能仅支持 Android 11 及以上系统。\n\n"
            "6. 若投屏黑屏，尝试降低分辨率或关闭音视频缓冲。\n\n"
            "7. 屏幕录制会按上方投屏配置（分辨率/帧率/码率/缓冲）录制，视频录制结束会自动保存到桌面（MKV 格式）。\n\n"
            "8. 点击“开始录制”时会同步打开投屏窗口，可实时查看录制画面。\n"
        )
        content.setWordWrap(True)
        _hint_color = "#9CA3AF" if isDarkTheme() else "#4e5969"
        content.setStyleSheet(f"color:{_hint_color}; font-size:14px; line-height: 1.6;")
        self._guide_content = content
        lay.addWidget(content)
        lay.addStretch(1)
        
        parent_lay.addWidget(card)

    def _build_command(self) -> list[str]:
        cmd: list[str] = [self._scrcpy_path]
        # 分辨率（默认不限制）
        ms = self.max_size_cb.currentText().strip()
        if ms and ms != "默认":
            cmd += ["--max-size", ms]
        # 帧率（最高 165）
        fps_txt = self.fps_cb.currentText().strip()
        if fps_txt and fps_txt != "默认":
            try:
                fps_val = min(int(fps_txt), 165)
                cmd += ["--max-fps", str(fps_val)]
            except Exception:
                pass
        # 码率
        br = self.bitrate_cb.currentText().strip()
        if br and br != "默认":
            cmd += ["--video-bit-rate", br]
        # 缓冲
        vbuf_txt = self.vbuf_cb.currentText().strip()
        if vbuf_txt and vbuf_txt != "默认":
            cmd += ["--video-buffer", vbuf_txt]
        abuf_txt = self.abuf_cb.currentText().strip()
        if abuf_txt and abuf_txt != "默认":
            cmd += ["--audio-buffer", abuf_txt]
        # 音频
        if not self.enable_audio.isChecked():
            cmd += ["--no-audio"]
        # 窗口/行为
        if self.fullscreen.isChecked():
            cmd += ["--fullscreen"]
        if self.borderless.isChecked():
            cmd += ["--window-borderless"]
        if self.always_on_top.isChecked():
            cmd += ["--always-on-top"]
        if self.disable_screensaver.isChecked():
            cmd += ["--disable-screensaver"]
        if self.stay_awake.isChecked():
            cmd += ["--stay-awake"]
        if self.turn_screen_off.isChecked():
            cmd += ["--turn-screen-off"]
        if self.show_touches.isChecked():
            cmd += ["--show-touches"]
        # 剪贴板与点击
        if not self.clip_sync.isChecked():
            cmd += ["--no-clipboard-autosync"]
        if self.legacy_paste.isChecked():
            cmd += ["--legacy-paste"]
        if self.forward_all_clicks.isChecked():
            cmd += ["--forward-all-clicks"]
        if self.print_fps.isChecked():
            cmd += ["--print-fps"]
        return cmd

    def _start(self):
        if self._proc and self._proc.poll() is None:
            InfoBar.info("提示", "投屏已在运行中。", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_ui_action("投屏-启动", "跳过：投屏已在运行中")
            except Exception:
                pass
            return

        # 投屏/录制互斥：若正在录制，提示先停止录制
        if self._record_proc and self._record_proc.poll() is None:
            InfoBar.warning(
                "提示",
                "正在录制屏幕，请先停止录制再开始投屏。",
                parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True
            )
            try:
                from app.services import log_service
                log_service.log_ui_action("投屏-启动", "跳过：正在录制屏幕中，投屏与录制互斥")
            except Exception:
                pass
            return

        serial = self._select_device_serial()
        if not serial:
            try:
                from app.services import log_service
                log_service.log_ui_action("投屏-启动", "取消：未选择设备或无可用设备")
            except Exception:
                pass
            return

        cmd = self._build_command()
        # Force scrcpy to use the chosen device when multiple ADB devices exist.
        if len(cmd) >= 1:
            cmd = [cmd[0], "-s", str(serial)] + cmd[1:]

        # 记录启动选项详情
        try:
            from app.services import log_service
            opts = [a for a in cmd[3:] if a and not a.startswith(str(serial))]
            log_service.log_ui_action("投屏-启动", f"设备={serial} 选项={' '.join(opts) if opts else '默认'}")
        except Exception:
            pass

        try:
            # 直接启动 scrcpy 进程，不捕获输出，让它在独立窗口运行
            self._proc = subprocess.Popen(cmd)
            InfoBar.success("成功", "scrcpy 已启动", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            # 同步禁用录制按钮（投屏运行中不能录制）
            self.record_start_btn.setEnabled(False)

            # 启动定时器监控进程状态，若 scrcpy 窗口被关闭则自动停止投屏
            self._proc_timer = QTimer(self)
            self._proc_timer.timeout.connect(self._check_proc_status)
            try:
                from app.components.hidden_settings import get_poll_interval
                interval = get_poll_interval("scrcpy_proc", 2000)
            except Exception:
                interval = 2000
            self._proc_timer.start(interval)  # 默认每 2 秒，性能模式 5 秒
        except FileNotFoundError:
            InfoBar.error("错误", "未找到 scrcpy 可执行文件", parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_operation("投屏", success=False, detail="未找到 scrcpy")
            except Exception:
                pass
        except Exception as e:
            InfoBar.error("错误", f"启动 scrcpy 失败: {e}", parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_operation("投屏", success=False, detail=str(e))
            except Exception:
                pass

    def _check_proc_status(self):
        """检查 scrcpy 进程是否仍在运行，若已退出则自动停止投屏。"""
        if self._proc and self._proc.poll() is not None:
            ret_code = self._proc.poll()
            try:
                from app.services import log_service
                log_service.log_ui_action("投屏-异常退出", f"scrcpy 进程已退出，返回码={ret_code}")
                log_service.log_operation("投屏", success=False, detail=f"进程异常退出，返回码={ret_code}")
            except Exception:
                pass
            self._stop(from_crash=True)

    def _stop(self, from_crash: bool = False):
        try:
            from app.services import log_service
            if from_crash:
                log_service.log_ui_action("投屏-停止", "进程异常退出后清理")
            else:
                log_service.log_ui_action("投屏-停止", "用户停止投屏")
                log_service.log_operation("投屏", success=True, detail="用户主动停止投屏")
        except Exception:
            pass
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                InfoBar.info("提示", "已发送停止信号", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
        except Exception as e:
            InfoBar.warning("提示", f"停止失败: {e}", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_ui_action("投屏-停止失败", f"错误: {e}")
                log_service.log_operation("投屏", success=False, detail=f"停止失败: {e}")
            except Exception:
                pass
        finally:
            self._proc = None
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            # 同步恢复录制按钮状态（投屏停止后录制按钮可用）
            self.record_start_btn.setEnabled(True)
            # 停止进程监控定时器
            if hasattr(self, '_proc_timer') and self._proc_timer:
                self._proc_timer.stop()
                self._proc_timer.deleteLater()
                self._proc_timer = None

    # ===== 屏幕录制 =====
    def _build_record_command(self, record_file: str) -> list[str]:
        """构建 scrcpy 录屏命令。复用投屏配置 + 行为选项，录制时同步显示投屏窗口。

        录制 = 投屏 + 录制文件，因此复用投屏的所有行为选项（窗口/行为），
        这样用户在录制时能看到投屏画面，且不会出现因无窗口模式导致的尺寸问题。
        """
        cmd: list[str] = [self._scrcpy_path]
        # 分辨率
        ms = self.max_size_cb.currentText().strip()
        if ms and ms != "默认":
            cmd += ["--max-size", ms]
        # 帧率
        fps_txt = self.fps_cb.currentText().strip()
        if fps_txt and fps_txt != "默认":
            try:
                fps_val = min(int(fps_txt), 165)
                cmd += ["--max-fps", str(fps_val)]
            except Exception:
                pass
        # 码率
        br = self.bitrate_cb.currentText().strip()
        if br and br != "默认":
            cmd += ["--video-bit-rate", br]
        # 缓冲
        vbuf_txt = self.vbuf_cb.currentText().strip()
        if vbuf_txt and vbuf_txt != "默认":
            cmd += ["--video-buffer", vbuf_txt]
        abuf_txt = self.abuf_cb.currentText().strip()
        if abuf_txt and abuf_txt != "默认":
            cmd += ["--audio-buffer", abuf_txt]
        # 录制核心选项：录制到文件 + 内录系统音频
        # 使用 playback 源：捕获设备音频播放（系统音频内录），且不静音设备扬声器
        # 录制时同步显示投屏窗口（不使用 --no-window），用户可实时查看录制内容
        cmd += [
            "--record=" + record_file,
            "--record-format=mkv",
            "--audio-source=playback",
        ]
        # 复用投屏行为选项（录制时同步显示投屏窗口）
        if self.fullscreen.isChecked():
            cmd += ["--fullscreen"]
        if self.borderless.isChecked():
            cmd += ["--window-borderless"]
        if self.always_on_top.isChecked():
            cmd += ["--always-on-top"]
        if self.disable_screensaver.isChecked():
            cmd += ["--disable-screensaver"]
        if self.stay_awake.isChecked():
            cmd += ["--stay-awake"]
        if self.turn_screen_off.isChecked():
            cmd += ["--turn-screen-off"]
        if self.show_touches.isChecked():
            cmd += ["--show-touches"]
        # 剪贴板与点击
        if not self.clip_sync.isChecked():
            cmd += ["--no-clipboard-autosync"]
        if self.legacy_paste.isChecked():
            cmd += ["--legacy-paste"]
        if self.forward_all_clicks.isChecked():
            cmd += ["--forward-all-clicks"]
        if self.print_fps.isChecked():
            cmd += ["--print-fps"]
        return cmd

    def _start_record(self):
        """开始录制 ADB 设备屏幕（含系统音频内录），视频自动保存到桌面。

        录制时同步打开投屏窗口，并同步更新投屏按钮状态。
        投屏与录制互斥：若正在投屏则提示先停止投屏。
        """
        if self._record_proc and self._record_proc.poll() is None:
            InfoBar.info("提示", "录制已在进行中。", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-启动", "跳过：录制已在进行中")
            except Exception:
                pass
            return

        # 投屏/录制互斥：若正在投屏，提示先停止投屏
        if self._proc and self._proc.poll() is None:
            InfoBar.warning(
                "提示",
                "正在投屏中，请先停止投屏再开始录制。",
                parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True
            )
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-启动", "跳过：正在投屏中，投屏与录制互斥")
            except Exception:
                pass
            return

        # 选择设备
        serial = self._select_device_serial()
        if not serial:
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-启动", "取消：未选择设备或无可用设备")
            except Exception:
                pass
            return

        # 生成桌面文件名（MKV 格式，抗硬终止，无需 finalization 即可播放）
        try:
            desktop_dir = Path(os.path.expanduser("~/Desktop"))
        except Exception:
            desktop_dir = Path.home()
        if not desktop_dir.exists():
            desktop_dir = Path.home()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 文件名中包含设备序列号（截短处理，去掉非法字符）
        safe_serial = "".join(c for c in str(serial) if c.isalnum() or c in "_-")[:20]
        record_file = str(desktop_dir / f"scrcpy_record_{timestamp}_{safe_serial}.mkv")

        cmd = self._build_record_command(record_file)
        # 指定设备
        cmd = [cmd[0], "-s", str(serial)] + cmd[1:]

        try:
            from app.services import log_service
            opts = [a for a in cmd[3:] if a and not a.startswith(str(serial))]
            log_service.log_ui_action("录屏-启动", f"设备={serial} 文件={record_file} 选项={' '.join(opts) if opts else '默认'}")
        except Exception:
            pass

        try:
            # 不使用 _silent_popen_kwargs()：该函数会设置 STARTF_USESHOWWINDOW
            # 且 wShowWindow 默认为 SW_HIDE(0)，会隐藏 scrcpy 的 SDL 窗口。
            # 投屏窗口需要正常显示，因此和投屏 _start() 一样用 Popen(cmd)。
            self._record_proc = subprocess.Popen(cmd)
            self._record_file = record_file
            self._record_start_time = time.time()
            InfoBar.success(
                "录制已开始",
                f"投屏窗口已同步打开，文件将保存到桌面：{os.path.basename(record_file)}",
                parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True
            )
            # 同步更新录制按钮状态
            self.record_start_btn.setEnabled(False)
            self.record_stop_btn.setEnabled(True)
            # 同步更新投屏按钮状态（录制时投屏按钮也变为"运行中"状态）
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            # 启动计时器，更新录制时长
            self._record_timer = QTimer(self)
            self._record_timer.timeout.connect(self._update_record_duration)
            self._record_timer.start(1000)
            # 启动进程监控定时器（检测投屏窗口被关闭的情况）
            self._record_proc_timer = QTimer(self)
            self._record_proc_timer.timeout.connect(self._check_record_proc_status)
            try:
                from app.components.hidden_settings import get_poll_interval
                interval = get_poll_interval("scrcpy_proc", 2000)
            except Exception:
                interval = 2000
            self._record_proc_timer.start(interval)
            # 初始更新一次
            self._update_record_duration()
        except FileNotFoundError:
            InfoBar.error("错误", "未找到 scrcpy 可执行文件", parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_operation("录屏", success=False, detail="未找到 scrcpy")
            except Exception:
                pass
        except Exception as e:
            InfoBar.error("错误", f"启动录制失败: {e}", parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)
            try:
                from app.services import log_service
                log_service.log_operation("录屏", success=False, detail=str(e))
            except Exception:
                pass

    def _check_record_proc_status(self):
        """检查录制进程是否仍在运行，若已退出则自动停止录制。"""
        if self._record_proc and self._record_proc.poll() is not None:
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-异常退出", f"scrcpy 录制进程已退出，返回码={self._record_proc.poll()}（设备可能断开）")
            except Exception:
                pass
            self._finish_record(unexpected=True)

    def _update_record_duration(self):
        """更新录制时长显示。"""
        elapsed = int(time.time() - self._record_start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        if h > 0:
            time_str = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            time_str = f"{m:02d}:{s:02d}"
        try:
            self.record_status_lbl.setText(f"● 录制中  {time_str}  →  {os.path.basename(self._record_file)}")
            self._apply_status_label_style("recording")
        except Exception:
            pass

    def _stop_record(self):
        """停止录制并保存文件。"""
        try:
            from app.services import log_service
            log_service.log_ui_action("录屏-停止录制", "用户点击停止录制按钮")
        except Exception:
            pass
        self._finish_record(unexpected=False)

    def _finish_record(self, unexpected: bool = False):
        """结束录制：发送 WM_CLOSE 优雅退出 scrcpy，非阻塞等待，完成后检查文件。

        重要：不能用 wait(timeout=N) 阻塞 UI 线程，否则点击停止录制时软件会卡住几秒。
        改用 QTimer 轮询进程状态，UI 完全不卡顿。
        """
        # 停止录制时长计时器
        if self._record_timer:
            try:
                self._record_timer.stop()
                self._record_timer.deleteLater()
            except Exception:
                pass
            self._record_timer = None

        # 停止录制进程监控定时器
        if hasattr(self, '_record_proc_timer') and self._record_proc_timer:
            try:
                self._record_proc_timer.stop()
                self._record_proc_timer.deleteLater()
            except Exception:
                pass
            self._record_proc_timer = None

        # 停止之前的轮询定时器（防止重复调用）
        if self._record_stop_timer:
            try:
                self._record_stop_timer.stop()
                self._record_stop_timer.deleteLater()
            except Exception:
                pass
            self._record_stop_timer = None

        record_file = self._record_file
        self._record_stop_file = record_file
        self._record_stop_unexpected = unexpected

        # 如果进程还在运行，发送 WM_CLOSE 优雅退出，启动非阻塞轮询
        if self._record_proc and self._record_proc.poll() is None:
            pid = self._record_proc.pid
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-停止进程", f"PID={pid} 发送 WM_CLOSE 优雅退出")
            except Exception:
                pass
            # 优雅停止：taskkill 不带 /F 发送 WM_CLOSE 给 scrcpy 窗口
            try:
                subprocess.Popen(
                    ['taskkill', '/PID', str(pid), '/T'],
                    **_silent_popen_kwargs()
                )
            except Exception:
                pass
            # 更新状态：正在保存
            try:
                self.record_status_lbl.setText("正在保存录制文件，请稍候...")
                self._apply_status_label_style("saving")
            except Exception:
                pass
            # 禁用所有按钮，防止重复操作
            try:
                self.record_start_btn.setEnabled(False)
                self.record_stop_btn.setEnabled(False)
                self.run_btn.setEnabled(False)
                self.stop_btn.setEnabled(False)
            except Exception:
                pass
            # 启动非阻塞轮询：每 200ms 检查一次进程是否退出，最多等 10 秒（50 次）
            # 200ms 间隔比 500ms 更快检测到 scrcpy 退出，减少保存等待感
            self._record_stop_count = 0
            self._record_stop_timer = QTimer(self)
            self._record_stop_timer.timeout.connect(self._check_record_stopped)
            self._record_stop_timer.start(200)
            return  # 不继续执行，等轮询检测到进程退出后再调用 _finalize_record

        # 进程已退出，直接检查文件
        self._record_proc = None
        self._finalize_record(record_file, unexpected)

    def _check_record_stopped(self):
        """QTimer 轮询回调：非阻塞检查 scrcpy 是否已优雅退出。"""
        self._record_stop_count += 1

        proc = self._record_proc
        if proc is None or proc.poll() is not None:
            # 进程已退出
            if self._record_stop_timer:
                try:
                    self._record_stop_timer.stop()
                    self._record_stop_timer.deleteLater()
                except Exception:
                    pass
                self._record_stop_timer = None
            self._record_proc = None
            self._finalize_record(self._record_stop_file, self._record_stop_unexpected)
            return

        # 超时检查：200ms * 50 = 10 秒
        if self._record_stop_count >= 50:
            if self._record_stop_timer:
                try:
                    self._record_stop_timer.stop()
                    self._record_stop_timer.deleteLater()
                except Exception:
                    pass
                self._record_stop_timer = None
            # 超时后强制终止（最后手段，可能丢失末尾数据）
            try:
                from app.services import log_service
                log_service.log_ui_action("录屏-停止进程", f"PID={proc.pid} 超时未退出，强制kill")
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            self._record_proc = None
            self._finalize_record(self._record_stop_file, self._record_stop_unexpected)

    def _finalize_record(self, record_file: str, unexpected: bool):
        """scrcpy 退出后，在主线程中检查文件并更新 UI。"""

        # 同步恢复录制按钮状态
        try:
            self.record_start_btn.setEnabled(True)
            self.record_stop_btn.setEnabled(False)
        except Exception:
            pass

        # 同步恢复投屏按钮状态（录制结束，投屏按钮恢复为可用）
        try:
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        except Exception:
            pass

        # 检查文件是否生成
        # 重要：设备断开或进程意外退出时，MKV 格式无需 finalization 即可播放，
        # 因此只要文件存在就视为已保存（不删除视频），即使文件很小也保留。
        file_exists = False
        file_size = 0
        if record_file:
            try:
                p = Path(record_file)
                if p.exists() and p.is_file():
                    file_size = p.stat().st_size
                    file_exists = True
            except Exception:
                pass

        try:
            from app.services import log_service
            # 计算录制时长
            duration_str = "未知"
            if self._record_start_time:
                try:
                    duration_sec = int(time.time() - self._record_start_time)
                    m = duration_sec // 60
                    s = duration_sec % 60
                    duration_str = f"{m:02d}:{s:02d}"
                except Exception:
                    pass
            if file_exists:
                size_kb = file_size / 1024
                if size_kb >= 1024:
                    size_str = f"{size_kb/1024:.2f} MB"
                else:
                    size_str = f"{size_kb:.1f} KB"
                log_service.log_ui_action("录屏-完成", f"文件={os.path.basename(record_file)} 大小={size_str} 时长={duration_str} 异常退出={unexpected}")
                log_service.log_operation("录屏", success=True, detail=f"文件={record_file} 大小={size_str} 时长={duration_str}")
            else:
                log_service.log_ui_action("录屏-失败", f"文件未生成: {record_file} 异常退出={unexpected}")
                log_service.log_operation("录屏", success=False, detail=f"文件未生成: {record_file}")
        except Exception:
            pass

        # 恢复状态标签
        try:
            self._apply_status_label_style("ready")
        except Exception:
            pass

        if file_exists:
            # 文件存在即保留（不删除），无论文件大小
            if unexpected:
                self.record_status_lbl.setText(f"录制已结束（设备可能断开） → {os.path.basename(record_file)}")
                InfoBar.warning(
                    "录制已结束",
                    f"设备可能已断开，视频已保存到桌面：{os.path.basename(record_file)}",
                    parent=self, position=InfoBarPosition.TOP, duration=5000, isClosable=True
                )
            else:
                self.record_status_lbl.setText(f"已保存 → {os.path.basename(record_file)}")
                InfoBar.success(
                    "录制完成",
                    f"视频已保存到桌面：{os.path.basename(record_file)}",
                    parent=self, position=InfoBarPosition.TOP, duration=4000, isClosable=True
                )
            # 尝试在文件管理器中定位文件
            try:
                if os.name == 'nt':
                    subprocess.Popen(['explorer', '/select,', record_file], **_silent_popen_kwargs())
            except Exception:
                pass
        else:
            self.record_status_lbl.setText("录制失败：未生成文件")
            InfoBar.error(
                "录制失败",
                "未生成视频文件，请检查设备连接和 scrcpy 是否支持录屏。",
                parent=self, position=InfoBarPosition.TOP, duration=4000, isClosable=True
            )

        self._record_file = ""

    def refresh_theme(self):
        """主题切换时刷新文字颜色。"""
        try:
            if hasattr(self, 'banner_w'):
                refresh_banner_style(self.banner_w)
            _hint = "#9CA3AF" if isDarkTheme() else "#4e5969"
            if hasattr(self, '_guide_content'):
                self._guide_content.setStyleSheet(f"color:{_hint}; font-size:14px; line-height: 1.6;")
        except Exception:
            pass

    def cleanup(self):
        # 记录清理时的状态（是否有投屏/录屏仍在运行）
        try:
            from app.services import log_service
            parts = []
            if self._proc and self._proc.poll() is None:
                parts.append("投屏运行中")
            if self._record_proc and self._record_proc.poll() is None:
                parts.append("录屏运行中")
            if parts:
                log_service.log_ui_action("投屏中心-清理", "退出时清理：" + "、".join(parts))
        except Exception:
            pass
        # 停止投屏进程
        try:
            if hasattr(self, '_proc') and self._proc:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3)
                    except Exception:
                        try:
                            self._proc.kill()
                        except Exception:
                            pass
        except Exception:
            pass
        # 停止投屏进程监控定时器
        try:
            if hasattr(self, '_proc_timer') and self._proc_timer:
                self._proc_timer.stop()
                self._proc_timer.deleteLater()
                self._proc_timer = None
        except Exception:
            pass
        # 停止录制进程（优雅退出，避免缓冲区数据丢失）
        try:
            if hasattr(self, '_record_proc') and self._record_proc:
                if self._record_proc.poll() is None:
                    pid = self._record_proc.pid
                    # taskkill 不带 /F 发送 WM_CLOSE，让 scrcpy 优雅退出并保存完整录制文件
                    try:
                        subprocess.Popen(
                            ['taskkill', '/PID', str(pid), '/T'],
                            **_silent_popen_kwargs()
                        )
                    except Exception:
                        pass
                    try:
                        self._record_proc.wait(timeout=5)
                    except Exception:
                        try:
                            self._record_proc.kill()
                        except Exception:
                            pass
        except Exception:
            pass
        # 停止录制计时器
        try:
            if hasattr(self, '_record_timer') and self._record_timer:
                self._record_timer.stop()
                self._record_timer.deleteLater()
                self._record_timer = None
        except Exception:
            pass
        # 停止录制进程监控定时器
        try:
            if hasattr(self, '_record_proc_timer') and self._record_proc_timer:
                self._record_proc_timer.stop()
                self._record_proc_timer.deleteLater()
                self._record_proc_timer = None
        except Exception:
            pass
        # 停止录制停止轮询定时器
        try:
            if hasattr(self, '_record_stop_timer') and self._record_stop_timer:
                self._record_stop_timer.stop()
                self._record_stop_timer.deleteLater()
                self._record_stop_timer = None
        except Exception:
            pass
        # 停止设备连接状态轮询定时器
        try:
            if hasattr(self, '_dev_status_timer') and self._dev_status_timer:
                self._dev_status_timer.stop()
        except Exception:
            pass
        # 停止设备状态后台线程（等待其完成，避免线程仍在运行时被销毁）
        try:
            t = getattr(self, '_dev_status_thread', None)
            if t is not None and t.isRunning():
                t.quit()
                t.wait(1500)
            self._dev_status_thread = None
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            self.cleanup()
        except Exception:
            pass
        return super().closeEvent(event)

    def _update_dev_status(self):
        """更新设备连接状态显示（参考文件管理TAB的 _update_dev_label）。

        使用后台线程执行 adb_service 的 ADB 调用，避免阻塞 UI。
        绿色=已连接，红色=未连接，灰色=未检测到设备。
        颜色根据深色/浅色主题动态切换，确保可读性。
        """
        # 防止线程重叠：上一轮还在跑则跳过本轮
        old = self._dev_status_thread
        if old is not None:
            if old.isRunning():
                return
            try:
                old.finished.disconnect(self._on_dev_status_finished)
            except Exception:
                pass
        self._dev_status_thread = _DevStatusThread(self)
        self._dev_status_thread.finished.connect(self._on_dev_status_finished, Qt.QueuedConnection)
        self._dev_status_thread.start()

    def _on_dev_status_finished(self):
        """后台线程完成后，在主线程更新设备状态显示。"""
        t = self._dev_status_thread
        if t is None:
            return
        _dark = isDarkTheme()
        _gray = "#9CA3AF" if _dark else "#808080"
        _green = "#23C343" if _dark else "#00b42a"
        _red = "#FF6B6B" if _dark else "#f53f3f"
        # 线程执行失败或无设备
        if not t._ok or not t._devices:
            try:
                self.dev_label.setText("未检测到设备")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_gray};")
            except Exception:
                pass
            return
        serial = t._serial
        summary = t._summary or {}
        try:
            if summary.get("connected"):
                self.dev_label.setText(f"{serial}（已连接）")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_green};")
            else:
                self.dev_label.setText(f"{serial}（未连接）")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_red};")
        except Exception:
            try:
                self.dev_label.setText("未检测到设备")
                self.dev_label.setStyleSheet(f"font-size: 14px; font-weight: 500; color: {_gray};")
            except Exception:
                pass

    def showEvent(self, event):
        """TAB 显示时启动设备状态轮询。"""
        super().showEvent(event)
        try:
            # 首次显示立即检测一次
            QTimer.singleShot(100, self._update_dev_status)
            # 启动定时轮询（3秒间隔，与文件管理TAB同步）
            if not self._dev_status_timer.isActive():
                self._dev_status_timer.start()
        except Exception:
            pass

    def hideEvent(self, event):
        """TAB 隐藏时停止设备状态轮询，节省 CPU。"""
        super().hideEvent(event)
        try:
            if self._dev_status_timer.isActive():
                self._dev_status_timer.stop()
        except Exception:
            pass
