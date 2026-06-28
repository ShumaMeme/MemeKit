import os
import subprocess
from typing import Optional

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QLabel,
    QFileDialog,
    QCheckBox,
)
from app.components.log_widget import LogWidget

from qfluentwidgets import (
    CardWidget,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
    CaptionLabel,
    ComboBox,
    SmoothScrollArea,
    LineEdit,
    isDarkTheme,
)

from app.widgets.misc_tools.workers import FlashPartitionWorker
from app.components.blur_popup import show_blur_info


class _PartitionFlashDialog(QDialog):
    def __init__(self, fastboot_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("单分区刷入")
        self.fastboot_path = fastboot_path
        self._worker: Optional[QThread] = None

        layout = QVBoxLayout(self)
        try:
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)
        except Exception:
            pass

        header = QVBoxLayout()
        try:
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(2)
        except Exception:
            pass
        header.addWidget(TitleLabel("单分区刷入", self))
        header.addWidget(CaptionLabel("手动填写分区名并选择镜像刷入（可选槽位 / 模式）", self))
        layout.addLayout(header)

        card_params = CardWidget(self)
        v_params = QVBoxLayout(card_params)
        try:
            v_params.setContentsMargins(16, 16, 16, 16)
            v_params.setSpacing(10)
        except Exception:
            pass

        row1 = QHBoxLayout()
        self.part_edit = LineEdit(self)
        self.part_edit.setPlaceholderText("手动输入分区名，例如：boot / vendor_boot / system")
        self.slot_combo = ComboBox(self)
        self.slot_combo.addItems(["不指定", "_a", "_b"])
        self.mode_combo = ComboBox(self)
        self.mode_combo.addItems(["bootloader", "fastbootd"])
        self.auto_switch = QCheckBox("自动切换模式")
        self.auto_switch.setChecked(True)
        row1.addWidget(QLabel("分区"))
        row1.addWidget(self.part_edit)
        row1.addWidget(QLabel("槽位"))
        row1.addWidget(self.slot_combo)
        row1.addWidget(QLabel("目标模式"))
        row1.addWidget(self.mode_combo)
        row1.addWidget(self.auto_switch)
        v_params.addLayout(row1)

        row2 = QHBoxLayout()
        self.img_edit = LineEdit()
        self.img_edit.setPlaceholderText("选择要刷入的 .img 文件")
        btn_pick = PushButton("选择镜像")
        btn_pick.clicked.connect(self._pick_img)
        self.run_btn = PrimaryPushButton("刷入分区")
        self.run_btn.clicked.connect(self._flash_partition)
        row2.addWidget(QLabel("镜像"))
        row2.addWidget(self.img_edit)
        row2.addWidget(btn_pick)
        row2.addStretch(1)
        row2.addWidget(self.run_btn)
        v_params.addLayout(row2)

        layout.addWidget(card_params)

        card_log = CardWidget(self)
        v_log = QVBoxLayout(card_log)
        try:
            v_log.setContentsMargins(16, 16, 16, 16)
            v_log.setSpacing(10)
        except Exception:
            pass
        v_log.addWidget(QLabel("输出日志", self))

        self.out = LogWidget()
        v_log.addWidget(self.out)

        layout.addWidget(card_log)

        self.refresh_theme()

    def refresh_theme(self):
        """主题切换时刷新内部组件的样式。"""
        if isDarkTheme():
            self.setStyleSheet("")
        else:
            self.setStyleSheet("QDialog { background-color: #F0E6F6; }")
        try:
            if hasattr(self.out, "refresh_theme"):
                self.out.refresh_theme()
        except Exception:
            pass

    def _pick_img(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择镜像", "", "镜像 (*.img);;所有文件 (*.*)")
        if path:
            self.img_edit.setText(path)

    def _flash_partition(self):
        img = self.img_edit.text().strip()
        part = self.part_edit.text().strip()
        if not img or not os.path.isfile(img):
            show_blur_info(self.window(), "提示", "请选择有效的镜像文件")
            return
        if not part:
            show_blur_info(self.window(), "提示", "请输入分区名")
            return
        if any(c.isspace() for c in part):
            show_blur_info(self.window(), "提示", "分区名不能包含空格")
            return

        slot = self.slot_combo.currentText()
        final_part = part
        if slot != "不指定":
            if not (final_part.endswith('_a') or final_part.endswith('_b')):
                final_part = final_part + slot
        target_mode = self.mode_combo.currentText()
        auto_switch = self.auto_switch.isChecked()

        # 检测当前设备模式
        current_mode, serial = self._detect_mode()
        self.out.append_log(f"当前设备模式: {current_mode}" + (f" ({serial})" if serial else ""))

        # 无设备连接：禁止执行
        if current_mode == "none":
            show_blur_info(
                self.window(), "未检测到设备",
                "未检测到已连接的 Fastboot 设备。\n\n"
                "请确保手机已进入 Fastboot 或 Bootloader 模式，\n"
                "并正确连接 USB 数据线后重试。"
            )
            return

        # 如果自动切换关闭，检查模式是否匹配
        if not auto_switch:
            if current_mode not in (target_mode, "fastbootd" if target_mode == "bootloader" else target_mode):
                show_blur_info(
                    self.window(), "模式不匹配",
                    f"设备当前处于 {current_mode} 模式，\n"
                    f"但需要 {target_mode} 模式。\n\n"
                    "请勾选「自动切换模式」或手动将设备切换到对应模式后重试。"
                )
                return

        cmd = [self.fastboot_path, 'flash', final_part, img]
        self.out.append_log(f"执行: {' '.join(cmd)}")

        # 全部操作（模式切换 + 刷写）都在后台线程完成，UI 不会卡顿
        self._run_proc(cmd, target_mode, auto_switch)

    def _detect_mode(self):
        """检测当前 Fastboot 设备模式。"""
        try:
            proc = subprocess.run(
                [self.fastboot_path, "devices"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            out = proc.stdout or ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    if serial.lower().startswith("(bootloader)"):
                        continue
                    # 判断是 bootloader 还是 fastbootd
                    try:
                        p = subprocess.run(
                            [self.fastboot_path, "-s", serial, "getvar", "is-userspace"],
                            capture_output=True, text=True, timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        )
                        info = (p.stdout or "") + (p.stderr or "")
                        if "yes" in info.lower():
                            return "fastbootd", serial
                    except Exception:
                        pass
                    return "bootloader", serial
        except Exception:
            pass
        return "none", ""

    def _run_proc(self, cmd, target_mode, auto_switch):
        if self._worker and self._worker.isRunning():
            show_blur_info(self.window(), "提示", "已有任务在执行中")
            return

        # 不设置 parent，避免 dialog 销毁时 Qt 自动清理与 deleteLater 冲突
        self._worker = FlashPartitionWorker(
            fastboot_path=self.fastboot_path,
            target_mode=target_mode,
            flash_cmd=cmd,
            auto_switch=auto_switch,
            parent=self,
        )
        self._worker.output.connect(self.out.append_log, Qt.QueuedConnection)
        self._worker.step_start.connect(self.out.start_step, Qt.QueuedConnection)
        self._worker.step_finish.connect(self.out.finish_step, Qt.QueuedConnection)
        self._worker.result_ready.connect(self._on_finished, Qt.QueuedConnection)
        self._worker.start()

    def _on_finished(self, code: int):
        """在主线程中安全清理 worker（线程已完成，wait 立即返回）。"""
        try:
            if self._worker:
                self._worker.quit()
                self._worker.wait(100)
                self._worker.deleteLater()
                self._worker = None
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭对话框时安全终止后台线程（防止闪退）。"""
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass
        try:
            if self._worker and self._worker.isRunning():
                self._worker.quit()
                if not self._worker.wait(100):
                    self._worker.terminate()
                    self._worker.wait(100)
        except Exception:
            pass
        try:
            if self._worker:
                self._worker.deleteLater()
        except Exception:
            pass
        try:
            if self._worker:
                self._worker.deleteLater()
        except Exception:
            pass
        self._worker = None
        self._worker = None
        super().closeEvent(event)
