"""CDP WebSocket client (Browser, Page, Element).

Communicates with Chrome DevTools Protocol over native WebSocket for browser automation.
Generic platform-agnostic engine.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any

import requests
import websockets.sync.client as ws_client

from .errors import CDPError, ElementNotFoundError
from .stealth import STEALTH_JS, build_ua_override

# Disable proxy to prevent system proxy from interfering with local CDP connection
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

logger = logging.getLogger(__name__)


class CDPClient:
    """Low-level CDP WebSocket communication client."""

    def __init__(self, ws_url: str) -> None:
        self._ws = ws_client.connect(ws_url, max_size=50 * 1024 * 1024)
        self._id = 0
        self._callbacks: dict[int, Any] = {}

    def send(self, method: str, params: dict | None = None) -> dict:
        """Send a CDP command and wait for the result."""
        self._id += 1
        msg: dict[str, Any] = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        self._ws.send(json.dumps(msg))
        return self._wait_for(self._id)

    def _wait_for(self, msg_id: int, timeout: float = 30.0) -> dict:
        """Wait for a response with the given id."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"CDP error: {data['error']}")
                return data.get("result", {})
        raise CDPError(f"Timed out waiting for CDP response (id={msg_id})")

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._ws.close()


class Page:
    """CDP page object wrapping common operations."""

    def __init__(self, cdp: CDPClient, target_id: str, session_id: str) -> None:
        self._cdp = cdp
        self.target_id = target_id
        self.session_id = session_id
        self._ws = cdp._ws
        self._id_counter = 1000

    def _send_session(self, method: str, params: dict | None = None) -> dict:
        """Send a command to the session."""
        self._id_counter += 1
        msg: dict[str, Any] = {
            "id": self._id_counter,
            "method": method,
            "sessionId": self.session_id,
        }
        if params:
            msg["params"] = params
        self._ws.send(json.dumps(msg))
        return self._wait_session(self._id_counter)

    def _wait_session(self, msg_id: int, timeout: float = 60.0) -> dict:
        """Wait for a session response."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise CDPError(f"CDP error: {data['error']}")
                return data.get("result", {})
        raise CDPError(f"Timed out waiting for session response (id={msg_id})")

    def navigate(self, url: str) -> None:
        """Navigate to the given URL."""
        logger.info("Navigating to: %s", url)
        self._send_session("Page.navigate", {"url": url})

    def wait_for_load(self, timeout: float = 60.0) -> None:
        """Wait for page load to complete (polls document.readyState)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.evaluate("document.readyState")
                if state == "complete":
                    return
            except CDPError:
                pass
            time.sleep(0.5)
        logger.warning("Timed out waiting for page load")

    def wait_dom_stable(self, timeout: float = 10.0, interval: float = 0.5) -> None:
        """Wait for the DOM to stabilize (two consecutive snapshots are identical)."""
        last_html = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                html = self.evaluate("document.body ? document.body.innerHTML.length : 0")
                if html == last_html and html != "":
                    return
                last_html = html
            except CDPError:
                pass
            time.sleep(interval)

    def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        """Execute a JavaScript expression and return the result."""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        if "exceptionDetails" in result:
            raise CDPError(f"JS exception: {result['exceptionDetails']}")
        remote_obj = result.get("result", {})
        return remote_obj.get("value")

    def evaluate_function(self, function_body: str, *args: Any) -> Any:
        """Execute a JavaScript function and return the result.

        function_body is a complete function expression, e.g. `() => { return 1; }`
        """
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": f"({function_body})()",
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
        if "exceptionDetails" in result:
            raise CDPError(f"JS function exception: {result['exceptionDetails']}")
        remote_obj = result.get("result", {})
        return remote_obj.get("value")

    def query_selector(self, selector: str) -> str | None:
        """Find a single element; returns objectId or None."""
        result = self._send_session(
            "Runtime.evaluate",
            {
                "expression": f"document.querySelector({json.dumps(selector)})",
                "returnByValue": False,
            },
        )
        remote_obj = result.get("result", {})
        if remote_obj.get("subtype") == "null" or remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("objectId")

    def query_selector_all(self, selector: str) -> list[str]:
        """Find multiple elements; returns a list of objectIds."""
        # Get element count via JS, then fetch each one individually
        count = self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length")
        if not count:
            return []
        object_ids = []
        for i in range(count):
            result = self._send_session(
                "Runtime.evaluate",
                {
                    "expression": (f"document.querySelectorAll({json.dumps(selector)})[{i}]"),
                    "returnByValue": False,
                },
            )
            obj = result.get("result", {})
            oid = obj.get("objectId")
            if oid:
                object_ids.append(oid)
        return object_ids

    def has_element(self, selector: str) -> bool:
        """Check whether an element exists."""
        return self.evaluate(f"document.querySelector({json.dumps(selector)}) !== null") is True

    def wait_for_element(self, selector: str, timeout: float = 30.0) -> str:
        """Wait for an element to appear; returns objectId."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            oid = self.query_selector(selector)
            if oid:
                return oid
            time.sleep(0.5)
        raise ElementNotFoundError(selector)

    def click_element(self, selector: str) -> None:
        """Click the element matching the selector (via CDP Input events, isTrusted=true)."""
        box = self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                el.scrollIntoView({{block: 'center'}});
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if not box:
            return
        x = box["x"] + random.uniform(-3, 3)
        y = box["y"] + random.uniform(-3, 3)
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        self.mouse_click(x, y)

    def click_element_by_text(self, tag_selector: str, text: str) -> bool:
        """Click an element by its text content (for buttons without aria-label).

        Args:
            tag_selector: CSS selector scope, e.g. 'div[role="button"]'
            text: Exact textContent to match (compared after strip)

        Returns:
            True if found and clicked, False if not found.
        """
        box = self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(tag_selector)});
                for (const el of els) {{
                    if ((el.textContent || '').trim() === {json.dumps(text)}) {{
                        el.scrollIntoView({{block: 'center'}});
                        const rect = el.getBoundingClientRect();
                        return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
                    }}
                }}
                return null;
            }})()
            """
        )
        if not box:
            return False
        x = box["x"] + random.uniform(-3, 3)
        y = box["y"] + random.uniform(-3, 3)
        self.mouse_move(x, y)
        time.sleep(random.uniform(0.03, 0.08))
        self.mouse_click(x, y)
        return True

    def input_text(self, selector: str, text: str) -> None:
        """Type text into the element matching the selector."""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.value = {json.dumps(text)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }})()
            """
        )

    def input_content_editable(self, selector: str, text: str) -> None:
        """Type text into a contentEditable element (CDP char-by-char, simulates real typing)."""
        # 1. Focus the element
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.focus();
            }})()
            """
        )
        time.sleep(0.1)
        # 2. Select all and clear (Ctrl+A + Backspace)
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
        )
        time.sleep(0.1)
        # 3. Type char-by-char (random 30-80ms delay; newline becomes Enter key)
        for char in text:
            if char == "\n":
                self.press_key("Enter")
            else:
                self._send_session(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "text": char},
                )
                self._send_session(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "text": char},
                )
            time.sleep(random.uniform(0.03, 0.08))

    def get_element_text(self, selector: str) -> str | None:
        """Get the text content of an element."""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.textContent : null;
            }})()
            """
        )

    def get_element_attribute(self, selector: str, attr: str) -> str | None:
        """Get an attribute value from an element."""
        return self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                return el ? el.getAttribute({json.dumps(attr)}) : null;
            }})()
            """
        )

    def get_elements_count(self, selector: str) -> int:
        """Get the number of matching elements."""
        result = self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length")
        return result if isinstance(result, int) else 0

    def scroll_by(self, x: int, y: int) -> None:
        """Scroll the page by the given offset."""
        self.evaluate(f"window.scrollBy({x}, {y})")

    def scroll_to(self, x: int, y: int) -> None:
        """Scroll to the given position."""
        self.evaluate(f"window.scrollTo({x}, {y})")

    def scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the page."""
        self.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    def scroll_element_into_view(self, selector: str) -> None:
        """Scroll an element into the visible viewport."""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
            }})()
            """
        )

    def scroll_nth_element_into_view(self, selector: str, index: int) -> None:
        """Scroll the Nth matching element into the visible viewport."""
        self.evaluate(
            f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                if (els[{index}]) els[{index}].scrollIntoView(
                    {{behavior: 'smooth', block: 'center'}}
                );
            }})()
            """
        )

    def get_scroll_top(self) -> int:
        """Get the current scroll position."""
        result = self.evaluate(
            "window.pageYOffset || document.documentElement.scrollTop"
            " || document.body.scrollTop || 0"
        )
        return int(result) if result else 0

    def get_viewport_height(self) -> int:
        """Get the viewport height."""
        result = self.evaluate("window.innerHeight")
        return int(result) if result else 768

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """Set files on a file input element (via CDP DOM.setFileInputFiles)."""
        # Get nodeId first
        doc = self._send_session("DOM.getDocument", {"depth": 0})
        root_node_id = doc["root"]["nodeId"]
        result = self._send_session(
            "DOM.querySelector",
            {"nodeId": root_node_id, "selector": selector},
        )
        node_id = result.get("nodeId", 0)
        if node_id == 0:
            raise ElementNotFoundError(selector)
        self._send_session(
            "DOM.setFileInputFiles",
            {"nodeId": node_id, "files": files},
        )

    def dispatch_wheel_event(self, delta_y: float) -> None:
        """Dispatch a wheel event to trigger lazy loading."""
        self.evaluate(
            f"""
            (() => {{
                let target = document.querySelector('.note-scroller')
                    || document.querySelector('.interaction-container')
                    || document.documentElement;
                const event = new WheelEvent('wheel', {{
                    deltaY: {delta_y},
                    deltaMode: 0,
                    bubbles: true,
                    cancelable: true,
                    view: window,
                }});
                target.dispatchEvent(event);
            }})()
            """
        )

    def mouse_move(self, x: float, y: float) -> None:
        """Move the mouse cursor."""
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )

    def mouse_click(self, x: float, y: float, button: str = "left") -> None:
        """Click at the given coordinates."""
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": 1},
        )
        self._send_session(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": 1},
        )

    def type_text(self, text: str, delay_ms: int = 50) -> None:
        """Type text character by character."""
        for char in text:
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char},
            )
            self._send_session(
                "Input.dispatchKeyEvent",
                {"type": "keyUp", "text": char},
            )
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

    def press_key(self, key: str) -> None:
        """Press and release the given key."""
        key_map = {
            "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            "ArrowDown": {
                "key": "ArrowDown",
                "code": "ArrowDown",
                "windowsVirtualKeyCode": 40,
            },
            "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
        }
        info = key_map.get(key, {"key": key, "code": key})
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", **info},
        )
        self._send_session(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", **info},
        )

    def inject_stealth(self) -> None:
        """Inject anti-detection scripts."""
        self._send_session(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STEALTH_JS},
        )

    def close(self) -> None:
        """Close the current tab (Target.closeTarget)."""
        import contextlib
        with contextlib.suppress(Exception):
            self._cdp.send("Target.closeTarget", {"targetId": self.target_id})

    def remove_element(self, selector: str) -> None:
        """Remove a DOM element."""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) el.remove();
            }})()
            """
        )

    def hover_element(self, selector: str) -> None:
        """Hover over the center of an element."""
        box = self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }})()
            """
        )
        if box:
            self.mouse_move(box["x"], box["y"])

    def select_all_text(self, selector: str) -> None:
        """Select all text inside an input element."""
        self.evaluate(
            f"""
            (() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return;
                el.focus();
                el.select ? el.select() : document.execCommand('selectAll');
            }})()
            """
        )

    def screenshot_element(self, selector: str, padding: int = 0) -> bytes:
        """Take a screenshot of the element matching the CSS selector; returns PNG bytes.

        Uses CDP Page.captureScreenshot to capture the element's region — much faster
        than Python-side PNG decode/re-encode, and the image comes directly from
        the browser's render output.

        Args:
            selector: CSS selector.
            padding:  Extra pixels to pad around the element (background-fill, like a white border).

        Returns:
            PNG bytes; returns b"" if the element is not found.
        """
        import base64 as _b64

        # Use DOM.getBoxModel for element coordinates — returns page coordinate space
        # (CSS px, relative to document top-left). getBoundingClientRect returns viewport
        # coordinates; for elements inside position:fixed overlays, adding pageXOffset still
        # captures content behind the overlay. DOM.getBoxModel is always correct.
        try:
            doc = self._send_session("DOM.getDocument", {"depth": 0})
            root_id = doc["root"]["nodeId"]
            query = self._send_session(
                "DOM.querySelector", {"nodeId": root_id, "selector": selector},
            )
            node_id = query.get("nodeId", 0)
            if not node_id:
                return b""
            box_model = self._send_session("DOM.getBoxModel", {"nodeId": node_id})
            model = box_model["model"]
            content = model["content"]  # [x1,y1, x2,y2, x3,y3, x4,y4] clockwise corners
            x, y = content[0], content[1]
            width, height = float(model["width"]), float(model["height"])
        except Exception:
            return b""

        result = self._send_session(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {
                    "x": max(0.0, x - padding),
                    "y": max(0.0, y - padding),
                    "width": width + padding * 2,
                    "height": height + padding * 2,
                    "scale": 1.0,
                },
            },
        )
        return _b64.b64decode(result.get("data", ""))


class Browser:
    """Chrome browser CDP controller."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._cdp: CDPClient | None = None
        self._chrome_version: str | None = None

    def connect(self) -> None:
        """Connect to Chrome DevTools."""
        resp = requests.get(f"{self.base_url}/json/version", timeout=5)
        resp.raise_for_status()
        info = resp.json()
        ws_url = info["webSocketDebuggerUrl"]

        # Extract the real version number from "Chrome/134.0.6998.88" for dynamic UA construction
        browser_str = info.get("Browser", "")
        if "/" in browser_str:
            self._chrome_version = browser_str.split("/", 1)[1]

        logger.info("Connected to Chrome: %s (version=%s)", ws_url, self._chrome_version)
        self._cdp = CDPClient(ws_url)

    def _setup_page(self, page: Page) -> Page:
        """Inject stealth, UA, and viewport into a Page object, and enable required CDP domains."""
        import contextlib

        page.inject_stealth()
        page._send_session(
            "Emulation.setUserAgentOverride",
            build_ua_override(self._chrome_version),
        )
        page._send_session(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": random.randint(1366, 1920),
                "height": random.randint(768, 1080),
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        for perm in ("geolocation", "notifications", "midi", "camera", "microphone"):
            with contextlib.suppress(CDPError):
                assert self._cdp is not None
                self._cdp.send(
                    "Browser.setPermission",
                    {"permission": {"name": perm}, "setting": "denied"},
                )
        page._send_session("Page.enable")
        page._send_session("DOM.enable")
        page._send_session("Runtime.enable")
        return page

    def new_page(self, url: str = "about:blank") -> Page:
        """Create a new page (always opens a new tab)."""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        result = self._cdp.send("Target.createTarget", {"url": url})
        target_id = result["targetId"]
        result = self._cdp.send(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        session_id = result["sessionId"]
        return self._setup_page(Page(self._cdp, target_id, session_id))

    def get_or_create_page(self) -> Page:
        """Reuse an existing blank tab, or create a new one if none is found.

        Prevents unbounded tab accumulation in Chrome from repeated commands.
        A tab is considered blank if its url is about:blank or chrome://newtab/.
        """
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        import contextlib

        resp = requests.get(f"{self.base_url}/json", timeout=5)
        targets = resp.json()

        for target in targets:
            if target.get("type") == "page" and target.get("url") in (
                "about:blank",
                "chrome://newtab/",
            ):
                target_id = target["id"]
                with contextlib.suppress(Exception):
                    result = self._cdp.send(
                        "Target.attachToTarget",
                        {"targetId": target_id, "flatten": True},
                    )
                    session_id = result.get("sessionId")
                    if session_id:
                        logger.debug("Reusing blank tab: %s", target_id)
                        return self._setup_page(Page(self._cdp, target_id, session_id))

        # No blank tab found, create one
        return self.new_page()

    def get_page_by_target_id(self, target_id: str) -> Page | None:
        """Connect to a specific tab by target_id."""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None
        try:
            result = self._cdp.send(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
        except Exception:
            return None
        session_id = result.get("sessionId")
        if not session_id:
            return None
        page = Page(self._cdp, target_id, session_id)
        page._send_session("Page.enable")
        page._send_session("DOM.enable")
        page._send_session("Runtime.enable")
        page.inject_stealth()
        return page

    def get_existing_page(self) -> Page | None:
        """Get an existing page (the first non-about:blank page target)."""
        if not self._cdp:
            self.connect()
        assert self._cdp is not None

        resp = requests.get(f"{self.base_url}/json", timeout=5)
        targets = resp.json()

        for target in targets:
            if target.get("type") == "page" and target.get("url") != "about:blank":
                target_id = target["id"]
                result = self._cdp.send(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                session_id = result["sessionId"]
                page = Page(self._cdp, target_id, session_id)
                page._send_session("Page.enable")
                page._send_session("DOM.enable")
                page._send_session("Runtime.enable")
                page.inject_stealth()
                return page
        return None

    def close_page(self, page: Page) -> None:
        """Close a page."""
        import contextlib

        if self._cdp:
            with contextlib.suppress(CDPError):
                self._cdp.send("Target.closeTarget", {"targetId": page.target_id})

    def close(self) -> None:
        """Close the connection."""
        if self._cdp:
            self._cdp.close()
            self._cdp = None
