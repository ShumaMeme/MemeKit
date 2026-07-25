import os
import subprocess
import time
import uuid

from PySide6.QtCore import QThread, Signal


def resolve_bin(path_like, fallback_name: str) -> str:
    try:
        if path_like and hasattr(path_like, 'exists') and path_like.exists():
            return str(path_like)
    except Exception:
        pass
    return fallback_name


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
