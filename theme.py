"""Centralized palette, spacing, and QSS templates for Helper's UI.

All color tokens are defined here in PALETTE_LIGHT and PALETTE_DARK. UI modules
should call theme.qss(template) to expand a stylesheet template with the active
palette, or theme.value(token) to look up a single color string.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QFont


# ---------------------------------------------------------------------------
# Spacing, radius, fonts — palette-independent design tokens.

@dataclass(frozen=True)
class _Spacing:
    XS: int = 4
    SM: int = 8
    MD: int = 12
    LG: int = 16
    XL: int = 24
    XXL: int = 32


@dataclass(frozen=True)
class _Radius:
    SM: int = 6
    MD: int = 8
    LG: int = 12
    XL: int = 16


SPACING = _Spacing()
RADIUS = _Radius()
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"


def font(size: int, weight: QFont.Weight | None = None) -> QFont:
    f = QFont(FONT_FAMILY, size)
    if weight is not None:
        f.setWeight(weight)
    return f


# ---------------------------------------------------------------------------
# Palettes. Keys must match between light and dark so qss() can substitute
# either dict into the same template.

PALETTE_LIGHT: dict[str, str] = {
    # Surfaces
    "bg_window": "rgb(255, 255, 255)",
    "bg_sidebar": "rgb(248, 249, 251)",
    "bg_topbar": "rgb(252, 252, 253)",
    "bg_card": "rgb(255, 255, 255)",
    "bg_subtle": "rgb(248, 250, 252)",
    "bg_hover": "rgb(243, 244, 246)",
    "bg_nav_selected": "rgb(229, 231, 235)",
    # Borders
    "border_light": "rgb(229, 231, 235)",
    "border_mid": "rgb(209, 213, 219)",
    # Text
    "text_primary": "rgb(17, 24, 39)",
    "text_secondary": "rgb(75, 85, 99)",
    "text_muted": "rgb(107, 114, 128)",
    "text_placeholder": "rgb(156, 163, 175)",
    # Accent
    "accent": "rgb(37, 99, 235)",
    "accent_hover": "rgb(29, 78, 216)",
    "accent_subtle": "rgb(239, 246, 255)",
    # Status pills
    "success_bg": "rgb(220, 252, 231)",
    "success_fg": "rgb(22, 101, 52)",
    "success_border": "rgb(187, 247, 208)",
    "warning_bg": "rgb(254, 249, 195)",
    "warning_fg": "rgb(133, 77, 14)",
    "warning_border": "rgb(253, 224, 71)",
    "danger_bg": "rgb(254, 226, 226)",
    "danger_fg": "rgb(153, 27, 27)",
    "danger_border": "rgb(252, 165, 165)",
    "info_bg": "rgb(219, 234, 254)",
    "info_fg": "rgb(30, 64, 175)",
    "info_border": "rgb(191, 219, 254)",
    "neutral_bg": "rgb(243, 244, 246)",
    "neutral_fg": "rgb(55, 65, 81)",
    "neutral_border": "rgb(229, 231, 235)",
    # Log viewer (always-dark in light theme for readability)
    "log_bg": "rgb(17, 24, 39)",
    "log_fg": "rgb(243, 244, 246)",
    # Primary button (dark-on-light)
    "btn_primary_bg": "rgb(17, 24, 39)",
    "btn_primary_hover": "rgb(31, 41, 55)",
    "btn_primary_pressed": "rgb(55, 65, 81)",
    "btn_primary_disabled": "rgb(156, 163, 175)",
    "btn_primary_fg": "rgb(255, 255, 255)",
    # Misc
    "scroll_handle": "rgba(17, 24, 39, 38)",
    "scroll_handle_hover": "rgba(17, 24, 39, 70)",
    "sidebar_chip_bg": "rgba(17, 24, 39, 6)",
    "sidebar_hover": "rgba(17, 24, 39, 9)",
    "convo_count_bg": "rgb(243, 244, 246)",
}


PALETTE_DARK: dict[str, str] = {
    # Surfaces
    "bg_window": "rgb(15, 18, 24)",
    "bg_sidebar": "rgb(20, 24, 32)",
    "bg_topbar": "rgb(18, 22, 30)",
    "bg_card": "rgb(24, 28, 38)",
    "bg_subtle": "rgb(20, 24, 32)",
    "bg_hover": "rgb(34, 40, 52)",
    "bg_nav_selected": "rgb(38, 44, 58)",
    # Borders
    "border_light": "rgb(40, 46, 60)",
    "border_mid": "rgb(60, 68, 84)",
    # Text
    "text_primary": "rgb(241, 245, 249)",
    "text_secondary": "rgb(203, 213, 225)",
    "text_muted": "rgb(148, 163, 184)",
    "text_placeholder": "rgb(100, 116, 139)",
    # Accent
    "accent": "rgb(96, 165, 250)",
    "accent_hover": "rgb(147, 197, 253)",
    "accent_subtle": "rgba(96, 165, 250, 32)",
    # Status pills
    "success_bg": "rgba(34, 197, 94, 36)",
    "success_fg": "rgb(134, 239, 172)",
    "success_border": "rgba(34, 197, 94, 80)",
    "warning_bg": "rgba(234, 179, 8, 36)",
    "warning_fg": "rgb(250, 204, 21)",
    "warning_border": "rgba(234, 179, 8, 80)",
    "danger_bg": "rgba(239, 68, 68, 36)",
    "danger_fg": "rgb(252, 165, 165)",
    "danger_border": "rgba(239, 68, 68, 80)",
    "info_bg": "rgba(59, 130, 246, 36)",
    "info_fg": "rgb(147, 197, 253)",
    "info_border": "rgba(59, 130, 246, 80)",
    "neutral_bg": "rgba(148, 163, 184, 28)",
    "neutral_fg": "rgb(203, 213, 225)",
    "neutral_border": "rgba(148, 163, 184, 60)",
    # Log viewer
    "log_bg": "rgb(10, 13, 18)",
    "log_fg": "rgb(226, 232, 240)",
    # Primary button (accent-on-dark)
    "btn_primary_bg": "rgb(96, 165, 250)",
    "btn_primary_hover": "rgb(147, 197, 253)",
    "btn_primary_pressed": "rgb(59, 130, 246)",
    "btn_primary_disabled": "rgb(71, 85, 105)",
    "btn_primary_fg": "rgb(15, 18, 24)",
    # Misc
    "scroll_handle": "rgba(226, 232, 240, 50)",
    "scroll_handle_hover": "rgba(226, 232, 240, 90)",
    "sidebar_chip_bg": "rgba(241, 245, 249, 10)",
    "sidebar_hover": "rgba(241, 245, 249, 14)",
    "convo_count_bg": "rgba(241, 245, 249, 10)",
}


def active_palette() -> dict[str, str]:
    """Palette dict for the currently-configured theme. Re-read each call."""
    import config  # local import keeps theme.py importable from config.py if needed

    name = (getattr(config, "THEME", "light") or "light").strip().lower()
    return PALETTE_DARK if name == "dark" else PALETTE_LIGHT


# ---------------------------------------------------------------------------
# QSS templates. Use {{ }} for literal CSS braces and {token} for substitution.

FRAME_QSS = """
#MainFrame {{
    background: {bg_window};
    border-radius: 0px;
    border: 1px solid {border_light};
}}
"""

SIDEBAR_QSS = """
#Sidebar {{
    background: {bg_sidebar};
    border-right: 1px solid {border_light};
}}
QLabel#SidebarBrand {{
    color: {text_primary};
    background: transparent;
    border: none;
}}
QLabel#SidebarSubtitle {{
    color: {text_muted};
    background: transparent;
    border: none;
}}
QLabel#SidebarFootChip {{
    color: {text_secondary};
    background: {sidebar_chip_bg};
    border: 1px solid {border_light};
    border-radius: 10px;
    padding: 8px 10px;
}}
"""

NAV_BUTTON_QSS = """
QPushButton#NavButton {{
    background: transparent;
    color: {text_secondary};
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    padding: 8px 14px 8px 11px;
    margin: 1px 0px;
    text-align: left;
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QPushButton#NavButton:hover {{
    background: {sidebar_hover};
    color: {text_primary};
}}
QPushButton#NavButton:checked {{
    background: {bg_nav_selected};
    color: {text_primary};
    border-left: 3px solid {accent};
    font-weight: 600;
}}
"""

BACK_BUTTON_QSS = """
QPushButton {{
    background: transparent;
    color: {text_secondary};
    border: none;
    border-radius: 6px;
    padding: 4px 6px;
    text-align: left;
    font-family: 'Segoe UI';
    font-size: 9pt;
}}
QPushButton:hover {{
    background: {sidebar_hover};
    color: {text_primary};
}}
"""

INPUT_QSS = """
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {bg_card};
    border: 1px solid {border_mid};
    border-radius: 8px;
    padding: 7px 11px;
    color: {text_primary};
    font-family: 'Segoe UI';
    font-size: 10pt;
    min-height: 18px;
    selection-background-color: {accent};
    selection-color: white;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border: 1px solid {text_muted};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
    background: {bg_card};
}}
QComboBox QAbstractItemView {{
    background: {bg_card};
    color: {text_primary};
    selection-background-color: {bg_hover};
    selection-color: {text_primary};
    border: 1px solid {border_light};
    border-radius: 8px;
}}
QPlainTextEdit, QTextEdit {{
    background: {bg_card};
    color: {text_primary};
    border: 1px solid {border_mid};
    border-radius: 8px;
    padding: 8px 10px;
    font-family: 'Segoe UI';
    font-size: 10pt;
    selection-background-color: {accent};
    selection-color: white;
}}
"""

BUTTON_QSS = """
QPushButton {{
    background: {btn_primary_bg};
    border: 1px solid {btn_primary_bg};
    border-radius: 8px;
    padding: 8px 16px;
    color: {btn_primary_fg};
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QPushButton:hover {{
    background: {btn_primary_hover};
    border: 1px solid {btn_primary_hover};
}}
QPushButton:pressed {{
    background: {btn_primary_pressed};
}}
QPushButton:disabled {{
    color: rgba(255, 255, 255, 145);
    background: {btn_primary_disabled};
    border: 1px solid {btn_primary_disabled};
}}
"""

GHOST_BUTTON_QSS = """
QPushButton {{
    background: {bg_card};
    border: 1px solid {border_mid};
    border-radius: 8px;
    padding: 8px 16px;
    color: {text_primary};
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QPushButton:hover {{
    background: {bg_hover};
}}
QPushButton:pressed {{
    background: {bg_nav_selected};
}}
"""

SEGMENTED_BUTTON_QSS = """
QPushButton {{
    background: {bg_card};
    border: 1px solid {border_mid};
    border-radius: 0px;
    padding: 7px 14px;
    color: {text_secondary};
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QPushButton:hover {{
    background: {bg_hover};
    color: {text_primary};
}}
QPushButton:checked {{
    background: {accent_subtle};
    color: {accent};
    border: 1px solid {accent};
    font-weight: 600;
}}
"""

SCROLL_QSS = """
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 10px;
    margin: 8px 4px 8px 0px;
}}
QScrollBar::handle:vertical {{
    background: {scroll_handle};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: {scroll_handle_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    border: none;
    background: none;
}}
"""

LOG_QSS = """
QPlainTextEdit {{
    background: {log_bg};
    color: {log_fg};
    border: 1px solid {border_light};
    border-radius: 10px;
    padding: 10px 12px;
    font-family: 'Consolas';
    font-size: 9pt;
}}
"""

CONVO_ROW_QSS = """
QPushButton#ConvoRow {{
    background: {bg_card};
    border: 1px solid {border_light};
    border-radius: 10px;
    padding: 12px 16px;
    text-align: left;
    color: {text_primary};
    font-family: 'Segoe UI';
    font-size: 10pt;
}}
QPushButton#ConvoRow:hover {{
    background: {bg_hover};
    border: 1px solid {border_mid};
}}
QPushButton#ConvoRow:pressed {{
    background: {bg_nav_selected};
}}
"""

CARD_QSS = (
    "background: {bg_card}; "
    "border: 1px solid {border_light}; "
    "border-radius: 12px;"
)

STAT_CARD_QSS = CARD_QSS

FOOTER_BAR_QSS = (
    "background-color: {bg_subtle}; "
    "border-top: 1px solid {border_light}; "
    "border-radius: 0px;"
)

# Status pills — kind ∈ {success, warning, danger, info, neutral}.
PILL_QSS = """
QLabel#StatusPill[kind="success"] {{
    background: {success_bg}; color: {success_fg};
    border: 1px solid {success_border};
}}
QLabel#StatusPill[kind="warning"] {{
    background: {warning_bg}; color: {warning_fg};
    border: 1px solid {warning_border};
}}
QLabel#StatusPill[kind="danger"] {{
    background: {danger_bg}; color: {danger_fg};
    border: 1px solid {danger_border};
}}
QLabel#StatusPill[kind="info"] {{
    background: {info_bg}; color: {info_fg};
    border: 1px solid {info_border};
}}
QLabel#StatusPill[kind="neutral"] {{
    background: {neutral_bg}; color: {neutral_fg};
    border: 1px solid {neutral_border};
}}
QLabel#StatusPill {{
    border-radius: 10px;
    padding: 3px 10px;
    font-family: 'Segoe UI';
    font-size: 9pt;
}}
"""


def qss(template: str, palette: dict[str, str] | None = None) -> str:
    """Substitute palette tokens into a QSS template."""
    pal = palette if palette is not None else active_palette()
    return template.format(**pal)


def value(token: str, palette: dict[str, str] | None = None) -> str:
    """Resolve a single color token in the active (or supplied) palette."""
    pal = palette if palette is not None else active_palette()
    return pal[token]


def inline(*declarations: str, palette: dict[str, str] | None = None) -> str:
    """Build an inline stylesheet snippet from {token}-containing fragments.

    Example: theme.inline("color: {text_muted}", "background: transparent")
    """
    pal = palette if palette is not None else active_palette()
    return "; ".join(d.format(**pal) for d in declarations) + ";"
