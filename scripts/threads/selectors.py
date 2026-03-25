"""Threads CSS selectors (centralized).

All selectors maintained here. When Threads updates its layout, only this file needs changing.
Marked: verified / unverified / failed
"""

# ===========================================================================
# Login detection
# ===========================================================================

LOGIN_INDICATORS = [
    'a[href="/"][role="link"]',               # verified: home nav link
    'div[data-pressable-container="true"]',   # verified: post container
    'svg[aria-label="Home"]',                 # English UI
    'svg[aria-label="Головна"]',              # Ukrainian UI
]

LOGOUT_INDICATORS = [
    'a[href*="login"]',                       # verified: login link
    'input[name="username"]',                 # Instagram login form
]

# ===========================================================================
# Post containers (feed extraction)
# ===========================================================================

POST_CONTAINER = 'div[data-pressable-container="true"]'
POST_AUTHOR_LINK = 'a[href^="/@"]'
POST_TEXT = 'span[dir="auto"]'
POST_TIMESTAMP = 'time, abbr[aria-label*="ago"]'


# ===========================================================================
# Utility
# ===========================================================================


def first_existing(page, selectors: list[str]) -> str | None:
    """Return the first selector that exists on the page."""
    for sel in selectors:
        if page.has_element(sel):
            return sel
    return None
