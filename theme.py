"""Centralized palette, spacing, and QSS templates for Helper's UI.

All color tokens are defined here in PALETTE_LIGHT and PALETTE_DARK. UI modules
should call theme.qss(template) to expand a stylesheet template with the active
palette, or theme.value(token) to look up a single color string.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from PyQt6.QtGui import QColor, QFont, QPalette


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
    'bg_window': '#f7f5fb',
    'bg_sidebar': '#eeeaf6',
    'bg_topbar': '#ffffff',
    'bg_card': '#ffffff',
    'bg_subtle': '#f1edf8',
    'bg_hover': '#eee8f8',
    'bg_nav_selected': '#e4d8f5',
    'border_light': '#e4ddeb',
    'border_mid': '#cec2de',
    'text_primary': '#272035',
    'text_secondary': '#5c526d',
    'text_muted': '#756985',
    'text_placeholder': '#81768f',
    'accent': '#7434bd',
    'accent_hover': '#6125a6',
    'accent_subtle': '#eee4fa',
    'success_bg': 'rgb(220, 252, 231)',
    'success_fg': 'rgb(22, 101, 52)',
    'success_border': 'rgb(187, 247, 208)',
    'warning_bg': 'rgb(254, 249, 195)',
    'warning_fg': 'rgb(133, 77, 14)',
    'warning_border': 'rgb(253, 224, 71)',
    'danger_bg': 'rgb(254, 226, 226)',
    'danger_fg': 'rgb(153, 27, 27)',
    'danger_border': 'rgb(252, 165, 165)',
    'info_bg': '#eee4fa',
    'info_fg': '#672da6',
    'info_border': '#d8bff1',
    'neutral_bg': 'rgb(243, 244, 246)',
    'neutral_fg': 'rgb(55, 65, 81)',
    'neutral_border': 'rgb(229, 231, 235)',
    'log_bg': '#1c1627',
    'log_fg': '#eee5f6',
    'btn_primary_bg': '#7434bd',
    'btn_primary_hover': '#6125a6',
    'btn_primary_pressed': '#50208b',
    'btn_primary_disabled': '#c8bbd8',
    'btn_primary_fg': '#ffffff',
    'scroll_handle': '#c7b8d8',
    'scroll_handle_hover': '#9c80ba',
    'sidebar_chip_bg': '#e8e0f2',
    'sidebar_hover': '#e6def1',
    'convo_count_bg': '#e8def4',
    'user_bg': '#eee4fa',
    'user_fg': '#3d1b61',
    'user_border': '#ddc8ef',
}


PALETTE_DARK: dict[str, str] = {
    'bg_window': '#17121f',
    'bg_sidebar': '#1c1627',
    'bg_topbar': '#211a2d',
    'bg_card': '#241d30',
    'bg_subtle': '#20192c',
    'bg_hover': '#30243f',
    'bg_nav_selected': '#3c2a51',
    'border_light': '#3b2e4b',
    'border_mid': '#5b466f',
    'text_primary': '#f3edf9',
    'text_secondary': '#d2c6df',
    'text_muted': '#b0a0c2',
    'text_placeholder': '#a291b4',
    'accent': '#c79bff',
    'accent_hover': '#d7b6ff',
    'accent_subtle': '#352344',
    'success_bg': 'rgba(34, 197, 94, 36)',
    'success_fg': 'rgb(134, 239, 172)',
    'success_border': 'rgba(34, 197, 94, 80)',
    'warning_bg': 'rgba(234, 179, 8, 36)',
    'warning_fg': 'rgb(250, 204, 21)',
    'warning_border': 'rgba(234, 179, 8, 80)',
    'danger_bg': 'rgba(239, 68, 68, 36)',
    'danger_fg': 'rgb(252, 165, 165)',
    'danger_border': 'rgba(239, 68, 68, 80)',
    'info_bg': '#352344',
    'info_fg': '#d7b6ff',
    'info_border': '#604079',
    'neutral_bg': 'rgba(148, 163, 184, 28)',
    'neutral_fg': 'rgb(203, 213, 225)',
    'neutral_border': 'rgba(148, 163, 184, 60)',
    'log_bg': '#120e19',
    'log_fg': '#eee5f6',
    'btn_primary_bg': '#b58ae9',
    'btn_primary_hover': '#cba4f8',
    'btn_primary_pressed': '#a174d7',
    'btn_primary_disabled': '#52405f',
    'btn_primary_fg': '#21152f',
    'scroll_handle': '#5b466f',
    'scroll_handle_hover': '#9474b0',
    'sidebar_chip_bg': '#2b2139',
    'sidebar_hover': '#2c213b',
    'convo_count_bg': '#352344',
    'user_bg': '#3a2850',
    'user_fg': '#f0e2ff',
    'user_border': '#5f407c',
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
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-position: top right; subcontrol-origin: padding; }}
QComboBox::down-arrow {{ image: url({arrow_down}); width: 10px; height: 6px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 25px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ border: none; width: 21px; subcontrol-origin: border; subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ border: none; width: 21px; subcontrol-origin: border; subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({arrow_up}); width: 8px; height: 5px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({arrow_down}); width: 8px; height: 5px; }}
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
    border-radius: 8px;
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
    border: none; background: none; height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
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

CARD_QSS = "QFrame#SurfaceCard {{ background: {bg_card}; border: 1px solid {border_light}; border-radius: 14px; }}"

STAT_CARD_QSS = CARD_QSS

FOOTER_BAR_QSS = "QFrame#SettingsFooter {{ background: {bg_subtle}; border-top: 1px solid {border_light}; }}"


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
    pal = dict(palette if palette is not None else active_palette())
    variant = 'dark' if pal['bg_window'] == PALETTE_DARK['bg_window'] else 'light'
    assets = Path(__file__).parent / 'assets' / 'ui'
    pal.update(arrow_down=(assets / f'chevron-down-{variant}.svg').as_posix(),
               arrow_up=(assets / f'chevron-up-{variant}.svg').as_posix(),
               checkmark=(assets / f'checkmark-{variant}.svg').as_posix())
    return template.format(**pal)


def value(token: str, palette: dict[str, str] | None = None) -> str:
    """Resolve a single color token in the active (or supplied) palette."""
    pal = palette if palette is not None else active_palette()
    return pal[token]


def color(token: str) -> QColor:
    """Resolve a painter color using the same tokens as widget styles."""
    spec = value(token)
    if spec.startswith('rgb'):
        parts = [int(v.strip()) for v in spec[spec.index('(') + 1:spec.index(')')].split(',')]
        return QColor(*parts)
    return QColor(spec)


def apply_application_theme(app) -> None:
    """Theme native controls too, including dialogs, tooltips and popups."""
    app.setStyle('Fusion')
    palette = QPalette()
    for role, token in {
        QPalette.ColorRole.Window: 'bg_window', QPalette.ColorRole.WindowText: 'text_primary',
        QPalette.ColorRole.Base: 'bg_card', QPalette.ColorRole.AlternateBase: 'bg_subtle',
        QPalette.ColorRole.Text: 'text_primary', QPalette.ColorRole.PlaceholderText: 'text_placeholder',
        QPalette.ColorRole.Button: 'bg_card', QPalette.ColorRole.ButtonText: 'text_primary',
        QPalette.ColorRole.Highlight: 'accent', QPalette.ColorRole.HighlightedText: 'bg_card',
        QPalette.ColorRole.ToolTipBase: 'bg_card', QPalette.ColorRole.ToolTipText: 'text_primary',
        QPalette.ColorRole.Link: 'accent',
    }.items():
        palette.setColor(role, color(token))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, color('text_muted'))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, color('text_muted'))
    app.setPalette(palette)
    app.setFont(font(10))
    app.setStyleSheet(qss(CONTROLS_QSS))


def apply_window_chrome(widget) -> None:
    """Match the Windows title bar to the selected light or dark palette."""
    from PyQt6.QtWidgets import QApplication

    if sys.platform != 'win32' or QApplication.platformName() != 'windows':
        return
    import ctypes

    try:
        hwnd = int(widget.winId())
        dark = ctypes.c_int(active_palette() is PALETTE_DARK)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(dark), 4)
        for attribute, token in ((35, 'bg_topbar'), (36, 'text_primary')):
            c = color(token)
            native = ctypes.c_uint(c.red() | (c.green() << 8) | (c.blue() << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(native), 4)
    except (AttributeError, OSError):
        pass  # Older Windows versions keep their standard title bar.


MENU_QSS = """
QMenu {{ background: {bg_card}; color: {text_primary}; border: 1px solid {border_mid}; padding: 6px; }}
QMenu::item {{ padding: 9px 24px; border-radius: 6px; background: transparent; }}
QMenu::item:selected {{ background: {accent_subtle}; color: {accent}; }}
QMenu::separator {{ height: 1px; background: {border_light}; margin: 6px; }}
"""

CONTROLS_QSS = MENU_QSS + """
QToolTip {{ background: {bg_card}; color: {text_primary}; border: 1px solid {border_mid}; padding: 8px; }}
QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {border_light}; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; background: {accent}; border: 2px solid {bg_card}; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {accent_hover}; }}
QCheckBox {{ color: {text_secondary}; spacing: 8px; }}
QMessageBox {{ background: {bg_window}; color: {text_primary}; }}
QMessageBox QLabel {{ color: {text_primary}; background: transparent; }}
QMessageBox QPushButton {{ background: {bg_card}; color: {text_primary}; padding: 8px 18px; border: 1px solid {border_mid}; border-radius: 8px; }}
QMessageBox QPushButton:default {{ background: {btn_primary_bg}; color: {btn_primary_fg}; }}
"""

CHECKBOX_QSS = """
QCheckBox {{ color: {text_secondary}; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {border_mid}; border-radius: 5px; background: {bg_card}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {btn_primary_bg}; border-color: {btn_primary_bg}; image: url({checkmark}); }}
QCheckBox::indicator:disabled {{ background: {bg_subtle}; border-color: {border_light}; }}
"""


def inline(*declarations: str, palette: dict[str, str] | None = None) -> str:
    """Build an inline stylesheet snippet from {token}-containing fragments.

    Example: theme.inline("color: {text_muted}", "background: transparent")
    """
    pal = palette if palette is not None else active_palette()
    return "; ".join(d.format(**pal) for d in declarations) + ";"
