"""Consistent widget palettes, including live switches with local typography styles."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


def apply_appearance(window, mode):
    app = QApplication.instance()
    if mode == "System":
        app.setStyle(window.system_style)
        palette = QPalette(window.system_palette)
    else:
        app.setStyle("Fusion")
        palette = QPalette()
        dark = mode == "Dark"
        colors = {
            "Window": "#242424" if dark else "#f4f4f4",
            "WindowText": "#eeeeee" if dark else "#171717",
            "Base": "#191919" if dark else "#ffffff",
            "AlternateBase": "#303030" if dark else "#eeeeee",
            "Text": "#eeeeee" if dark else "#171717",
            "Button": "#303030" if dark else "#eeeeee",
            "ButtonText": "#eeeeee" if dark else "#171717",
            "ToolTipBase": "#303030" if dark else "#ffffdc",
            "ToolTipText": "#eeeeee" if dark else "#171717",
            "PlaceholderText": "#b5b5b5" if dark else "#595959",
            "Highlight": "#356a9c" if dark else "#245b91",
            "HighlightedText": "#ffffff",
            "Link": "#8fc4ff" if dark else "#164b83",
        }
        for role, color in colors.items():
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(
                QPalette.ColorGroup.Disabled, role, QColor("#989898" if dark else "#6e6e6e")
            )
    app.setPalette(palette)
    # Local typography styles can retain their previous palette on Windows.
    for widget in [window, *window.findChildren(QWidget)]:
        widget.setPalette(palette)
    window.update()
