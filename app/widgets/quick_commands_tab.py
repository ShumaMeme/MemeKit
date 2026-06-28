import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QMimeData, QPoint, QThread, QObject
from PySide6.QtGui import QDrag, QPixmap, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QDialog, QApplication,
)

from qfluentwidgets import (
    CardWidget, PrimaryPushButton, PushButton, FluentIcon,
    InfoBar, InfoBarPosition, SmoothScrollArea, CaptionLabel,
    BodyLabel, SubtitleLabel, StrongBodyLabel, TitleLabel,
    isDarkTheme, ThemeColor, qconfig,
)

from app.services import adb_service as svc
from app.components.log_widget import LogWidget
from app.components.blur_popup import show_blur_custom, show_blur_info


def _dialog_stylesheet() -> str:
    """弹窗样式：浅紫色主题，跟随系统主题切换。"""
    if isDarkTheme():
        bg = "#F3D3E7"
        card = "#FFFFFF"
        text = "#1D1B20"
        border = "#E8C0D5"
        input_bg = "#FFFFFF"
    else:
        bg = "#F7F0FC"
        card = "#ffffff"
        text = "#1D1B20"
        border = "#E0D4EC"
        input_bg = "#ffffff"
    return f"""
        QDialog {{
            background-color: {bg};
            color: {text};
        }}
        CardWidget {{
            background-color: {card};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
        }}
        QLineEdit {{
            background-color: {input_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 8px 10px;
            selection-background-color: #2A74DA;
            selection-color: #ffffff;
        }}
        QLineEdit:focus {{
            border: 1px solid #2A74DA;
        }}
        QLabel, TitleLabel, CaptionLabel, BodyLabel, SubtitleLabel, StrongBodyLabel {{
            color: {text};
            background: transparent;
        }}
    """


def _card_style(selected: bool) -> str:
    """卡片基础样式：背景由 paintEvent 绘制，此处仅保留透明底色。"""
    return "QWidget { background: transparent; }"


def _code_block_style() -> str:
    """代码块样式：独立容器，圆角背景，等宽字体。"""
    if isDarkTheme():
        return (
            "QWidget {"
            "  background-color: #24243A;"
            "  border: 1px solid #313244;"
            "  border-radius: 8px;"
            "  padding: 8px 10px;"
            "}"
        )
    else:
        return (
            "QWidget {"
            "  background-color: #F4F4F9;"
            "  border: 1px solid #E8E6F0;"
            "  border-radius: 8px;"
            "  padding: 8px 10px;"
            "}"
        )


def _cmd_text_color() -> str:
    return "#A6E3A1" if isDarkTheme() else "#3B82F6"


def _name_text_color(selected: bool = False) -> str:
    if isDarkTheme():
        if selected:
            return "#1E1E30"  # 深色文字，在浅粉色背景上清晰可见
        return "#E4E4E7"  # 更亮的灰色，提高可读性
    else:
        return "#1E293B"


def _mode_tag_style() -> str:
    """模式标签：小圆角标签，区分 ADB / Fastboot。"""
    if isDarkTheme():
        return (
            "QLabel {"
            "  background-color: #313244;"
            "  color: #A6E3A1;"
            "  border-radius: 4px;"
            "  padding: 2px 8px;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "}"
        )
    else:
        return (
            "QLabel {"
            "  background-color: #EDE9FE;"
            "  color: #6D28D9;"
            "  border-radius: 4px;"
            "  padding: 2px 8px;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "}"
        )


# ---------------------------------------------------------------------------
# 指令卡片（现代化统一设计：CommandCard 直接承载内容 + 选中高亮）
# ---------------------------------------------------------------------------
class CommandCard(CardWidget):
    """指令卡片：选中时左侧紫色 accent bar + 柔和背景 + 阴影抬升，未选中时简洁边框。"""

    clicked = Signal(int)
    double_clicked = Signal(int)
    order_changed = Signal(int, int)

    def __init__(self, index: int, data: dict, parent=None):
        super().__init__(parent)
        self._index = index
        self._data = data
        self._selected = False
        self._drag_start_pos: Optional[QPoint] = None
        self._set_ui()

    def _set_ui(self):
        self.setMinimumHeight(120)
        self.setCursor(Qt.PointingHandCursor)
        self.setBorderRadius(12)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # ---- 标题行：模式标签 + 指令名称 ----
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header_row.setContentsMargins(0, 0, 0, 0)

        self.name_label = QLabel(self._data.get("name", ""))
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {_name_text_color(self._selected)}; background: transparent;"
        )
        header_row.addWidget(self.name_label, 1)

        # 模式标签
        mode = self._data.get("mode", "any")
        if mode == "bootloader":
            tag_text = "Fastboot"
        elif mode == "system":
            tag_text = "Adb"
        elif mode == "any":
            tag_text = "CMD"
        else:
            tag_text = mode.upper()
        self.mode_tag = QLabel(tag_text)
        self.mode_tag.setFixedHeight(22)
        self.mode_tag.setStyleSheet(_mode_tag_style())
        header_row.addWidget(self.mode_tag)

        layout.addLayout(header_row)

        # ---- 代码块容器 ----
        self.code_container = QWidget()
        self.code_container.setStyleSheet(_code_block_style())
        code_layout = QVBoxLayout(self.code_container)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(2)

        self.cmd_label = QLabel(self._data.get("command", ""))
        self.cmd_label.setWordWrap(True)
        self.cmd_label.setStyleSheet(
            f"font-size: 12px; color: {_cmd_text_color()}; background: transparent;"
            "font-family: 'Cascadia Code', 'Consolas', 'Monaco', 'Courier New', monospace;"
        )
        code_layout.addWidget(self.cmd_label)

        layout.addWidget(self.code_container)

        self._update_style()

    # ---- 样式 ----
    def _update_style(self):
        self.setStyleSheet(_card_style(self._selected))
        if hasattr(self, "cmd_label"):
            self.cmd_label.setStyleSheet(
                f"font-size: 12px; color: {_cmd_text_color()}; background: transparent;"
                "font-family: 'Cascadia Code', 'Consolas', 'Monaco', 'Courier New', monospace;"
            )
        if hasattr(self, "name_label"):
            self.name_label.setStyleSheet(
                f"font-size: 15px; font-weight: 600; color: {_name_text_color(self._selected)}; background: transparent;"
            )
        if hasattr(self, "mode_tag"):
            self.mode_tag.setStyleSheet(_mode_tag_style())
        if hasattr(self, "code_container"):
            self.code_container.setStyleSheet(_code_block_style())

    def refresh_theme(self):
        self._update_style()

    # ---- 属性 ----
    @property
    def index(self) -> int:
        return self._index

    @index.setter
    def index(self, value: int):
        self._index = value

    @property
    def data(self) -> dict:
        return self._data

    @data.setter
    def data(self, value: dict):
        self._data = value
        self.name_label.setText(value.get("name", ""))
        self.cmd_label.setText(value.get("command", ""))
        mode = value.get("mode", "any")
        if mode == "bootloader":
            tag_text = "Fastboot"
        elif mode == "system":
            tag_text = "Adb"
        elif mode == "any":
            tag_text = "CMD"
        else:
            tag_text = mode.upper()
        self.mode_tag.setText(tag_text)

    @property
    def selected(self) -> bool:
        return self._selected

    @selected.setter
    def selected(self, value: bool):
        self._selected = value
        self._update_style()
        self.update()

    # ---- 绘制圆角背景 + 边框 + 选中时左侧 accent bar ----
    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QPainterPath
        from PySide6.QtCore import QRectF

        r = 12.0

        # 1. 先绘制圆角背景（填充整个卡片，完美裁切到圆角）
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        bg_path = QPainterPath()
        bg_rect = QRectF(0, 0, self.width(), self.height())
        bg_path.addRoundedRect(bg_rect, r, r)

        if isDarkTheme():
            if self._selected:
                bg_color = QColor(243, 211, 231, int(255 * 0.85))
            else:
                bg_color = QColor("#252526")
        else:
            bg_color = QColor("#FFFFFF")

        painter.setBrush(bg_color)
        painter.drawPath(bg_path)
        painter.end()

        # 2. 让 CardWidget 绘制子控件等内容
        super().paintEvent(event)

        # 3. 在最上层绘制边框 + accent bar
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        border_rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        border_path = QPainterPath()
        border_path.addRoundedRect(border_rect, r, r)

        if isDarkTheme():
            if self._selected:
                border_color = QColor("#C084D0")
            else:
                border_color = QColor("#2E2E3E")
        else:
            if self._selected:
                border_color = QColor("#C4B5FD")
            else:
                border_color = QColor("#E5E7EB")

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(border_path)

        # 选中时绘制左侧 accent bar
        if self._selected:
            accent_color = QColor("#8B5CF6") if isDarkTheme() else QColor("#7C3AED")
            bar_rect = QRectF(4, 12, 4, self.height() - 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent_color)
            painter.drawRoundedRect(bar_rect, 2, 2)

        painter.end()

    # ---- 拖拽支持 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return
        if not (event.buttons() & Qt.LeftButton):
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self._index))
        drag.setMimeData(mime)

        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.7)
        self.render(painter)
        painter.end()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos())
        drag.exec(Qt.MoveAction)
        self._drag_start_pos = None

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._drag_start_pos is not None:
                self.clicked.emit(self._index)
        self._drag_start_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self._index)


# ---------------------------------------------------------------------------
# 新增/编辑指令对话框（分步引导）
# ---------------------------------------------------------------------------
class CommandEditDialog(QDialog):
    """新增指令：分步引导填写；编辑指令：一次性展示。"""

    def __init__(self, parent=None, title="新增指令", data=None):
        super().__init__(parent)
        self._data = data or {}
        self._is_edit = bool(data)
        self._step = 1
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setStyleSheet(_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self.step_label = TitleLabel(self)
        self.step_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.step_label)

        self.sub_label = CaptionLabel(self)
        self.sub_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.sub_label)

        self.content_card = CardWidget(self)
        card_layout = QVBoxLayout(self.content_card)
        try:
            card_layout.setContentsMargins(16, 16, 16, 16)
            card_layout.setSpacing(10)
        except Exception:
            pass

        self.name_label = QLabel("指令名称")
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        card_layout.addWidget(self.name_label)

        self.name_edit = QLineEdit(self.content_card)
        self.name_edit.setPlaceholderText("例如：解锁Bootloader")
        self.name_edit.setFixedHeight(38)
        card_layout.addWidget(self.name_edit)

        self.cmd_label = QLabel("命令内容")
        self.cmd_label.setStyleSheet("font-size: 13px; font-weight: 500; margin-top: 8px;")
        card_layout.addWidget(self.cmd_label)

        self.cmd_edit = QLineEdit(self.content_card)
        self.cmd_edit.setPlaceholderText("例如：fastboot flashing unlock")
        self.cmd_edit.setFixedHeight(38)
        card_layout.addWidget(self.cmd_edit)

        layout.addWidget(self.content_card, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self.btn_cancel = PushButton("取消")
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setMinimumWidth(80)
        self.btn_cancel.setStyleSheet("color: #1D1B20;")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_next = PrimaryPushButton("下一步")
        self.btn_next.setFixedHeight(36)
        self.btn_next.setMinimumWidth(80)
        self.btn_next.clicked.connect(self._go_next_or_finish)
        btn_layout.addWidget(self.btn_next)

        layout.addLayout(btn_layout)

        if self._is_edit:
            self._show_step(-1)
            self.name_edit.setText(self._data.get("name", ""))
            self.cmd_edit.setText(self._data.get("command", ""))
        else:
            self._show_step(1)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.name_edit.hasFocus() or self.cmd_edit.hasFocus():
                self._go_next_or_finish()
                return
        super().keyPressEvent(event)

    def _show_step(self, step: int):
        self._step = step

        if step == -1:
            self.step_label.setText("编辑指令")
            self.sub_label.setText("修改指令名称和对应的命令")
            self.name_label.setVisible(True)
            self.name_edit.setVisible(True)
            self.cmd_label.setVisible(True)
            self.cmd_edit.setVisible(True)
            self.btn_next.setText("完成")
            self.btn_cancel.setVisible(True)
        elif step == 1:
            self.step_label.setText("第 1 步：填写指令名称")
            self.sub_label.setText("为你的指令取一个容易辨识的名称")
            self.name_label.setVisible(True)
            self.name_edit.setVisible(True)
            self.cmd_label.setVisible(False)
            self.cmd_edit.setVisible(False)
            self.btn_next.setText("下一步")
            self.name_edit.setFocus()
        elif step == 2:
            self.step_label.setText("第 2 步：填写命令内容")
            self.sub_label.setText("填写需要执行的命令（adb / fastboot）")
            self.name_label.setVisible(False)
            self.name_edit.setVisible(False)
            self.cmd_label.setVisible(True)
            self.cmd_edit.setVisible(True)
            self.cmd_edit.setFocus()

    def _go_next_or_finish(self):
        if self._step == -1 or self._step == 2:
            self._finish()
        elif self._step == 1:
            name = self.name_edit.text().strip()
            if not name:
                InfoBar.warning(
                    "提示", "请先输入指令名称", parent=self,
                    duration=2000, position=InfoBarPosition.TOP,
                )
                self.name_edit.setFocus()
                return
            self._show_step(2)

    def _finish(self):
        name = self.name_edit.text().strip()
        command = self.cmd_edit.text().strip()

        if not name:
            InfoBar.warning("提示", "请输入指令名称", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            self.name_edit.setFocus()
            return
        if not command:
            InfoBar.warning("提示", "请输入命令内容", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            self.cmd_edit.setFocus()
            return

        tool_name = command.strip().split()[0].lower() if command.strip() else ""
        if tool_name in ("fastboot",):
            mode = "bootloader"
        elif tool_name in ("adb",):
            mode = "system"
        else:
            mode = "any"

        self._result_data = {
            "name": name,
            "command": command,
            "mode": mode,
        }
        self.accept()

    def get_data(self) -> Optional[dict]:
        return getattr(self, "_result_data", None)


# ---------------------------------------------------------------------------
# 指令网格容器（支持拖放排序）
# ---------------------------------------------------------------------------
class CommandGridWidget(QWidget):
    """网格布局的指令卡片容器，支持拖放排序。"""

    order_changed = Signal()
    double_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: list[CommandCard] = []
        self._selected_index: Optional[int] = None
        self._drag_source_index: Optional[int] = None
        self._init_ui()
        # 监听主题变化，自动刷新卡片样式
        qconfig.themeChanged.connect(self._on_theme_changed)

    def _init_ui(self):
        self.setAcceptDrops(True)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(16)
        # 2 列等宽
        self.grid_layout.setColumnStretch(0, 1)
        self.grid_layout.setColumnStretch(1, 1)
        self.main_layout.addWidget(self.grid_widget)
        self.main_layout.addStretch()

    def _on_theme_changed(self):
        """主题变化时刷新所有卡片样式"""
        for card in self._cards:
            card.refresh_theme()

    def set_commands(self, commands: list[dict]):
        self._clear_cards()
        for i, cmd in enumerate(commands):
            card = CommandCard(i, cmd, self.grid_widget)
            card.clicked.connect(self._on_card_clicked)
            card.double_clicked.connect(self._on_card_double_clicked)
            self._cards.append(card)
            row, col = divmod(i, 2)
            self.grid_layout.addWidget(card, row, col)

    def _clear_cards(self):
        for card in self._cards:
            self.grid_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._selected_index = None

    def add_command(self, data: dict):
        index = len(self._cards)
        card = CommandCard(index, data, self.grid_widget)
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        self._cards.append(card)
        row, col = divmod(index, 2)
        self.grid_layout.addWidget(card, row, col)

    def remove_selected(self) -> Optional[int]:
        if self._selected_index is None:
            return None
        idx = self._selected_index
        card = self._cards.pop(idx)
        self.grid_layout.removeWidget(card)
        card.deleteLater()
        self._selected_index = None
        self._rebuild_grid()
        return idx

    def get_selected_index(self) -> Optional[int]:
        return self._selected_index

    def get_commands(self) -> list[dict]:
        return [card.data for card in self._cards]

    def _on_card_clicked(self, index: int):
        if self._selected_index is not None and self._selected_index < len(self._cards):
            self._cards[self._selected_index].selected = False
        self._selected_index = index
        self._cards[index].selected = True

    def _on_card_double_clicked(self, index: int):
        if self._selected_index is not None and self._selected_index < len(self._cards):
            self._cards[self._selected_index].selected = False
        self._selected_index = index
        self._cards[index].selected = True
        self.double_clicked.emit(index)

    def _rebuild_grid(self):
        for i, card in enumerate(self._cards):
            row, col = divmod(i, 2)
            self.grid_layout.addWidget(card, row, col)
            card.index = i

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasText():
            return
        try:
            source_index = int(event.mimeData().text())
        except (ValueError, TypeError):
            event.ignore()
            return

        target_index = self._find_nearest_card_index(event.pos())
        if target_index is None or target_index == source_index:
            event.ignore()
            return

        card = self._cards.pop(source_index)
        self._cards.insert(target_index, card)

        if self._selected_index is not None:
            if self._selected_index == source_index:
                self._selected_index = target_index
            elif source_index < self._selected_index <= target_index:
                self._selected_index -= 1
            elif target_index <= self._selected_index < source_index:
                self._selected_index += 1

        self._rebuild_grid()
        self.order_changed.emit()
        event.acceptProposedAction()

    def _find_nearest_card_index(self, pos: QPoint) -> Optional[int]:
        best_index = None
        best_dist = float("inf")
        for i, card in enumerate(self._cards):
            center = card.geometry().center()
            dist = (pos - center).manhattanLength()
            if dist < best_dist:
                best_dist = dist
                best_index = i
        return best_index


# ---------------------------------------------------------------------------
# 后台执行 Worker
# ---------------------------------------------------------------------------
class _ExecuteWorker(QThread):
    """后台线程执行 ADB/Fastboot 指令，QThread 子类化确保 Cython 编译后安全可靠。"""
    log_signal = Signal(str, str, bool)  # text, color, bold
    finished_signal = Signal(bool, str, str)  # all_ok, name, popup_message

    def __init__(self, cmd_data: dict, parent=None):
        super().__init__(parent)
        self._cmd_data = cmd_data

    def _log(self, text: str, color: str = "#86909c", bold: bool = False):
        self.log_signal.emit(text, color, bold)

    def run(self):
        name = self._cmd_data.get("name", "")
        command = self._cmd_data.get("command", "")
        popup_message = self._cmd_data.get("popup_message", "")

        if not command.strip():
            self._log("[错误] 命令内容为空。", "#f53f3f", True)
            self.finished_signal.emit(False, name, popup_message)
            return

        steps = [s.strip() for s in command.split("&&") if s.strip()]
        if not steps:
            self._log("[错误] 命令内容为空。", "#f53f3f", True)
            self.finished_signal.emit(False, name, popup_message)
            return

        cmd_parts = steps[0].split()
        tool_name = cmd_parts[0].lower()

        self._log(f"{'=' * 50}")
        self._log(f"[执行] {name}")
        self._log(f"[命令] {command}")

        # 1. 检测设备模式
        self._log("[检查] 正在检测设备连接状态...")
        try:
            mode, serial = svc.detect_connection_mode()
        except Exception as e:
            self._log(f"[错误] 设备检测失败: {e}", "#f53f3f", True)
            self.finished_signal.emit(False, name, popup_message)
            return

        self._log(f"[检测] 当前设备模式: {mode}")

        if mode == "none":
            self._log("[错误] 未检测到已连接设备，请确认USB连接。", "#f53f3f", True)
            self.finished_signal.emit(False, name, popup_message)
            return
        if mode == "offline":
            self._log("[错误] 设备已连接但未授权，请在手机上授权USB调试。", "#f53f3f", True)
            self.finished_signal.emit(False, name, popup_message)
            return

        # 2. 模式匹配
        _MODE_CN = {"system": "系统", "sideload": "Sideload", "fastbootd": "FastbootD", "bootloader": "Bootloader"}
        mode_cn = _MODE_CN.get(mode, mode)

        if tool_name == "fastboot":
            if mode not in ("bootloader", "fastbootd"):
                self._log(f"[错误] 当前设备模式为 {mode_cn}，fastboot 指令需要在 Bootloader 模式下执行", "#f53f3f", True)
                self.finished_signal.emit(False, name, popup_message)
                return
        elif tool_name == "adb":
            if mode not in ("system", "sideload"):
                self._log(f"[错误] 当前设备模式为 {mode_cn}，adb 指令需要在系统模式下执行", "#f53f3f", True)
                self.finished_signal.emit(False, name, popup_message)
                return

        self._log(f"[检查] 设备模式检查通过（当前: {mode_cn}）")

        # 3. 执行多步指令
        all_ok = True
        for step in steps:
            step_parts = step.split()
            step_tool = step_parts[0].lower()

            self._log(f"[执行] {' '.join(step_parts)}")

            try:
                if step_tool == "fastboot":
                    returncode, output = svc.run_fastboot(step_parts[1:], timeout=30)
                elif step_tool == "adb":
                    returncode, output = svc.run_adb(step_parts[1:], timeout=30)
                else:
                    returncode, output = svc.run_adb(["shell"] + step_parts, timeout=30)
            except Exception as e:
                self._log(f"[异常] {e}", "#f53f3f", True)
                self.finished_signal.emit(False, name, popup_message)
                return

            if output:
                for line in output.splitlines():
                    self._log(line)

            if returncode != 0:
                self._log(f"[失败] 步骤执行失败 (exit code: {returncode})", "#f53f3f", True)
                all_ok = False
                break

        if all_ok:
            self._log(f"[完成] {name} 执行成功", "#00b42a", True)
        else:
            self._log(f"[失败] {name} 执行失败", "#f53f3f", True)

        self.finished_signal.emit(all_ok, name, popup_message)


# ---------------------------------------------------------------------------
# 主 Tab 页面
# ---------------------------------------------------------------------------
class QuickCommandsTab(QWidget):
    """快捷指令 Tab：自定义 ADB/Fastboot 快捷命令。"""

    def __init__(self):
        super().__init__()
        self._commands: list[dict] = self._load_commands()
        self._exec_worker: Optional[_ExecuteWorker] = None
        self._init_ui()

    # ---- 持久化 ----
    @staticmethod
    def _get_commands_file() -> Path:
        data_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "TraeToolbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "quick_commands.json"

    @classmethod
    def _default_commands(cls) -> list[dict]:
        return [
            {
                "name": "解锁Bootloader",
                "command": "fastboot flashing unlock",
                "mode": "bootloader",
                "popup_message": "请使用音量键选中\nUnlock The Bootloader\n最后按电源键确认即可成功解锁",
            },
            {
                "name": "锁定Bootloader",
                "command": "fastboot flashing lock",
                "mode": "bootloader",
                "popup_message": "请使用音量键选中\nLock The Bootloader\n最后按电源键确认即可成功锁定",
            },
            {
                "name": "获取临时Root权限",
                "command": "fastboot oem set-gpu-preemption 0 androidboot.selinux=permissive && fastboot continue",
                "mode": "bootloader",
                "popup_message": "执行成功，请在Root管理器中点击\"越狱\"选项激活临时Root",
            },
        ]

    def _load_commands(self) -> list[dict]:
        path = self._get_commands_file()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list) and all(
                    isinstance(d, dict) and "name" in d and "command" in d
                    for d in data
                ):
                    return data
        except Exception:
            pass
        return self._default_commands()

    def _save_commands(self):
        try:
            path = self._get_commands_file()
            path.write_text(json.dumps(self._commands, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _init_ui(self):
        self.v_layout = QVBoxLayout(self)
        self.v_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea {border: none; background: transparent;}")
        self.v_layout.addWidget(self.scroll_area)

        self.scroll_widget = QWidget(self.scroll_area)
        self.scroll_widget.setStyleSheet("QWidget {background: transparent;}")
        self.scroll_area.setWidget(self.scroll_widget)

        self.layout = QVBoxLayout(self.scroll_widget)
        self.layout.setContentsMargins(32, 32, 32, 32)
        self.layout.setSpacing(24)

        # ---- Banner ----
        self.banner_card = CardWidget(self)
        banner_layout = QHBoxLayout(self.banner_card)
        banner_layout.setContentsMargins(24, 18, 24, 18)
        banner_layout.setSpacing(16)

        icon_lbl = QLabel("", self.banner_card)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignCenter)
        try:
            _ico = FluentIcon.COMMAND_PROMPT.icon(ThemeColor.LIGHT_1 if isDarkTheme() else ThemeColor.DARK_1)
            icon_lbl.setPixmap(_ico.pixmap(48, 48))
        except Exception:
            pass

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)

        self.banner_title = SubtitleLabel("快捷指令")
        self.banner_title.setStyleSheet("font-size: 22px; font-weight: 600;")
        title_col.addWidget(self.banner_title)

        self.banner_subtitle = CaptionLabel("自定义ADB/Fastboot快捷命令")
        self.banner_subtitle.setStyleSheet("font-size: 14px;")
        title_col.addWidget(self.banner_subtitle)

        banner_layout.addWidget(icon_lbl)
        banner_layout.addLayout(title_col)
        banner_layout.addStretch(1)

        self.layout.addWidget(self.banner_card)

        # ---- 主体左右分栏 ----
        main_h_layout = QHBoxLayout()
        main_h_layout.setSpacing(24)

        # -- 左侧：指令列表 --
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_add = PushButton(FluentIcon.ADD, "新增指令")
        self.btn_add.clicked.connect(self._on_add_command)
        btn_row.addWidget(self.btn_add)

        self.btn_delete = PushButton(FluentIcon.DELETE, "删除选中")
        self.btn_delete.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(self.btn_delete)

        self.btn_execute = PrimaryPushButton(FluentIcon.PLAY, "执行选中")
        self.btn_execute.clicked.connect(self._on_execute_selected)
        btn_row.addWidget(self.btn_execute)

        self.btn_restore = PushButton(FluentIcon.ROTATE, "恢复默认")
        self.btn_restore.clicked.connect(self._on_restore_default)
        btn_row.addWidget(self.btn_restore)

        btn_row.addStretch()
        left_col.addLayout(btn_row)

        self.cmd_grid = CommandGridWidget()
        self.cmd_grid.order_changed.connect(self._on_order_changed)
        self.cmd_grid.double_clicked.connect(self._on_card_double_clicked)
        left_col.addWidget(self.cmd_grid, 1)

        # -- 右侧：执行日志 --
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        log_header = QHBoxLayout()
        log_title = StrongBodyLabel("执行日志")
        log_header.addWidget(log_title)
        log_header.addStretch()

        self.btn_clear_log = PushButton(getattr(FluentIcon, "CLEAR", FluentIcon.REMOVE), "清空日志")
        self.btn_clear_log.clicked.connect(self._on_clear_log)
        log_header.addWidget(self.btn_clear_log)

        right_col.addLayout(log_header)

        self.log_widget = LogWidget(self)
        right_col.addWidget(self.log_widget, 1)

        left_w = QWidget()
        left_w.setLayout(left_col)
        right_w = QWidget()
        right_w.setLayout(right_col)

        main_h_layout.addWidget(left_w, 3)
        main_h_layout.addWidget(right_w, 2)

        self.layout.addLayout(main_h_layout)

        self._refresh_grid()

    # ---- 指令管理 ----
    def _refresh_grid(self):
        self.cmd_grid.set_commands(self._commands)

    def _on_add_command(self):
        dlg = CommandEditDialog(self.window(), title="新增指令")
        if show_blur_custom(self.window(), dlg) and dlg.get_data():
            self._commands.append(dlg.get_data())
            self._save_commands()
            self._refresh_grid()
            self._append_log(f"[新增] 指令 \"{dlg.get_data()['name']}\" 已添加")

    def _on_delete_selected(self):
        idx = self.cmd_grid.get_selected_index()
        if idx is None:
            InfoBar.warning("提示", "请先选中要删除的指令", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            return
        removed = self._commands.pop(idx)
        self._save_commands()
        self._refresh_grid()
        self._append_log(f"[删除] 指令 \"{removed['name']}\" 已移除")

    def _on_restore_default(self):
        self._commands = self._default_commands()
        self._save_commands()
        self._refresh_grid()
        try:
            if hasattr(self.log_widget, "refresh_theme"):
                self.log_widget.refresh_theme()
        except Exception:
            pass
        InfoBar.success(
            "恢复成功",
            "已恢复默认指令",
            parent=self,
            duration=2000,
            position=InfoBarPosition.TOP,
        )
        self._append_log("[恢复] 已恢复默认指令")

    def _on_card_double_clicked(self, index: int):
        if 0 <= index < len(self._commands):
            if self._exec_worker is not None and self._exec_worker.isRunning():
                InfoBar.warning("提示", "当前有指令正在执行中，请稍候", parent=self,
                                duration=2000, position=InfoBarPosition.TOP)
                return
            self._start_execute_thread(self._commands[index])

    def _on_order_changed(self):
        self._commands = self.cmd_grid.get_commands()
        self._save_commands()

    # ---- 执行指令（后台线程，防卡顿） ----
    def _on_execute_selected(self):
        idx = self.cmd_grid.get_selected_index()
        if idx is None:
            InfoBar.warning("提示", "请先选中要执行的指令", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            return

        # 防止重复点击
        if self._exec_worker is not None and self._exec_worker.isRunning():
            InfoBar.warning("提示", "当前有指令正在执行中，请稍候", parent=self,
                            duration=2000, position=InfoBarPosition.TOP)
            return

        cmd_data = self._commands[idx]
        self._start_execute_thread(cmd_data)

    def _start_execute_thread(self, cmd_data: dict):
        """启动后台线程执行指令"""
        self._set_execute_buttons_enabled(False)

        self._exec_worker = _ExecuteWorker(cmd_data, parent=self)

        self._exec_worker.log_signal.connect(self._on_exec_log)
        self._exec_worker.finished_signal.connect(self._on_exec_finished)
        self._exec_worker.finished.connect(self._exec_worker.deleteLater)
        self._exec_worker.finished.connect(lambda: setattr(self, '_exec_worker', None))
        self._exec_worker.start()

    def _on_exec_log(self, text: str, color: str, bold: bool):
        self._append_log(text, color=color, bold=bold)

    def _on_exec_finished(self, all_ok: bool, name: str, popup_message: str):
        self._set_execute_buttons_enabled(True)
        if self._exec_worker is not None:
            self._exec_worker.quit()

        if all_ok:
            if popup_message:
                show_blur_info(self.window(), "执行成功", popup_message)
            else:
                InfoBar.success("执行成功", f'"{name}" 已成功执行。', parent=self, duration=3000, position=InfoBarPosition.TOP)
        else:
            if popup_message:
                show_blur_info(self.window(), "执行失败", "执行出错，请查看日志输出窗口")
            else:
                InfoBar.error("执行失败", f'"{name}" 执行失败。', parent=self, duration=4000, position=InfoBarPosition.TOP)

    def _set_execute_buttons_enabled(self, enabled: bool):
        """执行期间禁用按钮防连点"""
        self.btn_execute.setEnabled(enabled)
        if hasattr(self, 'btn_add'):
            self.btn_add.setEnabled(enabled)
        if hasattr(self, 'btn_delete'):
            self.btn_delete.setEnabled(enabled)
        if hasattr(self, 'btn_restore'):
            self.btn_restore.setEnabled(enabled)

    def cleanup(self):
        """退出时清理执行线程，避免阻塞关闭"""
        try:
            if self._exec_worker is not None and self._exec_worker.isRunning():
                self._exec_worker.quit()
                self._exec_worker.wait(100)
                if self._exec_worker.isRunning():
                    self._exec_worker.terminate()
        except Exception:
            pass

    # ---- 日志 ----
    def _append_log(self, text: str, color: str = None, bold: bool = False):
        self.log_widget.append_log(text, color=color, bold=bold)

    def _on_clear_log(self):
        self.log_widget.clear_log()
