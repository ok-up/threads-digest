"""Anti-detection config: UA / Client Hints / Chrome launch args.

Threads has lighter bot-detection, so only the essentials are kept:
- Chrome launch args (STEALTH_ARGS) — fully generic
- UA override (build_ua_override) — fully generic
- Basic JS injection — removes webdriver flag, patches chrome.runtime

Key principle: UA, navigator.platform, and Client Hints must be consistent
with the actual platform.
"""

from __future__ import annotations

import platform as _platform

# Chrome version — update periodically to match the mainstream release
_CHROME_VER = "136"
_CHROME_FULL_VER = "136.0.0.0"


def _build_platform_config() -> dict:
    """Build a consistent UA / Client Hints config based on the actual OS."""
    system = _platform.system()

    brands = [
        {"brand": "Chromium", "version": _CHROME_VER},
        {"brand": "Google Chrome", "version": _CHROME_VER},
        {"brand": "Not-A.Brand", "version": "24"},
    ]
    full_version_list = [
        {"brand": "Chromium", "version": _CHROME_FULL_VER},
        {"brand": "Google Chrome", "version": _CHROME_FULL_VER},
        {"brand": "Not-A.Brand", "version": "24.0.0.0"},
    ]

    if system == "Darwin":
        return {
            "ua": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
            ),
            "nav_platform": "MacIntel",
            "ua_metadata": {
                "brands": brands,
                "fullVersionList": full_version_list,
                "platform": "macOS",
                "platformVersion": "14.5.0",
                "architecture": "arm" if _platform.machine() == "arm64" else "x86",
                "model": "",
                "mobile": False,
                "bitness": "64",
                "wow64": False,
            },
        }

    if system == "Windows":
        return {
            "ua": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
            ),
            "nav_platform": "Win32",
            "ua_metadata": {
                "brands": brands,
                "fullVersionList": full_version_list,
                "platform": "Windows",
                "platformVersion": "15.0.0",
                "architecture": "x86",
                "model": "",
                "mobile": False,
                "bitness": "64",
                "wow64": False,
            },
        }

    # Linux
    return {
        "ua": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{_CHROME_FULL_VER} Safari/537.36"
        ),
        "nav_platform": "Linux x86_64",
        "ua_metadata": {
            "brands": brands,
            "fullVersionList": full_version_list,
            "platform": "Linux",
            "platformVersion": "6.5.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    }


PLATFORM_CONFIG = _build_platform_config()
REALISTIC_UA = PLATFORM_CONFIG["ua"]


def build_ua_override(chrome_full_ver: str | None = None) -> dict:
    """Build Emulation.setUserAgentOverride parameters.

    Args:
        chrome_full_ver: Full Chrome version string (obtained from CDP /json/version).

    Returns:
        Parameter dict ready to pass to Emulation.setUserAgentOverride.
    """
    ver = chrome_full_ver or _CHROME_FULL_VER
    major = ver.split(".")[0]
    system = _platform.system()

    brands = [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not-A.Brand", "version": "24"},
    ]
    full_version_list = [
        {"brand": "Chromium", "version": ver},
        {"brand": "Google Chrome", "version": ver},
        {"brand": "Not-A.Brand", "version": "24.0.0.0"},
    ]

    if system == "Darwin":
        arch = "arm" if _platform.machine() == "arm64" else "x86"
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
        nav_platform, ua_platform, platform_ver = "MacIntel", "macOS", "14.5.0"
    elif system == "Windows":
        arch = "x86"
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
        nav_platform, ua_platform, platform_ver = "Win32", "Windows", "15.0.0"
    else:
        arch = "x86"
        ua = (
            "Mozilla/5.0 (X11; Linux x86_64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        )
        nav_platform, ua_platform, platform_ver = "Linux x86_64", "Linux", "6.5.0"

    return {
        "userAgent": ua,
        "platform": nav_platform,
        "userAgentMetadata": {
            "brands": brands,
            "fullVersionList": full_version_list,
            "platform": ua_platform,
            "platformVersion": platform_ver,
            "architecture": arch,
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    }


# Basic anti-detection JS (removes navigator.webdriver flag, patches chrome.runtime)
# Threads has light bot-detection — no need for the heavy injection used with XHS
STEALTH_JS = """
(() => {
    // 1. Remove navigator.webdriver flag
    const wd = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    if (wd && wd.get) {
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: new Proxy(wd.get, { apply: () => false }),
            configurable: true,
        });
    }

    // 2. chrome.runtime — may be missing in headless mode
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
        window.chrome.runtime = { connect: () => {}, sendMessage: () => {} };
    }

    // 3. navigator.vendor
    Object.defineProperty(navigator, 'vendor', {
        get: () => 'Google Inc.',
        configurable: true,
    });
})();
"""

# Chrome launch args (generic anti-detection, platform-independent)
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-component-update",
    "--disable-extensions",
    "--disable-sync",
]
