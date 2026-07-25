"""关于对话框：使用纯 QDialog 避免与 MaskDialogBase 遮罩冲突。"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton
from PySide6.QtCore import Qt

try:
    from qfluentwidgets import isDarkTheme
except Exception:
    def isDarkTheme():
        return False

ABOUT_TEXT = (
    "══════════════════════════════════════════════════\n"
    "🎉 关于MemeKit V4.6.0 公开版\n"
    "══════════════════════════════════════════════════\n"
    "🤞 本版本由 数码Meme 基于 Tobapuw 开源项目「拖把工具箱」二次开发\n\n"
    "👾 更新内容清单 👇\n"
    "├─ 新增功能\n"
    "│  ├─ 1. ✨ ⌈固件提取⌋：新增OPS固件解包功能，并进一步提升解包速度\n"
    "│  ├─ 2. ✨ ⌈屏幕录制⌋：在投屏中心新增屏幕录制功能，进一步完善功能\n"
    "══════════════════════════════════════════════════\n"
    "├─ 界面 & 功能优化\n"
    "│  ├─ 3. ✨ 全面重构GUI界面，整个软件焕然一新(*/ω＼*)\n"
    "│  ├─ 4. ✨ 进一步优化软件性能表现，虽然是Python，但性能毫不含糊👈(ﾟヮﾟ👈)\n"
)
def show_about_with_blur(parent):
    """显示带模糊背景的关于对话框。

    使用纯 QDialog 而非 qfluentwidgets 的 MessageBox/Dialog，
    避免 MaskDialogBase 的遮罩层与 _BlurOverlay 产生 Z 序冲突导致卡死。
    """
    from app.components.blur_popup import _make_blur_overlay, _play_system_sound

    _play_system_sound()
    blur = _make_blur_overlay(parent)

    dlg = QDialog(parent)
    dlg.setWindowTitle("关于")
    dlg.setModal(True)
    dlg.setMinimumSize(540, 420)
    # 使用统一弹窗样式
    try:
        from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
        setup_dialog_window(dlg)
        dlg.setStyleSheet(dialog_stylesheet())
    except Exception:
        pass

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    title = QLabel("关于")
    # 颜色由 dialog_stylesheet 控制（跟随主题），此处仅保留字体样式
    title.setStyleSheet("font-size: 20px; font-weight: bold; background: transparent;")
    title.setAlignment(Qt.AlignCenter)
    layout.addWidget(title)

    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(ABOUT_TEXT)
    text.setContextMenuPolicy(Qt.NoContextMenu)
    # 透明背景让弹窗毛玻璃透出，文字颜色由 dialog_stylesheet 控制
    text.setStyleSheet("""
        QTextEdit {
            background: transparent;
            border: 1px solid rgba(42, 116, 218, 0.15);
            border-radius: 6px;
            padding: 8px;
        }
    """)
    layout.addWidget(text, 1)

    btn = QPushButton("确定")
    btn_bg = "#4A90E2" if isDarkTheme() else "#2A74DA"
    btn_hover = "#3A7FD2" if isDarkTheme() else "#2568C3"
    btn.setStyleSheet(f"""
        QPushButton {{
            color: #FFFFFF;
            background-color: {btn_bg};
            border: none;
            border-radius: 6px;
            padding: 8px 32px;
            font-size: 14px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover};
        }}
    """)
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn, alignment=Qt.AlignCenter)

    dlg.exec()
    if blur is not None:
        blur.dispose()