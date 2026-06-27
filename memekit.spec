# -*- mode: python ; coding: utf-8 -*-
'''
MemeKit PyInstaller spec - 自动适配加密/不加密打包
============================================================
打包流程:
  【加密打包】(Cython 编译):
    1. python build_cython.py        # 编译 app/*.py 为 .pyd（加密）
    2. pyinstaller memekit.spec --noconfirm --clean

  【不加密打包】(直接用 .py):
    1. pyinstaller memekit.spec --noconfirm --clean
    (无需先运行 build_cython.py，spec 会自动检测)

输出: dist/MemeKit/MemeKit.exe（文件夹模式，启动快不闪退）
============================================================
'''
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

# ============================================================
# 路径配置
# ============================================================
CYTHON_BUILD = Path('build/cython_build').resolve()
SPEC_DIR = Path(SPECPATH).absolute()  # SPECPATH 是 PyInstaller 内置变量

# 自动检测：是否有 Cython 编译输出（.pyd 文件）
USE_CYTHON = CYTHON_BUILD.exists() and any(CYTHON_BUILD.glob('**/*.pyd'))

print(f"[SPEC] Cython 构建目录: {CYTHON_BUILD}")
print(f"[SPEC] Spec 文件目录: {SPEC_DIR}")
print(f"[SPEC] 加密模式: {'是 (Cython .pyd)' if USE_CYTHON else '否 (纯 .py)'}")

# 入口脚本和路径
if USE_CYTHON:
    ENTRY_SCRIPT = str(CYTHON_BUILD / 'launcher.py')
    PATH_EX = [str(CYTHON_BUILD)]
    APP_SOURCE_DIR = CYTHON_BUILD / 'app'
else:
    ENTRY_SCRIPT = str(SPEC_DIR / 'launcher.py')
    PATH_EX = [str(SPEC_DIR)]
    APP_SOURCE_DIR = SPEC_DIR / 'app'

# ============================================================
# 1. 收集 Cython 编译的 .pyd 文件作为 binaries（仅加密模式）
# ============================================================
binaries = []
_app_modules = []

if USE_CYTHON:
    for pyd_file in sorted(CYTHON_BUILD.glob('**/*.pyd')):
        if '__pycache__' in pyd_file.parts:
            continue

        # 计算目标目录（相对于打包根目录）
        dest_dir = pyd_file.parent.relative_to(CYTHON_BUILD)
        binaries.append((str(pyd_file), str(dest_dir)))

        # 生成模块名用于 hiddenimports
        rel = pyd_file.relative_to(CYTHON_BUILD)
        module_name = str(rel.with_suffix(''))
        module_name = module_name.replace('\\', '.').replace('/', '.')

        # 去掉平台标签（如 .cp314-win_amd64）
        if '.' in module_name.rsplit('.', 1)[-1] and module_name.rsplit('.', 1)[-1].startswith('cp'):
            module_name = module_name.rsplit('.', 1)[0]

        # __init__.pyd 处理：app/__init__.pyd -> app
        # 注意：.pyd 文件名可能带平台标签（如 __init__.cp314-win_amd64.pyd）
        if rel.stem.split('.')[0] == '__init__':
            module_name = '.'.join(rel.parts[:-1])
            if not module_name:
                continue

        if module_name not in _app_modules:
            _app_modules.append(module_name)

print(f"[SPEC] 收集到 {len(binaries)} 个 .pyd 二进制文件")
print(f"[SPEC] 收集到 {len(_app_modules)} 个 app 子模块")

# ============================================================
# 2. 收集数据文件
# ============================================================
datas = [
    # 图标
    (str(SPEC_DIR / 'android-chrome-512x512.png'), '.'),
    (str(SPEC_DIR / 'memekit.ico'), '.'),
    (str(SPEC_DIR / '数码Meme.png'), '.'),
    # 二进制工具目录（adb, fastboot, scrcpy, 7z 等）
    (str(SPEC_DIR / 'bin'), 'bin'),
    # SVG 图标
    (str(SPEC_DIR / 'icon'), 'icon'),
]

# 收集 app 目录下的非 Python 资源文件（.qss, .png 等）
if APP_SOURCE_DIR.exists():
    for item in APP_SOURCE_DIR.rglob('*'):
        if item.is_dir():
            if '__pycache__' in item.parts:
                continue
            continue
        if item.suffix in ('.py', '.pyc', '.pyo', '.pyd', '.c', '.cpx'):
            continue
        rel = item.relative_to(APP_SOURCE_DIR)
        dest = 'app' / rel
        datas.append((str(item), str(dest)))
        print(f"[SPEC] [resource] {rel} -> {dest}")

# 收集 qfluentwidgets 的数据文件
datas += collect_data_files('qfluentwidgets')

# ============================================================
# 3. 显式声明需要的 hiddenimports
# ============================================================
hiddenimports = [
    # 压缩相关
    'bsdiff4',
    'zstd',
    'brotli',
    # GUI 框架
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'qfluentwidgets',
    # 其他依赖
    'requests',
    'httpx',
    'Cryptodome',
    'Cryptodome.Cipher',
    'Cryptodome.Cipher.AES',
    # 标准库（可能被遗漏的）
    'multiprocessing',
    'asyncio',
    'json',
    'sqlite3',
    'winsound',
    # Protobuf
    'google.protobuf',
    'google.protobuf.descriptor',
    'google.protobuf.descriptor_pool',
    'google.protobuf.runtime_version',
    'google.protobuf.symbol_database',
    'google.protobuf.internal.builder',
    # === importlib 懒加载的 Tab 模块（PyInstaller 无法自动追踪） ===
    'app.widgets.device_info_tab',
    'app.widgets.root_tab',
    'app.widgets.quick_commands_tab',
    'app.widgets.font_backup_tab',
    'app.widgets.font_restore_tab',
    'app.widgets.flash_center_tab',
    'app.widgets.scrcpy_tab',
    'app.widgets.software_manager_tab',
    'app.widgets.file_manager_tab',
    'app.widgets.settings_tab',
    'app.widgets.misc_tab',
    # Tab 内部依赖的子模块
    'app.widgets.misc_tools',
    'app.widgets.misc_tools.workers',
    'app.widgets.misc_tools.statusbar_icons_dialog',
    'app.widgets.misc_tools.screen_timeout_dialog',
    'app.widgets.misc_tools.payload_extract_dialog',
    'app.widgets.misc_tools.partition_flash_dialog',
    'app.widgets.misc_tools.ofp_dialog',
    'app.widgets.misc_tools.module_manager_dialog',
    'app.widgets.misc_tools.key_sim_dialog',
    'app.widgets.misc_tools.font_scale_dialog',
    'app.widgets.misc_tools.display_tweaks_dialog',
    'app.widgets.misc_tools.battery_sim_dialog',
    'app.widgets.misc_tools.animation_scale_dialog',
    'app.widgets.misc_tools.accessibility_reset_dialog',
    'app.widgets.misc_tools.run_shell_script_dialog',
    'app.widgets.misc_tools.bootloader_unlock_dialog',
    'app.widgets.misc_tools.config_check_dialog',
    # 服务层
    'app.services',
    'app.services.adb_service',
    'app.services.update_checker',
    # 逻辑层
    'app.logic',
    'app.logic.payload_extractor',
    'app.logic.flash_logic_sideload',
    'app.logic.flash_logic_miflash',
    'app.logic.module_manager',
    'app.logic.ofp_processor',
    'app.logic.payload_dumper',
    'app.logic.payload_dumper.extractor',
    'app.logic.payload_dumper.dumper_core',
    'app.logic.payload_dumper.http_file',
    'app.logic.payload_dumper.ziputil',
    'app.logic.payload_dumper.future_util',
    'app.logic.payload_dumper.update_metadata_pb2',
    'app.logic.payload_dumper.mtio',
    'app.logic.ojz',
    'app.logic.ojz.package_loader',
    # 组件
    'app.components',
    'app.components.log_widget',
    'app.components.blur_popup',
    'app.components.dialog_styles',
    # UI
    'app.ui',
    'app.ui.theme',
    'app.ui.about',
    'app.ui.about_author',
    'app.ui.disclaimer',
    'app.ui.startup_splash',
    'app.ui.fluent_main_window',
    # 版本
    'app.version',
] + _app_modules

# ============================================================
# 4. Analysis（分析依赖）
# ============================================================
a = Analysis(
    [ENTRY_SCRIPT],
    pathex=PATH_EX,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的 Qt 模块（减小体积）
        'PySide6.QtQml', 'PySide6.QtQml.*', 'PySide6.QtQuick',
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick',
        'PySide6.QtPdf', 'PySide6.QtVirtualKeyboard',
        # 排除科学计算库（不需要）
        'numpy', 'numpy.*', 'scipy', 'scipy.*',
        # 排除测试相关
        'pytest', 'unittest', 'unittest.*',
        # 排除已移除的依赖（防止意外打包）
        'enlighten', 'blessed', 'prefixed',
        'qrcode', 'PIL', 'PIL.*',
        'zeroconf', 'zeroconf.*',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ============================================================
# 5. 生成文件夹模式 EXE（启动快、不闪退）
# ============================================================
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemeKit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    hide_console='hide-early',
    icon=str(SPEC_DIR / 'memekit.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MemeKit',
)