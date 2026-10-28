"""Optional Chromium adapter: exact DOM handles, no model-authored JS or selectors."""
from __future__ import annotations

import io
import uuid
from urllib.parse import urlsplit

from .perception import STALE_REGION_THRESHOLD, image_difference, target_rect_or_stale
from .types import Effect, Element, Execution, Observation, Rect, StaleObservation


SELECT_CONTROLS = """() => Array.from(document.querySelectorAll(
'button,a[href],input,textarea,select,[contenteditable="true"],[role="button"],[role="tab"],' +
'[role="checkbox"],[role="menuitem"],[role="option"],[role="combobox"],[role="switch"]'))
.filter(e => {const r=e.getBoundingClientRect(),s=getComputedStyle(e);
 return r.width>1&&r.height>1&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth
 &&s.visibility!=="hidden"&&s.display!=="none";}).slice(0,120)"""

READ_CONTROLS = """nodes => nodes.map(e => {
 const r=e.getBoundingClientRect();
 const label=e.getAttribute('aria-label') || Array.from(e.labels||[]).map(l=>l.innerText).join(' ')
 || e.getAttribute('placeholder') || e.innerText || e.getAttribute('title') || '';
 return {name:label.trim().slice(0,500),role:e.getAttribute('role')||e.tagName.toLowerCase(),
 rect:[r.x,r.y,r.width,r.height],value:e.type==='password'?null:('value'in e?e.value:null),
 enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true',password:e.type==='password',
 focused:document.activeElement===e,
 checked:'checked'in e?Boolean(e.checked):(e.hasAttribute('aria-checked')?e.getAttribute('aria-checked')==='true':null)};
})"""


class BrowserDriver:
    def __init__(self, *, endpoint: str = "", page_index: int | None = None, headless: bool = False,
                 executable_path: str | None = None, page=None):
        if endpoint:
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {"http", "https", "ws", "wss"} or parsed.hostname not in {
                    "127.0.0.1", "::1", "localhost"} or parsed.username or parsed.password:
                raise ValueError("Browser CDP endpoint must be an explicit loopback URL without credentials")
        if page_index is not None and page_index < 0:
            raise ValueError("page_index cannot be negative")
        self.endpoint, self.page_index, self.headless = endpoint, page_index, headless
        self.executable_path = executable_path
        self._page = page
        self._injected = page is not None
        self._playwright = None
        self._browser = None
        self._handles = {}
        self._selection = None
        self._current_id = ""
        self._url = ""

    def _connect(self):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install optional browser dependencies: pip install -r requirements-browser.txt") from exc
        self._playwright = sync_playwright().start()
        try:
            if self.endpoint:
                # Preserve the user's profile settings; this requires Playwright >=1.60.
                self._browser = self._playwright.chromium.connect_over_cdp(
                    self.endpoint, timeout=5000, no_defaults=True)
                pages = [page for context in self._browser.contexts for page in context.pages]
                if self.page_index is None and len(pages) != 1:
                    if not pages:
                        raise RuntimeError("No browser tabs are open in the attached browser.")
                    raise RuntimeError("Multiple browser tabs exist. Set HELPER_BROWSER_PAGE_INDEX explicitly.")
                index = self.page_index if self.page_index is not None else 0
                if index >= len(pages):
                    raise RuntimeError("Selected browser page does not exist.")
                self._page = pages[index]
            else:
                self._browser = self._playwright.chromium.launch(
                    headless=self.headless, executable_path=self.executable_path)
                self._page = self._browser.new_page(viewport={"width": 1440, "height": 900})
            self._page.set_default_timeout(3000)
        except Exception:
            self.close()
            raise

    def _dispose_handles(self):
        for handle in self._handles.values():
            try:
                handle.dispose()
            except Exception:
                pass
        self._handles.clear()
        if self._selection is not None:
            try:
                self._selection.dispose()
            except Exception:
                pass
            self._selection = None

    def observe(self) -> Observation:
        self._connect()
        if self._page.is_closed():
            raise RuntimeError("The selected browser tab was closed.")
        self._dispose_handles()
        self._selection = self._page.evaluate_handle(SELECT_CONTROLS)
        records = self._selection.evaluate(READ_CONTROLS)
        properties = self._selection.get_properties()
        elements = []
        for index, item in enumerate(records):
            mark = f"b{index + 1}"
            # The DOM can mutate between the evaluate() and get_properties()
            # round-trips above; skip elements that vanished in between.
            prop = properties.get(str(index))
            if prop is None:
                continue
            handle = prop.as_element()
            if handle is None:
                continue
            self._handles[mark] = handle
            elements.append(Element(
                mark, item["name"], item["role"], Rect(*(round(v) for v in item["rect"])),
                item["value"], item["enabled"], True, item["focused"], item["checked"], item["password"],
                ("click", "fill"),
            ))
        png = self._page.screenshot(type="png", full_page=False, scale="css", timeout=3000)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
        self._current_id, self._url = uuid.uuid4().hex, self._page.url
        text = self._page.evaluate("() => (document.body?.innerText||'').slice(0,8000)")
        return Observation(self._current_id, "browser", f"tab:{id(self._page)}", self._page.title(),
                           png, width, height, tuple(elements), url=self._url, text=text,
                           warnings=("DOM snapshot covers visible main-frame controls; use vision for iframe/canvas targets.",))

    def matches_window_title(self, title: str) -> bool:
        from .router import BROWSER_TITLE_MARKERS
        self._connect()
        page_title = self._page.title().strip()
        for marker in BROWSER_TITLE_MARKERS:
            for sep in (" - ", " — "):
                suffix = sep + marker
                if title.endswith(suffix):
                    title = title[:-len(suffix)]
                    break
        return bool(page_title) and title.strip() == page_title

    def validate(self, action, observation):
        try:
            self._validate_current(action, observation)
        except StaleObservation:
            raise
        except Exception as exc:
            # Detached nodes and navigation races happen before input. Refresh rather than record a possible click.
            raise StaleObservation(f"Browser validation unavailable: {type(exc).__name__}") from exc

    def _validate_current(self, action, observation):
        if self._page.is_closed() or observation.id != self._current_id or self._page.url != self._url:
            raise StaleObservation("The selected tab navigated or its observation expired")
        rect = target_rect_or_stale(action, observation)
        if action.target and action.target.element_id:
            handle = self._handles.get(action.target.element_id)
            if handle is None or not handle.is_visible() or not handle.is_enabled():
                raise StaleObservation("DOM target detached, became hidden, or is disabled")
            expected = observation.element(action.target.element_id)
            live = handle.evaluate("(element) => (" + READ_CONTROLS + ")([element])[0]")
            if live["name"] != expected.name or live["role"] != expected.role:
                raise StaleObservation("The DOM target's meaning changed")
            box = handle.bounding_box()
            if not box or any(abs(box[key] - value) > 3 for key, value in zip(
                    ("x", "y", "width", "height"), rect.as_list())):
                raise StaleObservation("DOM target moved")
        png = self._page.screenshot(type="png", full_page=False, scale="css", timeout=3000)
        if image_difference(observation.png, png, box=observation.image_box(rect) if rect else None) > STALE_REGION_THRESHOLD:
            raise StaleObservation("The visible browser action region changed")

    def execute(self, action, observation):
        handle = self._handles.get(action.target.element_id) if action.target else None
        rect = target_rect_or_stale(action, observation)
        try:
            if action.kind == "open":
                try:
                    parsed = urlsplit(action.text)
                except ValueError:
                    return Execution(Effect.REJECTED, "dom", "That launch target is not a valid URL.")
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    return Execution(Effect.REJECTED, "dom", "Browser navigation requires an http(s) URL.")
                self._page.goto(action.text, wait_until="domcontentloaded", timeout=15000)
            elif action.kind == "fill" and handle:
                handle.fill(action.text, timeout=3000)
            elif action.kind in {"click", "double_click", "right_click"} and handle:
                if action.kind == "double_click":
                    handle.dblclick(timeout=3000)
                else:
                    handle.click(button="right" if action.kind == "right_click" else "left", timeout=3000)
            elif action.kind == "press":
                aliases = {"ctrl": "Control", "alt": "Alt", "shift": "Shift", "meta": "Meta",
                           "enter": "Enter", "esc": "Escape", "tab": "Tab", "space": "Space",
                           "backspace": "Backspace", "delete": "Delete"}
                self._page.keyboard.press("+".join(aliases.get(k.casefold(), k) for k in action.keys))
            elif action.kind == "scroll":
                if rect:
                    self._page.mouse.move(*rect.center)
                self._page.mouse.wheel(0, action.amount)
            elif action.kind == "drag":
                self._page.mouse.move(*rect.center)
                self._page.mouse.down()
                try:
                    self._page.mouse.move(*observation.screen_box(action.destination).center, steps=8)
                finally:
                    self._page.mouse.up()
            elif action.kind in {"click", "double_click", "right_click", "fill"}:
                self._page.mouse.click(*rect.center, click_count=2 if action.kind == "double_click" else 1,
                                       button="right" if action.kind == "right_click" else "left")
                if action.kind == "fill":
                    self._page.keyboard.press("ControlOrMeta+A")
                    self._page.keyboard.insert_text(action.text)
            else:
                return Execution(Effect.REJECTED, "dom", "Unsupported browser action")
        except Exception as exc:
            # A click/navigation can take effect before Playwright reports a timeout.
            return Execution(Effect.UNKNOWN, "dom" if handle else "browser-input", type(exc).__name__)
        return Execution(Effect.APPLIED, "dom" if handle or action.kind == "open" else "browser-input")

    def close(self):
        self._dispose_handles()
        if self._injected:
            return
        try:
            # Only close browsers we launched. Stopping Playwright disconnects a CDP attachment.
            if self._browser is not None and not self.endpoint:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._page = self._browser = self._playwright = None
            self._current_id = ""
