"""运行日志服务：基于 Python logging 的内存 + 文件日志记录。

设计目标：
- 默认启用，开箱即用，无需用户手动开启
- 记录软件全生命周期事件：启动、关闭、异常崩溃
- 记录 UI 操作：Tab 切换、文件选择
- 记录设备事件：连接、断开、模式切换
- 记录功能执行：刷写、Root、快指等操作的开始/成功/失败
- 写入内存 deque（O(1)、线程安全）+ 文件持久化（RotatingFileHandler）
- 软件退出后日志不丢失，下次启动自动加载历史日志
- 单条日志格式：[时间.毫秒] [级别] [模块] 消息
"""
import logging
import logging.handlers
import os
import sys
import time
import traceback
from collections import deque
from pathlib import Path

_LOGGER_NAME = "MemeKit"
_MAX_MEMORY_RECORDS = 2000
_MAX_LOG_FILE_BYTES = 2 * 1024 * 1024  # 单个日志文件最大 2MB
_BACKUP_COUNT = 3  # 保留 3 个备份文件

# 内存日志缓冲区（线程安全 deque）
_memory_records: deque = deque(maxlen=_MAX_MEMORY_RECORDS)
_logging_enabled = True  # 默认启用
_log_file_path: Path = None  # 持久化日志文件路径（init 后赋值）


def _get_log_dir() -> Path:
    """获取日志文件存储目录（用户 APPDATA/TraeToolbox）。"""
    try:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    except Exception:
        base = Path.home()
    log_dir = base / "TraeToolbox"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir


def _get_log_file_path() -> Path:
    """获取运行日志文件路径。"""
    global _log_file_path
    if _log_file_path is None:
        _log_file_path = _get_log_dir() / "runtime.log"
    return _log_file_path


class _MemoryLogHandler(logging.Handler):
    """将日志记录存入内存缓冲区，供界面查看和导出。

    仅做 O(1) 的 deque.append，不做任何 I/O，不会阻塞调用线程。
    """

    def emit(self, record):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            millis = int((record.created - int(record.created)) * 1000)
            line = f"[{ts}.{millis:03d}] [{record.levelname}] [{record.name}] {record.getMessage()}"
            _memory_records.append(line)
        except Exception:
            # 日志记录本身绝不能抛出异常影响主流程
            pass


def _load_history_logs_from_file():
    """启动时从日志文件加载历史记录到内存 deque。

    仅加载最后一个文件的最后 _MAX_MEMORY_RECORDS 条，避免内存爆炸。
    """
    try:
        log_path = _get_log_file_path()
        if not log_path.exists():
            return
        # 读取文件最后部分（最多 1MB，避免大文件阻塞启动）
        max_read_bytes = 1024 * 1024
        file_size = log_path.stat().st_size
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if file_size > max_read_bytes:
                f.seek(file_size - max_read_bytes)
                f.readline()  # 跳过可能截断的第一行
            lines = f.readlines()
        # 截取最后 _MAX_MEMORY_RECORDS 条
        recent = lines[-_MAX_MEMORY_RECORDS:]
        for line in recent:
            line = line.rstrip("\n\r")
            if line:
                _memory_records.append(line)
    except Exception:
        pass


def init_logging(enabled: bool = True):
    """初始化全局日志记录器。

    默认启用。即使 enabled=False 也会保留 logger，
    仅移除内存 handler，避免外部代码 get_logger() 失败。

    启用时：
    - 启动时加载历史日志文件到内存 deque
    - 添加内存 handler（实时显示）
    - 添加 RotatingFileHandler（持久化到磁盘，软件退出后不丢失）
    """
    global _logging_enabled
    _logging_enabled = bool(enabled)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    # 移除旧的 _MemoryLogHandler 和 _FileLogHandler 避免重复
    for h in list(logger.handlers):
        if isinstance(h, (_MemoryLogHandler, logging.handlers.RotatingFileHandler)):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    if _logging_enabled:
        # 启动时加载历史日志到内存
        _load_history_logs_from_file()

        # 内存 handler（实时显示）
        mem_handler = _MemoryLogHandler()
        mem_handler.setLevel(logging.DEBUG)
        logger.addHandler(mem_handler)

        # 文件 handler（持久化，软件退出后不丢失）
        try:
            log_path = _get_log_file_path()
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=_MAX_LOG_FILE_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,  # 延迟创建文件，避免初始化时 I/O
            )
            file_handler.setLevel(logging.DEBUG)
            # 文件格式与内存格式保持一致
            file_handler.setFormatter(logging.Formatter(
                "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(file_handler)
        except Exception:
            # 文件 handler 创建失败不影响内存日志
            pass

        logger.setLevel(logging.DEBUG)
    else:
        # 关闭时调高阈值，避免无谓的日志构造开销
        logger.setLevel(logging.CRITICAL + 1)


def flush_logs():
    """手动将文件 handler 的缓冲写入磁盘（软件退出前调用）。"""
    try:
        logger = logging.getLogger(_LOGGER_NAME)
        for h in logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                try:
                    h.flush()
                except Exception:
                    pass
    except Exception:
        pass


def is_logging_enabled() -> bool:
    return _logging_enabled


def get_logger(name: str = "") -> logging.Logger:
    """获取日志记录器。name 为空时返回主记录器。"""
    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return logging.getLogger(_LOGGER_NAME)


def get_memory_logs() -> list:
    """返回内存中所有日志记录列表。"""
    return list(_memory_records)


def clear_memory_logs():
    """清空内存日志缓冲区，并删除持久化日志文件（包括备份）。

    用户主动清空日志时，应同时清除内存和磁盘上的历史记录，
    避免下次启动时又加载回已清空的日志。
    清空后重新初始化文件 handler，确保后续日志仍能正常写入。
    """
    _memory_records.clear()
    # 先关闭并移除文件 handler，释放文件句柄（Windows 下无法删除已打开的文件）
    logger = logging.getLogger(_LOGGER_NAME)
    for h in list(logger.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # 删除日志文件及滚动备份
    try:
        log_path = _get_log_file_path()
        try:
            if log_path.exists():
                log_path.unlink()
        except Exception:
            pass
        for i in range(1, _BACKUP_COUNT + 1):
            backup = log_path.parent / f"{log_path.name}.{i}"
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
    except Exception:
        pass
    # 重新创建文件 handler，确保后续日志仍能持久化
    if _logging_enabled:
        try:
            log_path = _get_log_file_path()
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=_MAX_LOG_FILE_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(
                "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(file_handler)
        except Exception:
            pass


def export_logs(file_path: str) -> tuple:
    """将内存日志导出到文件。

    文件格式：
      - 第 1 行：固定标识头 `MemeKitLogV1`
      - 第 2 行：明文元信息（导出时间、日志条数）
      - 第 3 行起：编码后的日志正文

    对外不暴露编码细节，仅作普通日志文件呈现。
    返回 (ok, message)。
    """
    try:
        import base64

        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        logs = get_memory_logs()

        # 明文内容（仅元信息，不含日志正文）
        plain_header = (
            f"MemeKit 运行日志\n"
            f"导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"日志条数: {len(logs)}\n"
            f"{'=' * 60}\n"
        )
        # 日志正文：UTF-8 编码后编码存储
        body_text = "\n".join(logs)
        body_bytes = body_text.encode("utf-8")
        body_b64 = base64.b64encode(body_bytes).decode("ascii")

        with open(p, "w", encoding="utf-8") as f:
            f.write("MemeKitLogV1\n")
            f.write(plain_header)
            f.write(body_b64)
        return True, f"已导出 {len(logs)} 条日志到 {p}"
    except Exception as e:
        return False, f"导出失败: {e}"


# ============================================================
# 语义化事件 API：在关键位置埋点，记录软件运行全貌
# 所有函数都内置 try/except，绝不抛出异常影响主流程
# ============================================================

def _emit(level: str, category: str, message: str):
    """统一格式化并写入一条日志。"""
    try:
        if not _logging_enabled:
            return
        logger = logging.getLogger(f"{_LOGGER_NAME}.{category}")
        getattr(logger, level.lower(), logger.info)(message)
    except Exception:
        pass


def log_app_start(version: str = ""):
    """记录软件启动。每次启动前插入空行分隔，区分不同运行周期。"""
    # 插入空行分隔，区分不同运行周期（第一次启动时除外）
    # 写入内存和文件
    try:
        if _memory_records:
            _memory_records.append("")
        # 向文件 handler 直接写入空行
        logger = logging.getLogger(_LOGGER_NAME)
        for h in logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler):
                try:
                    h.stream.write("\n")
                    h.flush()
                except Exception:
                    pass
    except Exception:
        pass
    _emit("INFO", "LIFECYCLE", f"===== 软件启动 {version} =====".strip())


def log_app_exit(reason: str = "normal"):
    """记录软件关闭。reason: normal/crash/killed"""
    _emit("INFO", "LIFECYCLE", f"软件关闭 (reason={reason})")


def log_ui_tab_switch(tab_name: str):
    """记录用户切换 TAB。"""
    _emit("INFO", "UI", f"切换界面 → {tab_name}")


def log_ui_action(action: str, detail: str = ""):
    """记录 UI 上的关键操作（点击按钮等）。"""
    msg = f"UI操作: {action}"
    if detail:
        msg += f" - {detail}"
    _emit("INFO", "UI", msg)


def log_device_event(action: str, serial: str = "", mode: str = ""):
    """记录设备事件。action: connected/disconnected/mode_changed/selected"""
    parts = [f"设备{action}"]
    if serial:
        parts.append(f"serial={serial}")
    if mode:
        parts.append(f"mode={mode}")
    _emit("INFO", "DEVICE", " ".join(parts))


def log_device_info(info: dict):
    """记录设备详细信息（连接时调用）。

    记录字段：品牌、型号、序列号、运行模式、Bootloader状态、
    Android版本、详细版本号、当前运行槽位、锁机状态等。
    """
    try:
        if not info:
            return
        parts = ["设备详细信息:"]
        # 按重要性排序的字段映射
        field_map = [
            ("brand", "品牌"),
            ("model", "型号"),
            ("serial", "序列号"),
            ("mode", "运行模式"),
            ("connection_status", "连接模式"),
            ("android_version", "Android版本"),
            ("build_display", "详细版本号"),
            ("fingerprint", "指纹"),
            ("bootloader", "Bootloader"),
            ("bootloader_locked", "Bootloader锁定状态"),
            ("flash_locked", "Flash锁定状态"),
            ("verified_boot_state", "已验证启动状态"),
            ("slot_suffix", "当前槽位"),
            ("slot", "槽位(备用)"),
            ("device", "设备代号"),
            ("product", "产品名"),
            ("sdk", "SDK版本"),
            ("battery", "电量"),
            ("battery_health", "电池健康度"),
            ("battery_health_percent", "电池健康度(%)"),
            ("rooted", "Root状态"),
            ("su_path", "su路径"),
            ("magisk_path", "Magisk路径"),
            ("unlock_supported", "支持解锁"),
        ]
        for key, label in field_map:
            val = info.get(key)
            if val is not None and str(val).strip():
                parts.append(f"{label}={val}")
        # 若以上字段都为空，至少记录可用的键
        if len(parts) == 1:
            for k, v in info.items():
                if v is not None and str(v).strip() and k not in ("status_line", "status_color", "banner_state"):
                    parts.append(f"{k}={v}")
        if len(parts) > 1:
            _emit("INFO", "DEVICE", " | ".join(parts))
    except Exception:
        pass


def log_file_event(action: str, file_path: str = ""):
    """记录文件选择/打开事件。action: select/open/save"""
    name = ""
    try:
        if file_path:
            name = Path(file_path).name
    except Exception:
        name = str(file_path)
    _emit("INFO", "FILE", f"文件{action}: {name or file_path}")


def log_operation(action: str, success: bool = True, detail: str = ""):
    """记录功能操作结果。action: 刷写/root/快指/字库备份 等"""
    status = "成功" if success else "失败"
    msg = f"功能{action}{status}"
    if detail:
        msg += f" - {detail}"
    _emit("INFO" if success else "ERROR", "OP", msg)


def log_error(scope: str, message: str, exc: BaseException = None):
    """记录关键错误（非致命，软件继续运行）。"""
    try:
        if exc is not None:
            extra = f"{type(exc).__name__}: {exc}"
            message = f"{message} | {extra}" if message else extra
        _emit("ERROR", "ERROR", f"[{scope}] {message}")
    except Exception:
        pass


def install_excepthook():
    """安装全局异常钩子，捕获未处理异常并写入日志。"""
    try:
        _orig = sys.excepthook

        def _hook(exc_type, exc_value, exc_tb):
            try:
                if _logging_enabled:
                    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                    _emit("ERROR", "CRASH", f"未捕获异常: {exc_type.__name__}: {exc_value}\n{tb_text}")
            except Exception:
                pass
            # 调用原始钩子（默认打印到 stderr）
            try:
                _orig(exc_type, exc_value, exc_tb)
            except Exception:
                pass

        sys.excepthook = _hook
    except Exception:
        pass
