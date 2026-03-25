"""Threads automation exceptions."""


class ThreadsError(Exception):
    """Base exception for Threads automation."""


class NotLoggedInError(ThreadsError):
    """Not logged in."""

    def __init__(self) -> None:
        super().__init__("Not logged in. Please log in to Threads first.")


class CDPError(ThreadsError):
    """CDP communication error."""


class ElementNotFoundError(ThreadsError):
    """Page element not found."""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(f"Element not found: {selector}")


class NoFeedsError(ThreadsError):
    """No feed data captured."""

    def __init__(self) -> None:
        super().__init__("No feed data captured.")
