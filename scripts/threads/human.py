"""Human behaviour simulation parameters (delays, scrolling, hovering).

Generic simulation parameters for human-like browser interaction.
"""

import random
import time

# ========== Configuration constants ==========
DEFAULT_MAX_ATTEMPTS = 500
STAGNANT_LIMIT = 20
MIN_SCROLL_DELTA = 10
MAX_CLICK_PER_ROUND = 3
STAGNANT_CHECK_THRESHOLD = 2
LARGE_SCROLL_TRIGGER = 5
BUTTON_CLICK_INTERVAL = 3
FINAL_SPRINT_PUSH_COUNT = 15

# ========== Delay ranges (milliseconds) ==========
HUMAN_DELAY = (300, 700)
REACTION_TIME = (300, 800)
HOVER_TIME = (100, 300)
READ_TIME = (500, 1200)
SHORT_READ = (600, 1200)
SCROLL_WAIT = (100, 200)
POST_SCROLL = (300, 500)


def sleep_random(min_ms: int, max_ms: int) -> None:
    """Random delay."""
    if max_ms <= min_ms:
        time.sleep(min_ms / 1000.0)
        return
    delay = random.randint(min_ms, max_ms) / 1000.0
    time.sleep(delay)


def navigation_delay() -> None:
    """Random wait after page navigation to simulate human reading."""
    sleep_random(1000, 2500)


def get_scroll_interval(speed: str) -> float:
    """Get scroll interval in seconds for the given speed."""
    if speed == "slow":
        return (1200 + random.randint(0, 300)) / 1000.0
    if speed == "fast":
        return (300 + random.randint(0, 100)) / 1000.0
    # normal
    return (600 + random.randint(0, 200)) / 1000.0


def get_scroll_ratio(speed: str) -> float:
    """Get scroll ratio for the given speed."""
    if speed == "slow":
        return 0.5
    if speed == "fast":
        return 0.9
    return 0.7


def calculate_scroll_delta(viewport_height: int, base_ratio: float) -> float:
    """Calculate scroll distance."""
    scroll_delta = viewport_height * (base_ratio + random.random() * 0.2)
    if scroll_delta < 400:
        scroll_delta = 400.0
    return scroll_delta + random.randint(-50, 50)


# Page inaccessibility keywords (Threads platform)
INACCESSIBLE_KEYWORDS = [
    "Sorry, this page isn't available",
    "This content isn't available",
    "This account is private",
    "Something went wrong",
    "Page not found",
]
