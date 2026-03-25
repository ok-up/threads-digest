"""Threads platform URL constants."""

BASE_URL = "https://www.threads.com"
HOME_URL = f"{BASE_URL}/"
SEARCH_URL = f"{BASE_URL}/search"
LOGIN_URL = f"{BASE_URL}/login"


def profile_url(username: str) -> str:
    """User profile URL."""
    username = username.lstrip("@")
    return f"{BASE_URL}/@{username}"


def post_url(username: str, post_id: str) -> str:
    """Single thread post URL."""
    username = username.lstrip("@")
    return f"{BASE_URL}/@{username}/post/{post_id}"
