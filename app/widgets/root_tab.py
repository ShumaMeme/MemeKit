import os
import subprocess

import shutil
import gzip
import time
from pathlib import Path
from typing import List, Tuple
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QFileDialog, QGridLayout
from qfluentwidgets import (
    PrimaryPushButton,
    PushButton,
    InfoBar,
    InfoBarPosition,
    CardWidget,
    TitleLabel,
    FluentIcon,
    SmoothScrollArea,
    ComboBox,
    BodyLabel,
    CaptionLabel,
    LineEdit,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSize

from app.services import adb_service
from app.components.log_widget import LogWidget


from app import get_project_root
_BIN_DIR = get_project_root() / "bin"
_ROOT_MGR_DIR = _BIN_DIR / "root_managers"

ROOT_MANAGERS: list[dict] = [
    {
        "name": "Sukisu Ultra",
        "path": _ROOT_MGR_DIR / "Sukisu-Ultra.apk",
        "package_name": "com.sukisu.ultra",
    },
    {
        "name": "KernelSU",
        "path": _ROOT_MGR_DIR / "KernelSU.apk",
        "package_name": "me.weishu.kernelsu",
    },
    {
        "name": "Magisk",
        "path": _ROOT_MGR_DIR / "Magisk.apk",
        "package_name": "com.topjohnwu.magisk",
    },
    {
        "name": "Magisk Alpha",
        "path": _ROOT_MGR_DIR / "Magisk-Alpha.apk",
        "package_name": "io.github.vvb2060.magisk",
    },
    {
        "name": "APatch",
        "path": _ROOT_MGR_DIR / "APatch.apk",
        "package_name": "me.bmax.apatch",
    },
]


from app.components.blur_popup import show_blur_info


class _RootRefreshThread(QThread):
    """后台线程：刷新设备信息，使用 ADB Server Socket 直连"""
    result_ready = Signal(dict)

    def run(self):
        result = {}
        try:
            adb_client = adb_service._adb_server(timeout=2.0)
            devs = adb_client.host_devices(timeout=2.0)
            serial = ""
            mode = "none"
            for s, st in devs:
                if st == "device":
                    serial = s
                    mode = "system"
                    break
            result["mode"] = mode
            result["serial"] = serial
            if mode == "system" and serial:
                result["brand"] = (adb_service.adb_shell_serial(serial, "getprop ro.product.brand", timeout=6) or "").strip()
                result["model"] = (adb_service.adb_shell_serial(serial, "getprop ro.product.model", timeout=6) or "").strip()
                result["android"] = (adb_service.adb_shell_serial(serial, "getprop ro.build.version.release", timeout=6) or "").strip()
                result["rom"] = (adb_service.adb_shell_serial(serial, "getprop ro.build.display.id", timeout=6) or "").strip()
                result["kernel"] = (adb_service.adb_shell_serial(serial, "uname -r", timeout=6) or "").strip()
        except Exception:
            pass
        self.result_ready.emit(result)


class _RootWatchTickThread(QThread):
    """后台线程：执行轻量级设备状态检测（Root TAB专用）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None

    def run(self):
        try:
            adb_client = adb_service._adb_server(timeout=2.0)
            devs = adb_client.host_devices(timeout=2.0)
            self._state = "|".join(f"{s}:{st}" for s, st in sorted(devs))
        except Exception:
            self._state = None


class _GuidedRootWorker(QThread):
    log = Signal(str)
    result_ready = Signal(int)
    stage = Signal(str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)

    def __init__(
        self,
        *,
        boot_img: str,
        manager_name: str,
        manager_path: str,
        package_name: str,
        flash_partition: str,
        adb_path: str,
        fastboot_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self.boot_img = str(boot_img or "").strip()
        self.boot_name = Path(self.boot_img).name  # 实际文件名，如 init_boot.img
        self.manager_name = str(manager_name or "").strip()
        self.manager_path = str(manager_path or "").strip()
        self.package_name = str(package_name or "").strip()
        self.flash_partition = str(flash_partition or "boot").strip() or "boot"
        self.adb = adb_path
        self.fastboot = fastboot_path
        self.work_dir = Path("root_work")
        self._stop_flag = False
        self._current_proc = None

    def _sleep(self, secs: float):
        """支持 stop_flag 中断的 sleep。"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop_flag:
                return
            time.sleep(0.1)

    def _run_cmd(self, cmd: List[str], timeout=None, *, quiet: bool = False) -> Tuple[int, str]:
        proc = None
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self._current_proc = proc

            out_lines = []
            while True:
                if self._stop_flag:
                    try:
                        proc.terminate()
                        # 关键：关闭 stdout 管道以强制 readline() 返回，解决线程阻塞无法终止
                        try:
                            if proc.stdout:
                                proc.stdout.close()
                        except Exception:
                            pass
                    except Exception:
                        pass
                    break
                if proc.stdout is None:
                    break
                line = proc.stdout.readline()
                if not line:
                    # readline() 返回空字符串表示 EOF
                    if proc.poll() is not None:
                        break
                    # 可能是管道被关闭（stop 触发），等待进程退出
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
                    break
                s = line.strip()
                if s and not quiet:
                    self.log.emit(s)
                out_lines.append(s)

            try:
                exit_code = proc.wait(timeout=5) if proc.poll() is None else proc.poll()
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait()
                except Exception:
                    pass
                exit_code = -1
            return exit_code, "\n".join(out_lines)
        except Exception as e:
            return -1, str(e)
        finally:
            self._current_proc = None

    def run(self):
        import uuid
        try:
            self.work_dir.mkdir(exist_ok=True)
            boot_path = Path(self.boot_img)
            if not boot_path.exists() or not boot_path.is_file():
                self.log.emit("boot.img 路径无效")
                self.result_ready.emit(-1)
                return

            mode, serial = adb_service.detect_connection_mode()
            if mode != 'system' or not serial:
                self.log.emit("请确保手机在系统模式并连接 ADB")
                self.result_ready.emit(-1)
                return

            # 1) 检查本地管理器 APK
            sid_dl = str(uuid.uuid4())
            self.stage.emit("download_apk")
            self.step_start.emit(sid_dl, f"检查管理器 ({self.manager_name})")
            
            apk_local = Path(self.manager_path)
            if not apk_local.exists() or not apk_local.is_file():
                self.log.emit(f"管理器文件不存在：{apk_local}")
                self.step_finish.emit(sid_dl, False, f"文件不存在：{apk_local}")
                self.result_ready.emit(-1)
                return
            self.step_finish.emit(sid_dl, True, "")

            # 2) 推送 boot.img + APK
            sid_push = str(uuid.uuid4())
            self.stage.emit("push_files")
            self.step_start.emit(sid_push, "推送文件到手机")
            
            remote_dir = "/storage/emulated/0/MemeKit"
            remote_boot = remote_dir + "/" + self.boot_name
            remote_apk = remote_dir + "/root_manager.apk"
            # self.log.emit("推送文件到手机 Download...")
            self._run_cmd([self.adb, "-s", serial, "shell", f"mkdir -p '{remote_dir}'"])
            code, out = self._run_cmd([self.adb, "-s", serial, "push", str(boot_path), remote_boot])
            if code != 0:
                self.log.emit(f"boot.img 推送失败：{out}")
                self.step_finish.emit(sid_push, False, "boot.img 推送失败")
                self.result_ready.emit(-1)
                return
            code, out = self._run_cmd([self.adb, "-s", serial, "push", str(apk_local), remote_apk])
            if code != 0:
                self.log.emit(f"APK 推送失败：{out}")
                self.step_finish.emit(sid_push, False, "APK 推送失败")
                self.result_ready.emit(-1)
                return
            # self.log.emit(f"已推送到：{remote_dir}")
            self.step_finish.emit(sid_push, True, "")

            # 3) 安装 APK
            sid_install = str(uuid.uuid4())
            self.stage.emit("install_apk")
            self.step_start.emit(sid_install, "安装 Root 管理器")

            self.log.emit("正在安装管理器...")
            code, out = self._run_cmd([self.adb, "-s", serial, "install", "-r", str(apk_local)])
            if code == 0:
                self.log.emit(f"{self.manager_name} 安装成功")
                self.step_finish.emit(sid_install, True, "")
            else:
                self.log.emit(f"安装失败，请手动安装 APK 后重试")
                self.log.emit(f"APK 路径：{remote_dir}/root_manager.apk")
                self.step_finish.emit(sid_install, False, "安装失败")
                self.result_ready.emit(-1)
                return

            # 4) 等待安装确认，然后自动打开管理器 App
            self.stage.emit("wait_install")
            self.log.emit("等待 5 秒以确保安装完成...")
            self._sleep(5)

            if self.package_name:
                self.log.emit(f"正在自动打开 {self.manager_name} 主界面...")
                code, out = self._run_cmd(
                    [self.adb, "-s", serial, "shell", "monkey", "-p", self.package_name, "-c", "android.intent.category.LAUNCHER", "1"],
                    quiet=True,
                )
                if code == 0:
                    self.log.emit(f"已成功打开 {self.manager_name}，请查看手机屏幕")
                else:
                    self.log.emit(f"自动打开 {self.manager_name} 失败，请手动打开应用")
                    self.log.emit(f"包名: {self.package_name}")
            else:
                self.log.emit("未提供管理器包名，请手动打开应用")

            # 5) 等待用户修补 Boot 镜像
            self.stage.emit("open_manager")
            
            sid_patch = str(uuid.uuid4())
            self.step_start.emit(sid_patch, "等待用户修补 Boot 镜像")
            self.log.emit("================ 关键操作 ================")
            self.log.emit("请在 Root 管理器中选择：‘选择并修补 boot’")
            self.log.emit("并选择以下文件：")
            self.log.emit(f"{remote_dir}/{self.boot_name}")
            self.log.emit("修补完成后，工具箱会自动进行下一步流程，无需操作手机。")
            self.log.emit("========================================")

            # 6) 轮询 Download 新增 .img
            self.stage.emit("wait_patched")
            # self.log.emit("开始轮询手机 Download 查找新增镜像（不刷屏输出）...")

            # 彻底废弃所有复杂的 shell 命令，全盘用 Python 原生解析
            # 用最基础的 adb shell ls -1 <dir> 列出文件名，再按需查 stat
            def _get_img_files() -> dict:
                files = {}
                for d in ["/sdcard/Download", remote_dir]:
                    code, out = self._run_cmd([self.adb, "-s", serial, "shell", f"ls -1 '{d}'"], quiet=True)
                    if code != 0 or not out:
                        continue
                    for line in out.splitlines():
                        filename = line.strip()
                        if not filename or "No such file" in filename or "Permission denied" in filename:
                            continue
                        if filename.endswith(".img") or filename.endswith(".img.gz"):
                            full_path = f"{d}/{filename}"
                            if full_path == f"{remote_dir}/{self.boot_name}":
                                continue
                            
                            # 获取大小和修改时间作为签名，避免同名覆盖检测不到
                            sc, sout = self._run_cmd(
                                [self.adb, "-s", serial, "shell", f"stat -c '%Y %s' '{full_path}' 2>/dev/null || stat -f '%m %z' '{full_path}' 2>/dev/null || echo ''"],
                                quiet=True
                            )
                            sig = sout.strip() if sc == 0 else ""
                            files[full_path] = sig
                return files

            baseline_map = _get_img_files()
            # try:
            #     print("[root][poll] baseline_count=", len(baseline_map))
            #     for p in sorted(list(baseline_map.keys()))[:10]:
            #         print(f"[root][poll] baseline: {p} -> {baseline_map[p]}")
            # except Exception:
            #     pass
            patched_remote = ""
            for i in range(900):
                if self._stop_flag:
                    self.step_finish.emit(sid_patch, False, "取消")
                    self.log.emit("已取消")
                    self.result_ready.emit(-2)
                    return

                cur_map = _get_img_files()

                new_paths = [p for p in cur_map.keys() if p not in baseline_map]
                changed_paths = [p for p in cur_map.keys() if (p in baseline_map and cur_map[p] != baseline_map[p])]

                # try:
                #     if i % 5 == 0:
                #         print(
                #             "[root][poll] tick=", i,
                #             "cur_count=", len(cur_map),
                #             "new=", len(new_paths),
                #             "changed=", len(changed_paths),
                #         )
                #         if new_paths:
                #             for p in sorted(new_paths)[:5]:
                #                 print("[root][poll] new:", p)
                #         if changed_paths:
                #             for p in sorted(changed_paths)[:5]:
                #                 print("[root][poll] changed:", p)
                # except Exception:
                #     pass

                cand = ""
                if new_paths:
                    cand = sorted(new_paths)[-1]
                elif changed_paths:
                    cand = sorted(changed_paths)[-1]

                if cand:
                    # try:
                    #     print("[root][poll] picked:", cand)
                    # except Exception:
                    #     pass
                    patched_remote = cand
                    break
                self._sleep(2)
            if not patched_remote:
                self.step_finish.emit(sid_patch, False, "超时未检测到镜像")
                self.log.emit("超时：未检测到新增 .img")
                self.result_ready.emit(-1)
                return
            self.step_finish.emit(sid_patch, True, f"检测到 {Path(patched_remote).name}")
            # self.log.emit(f"检测到修补镜像：{patched_remote}")

            # 等待文件写入完成：循环检查文件大小直到稳定，防止拉取到不完整的文件
            self.log.emit("等待修补文件写入完成...")
            stable = False
            last_size = -1
            for _ in range(30):  # 最多等待 60 秒 (30 * 2s)
                if self._stop_flag:
                    self.step_finish.emit(sid_patch, False, "取消")
                    self.log.emit("已取消")
                    self.result_ready.emit(-2)
                    return
                sc, sout = self._run_cmd(
                    [self.adb, "-s", serial, "shell", f"stat -c '%s' '{patched_remote}' 2>/dev/null || stat -f '%z' '{patched_remote}' 2>/dev/null || echo '0'"],
                    quiet=True
                )
                try:
                    cur_size = int(sout.strip())
                except ValueError:
                    cur_size = 0
                if cur_size > 0 and cur_size == last_size:
                    stable = True
                    break
                last_size = cur_size
                self._sleep(2)
            if not stable:
                self.log.emit(f"警告：修补文件大小未稳定（当前 {last_size} 字节），将尝试继续...")

            # 7) 拉取到电脑
            sid_pull = str(uuid.uuid4())
            self.stage.emit("pull_patched")
            self.step_start.emit(sid_pull, "拉取修补镜像")
            
            patched_local = self.work_dir / Path(patched_remote).name
            # self.log.emit("正在拉取镜像到电脑...")
            code, out = self._run_cmd([self.adb, "-s", serial, "pull", patched_remote, str(patched_local)])
            if code != 0 or not patched_local.exists():
                self.step_finish.emit(sid_pull, False, f"拉取失败: {out}")
                self.log.emit(f"拉取失败：{out}")
                self.result_ready.emit(-1)
                return

            flash_img = patched_local
            try:
                if str(patched_local).lower().endswith(".img.gz"):
                    out_img = self.work_dir / (patched_local.stem)
                    self.log.emit("检测到 .img.gz，正在解压...")
                    with gzip.open(patched_local, 'rb') as f_in, open(out_img, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    if out_img.exists():
                        flash_img = out_img
                        self.log.emit(f"解压完成：{out_img}")
            except Exception as e:
                self.log.emit(f"解压失败（将尝试直接刷写原文件）：{e}")

            # self.log.emit(f"已拉取：{patched_local}")
            self.step_finish.emit(sid_pull, True, "")

            # 8) 重启 bootloader 并刷写
            sid_flash = str(uuid.uuid4())
            self.stage.emit("flash")
            self.step_start.emit(sid_flash, f"刷入修补镜像 ({self.flash_partition})")
            
            self.log.emit("重启至 bootloader...")
            self._run_cmd([self.adb, "-s", serial, "reboot", "bootloader"])
            self.log.emit("已进入 bootloader，额外等待 7 秒以确保 fastboot 连接稳定...")
            self._sleep(7)
            # self.log.emit(f"刷写分区：{self.flash_partition}")
            code, out = self._run_cmd([self.fastboot, "flash", self.flash_partition, str(flash_img)])
            if code != 0:
                self.step_finish.emit(sid_flash, False, "刷写失败")
                self.log.emit(f"刷写失败：{out}")
                self.result_ready.emit(-1)
                return
            self.step_finish.emit(sid_flash, True, "")

            # 9) 重启
            sid_reboot = str(uuid.uuid4())
            self.stage.emit("reboot")
            self.step_start.emit(sid_reboot, "重启系统")
            # self.log.emit("刷写成功，正在重启系统...")
            self._run_cmd([self.fastboot, "reboot"])
            self.step_finish.emit(sid_reboot, True, "")
            
            self.log.emit("恭喜你，成功获取到了Root权限")
            self.result_ready.emit(0)

        except Exception as e:
            self.log.emit(f"发生未知错误: {e}")
            self.result_ready.emit(-1)

    def stop(self):
        self._stop_flag = True
        try:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
                # 关闭 stdout 管道强制 readline() 返回，解决线程阻塞
                try:
                    if self._current_proc.stdout:
                        self._current_proc.stdout.close()
                except Exception:
                    pass
        except Exception:
            pass


class RootTab(QWidget):
    def __init__(self):
        super().__init__()
        self._adb = self._resolve_bin("adb")
        self._fastboot = self._resolve_bin("fastboot")
        self._thread: QThread | None = None
        self._worker: _GuidedRootWorker | None = None
        self._watch_worker = None  # 兼容旧引用
        self._watch_timer = None
        self._watch_tick_thread = None
        self._last_watch_state = ""
        self._refresh_thread: QThread | None = None
        self._refreshing = False
        self._did_first_show = False
        self._build_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if self._did_first_show:
            return
        self._did_first_show = True
        QTimer.singleShot(100, self._start_device_watcher)
        QTimer.singleShot(200, self._refresh_device_info)

    def closeEvent(self, event):
        try:
            self.cleanup()
        except Exception:
            pass
        try:
            return super().closeEvent(event)
        except Exception:
            try:
                event.accept()
            except Exception:
                pass

    def _resolve_bin(self, name: str) -> str:
        base = Path(__file__).resolve().parent
        tool = (base / ".." / ".." / "bin" / (name + ".exe")).resolve()
        if tool.exists():
            return str(tool)
        return name

    def _build_ui(self):
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
        self.layout.setContentsMargins(32, 32, 32, 32)
        self.layout.setSpacing(24)

        main_h_layout = QHBoxLayout()
        main_h_layout.setSpacing(24)
        
        left_col = QVBoxLayout()
        left_col.setSpacing(24)
        
        self._build_status_card(left_col)
        self._build_options_card(left_col)
        self._build_action_card(left_col)
        left_col.addStretch(1)
        
        right_col = QVBoxLayout()
        right_col.setSpacing(24)
        self._build_log_card(right_col)
        
        left_w = QWidget()
        left_w.setLayout(left_col)
        right_w = QWidget()
        right_w.setLayout(right_col)
        
        main_h_layout.addWidget(left_w, 4)
        main_h_layout.addWidget(right_w, 5)
        
        self.layout.addLayout(main_h_layout)

    def _build_status_card(self, parent_layout):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        head_lay = QHBoxLayout()
        icon = QLabel("📱")
        icon.setStyleSheet("font-size: 20px;")
        title = QLabel("设备信息")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        head_lay.addWidget(icon)
        head_lay.addWidget(title)
        head_lay.addStretch(1)
        lay.addLayout(head_lay)

        status_row = QHBoxLayout()
        self.lbl_dev = QLabel("未检测到设备")
        self.lbl_dev.setStyleSheet("font-size: 15px; font-weight: 500;")
        self.lbl_suggest = QLabel("分区建议：-")
        self.lbl_suggest.setStyleSheet("font-size: 14px; color: #86909c;")
        self.lbl_suggest.setWordWrap(True)
        
        info_lay = QVBoxLayout()
        info_lay.setSpacing(4)
        info_lay.addWidget(self.lbl_dev)
        info_lay.addWidget(self.lbl_suggest)
        
        btn_refresh = PushButton(FluentIcon.SYNC, "刷新状态")
        btn_refresh.setFixedHeight(32)
        btn_refresh.clicked.connect(self._refresh_device_info)
        self.btn_refresh = btn_refresh
        
        status_row.addLayout(info_lay, 1)
        status_row.addWidget(btn_refresh)
        
        lay.addLayout(status_row)
        parent_layout.addWidget(card)

    def _build_options_card(self, parent_layout):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        head_lay = QHBoxLayout()
        icon = QLabel("⚙️")
        icon.setStyleSheet("font-size: 20px;")
        title = QLabel("Root 选项")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        head_lay.addWidget(icon)
        head_lay.addWidget(title)
        head_lay.addStretch(1)
        lay.addLayout(head_lay)

        # Image Selection
        row_boot = QHBoxLayout()
        row_boot.setSpacing(12)
        self.edt_boot = LineEdit()
        self.edt_boot.setReadOnly(True)
        self.edt_boot.setPlaceholderText("选择原版 boot.img / init_boot.img")
        self.edt_boot.setFixedHeight(36)
        btn_pick = PushButton(FluentIcon.FOLDER, "选择文件")
        btn_pick.setFixedHeight(36)
        btn_pick.clicked.connect(self._pick_boot)
        row_boot.addWidget(self.edt_boot, 1)
        row_boot.addWidget(btn_pick)
        lay.addLayout(row_boot)

        # Manager Selection
        row_mgr = QHBoxLayout()
        row_mgr.setSpacing(12)
        self.combo_mgr = ComboBox()
        self.combo_mgr.addItems([m["name"] for m in ROOT_MANAGERS])
        self.combo_mgr.setFixedHeight(36)
        
        row_mgr.addWidget(self.combo_mgr, 1)
        lay.addLayout(row_mgr)

        # Partition Selection
        row_part = QHBoxLayout()
        row_part.setSpacing(12)
        part_label = QLabel("刷写分区：")
        part_label.setStyleSheet("font-size: 14px; font-weight: 500;")
        self.combo_part = ComboBox()
        self.combo_part.addItems(["boot", "init_boot"])
        self.combo_part.setFixedHeight(36)
        row_part.addWidget(part_label)
        row_part.addWidget(self.combo_part, 1)
        lay.addLayout(row_part)

        parent_layout.addWidget(card)

    def _build_action_card(self, parent_layout):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        
        self.btn_start = PrimaryPushButton("开始 Root")
        self.btn_start.setFixedHeight(44)
        self.btn_start.setIcon(FluentIcon.PLAY)
        self.btn_start.setIconSize(QSize(16, 16))
        self.btn_start.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.btn_start.clicked.connect(self._start)
        
        self.btn_cancel = PushButton(FluentIcon.CLOSE, "终止任务")
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        
        lay.addWidget(self.btn_start)
        lay.addSpacing(12)
        lay.addWidget(self.btn_cancel)
        
        parent_layout.addWidget(card)

    def _build_log_card(self, parent_layout):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)

        head_lay = QHBoxLayout()
        icon = QLabel("📝")
        icon.setStyleSheet("font-size: 20px;")
        title = QLabel("执行日志")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        head_lay.addWidget(icon)
        head_lay.addWidget(title)
        head_lay.addStretch(1)
        
        self.btn_clear_log = PushButton(FluentIcon.DELETE, "清空")
        self.btn_clear_log.setFixedHeight(30)
        self.btn_clear_log.clicked.connect(lambda: self.log.clear_log())
        head_lay.addWidget(self.btn_clear_log)
        
        lay.addLayout(head_lay)

        self.log = LogWidget()
        lay.addWidget(self.log, 1)
        parent_layout.addWidget(card, 1)

    def _start(self):
        if self._thread and self._thread.isRunning():
            InfoBar.info("提示", "已有任务在执行中", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return

        boot_img = (self.edt_boot.text() or "").strip()
        if not boot_img:
            InfoBar.warning("提示", "请先选择boot.img或init_boot.img", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return
        if not Path(boot_img).exists():
            InfoBar.warning("提示", "boot.img 路径不存在", parent=self, position=InfoBarPosition.TOP, isClosable=True)
            return

        idx = int(self.combo_mgr.currentIndex())
        mgr = ROOT_MANAGERS[idx]
        mgr_name = mgr.get("name", "")
        mgr_path = str(mgr.get("path", ""))
        mgr_pkg = str(mgr.get("package_name", ""))
        part = str(self.combo_part.currentText() or "boot").strip() or "boot"
        self._current_boot_name = Path(boot_img).name

        self.log.clear_log()
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._thread = _GuidedRootWorker(
            boot_img=boot_img,
            manager_name=mgr_name,
            manager_path=mgr_path,
            package_name=mgr_pkg,
            flash_partition=part,
            adb_path=self._adb,
            fastboot_path=self._fastboot,
            parent=self,
        )
        self._worker = self._thread  # 兼容旧引用

        self._thread.log.connect(self._on_worker_log)
        self._thread.step_start.connect(self.log.start_step)
        self._thread.step_finish.connect(self.log.finish_step)
        self._thread.result_ready.connect(self._on_finished)
        self._thread.result_ready.connect(self._thread.quit)
        self._thread.result_ready.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        InfoBar.info("开始", "Root 向导已启动，请按日志提示操作", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        self._thread.start()

    def _on_worker_log(self, msg: str):
        """拦截日志消息，检测关键提示并弹出弹窗"""
        self.log.append_log(msg)

        # 检测安装失败
        if "安装失败" in msg and "请手动安装" in msg:
            self._show_notification("APK 安装失败，请手动安装 APK 后重试。\n\n"
                                    "APK 位于手机存储中：\n"
                                    "/storage/emulated/0/MemeKit/root_manager.apk")

        # 检测"修补 boot"提示
        if ("请在 Root 管理器中选择" in msg and "选择并修补镜像" in msg) or \
           ("并选择以下文件" in msg) or \
           ("修补完成后，工具箱会自动进行下一步流程" in msg):
            if "修补完成后，工具箱会自动进行下一步流程" in msg:
                # 聚合显示完整提示，使用实际文件名
                boot_name = getattr(self, '_current_boot_name', 'boot.img')
                popup_text = (
                    "请在 Root 管理器中选择：'选择并修补镜像'\n\n"
                    f"并选择以下文件：\n"
                    f"/storage/emulated/0/MemeKit/{boot_name}\n\n"
                    "修补完成后，工具箱会自动进行下一步流程，无需操作手机"
                )
                self._show_notification(popup_text)

        # 检测"Root 成功"提示
        if "恭喜你，成功获取到了Root权限" in msg:
            self._show_notification("恭喜你，成功获取到了Root权限！")

    def _show_notification(self, text: str):
        """显示关键日志弹窗（模糊背景 + Dialog 样式）"""
        main_win = self.window()
        show_blur_info(main_win, "提示", text)

    def _on_finished(self, code):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if code == 0:
            InfoBar.success("完成", "Root 流程执行完毕", parent=self, position=InfoBarPosition.TOP)
        elif code == -2:
            InfoBar.info("已取消", "任务已取消", parent=self, position=InfoBarPosition.TOP, isClosable=True)
        else:
            InfoBar.error("失败", "Root 流程遇到错误，请查看日志", parent=self, position=InfoBarPosition.TOP)

    def _on_thread_finished(self):
        self._thread = None
        self._worker = None

    def _cancel(self):
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass

        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(100)
        except Exception:
            pass

    def _pick_boot(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 boot 镜像", "", "镜像 (*.img);;所有文件 (*.*)")
        if path:
            self.edt_boot.setText(path)

    def _refresh_device_info(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self.btn_refresh.setEnabled(False)
            self.btn_refresh.setText("刷新中...")
        except Exception:
            pass

        # 旧线程通过 finished -> deleteLater 自动清理，无需手动 cleanup
        self._refresh_thread = _RootRefreshThread(self)
        self._refresh_thread.result_ready.connect(self._on_refresh_info_finished)
        self._refresh_thread.finished.connect(self._refresh_thread.deleteLater)
        self._refresh_thread.start()

    def _on_refresh_info_finished(self, result: dict):
        self._refreshing = False
        self._refresh_thread = None
        try:
            self.btn_refresh.setEnabled(True)
            self.btn_refresh.setText("刷新状态")
        except Exception:
            pass

        try:
            mode = result.get("mode", "")
            serial = result.get("serial", "")
            if mode != "system" or not serial:
                self.lbl_dev.setText("未检测到系统模式设备")
                self.lbl_dev.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff4d4f;")
                self.lbl_suggest.setText("分区建议：-")
                return

            brand = result.get("brand", "")
            model = result.get("model", "")
            android = result.get("android", "")
            rom = result.get("rom", "")
            kernel = result.get("kernel", "")

            self.lbl_dev.setText(f"{brand} {model} | Android {android} | {rom}\n内核: {kernel}")
            self.lbl_dev.setStyleSheet("font-size: 15px; font-weight: 500; color: #00b42a;")

            suggested = "boot"
            try:
                k0 = kernel.split("-", 1)[0]
                parts = k0.split(".")
                major = int(parts[0]) if len(parts) > 0 else 0
                minor = int(parts[1]) if len(parts) > 1 else 0
                if major > 5 or (major == 5 and minor >= 15):
                    suggested = "init_boot"
            except Exception:
                suggested = "boot"

            self.lbl_suggest.setText(f"分区建议：内核 < 5.15 推荐 boot；内核 ≥ 5.15 推荐 init_boot\n当前建议：{suggested}")
            try:
                if suggested in ("boot", "init_boot"):
                    self.combo_part.setCurrentText(suggested)
            except Exception:
                pass
        except Exception:
            try:
                self.lbl_dev.setText("获取设备信息失败")
                self.lbl_dev.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff4d4f;")
            except Exception:
                pass

    def _start_device_watcher(self):
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._on_watch_tick)
        self._watch_timer.start(2000)
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
        self._watch_tick_thread = _RootWatchTickThread(self)
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
            self._refresh_device_info()
        if cur != self._last_watch_state:
            self._last_watch_state = cur
            self._refresh_device_info()

    def cleanup(self):
        try:
            if hasattr(self, '_watch_timer') and self._watch_timer is not None:
                self._watch_timer.stop()
                self._watch_timer.deleteLater()
                self._watch_timer = None
        except Exception:
            pass
        try:
            if getattr(self, '_watch_tick_thread', None) and self._watch_tick_thread.isRunning():
                self._watch_tick_thread.quit()
                self._watch_tick_thread.wait(100)
        except Exception:
            pass
        # 兼容旧 _watch_worker 清理
        try:
            if getattr(self, '_watch_worker', None):
                try:
                    self._watch_worker.stop()
                except Exception:
                    pass
                if self._watch_worker.isRunning():
                    self._watch_worker.quit()
                    self._watch_worker.wait(100)
        except Exception:
            pass
        try:
            if self._refresh_thread and self._refresh_thread.isRunning():
                self._refresh_thread.quit()
                self._refresh_thread.wait(200)
        except Exception:
            pass
        try:
            if self._worker:
                self._worker.stop()
        except Exception:
            pass
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(200)
        except Exception:
            pass
