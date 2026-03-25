[English](README.md) | [Українська](README.uk.md)

# Threads Digest

> **⚠️ Warning:** Threads may block or restrict accounts for automated access. Use at your own risk. While some anti-detection measures are in place, there are no guarantees. This project is published for educational purposes.

A Claude Code skill that reads your Threads feed and creates an AI-curated digest. You don't scroll — Claude scrolls for you.

## Install

```
/plugin marketplace add ok-up/threads-digest
/plugin install threads-digest
```

Then ask Claude: "read my Threads feed" or use `/threads-digest`. If any prerequisites (Chrome, Python, uv) are missing, Claude will automatically help install them.

## Login setup (for headless VPS)

If Claude runs on a headless server, you need to log in on your local machine first and copy the session.

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), Chrome or Chromium.

```bash
git clone https://github.com/ok-up/threads-digest.git
cd threads-digest
uv run python -m scripts.cli login
```

Chrome will open — log in to Threads via Instagram. Once you see your feed, Ctrl+C.

Copy the saved session to the server:

```bash
rsync -az profile/ YOUR_SERVER:~/.claude/plugins/marketplaces/threads-digest/profile/
```

If the session expires later, repeat these steps.

## Architecture

**CDP over WebSocket** — direct browser control via Chrome DevTools Protocol. No Puppeteer/Playwright — just `requests` + `websockets`. Minimal dependencies, full control over timing.

**Stealth** — multi-layer anti-detection:
- Launch args (`--disable-blink-features=AutomationControlled`)
- User-Agent and Client Hints spoofing via CDP
- JS injection: `navigator.webdriver = false`, `chrome.runtime` patch
- Human-like delays and scroll randomization — protects against server-side bot detection, works in headless

**Dual feed parsing** — first extracts posts from SSR JSON (`<script type="application/json">`), on scroll — fallback to DOM parsing via CSS selectors. Deduplication by post URL.

**Session via Chrome profile** — login once in a GUI browser, copy the profile to the server via rsync. Cookies are cross-platform (SQLite) — a profile from Windows works on Linux ARM.

**Headless by default** — `--headless=new`. Interactive mode only for login.

## Tested on

- macOS
- Debian ARM (VPS)

Other platforms may work — if something breaks, Claude will help you fix it.

## License

MIT
