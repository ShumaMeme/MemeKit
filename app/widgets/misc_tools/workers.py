import os
import subprocess
import time
import uuid
from typing import Optional

from PySide6.QtCore import QThread, Signal


def resolve_bin(path_like, fallback_name: str) -> str:
    try:
        if path_like and hasattr(path_like, 'exists') and path_like.exists():
            return str(path_like)
    except Exception:
        pass
    return fallback_name


class ProcWorker(QThread):
    output = Signal(str)
    result_ready = Signal(int)

    def __init__(self, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        code = -1
        proc: Optional[subprocess.Popen] = None
        try:
            popen_kwargs = {}
            try:
                if os.name == 'nt':
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    popen_kwargs = {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
            except Exception:
                pass

            proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                **popen_kwargs,
            )
            for line in iter(proc.stdout.readline, ''):
                if self._stop:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
                self.output.emit(line.rstrip('\r\n'))
            try:
                code = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.output.emit("执行超时（120秒），正在终止进程...")
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait()
                except Exception:
                    pass
        except FileNotFoundError:
            self.output.emit("未找到可执行文件，请检查工具是否存在。")
        except Exception as e:
            self.output.emit(f"执行失败：{e}")
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                        proc.wait()
            except Exception:
                pass
        finally:
            if proc and proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            self.result_ready.emit(code)


class BootFixWorker(QThread):
    log = Signal(str)
    result_ready = Signal(bool, str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)

    def __init__(self, adb_path: str, fastboot_path: str, abl_img: str, wait_secs: int = 25, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path or 'adb'
        self.fastboot_path = fastboot_path or 'fastboot'
        self.abl_img = abl_img
        self.wait_secs = wait_secs

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

    def _run_cmd(self, cmd, timeout=120):
        try:
            self.log.emit('执行: ' + ' '.join(cmd))
        except Exception:
            pass
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=timeout,
            **self._silent_kwargs(),
        )
        out = (proc.stdout or '').strip()
        if out:
            for line in out.splitlines():
                self.log.emit(line)

    def run(self):
        try:
            import uuid
            if not os.path.exists(self.abl_img):
                raise RuntimeError(f'未找到修复镜像: {self.abl_img}')
            
            sid_reboot = str(uuid.uuid4())
            self.step_start.emit(sid_reboot, '重启到 Fastboot')
            # self.log.emit('正在重启到 Fastboot (adb reboot fastboot)...')
            self._run_cmd([self.adb_path, 'reboot', 'fastboot'], timeout=30)
            self.step_finish.emit(sid_reboot, True, "")
            
            self.log.emit(f'等待设备进入 Fastboot （约 {self.wait_secs} 秒）...')
            time.sleep(self.wait_secs)
            
            sid_flash_a = str(uuid.uuid4())
            self.step_start.emit(sid_flash_a, '刷写 abl_a')
            # self.log.emit('开始刷写 abl_a ...')
            self._run_cmd([self.fastboot_path, 'flash', 'abl_a', self.abl_img], timeout=120)
            self.step_finish.emit(sid_flash_a, True, "")
            
            sid_flash_b = str(uuid.uuid4())
            self.step_start.emit(sid_flash_b, '刷写 abl_b')
            # self.log.emit('开始刷写 abl_b ...')
            self._run_cmd([self.fastboot_path, 'flash', 'abl_b', self.abl_img], timeout=120)
            self.step_finish.emit(sid_flash_b, True, "")
            
            sid_done = str(uuid.uuid4())
            self.step_start.emit(sid_done, '重启回系统')
            # self.log.emit('重启回系统 ...')
            self._run_cmd([self.fastboot_path, 'reboot'], timeout=30)
            self.step_finish.emit(sid_done, True, "")
            
            self.log.emit('修复已完成，设备正在重启回系统')
            self.result_ready.emit(True, '修复已完成，设备正在重启回系统')
        except subprocess.CalledProcessError as e:
            msg = (e.stdout or e.stderr or str(e)) if hasattr(e, 'stdout') else str(e)
            self.log.emit(msg)
            self.result_ready.emit(True, '修复流程已结束')
        except Exception as e:
            self.log.emit(str(e))
            self.result_ready.emit(True, '修复流程已结束')


class GoogleLockWorker(QThread):
    log = Signal(str)
    result_ready = Signal(bool, str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)

    def __init__(self, adb_path: str, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path or 'adb'

    def run(self):
        try:
            import uuid
            sid_frp = str(uuid.uuid4())
            self.step_start.emit(sid_frp, "执行 FRP 清除 (需Root)")
            # self.log.emit("尝试请求 Root 权限并执行 FRP 清除...")
            dd_cmd = "dd if=/dev/zero of=/dev/block/bootdevice/by-name/frp"
            cmd = [self.adb_path, 'shell', f"su -c '{dd_cmd}'"]

            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            self.log.emit(f"执行: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )

            output = proc.stdout.strip()
            if output:
                self.log.emit(f"命令输出: {output}")

            if "permission denied" in output.lower() or "not found" in output.lower():
                self.step_finish.emit(sid_frp, False, "权限拒绝或命令未找到")
                raise RuntimeError("执行失败，请确认设备已 Root 并授权 Shell 获取 Root 权限。")
            
            self.step_finish.emit(sid_frp, True, "")

            sid_reboot = str(uuid.uuid4())
            self.step_start.emit(sid_reboot, "重启设备")
            # self.log.emit("正在重启设备...")
            subprocess.run(
                [self.adb_path, 'reboot'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self.step_finish.emit(sid_reboot, True, "")

            self.result_ready.emit(True, "移除指令执行完成，设备正在重启。")
        except Exception as e:
            self.log.emit(f"发生错误: {e}")
            self.result_ready.emit(False, str(e))


class MagiskRemoveModulesWorker(QThread):
    log = Signal(str)
    result_ready = Signal(bool, str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)

    def __init__(self, adb_path: str, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path or 'adb'

    def run(self):
        try:
            import uuid
            sid_remove = str(uuid.uuid4())
            self.step_start.emit(sid_remove, "移除 Magisk 模块")
            # self.log.emit("尝试执行 Magisk 模块移除指令...")
            cmd = [self.adb_path, 'shell', 'Magisk', '--remove-modules']

            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            self.log.emit(f"执行: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )

            output = proc.stdout.strip()
            if output:
                self.log.emit(f"命令输出: {output}")

            if "not found" in output.lower() or "inaccessible" in output.lower():
                self.step_finish.emit(sid_remove, False, "命令未找到或不可用")
                raise RuntimeError("执行失败，可能是未安装 Magisk 或指令不支持。")
            
            self.step_finish.emit(sid_remove, True, "")

            sid_reboot = str(uuid.uuid4())
            self.step_start.emit(sid_reboot, "重启设备")
            # self.log.emit("正在重启设备...")
            subprocess.run(
                [self.adb_path, 'reboot'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self.step_finish.emit(sid_reboot, True, "")

            self.result_ready.emit(True, "指令执行完成，设备正在重启。")
        except Exception as e:
            self.log.emit(f"发生错误: {e}")
            self.result_ready.emit(False, str(e))


class _ExecPipelineWorker(QThread):
    """后台线程：检测设备 → 验证模式 → 执行命令。QThread 子类化确保 Cython 编译后安全。"""
    output = Signal(str)
    output_colored = Signal(str, str, bool)  # line, color, bold
    notify_error = Signal(str, str, int)    # title, message, duration_ms
    notify_success = Signal(str, str, int)  # title, message, duration_ms
    result_ready = Signal()

    _MODE_COMPAT = {
        "system": {"system"},
        "bootloader": {"bootloader"},
        "fastbootd": {"fastbootd", "bootloader"},
        "any": {"system", "sideload", "fastbootd", "bootloader"},
    }

    _MODE_CN = {
        "system": "系统",
        "sideload": "Sideload",
        "fastbootd": "FastbootD",
        "bootloader": "Bootloader",
    }

    def __init__(self, cmd: list, name: str, required_mode: str,
                 mode_compat: dict = None, adb_path: str = "adb",
                 fastboot_path: str = "fastboot", parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.name = name
        self.required_mode = required_mode
        self._mode_compat = mode_compat or self._MODE_COMPAT
        self.adb_path = adb_path or "adb"
        self.fastboot_path = fastboot_path or "fastboot"
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def _emit_error(self, text: str):
        self.output_colored.emit(text, "#f53f3f", True)

    def _detect_connection_mode(self):
        """重写的设备检测：优先使用系统 adb/fastboot（和用户配置），避免和其他服务冲突。"""
        out = ""
        try:
            # 用当前配置的 adb 执行 devices
            proc = subprocess.run(
                [self.adb_path, "devices"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            out = proc.stdout or ""
        except Exception:
            out = ""

        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                state = parts[1]
                if state == "device":
                    return "system", parts[0]
                if state == "sideload":
                    return "sideload", parts[0]
                if state in ("offline", "unauthorized"):
                    return "offline", parts[0]

        # 再检测 fastboot
        try:
            proc = subprocess.run(
                [self.fastboot_path, "devices"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            out = proc.stdout or ""
        except Exception:
            out = ""
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
                        capture_output=True, text=True, timeout=2,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    )
                    info = (p.stdout or "") + (p.stderr or "")
                    if "yes" in info.lower():
                        return "fastbootd", serial
                except Exception:
                    pass
                return "bootloader", serial

        return "none", ""

    def run(self):
        try:
            # 1. 检测设备
            mode, serial = self._detect_connection_mode()

            # 2. 连接状态判断
            if mode == "none":
                self._emit_error("[错误] 未检测到已连接设备，请确认USB连接。")
                self.notify_error.emit("设备未连接", "未检测到已连接设备，请确认USB连接。", 3000)
                self.result_ready.emit()
                return

            if mode == "offline":
                self._emit_error("[错误] 设备已连接但未授权（离线），请在手机上授权USB调试。")
                self.notify_error.emit("设备未授权", "设备已连接但未授权，请在手机上授权USB调试。", 3000)
                self.result_ready.emit()
                return

            # 3. 模式匹配
            acceptable_modes = self._mode_compat.get(self.required_mode)
            if acceptable_modes is not None and mode not in acceptable_modes:
                mode_cn = self._MODE_CN.get(mode, mode)
                required_cn = self._MODE_CN.get(self.required_mode, self.required_mode)
                self._emit_error(
                    f"[错误] 当前设备模式为 {mode_cn}，但该指令需要 {required_cn} 模式。"
                )
                self.notify_error.emit(
                    "模式不匹配",
                    f"当前设备模式为 {mode_cn}，但该指令需要 {required_cn} 模式。",
                    4000,
                )
                self.result_ready.emit()
                return

            self.output.emit(f"[检查] 设备模式检查通过（当前: {mode}）")

            # 4. 执行命令
            code = self._run_cmd()

            # 5. 结果
            if code == 0:
                self.output_colored.emit(
                    f"[完成] {self.name} 执行成功 (exit code: 0)",
                    "#00b42a", True,
                )
                self.notify_success.emit(
                    "执行成功", f'"{self.name}" 已成功执行。', 3000
                )
            else:
                self._emit_error(f"[失败] {self.name} 执行失败 (exit code: {code})")
                self.notify_error.emit(
                    "执行失败",
                    f'"{self.name}" 执行失败，退出码: {code}。请查看日志获取详情。',
                    4000,
                )
            self.result_ready.emit()
        except Exception as e:
            self._emit_error(f"[异常] 执行过程发生错误: {e}")
            self.notify_error.emit("执行异常", f"{self.name}: {e}", 4000)
            self.result_ready.emit()

    def _run_cmd(self) -> int:
        """执行命令，逐行输出 stdout/stderr。返回 exit code。"""
        proc = None
        try:
            popen_kwargs = {}
            try:
                if os.name == 'nt':
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    popen_kwargs = {
                        'startupinfo': si,
                        'creationflags': subprocess.CREATE_NO_WINDOW,
                    }
            except Exception:
                pass

            proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                **popen_kwargs,
            )
            for line in iter(proc.stdout.readline, ''):
                if self._stop_flag:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break
                self.output.emit(line.rstrip('\r\n'))
            try:
                return proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self._emit_error("执行超时（120秒），正在终止进程...")
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
            self._emit_error("未找到可执行文件，请检查工具是否存在。")
            return 127
        except Exception as e:
            self._emit_error(f"执行失败：{e}")
            return 1
        finally:
            if proc and proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass


class FlashPartitionWorker(QThread):
    """后台线程：模式切换 + 刷写分区，QThread 子类化确保 Cython 编译后安全。"""
    output = Signal(str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)
    result_ready = Signal(int)

    def __init__(self, fastboot_path: str, target_mode: str,
                 flash_cmd: list, auto_switch: bool = True, parent=None):
        super().__init__(parent)
        self.fastboot_path = fastboot_path
        self.target_mode = target_mode
        self.flash_cmd = flash_cmd
        self.auto_switch = auto_switch
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

    def run(self):
        try:
            # Step 1: 模式切换
            if self.auto_switch:
                sid_mode = str(uuid.uuid4())
                self.step_start.emit(sid_mode, f"切换到 {self.target_mode} 模式")
                try:
                    if self.target_mode == 'fastbootd':
                        subprocess.check_call(
                            [self.fastboot_path, 'reboot', 'fastboot'],
                            **self._silent_kwargs(),
                        )
                    else:
                        subprocess.check_call(
                            [self.fastboot_path, 'reboot-bootloader'],
                            **self._silent_kwargs(),
                        )
                    self.output.emit("等待设备重连(7s)...")
                    time.sleep(7)
                    self.step_finish.emit(sid_mode, True, "")
                except Exception as e:
                    self.step_finish.emit(sid_mode, False, str(e))
                    self.output.emit(f"切换模式失败：{e}")
                    self.result_ready.emit(-1)
                    return

            # Step 2: 刷写分区
            part = self.flash_cmd[2] if len(self.flash_cmd) >= 3 else "?"
            sid_flash = str(uuid.uuid4())
            self.step_start.emit(sid_flash, f"刷入分区 {part}")

            proc = subprocess.Popen(
                self.flash_cmd,
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
                self.output.emit(line.rstrip('\r\n'))
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                code = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.output.emit("执行超时（120秒），正在终止进程...")
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait()
                except Exception:
                    pass
                code = -1

            self.step_finish.emit(sid_flash, code == 0, "" if code == 0 else f"Code {code}")
            self.result_ready.emit(code)
        except FileNotFoundError:
            self.output.emit("未找到可执行文件，请检查工具是否存在。")
            self.result_ready.emit(-1)
        except Exception as e:
            self.output.emit(f"执行失败：{e}")
            self.result_ready.emit(-1)
