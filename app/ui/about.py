"""关于对话框：使用纯 QDialog 避免与 MaskDialogBase 遮罩冲突。"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton
from PySide6.QtCore import Qt

ABOUT_TEXT = (
    "══════════════════════════════════════════════════\n"
    "                      🧹 MemeKit V3.1.7 公开版\n"
    "══════════════════════════════════════════════════\n\n"
    "🤞 本版本由 数码Meme 基于 Tobapuw 开源项目「拖把工具箱」二次修改\n"
    "👾 更新内容清单 👇\n"
    "├─ 新增功能\n"
    "│  ├─ 1. ✨ ⌈快捷指令⌋：支持一键添加并执行各种指令\n"
    "│  ├─ 2. ✨ ⌈备份字库⌋：支持一键备份手机全分区字库文件，玩机更安心\n"
    "│  ├─ 3. ✨ ⌈还原字库⌋：支持一键刷写备份的全分区字库文件\n"
    "│  └─ 4. ✨ ⌈安装驱动⌋：支持一键配置Adb/Fastboot环境变量\n\n"
    "├─ 界面 & 功能优化\n"
    "│  ├─ 5. ✨ 刷机中心重构：加入 Payload.bin 处理、单分区独立刷入等功能\n"
    "│  ├─ 6. ✨ 全面优化UI界面排版，颜值既是正义(*/ω＼*)\n"
    "│  ├─ 7. ✨ 彻底移除全部联网逻辑，软件纯本地离线运行\n"
    "│  └─ 8. ✨ 底层代码重构，大幅降低性能消耗、提升运行速度\n\n"
    "──────────────────────────────────────────────────\n"
    "ℹ 版本说明\n"
    "本版本已移除在线下载、云端存储等所有联网功能，不再标注线上服务相关贡献者，敬请谅解。\n\n"
    "──────────────────────────────────────────────────\n"
    "🎉 致谢开源贡献者\n\n"
    "▫️ 刷机底层、移植适配：@秋詞、@Lucky\n"
    "▫️ 拖把工具箱作者：@Tobapuw、@人美心善且温柔\n"
    "▫️ 界面UI框架：@zhiyiYo PyQt-Fluent-Widgets\n\n"
    "📜 开源协议声明\n"
    "界面组件遵循 GNU GPLv3.0 开源协议，相关版权归原作者所有，完整源码可前往官方开源仓库查看。\n\n"
    "══════════════════════════════════════════════════\n"
    "©️ 2025–2026 Tobapuw 保留原始版权\n"
    "🛠️ 开发技术栈：Python + PySide6 + PyQt-Fluent-Widgets\n"
    "══════════════════════════════════════════════════"
)
def show_about_with_blur(parent):
    """显示带模糊背景的关于对话框。

    使用纯 QDialog 而非 qfluentwidgets 的 MessageBox/Dialog，
    避免 MaskDialogBase 的遮罩层与 _BlurOverlay 产生 Z 序冲突导致卡死。
    """
    from app.components.blur_popup import _BlurOverlay, _play_system_sound

    _play_system_sound()
    blur = _BlurOverlay(parent)

    dlg = QDialog(parent)
    dlg.setWindowTitle("关于")
    dlg.setModal(True)
    dlg.setMinimumSize(540, 420)
    dlg.setStyleSheet("""
        QDialog {
            background-color: #F5F3FF;
            border-radius: 10px;
        }
    """)

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(14)

    title = QLabel("关于")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1D1B20;")
    title.setAlignment(Qt.AlignCenter)
    layout.addWidget(title)

    text = QTextEdit()
    text.setReadOnly(True)
    text.setPlainText(ABOUT_TEXT)
    text.setStyleSheet("""
        QTextEdit {
            color: #1D1B20;
            background: transparent;
            border: 1px solid #E0E0E0;
            border-radius: 6px;
            padding: 8px;
        }
    """)
    layout.addWidget(text, 1)

    btn = QPushButton("确定")
    btn.setStyleSheet("""
        QPushButton {
            color: #FFFFFF;
            background-color: #2A74DA;
            border: none;
            border-radius: 6px;
            padding: 8px 32px;
            font-size: 14px;
        }
        QPushButton:hover {
            background-color: #2568C3;
        }
    """)
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn, alignment=Qt.AlignCenter)

    dlg.exec()
    blur.dispose()