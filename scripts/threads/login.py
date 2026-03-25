"""Threads login state check and login flow.

Threads uses an Instagram account for login. Login state is persisted via
cookies in the Chrome Profile. No SMS code is required — once logged in,
the session persists in the Profile directory indefinitely.
"""

from __future__ import annotations

import logging
import time

from .cdp import Page
from .errors import NotLoggedInError
from .human import sleep_random
from .selectors import LOGIN_INDICATORS, LOGOUT_INDICATORS
from .urls import HOME_URL, LOGIN_URL

logger = logging.getLogger(__name__)


def check_login(page: Page) -> dict:
    """Check current login state.

    Returns:
        {
            "logged_in": bool,
            "username": str | None,
            "message": str,
        }
    """
    logger.info("Checking Threads login state")
    page.navigate(HOME_URL)
    page.wait_for_load(timeout=15)
    time.sleep(2)

    # Check if redirected to the login page
    current_url = page.evaluate("window.location.href") or ""
    if "/login" in current_url:
        return {
            "logged_in": False,
            "username": None,
            "message": "Not logged in — redirected to login page",
        }

    # Try to extract username from page state
    username = _extract_username(page)
    if username:
        return {
            "logged_in": True,
            "username": username,
            "message": f"Logged in: @{username}",
        }

    # Count login vs logout signals to avoid false negatives.
    # The authenticated home page can still contain an `a[href*="login"]`
    # element (e.g. footer link), so a single logout indicator is not
    # conclusive when login indicators are also present.
    login_signals = sum(1 for s in LOGIN_INDICATORS if page.has_element(s))
    logout_signals = sum(1 for s in LOGOUT_INDICATORS if page.has_element(s))

    if login_signals > 0 and login_signals >= logout_signals:
        return {
            "logged_in": True,
            "username": None,
            "message": "Logged in (username unavailable)",
        }

    if logout_signals > 0:
        return {
            "logged_in": False,
            "username": None,
            "message": "Not logged in — run the login command",
        }

    if login_signals > 0:
        return {
            "logged_in": True,
            "username": None,
            "message": "Logged in (username unavailable)",
        }

    return {
        "logged_in": False,
        "username": None,
        "message": "Login state unclear — consider logging in again",
    }


def _extract_username(page: Page) -> str | None:
    """Extract current username from page JS state or DOM."""
    # Method 1: extract from window.__reactFiber or Meta global state (if exposed)
    candidates = [
        # Common Instagram/Meta global state variables
        "window._sharedData?.config?.viewer?.username",
        "window.__additionalData?.[Object.keys(window.__additionalData)[0]]?.data?.user?.username",
    ]
    for expr in candidates:
        try:
            val = page.evaluate(expr)
            if val and isinstance(val, str):
                return val
        except Exception:
            pass

    # Method 2: infer from meta og:url tag
    try:
        og_url = page.evaluate(
            'document.querySelector(\'meta[property="og:url"]\')?.content'
        )
        if og_url and "threads.com/@" in og_url:
            return og_url.split("/@")[1].split("/")[0]
    except Exception:
        pass

    return None


def open_login_page(page: Page) -> dict:
    """Navigate to the Threads login page and wait for the user to log in manually.

    Threads login requires an Instagram account. It is recommended to complete
    this manually in a Chrome instance with a GUI. After login, cookies are
    persisted in the Chrome Profile and no further login is needed.

    Returns:
        Status info dict.
    """
    logger.info("Opening Threads login page")
    page.navigate(LOGIN_URL)
    page.wait_for_load(timeout=15)
    sleep_random(1000, 2000)

    return {
        "status": "waiting",
        "message": (
            "Login page opened. Please complete Instagram login in the browser.\n"
            "Cookies will be saved automatically — no need to log in again.\n"
            "Run check-login after completing login to confirm the state."
        ),
        "url": LOGIN_URL,
    }


def ensure_logged_in(page: Page) -> dict:
    """Assert that the user is logged in, otherwise raise an exception.

    Returns:
        Login info dict.

    Raises:
        NotLoggedInError: raised when not logged in.
    """
    status = check_login(page)
    if not status["logged_in"]:
        raise NotLoggedInError()
    return status
