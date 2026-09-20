"""Optional desktop bootstrap; core/CLI stay usable without Qt."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Stimulus Harvester desktop")
    parser.add_argument("--project", help="Explicit project folder to reopen")
    args = parser.parse_args()
    try:
        from PySide6.QtWidgets import QApplication
        from .window import Window
    except ImportError as exc:
        raise SystemExit(
            'Desktop dependency missing. Install: python -m pip install "stimulus-harvester[gui]"'
        ) from exc
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Stimulus Harvester")
    app.setOrganizationName("Stimulus Modernization Suite")
    window = Window()
    window.show()
    if args.project:
        window.session.submit("open", path=args.project)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
