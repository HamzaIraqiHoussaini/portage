"""Desktop app entry: embed FastAPI + open a native window (no terminal)."""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from typing import Callable

import uvicorn

from .config import get_settings
from .main import app


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _show_error(message: str, title: str = "Portage") -> None:
    """Best-effort native error when there is no console (frozen builds)."""
    print(message, file=sys.stderr)
    try:
        if sys.platform == "darwin":
            import subprocess

            subprocess.run(
                ["osascript", "-e", f'display alert "{title}" message "{message}" as critical'],
                check=False,
            )
            return
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # type: ignore[attr-defined]
            return
        # Linux: try zenity / notify-send
        import shutil
        import subprocess

        if shutil.which("zenity"):
            subprocess.run(["zenity", "--error", f"--title={title}", f"--text={message}"], check=False)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "-u", "critical", title, message], check=False)
    except Exception:
        pass


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred: int) -> int:
    if _port_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str, timeout: float = 20.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:  # noqa: S310 — localhost only
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.15)
    return False


def start_server(host: str, port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        server.run()

    thread = threading.Thread(target=_run, name="uvicorn", daemon=True)
    thread.start()
    return server


def open_window(url: str, title: str = "Portage") -> None:
    try:
        import webview
    except ImportError:
        if _is_frozen():
            _show_error(
                "Desktop window support (pywebview) is missing from this build. "
                "Reinstall with requirements-desktop.txt or use the browser at "
                f"{url}"
            )
            raise SystemExit(1) from None
        webbrowser.open(url)
        print(
            "pywebview not installed — opened your default browser instead.\n"
            "Install desktop deps: pip install -r requirements-desktop.txt",
            file=sys.stderr,
        )
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    window = webview.create_window(
        title,
        url=url,
        width=1280,
        height=860,
        min_size=(900, 600),
        background_color="#E7EFE8",
    )
    webview.start()
    _ = window


def run(on_ready: Callable[[str], None] | None = None) -> None:
    settings = get_settings()
    host = settings.app_host or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = pick_port(host, settings.app_port)
    url = f"http://{host}:{port}/"

    start_server(host, port)
    if not wait_ready(url):
        msg = f"Server failed to start at {url}"
        if _is_frozen():
            _show_error(msg)
        raise RuntimeError(msg)

    if on_ready:
        on_ready(url)
    open_window(url)


def main() -> None:
    try:
        run()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_frozen():
            _show_error(str(exc))
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    main()
