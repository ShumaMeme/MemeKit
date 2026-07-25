"""弹窗通用样式工具。"""
try:
    from qfluentwidgets import isDarkTheme as _isDarkTheme
except Exception:
    def _isDarkTheme():
        return False


def _is_perf():
    """性能模式下降级为纯色不透明背景。"""
    try:
        from app.components.hidden_settings import is_performance_mode
        return is_performance_mode()
    except Exception:
        return False


def setup_dialog_window(dlg):
    """设置弹窗窗口属性。

    保留系统标题栏（有标题、关闭按钮），提供清晰的边界感。
    应在 setStyleSheet(dialog_stylesheet()) 之前调用。
    """
    try:
        from PySide6.QtCore import Qt
        dlg.setWindowFlags(Qt.Dialog)
    except Exception:
        pass


def dialog_stylesheet() -> str:
    """弹窗样式：跟随系统主题切换，清晰的边框和背景。

    保留系统标题栏，内容区域使用纯色背景 + 明显边框，
    提供清晰的边界感和良好的可读性。
    性能模式与非性能模式保持一致的视觉效果。
    """
    if _isDarkTheme():
        text = "#FFFFFF"
        card_bg = "rgba(50, 50, 58, 0.55)"
        input_border = "rgba(120, 145, 200, 0.30)"
        input_focus_border = "#2A74DA"
        placeholder = "#8A8F98"
        check_indicator_bg = "rgba(255, 255, 255, 0.08)"
        check_indicator_border = "rgba(255, 255, 255, 0.20)"
        if _is_perf():
            bg = "#2A2D36"
            input_bg = "#383B45"
            dlg_border = "1px solid rgba(255, 255, 255, 0.15)"
        else:
            bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                  "stop:0 rgba(45, 47, 58, 0.55), stop:1 rgba(35, 37, 48, 0.50))")
            input_bg = "rgba(45, 47, 58, 0.65)"
            dlg_border = "1px solid rgba(120, 145, 200, 0.40)"
    else:
        text = "#1D1B20"
        card_bg = "rgba(255, 255, 255, 0.65)"
        input_border = "rgba(42, 116, 218, 0.25)"
        input_focus_border = "#2A74DA"
        placeholder = "#999999"
        check_indicator_bg = "rgba(255, 255, 255, 0.70)"
        check_indicator_border = "rgba(42, 116, 218, 0.40)"
        if _is_perf():
            bg = "#F0F5FF"
            input_bg = "#FFFFFF"
            dlg_border = "1px solid rgba(0, 0, 0, 0.12)"
        else:
            bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                  "stop:0 rgba(255, 255, 255, 0.65), stop:1 rgba(240, 245, 255, 0.60))")
            input_bg = "rgba(255, 255, 255, 0.70)"
            dlg_border = "1px solid rgba(42, 116, 218, 0.40)"
    return f"""
        QDialog {{
            background-color: {bg};
            color: {text};
            border: {dlg_border};
        }}
        QLabel, SubtitleLabel, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel {{
            color: {text};
            background: transparent;
        }}
        QCardWidget, CardWidget {{
            background-color: {card_bg};
            border-radius: 8px;
        }}
        QLineEdit {{
            color: {text};
            background-color: {input_bg};
            border: 1px solid {input_border};
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 14px;
            selection-background-color: #2A74DA;
        }}
        QLineEdit:focus {{
            border: 1px solid {input_focus_border};
        }}
        QLineEdit::placeholder {{
            color: {placeholder};
        }}
        QTextEdit, QPlainTextEdit, QTextBrowser {{
            color: {text};
            background-color: {input_bg};
            border: 1px solid {input_border};
            border-radius: 6px;
            padding: 8px;
            selection-background-color: #2A74DA;
        }}
        QComboBox {{
            color: {text};
            background-color: {input_bg};
            border: 1px solid {input_border};
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 14px;
        }}
        QComboBox:focus {{
            border: 1px solid {input_focus_border};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            color: {text};
            background-color: {input_bg};
            border: 1px solid {input_border};
            border-radius: 4px;
            selection-background-color: #2A74DA;
            selection-color: #FFFFFF;
            outline: none;
        }}
        QSpinBox {{
            color: {text};
            background-color: {input_bg};
            border: 1px solid {input_border};
            border-radius: 6px;
            padding: 6px 8px;
        }}
        QSpinBox:focus {{
            border: 1px solid {input_focus_border};
        }}
        QCheckBox, QRadioButton {{
            color: {text};
            background: transparent;
            spacing: 8px;
            padding: 2px 0;
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border: 2px solid {check_indicator_border};
            border-radius: 4px;
            background: {check_indicator_bg};
        }}
        QRadioButton::indicator {{
            border-radius: 9px;
        }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background: #2A74DA;
            border-color: #2A74DA;
        }}
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
            border-color: #2A74DA;
        }}
        QGroupBox {{
            color: {text};
            background-color: {card_bg};
            border: 1px solid {input_border};
            border-radius: 8px;
            margin-top: 10px;
        }}
        QGroupBox::title {{
            color: {text};
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }}
        QMenu {{
            background-color: {card_bg};
            border: 1px solid {input_border};
            border-radius: 8px;
            padding: 6px;
            color: {text};
        }}
        QMenu::item {{
            padding: 6px 24px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: rgba(42, 116, 218, 0.20);
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical {{
            background: rgba(42, 116, 218, 0.40);
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: rgba(42, 116, 218, 0.60);
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            background: transparent;
            border: none;
            height: 0px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}
    """
