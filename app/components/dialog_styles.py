"""弹窗通用样式工具。"""
try:
    from qfluentwidgets import isDarkTheme as _isDarkTheme
except Exception:
    def _isDarkTheme():
        return False


def dialog_stylesheet() -> str:
    """弹窗样式：浅紫色主题，跟随系统主题切换。"""
    if _isDarkTheme():
        bg = "#F3D3E7"
        text = "#1D1B20"
        sub_text = "#333333"
    else:
        bg = "#F7F0FC"
        text = "#1D1B20"
        sub_text = "#333333"
    return f"""
        QDialog {{
            background-color: {bg};
            color: {text};
        }}
        QLabel, SubtitleLabel, TitleLabel, CaptionLabel, BodyLabel, StrongBodyLabel {{
            color: {text};
            background: transparent;
        }}
        QCheckBox {{
            color: {text};
            background: transparent;
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
        }}
    """