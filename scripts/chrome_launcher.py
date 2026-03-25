"""Chrome process management (cross-platform).

Mirrors the process management part of Go browser/browser.go.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from threads.stealth import STEALTH_ARGS

logger = logging.getLogger(__name__)

# Default remote debugging port
DEFAULT_PORT = 8666

# Global process tracking
_chrome_process: subprocess.Popen | None = None

# Default Chrome paths per platform
_CHROME_PATHS: dict[str, list[str]] = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "Linux": [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
}


def _get_default_data_dir() -> str:
    """Return the default Chrome profile directory path (project-local)."""
    return str(Path(__file__).parent.parent / "profile")


def _is_snap_chrome(chrome_bin: str) -> bool:
    """Return True if the Chrome binary is a snap package."""
    try:
        resolved = str(Path(chrome_bin).resolve())
        if "/snap/" in resolved:
            return True
        # Check if it's a wrapper script that delegates to snap
        if os.path.isfile(chrome_bin):
            with open(chrome_bin, "rb") as f:
                head = f.read(256)
                if b"/snap/" in head or b"snap" in head:
                    return True
    except OSError:
        pass
    return False


def _is_snap_writable(path: str) -> bool:
    """Check if snap Chromium can write to the given directory."""
    # Snap Chromium can write to /tmp, ~/snap/chromium/, and $HOME
    p = str(Path(path).resolve())
    return p.startswith("/tmp") or "/snap/chromium/" in p


def _copy_profile_for_snap(src_dir: str) -> str:
    """Copy profile to a snap-writable temp location and return the new path."""
    import tempfile

    dest = os.path.join(tempfile.gettempdir(), "threads-chrome-profile")
    # Remove stale singleton files that prevent launch
    if os.path.isdir(dest):
        for f in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(dest, f))
    _snap_ignore = shutil.ignore_patterns("Singleton*", "RunningChromeVersion")
    if os.path.isdir(src_dir):
        # Copy only if source is newer or dest doesn't exist
        if not os.path.isdir(dest):
            shutil.copytree(src_dir, dest, ignore=_snap_ignore)
        else:
            # Sync: copy Default/ subdirectory (contains cookies/session)
            src_default = os.path.join(src_dir, "Default")
            dst_default = os.path.join(dest, "Default")
            if os.path.isdir(src_default):
                shutil.copytree(src_default, dst_default, dirs_exist_ok=True, ignore=_snap_ignore)
    logger.info("Copied profile for snap Chromium: %s -> %s", src_dir, dest)
    return dest


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """TCP socket-level port check (responds within a second)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False


def find_chrome() -> str | None:
    """Find the Chrome executable path."""
    # Environment variable takes precedence
    env_path = os.getenv("CHROME_BIN")
    if env_path and os.path.isfile(env_path):
        return env_path

    # which/where lookup (including Windows chrome.exe)
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium-browser")
        or shutil.which("chromium")
        or shutil.which("chrome")
        or shutil.which("chrome.exe")
    )
    if chrome:
        return chrome

    # Platform default paths
    system = platform.system()

    # Windows: also check environment variable paths
    if system == "Windows":
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if base:
                candidate = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if os.path.isfile(candidate):
                    return candidate

    for path in _CHROME_PATHS.get(system, []):
        if os.path.isfile(path):
            return path

    return None


def is_chrome_running(port: int = DEFAULT_PORT) -> bool:
    """Check if Chrome is running on the given port (TCP-level check)."""
    return is_port_open(port)


def launch_chrome(
    port: int = DEFAULT_PORT,
    headless: bool = False,
    user_data_dir: str | None = None,
    chrome_bin: str | None = None,
) -> subprocess.Popen | None:
    """Launch a Chrome process with remote debugging enabled.

    Args:
        port: Remote debugging port.
        headless: Whether to run in headless mode.
        user_data_dir: User data directory (profile isolation), default ~/.threads/chrome-profile.
        chrome_bin: Path to the Chrome executable.

    Returns:
        Chrome subprocess, or None if already running.

    Raises:
        FileNotFoundError: Chrome not found.
    """
    global _chrome_process

    # Already running — skip
    if is_port_open(port):
        logger.info("Chrome is already running (port=%d), skipping launch", port)
        return None

    if not chrome_bin:
        chrome_bin = find_chrome()
    if not chrome_bin:
        raise FileNotFoundError(
            "Chrome not found; set CHROME_BIN or install Chrome"
        )

    # Default user-data-dir
    if not user_data_dir:
        user_data_dir = _get_default_data_dir()

    # Snap-confined Chromium cannot write to arbitrary directories.
    # Detect snap and copy the profile to a temp location if needed.
    if _is_snap_chrome(chrome_bin) and not _is_snap_writable(user_data_dir):
        user_data_dir = _copy_profile_for_snap(user_data_dir)

    args = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-sandbox",
        *STEALTH_ARGS,
    ]

    if headless:
        args.append("--headless=new")

    # Proxy: prefer THREADS_PROXY, otherwise fall back to system proxy env vars
    proxy = (
        os.getenv("THREADS_PROXY")
        or os.getenv("ALL_PROXY")
        or os.getenv("all_proxy")  # noqa: SIM112 - lowercase is standard Unix convention
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )
    if proxy:
        args.append(f"--proxy-server={proxy}")
        logger.info("Using proxy: %s", _mask_proxy(proxy))

    logger.info("Launching Chrome: port=%d, headless=%s, profile=%s", port, headless, user_data_dir)
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _chrome_process = process

    # Wait for Chrome to be ready
    _wait_for_chrome(port)

    # Snap Chromium may need GPU interface connected on headless servers
    if not is_port_open(port) and _is_snap_chrome(chrome_bin):
        subprocess.run(
            ["snap", "connect", "chromium:gpu-2404", "mesa-2404:gpu-2404"],
            capture_output=True,
        )
        close_chrome(process)
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _chrome_process = process
        _wait_for_chrome(port)

    return process


def close_chrome(process: subprocess.Popen) -> None:
    """Close the Chrome process."""
    if process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        process.kill()
        process.wait(timeout=3)

    logger.info("Chrome process closed")


def kill_chrome(port: int = DEFAULT_PORT) -> None:
    """Close the Chrome instance on the given port.

    Strategy: CDP Browser.close → terminate tracked process → find and kill by port.

    Args:
        port: Chrome debugging port.
    """
    global _chrome_process

    # Strategy 1: close via CDP
    try:
        import os

        import requests

        _proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SOCKS_PROXY",
                       "http_proxy", "https_proxy", "all_proxy", "socks_proxy"]
        s = requests.Session()
        s.proxies = {"http": None, "https": None}  # type: ignore[assignment]
        resp = s.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        if resp.status_code == 200:
            ws_url = resp.json().get("webSocketDebuggerUrl")
            if ws_url:
                import websockets.sync.client

                saved = {k: os.environ.pop(k) for k in _proxy_vars if k in os.environ}
                try:
                    ws = websockets.sync.client.connect(ws_url)
                    ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    ws.close()
                finally:
                    os.environ.update(saved)
                logger.info("Closed Chrome via CDP Browser.close (port=%d)", port)
                time.sleep(1)
    except Exception:
        pass

    # Strategy 2: terminate the tracked subprocess
    if _chrome_process and _chrome_process.poll() is None:
        try:
            _chrome_process.terminate()
            _chrome_process.wait(timeout=5)
            logger.info("Closed tracked Chrome process via terminate")
        except Exception:
            with contextlib.suppress(Exception):
                _chrome_process.kill()
    _chrome_process = None

    # Strategy 3: find and kill the process by port (cross-platform)
    if is_port_open(port):
        pids = _find_pids_by_port(port)
        if pids:
            for pid in pids:
                _kill_pid(pid)
            logger.info("Closed Chrome by killing process (port=%d)", port)

    # Wait for port to be released (up to 5s)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not is_port_open(port):
            return
        time.sleep(0.5)

    if is_port_open(port):
        logger.warning("Port %d is still in use; kill may not have taken full effect", port)


def ensure_chrome(
    port: int = DEFAULT_PORT,
    headless: bool = False,
    user_data_dir: str | None = None,
    chrome_bin: str | None = None,
) -> bool:
    """Ensure Chrome is available on the given port (single entry point).

    If Chrome is already running, return True immediately.
    Otherwise attempt to launch Chrome and wait for the port to be ready.

    Args:
        port: Remote debugging port.
        headless: Whether to run headless (only applies to a fresh launch).
        user_data_dir: User data directory.
        chrome_bin: Path to the Chrome executable.

    Returns:
        True if Chrome is available, False if launch failed.
    """
    if is_port_open(port):
        return True

    try:
        launch_chrome(
            port=port, headless=headless, user_data_dir=user_data_dir, chrome_bin=chrome_bin,
        )
        return is_port_open(port)
    except FileNotFoundError as e:
        logger.error("Failed to launch Chrome: %s", e)
        return False


def restart_chrome(
    port: int = DEFAULT_PORT,
    headless: bool = False,
    user_data_dir: str | None = None,
    chrome_bin: str | None = None,
) -> subprocess.Popen | None:
    """Restart Chrome: close the current instance, then launch fresh.

    Args:
        port: Remote debugging port.
        headless: Whether to run in headless mode.
        user_data_dir: User data directory.
        chrome_bin: Path to the Chrome executable.

    Returns:
        New Chrome subprocess, or None.
    """
    logger.info("Restarting Chrome: port=%d, headless=%s", port, headless)
    kill_chrome(port)
    time.sleep(1)
    return launch_chrome(
        port=port,
        headless=headless,
        user_data_dir=user_data_dir,
        chrome_bin=chrome_bin,
    )


def _wait_for_chrome(port: int, timeout: float = 15.0) -> None:
    """Wait for the Chrome debugging port to be ready (TCP-level check)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_port_open(port):
            logger.info("Chrome is ready (port=%d)", port)
            return
        time.sleep(0.5)
    logger.warning("Timed out waiting for Chrome to be ready (port=%d)", port)


def _find_pids_by_port(port: int) -> list[int]:
    """Find PIDs of processes occupying the given port (cross-platform)."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []
            pids: list[int] = []
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    with contextlib.suppress(ValueError, IndexError):
                        pids.append(int(parts[-1]))
            return list(set(pids))
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            pids = []
            for p in result.stdout.strip().split("\n"):
                with contextlib.suppress(ValueError):
                    pids.append(int(p))
            return pids
    except Exception:
        return []


def _kill_pid(pid: int) -> None:
    """Kill the process with the given PID (cross-platform)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=5,
            )
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
    except Exception:
        logger.debug("Failed to kill process %d", pid)


def _mask_proxy(proxy_url: str) -> str:
    """Mask sensitive credentials in a proxy URL."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(proxy_url)
        if parsed.username:
            return proxy_url.replace(parsed.username, "***").replace(parsed.password or "", "***")
    except Exception:
        pass
    return proxy_url


def has_display() -> bool:
    """Detect whether the current environment has a GUI (used to auto-select login method)."""
    system = platform.system()
    if system in ("Windows", "Darwin"):
        return True  # Windows / macOS have a GUI by default
    # Linux: check DISPLAY or WAYLAND_DISPLAY environment variables
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Launch Chrome with remote debugging port")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Debug port (default {DEFAULT_PORT})",
    )
    parser.add_argument("--headless", action="store_true", help="Headless mode")
    parser.add_argument(
        "--profile", default=None,
        help="Chrome Profile directory (absolute path)",
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="Force restart (close existing first)",
    )
    args = parser.parse_args()

    if args.restart and is_port_open(args.port):
        print(f"Closing existing Chrome instance (port={args.port}) ...")
        kill_chrome(args.port)

    if is_port_open(args.port):
        print(f"Chrome is already running (port={args.port})")
    else:
        print(f"Starting Chrome (port={args.port}, headless={args.headless}) ...")
        try:
            launch_chrome(port=args.port, headless=args.headless, user_data_dir=args.profile)
            print(f"Chrome is ready (port={args.port})")
            print(f"  Profile directory: {args.profile or _get_default_data_dir()}")
            print("  Now open https://www.threads.com in the browser and log in")
        except FileNotFoundError as e:
            print(f"Error: {e}")
            raise SystemExit(1) from e
