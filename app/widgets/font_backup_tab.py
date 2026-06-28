"""备份字库 TAB - 通过 ADB 逐个 DD 分区 + 拉取到电脑"""


import os
import subprocess
import time
from pathlib import Path
from typing import Optional, List

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


# 排除的高危分区（不参与列表和计数）
RISKY_PARTITIONS = {"userdata", "metadata", "frp", "cache"}


# ---------------------------------------------------------------------------
# 弹窗样式（与快捷指令新增指令弹窗保持一致）
# ---------------------------------------------------------------------------
from app.components.dialog_styles import dialog_stylesheet


# ---------------------------------------------------------------------------
# 扫描 Worker
# ---------------------------------------------------------------------------
class _ScanWorker(QThread):
    log = Signal(str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)
    result_ready = Signal(list, str)  # partitions, error_msg

    def __init__(self, adb_path: str, serial: str, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = serial
        self._stop = False

    def stop(self):
        self._stop = True

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

    def _run_cmd(self, cmd: List[str], timeout=30) -> str:
        if self._stop:
            raise RuntimeError("Stopped")
        try:
            return subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                **self._silent_kwargs(),
            ).stdout.decode('utf-8', errors='ignore').strip()
        except Exception as e:
            raise RuntimeError(str(e))

    def _adb_shell(self, cmd: str, timeout=10) -> str:
        return self._run_cmd([self.adb_path, '-s', self.serial, 'shell', cmd], timeout=timeout)

    def run(self):
        import uuid
        try:
            self.log.emit("正在初始化连接...")

            if not os.path.exists(self.adb_path):
                self.result_ready.emit([], f"ADB 可执行文件未找到: {self.adb_path}")
                return

            # 检查 Root
            sid_root = str(uuid.uuid4())
            self.step_start.emit(sid_root, "检查 Root 权限")
            try:
                res_su = self._adb_shell("su -c 'id'", timeout=8)
                if "uid=0" not in res_su:
                    self.step_finish.emit(sid_root, False, "无 Root 权限")
                    self.result_ready.emit([], "未获取到 Root 权限。\n请在 Root 管理器（如 Magisk/KernelSU）中授予 Shell 软件 Root 权限后重试。")
                    return
                self.step_finish.emit(sid_root, True, "")
            except Exception as e:
                self.step_finish.emit(sid_root, False, str(e))
                self.result_ready.emit([], f"Root 权限检查失败: {e}\n请在 Root 管理器（如 Magisk/KernelSU）中授予 Shell 软件 Root 权限。")
                return

            # 查找分区表
            sid_scan = str(uuid.uuid4())
            self.step_start.emit(sid_scan, "查找分区表")
            search_paths = [
                "/dev/block/bootdevice/by-name",
                "/dev/block/by-name",
                "/dev/block/platform/*/by-name"
            ]
            partitions = []
            for p in search_paths:
                try:
                    if '*' in p:
                        base = p.split('*')[0]
                        ls_base = self._adb_shell(f"ls -d {base}* 2>/dev/null", timeout=5).strip()
                        if ls_base and "No such" not in ls_base:
                            lines = ls_base.splitlines()
                            if lines:
                                p = lines[0].strip() + "/by-name"
                    res = self._adb_shell(f"ls -1 {p}", timeout=5)
                    if res and "No such file" not in res and "Permission denied" not in res:
                        found = [x.strip() for x in res.split() if x.strip()]
                        partitions = [x for x in found if not x.startswith('/') and not x.startswith('ls:') and x]
                        if partitions:
                            # 过滤掉高危分区，统一使用过滤后的结果
                            partitions = [p for p in partitions if p.lower() not in RISKY_PARTITIONS]
                            self.log.emit(f"找到分区路径: {p} ({len(partitions)} 个分区)")
                            break
                except Exception:
                    continue

            if not partitions:
                self.step_finish.emit(sid_scan, False, "未找到分区")
                self.result_ready.emit([], "无法找到分区路径 (/dev/block/by-name 等)。")
            else:
                self.step_finish.emit(sid_scan, True, f"找到 {len(partitions)} 个分区")
                partitions.sort()
                self.result_ready.emit(partitions, "")

        except Exception as e:
            self.result_ready.emit([], f"扫描流程异常: {str(e)}")


# ---------------------------------------------------------------------------
# 备份 Worker
# ---------------------------------------------------------------------------
class _BackupWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)  # current, total
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)
    result_ready = Signal(bool, str)

    def __init__(self, adb_path: str, serial: str, partitions: List[str],
                 out_dir: str, phone_backup_dir: str = "/sdcard/Download/字库备份", parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self.serial = serial
        self.partitions = partitions
        self.out_dir = out_dir
        self.phone_backup_dir = phone_backup_dir
        self._stop = False

    def stop(self):
        self._stop = True

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

    def _run_cmd(self, cmd: List[str], timeout=30) -> str:
        try:
            return subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                **self._silent_kwargs(),
            ).stdout.decode('utf-8', errors='ignore').strip()
        except Exception as e:
            raise RuntimeError(str(e))

    def _adb_shell(self, cmd: str, timeout=60) -> str:
        return self._run_cmd([self.adb_path, '-s', self.serial, 'shell', cmd], timeout=timeout)

    def run(self):
        import uuid
        try:
            if not self.partitions:
                self.result_ready.emit(False, "未选择任何分区")
                return

            # 查找分区路径
            self.log.emit("正在定位分区表路径...")
            target_path = ""
            search_paths = [
                "/dev/block/bootdevice/by-name",
                "/dev/block/by-name",
                "/dev/block/platform/*/by-name"
            ]
            for p in search_paths:
                try:
                    if '*' in p:
                        base = p.split('*')[0]
                        ls_base = self._adb_shell(f"ls -d {base}* 2>/dev/null", timeout=5).strip()
                        if ls_base and "No such" not in ls_base:
                            p = ls_base + "/by-name"
                    res = self._adb_shell(f"ls {p}", timeout=5)
                    if res and "No such file" not in res:
                        target_path = p
                        break
                except Exception:
                    continue

            if not target_path:
                self.result_ready.emit(False, "找不到分区路径")
                return

            self.log.emit(f"分区路径: {target_path}")

            # 创建手机端备份目录
            self._adb_shell(f"mkdir -p {self.phone_backup_dir}")

            total = len(self.partitions)
            success_count = 0

            for idx, part in enumerate(self.partitions):
                if self._stop:
                    break

                self.progress.emit(idx + 1, total)
                sid_part = str(uuid.uuid4())
                self.step_start.emit(sid_part, f"[{idx+1}/{total}] 备份 {part}")

                remote_tmp = f"{self.phone_backup_dir}/{part}.img"

                # DD 备份分区
                dd_cmd = f"su -c 'dd if={target_path}/{part} of={remote_tmp}'"
                try:
                    self._adb_shell(dd_cmd, timeout=3600)
                except Exception as e:
                    self.log.emit(f"  - 分区 {part} 备份失败 (DD): {e}")
                    self.step_finish.emit(sid_part, False, f"DD失败: {e}")
                    continue

                # Pull 到电脑
                local_img = os.path.join(self.out_dir, f"{part}.img")
                try:
                    pull_cmd = [self.adb_path, '-s', self.serial, 'pull', remote_tmp, local_img]
                    self._run_cmd(pull_cmd, timeout=3600)
                    success_count += 1
                    self.log.emit(f"✅ {part} 备份成功")
                    self.step_finish.emit(sid_part, True, "")
                except Exception as e:
                    self.log.emit(f"  - 分区 {part} 拉取失败: {e}")
                    self.step_finish.emit(sid_part, False, f"Pull失败: {e}")

                # 清理手机端临时文件
                try:
                    self._adb_shell(f"rm -f {remote_tmp}", timeout=10)
                except Exception:
                    pass

            if self._stop:
                self.result_ready.emit(False, "备份已取消")
                return

            self.log.emit(f"\n===== 备份完成 =====")
            self.log.emit(f"成功: {success_count} / 总计: {total}")
            self.log.emit(f"保存至: {os.path.abspath(self.out_dir)}")
            self.result_ready.emit(success_count > 0, self.out_dir)

        except Exception as e:
            self.log.emit(f"错误: {str(e)}")
            self.result_ready.emit(False, str(e))


# ---------------------------------------------------------------------------
# 分区选择对话框（使用 QDialog + 模糊背景，避开 MessageBoxBase 事件吞没问题）
# ---------------------------------------------------------------------------
class _PartitionSelectDialog(QDialog):
    def __init__(self, partitions: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择需要备份的分区")
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title_lbl = SubtitleLabel("选择需要备份的分区", self)
        title_lbl.setStyleSheet("color: #1D1B20;")
        layout.addWidget(title_lbl)

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
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.setStyleSheet("color: #1D1B20;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        self.checkboxes = {}
        self._populate(partitions)

    def _populate(self, partitions):
        row, col = 0, 0
        for p in partitions:
            chk = QCheckBox(p)
            chk.setChecked(True)
            chk.setStyleSheet("color: #1D1B20;")
            self.grid.addWidget(chk, row, col)
            self.checkboxes[p] = chk
            col += 1
            if col > 2:
                col = 0
                row += 1

    def get_selected(self):
        return [n for n, c in self.checkboxes.items() if c.isChecked()]


# ---------------------------------------------------------------------------
# 备份字库 TAB 界面
# ---------------------------------------------------------------------------
class FontBackupTab(QWidget):
    def __init__(self):
        super().__init__()
        self._scan_worker: Optional[QThread] = None
        self._scan_worker: Optional[_ScanWorker] = None
        self._backup_worker: Optional[QThread] = None
        self._backup_worker: Optional[_BackupWorker] = None
        self._adb_path = self._find_adb()
        self._serial = ""
        self._partitions: List[str] = []
        self._selected_partitions: List[str] = []

        self._init_ui()

    def _find_adb(self) -> str:
        base = get_project_root()
        candidates = [
            base / "bin" / "adb.exe",
            base / "bin" / "adb",
            Path.cwd() / "bin" / "adb.exe",
            Path.cwd() / "bin" / "adb",
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return "adb"

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
            _ico = FluentIcon.SAVE.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
            icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)
        t = QLabel("备份字库", banner_w)
        t.setStyleSheet("font-size: 22px; font-weight: 600;")
        s = QLabel("通过 ADB 逐个 DD 备份分区镜像到电脑", banner_w)
        s.setStyleSheet("font-size: 14px;")
        title_col.addWidget(t)
        title_col.addWidget(s)

        banner.addWidget(icon_lbl)
        banner.addLayout(title_col)
        banner.addStretch(1)
        layout.addWidget(banner_w)

        # 设置卡片
        card_settings = CardWidget(self)
        v_settings = QVBoxLayout(card_settings)
        v_settings.setContentsMargins(16, 16, 16, 16)
        v_settings.setSpacing(10)

        # 重要提示
        v_settings.addWidget(CaptionLabel(
            "提示：请在 Root 管理器（如 Magisk/KernelSU）中授予 Shell 软件 Root 权限。"
        ))

        # 保存目录
        row_pull = QHBoxLayout()
        self.pull_dir_edit = LineEdit(self)
        self.pull_dir_edit.setPlaceholderText("选择电脑上的备份保存目录")
        self.pull_dir_edit.setReadOnly(True)
        row_pull.addWidget(SubtitleLabel("保存到电脑："))
        row_pull.addWidget(self.pull_dir_edit, 1)
        btn_pick = PushButton("选择目录", self, FluentIcon.FOLDER)
        btn_pick.clicked.connect(self._pick_pc_dir)
        row_pull.addWidget(btn_pick)
        v_settings.addLayout(row_pull)

        layout.addWidget(card_settings)

        # 操作按钮
        row_btn = QHBoxLayout()
        self.btn_clear = PushButton("清空日志", self, FluentIcon.DELETE)
        self.btn_clear.clicked.connect(lambda: self.log.clear_log())
        row_btn.addWidget(self.btn_clear)
        row_btn.addStretch(1)
        self.btn_scan = PushButton("扫描分区", self, FluentIcon.SEARCH)
        self.btn_scan.clicked.connect(self._scan_partitions)
        row_btn.addWidget(self.btn_scan)
        self.btn_cancel = PushButton("取消", self, FluentIcon.CANCEL)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        row_btn.addWidget(self.btn_cancel)
        self.btn_start = PrimaryPushButton("开始备份", self, FluentIcon.PLAY)
        self.btn_start.clicked.connect(self._start_backup)
        row_btn.addWidget(self.btn_start)
        layout.addLayout(row_btn)

        # 日志卡片
        card_log = CardWidget(self)
        v_log = QVBoxLayout(card_log)
        v_log.setContentsMargins(16, 16, 16, 16)
        v_log.setSpacing(10)
        v_log.addWidget(SubtitleLabel("执行日志"))
        self.log = LogWidget()
        v_log.addWidget(self.log)
        layout.addWidget(card_log)

    def _pick_pc_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择备份保存目录")
        if folder:
            self.pull_dir_edit.setText(folder)

    def _scan_partitions(self):
        """扫描分区表。"""
        # 检查是否已选择保存路径
        pc_dir = self.pull_dir_edit.text().strip()
        if not pc_dir:
            show_blur_info(self.window(), "提示", "请先选择电脑上的备份保存目录")
            return

        # 检测设备
        self.log.clear_log()
        self.log.append_log("================ 重要提示 ================")
        self.log.append_log("请确保在 Root 管理器（如 Magisk/KernelSU）中")
        self.log.append_log("已授予 Shell 软件 Root 权限！")
        self.log.append_log("========================================")
        self.log.append_log("正在检测设备连接...")

        try:
            proc = subprocess.run(
                [self._adb_path, "devices"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            out = (proc.stdout or "").strip()
            lines = out.splitlines()[1:] if out else []
            devices = [l for l in lines if l.strip() and "\tdevice" in l]
            if not devices:
                show_blur_info(self.window(), "设备未连接", "未检测到已连接的 ADB 设备。\n请确保手机已开启 USB 调试并连接电脑。")
                return
            self._serial = devices[0].split("\t")[0].strip()
            self.log.append_log(f"已识别设备: {self._serial}")
        except Exception as e:
            show_blur_info(self.window(), "错误", f"检测设备失败：{e}")
            return

        self.btn_scan.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._scan_worker = _ScanWorker(self._adb_path, self._serial, parent=self)
        self._scan_worker.log.connect(self.log.append_log)
        self._scan_worker.step_start.connect(self.log.start_step)
        self._scan_worker.step_finish.connect(self.log.finish_step)
        self._scan_worker.result_ready.connect(self._on_scan_finished)
        self._scan_worker.result_ready.connect(self._scan_worker.quit)
        self._scan_worker.result_ready.connect(self._scan_worker.deleteLater)
        self._scan_worker.start()

    def _on_scan_finished(self, partitions: list, error: str):
        self.btn_scan.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)

        if error:
            show_blur_info(self.window(), "扫描失败", error)
            return

        self._partitions = partitions
        self.log.append_log(f"\n扫描完成，共发现 {len(partitions)} 个分区。")

        # 弹出分区选择对话框
        dlg = _PartitionSelectDialog(partitions, self.window())
        if show_blur_custom(self.window(), dlg):
            self._selected_partitions = dlg.get_selected()
            if not self._selected_partitions:
                self.log.append_log("未选择任何分区。")
            else:
                self.log.append_log(f"已选择 {len(self._selected_partitions)} 个分区：")
                for p in self._selected_partitions[:20]:
                    self.log.append_log(f"  - {p}")
                if len(self._selected_partitions) > 20:
                    self.log.append_log(f"  ... 等共 {len(self._selected_partitions)} 个")
        else:
            self.log.append_log("已取消分区选择。")

    def _start_backup(self):
        pc_dir = self.pull_dir_edit.text().strip()
        if not pc_dir:
            show_blur_info(self.window(), "提示", "请先选择电脑上的备份保存目录")
            return
        if not self._selected_partitions:
            show_blur_info(self.window(), "提示", "请先扫描并选择需要备份的分区")
            return
        if not self._serial:
            show_blur_info(self.window(), "提示", "请先扫描设备")
            return

        # 创建带时间戳的备份目录
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(pc_dir, f"字库备份_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)

        self.log.append_log(f"\n开始备份到: {backup_dir}")
        self.log.append_log(f"共 {len(self._selected_partitions)} 个分区\n")

        self.btn_start.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._backup_worker = _BackupWorker(
            adb_path=self._adb_path,
            serial=self._serial,
            partitions=self._selected_partitions,
            out_dir=backup_dir,
            parent=self,
        )
        self._backup_worker.log.connect(self.log.append_log)
        self._backup_worker.step_start.connect(self.log.start_step)
        self._backup_worker.step_finish.connect(self.log.finish_step)
        self._backup_worker.result_ready.connect(self._on_backup_finished)
        self._backup_worker.result_ready.connect(self._backup_worker.quit)
        self._backup_worker.result_ready.connect(self._backup_worker.deleteLater)
        self._backup_worker.start()

    def _on_backup_finished(self, success: bool, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if success:
            show_blur_dialog(
                self.window(), "备份完成",
                f"字库备份已完成！\n\n保存路径：{msg}\n\n请检查备份文件是否完整。"
            )
            InfoBar.success("完成", f"备份完成！保存至: {msg}", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        else:
            show_blur_dialog(
                self.window(), "备份失败",
                f"字库备份过程中出现错误：\n\n{msg}"
            )
            InfoBar.error("失败", msg, parent=self, position=InfoBarPosition.TOP, isClosable=True)

    def _cancel(self):
        if self._scan_worker:
            self._scan_worker.stop()
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.quit()
            self._scan_worker.wait(100)

        if self._backup_worker:
            self._backup_worker.stop()
        if self._backup_worker and self._backup_worker.isRunning():
            self._backup_worker.quit()
            self._backup_worker.wait(100)

        self.btn_start.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.log.append_log("操作已取消")

    def cleanup(self):
        # 关闭时不通过 _cancel() 避免阻塞 wait
        if self._scan_worker:
            self._scan_worker.stop()
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.quit()
        if self._backup_worker:
            self._backup_worker.stop()
        if self._backup_worker and self._backup_worker.isRunning():
            self._backup_worker.quit()