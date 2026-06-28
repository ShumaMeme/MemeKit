from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QGridLayout
from PySide6.QtCore import Qt, QObject, Signal, QThread, QCoreApplication, QTimer
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QFont, QPalette
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    CardWidget,
    FluentIcon,
    ComboBox,
    PopupTeachingTip,
    FlyoutViewBase,
    BodyLabel,
    SmoothScrollArea,
    )
import os
import subprocess
import time
from typing import Optional

from app.services import adb_service
from app.components.blur_popup import show_blur_custom


class _RefreshThread(QThread):
    """后台线程：执行 ADB 设备信息采集，避免阻塞 UI。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._info = {}
        self._mode = ""
        self._serial = ""

    def run(self):
        try:
            self._info = adb_service.collect_overall_info()
        except Exception:
            self._info = {}
        self._mode = str(self._info.get("connection_status", "") or "")
        self._serial = str(self._info.get("serial", "") or "")
        if not self._mode and not self._serial:
            try:
                self._mode, self._serial = adb_service.detect_connection_mode()
            except Exception:
                self._mode, self._serial = "", ""


class StatsRingWidget(QWidget):
    def __init__(self, accent: str = "#2BC3A8", parent=None):
        super().__init__(parent)
        self._value = 0
        self._display = "--"
        self._accent = QColor(accent)
        self._track = QColor(134, 144, 156, 80)
        self._thickness = 10
        self.setMinimumSize(108, 108)
        self.setMaximumSize(132, 132)

    def setAccent(self, accent: str):
        self._accent = QColor(accent)
        self.update()

    def setValue(self, value: int, display: Optional[str] = None):
        try:
            val = int(value)
        except Exception:
            val = 0
        self._value = max(0, min(100, val))
        if display is not None:
            self._display = display or "--"
        self.update()

    def setDisplayText(self, text: str):
        self._display = text or "--"
        self.update()

    def sizeHint(self):
        return self.minimumSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(self._thickness, self._thickness, -self._thickness, -self._thickness)
        pen = QPen(self._track, self._thickness)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        if self._value > 0:
            pen.setColor(self._accent)
            painter.setPen(pen)
            angle = int((self._value / 100) * 360)
            painter.drawArc(rect, 90 * 16, -angle * 16)

        painter.setPen(self.palette().color(QPalette.WindowText))
        font = painter.font()
        font.setPointSize(18)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self._display or "--")


class DonateView(FlyoutViewBase):
    def __init__(self, img_path: str, parent=None):
        super().__init__(parent)
        vb = QVBoxLayout(self)
        vb.setContentsMargins(20, 16, 20, 16)
        vb.setSpacing(12)
        self.label = BodyLabel("感谢支持！")
        self.pic = QLabel()
        try:
            pm = QPixmap(img_path)
            if not pm.isNull():
                pm = pm.scaledToWidth(260, Qt.SmoothTransformation)
                self.pic.setPixmap(pm)
        except Exception:
            pass
        self.close_btn = PushButton("关闭")
        vb.addWidget(self.label)
        vb.addWidget(self.pic, 0, Qt.AlignCenter)
        vb.addWidget(self.close_btn, 0, Qt.AlignRight)


class _WatchTickThread(QThread):
    """后台线程：轻量级设备状态检测，避免阻塞 UI。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None

    def run(self):
        try:
            mode, serial = adb_service.detect_connection_mode()
            devs = adb_service.list_devices()
            self._state = f"{mode}:{serial}:{','.join(devs or [])}"
        except Exception:
            self._state = None


class _DriverInstallWorker(QObject):
    """后台线程：通过 ShellExecuteEx + runas 触发 UAC，监控驱动安装进程。"""
    install_finished = Signal(bool, str)

    def __init__(self, driver_path: str):
        super().__init__()
        self._driver_path = driver_path

    def run(self):
        import ctypes
        from ctypes import wintypes

        try:
            SEE_MASK_NOCLOSEPROCESS = 0x00000040
            SW_SHOWNORMAL = 1

            class SHELLEXECUTEINFOW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE),
                ]

            sei = SHELLEXECUTEINFOW()
            sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
            sei.fMask = SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb = "runas"
            sei.lpFile = self._driver_path
            sei.nShow = SW_SHOWNORMAL

            result = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
            if not result:
                error_code = ctypes.windll.kernel32.GetLastError()
                if error_code == 1223:
                    self.install_finished.emit(False, "用户取消了 UAC 授权，驱动安装未启动")
                else:
                    self.install_finished.emit(False, f"启动驱动安装程序失败 (错误码: {error_code})")
                return

            if sei.hProcess:
                INFINITE = 0xFFFFFFFF
                ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, INFINITE)

                exit_code = wintypes.DWORD()
                ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(sei.hProcess)

                if exit_code.value == 0:
                    self.install_finished.emit(True, "驱动安装程序已完成")
                else:
                    self.install_finished.emit(True, f"驱动安装程序已退出 (退出码: {exit_code.value})")
            else:
                self.install_finished.emit(True, "驱动安装程序已启动")
        except Exception as e:
            self.install_finished.emit(False, f"驱动安装失败: {e}")


class DeviceInfoTab(QWidget):
    def __init__(self):
        super().__init__()
        self._msg_boxes = []
        self._watch_timer = None
        self._watch_tick_thread = None
        self._wifi_thread = None
        self._wifi_worker = None
        self._driver_thread = None
        self._driver_worker = None
        self._last_conn_banner = None
        self._did_first_show = False
        self._loading_infobar = None

        self._init_ui()
        self._connect_signals()
        # 安全网：打包后 ADB 服务启动较慢，多次重试确保检测到设备
        QTimer.singleShot(1500, self.refresh)
        QTimer.singleShot(4000, self.refresh)
        QTimer.singleShot(8000, self.refresh)

    def _init_ui(self):
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        self.v_layout.addWidget(self.scroll)

        self.container = QWidget()
        self.container.setStyleSheet("QWidget {background: transparent;}")
        self.scroll.setWidget(self.container)

        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(16)

        self._build_hero_card()
        self._build_rings_row()
        self._build_info_grids()
        self._build_power_menu()
        self._build_action_zone()
        self.layout.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        # 每次显示时都触发一次刷新，确保从其他 TAB 切回来时数据是最新的
        QTimer.singleShot(50, self.refresh)
        if self._did_first_show:
            return
        self._did_first_show = True
        try:
            self._start_watcher()
        except Exception:
            pass

    def closeEvent(self, event):
        try:
            if hasattr(self, '_watch_timer') and self._watch_timer is not None:
                self._watch_timer.stop()
                self._watch_timer.deleteLater()
                self._watch_timer = None
        except Exception:
            pass
        try:
            if self._watch_tick_thread is not None and self._watch_tick_thread.isRunning():
                self._watch_tick_thread.quit()
                self._watch_tick_thread.wait(1500)
        except Exception:
            pass
        return super().closeEvent(event)

    def _build_hero_card(self):
        self.hero_card = CardWidget(self)
        lay = QHBoxLayout(self.hero_card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(16)

        self.hero_icon = QLabel()
        self.hero_icon.setFixedSize(56, 56)
        self.hero_icon.setText("📱")
        self.hero_icon.setStyleSheet("font-size: 44px;")
        self.hero_icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.hero_icon)

        info_lay = QVBoxLayout()
        info_lay.setSpacing(4)
        self.lbl_hero_model = QLabel("未连接设备")
        self.lbl_hero_model.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.lbl_hero_status = QLabel("状态：离线")
        self.lbl_hero_status.setStyleSheet("font-size: 14px; color: #ff4d4f; font-weight: 500;")
        self.lbl_hero_serial = QLabel("序列号：-")
        self.lbl_hero_serial.setStyleSheet("font-size: 13px; color: #86909c;")
        info_lay.addWidget(self.lbl_hero_model)
        info_lay.addWidget(self.lbl_hero_status)
        info_lay.addWidget(self.lbl_hero_serial)
        info_lay.addStretch(1)
        lay.addLayout(info_lay)

        lay.addStretch(1)

        self.btn_install_driver = PushButton(FluentIcon.DOWNLOAD, "安装驱动")
        self.btn_install_driver.setToolTip("安装 Fastboot 驱动（需要管理员权限）")
        self.btn_install_driver.clicked.connect(self._on_install_driver)
        lay.addWidget(self.btn_install_driver)

        self.layout.addWidget(self.hero_card)

    def _build_rings_row(self):
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(16)

        def _make_ring_card(title, accent, icon_str):
            card = CardWidget()
            lay = QVBoxLayout(card)
            lay.setContentsMargins(16, 16, 16, 16)
            lay.setSpacing(8)

            head_lay = QHBoxLayout()
            icon = QLabel(icon_str)
            icon.setStyleSheet("font-size:16px;")
            header = QLabel(title)
            header.setStyleSheet("font-size:15px; font-weight:bold;")
            head_lay.addWidget(icon)
            head_lay.addWidget(header)
            head_lay.addStretch(1)
            lay.addLayout(head_lay)

            ring = StatsRingWidget(accent, parent=card)
            detail = QLabel("-")
            detail.setAlignment(Qt.AlignCenter)
            detail.setStyleSheet("color:#86909c; font-size:13px; font-weight: 500;")
            lay.addWidget(ring, alignment=Qt.AlignCenter)
            lay.addWidget(detail)
            return card, ring, detail

        self.card_bat, self.ring_battery, self.lbl_bat_detail = _make_ring_card("电池电量", "#2BC3A8", "🔋")
        self.card_sto, self.ring_storage, self.lbl_sto_detail = _make_ring_card("存储空间", "#4098FF", "💾")
        self.card_mem, self.ring_memory, self.lbl_mem_detail = _make_ring_card("运行内存", "#A66BFF", "🧠")

        row_lay.addWidget(self.card_bat)
        row_lay.addWidget(self.card_sto)
        row_lay.addWidget(self.card_mem)

        self.layout.addWidget(row_w)

    def _build_info_grids(self):
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(16)

        self.info_labels = {}

        def _make_grid_card(title, icon_str, items):
            card = CardWidget()
            lay = QVBoxLayout(card)
            lay.setContentsMargins(18, 18, 18, 18)
            lay.setSpacing(12)

            head_lay = QHBoxLayout()
            icon = QLabel(icon_str)
            icon.setStyleSheet("font-size:18px;")
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet("font-size:16px; font-weight:bold;")
            head_lay.addWidget(icon)
            head_lay.addWidget(title_lbl)
            head_lay.addStretch(1)
            lay.addLayout(head_lay)

            grid = QGridLayout()
            grid.setSpacing(12)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(10)
            for i, (key, label_text) in enumerate(items):
                row = i // 2
                col = i % 2
                lbl_name = QLabel(label_text)
                lbl_name.setStyleSheet("color:#86909c; font-size:13px;")
                lbl_val = QLabel("-")
                lbl_val.setStyleSheet("font-size:14px; font-weight:500;")
                lbl_val.setWordWrap(True)
                item_lay = QVBoxLayout()
                item_lay.setSpacing(4)
                item_lay.addWidget(lbl_name)
                item_lay.addWidget(lbl_val)
                grid.addLayout(item_lay, row, col)
                self.info_labels[key] = lbl_val

            lay.addLayout(grid)
            lay.addStretch(1)
            return card

        hw_items = [
            ("brand", "品牌"), ("model", "型号"),
            ("cpu_info", "CPU处理器"), ("resolution", "屏幕分辨率"),
            ("display_density", "屏幕密度(DPI)"), ("storage_type", "存储类型"),
            ("battery_health", "电池健康度"), ("battery_cap", "电池容量")
        ]
        sys_items = [
            ("android_version", "Android版本"), ("sdk", "SDK版本"),
            ("kernel", "内核版本"), ("vndk", "VNDK版本"),
            ("bootloader_unlock", "Bootloader状态"), ("root_status", "Root权限"),
            ("current_slot", "当前A/B槽位"), ("uptime", "本次开机时间")
        ]

        self.card_hw = _make_grid_card("硬件参数", "📱", hw_items)
        self.card_sys = _make_grid_card("系统信息", "⚙️", sys_items)

        row_lay.addWidget(self.card_hw, 1)
        row_lay.addWidget(self.card_sys, 1)

        self.layout.addWidget(row_w)

    def _build_power_menu(self):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        head_lay = QHBoxLayout()
        icon = QLabel("⚡")
        icon.setStyleSheet("font-size:18px;")
        title = QLabel("重启控制")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        head_lay.addWidget(icon)
        head_lay.addWidget(title)
        head_lay.addStretch(1)
        lay.addLayout(head_lay)

        self.reboot_mode_combo = ComboBox(card)
        items = ["重启系统", "Recovery", "Bootloader", "FastbootD", "EDL (9008)"]
        try:
            self.reboot_mode_combo.addItems(items)
        except Exception:
            for item in items:
                self.reboot_mode_combo.addItem(item)
        self.reboot_mode_combo.setFixedHeight(36)

        self.btn_reboot_exec = PrimaryPushButton("执行重启")
        self.btn_reboot_exec.setIcon(FluentIcon.POWER_BUTTON)
        self.btn_reboot_exec.setFixedHeight(36)

        lay.addWidget(self.reboot_mode_combo)
        lay.addWidget(self.btn_reboot_exec)
        lay.addStretch(1)

        self.card_power = card

    def _build_action_zone(self):
        self.action_row_widget = QWidget()
        row_lay = QHBoxLayout(self.action_row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(16)

        device_card = CardWidget()
        device_lay = QVBoxLayout(device_card)
        device_lay.setContentsMargins(16, 16, 16, 16)
        device_lay.setSpacing(12)

        device_head = QHBoxLayout()
        device_icon = QLabel("📟")
        device_icon.setStyleSheet("font-size:18px;")
        device_title = QLabel("选择设备")
        device_title.setStyleSheet("font-size:15px; font-weight:bold;")
        device_head.addWidget(device_icon)
        device_head.addWidget(device_title)
        device_head.addStretch(1)
        device_lay.addLayout(device_head)

        self.device_selector = ComboBox(device_card)
        self.device_selector.setFixedHeight(36)

        self.btn_refresh = PrimaryPushButton(FluentIcon.SYNC, "刷新设备")
        self.btn_refresh.setFixedHeight(36)

        device_lay.addWidget(self.device_selector)
        device_lay.addWidget(self.btn_refresh)
        device_lay.addStretch(1)

        action_card = CardWidget()
        action_lay = QVBoxLayout(action_card)
        action_lay.setContentsMargins(16, 16, 16, 16)
        action_lay.setSpacing(12)

        action_head = QHBoxLayout()
        action_icon = QLabel("🛠️")
        action_icon.setStyleSheet("font-size:18px;")
        action_title = QLabel("常用工具")
        action_title.setStyleSheet("font-size:15px; font-weight:bold;")
        action_head.addWidget(action_icon)
        action_head.addWidget(action_title)
        action_head.addStretch(1)
        action_lay.addLayout(action_head)

        self.tool_selector = ComboBox(action_card)
        self.tool_selector.addItems(["ADB 终端", "重启 ADB 服务", "设备管理器"])
        self.tool_selector.setFixedHeight(36)

        self.btn_run_tool = PrimaryPushButton("执行工具")
        self.btn_run_tool.setIcon(FluentIcon.PLAY)
        self.btn_run_tool.setFixedHeight(36)

        action_lay.addWidget(self.tool_selector)
        action_lay.addWidget(self.btn_run_tool)
        action_lay.addStretch(1)

        row_lay.addWidget(device_card, 1)
        row_lay.addWidget(self.card_power, 1)
        row_lay.addWidget(action_card, 1)
        self.layout.addWidget(self.action_row_widget)

    def _connect_signals(self):
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_reboot_exec.clicked.connect(self._do_selected_reboot)
        self.btn_run_tool.clicked.connect(self._run_selected_tool)
        self.device_selector.currentTextChanged.connect(self._on_device_selector_changed)

    def refresh(self):
        """使用后台线程执行 ADB 设备信息采集，避免阻塞 UI。"""
        old = getattr(self, '_refresh_thread', None)
        if old is not None:
            if old.isRunning():
                return
            try:
                old.finished.disconnect(self._on_refresh_finished)
            except Exception:
                pass

        self._refresh_thread = _RefreshThread(self)
        self._refresh_thread.finished.connect(self._on_refresh_finished, Qt.QueuedConnection)
        self._refresh_thread.start()

    def _on_refresh_finished(self):
        t = self._refresh_thread
        if t is None:
            return
        if not self.isVisible():
            return
        info = t._info or {}
        mode = str(t._mode or "")
        serial = str(t._serial or "")
        status_line = str(info.get("status_line", "") or "未发现已连接设备")
        status_color = str(info.get("status_color", "") or "#86909c")
        banner_state = str(info.get("banner_state", "") or "")

        try:
            banner_key = (banner_state, mode, serial)
            last_key = getattr(self, "_last_conn_banner", None)
            last_mode = last_key[1] if isinstance(last_key, tuple) and len(last_key) >= 2 else ""
            if self._did_first_show and last_key is not None and banner_key != last_key:
                if mode in ("system", "sideload") and last_mode not in ("system", "sideload"):
                    InfoBar.success(
                        "设备已连接",
                        f"当前模式：{self._cn_connection(mode) or mode}",
                        parent=self,
                        position=InfoBarPosition.TOP,
                        duration=2200,
                        isClosable=True,
                    )
                elif mode in ("fastbootd", "bootloader", "edl", "brom"):
                    InfoBar.info(
                        "设备模式变化",
                        f"当前模式：{self._cn_connection(mode) or mode}",
                        parent=self,
                        position=InfoBarPosition.TOP,
                        duration=2200,
                        isClosable=True,
                    )
                elif mode == "offline":
                    InfoBar.warning(
                        "设备未授权",
                        "请在手机上授权 USB 调试",
                        parent=self,
                        position=InfoBarPosition.TOP,
                        duration=2600,
                        isClosable=True,
                    )
                elif mode == "none":
                    InfoBar.warning(
                        "设备已断开",
                        "未发现已连接设备",
                        parent=self,
                        position=InfoBarPosition.TOP,
                        duration=2000,
                        isClosable=True,
                    )
            self._last_conn_banner = banner_key
        except Exception:
            pass

        try:
            self._refresh_device_selector(serial)
        except Exception:
            pass

        if mode in ("system", "sideload") and serial:
            brand = str(info.get("brand", "") or "").strip()
            model = str(info.get("model", "") or "").strip()
            title = " ".join([p for p in [brand, model] if p]).strip() or "已连接设备"
        elif mode in ("fastbootd", "bootloader"):
            product = str(info.get("product", "") or "").strip()
            title = product or "Fastboot 设备"
        elif mode in ("edl", "brom"):
            title = "端口模式设备"
        elif mode == "offline":
            title = "设备未授权"
        else:
            title = "未连接设备"

        try:
            self.lbl_hero_model.setText(title)
            self.lbl_hero_status.setText(f"状态：{status_line}")
            self.lbl_hero_serial.setText(f"序列号：{serial or '-'}")
            if mode in ("system", "sideload"):
                self.lbl_hero_status.setStyleSheet("font-size: 13px; font-weight: 500; color: #00b42a;")
            elif mode in ("fastbootd", "bootloader", "edl", "brom"):
                self.lbl_hero_status.setStyleSheet("font-size: 13px; font-weight: 500; color: #fa8c16;")
            elif mode == "offline":
                self.lbl_hero_status.setStyleSheet("font-size: 13px; font-weight: 500; color: #ff4d4f;")
            else:
                self.lbl_hero_status.setStyleSheet("font-size: 13px; font-weight: 500;")
        except Exception:
            pass

        battery = str(info.get("battery", "") or "").strip()
        try:
            battery_num = int(float(battery)) if battery not in ("", "-") else 0
        except Exception:
            battery_num = 0
        try:
            self.ring_battery.setValue(battery_num, f"{battery_num}%")
            self.lbl_bat_detail.setText(
                f"健康度：{info.get('battery_health_percent') or info.get('battery_health') or '-'}"
            )
        except Exception:
            pass

        storage_percent = 0
        storage_detail = str(info.get("storage_data", "") or "-").strip()
        try:
            parts = storage_detail.split()
            if len(parts) >= 5:
                pct = parts[4].strip()
                if pct.endswith("%"):
                    storage_percent = max(0, min(100, int(pct[:-1])))
        except Exception:
            storage_percent = 0
        try:
            self.ring_storage.setValue(storage_percent, f"{storage_percent}%")
            self.lbl_sto_detail.setText(storage_detail or "-")
        except Exception:
            pass

        memory_percent = 0
        try:
            memory_percent = int(float(str(info.get("memory_percent", "0") or "0")))
        except Exception:
            memory_percent = 0
        try:
            self.ring_memory.setValue(memory_percent, f"{memory_percent}%")
            self.lbl_mem_detail.setText(str(info.get("memory_summary", "") or "-"))
        except Exception:
            pass

        label_map = {
            "brand": info.get("brand", ""),
            "model": info.get("model", ""),
            "cpu_info": info.get("cpu_info", ""),
            "resolution": info.get("resolution", ""),
            "display_density": info.get("display_density", ""),
            "storage_type": info.get("storage_type", ""),
            "battery_health": info.get("battery_health_percent", "") or info.get("battery_health", ""),
            "battery_cap": info.get("battery_full_capacity", "") or info.get("battery_rated_capacity", ""),
            "android_version": info.get("android_version", ""),
            "sdk": info.get("sdk", ""),
            "kernel": info.get("kernel", ""),
            "vndk": info.get("vndk", ""),
            "build_display": info.get("build_display", ""),
            "current_slot": info.get("current_slot", ""),
            "bootloader_unlock": info.get("bootloader_unlock", ""),
            "root_status": info.get("root_status", ""),
            "device_serial": info.get("device_serial", "") or serial,
            "uptime": info.get("uptime", ""),
        }
        for key, value in label_map.items():
            try:
                if key in self.info_labels:
                    self.info_labels[key].setText(str(value or "-"))
            except Exception:
                pass
        if getattr(self, '_pending_refresh', False):
            self._pending_refresh = False
            QTimer.singleShot(500, self.refresh)

    def _on_device_selector_changed(self, text):
        pass

    def _run_selected_tool(self):
        text = self.tool_selector.currentText()
        if text == "ADB 终端":
            self._open_adb_terminal()
        elif text == "重启 ADB 服务":
            self._restart_adb()
        elif text == "设备管理器":
            self._open_device_manager()

    def _cn_connection(self, mode: str) -> str:
        mapping = {
            "system": "系统",
            "sideload": "Sideload",
            "fastbootd": "FastbootD",
            "bootloader": "Bootloader",
            "offline": "未授权",
            "edl": "EDL",
            "brom": "BROM",
            "none": "未连接",
        }
        return mapping.get(str(mode or "").strip(), str(mode or "").strip())

    def _restart_adb(self):
        class _RestartWorker(QObject):
            finished = Signal()
            def run(self):
                try:
                    adb_service.adb_kill_server()
                    time.sleep(1)
                    adb_service.adb_start_server()
                except Exception:
                    pass
                self.finished.emit()

        try:
            InfoBar.info("提示", "正在重启 ADB 服务...", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
        except Exception:
            pass

        self._restart_thread = QThread(self)
        self._restart_worker = _RestartWorker()
        self._restart_worker.moveToThread(self._restart_thread)
        self._restart_thread.started.connect(self._restart_worker.run)
        self._restart_worker.finished.connect(self._restart_thread.quit)
        self._restart_worker.finished.connect(self._restart_worker.deleteLater)
        self._restart_thread.finished.connect(self._restart_thread.deleteLater)
        self._restart_thread.start()

    def _start_watcher(self):
        """使用 QTimer 定时轮询设备变化，避免 QThread 自定义信号在 Cython 中的兼容性问题。"""
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._on_watch_tick)
        self._watch_timer.start(2500)
        self._last_watch_state = ""
        self._watch_tick_thread = None

    def _on_watch_tick(self):
        old = self._watch_tick_thread
        if old is not None:
            if old.isRunning():
                return
            try:
                old.finished.disconnect(self._on_watch_tick_finished)
            except Exception:
                pass
        self._watch_tick_thread = _WatchTickThread(self)
        self._watch_tick_thread.finished.connect(self._on_watch_tick_finished, Qt.QueuedConnection)
        self._watch_tick_thread.start()

    def _on_watch_tick_finished(self):
        t = self._watch_tick_thread
        if t is None:
            return
        cur = t._state
        if cur is None:
            return
        if not self.isVisible():
            self._last_watch_state = cur
            return
        if cur != self._last_watch_state:
            self._last_watch_state = cur
            self.refresh()

    def _open_adb_terminal(self):
        try:
            bin_dir = None
            try:
                bin_dir = str(getattr(adb_service, 'BIN_DIR', None) or '').strip() or None
            except Exception:
                bin_dir = None
            if os.name == 'nt':
                subprocess.Popen(
                    ['cmd.exe', '/K', 'adb'],
                    cwd=bin_dir,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                subprocess.Popen(['adb'], cwd=bin_dir)
        except Exception as e:
            InfoBar.error("失败", f"无法打开终端：{e}", parent=self, position=InfoBarPosition.TOP, isClosable=True)

    def _open_device_manager(self):
        try:
            if os.name == 'nt':
                os.startfile('devmgmt.msc')
                InfoBar.success("成功", "已打开设备管理器", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
            else:
                InfoBar.warning("提示", "设备管理器仅支持 Windows 系统", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
        except Exception as e:
            InfoBar.error("错误", f"无法打开设备管理器: {e}", parent=self, position=InfoBarPosition.TOP, duration=3000, isClosable=True)

    def _refresh_device_selector_with_data(self, current_serial: str, current_mode: str, serials: list):
        """使用后台线程已采集的设备列表更新选择器，不调用任何 ADB 函数。"""
        selected = current_serial or ""
        items = []
        if selected and selected not in serials:
            items.append(f"{selected} ({self._cn_connection(current_mode) or '当前'})")
        for s in serials:
            items.append(s)
        if not items:
            items = ["未检测到设备"]

        try:
            self.device_selector.clear()
        except Exception:
            pass
        try:
            self.device_selector.addItems(items)
        except Exception:
            for item in items:
                self.device_selector.addItem(item)
        try:
            idx = 0
            if selected:
                for i, item in enumerate(items):
                    if item.startswith(selected):
                        idx = i
                        break
            self.device_selector.setCurrentIndex(idx)
        except Exception:
            pass

    def _refresh_device_selector(self, current_serial: str = ""):
        try:
            serials = adb_service.list_devices()
        except Exception:
            serials = []
        try:
            mode, detected_serial = adb_service.detect_connection_mode()
        except Exception:
            mode, detected_serial = "", ""

        selected = current_serial or detected_serial or ""
        items = []
        if selected and selected not in serials:
            items.append(f"{selected} ({self._cn_connection(mode) or '当前'})")
        for s in serials:
            items.append(s)
        if not items:
            items = ["未检测到设备"]

        try:
            self.device_selector.clear()
        except Exception:
            pass
        try:
            self.device_selector.addItems(items)
        except Exception:
            for item in items:
                self.device_selector.addItem(item)
        try:
            idx = 0
            if selected:
                for i, item in enumerate(items):
                    if item.startswith(selected):
                        idx = i
                        break
            self.device_selector.setCurrentIndex(idx)
        except Exception:
            pass

    def _do_selected_reboot(self):
        try:
            text = str(self.reboot_mode_combo.currentText() or "").strip()
        except Exception:
            text = ""
        mapping = {
            "重启系统": "system",
            "Recovery": "recovery",
            "Bootloader": "bootloader",
            "FastbootD": "fastbootd",
            "EDL (9008)": "edl",
        }
        self._do_reboot(mapping.get(text, "system"))

    def _do_reboot(self, target: str):
        class Worker(QObject):
            finished = Signal()
            def __init__(self, t: str):
                super().__init__()
                self.t = t
            def run(self):
                try:
                    adb_service.reboot_to(self.t)
                except Exception:
                    pass
                try:
                    self.finished.emit()
                except Exception:
                    pass

        try:
            InfoBar.info("提示", "重启指令已发送", parent=self, position=InfoBarPosition.TOP, duration=2000, isClosable=True)
        except Exception:
            pass

        self._thread2 = QThread(self)
        self._worker2 = Worker(target)
        self._worker2.moveToThread(self._thread2)
        self._thread2.started.connect(self._worker2.run)
        try:
            self._worker2.finished.connect(self._thread2.quit)
            self._worker2.finished.connect(self._worker2.deleteLater)
            self._thread2.finished.connect(self._thread2.deleteLater)
        except Exception:
            pass
        self._thread2.start()

    def _resolve_donate_img(self) -> str:
        try:
            app_dir = QCoreApplication.applicationDirPath()
        except Exception:
            app_dir = ''
        fname = '67a6a81e13a2d739e32d25cc76172f36.jpeg'
        cand1 = os.path.join(app_dir, 'bin', fname) if app_dir else ''
        from app import get_project_root
        cand2 = str(get_project_root() / 'bin' / fname)
        for p in (cand1, cand2):
            if p and os.path.exists(p):
                return p
        return cand2

    def _show_donate_tip(self):
        try:
            view = DonateView(self._resolve_donate_img(), self)
            tip = PopupTeachingTip(view, self.btn_donate)
            self._donate_view = view
            self._donate_tip = tip
            try:
                tip.setDuration(10000)
                view.close_btn.clicked.connect(tip.close)
            except Exception:
                pass
            tip.show()
        except Exception:
            mb = MessageBox("赞赏", "非常感谢你的支持！", self)
            show_blur_custom(self.window(), mb)

    def _on_install_driver(self):
        from app import get_project_root
        driver_path = str(get_project_root() / 'bin' / 'fastboot_driver_64.exe')
        if not os.path.exists(driver_path):
            InfoBar.error(
                "文件不存在",
                f"未找到驱动文件: {driver_path}",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            return

        if self._driver_thread is not None and self._driver_thread.isRunning():
            InfoBar.info(
                "提示", "驱动安装程序正在运行中，请稍候",
                parent=self,
                duration=2000,
                position=InfoBarPosition.TOP,
            )
            return

        self.btn_install_driver.setEnabled(False)

        InfoBar.info(
            "安装驱动",
            "正在请求管理员权限，请在弹出的 UAC 窗口中点击「是」以授权...",
            parent=self,
            duration=4000,
            position=InfoBarPosition.TOP,
        )

        self._driver_thread = QThread(self)
        self._driver_worker = _DriverInstallWorker(driver_path)
        self._driver_worker.moveToThread(self._driver_thread)

        self._driver_worker.install_finished.connect(self._on_driver_install_finished)
        self._driver_thread.started.connect(self._driver_worker.run)
        self._driver_thread.finished.connect(self._driver_thread.deleteLater)
        self._driver_thread.finished.connect(lambda: setattr(self, '_driver_thread', None))
        self._driver_thread.finished.connect(lambda: setattr(self, '_driver_worker', None))
        self._driver_thread.start()

    def _on_driver_install_finished(self, success: bool, message: str):
        self.btn_install_driver.setEnabled(True)
        if self._driver_thread is not None:
            self._driver_thread.quit()

        if success:
            InfoBar.success(
                "安装完成",
                message,
                parent=self,
                duration=5000,
                position=InfoBarPosition.TOP,
            )
        else:
            InfoBar.error(
                "安装失败",
                message,
                parent=self,
                duration=5000,
                position=InfoBarPosition.TOP,
            )

    def cleanup(self):
        try:
            if hasattr(self, '_watch_timer') and self._watch_timer is not None:
                self._watch_timer.stop()
                self._watch_timer.deleteLater()
                self._watch_timer = None
        except Exception:
            pass
        for thread_attr in (
            '_restart_thread', '_thread',
            '_refresh_thread', '_watch_tick_thread', '_thread2', '_driver_thread',
        ):
            try:
                t = getattr(self, thread_attr, None)
                if t is not None and t.isRunning():
                    t.quit()
            except Exception:
                pass
