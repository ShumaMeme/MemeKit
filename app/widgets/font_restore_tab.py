"""还原字库 TAB - 批量刷写分区镜像恢复字库"""


import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDialog,
    QFileDialog, QCheckBox, QGridLayout, QScrollArea,
)

from qfluentwidgets import (
    CardWidget, PrimaryPushButton, PushButton, FluentIcon,
    InfoBar, InfoBarPosition, CaptionLabel,
    BodyLabel, SubtitleLabel, TitleLabel, LineEdit,
    isDarkTheme, ThemeColor,
)

from app import get_project_root
from app.components.log_widget import LogWidget
from app.components.blur_popup import show_blur_info, show_blur_dialog, show_blur_custom


# ---------------------------------------------------------------------------
# 弹窗样式（与快捷指令新增指令弹窗保持一致）
# ---------------------------------------------------------------------------
from app.components.dialog_styles import dialog_stylesheet


# ---------------------------------------------------------------------------
# 后台 Worker：重启设备
# ---------------------------------------------------------------------------
class _RebootWorker(QThread):
    log = Signal(str)
    result_ready = Signal(int)

    def __init__(self, fastboot_path: str, parent=None):
        super().__init__(parent)
        self.fastboot_path = fastboot_path

    def _silent_kwargs(self):
        kw = {}
        try:
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kw = {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
        except Exception:
            pass
        return kw

    def run(self):
        try:
            self.log.emit("正在重启设备...")
            proc = subprocess.run(
                [self.fastboot_path, "reboot"],
                capture_output=True, text=True, timeout=10,
                **self._silent_kwargs(),
            )
            if proc.returncode == 0:
                self.log.emit("重启指令已发送")
                self.result_ready.emit(0)
            else:
                err = (proc.stderr or proc.stdout or "").strip()
                self.log.emit(f"重启失败：{err}")
                self.result_ready.emit(-1)
        except Exception as e:
            self.log.emit(f"重启失败：{e}")
            self.result_ready.emit(-1)


# ---------------------------------------------------------------------------
# 后台 Worker：批量刷写分区镜像
# ---------------------------------------------------------------------------
class _FontRestoreWorker(QThread):
    log = Signal(str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)
    result_ready = Signal(int)
    ask_continue = Signal(str)  # 发送失败文件名，等待用户选择

    def __init__(self, fastboot_path: str, img_files: list, parent=None):
        super().__init__(parent)
        self.fastboot_path = fastboot_path
        self.img_files = img_files
        self._stop = False
        self._waiting = False
        self._continue = True

    def stop(self):
        self._stop = True
        self._continue = False

    def continue_flash(self, yes: bool):
        self._continue = yes
        self._waiting = False

    def _silent_kwargs(self):
        kw = {}
        try:
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kw = {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
        except Exception:
            pass
        return kw

    def _detect_device(self) -> tuple:
        """检测 Fastboot 设备，返回 (has_device, serial)"""
        try:
            proc = subprocess.run(
                [self.fastboot_path, "devices"],
                capture_output=True, text=True, timeout=5,
                **self._silent_kwargs(),
            )
            out = proc.stdout or ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and not parts[0].lower().startswith("(bootloader)"):
                    return True, parts[0]
        except Exception:
            pass
        return False, ""

    def _run_cmd(self, cmd, timeout=120):
        """执行命令并逐行输出日志。返回 exit code。"""
        try:
            self.log.emit(f"执行: {' '.join(cmd)}")
        except Exception:
            pass
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                **self._silent_kwargs(),
            )
            for line in iter(proc.stdout.readline, ''):
                if self._stop:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
                self.log.emit(line.rstrip('\r\n'))
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log.emit(f"执行超时（{timeout}秒），正在终止进程...")
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait()
                except Exception:
                    pass
                return -1
        except FileNotFoundError:
            self.log.emit("未找到 fastboot 可执行文件，请检查工具是否存在。")
            return -1
        except Exception as e:
            self.log.emit(f"执行失败：{e}")
            return -1
        finally:
            if proc and proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

    def run(self):
        import uuid
        try:
            # Step 1: 检测设备
            sid_dev = str(uuid.uuid4())
            self.step_start.emit(sid_dev, "检测 Fastboot 设备")
            has_device, serial = self._detect_device()
            if not has_device:
                self.step_finish.emit(sid_dev, False, "未检测到设备")
                self.log.emit("❌ 未检测到已连接的 Fastboot 设备，请检查 USB 连接并确保手机处于 Fastboot 或 Bootloader 模式。")
                self.result_ready.emit(-1)
                return
            self.log.emit(f"已识别设备: {serial}")
            self.step_finish.emit(sid_dev, True, "")

            # Step 2: 确认镜像文件列表
            sid_scan = str(uuid.uuid4())
            self.step_start.emit(sid_scan, "确认镜像文件")
            self.log.emit(f"确认刷写 {len(self.img_files)} 个镜像文件：")
            for f in self.img_files:
                size_mb = f.stat().st_size / (1024 * 1024)
                self.log.emit(f"  - {f.name} ({size_mb:.1f} MB)")
            self.step_finish.emit(sid_scan, True, "")

            if self._stop:
                self.result_ready.emit(-1)
                return

            # Step 3: 逐一刷写
            total = len(self.img_files)
            success_count = 0
            fail_count = 0
            for i, img_file in enumerate(self.img_files, 1):
                if self._stop:
                    break
                part_name = img_file.stem  # 文件名去掉 .img 作为分区名
                cmd = [self.fastboot_path, "flash", part_name, str(img_file)]
                self.log.emit(f"\n[{i}/{total}] 刷写分区: {part_name}")

                code = self._run_cmd(cmd, timeout=180)

                if code == 0:
                    self.log.emit(f"✅ {part_name} 刷写成功")
                    success_count += 1
                else:
                    self.log.emit(f"❌ {part_name} 刷写失败 (exit code: {code})")
                    fail_count += 1

                    # 询问用户是否继续
                    self._waiting = True
                    self.ask_continue.emit(part_name)
                    # 等待用户选择（最多等 60 秒）
                    waited = 0
                    while self._waiting and waited < 60 and not self._stop:
                        time.sleep(0.1)
                        waited += 0.1

                    if not self._continue:
                        self.log.emit("用户终止全部刷写任务")
                        break

            # Step 4: 汇总
            self.log.emit(f"\n===== 刷写完成 =====")
            self.log.emit(f"成功: {success_count}  失败: {fail_count}  总计: {total}")
            self.result_ready.emit(0 if fail_count == 0 else 1)

        except Exception as e:
            self.log.emit(f"发生异常：{e}")
            self.result_ready.emit(-1)


# ---------------------------------------------------------------------------
# 还原确认对话框（与备份字库分区列表同款样式）
# ---------------------------------------------------------------------------
class _RestoreConfirmDialog(QDialog):
    def __init__(self, img_files: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认刷写分区镜像")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title_lbl = SubtitleLabel("确认刷写分区镜像", self)
        title_lbl.setStyleSheet("color: #1D1B20;")
        layout.addWidget(title_lbl)

        self.img_files = img_files
        self.checkboxes = {}

        # 工具按钮
        tools = QHBoxLayout()
        btn_all = PushButton("全选")
        btn_all.setStyleSheet("color: #1D1B20;")
        btn_all.clicked.connect(self._select_all)
        btn_inv = PushButton("反选")
        btn_inv.setStyleSheet("color: #1D1B20;")
        btn_inv.clicked.connect(self._invert)
        tools.addWidget(btn_all)
        tools.addWidget(btn_inv)
        tools.addStretch(1)
        layout.addLayout(tools)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)

        grid_w = QWidget()
        self.grid = QGridLayout(grid_w)
        vbox.addWidget(grid_w)
        vbox.addStretch(1)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_ok = PrimaryPushButton("确定刷写", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.setStyleSheet("color: #1D1B20;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        self._populate()

    def _populate(self):
        row, col = 0, 0
        for f in self.img_files:
            display_name = f.name
            chk = QCheckBox(display_name)
            chk.setChecked(True)
            chk.setStyleSheet("color: #1D1B20;")
            self.grid.addWidget(chk, row, col)
            self.checkboxes[display_name] = (chk, f)
            col += 1
            if col > 2:
                col = 0
                row += 1

    def _select_all(self):
        for chk, _ in self.checkboxes.values():
            chk.setChecked(True)

    def _invert(self):
        for chk, _ in self.checkboxes.values():
            chk.setChecked(not chk.isChecked())

    def get_selected(self):
        return [f for name, (chk, f) in self.checkboxes.items() if chk.isChecked()]


# ---------------------------------------------------------------------------
# 还原字库 TAB 界面
# ---------------------------------------------------------------------------
class FontRestoreTab(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: Optional[QThread] = None
        self._worker: Optional[_FontRestoreWorker] = None
        self._reboot_worker: Optional[QThread] = None
        self._reboot_worker: Optional[_RebootWorker] = None
        self._fastboot_path = self._find_fastboot()
        self._img_dir = ""

        self._init_ui()

    def _find_fastboot(self) -> str:
        base = get_project_root()
        candidates = [
            base / "bin" / "fastboot.exe",
            base / "bin" / "fastboot",
            Path.cwd() / "bin" / "fastboot.exe",
            Path.cwd() / "bin" / "fastboot",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return "fastboot"

    def _detect_device(self) -> tuple:
        """检测 Fastboot 设备，返回 (has_device, serial)"""
        try:
            proc = subprocess.run(
                [self._fastboot_path, "devices"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            out = proc.stdout or ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and not parts[0].lower().startswith("(bootloader)"):
                    return True, parts[0]
        except Exception:
            pass
        return False, ""

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # ---- Banner ----
        banner_w = QWidget(self)
        banner_w.setFixedHeight(110)
        banner_w.setStyleSheet("background: transparent;")
        banner = QHBoxLayout(banner_w)
        banner.setContentsMargins(24, 18, 24, 18)
        banner.setSpacing(16)

        icon_lbl = QLabel("", banner_w)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignCenter)
        try:
            _ico = FluentIcon.PLAY.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
            icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        t = QLabel("还原字库", banner_w)
        t.setStyleSheet("font-size: 22px; font-weight: 600;")
        s = QLabel("批量刷写分区镜像文件，完整恢复手机字库", banner_w)
        s.setStyleSheet("font-size: 14px;")
        title_col.addWidget(t)
        title_col.addWidget(s)

        banner.addWidget(icon_lbl)
        banner.addLayout(title_col)
        banner.addStretch(1)
        layout.addWidget(banner_w)

        # 文件夹选择卡片
        card_select = CardWidget(self)
        v_select = QVBoxLayout(card_select)
        v_select.setContentsMargins(16, 16, 16, 16)
        v_select.setSpacing(10)

        row_folder = QHBoxLayout()
        row_folder.addWidget(SubtitleLabel("字库备份目录："))
        self.folder_edit = LineEdit(self)
        self.folder_edit.setPlaceholderText("选择包含分区镜像(.img)的备份文件夹")
        self.folder_edit.setReadOnly(True)
        row_folder.addWidget(self.folder_edit, 1)
        btn_pick = PushButton("选择文件夹", self, FluentIcon.FOLDER)
        btn_pick.clicked.connect(self._pick_folder)
        row_folder.addWidget(btn_pick)
        v_select.addLayout(row_folder)

        v_select.addWidget(CaptionLabel("提示：请确保文件夹内包含所有分区镜像文件（.img），文件名将作为分区名。"))

        # 按钮行：清空日志 + 开始还原 + 取消
        row_btn = QHBoxLayout()
        self.btn_clear = PushButton("清空日志", self, FluentIcon.DELETE)
        self.btn_clear.clicked.connect(lambda: self.log.clear_log())
        row_btn.addWidget(self.btn_clear)
        row_btn.addStretch(1)
        self.btn_cancel = PushButton("取消", self, FluentIcon.CANCEL)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        row_btn.addWidget(self.btn_cancel)
        self.btn_start = PrimaryPushButton("开始还原", self, FluentIcon.PLAY)
        self.btn_start.clicked.connect(self._start_restore)
        row_btn.addWidget(self.btn_start)
        v_select.addLayout(row_btn)

        layout.addWidget(card_select)

        # 日志卡片
        card_log = CardWidget(self)
        v_log = QVBoxLayout(card_log)
        v_log.setContentsMargins(16, 16, 16, 16)
        v_log.setSpacing(10)
        v_log.addWidget(SubtitleLabel("执行日志"))
        self.log = LogWidget()
        v_log.addWidget(self.log)
        layout.addWidget(card_log)

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择字库备份目录")
        if folder:
            self._img_dir = folder
            self.folder_edit.setText(folder)
            # 预览镜像文件
            img_files = []
            try:
                for f in sorted(Path(folder).iterdir()):
                    if f.suffix.lower() == '.img' and f.stat().st_size > 0:
                        img_files.append(f)
            except Exception:
                pass
            if img_files:
                self.log.clear_log()
                self.log.append_log(f"在 {folder} 中找到 {len(img_files)} 个镜像文件：")
                for f in img_files:
                    size_mb = f.stat().st_size / (1024 * 1024)
                    self.log.append_log(f"  - {f.name} ({size_mb:.1f} MB)")

    def _start_restore(self):
        if not self._img_dir or not os.path.isdir(self._img_dir):
            show_blur_info(self.window(), "提示", "请先选择字库备份目录")
            return

        # 检查镜像文件
        img_files = []
        try:
            for f in sorted(Path(self._img_dir).iterdir()):
                if f.suffix.lower() == '.img' and f.stat().st_size > 0:
                    img_files.append(f)
        except Exception:
            pass

        if not img_files:
            show_blur_info(self.window(), "提示", "所选文件夹内未找到任何有效的 .img 镜像文件")
            return

        # 二次确认 - 使用网格分区选择弹窗（与备份字库同款样式）
        dlg = _RestoreConfirmDialog(img_files, self.window())
        if show_blur_custom(self.window(), dlg) != QDialog.Accepted:
            self.log.append_log("已取消操作")
            return

        # 获取用户选择的文件
        selected_files = dlg.get_selected()
        self.log.append_log(f"已选择 {len(selected_files)} 个镜像文件")

        # 启动前检测设备连接状态
        has_device, serial = self._detect_device()
        if not has_device:
            show_blur_info(
                self.window(), "设备未连接",
                "未检测到已连接的 Fastboot 设备。\n\n"
                "请确保手机已进入 Fastboot 或 Bootloader 模式，\n"
                "并正确连接 USB 数据线后重试。"
            )
            return

        self.log.clear_log()
        self.log.append_log(f"开始字库还原流程... 已识别设备: {serial}")
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._worker = _FontRestoreWorker(
            fastboot_path=self._fastboot_path,
            img_files=selected_files,
            parent=self,
        )
        self._worker.log.connect(self.log.append_log)
        self._worker.step_start.connect(self.log.start_step)
        self._worker.step_finish.connect(self.log.finish_step)
        self._worker.ask_continue.connect(self._on_ask_continue)
        self._worker.result_ready.connect(self._on_finished)
        self._worker.result_ready.connect(self._worker.quit)
        self._worker.result_ready.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_ask_continue(self, failed_part: str):
        """刷写失败时询问用户是否继续。"""
        main_win = self.window()
        choice = show_blur_dialog(
            main_win, "刷写失败",
            f"分区 {failed_part} 刷写失败。\n\n是否继续刷写剩余镜像？"
        )
        if self._worker:
            self._worker.continue_flash(choice)

    def _on_finished(self, code: int):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)

        if code == 0:
            show_blur_dialog(
                self.window(), "刷写完成",
                "所有分区镜像已刷写完毕！"
            )
            InfoBar.success("完成", "所有分区镜像刷写完毕", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        elif code == 1:
            show_blur_dialog(
                self.window(), "刷写完成（部分失败）",
                "刷写流程结束，部分分区刷写失败。\n\n请检查日志确认失败的分区。"
            )
            InfoBar.warning("完成", "刷写流程结束（部分失败）", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        else:
            show_blur_dialog(
                self.window(), "刷写异常",
                "刷写流程异常中断，可能需要重新连接设备后重试。"
            )
            InfoBar.error("完成", "刷写流程异常中断", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            self.log.append_log("流程异常中断，可能需要重新连接设备后重试。")
            return

        # 只有在设备仍然连接的情况下才询问是否重启
        has_device, serial = self._detect_device()
        if not has_device:
            self.log.append_log("设备已断开连接，跳过重启询问。")
            return

        main_win = self.window()
        if show_blur_dialog(main_win, "重启设备", "字库还原流程已完成。\n\n是否重启手机？"):
            # 在后台线程执行重启，避免 UI 卡顿
            self._reboot_worker = _RebootWorker(self._fastboot_path, parent=self)
            self._reboot_worker.log.connect(self.log.append_log)
            self._reboot_worker.result_ready.connect(self._reboot_worker.quit)
            self._reboot_worker.result_ready.connect(self._reboot_worker.deleteLater)
            self._reboot_worker.start()
        else:
            self.log.append_log("已跳过重启")

    def _cancel(self):
        if self._worker:
            self._worker.stop()
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(100)
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.log.append_log("用户取消操作")

    def cleanup(self):
        # 关闭时不通过 _cancel() 避免阻塞 wait
        if self._worker:
            self._worker.stop()
        if self._worker and self._worker.isRunning():
            self._worker.quit()
        if self._reboot_worker and self._reboot_worker.isRunning():
            self._reboot_worker.quit()