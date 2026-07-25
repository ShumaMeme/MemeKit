"""全局毛玻璃质感样式：通过预渲染模糊光斑背景 + 半透明卡片实现真正的毛玻璃效果。

设计原理：
- 功能区域背景：渐变底色 + 多个柔和彩色光斑（径向渐变），模拟"被模糊的背景内容"
- CardWidget：降低不透明度（alpha 80/60），透出背景光斑 → 毛玻璃通透感
- 弹窗：半透明渐变 + blur_popup 预渲染模糊背景层

性能优化：
- 背景 QPixmap 预渲染一次，paint 事件只 drawPixmap（零实时计算）
- resize 时按需重建（缓存 + 延迟重建）
- 性能模式降级为纯色不透明背景
- 不使用 QGraphicsBlurEffect / AcrylicBrush（会导致持续重绘卡顿）
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QRadialGradient, QBrush
)


def _is_performance_mode() -> bool:
    try:
        from app.components.hidden_settings import is_performance_mode
        return is_performance_mode()
    except Exception:
        return False


def _is_dark() -> bool:
    try:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())
    except Exception:
        return False


def create_glass_background(width: int, height: int, dark: bool | None = None,
                            variant: str = "content") -> QPixmap:
    """创建毛玻璃质感背景 QPixmap。

    渐变底色 + 多个柔和彩色光斑（径向渐变），模拟被模糊的背景内容。
    光斑本身边缘柔和（径向渐变 alpha 0→峰值→0），无需额外模糊算法。

    variant:
    - "content": 功能区域（浅紫→白 / 深蓝灰→深灰），光斑较多
    - "nav": 侧边栏/标题栏（更深底色），光斑较少较暗，保持视觉层次

    性能：仅在初始化/resize/主题切换时调用一次，paint 事件直接 drawPixmap。
    性能优化：模块级缓存避免相同尺寸+主题重复创建（连续弹窗场景）。
    """
    if dark is None:
        dark = _is_dark()

    w = max(width, 1)
    h = max(height, 1)

    # 缓存查找（相同尺寸+主题+变体直接复用，避免连续弹窗重复创建）
    cache_key = (w, h, dark, variant)
    cached = _glass_bg_cache.get(cache_key)
    if cached is not None and not cached.isNull():
        _glass_bg_cache.move_to_end(cache_key)
        return cached

    pixmap = QPixmap(w, h)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 1. 底色渐变（nav 和 content 统一配色，保证视觉一致性）
    if dark:
        base_grad = QLinearGradient(0, 0, w, h)
        base_grad.setColorAt(0, QColor("#1A1A22"))
        base_grad.setColorAt(1, QColor("#141418"))
    else:
        base_grad = QLinearGradient(0, 0, w, h)
        base_grad.setColorAt(0, QColor("#E8EFFA"))
        base_grad.setColorAt(1, QColor("#F0F5FF"))
    painter.fillRect(pixmap.rect(), base_grad)

    # 2. 柔和彩色光斑（模拟被模糊的背景内容）
    spots = _get_spot_colors(dark, variant)
    for i, (color, cx_ratio, cy_ratio, radius_ratio, alpha) in enumerate(spots):
        cx = w * cx_ratio
        cy = h * cy_ratio
        radius = max(w, h) * radius_ratio
        if radius < 10:
            continue
        grad = QRadialGradient(QPointF(cx, cy), radius)
        c = QColor(color)
        c.setAlpha(alpha)
        grad.setColorAt(0, c)
        c2 = QColor(color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawRect(pixmap.rect())

    painter.end()

    # 缓存存储（LRU 淘汰：满 16 条时淘汰最久未访问的条目）
    if len(_glass_bg_cache) >= 16:
        _glass_bg_cache.popitem(last=False)
    _glass_bg_cache[cache_key] = pixmap

    return pixmap


# 毛玻璃背景缓存（key: (width, height, dark, variant)）
# 使用 OrderedDict 实现 LRU 淘汰：访问时 move_to_end，满时 popitem(last=False)
_glass_bg_cache: OrderedDict[tuple, QPixmap] = OrderedDict()


def _get_spot_colors(dark: bool, variant: str = "content"):
    """返回光斑配置列表：(颜色, 中心X比例, 中心Y比例, 半径比例, alpha)。

    nav 和 content 使用相同的光斑配置，保证侧边栏/标题栏与功能区
    的毛玻璃高光效果完全一致。
    """
    if dark:
        return [
            ("#3B82F6", 0.15, 0.20, 0.45, 115),  # 蓝色光斑（左上）
            ("#2A74DA", 0.85, 0.15, 0.40, 95),   # 深蓝光斑（右上）
            ("#EC4899", 0.75, 0.85, 0.50, 85),   # 粉色光斑（右下）
            ("#10B981", 0.20, 0.80, 0.35, 72),   # 绿色光斑（左下）
            ("#F59E0B", 0.50, 0.50, 0.30, 62),   # 橙色光斑（中央）
        ]
    else:
        return [
            ("#7BA8F0", 0.15, 0.20, 0.45, 85),   # 中蓝光斑（左上）
            ("#5B9BD5", 0.85, 0.15, 0.40, 80),   # 深蓝光斑（右上）
            ("#F0A0C8", 0.75, 0.85, 0.50, 75),   # 中粉光斑（右下）
            ("#6DD9B0", 0.20, 0.80, 0.35, 65),   # 中绿光斑（左下）
            ("#F5C969", 0.50, 0.50, 0.30, 60),   # 中黄光斑（中央）
        ]


def get_card_alpha(dark: bool | None = None) -> int:
    """返回 CardWidget 的推荐背景 alpha 值。

    降低 alpha 让模糊背景光斑透出，呈现毛玻璃通透感。
    适当提高 alpha 保证文字可读性（浅色模式下避免彩色光斑干扰文字）。
    性能模式返回较高 alpha（接近不透明），因为背景没有光斑。
    """
    if dark is None:
        dark = _is_dark()
    if _is_performance_mode():
        # 性能模式：纯色背景，卡片用较高 alpha
        return 200 if not dark else 30
    # 毛玻璃模式：平衡通透感与可读性
    # 浅色：70（半透明白，透出彩色光斑但保证文字可读）；深色：55
    return 70 if not dark else 55


def apply_card_glass_alpha():
    """Monkey-patch CardWidget 的背景色和圆角，统一所有卡片的毛玻璃外观。

    直接修改 qfluentwidgets CardWidget._normalBackgroundColor 等方法，
    让所有卡片自动获得更透明的背景。
    同时 patch __init__ 让所有 CardWidget 默认设置 borderRadius(12)，
    与快捷指令Tab的 CommandCard 视觉保持一致。
    性能模式恢复为接近原始值。

    防链式嵌套：通过 _glass_patched 标记避免重复包裹，
    防止每次主题切换后产生 N+1 层调用链。
    """
    try:
        from qfluentwidgets.components.widgets.card_widget import CardWidget
        from qfluentwidgets.common.style_sheet import isDarkTheme

        # 防链式嵌套：已 patch 过则跳过
        if getattr(CardWidget, '_glass_patched', False):
            return

        _original_normal = CardWidget._normalBackgroundColor
        _original_hover = CardWidget._hoverBackgroundColor
        _original_pressed = CardWidget._pressedBackgroundColor

        def _glass_normal(self):
            return QColor(255, 255, 255, get_card_alpha(dark=isDarkTheme()))

        def _glass_hover(self):
            base = get_card_alpha(dark=isDarkTheme())
            return QColor(255, 255, 255, min(base + 30, 255))

        def _glass_pressed(self):
            base = get_card_alpha(dark=isDarkTheme())
            return QColor(255, 255, 255, max(base - 10, 0))

        CardWidget._normalBackgroundColor = _glass_normal
        CardWidget._hoverBackgroundColor = _glass_hover
        CardWidget._pressedBackgroundColor = _glass_pressed

        # 统一所有 CardWidget 的圆角半径为 12（与 CommandCard 一致）
        _original_init = CardWidget.__init__

        def _glass_init(self, *args, **kwargs):
            _original_init(self, *args, **kwargs)
            try:
                self.setBorderRadius(12)
            except Exception:
                pass

        CardWidget.__init__ = _glass_init
        CardWidget._glass_patched = True
    except Exception:
        pass


def apply_combo_glass_alpha():
    """Monkey-patch ComboBox/EditableComboBox 让下拉框背景半透明，呈现毛玻璃通透感。

    qfluentwidgets ComboBox 通过 FluentStyleSheet 内部 QSS 设置背景色，
    该 QSS 直接作用于 widget，优先级高于父级 stylesheet，需在 _setUpUi 后追加覆盖。
    性能模式恢复为接近原始值。

    防链式嵌套：通过 _glass_patched 标记避免重复包裹。
    """
    try:
        from qfluentwidgets.components.widgets.combo_box import ComboBoxBase

        # 防链式嵌套：已 patch 过则跳过
        if getattr(ComboBoxBase, '_glass_patched', False):
            return

        _original_setUpUi = ComboBoxBase._setUpUi

        def _glass_setUpUi(self):
            _original_setUpUi(self)
            try:
                dark = _is_dark()
                alpha = get_card_alpha(dark=dark)
                if _is_performance_mode():
                    # 性能模式：接近不透明
                    if dark:
                        bg = "rgba(50, 50, 53, 0.90)"
                        hover_bg = "rgba(60, 60, 63, 0.90)"
                    else:
                        bg = "rgba(255, 255, 255, 0.90)"
                        hover_bg = "rgba(249, 249, 249, 0.90)"
                else:
                    if dark:
                        bg = f"rgba(50, 50, 53, {alpha / 255:.2f})"
                        hover_bg = f"rgba(60, 60, 63, {min(alpha + 25, 255) / 255:.2f})"
                    else:
                        bg = f"rgba(255, 255, 255, {alpha / 255:.2f})"
                        hover_bg = f"rgba(255, 255, 255, {min(alpha + 25, 255) / 255:.2f})"

                current_qss = self.styleSheet()
                self.setStyleSheet(current_qss + f"""
                    ComboBox, ModelComboBox, EditableComboBox {{
                        background-color: {bg};
                    }}
                    ComboBox:hover, ModelComboBox:hover, EditableComboBox:hover {{
                        background-color: {hover_bg};
                    }}
                """)
            except Exception:
                pass

        ComboBoxBase._setUpUi = _glass_setUpUi
        ComboBoxBase._glass_patched = True
    except Exception:
        pass


# QSS 缓存（避免每次调用都重新生成字符串）
_qss_cache: dict[str, str] = {}

# 毛玻璃光斑背景缓存（key: "dark" / "light"）
_glass_pixmap_cache: dict[str, QPixmap] = {}


def _build_banner_qss(dark: bool) -> str:
    """构建 Banner 卡片样式（始终生效，不受性能模式影响）。

    可读性优化：
    - 提高背景 alpha（深色 0.78 / 浅色 0.82），保证文字清晰可读
    - 顶部高光渐变（模拟毛玻璃顶部反光），增强毛玻璃通透感
    - hover 时进一步提亮 + 蓝色边框高亮
    """
    if dark:
        # 深色模式：顶部高光 + 深色半透明底
        banner_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                     "stop:0 rgba(55, 58, 68, 0.82), stop:0.5 rgba(42, 44, 52, 0.80), "
                     "stop:1 rgba(35, 37, 44, 0.78))")
        banner_border = "rgba(255, 255, 255, 0.10)"
        hover_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    "stop:0 rgba(65, 70, 82, 0.88), stop:1 rgba(48, 52, 62, 0.85))")
        hover_border = "rgba(42, 116, 218, 0.45)"
    else:
        # 浅色模式：顶部高光 + 浅色半透明底
        banner_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                     "stop:0 rgba(255, 255, 255, 0.88), stop:0.5 rgba(248, 250, 255, 0.85), "
                     "stop:1 rgba(240, 245, 255, 0.82))")
        banner_border = "rgba(42, 116, 218, 0.18)"
        hover_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    "stop:0 rgba(255, 255, 255, 0.95), stop:1 rgba(245, 248, 255, 0.90))")
        hover_border = "rgba(42, 116, 218, 0.40)"

    return f"""
        /* Banner 区域毛玻璃包裹（统一所有TAB标题区域样式）+ hover 高亮反馈
           始终生效，不受性能模式影响，保证主题切换后卡片包裹效果可见 */
        QWidget[banner="true"] {{
            background: {banner_bg};
            border: 1px solid {banner_border};
            border-radius: 12px;
        }}
        QWidget[banner="true"]:hover {{
            background: {hover_bg};
            border: 1px solid {hover_border};
        }}
    """


def apply_banner_style(banner_widget):
    """直接在 banner widget 上应用毛玻璃卡片样式。

    解决 QSS 级联问题：banner widget 通常位于 ScrollArea 内部，
    而 ScrollArea 的 content widget 有自己的 setStyleSheet("background: transparent;")，
    这会创建新的样式作用域，阻止父级 stackedWidget 的 QWidget[banner="true"] 规则级联到 banner。

    使用 #objectName 选择器确保样式只作用于 banner widget 本身，
    不会影响内部的图标、标题等子控件。

    配色与功能区域 create_glass_background() 的底色保持一致：
    - 深色：基于 #1A1A22 / #141418
    - 浅色：基于 #E8EFFA / #F0F5FF
    """
    try:
        from PySide6.QtCore import Qt
        dark = _is_dark()
        # 设置 objectName，用 #objectName 选择器只针对 banner 本身，不影响子控件
        banner_widget.setObjectName("banner_card")
        if dark:
            # 深色模式：与 CardWidget 的白色叠加方案一致（get_card_alpha dark=55 → 0.22），
            # 使用白色半透明叠加，让功能区域的彩色光斑透出，呈现毛玻璃通透感
            banner_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                         "stop:0 rgba(255, 255, 255, 0.18), stop:1 rgba(255, 255, 255, 0.12))")
            banner_border = "rgba(255, 255, 255, 0.10)"
        else:
            # 浅色模式：与 CardWidget 一致（get_card_alpha light=70 → 0.27），
            # 白色半透明叠加，透出功能区域的彩色光斑
            banner_bg = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                         "stop:0 rgba(255, 255, 255, 0.75), stop:1 rgba(240, 245, 255, 0.68))")
            banner_border = "rgba(42, 116, 218, 0.15)"
        banner_widget.setStyleSheet(f"""
            #banner_card {{
                background: {banner_bg};
                border: 1px solid {banner_border};
                border-radius: 12px;
            }}
        """)
        banner_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    except Exception:
        pass


def refresh_banner_style(banner_widget):
    """主题切换时刷新 banner widget 的样式（颜色跟随主题）。

    与 apply_banner_style 配合使用：初始化时调用 apply_banner_style，
    主题切换时调用 refresh_banner_style。
    """
    apply_banner_style(banner_widget)


def glass_widgets_qss() -> str:
    """返回全局控件毛玻璃半透明 QSS。

    应用到主窗口 stackedWidget，让所有子控件半透明透出背景光斑。
    性能模式仅返回 banner 卡片样式（保证基础UI效果始终可见）。

    性能优化：
    - 缓存 QSS 字符串，仅在主题切换时重新生成
    - 不对 QWidget 全局设置 transparent（避免所有控件强制 alpha 合成导致卡顿）
    - 仅对 QLabel/QFrame 等轻量控件设置透明
    """
    dark = _is_dark()

    # Banner 卡片样式始终生效（不受性能模式影响，修复主题切换后卡片消失的bug）
    banner_qss = _build_banner_qss(dark)

    if _is_performance_mode():
        # 性能模式：仅返回 banner 样式，跳过其他轻量控件的透明效果
        return banner_qss

    cache_key = "dark" if dark else "light"
    if cache_key in _qss_cache:
        return _qss_cache[cache_key]

    if dark:
        # 深色模式：控件用深色半透明
        frame_bg = "rgba(35, 35, 40, 0.55)"
        text_bg = "rgba(30, 30, 35, 0.65)"
        scroll_bg = "rgba(40, 40, 45, 0.50)"
        border = "rgba(255, 255, 255, 0.06)"
        text_color = "#E6E1E5"
        check_color = "#E6E1E5"
        indicator_bg = "rgba(255, 255, 255, 0.10)"
        indicator_border = "rgba(255, 255, 255, 0.20)"
        menu_bg = "rgba(30, 30, 35, 0.85)"
    else:
        # 浅色模式：控件用浅色半透明，透出彩色光斑
        frame_bg = "rgba(255, 255, 255, 0.55)"
        text_bg = "rgba(255, 255, 255, 0.70)"
        scroll_bg = "rgba(255, 255, 255, 0.40)"
        border = "rgba(42, 116, 218, 0.12)"
        text_color = "#1D1B20"
        check_color = "#1D1B20"
        indicator_bg = "rgba(255, 255, 255, 0.70)"
        indicator_border = "rgba(42, 116, 218, 0.30)"
        menu_bg = "rgba(255, 255, 255, 0.88)"

    # hover 高亮背景色（比默认稍亮）
    if dark:
        hover_bg = "rgba(50, 50, 58, 0.65)"
        hover_border = "rgba(42, 116, 218, 0.35)"
    else:
        hover_bg = "rgba(255, 255, 255, 0.75)"
        hover_border = "rgba(42, 116, 218, 0.30)"

    qss = f"""
        /* 仅 QLabel 透明（轻量，不影响性能）；不全局设 QWidget transparent 避免卡顿 */
        QLabel {{
            background: transparent;
        }}
        {banner_qss}
        /* QFrame/QGroupBox 半透明 */
        QFrame[glassFrame="true"], QGroupBox {{
            background-color: {frame_bg};
            border: 1px solid {border};
            border-radius: 8px;
            color: {text_color};
        }}
        QGroupBox::title {{
            color: {text_color};
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }}
        /* 文本类控件半透明 */
        QTextEdit, QPlainTextEdit, QTextBrowser {{
            background-color: {text_bg};
            color: {text_color};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        /* QCheckBox / QRadioButton 毛玻璃样式 + 可读文字色 */
        QCheckBox, QRadioButton {{
            background: transparent;
            color: {check_color};
            spacing: 8px;
            padding: 2px 0;
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border: 2px solid {indicator_border};
            border-radius: 4px;
            background: {indicator_bg};
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
        /* QMenu 下拉菜单半透明 */
        QMenu {{
            background-color: {menu_bg};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 24px;
            border-radius: 4px;
            color: {text_color};
        }}
        QMenu::item:selected {{
            background-color: rgba(42, 116, 218, 0.20);
        }}
        QMenu::separator {{
            height: 1px;
            background: {border};
            margin: 4px 8px;
        }}
        /* 滚动条半透明 */
        QScrollBar:vertical {{
            background: {scroll_bg};
            width: 10px;
            border-radius: 5px;
        }}
        QScrollBar:horizontal {{
            background: {scroll_bg};
            height: 10px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: rgba(42, 116, 218, 0.40);
            border-radius: 5px;
            min-height: 30px;
            min-width: 30px;
        }}
        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
            background: rgba(42, 116, 218, 0.60);
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            background: transparent;
            border: none;
            height: 0px;
            width: 0px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}
        /* QComboBox 下拉列表半透明 */
        QComboBox QAbstractItemView {{
            background-color: {menu_bg};
            border: 1px solid {border};
            border-radius: 6px;
            outline: none;
            padding: 4px;
            color: {text_color};
        }}
        QComboBox QAbstractItemView::item {{
            padding: 4px 8px;
            border-radius: 4px;
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: rgba(42, 116, 218, 0.25);
        }}
        /* QToolTip 半透明 */
        QToolTip {{
            background-color: {menu_bg};
            color: {text_color};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 4px 8px;
        }}
        /* QListWidget / QTreeWidget 半透明 */
        QListWidget, QListView, QTreeWidget, QTreeView, QTableWidget, QTableView {{
            background-color: {text_bg};
            color: {text_color};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        /* QSpinBox / QDoubleSpinBox 半透明 */
        QSpinBox, QDoubleSpinBox {{
            background-color: {text_bg};
            color: {text_color};
            border: 1px solid {border};
            border-radius: 6px;
            padding: 4px 6px;
        }}
    """
    _qss_cache[cache_key] = qss
    return qss


def clear_qss_cache():
    """清除 QSS 缓存和毛玻璃背景缓存（主题切换时调用）。"""
    _qss_cache.clear()
    _glass_bg_cache.clear()
    _glass_pixmap_cache.clear()


# ---------------------------------------------------------------------------
# 毛玻璃光斑背景（静态 pixmap，创建一次后缓存，供 paintEvent 使用）
# ---------------------------------------------------------------------------


def create_glass_pixmap(dark: bool, w: int, h: int) -> QPixmap:
    """创建毛玻璃光斑背景 pixmap（与 create_glass_background 相同配色，全窗口尺寸）。"""
    w = max(w, 800)
    h = max(h, 600)

    pixmap = QPixmap(w, h)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if dark:
        base_grad = QLinearGradient(0, 0, w, h)
        base_grad.setColorAt(0, QColor("#1A1A22"))
        base_grad.setColorAt(1, QColor("#141418"))
        painter.fillRect(pixmap.rect(), base_grad)
        spots = [
            (0.15, 0.20, 0.45, QColor("#3B82F6"), 115),
            (0.85, 0.15, 0.40, QColor("#2A74DA"), 95),
            (0.75, 0.85, 0.50, QColor("#EC4899"), 85),
            (0.20, 0.80, 0.35, QColor("#10B981"), 72),
            (0.50, 0.50, 0.30, QColor("#F59E0B"), 62),
        ]
    else:
        base_grad = QLinearGradient(0, 0, w, h)
        base_grad.setColorAt(0, QColor("#E8EFFA"))
        base_grad.setColorAt(1, QColor("#F0F5FF"))
        painter.fillRect(pixmap.rect(), base_grad)
        spots = [
            (0.15, 0.20, 0.45, QColor("#7BA8F0"), 85),
            (0.85, 0.15, 0.40, QColor("#5B9BD5"), 80),
            (0.75, 0.85, 0.50, QColor("#F0A0C8"), 75),
            (0.20, 0.80, 0.35, QColor("#6DD9B0"), 65),
            (0.50, 0.50, 0.30, QColor("#F5C969"), 60),
        ]

    for cx_ratio, cy_ratio, r_ratio, color, alpha in spots:
        cx = w * cx_ratio
        cy = h * cy_ratio
        radius = max(w, h) * r_ratio
        if radius < 10:
            continue
        grad = QRadialGradient(QPointF(cx, cy), radius)
        c = QColor(color)
        c.setAlpha(alpha)
        grad.setColorAt(0, c)
        c2 = QColor(color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawRect(pixmap.rect())

    painter.end()
    return pixmap


def get_glass_pixmap(dark: bool, w: int, h: int) -> QPixmap:
    """获取毛玻璃光斑 pixmap（按主题缓存，尺寸差异 <300px 时复用）。"""
    cache_key = "dark" if dark else "light"
    cached = _glass_pixmap_cache.get(cache_key)
    if cached is not None:
        if abs(cached.width() - w) < 300 and abs(cached.height() - h) < 300:
            return cached
    target_w = max(w, 1200)
    target_h = max(h, 800)
    pixmap = create_glass_pixmap(dark, target_w, target_h)
    _glass_pixmap_cache[cache_key] = pixmap
    return pixmap


def clear_glass_pixmap_cache():
    """清除光斑 pixmap 缓存（主题切换时调用）。"""
    _glass_pixmap_cache.clear()
