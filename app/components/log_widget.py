from PySide6.QtWidgets import QTextBrowser
from PySide6.QtGui import QFont, QTextCursor, QColor, QTextCharFormat
from PySide6.QtCore import Qt

try:
    from qfluentwidgets import isDarkTheme
except Exception:
    isDarkTheme = None


def _is_dark() -> bool:
    try:
        if isDarkTheme is not None:
            return bool(isDarkTheme())
    except Exception:
        pass
    return False


def _build_stylesheet() -> str:
    """根据当前主题动态构建 LogWidget 的样式表。"""
    if _is_dark():
        bg = "#1E1E1E"
        text = "#E6E1E5"
        border = "#2A2A2A"
        sel_bg = "#7C3AED"
        sel_text = "#FFFFFF"
        scroll_bg = "#181818"
        handle = "#3A3A3A"
        handle_hover = "#4A4A4A"
        handle_active = "#7C3AED"
    else:
        bg = "#F5F3FF"
        text = "#1f2329"
        border = "#DDD6FE"
        sel_bg = "#EDE9FE"
        sel_text = "#1f2329"
        scroll_bg = "transparent"
        handle = "rgba(0, 0, 0, 0.22)"
        handle_hover = "rgba(0, 0, 0, 0.34)"
        handle_active = "rgba(124, 58, 237, 0.55)"

    return f"""
        QTextBrowser {{
            background-color: {bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 10px;
            selection-background-color: {sel_bg};
            selection-color: {sel_text};
        }}
        QScrollBar:vertical {{
            background: {scroll_bg};
            width: 12px;
            margin: 6px 2px 6px 0;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {handle};
            min-height: 36px;
            border-radius: 6px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {handle_hover};
        }}
        QScrollBar::handle:vertical:pressed {{
            background: {handle_active};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
            border: none;
            background: transparent;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{
            background: {scroll_bg};
            height: 12px;
            margin: 0 6px 2px 6px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {handle};
            min-width: 36px;
            border-radius: 6px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {handle_hover};
        }}
        QScrollBar::handle:horizontal:pressed {{
            background: {handle_active};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
            border: none;
            background: transparent;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
    """


class LogWidget(QTextBrowser):
    """
    精美的日志组件，支持普通日志输出、步骤化日志追加与状态更新（如 ... OK / Error）。
    背景色会跟随当前主题（浅色 / 深色）自动调整。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._step_blocks = {}
        self._step_counter = 0
        self._init_ui()

    def _init_ui(self):
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.Monospace)
        font.setFamilies([
            "Cascadia Code",
            "Segoe UI Mono",
            "JetBrains Mono",
            "Source Code Pro",
            "Consolas",
            "Fira Code",
            "Roboto Mono",
            "Menlo",
            "Monaco",
            "Courier New",
            "monospace"
        ])
        font.setPointSize(9)
        self.setFont(font)

        self.setStyleSheet(_build_stylesheet())
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextBrowser.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def refresh_theme(self):
        """主题切换后重新应用样式表并更新已有文字颜色。"""
        self.setStyleSheet(_build_stylesheet())

        # 更新已有文字中默认颜色 → 新主题颜色
        html = self.toHtml()
        if _is_dark():
            # 切换到深色：浅色默认文字 → 深色默认文字
            html = html.replace("color:#1f2329;", "color:#e6e1e5;")
        else:
            # 切换到浅色：深色默认文字 → 浅色默认文字
            html = html.replace("color:#e6e1e5;", "color:#1f2329;")
        # 保持滚动位置
        vbar = self.verticalScrollBar()
        pos = vbar.value() if vbar else 0
        self.setHtml(html)
        if vbar:
            vbar.setValue(pos)

    def clear_log(self):
        self.clear()
        self._step_blocks.clear()
        self._step_counter = 0

    def append_log(self, text: str, color: str = None, bold: bool = False):
        """
        普通的日志追加。
        """
        if color is None:
            color = "#E6E1E5" if _is_dark() else "#1f2329"
        self.moveCursor(QTextCursor.End)
        cursor = self.textCursor()
        if not cursor.atBlockStart():
            cursor.insertText("\n")
            
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Bold)
            
        cursor.insertText(text, fmt)
        cursor.insertText("\n")
        self.ensureCursorVisible()

    def start_step(self, step_id: str, text: str):
        """
        开始一个步骤，输出类似 "重启至bootloader..."，并返回该步骤的 ID 以供后续更新状态。
        """
        self.moveCursor(QTextCursor.End)
        cursor = self.textCursor()
        
        if not cursor.atBlockStart():
            cursor.insertText("\n")
            
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#1677ff"))
        
        step_text = f"{text}... "
        cursor.insertText(step_text, fmt)
        
        block_num = cursor.blockNumber()
        self._step_blocks[step_id] = block_num
        
        cursor.insertText("\n")
        self.ensureCursorVisible()

    def finish_step(self, step_id: str, success: bool, detail: str = ""):
        """
        完成一个步骤，在对应的行末尾追加 "OK" 或 "Error"。
        """
        if step_id not in self._step_blocks:
            return
            
        block_num = self._step_blocks[step_id]
        block = self.document().findBlockByNumber(block_num)
        if not block.isValid():
            return
            
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock)
        
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold)
        if success:
            fmt.setForeground(QColor("#00b42a")) # 绿色 OK
            status_text = "OK"
        else:
            fmt.setForeground(QColor("#f53f3f")) # 红色 Error
            status_text = "Error"
            
        cursor.insertText(status_text, fmt)
        
        if detail:
            fmt.setForeground(QColor("#f53f3f") if not success else QColor("#4e5969"))
            fmt.setFontWeight(QFont.Normal)
            cursor.insertText(f" ({detail})", fmt)
            
        # 恢复光标到末尾并滚动
        self.moveCursor(QTextCursor.End)
        self.ensureCursorVisible()
