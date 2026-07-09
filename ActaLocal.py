"""Lanzador de ActaLocal."""

from __future__ import annotations


def _set_dpi_awareness() -> None:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v1
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    _set_dpi_awareness()
    from actalocal.app import main as run
    run()


if __name__ == "__main__":
    main()
