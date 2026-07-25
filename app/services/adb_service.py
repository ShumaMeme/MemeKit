import subprocess
import re
import socket
import struct
import time
import uuid
import sys
from typing import Dict, List, Tuple
from pathlib import Path

from app import get_project_root

ROOT_DIR = get_project_root()
BIN_DIR = ROOT_DIR / "bin"
ADB_BIN = BIN_DIR / "adb.exe" if (BIN_DIR / "adb.exe").exists() else BIN_DIR / "adb"
FASTBOOT_BIN = BIN_DIR / "fastboot.exe" if (BIN_DIR / "fastboot.exe").exists() else BIN_DIR / "fastboot"


class AdbServerError(RuntimeError):
    pass


class _AdbServerClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 5037, timeout: float = 8.0):
        self._host = host
        self._port = int(port)
        self._timeout = float(timeout)

    def _connect(self) -> socket.socket:
        s = socket.create_connection((self._host, self._port), timeout=self._timeout)
        s.settimeout(self._timeout)
        return s

    @staticmethod
    def _encode_service(service: str) -> bytes:
        b = (service or "").encode("utf-8")
        return f"{len(b):04x}".encode("ascii") + b

    @staticmethod
    def _read_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise AdbServerError("adb server closed connection")
            buf.extend(chunk)
        return bytes(buf)

    def _read_status(self, sock: socket.socket) -> None:
        status = self._read_exact(sock, 4)
        if status == b"OKAY":
            return
        if status == b"FAIL":
            msg = self._read_string(sock)
            raise AdbServerError(msg or "adb server FAIL")
        raise AdbServerError(f"unexpected adb status: {status!r}")

    def _read_string(self, sock: socket.socket) -> str:
        ln_hex = self._read_exact(sock, 4)
        try:
            ln = int(ln_hex.decode("ascii"), 16)
        except Exception as e:
            raise AdbServerError(f"invalid length prefix: {ln_hex!r}") from e
        if ln <= 0:
            return ""
        data = self._read_exact(sock, ln)
        return data.decode("utf-8", errors="replace")

    def _request(self, service: str, *, timeout: float | None = None, expect_string: bool = True) -> str:
        s = self._connect()
        try:
            if timeout is not None:
                s.settimeout(float(timeout))
            s.sendall(self._encode_service(service))
            self._read_status(s)
            if not expect_string:
                return ""
            return self._read_string(s)
        finally:
            try:
                s.close()
            except Exception:
                pass

    def host_devices(self, *, timeout: float = 5.0) -> list[tuple[str, str]]:
        payload = self._request("host:devices", timeout=timeout, expect_string=True)
        out: list[tuple[str, str]] = []
        for line in (payload or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            out.append((parts[0].strip(), parts[1].strip()))
        return out

    def host_mdns_services(self, *, timeout: float = 5.0) -> str:
        return self._request("host:mdns:services", timeout=timeout, expect_string=True)

    def host_connect(self, hp: str, *, timeout: float = 10.0) -> str:
        return self._request(f"host:connect:{hp}", timeout=timeout, expect_string=True)

    def host_disconnect(self, hp: str | None = None, *, timeout: float = 10.0) -> str:
        if hp:
            return self._request(f"host:disconnect:{hp}", timeout=timeout, expect_string=True)
        return self._request("host:disconnect:", timeout=timeout, expect_string=True)

    def host_pair(self, hp: str, code: str, *, timeout: float = 15.0) -> str:
        return self._request(f"host:pair:{hp}:{code}", timeout=timeout, expect_string=True)

    def shell(self, serial: str, cmd: str, *, timeout: float = 20.0) -> str:
        s = self._connect()
        try:
            s.settimeout(float(timeout))
            s.sendall(self._encode_service(f"host:transport:{serial}"))
            self._read_status(s)
            s.sendall(self._encode_service(f"shell:{cmd}"))
            self._read_status(s)
            chunks: list[bytes] = []
            while True:
                try:
                    b = s.recv(64 * 1024)
                except socket.timeout:
                    break
                if not b:
                    break
                chunks.append(b)
            return b"".join(chunks).decode("utf-8", errors="ignore").strip()
        finally:
            try:
                s.close()
            except Exception:
                pass

    def _sync_open(self, serial: str, *, timeout: float = 30.0) -> socket.socket:
        s = self._connect()
        s.settimeout(float(timeout))
        s.sendall(self._encode_service(f"host:transport:{serial}"))
        self._read_status(s)
        s.sendall(self._encode_service("sync:"))
        self._read_status(s)
        return s

    @staticmethod
    def _sync_send_cmd(sock: socket.socket, cmd4: bytes, payload: bytes = b"") -> None:
        sock.sendall(cmd4 + struct.pack("<I", len(payload)) + payload)

    @staticmethod
    def _sync_recv_header(sock: socket.socket) -> tuple[bytes, int]:
        hdr = _AdbServerClient._read_exact(sock, 8)
        cmd4 = hdr[:4]
        ln = struct.unpack("<I", hdr[4:])[0]
        return cmd4, int(ln)

    def sync_list(self, serial: str, remote_dir: str, *, timeout: float = 20.0) -> list[dict]:
        s = self._sync_open(serial, timeout=timeout)
        try:
            self._sync_send_cmd(s, b"LIST", (remote_dir or "").encode("utf-8"))
            items: list[dict] = []
            while True:
                cmd4, ln = self._sync_recv_header(s)
                if cmd4 == b"DONE":
                    break
                if cmd4 == b"DENT":
                    dent = self._read_exact(s, 16 + ln)
                    mode, size, mtime = struct.unpack("<III", dent[:12])
                    name = dent[16:].decode("utf-8", errors="replace")
                    items.append({"name": name, "mode": int(mode), "size": int(size), "mtime": int(mtime)})
                    continue
                if cmd4 == b"FAIL":
                    msg = self._read_exact(s, ln).decode("utf-8", errors="replace")
                    raise AdbServerError(msg or "sync LIST fail")
                if ln > 0:
                    _ = self._read_exact(s, ln)
            return items
        finally:
            try:
                s.close()
            except Exception:
                pass

    def sync_pull_file(self, serial: str, remote: str, local: str, *, timeout: float = 600.0) -> None:
        s = self._sync_open(serial, timeout=timeout)
        try:
            self._sync_send_cmd(s, b"RECV", (remote or "").encode("utf-8"))
            with open(local, "wb") as f:
                while True:
                    cmd4, ln = self._sync_recv_header(s)
                    if cmd4 == b"DATA":
                        if ln:
                            f.write(self._read_exact(s, ln))
                        continue
                    if cmd4 == b"DONE":
                        break
                    if cmd4 == b"FAIL":
                        msg = self._read_exact(s, ln).decode("utf-8", errors="replace")
                        raise AdbServerError(msg or "sync RECV fail")
                    if ln:
                        _ = self._read_exact(s, ln)
        finally:
            try:
                s.close()
            except Exception:
                pass

    def sync_push_file(self, serial: str, local: str, remote: str, *, mode: int = 0o644, timeout: float = 600.0) -> None:
        s = self._sync_open(serial, timeout=timeout)
        try:
            r = (remote or "").encode("utf-8") + f",{int(mode)}".encode("utf-8")
            self._sync_send_cmd(s, b"SEND", r)
            with open(local, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self._sync_send_cmd(s, b"DATA", chunk)
            self._sync_send_cmd(s, b"DONE", struct.pack("<I", int(time.time())))
            cmd4, ln = self._sync_recv_header(s)
            if cmd4 == b"OKAY":
                if ln:
                    _ = self._read_exact(s, ln)
                return
            if cmd4 == b"FAIL":
                msg = self._read_exact(s, ln).decode("utf-8", errors="replace")
                raise AdbServerError(msg or "sync SEND fail")
            if ln:
                _ = self._read_exact(s, ln)
            raise AdbServerError("unexpected sync response")
        finally:
            try:
                s.close()
            except Exception:
                pass


def _adb_server(timeout: float = 8.0) -> _AdbServerClient:
    return _AdbServerClient(timeout=timeout)


def _ensure_adb_server_running() -> bool:
    try:
        _adb_server(timeout=1.0).host_devices(timeout=1.0)
        return True
    except Exception:
        pass
    try:
        run_adb(["start-server"], timeout=6)
    except Exception:
        pass
    try:
        _adb_server(timeout=2.0).host_devices(timeout=2.0)
        return True
    except Exception:
        return False


def _silent_kwargs():
    try:
        import os as _os
        if _os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return {'startupinfo': si, 'creationflags': subprocess.CREATE_NO_WINDOW}
    except Exception:
        pass
    return {}


def _run(cmd: List[str], timeout: int = 8) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, **_silent_kwargs())
        return result.stdout.decode(errors='ignore')
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _adb_bin() -> str:
    return str(ADB_BIN) if ADB_BIN.exists() else "adb"


def run_adb(args: List[str], timeout: int = 10, cwd: str = None) -> Tuple[int, str]:
    adb = _adb_bin()
    cmd = [adb] + list(args or [])
    try:
        kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        if cwd:
            kwargs['cwd'] = cwd
        kwargs.update(_silent_kwargs())
        r = subprocess.run(cmd, **kwargs)
        return int(r.returncode), (r.stdout or '').strip()
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    except FileNotFoundError:
        return 127, 'adb not found'
    except Exception as e:
        return 1, str(e)


def _normalize_host_port(host: str, port: str | int) -> str:
    h = str(host or '').strip()
    p = str(port or '').strip()
    if not h:
        return ''
    if ':' in h:
        return h
    if not p:
        return h
    return f"{h}:{p}"


def adb_kill_server() -> Tuple[int, str]:
    return run_adb(['kill-server'], timeout=10)


def adb_start_server() -> Tuple[int, str]:
    return run_adb(['start-server'], timeout=10)


def list_devices() -> List[str]:
    try:
        _ensure_adb_server_running()
        devs = _adb_server(timeout=5.0).host_devices(timeout=5.0)
        result = [s for (s, st) in devs if st == "device"]
        if result:
            return result
    except Exception:
        pass
    # subprocess 回退
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"

    max_retries = 2
    retry_delay = 0.3

    for attempt in range(max_retries):
        out = _run([adb, "devices"], timeout=5)
        if "daemon" in out.lower() and "start" in out.lower():
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue

        serials: List[str] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])

        if serials or attempt == max_retries - 1:
            return serials

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    return []


def list_all_devices() -> List[str]:
    """返回所有在线设备 serial，包括 ADB(system/sideload) 和 Fastboot(bootloader/fastbootd) 模式。

    用于设备选择器：设备重启到 Bootloader/Fastboot 后不会出现在 adb devices 中，
    但 fastboot devices 能检测到。合并两者确保设备选择器能列出所有在线设备。
    """
    return [s for s, _ in list_all_devices_with_mode()]


def list_all_devices_with_mode() -> List[Tuple[str, str]]:
    """返回所有在线设备 (serial, mode) 列表，包括模式标识。

    模式取值：system / sideload / bootloader / fastbootd
    用于设备选择器显示模式标识（如 "serial (Fastbootd)"）。
    """
    devices: List[Tuple[str, str]] = []
    seen: set = set()

    # 1. 获取 ADB 模式设备（state == "device" 或 "sideload"）
    try:
        _ensure_adb_server_running()
        devs = _adb_server(timeout=3.0).host_devices(timeout=3.0)
        for s, st in devs:
            if st in ("device", "sideload") and s not in seen:
                mode = "sideload" if st == "sideload" else "system"
                devices.append((s, mode))
                seen.add(s)
    except Exception:
        pass

    # subprocess 回退（覆盖 host_devices 失败的情况）
    if not devices:
        try:
            adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
            out = _run([adb, "devices"], timeout=3)
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] in ("device", "sideload"):
                    if parts[0] not in seen:
                        mode = "sideload" if parts[1] == "sideload" else "system"
                        devices.append((parts[0], mode))
                        seen.add(parts[0])
        except Exception:
            pass

    # 2. 获取 Fastboot 模式设备（bootloader/fastbootd）
    # 直接解析 fastboot devices 输出的状态字段，区分 bootloader 和 fastbootd
    try:
        fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"
        out = _run([fb, "devices"], timeout=2)
        if out:
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("(bootloader)"):
                    continue
                parts = line.split()
                if parts and len(parts) >= 2:
                    fb_serial = parts[0]
                    if fb_serial not in seen:
                        state = parts[1].lower()
                        if "fastbootd" in state:
                            mode = "fastbootd"
                        elif "fastboot" in state:
                            # 备用：用 getvar is-userspace 进一步确认
                            # 注意：fastboot getvar 输出在 stderr，必须用 _fastboot
                            try:
                                is_userspace = _fastboot(["getvar", "is-userspace"], timeout=2, serial=fb_serial)
                                mode = "fastbootd" if is_userspace and "yes" in is_userspace.lower() else "bootloader"
                            except Exception:
                                mode = "bootloader"
                        else:
                            mode = "bootloader"
                        devices.append((fb_serial, mode))
                        seen.add(fb_serial)
    except Exception:
        pass

    return devices


def _getprop(serial: str, key: str) -> str:
    if not serial:
        return ""
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    return _run([adb, "-s", serial, "shell", "getprop", key], timeout=3)


def _getprop_batch(serial: str, keys: list) -> dict:
    """批量查询多个 getprop 属性，用一次 ADB shell 调用替代 N 次子进程。

    将 N 次 adb 子进程往返（每次 150-400ms）压缩为 1 次 socket 调用，
    显著降低仪表盘刷新延迟。
    """
    if not serial or not keys:
        return {}
    try:
        parts = []
        for k in keys:
            parts.append('echo "\\x01{0}\\x01"; getprop "{0}"'.format(k))
        cmd = "; ".join(parts)
        out = adb_shell_serial(serial, cmd, timeout=10)
        result = {}
        current_key = None
        for line in out.splitlines():
            if "\x01" in line:
                # 提取 \x01key\x01 标记行
                stripped = line.strip("\x01\r\n ")
                if stripped in keys:
                    current_key = stripped
                    continue
            if current_key is not None:
                result[current_key] = line.strip()
                current_key = None
        # 补全缺失的 key（值为空）
        for k in keys:
            result.setdefault(k, "")
        return result
    except Exception:
        return {k: _getprop(serial, k) for k in keys}


def _shell_batch(serial: str, commands: dict) -> dict:
    """批量执行多个 shell 命令，用一次 ADB shell 调用替代 N 次。

    commands: {tag: cmd_str}
    返回: {tag: output_str}
    """
    if not serial or not commands:
        return {}
    try:
        parts = []
        for tag, cmd in commands.items():
            parts.append('echo "\\x02{0}\\x02"; {1}'.format(tag, cmd))
        cmd = "; ".join(parts)
        out = adb_shell_serial(serial, cmd, timeout=15)
        result = {}
        current_tag = None
        buf = []
        for line in out.splitlines():
            if "\x02" in line:
                if current_tag is not None:
                    result[current_tag] = "\n".join(buf)
                stripped = line.strip("\x02\r\n ")
                current_tag = stripped if stripped in commands else None
                buf = []
            elif current_tag is not None:
                buf.append(line)
        if current_tag is not None:
            result[current_tag] = "\n".join(buf)
        for tag in commands:
            result.setdefault(tag, "")
        return result
    except Exception:
        return {tag: _shell(serial, cmd) for tag, cmd in commands.items()}


def _shell(serial: str, cmd: str) -> str:
    if not serial:
        return ""
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    return _run([adb, "-s", serial, "shell", cmd], timeout=8)


def _adb_get_state(serial: str) -> str:
    try:
        if not serial:
            return ""
        _ensure_adb_server_running()
        out = _adb_server(timeout=2.0)._request(f"host-serial:{serial}:get-state", timeout=2.0, expect_string=True)
        return (out or "").strip()
    except Exception:
        adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
        return _run([adb, "-s", serial, "get-state"], timeout=2)


def _detect_fastboot_mode(target_serial: str = "") -> Tuple[str, str]:
    """检测 fastboot 设备的模式（bootloader 或 fastbootd）。

    使用 `fastboot devices` 输出的状态字段作为主要判断依据：
    - 输出格式：`<serial> fastboot` 或 `<serial> fastbootd`
    - fastbootd 模式会显示 "fastbootd"，bootloader 模式显示 "fastboot"

    若主检测失败，回退到 `getvar is-userspace` 命令（注意：fastboot getvar 的
    输出在 stderr，必须用 _fastboot 函数捕获，不能用 _run）。

    Args:
        target_serial: 目标设备 serial。若指定，只检测该设备；否则检测第一台 fastboot 设备。

    Returns:
        (mode, serial): mode 为 "bootloader"/"fastbootd"/""，serial 为检测到的设备 serial。
    """
    fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"
    try:
        fb_out = _run([fb, "devices"], timeout=2)
    except Exception:
        fb_out = ""

    if not fb_out or not fb_out.strip():
        return ("", "")

    # 解析 fastboot devices 输出，收集 (serial, state) 对
    devices: List[Tuple[str, str]] = []
    for line in fb_out.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("(bootloader)"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1].lower()))

    if not devices:
        return ("", "")

    # 优先匹配 target_serial
    matched = None
    if target_serial:
        for s, st in devices:
            if s == target_serial:
                matched = (s, st)
                break
    # 未匹配到目标，取第一个
    if matched is None:
        matched = devices[0]

    s, st = matched
    # 主要判断：fastboot devices 的状态字段
    if "fastbootd" in st:
        return ("fastbootd", s)
    if "fastboot" in st:
        # 状态为 fastboot，但需进一步确认是否为 fastbootd（某些 fastboot 版本不区分）
        # 关键：fastboot getvar 的输出在 stderr，必须用 _fastboot 函数（合并 stderr 到 stdout）
        # 用 _run 只会得到空字符串，导致 fastbootd 永远被误判为 bootloader
        try:
            is_userspace = _fastboot(["getvar", "is-userspace"], timeout=2, serial=s)
            if is_userspace and "yes" in is_userspace.lower():
                return ("fastbootd", s)
        except Exception:
            pass
        return ("bootloader", s)

    # 未知状态，尝试 getvar is-userspace（同样用 _fastboot 捕获 stderr）
    try:
        is_userspace = _fastboot(["getvar", "is-userspace"], timeout=2, serial=s)
        if is_userspace and "yes" in is_userspace.lower():
            return ("fastbootd", s)
    except Exception:
        pass

    return ("bootloader", s)


def _fastboot(cmds: List[str], timeout: int = 5, serial: str = "") -> str:
    """执行 fastboot 命令，支持自定义超时和指定设备 serial。

    注意：fastboot 的 getvar 等命令把设备响应写到 stderr 而非 stdout，
    因此这里必须用 stderr=subprocess.STDOUT 把两路输出合并，否则会丢失
    unlocked / current-slot 等关键信息。

    Args:
        cmds: fastboot 命令参数列表（如 ["getvar", "product"]）
        timeout: 超时秒数
        serial: 目标设备 serial。多台 fastboot 设备时必须指定，否则命令会失败或操作错误设备。
    """
    fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"
    full_cmds = [fb]
    if serial:
        full_cmds += ["-s", serial]
    full_cmds += cmds
    try:
        result = subprocess.run(
            full_cmds,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            **_silent_kwargs(),
        )
        return result.stdout.decode(errors='ignore')
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def run_fastboot(args: List[str], timeout: int = 10) -> Tuple[int, str]:
    """执行 fastboot 命令，返回 (returncode, output)，对标 run_adb。"""
    fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"
    cmd = [fb] + list(args or [])
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            **_silent_kwargs(),
        )
        return int(r.returncode), (r.stdout or '').strip()
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    except FileNotFoundError:
        return 127, 'fastboot not found'
    except Exception as e:
        return 1, str(e)


def _read_sys_value(serial: str, paths: List[str]) -> int:
    for path in paths:
        cmd = f"if [ -f {path} ]; then cat {path}; fi"
        out = _shell(serial, cmd)
        val = (out or "").strip()
        if not val or "No such file" in val or "Permission denied" in val:
            continue
        try:
            return int(float(val))
        except Exception:
            continue
    return 0


def _parse_int(val) -> int:
    """从字符串安全解析整数，失败返回 0。"""
    try:
        v = str(val or "").strip()
        if not v or "No such file" in v or "Permission denied" in v:
            return 0
        return int(float(v))
    except Exception:
        return 0


def _meminfo_value(meminfo: str, key: str) -> int:
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(\d+)", re.MULTILINE)
    match = pattern.search(meminfo or "")
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def _format_mem_size(kb: int) -> str:
    if kb <= 0:
        return "0 MB"
    gb = kb / (1024 * 1024)
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = kb / 1024
    return f"{mb:.0f} MB"


def _harmonize_capacity_pair(rated: int, full: int) -> Tuple[int, int]:
    if rated <= 0 or full <= 0:
        return rated, full
    if rated <= full:
        smaller, larger = rated, full
        swap = False
    else:
        smaller, larger = full, rated
        swap = True
    while larger / max(1, smaller) >= 8 and smaller < 10 ** 9:
        smaller *= 10
    if swap:
        return larger, smaller
    return smaller, larger


def _format_capacity(uah: int) -> str:
    if uah <= 0:
        return ""
    mah = uah / 1000
    if mah >= 1000:
        return f"{mah:,.0f} mAh"
    if mah >= 100:
        return f"{mah:.0f} mAh"
    return f"{mah:.1f} mAh"


def detect_connection_mode() -> Tuple[str, str]:
    """Return (mode, serial). mode in: system, sideload, fastbootd, bootloader, offline, none"""
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    found_serial = ""
    
    # 添加重试机制（与 list_devices 保持一致）
    max_retries = 2
    for attempt in range(max_retries):
        # 减少 ADB 超时时间到 2 秒（设备存在时响应很快）
        out = ""
        try:
            _ensure_adb_server_running()
            devs = _adb_server(timeout=2.0).host_devices(timeout=2.0)
            out = "List of devices attached\n" + "\n".join([f"{s}\t{st}" for (s, st) in devs])
        except Exception:
            out = ""
        if not out:
            out = _run([adb, "devices"], timeout=2)
        
        if out:
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            start = 1 if lines and lines[0].lower().startswith("list of devices") else 0
            for line in lines[start:]:
                if line.startswith("*"):
                    continue
                parts = line.split()
                if not parts:
                    continue
                serial = parts[0]
                state = parts[1] if len(parts) > 1 else ""
                found_serial = serial
                if state == "device":
                    return ("system", serial)
                if state == "sideload":
                    return ("sideload", serial)
                if state in ("offline", "unauthorized"):
                    return ("offline", serial)
        
        # 如果还没找到且不是最后一次尝试，等待后重试
        if not found_serial and attempt < max_retries - 1:
            time.sleep(0.3)

    fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"
    # 减少 Fastboot 超时时间到 2 秒
    out = _run([fb, "devices"], timeout=2)
    if out:
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            serial = parts[0]
            if serial.lower().startswith("(bootloader)"):
                continue

            # 使用统一的 fastbootd 检测函数
            # `fastboot devices` 输出会显示 "fastbootd" 或 "fastboot" 状态
            # 这是区分 bootloader 和 fastbootd 最可靠的方式
            state = parts[1].lower() if len(parts) >= 2 else ""
            if "fastbootd" in state:
                return ("fastbootd", serial)
            if "fastboot" in state:
                # 备用：用 getvar is-userspace 进一步确认
                # 关键：fastboot getvar 输出在 stderr，必须用 _fastboot（合并 stderr）而非 _run
                try:
                    is_userspace = _fastboot(["getvar", "is-userspace"], timeout=2, serial=serial)
                    if is_userspace and "yes" in is_userspace.lower():
                        return ("fastbootd", serial)
                except Exception:
                    pass
                return ("bootloader", serial)

    # Fallback: detect special USB/COM port modes (Windows)
    try:
        port_mode, port_id = _detect_special_port_mode()
        if port_mode != "none":
            return (port_mode, port_id)
    except Exception:
        pass

    return ("none", found_serial)


def _detect_special_port_mode() -> Tuple[str, str]:
    """Detect EDL(9008) / MTK BROM via Windows COM ports.

    Returns (mode, id):
    - ("edl", "COMx") for Qualcomm 9008
    - ("brom", "COMx") for MediaTek preloader/brom/vcom
    - ("none", "") otherwise
    """
    if not sys.platform.startswith("win"):
        return ("none", "")

    ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$items = @()
$items += Get-CimInstance Win32_SerialPort | Select-Object DeviceID, Name, PNPDeviceID
$items += Get-CimInstance Win32_PnPEntity | Where-Object {
    $_.Name -match '\(COM\d+\)' -or $_.Caption -match '\(COM\d+\)'
} | Select-Object @{Name='DeviceID';Expression={
    if ($_.Name -match '\((COM\d+)\)') { $matches[1] }
    elseif ($_.Caption -match '\((COM\d+)\)') { $matches[1] }
    else { '' }
}}, @{Name='Name';Expression={
    if ($_.Name) { $_.Name } elseif ($_.Caption) { $_.Caption } else { '' }
}}, PNPDeviceID
$items | ForEach-Object {
    $dev = [string]$_.DeviceID
    $name = [string]$_.Name
    $pnp = [string]$_.PNPDeviceID
    if ($dev) { Write-Output ($dev + '|' + $name + '|' + $pnp) }
}
"""
    cmd = ["powershell", "-NoProfile", "-Command", ps_script]
    out = _run(cmd, timeout=4)
    if not out:
        return ("none", "")

    seen: set[tuple[str, str]] = set()
    for raw in out.splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        parts = line.split("|", 2)
        com = (parts[0].strip() if len(parts) > 0 else "")
        name = (parts[1].strip() if len(parts) > 1 else "")
        pnp = (parts[2].strip() if len(parts) > 2 else "")
        if not re.match(r"^COM\d+$", com, re.IGNORECASE):
            continue
        key = (com.upper(), name)
        if key in seen:
            continue
        seen.add(key)

        text = f"{name} {pnp}".lower()
        text = text.replace("_", "-")

        # Qualcomm EDL / 9008
        if (
            "9008" in text
            or "qdloader" in text
            or "qualcomm hs-usb" in text
            or "qualcomm usb" in text
            or "emergency download" in text
            or ("qualcomm" in text and "edl" in text)
        ):
            return ("edl", com)

        # MediaTek BROM / Preloader / VCOM
        if (
            ("mediatek" in text or "mtk" in text)
            and (
                "preloader" in text
                or "brom" in text
                or "vcom" in text
                or "usb port" in text
                or "download port" in text
            )
        ):
            return ("brom", com)

    return ("none", "")


def get_device_info(serial: str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    def add(k, v):
        if v is None:
            v = ""
        info[k] = v.strip()

    add("serial", serial)

    # === 批量 getprop：20+ 次子进程调用 → 1 次 socket 调用 ===
    prop_keys = [
        "ro.product.brand", "ro.product.model", "ro.product.device",
        "ro.product.name", "ro.build.version.release", "ro.build.version.sdk",
        "ro.build.version.vndk", "ro.build.display.id", "ro.build.fingerprint",
        "ro.bootloader", "gsm.version.baseband", "ro.hardware",
        "ro.product.cpu.abi", "ro.product.cpu.abi2",
        "ro.boot.slot_suffix", "ro.boot.slot",
        "ro.boot.vbmeta.device_state", "ro.boot.flash.locked",
        "ro.boot.verifiedbootstate", "ro.debuggable", "ro.oem_unlock_supported",
        "ro.serialno", "ro.sf.lcd_density",
    ]
    props = _getprop_batch(serial, prop_keys)
    g = lambda k: props.get(k, "")

    add("brand", g("ro.product.brand"))
    add("model", g("ro.product.model"))
    add("device", g("ro.product.device"))
    add("product", g("ro.product.name"))
    add("android_version", g("ro.build.version.release"))
    add("sdk", g("ro.build.version.sdk"))
    add("vndk", g("ro.build.version.vndk"))
    add("build_display", g("ro.build.display.id"))
    add("fingerprint", g("ro.build.fingerprint"))
    add("bootloader", g("ro.bootloader"))
    add("baseband", g("gsm.version.baseband"))

    # === 批量 shell：15+ 次子进程调用 → 1 次 socket 调用 ===
    shell_cmds = {
        "battery": "dumpsys battery",
        "cpuinfo": "cat /proc/cpuinfo",
        "df": "df -h /data | tail -n 1",
        "meminfo": "cat /proc/meminfo",
        "kernel": "uname -r",
        "uptime": "cat /proc/uptime",
        "wm_size": "wm size",
        "wm_density": "wm density",
        "su": "which su",
        "magisk": "which magisk",
        "sys_mount": "mount | grep ' /system ' | grep rw",
        "sys_charge_full_design": "cat /sys/class/power_supply/battery/charge_full_design 2>/dev/null || cat /sys/class/power_supply/BAT0/charge_full_design 2>/dev/null",
        "sys_charge_full": "cat /sys/class/power_supply/battery/charge_full 2>/dev/null || cat /sys/class/power_supply/BAT0/charge_full 2>/dev/null",
        "sys_mmc_type": "cat /sys/block/mmcblk0/device/type 2>/dev/null || cat /sys/block/mmcblk1/device/type 2>/dev/null",
        "sys_sda_type": "cat /sys/block/sda/device/type 2>/dev/null || cat /sys/block/sdb/device/type 2>/dev/null",
        "sys_sda_exists": "ls /sys/block/sda 2>/dev/null",
        "sys_mmc_exists": "ls /sys/block/mmcblk0 2>/dev/null",
    }
    sh = _shell_batch(serial, shell_cmds)
    s = lambda t: sh.get(t, "")

    # battery
    battery_dump = s("battery")
    battery_level = ""
    for line in battery_dump.splitlines():
        line = line.strip()
        if line.lower().startswith("level:"):
            battery_level = line.split(":", 1)[-1].strip()
            break
    add("battery", battery_level)

    # CPU
    cpu_model = ""
    cpuinfo = s("cpuinfo")
    if cpuinfo:
        for line in cpuinfo.splitlines():
            line = line.strip()
            if line.startswith("Hardware"):
                cpu_model = line.split(":", 1)[-1].strip()
                break
            elif line.startswith("Processor") and not cpu_model:
                cpu_model = line.split(":", 1)[-1].strip()
    if not cpu_model:
        cpu_model = g("ro.hardware")
    if not cpu_model:
        soc_id = _read_sys_value(serial, [
            "/sys/devices/system/cpu/soc0/serial_number",
            "/sys/devices/system/cpu/soc0/family",
            "/sys/devices/system/cpu/soc0/id"
        ])
        if soc_id:
            cpu_model = str(soc_id)
    if not cpu_model:
        dmesg = _shell(serial, "dmesg | grep -i 'cpu\\|processor\\|soc' | head -5")
        if dmesg:
            for line in dmesg.splitlines():
                if any(keyword in line.lower() for keyword in ["mt", "snapdragon", "qualcomm", "mediatek", "dimensity"]):
                    match = re.search(r'(MT\d+\w*|SDM\d+\w*|SM\d+\w*|Snapdragon\s+\w+|Dimensity\s+\d+\w*)', line, re.IGNORECASE)
                    if match:
                        cpu_model = match.group(1)
                        break
    if not cpu_model:
        cpu_abi = g("ro.product.cpu.abi")
        cpu_abi2 = g("ro.product.cpu.abi2")
        cpu_model = cpu_abi
        if cpu_abi2 and cpu_abi2 != cpu_abi:
            cpu_model = f"{cpu_abi} ({cpu_abi2})"
    add("cpu_info", cpu_model or "Unknown")

    # battery health
    rated_capacity = _parse_int(s("sys_charge_full_design"))
    full_capacity = _parse_int(s("sys_charge_full"))
    if rated_capacity and full_capacity:
        rated_capacity, full_capacity = _harmonize_capacity_pair(rated_capacity, full_capacity)
        health_pct = max(0, min(100, int(full_capacity / rated_capacity * 100)))
        add("battery_health_percent", str(health_pct))
    if rated_capacity:
        add("battery_rated_capacity", _format_capacity(rated_capacity))
    if full_capacity:
        add("battery_full_capacity", _format_capacity(full_capacity))

    # storage
    add("storage_data", s("df"))

    # memory
    meminfo = s("meminfo")
    mem_total = _meminfo_value(meminfo, "MemTotal")
    mem_available = _meminfo_value(meminfo, "MemAvailable")
    if not mem_available:
        mem_available = _meminfo_value(meminfo, "MemFree")
    if mem_total > 0:
        used = max(0, mem_total - (mem_available or 0))
        percent = int(used / mem_total * 100) if mem_total else 0
        percent = max(0, min(100, percent))
        detail = f"已用 {_format_mem_size(used)} / 总 {_format_mem_size(mem_total)}"
        add("memory_percent", str(percent))
        add("memory_summary", detail)

    # kernel
    kern = s("kernel")
    if not kern:
        kern = _shell(serial, "cat /proc/version")
    add("kernel", kern)

    # slot
    slot_suffix = g("ro.boot.slot_suffix").strip()
    slot = g("ro.boot.slot").strip()
    cur_slot = (slot or slot_suffix.replace("_", "")).strip()
    add("current_slot", cur_slot)

    # bootloader unlock status via props
    vb_state = g("ro.boot.vbmeta.device_state").strip()
    flash_locked = g("ro.boot.flash.locked").strip()
    verified_boot = g("ro.boot.verifiedbootstate").strip()
    unlock_enable = g("ro.debuggable").strip()

    unlocked = "unknown"
    if vb_state:
        unlocked = "unlocked" if vb_state.lower() == "unlocked" else "locked"
    elif flash_locked:
        unlocked = "unlocked" if flash_locked == "0" else "locked"
    elif verified_boot:
        vb = verified_boot.lower()
        if vb in ("orange", "yellow"):
            unlocked = "unlocked"
        elif vb == "green":
            unlocked = "locked"
    elif unlock_enable == "1":
        unlocked = "unlocked"
    add("bootloader_unlock", unlocked)

    # 代号
    add("codename", g("ro.product.device"))

    # 序列号
    device_serial = g("ro.serialno")
    if not device_serial:
        device_serial = _shell(serial, "cat /proc/cmdline | tr ' ' '\\n' | grep androidboot.serialno | cut -d'=' -f2")
    add("device_serial", device_serial.strip() if device_serial else "")

    # 已开机时间
    uptime = s("uptime")
    if uptime:
        try:
            uptime_seconds = float(uptime.split()[0])
        except (ValueError, IndexError):
            uptime_seconds = 0.0
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            uptime_str = f"{hours}小时 {minutes}分钟"
        else:
            uptime_str = f"{minutes}分钟"
        add("uptime", uptime_str)
    else:
        add("uptime", "")

    # 分辨率
    wm = s("wm_size")
    if wm and "Physical size:" in wm:
        resolution = wm.split("Physical size:")[1].strip()
        add("resolution", resolution)
    else:
        add("resolution", "")

    # 显示密度
    density = g("ro.sf.lcd_density")
    if not density:
        wm_density = s("wm_density")
        if wm_density and "Physical density:" in wm_density:
            density = wm_density.split("Physical density:")[1].strip()
    add("display_density", density)

    # 闪存类型
    emmc = s("sys_mmc_type")
    ufs = s("sys_sda_type")
    storage_type = ""
    if emmc and "mmc" in emmc.lower():
        storage_type = "eMMC"
    elif ufs and "ufs" in ufs.lower():
        storage_type = "UFS"
    else:
        if s("sys_sda_exists"):
            storage_type = "UFS"
        elif s("sys_mmc_exists"):
            storage_type = "eMMC"
    add("storage_type", storage_type)

    # Root权限状态
    root_status = "未检测到"
    su_check = s("su")
    if su_check and su_check.strip():
        root_status = "已Root"
    else:
        magisk = s("magisk")
        if magisk and magisk.strip():
            root_status = "已Root (Magisk)"
        else:
            system_rw = s("sys_mount")
            if system_rw and system_rw.strip():
                root_status = "已Root"
    add("root_status", root_status)

    return info


def reboot_to(target: str, serial: str = "") -> Tuple[bool, str]:
    """Reboot device to target: bootloader, recovery, fastbootd, system, edl.
    Auto-detect current mode and use adb or fastboot accordingly.
    若指定 serial，则仅对该设备执行（多设备连接时必需）。
    Returns (ok, message).
    """
    target = (target or "").strip().lower()
    if target not in ("bootloader", "recovery", "fastbootd", "system", "edl"):
        return False, f"不支持的目标: {target}"

    target_serial = (serial or "").strip()
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    fb = str(FASTBOOT_BIN) if FASTBOOT_BIN.exists() else "fastboot"

    def _ok(msg: str):
        return True, msg

    def _fail(msg: str):
        return False, msg

    # 关键修复：不再调用 detect_connection_mode()（会遍历所有设备并逐个 getvar，多设备时阻塞）
    # 而是针对 target_serial 精确检测其当前模式，避免阻塞和误操作其他设备
    mode = "none"
    detected_serial = ""

    if target_serial:
        # 1. 检查 target_serial 是否在 ADB 设备列表中
        try:
            _ensure_adb_server_running()
            devs = _adb_server(timeout=2.0).host_devices(timeout=2.0)
            for s, st in devs:
                if s == target_serial:
                    if st == "device":
                        mode = "system"
                    elif st == "sideload":
                        mode = "sideload"
                    elif st in ("offline", "unauthorized"):
                        mode = "offline"
                    else:
                        mode = "system"
                    detected_serial = target_serial
                    break
        except Exception:
            pass

        # 2. 若不在 ADB 列表，使用统一的 fastboot 模式检测函数
        if mode in ("none", ""):
            try:
                fb_mode, fb_serial = _detect_fastboot_mode(target_serial)
                if fb_mode:
                    mode = fb_mode
                    detected_serial = fb_serial
            except Exception:
                pass
    else:
        # 未指定 serial，回退到自动检测（单设备场景）
        mode, detected_serial = detect_connection_mode()

    serial = target_serial or detected_serial

    # If nothing connected
    if mode == "none" or not (serial or mode in ("fastbootd", "bootloader")):
        return _fail("未检测到已连接设备")

    # 若指定了 serial 但与检测到的不同，且当前为 ADB 模式，则用 -s 显式指定
    use_serial_flag = bool(serial) and mode in ("system", "sideload")

    # Map of actions per mode
    if mode in ("system", "sideload"):
        # Use ADB reboot variants
        adb_cmd = [adb]
        if use_serial_flag:
            adb_cmd += ["-s", serial]
        if target == "system":
            out = _run(adb_cmd + ["reboot"])  # simple reboot to system
            return _ok(out or "已重启到系统")
        if target == "bootloader":
            out = _run(adb_cmd + ["reboot", "bootloader"])
            return _ok(out or "正在重启到 Bootloader")
        if target == "fastbootd":
            out = _run(adb_cmd + ["reboot", "fastboot"])  # userspace fastbootd
            return _ok(out or "正在重启到 FastbootD")
        if target == "recovery":
            out = _run(adb_cmd + ["reboot", "recovery"])
            return _ok(out or "正在重启到 Recovery")
        if target == "edl":
            # Some devices may accept this; otherwise user must enter from fastboot
            out = _run(adb_cmd + ["reboot", "edl"])
            if out:
                return _ok(out)
            return _ok("已尝试通过 ADB 进入 EDL（是否成功取决于设备支持）")

    # Fastboot/Bootloader family
    if mode in ("fastbootd", "bootloader"):
        # fastboot 模式下，若有多设备需要用 -s serial 指定
        fb_cmd = [fb]
        if serial:
            fb_cmd += ["-s", serial]
        if target == "system":
            out = _run(fb_cmd + ["reboot"], timeout=10)
            return _ok(out or "正在重启到系统")
        if target == "bootloader":
            out = _run(fb_cmd + ["reboot-bootloader"], timeout=10) if mode != "bootloader" else ""
            return _ok(out or "已在 Bootloader 或正在进入 Bootloader")
        if target == "fastbootd":
            # Enter userspace fastboot
            out = _run(fb_cmd + ["reboot", "fastboot"], timeout=10)  # fastboot reboot fastboot
            return _ok(out or "正在重启到 FastbootD")
        if target == "recovery":
            # Not universally supported, but commonly available
            out = _run(fb_cmd + ["reboot", "recovery"], timeout=10)
            if out:
                return _ok(out)
            # Fallback OEM command
            out2 = _run(fb_cmd + ["oem", "reboot-recovery"], timeout=10)  # vendor specific
            return _ok(out2 or "已尝试进入 Recovery（是否成功取决于设备支持）")
        if target == "edl":
            # Qualcomm devices (OnePlus) often support either command
            out = _run(fb_cmd + ["oem", "edl"], timeout=10)  # try OEM first
            if out:
                return _ok(out)
            out2 = _run(fb_cmd + ["edl"], timeout=10)  # standard new fastboot cmd
            return _ok(out2 or "已尝试进入 EDL（是否成功取决于设备支持）")

    return _fail("未能执行重启命令")


# -------- ADB File Ops --------
def list_dir(path: str, serial: str = "") -> Tuple[List[Dict[str, str]], str]:
    """List directory on device. Returns (items, err).
    Each item: {name, size, type: 'dir'|'file'}
    """
    p = path or "/"
    try:
        if not serial:
            serials = list_devices()
            serial = serials[0] if serials else ""
        if serial and _ensure_adb_server_running():
            # Fast path: avoid heavy `ls -l` parsing and avoid SYNC metadata overhead.
            # `ls -1p` appends '/' to dirs (toybox/busybox compatible in most ROMs).
            out = _adb_server(timeout=6.0).shell(serial, f"sh -c \"ls -1p '{p}' 2>/dev/null || toybox ls -1p '{p}' 2>/dev/null\"", timeout=6.0)
            if out and ("No such file" not in out) and ("Permission denied" not in out):
                items: List[Dict[str, str]] = []
                import re as _re
                for line in (out or "").splitlines():
                    name = (line or "").strip()
                    if not name:
                        continue
                    is_dir = name.endswith('/')
                    if is_dir:
                        name = name[:-1]
                    # 反转义 toybox/busybox ls 的 shell 转义（如 '\ ' -> ' ', '\(' -> '('）
                    # 避免含空格/特殊字符的文件名被错误转义导致后续操作失败
                    name = _re.sub(r'\\(.)', r'\1', name)
                    items.append({"name": name, "size": "-", "type": ("dir" if is_dir else "file")})
                return items, ""

            # Fallback: SYNC LIST for cases where shell `ls` is restricted/unavailable.
            entries = _adb_server(timeout=10.0).sync_list(serial, p, timeout=10.0)
            items2: List[Dict[str, str]] = []
            for e in entries:
                name = (e.get("name") or "").strip()
                if not name or name in (".", ".."): 
                    continue
                mode = int(e.get("mode") or 0)
                is_dir = bool(mode & 0o040000)
                items2.append({"name": name, "size": str(e.get("size") or "-"), "type": ("dir" if is_dir else "file")})
            return items2, ""
    except Exception:
        pass

    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    out = _run([adb, "shell", "ls", "-l", p], timeout=10)
    if out is None:
        out = ""
    if not out.strip():
        # try without -l
        out2 = _run([adb, "shell", "ls", p], timeout=10)
        if not out2.strip():
            return [], f"无法列出目录：{p}（设备未连接或权限不足）"
        items: List[Dict[str, str]] = []
        for line in out2.split():
            if not line:
                continue
            items.append({"name": line.strip(), "size": "-", "type": "file"})
        return items, ""
    items: List[Dict[str, str]] = []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.startswith("total "):
            continue
        # typical: drwxr-xr-x  2 root root     4096 Jan  1 00:00 Download
        parts = s.split()
        try:
            perm = parts[0]
            is_dir = perm.startswith('d')
            # size usually at index 4 (busybox/toybox may vary). Try last numeric before month name
            size = "-"
            for tok in parts[1:6]:
                if tok.isdigit():
                    size = tok
            name = parts[-1]
            items.append({"name": name, "size": size, "type": ("dir" if is_dir else "file")})
        except Exception:
            # fallback: whole line as name
            items.append({"name": s, "size": "-", "type": "file"})
    return items, ""


# -------- Mobile-side Ops (ADB shell) --------
def _adb_shell(args: List[str], timeout: int = 20, serial: str = "") -> str:
    try:
        if not serial:
            serials = list_devices()
            serial = serials[0] if serials else ""
        if serial and _ensure_adb_server_running():
            cmd = " ".join([str(x) for x in (args or [])])
            return _adb_server(timeout=float(timeout)).shell(serial, cmd, timeout=float(timeout))
    except Exception:
        pass
    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    return _run([adb, "shell"] + args, timeout=timeout)


def _sh_quote(s: str) -> str:
    t = str(s or "")
    if not t:
        return "''"
    if "'" not in t:
        return f"'{t}'"
    # close-open pattern: 'foo'"'"'bar'
    return "'" + t.replace("'", "'\"'\"'") + "'"


def adb_shell_serial(serial: str, args: List[str] | str, timeout: int = 20) -> str:
    """Execute a shell command on a specific device serial via adb server socket.

    args can be:
    - list[str]: will be shell-quoted and joined
    - str: passed as-is to shell
    """
    try:
        if not serial:
            return ""
        _ensure_adb_server_running()
        if isinstance(args, str):
            cmd = args
        else:
            cmd = " ".join([_sh_quote(x) for x in (args or [])])
        return _adb_server(timeout=float(timeout)).shell(serial, cmd, timeout=float(timeout))
    except Exception:
        adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
        if isinstance(args, str):
            return _run([adb, "-s", serial, "shell", args], timeout=timeout)
        return _run([adb, "-s", serial, "shell"] + list(args or []), timeout=timeout)


def adb_pm_path(serial: str, pkg: str, timeout: int = 6) -> str:
    out = adb_shell_serial(serial, ["pm", "path", str(pkg or "").strip()], timeout=timeout)
    remote = ""
    for line in (out or "").splitlines():
        s = (line or "").strip()
        if s.startswith("package:"):
            remote = s.split(":", 1)[1].strip()
            break
    return remote


def adb_pull_file_serial(serial: str, remote: str, local: str, timeout: int = 600) -> Tuple[bool, str]:
    try:
        if not serial:
            return False, "未检测到设备"
        _ensure_adb_server_running()
        _adb_server(timeout=float(timeout)).sync_pull_file(serial, remote, local, timeout=float(timeout))
        return True, "完成"
    except Exception as e:
        adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
        try:
            out = _run([adb, "-s", serial, "pull", remote, local], timeout=timeout)
            return True, out or "完成"
        except Exception:
            return False, str(e)


def adb_install_apk(serial: str, apk_path: str, *, reinstall: bool = False, downgrade: bool = False, timeout: int = 600) -> Tuple[bool, str]:
    """Install APK without invoking `adb install` subprocess.

    Strategy:
    - SYNC push to /data/local/tmp/<uuid>.apk
    - pm install [-r] [-d] <remote>
    - cleanup remote file (best-effort)
    """
    p = str(apk_path or "").strip()
    if not p:
        return False, "APK 路径为空"
    try:
        if not serial:
            return False, "未检测到设备"
        _ensure_adb_server_running()

        remote = f"/data/local/tmp/{uuid.uuid4().hex}.apk"
        _adb_server(timeout=float(timeout)).sync_push_file(serial, p, remote, timeout=float(timeout))

        flags: list[str] = []
        if reinstall:
            flags.append("-r")
        if downgrade:
            flags.append("-d")

        cmd = ["pm", "install"] + flags + [remote]
        out = adb_shell_serial(serial, cmd, timeout=timeout)
        ok = ("Success" in (out or "")) and ("Failure" not in (out or ""))
        # cleanup (ignore failure)
        try:
            adb_shell_serial(serial, ["rm", "-f", remote], timeout=10)
        except Exception:
            pass
        return ok, (out or "").strip()
    except Exception as e:
        return False, str(e)


def is_dir(path: str, serial: str = "") -> bool:
    out = _adb_shell([f"[ -d {_sh_quote(path)} ] && echo d || echo f"], timeout=6, serial=serial)
    return out.strip().startswith('d')


def mkdir_p(path: str, serial: str = "") -> Tuple[bool, str]:
    out = _adb_shell([f"mkdir -p {_sh_quote(path)}"], timeout=8, serial=serial)
    ok = True if (out is None or out.strip() == "") else True
    return ok, out or ""


def delete_path(path: str, serial: str = "") -> Tuple[bool, str]:
    out = _adb_shell([f"rm -rf {_sh_quote(path)} && echo __OK__"], timeout=20, serial=serial)
    if "__OK__" in (out or ""):
        return True, ""
    return False, (out or "删除失败").strip()


def move_path(src: str, dst_dir: str, serial: str = "") -> Tuple[bool, str]:
    # 同目录无需移动
    src_parent = src.rsplit('/', 1)[0] if '/' in src else ''
    if src_parent == dst_dir:
        return True, ""
    mkdir_p(dst_dir, serial=serial)
    out = _adb_shell([f"mv {_sh_quote(src)} {_sh_quote(dst_dir)}/ && echo __OK__"], timeout=30, serial=serial)
    if "__OK__" in (out or ""):
        return True, ""
    return False, (out or "移动失败").strip()


def copy_path(src: str, dst_dir: str, serial: str = "") -> Tuple[bool, str]:
    mkdir_p(dst_dir, serial=serial)
    out = _adb_shell([f"cp -r {_sh_quote(src)} {_sh_quote(dst_dir)}/ && echo __OK__ || toybox cp -r {_sh_quote(src)} {_sh_quote(dst_dir)}/ && echo __OK__"], timeout=120, serial=serial)
    if "__OK__" in (out or ""):
        return True, ""
    return False, (out or "复制失败").strip()


def rename_path(src: str, new_name: str, serial: str = "") -> Tuple[bool, str]:
    parent = src.rsplit('/', 1)[0] if '/' in src else '/'
    dst = parent + '/' + new_name
    out = _adb_shell([f"mv {_sh_quote(src)} {_sh_quote(dst)} && echo __OK__"], timeout=15, serial=serial)
    if "__OK__" in (out or ""):
        return True, ""
    return False, (out or "重命名失败").strip()


def stat_path(path: str, serial: str = "") -> dict:
    # Use stat if available; fallback to ls -ld and du -s
    info: dict = {"path": path}
    s = _adb_shell(["sh", "-c", f"stat -c '%F|%s|%a|%U|%G|%y' '{path}' || toybox stat -c '%F|%s|%a|%U|%G|%y' '{path}'"], timeout=8, serial=serial)
    if s and '|' in s:
        try:
            ftype, size, perm, user, group, mtime = s.strip().split('|', 5)
            info.update({"type": ftype, "size": size, "perm": perm, "user": user, "group": group, "mtime": mtime})
            return info
        except Exception:
            pass
    # Fallbacks
    ls = _adb_shell(["ls", "-ld", path], timeout=6, serial=serial)
    info["raw_ls"] = ls
    du = _adb_shell(["du", "-s", path], timeout=10, serial=serial)
    info["raw_du"] = du
    return info


def pull_path(remote: str, local_dest: str, serial: str = "") -> Tuple[bool, str]:
    """adb pull remote local_dest (支持文件或目录).

    直接使用 adb pull 子进程，原生支持文件和目录递归，最可靠。
    """
    if not serial:
        serials = list_devices()
        serial = serials[0] if serials else ""
    if not serial:
        return False, "未检测到设备"

    adb = str(ADB_BIN) if ADB_BIN.exists() else "adb"
    # adb pull 原生支持目录递归；确保本地目录存在
    try:
        import os as _os
        local_parent = _os.path.dirname(local_dest) or "."
        _os.makedirs(local_parent, exist_ok=True)
    except Exception:
        pass

    [adb, "-s", serial, "pull", remote, local_dest]
    code, out = run_adb(["-s", serial, "pull", remote, local_dest], timeout=3600)
    if code == 0:
        return True, out or "完成"
    return False, out or "拉取失败"


def push_path(local_path: str, remote_dir: str, serial: str = "") -> Tuple[bool, str]:
    """adb push local_path remote_dir (支持文件或目录).

    直接使用 adb push 子进程，原生支持文件和目录递归，最可靠。
    使用 cwd 切换到源文件父目录 + 相对路径，避免 adb 解析含中文的绝对路径时出错。
    """
    if not serial:
        serials = list_devices()
        serial = serials[0] if serials else ""
    if not serial:
        return False, "未检测到设备"

    lp = Path(local_path)
    if not lp.exists():
        return False, "本地文件不存在"

    # adb push 原生支持目录递归；确保远程目录存在
    r = remote_dir or '/storage/emulated/0'
    try:
        mkdir_p(r)
    except Exception:
        pass
    if lp.is_dir():
        # 目录：远程目标为目录路径（不带尾斜杠，adb 会在该目录下创建同名子目录）
        target = r.rstrip('/')
    else:
        # 文件：远程目标必须是 "目录/文件名" 完整路径，否则 adb 会把文件内容
        # 写成一个名为目录名的文件
        target = r.rstrip('/') + '/' + lp.name

    # 关键修复：cd 到源文件/文件夹的父目录，用相对路径 push
    # 避免 adb 解析含中文的绝对路径时路径被截断/错乱
    cwd = str(lp.parent) if lp.is_file() else str(lp.parent)
    push_name = lp.name  # 相对路径：文件夹名或文件名

    code, out = run_adb(
        ["-s", serial, "push", push_name, target],
        timeout=3600,
        cwd=cwd,
    )
    if code == 0:
        return True, out or "完成"
    return False, out or "推送失败"


def _mode_cn(mode: str) -> str:
    mapping = {
        "system": "系统",
        "sideload": "Sideload",
        "fastbootd": "FastbootD",
        "bootloader": "Bootloader",
        "edl": "9008 (EDL)",
        "brom": "BROM",
        "offline": "离线",
        "none": "未连接",
    }
    return mapping.get(mode, mode or "未知")


def connection_summary(serial: str = "") -> Dict[str, str]:
    target_serial = str(serial or "").strip()
    if target_serial:
        # 使用指定 serial 检测该设备的状态，避免总是返回第一台设备
        mode = "none"
        try:
            devs = _adb_server(timeout=2.0).host_devices(timeout=2.0)
            for s, st in devs:
                if s == target_serial:
                    if st == "device":
                        mode = "system"
                    elif st == "sideload":
                        mode = "sideload"
                    elif st in ("offline", "unauthorized"):
                        mode = "offline"
                    else:
                        mode = "system"
                    break
        except Exception:
            pass

        # 关键修复：若 target_serial 不在 ADB 列表，检查 fastboot 模式
        # 否则 fastbootd/bootloader 设备会被误判为"未连接"
        if mode in ("none", ""):
            try:
                fb_mode, fb_serial = _detect_fastboot_mode(target_serial)
                if fb_mode:
                    mode = fb_mode
                    target_serial = fb_serial
            except Exception:
                pass
        serial = target_serial
    else:
        mode, serial = detect_connection_mode()
    cn = _mode_cn(mode)
    serial = serial or ""
    summary: Dict[str, str] = {
        "mode": mode,
        "serial": serial,
        "connected": mode in ("system", "sideload", "fastbootd", "bootloader", "edl", "brom"),
        "status_conn": "",
        "status_mode": "",
        "status_line": "",
        "status_color": "#86909c",
        "banner_state": "disconnected",
    }
    if mode in ("system", "sideload"):
        summary["status_conn"] = f"设备：已连接（{cn}）"
        summary["status_mode"] = f"模式：{cn}"
        summary["status_line"] = f"已连接：{cn}"
        summary["status_color"] = "#00b42a"
        summary["banner_state"] = "connected"
    elif mode in ("fastbootd", "bootloader"):
        summary["status_conn"] = f"设备：已连接（{cn}）"
        summary["status_mode"] = f"模式：{cn}"
        summary["status_line"] = f"已连接：{cn}"
        summary["status_color"] = "#00b42a"
        summary["banner_state"] = "connected"
    elif mode in ("edl", "brom"):
        # Port-based modes: no ADB/Fastboot, but device exists at a serial port.
        port = f"（{serial}）" if serial else ""
        summary["status_conn"] = f"设备：已连接（端口{port}）"
        summary["status_mode"] = f"模式：{cn}"
        summary["status_line"] = f"已连接：{cn}{port}"
        summary["status_color"] = "#fa8c16"
        summary["banner_state"] = "connected"
    elif mode == "offline":
        summary["status_conn"] = "设备：已连接但未授权"
        summary["status_mode"] = "模式：离线"
        summary["status_line"] = "设备已连接但离线/未授权，请在手机上授权 USB 调试"
        summary["status_color"] = "#ff4d4f"
        summary["banner_state"] = "disconnected"
    else:
        summary["status_conn"] = "设备：未连接"
        summary["status_mode"] = "模式：未知"
        summary["status_line"] = "未发现已连接设备"
        summary["status_color"] = "#86909c"
        summary["banner_state"] = "disconnected"
    return summary


def collect_overall_info(serial: str = "") -> Dict[str, str]:
    # 如果指定了 serial，则使用它构造 summary，避免总是取第一台设备
    target_serial = str(serial or "").strip()
    if target_serial:
        # 验证设备在线并确定模式
        mode = "none"
        online_devs: List[tuple] = []
        try:
            online_devs = _adb_server(timeout=2.0).host_devices(timeout=2.0)
            for s, st in online_devs:
                if s == target_serial:
                    if st == "device":
                        mode = "system"
                    elif st == "sideload":
                        mode = "sideload"
                    elif st in ("offline", "unauthorized"):
                        mode = "offline"
                    else:
                        mode = "system"
                    break
        except Exception:
            pass

        # 若 target_serial 不在 ADB 设备列表中，检查是否处于 fastboot 模式
        # 设备重启到 fastboot/bootloader 后不会出现在 adb devices 中，但 fastboot devices 能检测到
        if mode in ("none", ""):
            try:
                fb_mode, fb_serial = _detect_fastboot_mode(target_serial)
                if fb_mode:
                    mode = fb_mode
                    target_serial = fb_serial
            except Exception:
                pass

        # 若仍为 none/offline，返回断开状态（此时设备确实不在线）
        if mode in ("none", "offline", ""):
            cn = _mode_cn(mode) if mode else "已断开"
            return {
                "connection_status": mode or "none",
                "serial": target_serial,
                "connected": False,
                "status_conn": f"设备：{cn}",
                "status_mode": f"模式：{cn}",
                "status_line": f"已断开：{target_serial}",
                "status_color": "#86909c",
                "banner_state": "disconnected",
            }

        # target_serial 确实在线，构造 summary
        summary = connection_summary()
        summary["mode"] = mode
        summary["serial"] = target_serial
        summary["connected"] = mode in ("system", "sideload", "fastbootd", "bootloader", "edl", "brom")
        cn = _mode_cn(mode)
        if mode in ("system", "sideload", "fastbootd", "bootloader"):
            summary["status_conn"] = f"设备：已连接（{cn}）"
            summary["status_mode"] = f"模式：{cn}"
            summary["status_line"] = f"已连接：{cn}"
            summary["status_color"] = "#00b42a"
            summary["banner_state"] = "connected"
    else:
        summary = connection_summary()
    mode = summary["mode"]
    serial = summary["serial"]
    info: Dict[str, str] = {"connection_status": mode, "serial": serial}
    if mode in ("system", "sideload") and serial:
        try:
            dev = get_device_info(serial)
            info.update(dev)
        except Exception:
            pass
    elif mode in ("fastbootd", "bootloader"):
        # Query via fastboot where possible (使用较短的超时)
        def clean_fastboot_output(output):
            """去除fastboot输出中的冗余前缀和后缀"""
            if not output:
                return output
            
            # 处理多行输出，只取第一行（fastboot getvar通常第一行是结果，后面是finished）
            lines = output.strip().split('\n')
            if not lines:
                return output
                
            first_line = lines[0].strip()
            
            # 去除 (bootloader) 前缀
            clean_output = first_line.replace("(bootloader) ", "")
            
            # 如果第一行包含finish，则截断
            if 'finish' in clean_output.lower():
                finish_pos = clean_output.lower().find('finish')
                clean_output = clean_output[:finish_pos].strip()
            
            return clean_output
        
        prod = _fastboot(["getvar", "product"], timeout=2, serial=target_serial) or ""
        prod = clean_fastboot_output(prod)
        # 提取 product: 后面的值，去除冗余前缀
        if "product:" in prod:
            product_value = prod.split("product:")[1].strip()
            info["product"] = product_value
        else:
            info["product"] = prod

        cur_slot = _fastboot(["getvar", "current-slot"], timeout=2, serial=target_serial) or ""
        cur_slot = clean_fastboot_output(cur_slot)
        # 提取 current-slot: 或 SLOT: 后面的值，去除冗余前缀
        if "current-slot:" in cur_slot:
            slot_value = cur_slot.split("current-slot:")[1].strip()
            info["current_slot"] = slot_value
        elif "SLOT:" in cur_slot:
            slot_value = cur_slot.split("SLOT:")[1].strip()
            info["current_slot"] = slot_value
        else:
            info["current_slot"] = cur_slot

        status = "unknown"
        # 使用 fastboot getvar unlocked 检测bootloader锁状态
        unlock_state = _fastboot(["getvar", "unlocked"], timeout=2, serial=target_serial) or ""
        unlock_state = clean_fastboot_output(unlock_state)
        if "yes" in unlock_state.lower():
            status = "unlocked"
        elif "no" in unlock_state.lower():
            status = "locked"

        if status == "unknown":
            # 备用方法：尝试 secure 变量
            boot_state = _fastboot(["getvar", "secure"], timeout=2, serial=target_serial) or ""
            boot_state = clean_fastboot_output(boot_state)
            if "no" in boot_state.lower():
                status = "unlocked"
            elif "yes" in boot_state.lower():
                status = "locked"
        info["bootloader_unlock"] = status
        # Not available in fastboot mode
        info.setdefault("battery", "-")
        info.setdefault("storage_data", "-")
        info.setdefault("kernel", "-")
        info.setdefault("android_version", "-")
    info.update(summary)
    return info
