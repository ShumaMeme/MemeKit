import sys
from pathlib import Path


def get_project_root():
    """获取项目根目录（兼容开发模式和 PyInstaller 打包后的 EXE 模式）。

    在打包后的 EXE 中，所有 .pyd 文件被解压到 sys._MEIPASS 下，
    不能依赖 __file__ 的相对层级来定位项目根目录。
    """
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parents[1]