#!/usr/bin/env python3
"""Threads Digest CLI — minimal feed scraper.

Commands: scrape, login, kill-chrome.
All output is JSON. Exit codes: 0=success, 1=not logged in, 2=error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from chrome_launcher import DEFAULT_PORT, ensure_chrome, kill_chrome
from threads.cdp import Browser
from threads.errors import NotLoggedInError, ThreadsError
from threads.feed import list_feeds
from threads.login import check_login, ensure_logged_in, open_login_page

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

DIGEST_DIR = Path(__file__).parent.parent / "digests"


def _ok(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(0)


def _fail(message: str, code: int = 2) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False, indent=2))
    sys.exit(code)


def _get_page(args: argparse.Namespace):
    b = Browser(host=args.host, port=args.port)
    b.connect()
    page = b.new_page()
    return page


def cmd_scrape(args: argparse.Namespace) -> None:
    """Scrape Threads feed and save raw JSON."""
    # Ensure Chrome is running (headless by default)
    if not ensure_chrome(port=args.port, headless=not args.no_headless):
        _fail("Chrome not found. Install with: apt install google-chrome-stable")

    page = _get_page(args)
    ensure_logged_in(page)

    result = list_feeds(page, max_posts=args.limit)
    page.close()

    # Save raw JSON
    DIGEST_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = DIGEST_DIR / f"raw-{today}.json"
    output_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _ok({
        "status": "success",
        "posts": len(result.posts),
        "file": str(output_path),
    })


def cmd_login(args: argparse.Namespace) -> None:
    """Open Chrome for manual login."""
    if not ensure_chrome(port=args.port, headless=False):
        _fail("Chrome not found. Install with: apt install google-chrome-stable")

    page = _get_page(args)

    # Check if already logged in
    status = check_login(page)
    if status["logged_in"]:
        page.close()
        _ok({"status": "already_logged_in"})

    result = open_login_page(page)
    # Don't close page — user needs to log in
    _ok(result)


def cmd_kill_chrome(args: argparse.Namespace) -> None:
    """Stop Chrome process."""
    kill_chrome(port=args.port)
    _ok({"status": "success", "message": "Chrome stopped"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Threads Digest — feed scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Chrome debug host")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Chrome debug port (default {DEFAULT_PORT})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="Scrape feed, save raw JSON")
    p.add_argument("--limit", type=int, default=20, help="Max posts to collect (default 20)")
    p.add_argument(
        "--no-headless", action="store_true",
        help="Run Chrome with GUI (default: headless)",
    )

    sub.add_parser("login", help="Open Chrome for manual login")
    sub.add_parser("kill-chrome", help="Stop Chrome process")

    return parser


_COMMAND_MAP = {
    "scrape": cmd_scrape,
    "login": cmd_login,
    "kill-chrome": cmd_kill_chrome,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handler = _COMMAND_MAP.get(args.command)
    if not handler:
        _fail(f"Unknown command: {args.command}")

    try:
        handler(args)
    except NotLoggedInError:
        _fail(
            "Not logged in. Run 'login' on a machine with GUI"
            " and copy ~/.threads/chrome-profile/ to this machine.",
            code=1,
        )
    except ThreadsError as e:
        _fail(str(e), code=2)
    except ConnectionRefusedError:
        _fail(
            "Cannot connect to Chrome. Run 'python -m scripts.cli login'"
            " first or ensure Chrome is installed.",
            code=2,
        )
    except Exception as e:
        _fail(f"Unexpected error: {e}", code=2)


if __name__ == "__main__":
    main()
