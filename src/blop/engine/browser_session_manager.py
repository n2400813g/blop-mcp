"""Persistent Playwright session manager for browser_* compatibility tools."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Dialog, Page, Playwright, async_playwright

from blop.config import (
    BLOP_COMPAT_HEADLESS,
    BLOP_COMPAT_OUTPUT_DIR,
    BLOP_TEST_ID_ATTRIBUTE,
    validate_app_url,
)
from blop.engine import auth as auth_engine
from blop.engine.errors import BLOP_BROWSER_SESSION_ERROR, BLOP_RESOURCE_NOT_FOUND, tool_error
from blop.engine.snapshot_refs import SnapshotNode, build_stable_key, render_snapshot_markdown


def _event_level_weight(level: str) -> int:
    mapping = {"error": 0, "warning": 1, "info": 2, "debug": 3}
    return mapping.get(level.lower(), 2)


class BrowserSessionManager:
    """Single-process browser session used by compatibility tools."""

    _MAX_EVENTS = 1000

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._current_tab: int = 0
        self._refs: dict[str, str] = {}
        self._profile_name: Optional[str] = None
        self._storage_state_path: Optional[str] = None
        self._console_events: list[dict] = []
        self._network_events: list[dict] = []
        self._last_nav_console_idx = 0
        self._last_nav_network_idx = 0
        self._dialog_policy: dict = {"accept": True, "promptText": None}
        self._active_routes: list[dict] = []
        self._offline = False
        self._output_dir = Path(BLOP_COMPAT_OUTPUT_DIR)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_started(self, profile_name: Optional[str] = None) -> None:
        async with self._lock:
            if self._context is not None and self._browser is not None:
                return

            storage_state = await auth_engine.resolve_storage_state_for_profile(
                profile_name,
                allow_auto_env=True,
            )
            if storage_state and profile_name:
                self._storage_state_path = storage_state
                self._profile_name = profile_name

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=BLOP_COMPAT_HEADLESS)
            kwargs: dict = {
                "ignore_https_errors": True,
                "accept_downloads": True,
            }
            if storage_state:
                kwargs["storage_state"] = storage_state
            self._context = await self._browser.new_context(**kwargs)
            page = await self._context.new_page()
            self._attach_page_listeners(page)
            self._current_tab = 0
            await self._apply_saved_routes()

    async def close(self) -> dict:
        async with self._lock:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
            self._pw = None
            self._browser = None
            self._context = None
            self._refs.clear()
            self._console_events.clear()
            self._network_events.clear()
            self._active_routes.clear()
            self._profile_name = None
            self._storage_state_path = None
            self._offline = False
            self._current_tab = 0
            return {"status": "closed"}

    def _pages(self) -> list[Page]:
        if self._context is None:
            return []
        return self._context.pages

    def _current_page(self) -> Page:
        pages = self._pages()
        if not pages:
            raise RuntimeError("No active browser page. Call browser_navigate first.")
        self._current_tab = min(self._current_tab, len(pages) - 1)
        return pages[self._current_tab]

    def _attach_page_listeners(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
        page.on("dialog", self._on_dialog)

    async def _on_dialog(self, dialog: Dialog) -> None:
        try:
            if self._dialog_policy.get("accept", True):
                await dialog.accept(self._dialog_policy.get("promptText"))
            else:
                await dialog.dismiss()
        except Exception:
            try:
                await dialog.dismiss()
            except Exception:
                pass

    def _on_console(self, msg) -> None:
        self._console_events.append(
            {
                "level": msg.type,
                "text": msg.text,
                "url": msg.location.get("url", "") if msg.location else "",
                "ts": time.time(),
            }
        )
        self._trim_console_events()

    def _on_response(self, response) -> None:
        req = response.request
        self._network_events.append(
            {
                "method": req.method,
                "url": response.url,
                "status": response.status,
                "resourceType": req.resource_type,
                "ts": time.time(),
            }
        )
        self._trim_network_events()

    def _on_request_failed(self, request) -> None:
        self._network_events.append(
            {
                "method": request.method,
                "url": request.url,
                "status": 0,
                "resourceType": request.resource_type,
                "failure": request.failure,
                "ts": time.time(),
            }
        )
        self._trim_network_events()

    def _trim_console_events(self) -> None:
        overflow = len(self._console_events) - self._MAX_EVENTS
        if overflow > 0:
            del self._console_events[:overflow]
            self._last_nav_console_idx = max(0, self._last_nav_console_idx - overflow)

    def _trim_network_events(self) -> None:
        overflow = len(self._network_events) - self._MAX_EVENTS
        if overflow > 0:
            del self._network_events[:overflow]
            self._last_nav_network_idx = max(0, self._last_nav_network_idx - overflow)

    async def read_page_info(self) -> dict:
        """Current page URL and title (no navigation)."""
        await self.ensure_started()
        page = self._current_page()
        return {"url": page.url, "title": await page.title()}

    async def navigate(self, url: str, profile_name: Optional[str] = None) -> dict:
        err = validate_app_url(url)
        if err:
            raise ValueError(err)
        await self.ensure_started(profile_name=profile_name)
        page = self._current_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._refs.clear()
        self._last_nav_console_idx = len(self._console_events)
        self._last_nav_network_idx = len(self._network_events)
        return {"url": page.url, "title": await page.title()}

    async def navigate_back(self) -> dict:
        page = self._current_page()
        await page.go_back(wait_until="domcontentloaded", timeout=60000)
        self._refs.clear()
        return {"url": page.url, "title": await page.title()}

    async def tabs(self, action: str, index: Optional[int] = None) -> dict:
        await self.ensure_started()
        pages = self._pages()
        if action == "list":
            return {
                "tabs": [
                    {"index": i, "url": p.url, "title": await p.title(), "current": i == self._current_tab}
                    for i, p in enumerate(pages)
                ]
            }
        if action == "new":
            p = await self._context.new_page()  # type: ignore[union-attr]
            self._attach_page_listeners(p)
            self._current_tab = len(self._pages()) - 1
            return {"status": "created", "index": self._current_tab}
        if action == "select":
            if index is None or index < 0 or index >= len(pages):
                return tool_error(f"Invalid tab index: {index}", BLOP_BROWSER_SESSION_ERROR, details={"index": index})
            self._current_tab = index
            return {"status": "selected", "index": self._current_tab, "url": self._current_page().url}
        if action == "close":
            close_idx = self._current_tab if index is None else index
            if close_idx < 0 or close_idx >= len(pages):
                return tool_error(
                    f"Invalid tab index: {close_idx}",
                    BLOP_BROWSER_SESSION_ERROR,
                    details={"index": close_idx},
                )
            await pages[close_idx].close()
            remaining = self._pages()
            self._current_tab = max(0, min(self._current_tab, len(remaining) - 1)) if remaining else 0
            return {"status": "closed", "index": close_idx, "remaining": len(remaining)}
        return tool_error(f"Unsupported action '{action}'", BLOP_BROWSER_SESSION_ERROR, details={"action": action})

    async def snapshot(self, selector: Optional[str] = None, filename: Optional[str] = None) -> dict:
        page = self._current_page()
        test_id_attr = BLOP_TEST_ID_ATTRIBUTE.replace("\\", "\\\\").replace("'", "\\'")
        root_selector = (selector or "body").replace("\\", "\\\\").replace("'", "\\'")
        raw = await page.evaluate(
            f"""() => {{
                const requestedSelector = '{root_selector}';
                const requestedRoot = document.querySelector(requestedSelector);
                const root = requestedRoot || document.body;
                if (!root) return {{
                    requested_root_selector: requestedSelector,
                    root_found: false,
                    effective_root_selector: 'body',
                    nodes: [],
                }};
                const roleMap = {{a:'link',button:'button',select:'combobox',textarea:'textbox'}};
                const inputRole = {{checkbox:'checkbox',radio:'radio',button:'button',submit:'button',reset:'button'}};
                const selectorList = [
                    'a[href]','button','input:not([type="hidden"])','select','textarea',
                    '[role]','[contenteditable="true"]','[tabindex]'
                ];
                function cssPath(el) {{
                    if (!(el instanceof Element)) return '';
                    const parts = [];
                    let node = el;
                    let anchored = false;
                    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body) {{
                        let sel = node.nodeName.toLowerCase();
                        if (node.id) {{
                            sel += '#' + CSS.escape(node.id);
                            parts.unshift(sel);
                            anchored = true;
                            break;
                        }}
                        const testId = node.getAttribute('{test_id_attr}');
                        if (testId) {{
                            sel += '[{test_id_attr}=\"' + CSS.escape(testId) + '\"]';
                            parts.unshift(sel);
                            anchored = true;
                            break;
                        }}
                        let nth = 1;
                        let sib = node;
                        while ((sib = sib.previousElementSibling) != null) {{
                            if (sib.nodeName.toLowerCase() === node.nodeName.toLowerCase()) nth++;
                        }}
                        sel += `:nth-of-type(${{nth}})`;
                        parts.unshift(sel);
                        node = node.parentElement;
                    }}
                    if (!anchored) parts.unshift('body');
                    return parts.join(' > ');
                }}
                const out = [];
                const seen = new Set();
                for (const sel of selectorList) {{
                    for (const el of root.querySelectorAll(sel)) {{
                        if (seen.has(el)) continue;
                        seen.add(el);
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 && rect.height === 0) continue;
                        const tag = el.tagName.toLowerCase();
                        const explicitRole = el.getAttribute('role');
                        let role = explicitRole || (tag === 'input' ? (inputRole[el.type] || 'textbox') : roleMap[tag] || null);
                        if (!role && el.hasAttribute('contenteditable')) role = 'textbox';
                        if (!role) role = 'generic';
                        const name = (
                            el.getAttribute('aria-label') ||
                            el.getAttribute('title') ||
                            (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120) ||
                            el.getAttribute('placeholder') ||
                            el.getAttribute('value') ||
                            ''
                        ).trim() || tag;
                        out.push({{
                            role,
                            name,
                            selector: cssPath(el),
                            disabled: !!(el.disabled || el.getAttribute('disabled') !== null)
                        }});
                    }}
                }}
                return {{
                    requested_root_selector: requestedSelector,
                    root_found: !!requestedRoot || requestedSelector === 'body',
                    effective_root_selector: requestedRoot ? requestedSelector : 'body',
                    nodes: out.slice(0, 250),
                }};
            }}"""
        )

        if isinstance(raw, list):
            data = {
                "requested_root_selector": selector or "body",
                "root_found": True,
                "effective_root_selector": selector or "body",
                "nodes": raw,
            }
        else:
            data = raw or {}
        items = data.get("nodes", []) if isinstance(data, dict) else []

        nodes: list[SnapshotNode] = []
        refs: dict[str, str] = {}
        for i, item in enumerate(items, 1):
            ref = f"e{i}"
            node = SnapshotNode(
                ref=ref,
                role=str(item.get("role", "generic")),
                name=str(item.get("name", "")),
                selector=str(item.get("selector", "")),
                disabled=bool(item.get("disabled", False)),
                stable_key=build_stable_key(
                    role=str(item.get("role", "generic")),
                    name=str(item.get("name", "")),
                    selector=str(item.get("selector", "")),
                    disabled=bool(item.get("disabled", False)),
                ),
            )
            nodes.append(node)
            refs[ref] = node.selector
        self._refs = refs

        markdown = render_snapshot_markdown(nodes)
        output_file = None
        if filename:
            output_file = self._resolve_output_path(filename, default_ext=".md")
            output_file.write_text(markdown)
        return {
            "url": page.url,
            "title": await page.title(),
            "node_count": len(nodes),
            "snapshot": markdown if not filename else None,
            "path": str(output_file) if output_file else None,
            "snapshot_format": "playwright_mcp_markdown_v1",
            "requested_root_selector": str(data.get("requested_root_selector") or (selector or "body")),
            "effective_root_selector": str(data.get("effective_root_selector") or (selector or "body")),
            "root_found": bool(data.get("root_found", True)),
            "test_id_attribute": BLOP_TEST_ID_ATTRIBUTE,
            "nodes": [
                {
                    "ref": node.ref,
                    "stable_key": node.stable_key,
                    "role": node.role,
                    "name": node.name,
                    "selector": node.selector,
                    "disabled": node.disabled,
                }
                for node in nodes
            ],
        }

    def _resolve_selector(self, ref: Optional[str], selector: Optional[str]) -> str:
        if ref:
            if ref not in self._refs:
                raise ValueError(f"Unknown ref '{ref}'. Run browser_snapshot first.")
            return self._refs[ref]
        if selector:
            return selector
        raise ValueError("Either ref or selector is required")

    async def resolve_locator(self, ref: Optional[str], selector: Optional[str]) -> dict:
        """Observe-only: resolve selector and report visibility/count (no click/fill)."""
        page = self._current_page()
        sel = self._resolve_selector(ref, selector)
        loc = page.locator(sel)
        try:
            count = await loc.count()
        except Exception as exc:
            return tool_error(
                str(exc)[:300],
                BLOP_BROWSER_SESSION_ERROR,
                details={"selector": sel, "cause": type(exc).__name__},
                status="error",
                selector=sel,
            )
        visible = False
        if count > 0:
            try:
                visible = await loc.first.is_visible()
            except Exception:
                visible = False
        return {
            "status": "ok",
            "selector": sel,
            "count": count,
            "first_visible": bool(visible),
        }

    async def click(self, ref: Optional[str], selector: Optional[str], double_click: bool = False) -> dict:
        page = self._current_page()
        sel = self._resolve_selector(ref, selector)
        loc = page.locator(sel).first
        if double_click:
            await loc.dblclick(timeout=10000)
        else:
            await loc.click(timeout=10000)
        return {"status": "ok", "selector": sel}

    async def type_text(
        self,
        ref: Optional[str],
        selector: Optional[str],
        text: str,
        submit: bool = False,
        slowly: bool = False,
    ) -> dict:
        page = self._current_page()
        sel = self._resolve_selector(ref, selector)
        loc = page.locator(sel).first
        if slowly:
            await loc.click(timeout=10000)
            await loc.type(text, delay=35)
        else:
            await loc.fill(text, timeout=10000)
        if submit:
            await loc.press("Enter")
        return {"status": "ok", "selector": sel}

    async def hover(self, ref: Optional[str], selector: Optional[str]) -> dict:
        page = self._current_page()
        sel = self._resolve_selector(ref, selector)
        await page.locator(sel).first.hover(timeout=10000)
        return {"status": "ok", "selector": sel}

    async def select_option(self, ref: Optional[str], selector: Optional[str], values: list[str]) -> dict:
        page = self._current_page()
        sel = self._resolve_selector(ref, selector)
        await page.locator(sel).first.select_option(values, timeout=10000)
        return {"status": "ok", "selector": sel, "values": values}

    async def file_upload(self, paths: Optional[list[str]]) -> dict:
        page = self._current_page()
        if paths is None:
            return {"status": "cancelled", "reason": "No file paths provided"}
        await page.set_input_files("input[type='file']", paths)
        return {"status": "ok", "files": len(paths)}

    async def press_key(self, key: str) -> dict:
        page = self._current_page()
        await page.keyboard.press(key)
        return {"status": "ok", "key": key}

    async def resize(self, width: int, height: int) -> dict:
        page = self._current_page()
        await page.set_viewport_size({"width": width, "height": height})
        return {"status": "ok", "width": width, "height": height}

    async def wait_for(self, time_secs: Optional[float], text: Optional[str], text_gone: Optional[str]) -> dict:
        page = self._current_page()
        if time_secs is not None:
            await page.wait_for_timeout(int(time_secs * 1000))
        if text:
            await page.get_by_text(text).first.wait_for(timeout=15000)
        if text_gone:
            await page.get_by_text(text_gone).first.wait_for(state="hidden", timeout=15000)
        return {"status": "ok"}

    async def handle_dialog(self, accept: bool, prompt_text: Optional[str]) -> dict:
        self._dialog_policy = {"accept": accept, "promptText": prompt_text}
        return {"status": "armed", "accept": accept}

    async def take_screenshot(
        self,
        filename: Optional[str],
        full_page: bool,
        ref: Optional[str],
        selector: Optional[str],
        img_type: str = "png",
    ) -> dict:
        page = self._current_page()
        type_param = img_type.lower()
        if type_param == "jpg":
            type_param = "jpeg"
        if type_param not in {"png", "jpeg"}:
            type_param = "png"
        ext = ".jpeg" if type_param == "jpeg" else ".png"
        output = self._resolve_output_path(
            filename or f"page-{int(time.time() * 1000)}{ext}",
            default_ext=ext,
        )
        if ref or selector:
            sel = self._resolve_selector(ref, selector)
            await page.locator(sel).first.screenshot(path=str(output), type=type_param)
        else:
            await page.screenshot(path=str(output), full_page=full_page, type=type_param)
        return {"status": "ok", "path": str(output)}

    def _resolve_output_path(self, filename: str, default_ext: str) -> Path:
        out = Path(filename)
        if out.is_absolute():
            raise ValueError("filename must be a relative path")
        if ".." in out.parts:
            raise ValueError("filename must not contain parent directory traversal")
        if not out.suffix:
            out = out.with_suffix(default_ext)
        out = self._output_dir / out
        base = self._output_dir.resolve()
        resolved = out.resolve(strict=False)
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError("filename must stay within the compatibility output directory") from exc
        out.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    async def console_messages(self, level: str = "info", all_messages: bool = False) -> dict:
        cutoff = 0 if all_messages else self._last_nav_console_idx
        min_weight = _event_level_weight(level)
        data = [
            m for m in self._console_events[cutoff:] if _event_level_weight(str(m.get("level", "info"))) <= min_weight
        ]
        return {"count": len(data), "messages": data}

    async def network_requests(self, include_static: bool = False) -> dict:
        data = self._network_events[self._last_nav_network_idx :]
        if not include_static:
            noisy_types = {"image", "font", "stylesheet", "media"}
            data = [ev for ev in data if ev.get("resourceType") not in noisy_types]
        return {"count": len(data), "requests": data}

    async def route_add(
        self,
        pattern: str,
        status: int = 200,
        body: Optional[str] = None,
        content_type: Optional[str] = None,
        headers: Optional[list[str]] = None,
    ) -> dict:
        await self.ensure_started()
        route_spec = {
            "pattern": pattern,
            "status": status,
            "body": body or "",
            "content_type": content_type or "application/json",
            "headers": headers or [],
        }
        if route_spec in self._active_routes:
            return {"status": "registered", "pattern": pattern}
        self._active_routes.append(route_spec)
        await self._register_route(route_spec)
        return {"status": "registered", "pattern": pattern}

    async def _register_route(self, route_spec: dict) -> None:
        async def _handler(route):
            hdrs = {}
            for h in route_spec["headers"]:
                if ":" in h:
                    k, v = h.split(":", 1)
                    hdrs[k.strip()] = v.strip()
            await route.fulfill(
                status=route_spec["status"],
                body=route_spec["body"],
                content_type=route_spec["content_type"],
                headers=hdrs,
            )

        await self._context.route(route_spec["pattern"], _handler)  # type: ignore[union-attr]

    async def _apply_saved_routes(self) -> None:
        for route in list(self._active_routes):
            await self._register_route(route)

    async def route_list(self) -> dict:
        return {"routes": list(self._active_routes), "count": len(self._active_routes)}

    async def route_remove(self, pattern: Optional[str]) -> dict:
        if self._context is None:
            return {"status": "ok", "removed": 0}
        if pattern:
            removed_entries = [r for r in self._active_routes if r.get("pattern") == pattern]
            if removed_entries:
                await self._safe_unroute_pattern(pattern)
            self._active_routes = [r for r in self._active_routes if r.get("pattern") != pattern]
            return {"status": "ok", "removed_pattern": pattern, "removed": len(removed_entries)}
        patterns = sorted({r.get("pattern") for r in self._active_routes if r.get("pattern")})
        if patterns:
            await asyncio.gather(*(self._safe_unroute_pattern(pat) for pat in patterns))
        removed_count = len(self._active_routes)
        self._active_routes.clear()
        return {"status": "ok", "removed_all": True, "removed": removed_count}

    async def _safe_unroute_pattern(self, pattern: str) -> None:
        try:
            await self._context.unroute(pattern)  # type: ignore[union-attr]
        except Exception:
            pass

    async def network_state_set(self, state: str) -> dict:
        if self._context is None:
            raise RuntimeError("No active browser context")
        if state not in {"offline", "online"}:
            return tool_error(
                "state must be 'offline' or 'online'", BLOP_BROWSER_SESSION_ERROR, details={"state": state}
            )
        self._offline = state == "offline"
        await self._context.set_offline(self._offline)
        return {"status": "ok", "state": state}

    async def cookie_list(self, domain: Optional[str] = None, path: Optional[str] = None) -> dict:
        await self.ensure_started()
        cookies = await self._context.cookies()  # type: ignore[union-attr]
        if domain:
            cookies = [c for c in cookies if domain in c.get("domain", "")]
        if path:
            cookies = [c for c in cookies if c.get("path") == path]
        return {"count": len(cookies), "cookies": cookies}

    async def cookie_get(self, name: str) -> dict:
        await self.ensure_started()
        cookies = await self._context.cookies()  # type: ignore[union-attr]
        for c in cookies:
            if c.get("name") == name:
                return {"cookie": c}
        return {"cookie": None}

    async def cookie_set(
        self,
        name: str,
        value: str,
        domain: Optional[str] = None,
        path: str = "/",
        expires: Optional[float] = None,
        http_only: bool = False,
        secure: bool = False,
        same_site: Optional[str] = None,
    ) -> dict:
        await self.ensure_started()
        page = self._current_page()
        host = domain or urlparse(page.url).hostname or "localhost"
        cookie = {
            "name": name,
            "value": value,
            "domain": host,
            "path": path,
            "httpOnly": http_only,
            "secure": secure,
        }
        if expires is not None:
            cookie["expires"] = expires
        if same_site:
            cookie["sameSite"] = same_site
        await self._context.add_cookies([cookie])  # type: ignore[union-attr]
        return {"status": "ok", "name": name}

    async def cookie_delete(self, name: str) -> dict:
        await self.ensure_started()
        cookies = await self._context.cookies()  # type: ignore[union-attr]
        keep = [c for c in cookies if c.get("name") != name]
        await self._context.clear_cookies()  # type: ignore[union-attr]
        if keep:
            await self._context.add_cookies(keep)  # type: ignore[union-attr]
        return {"status": "ok", "deleted": name}

    async def cookie_clear(self) -> dict:
        await self.ensure_started()
        await self._context.clear_cookies()  # type: ignore[union-attr]
        return {"status": "ok"}

    async def storage_state_save(self, filename: Optional[str]) -> dict:
        output = self._resolve_output_path(
            filename or f"storage-state-{int(time.time() * 1000)}.json",
            default_ext=".json",
        )
        await self._context.storage_state(path=str(output))  # type: ignore[union-attr]
        return {"status": "ok", "path": str(output)}

    async def storage_state_restore(self, filename: str) -> dict:
        fp = Path(filename)
        if not fp.is_absolute():
            fp = self._output_dir / fp
        if not fp.exists():
            return tool_error(
                f"Storage state file not found: {fp}",
                BLOP_RESOURCE_NOT_FOUND,
                details={"path": str(fp)},
            )
        payload = json.loads(fp.read_text())
        await self._context.clear_cookies()  # type: ignore[union-attr]
        cookies = payload.get("cookies", [])
        if cookies:
            await self._context.add_cookies(cookies)  # type: ignore[union-attr]
        page = self._current_page()
        origins = payload.get("origins", [])
        for origin in origins:
            if not origin.get("origin"):
                continue
            try:
                await page.goto(origin["origin"], wait_until="domcontentloaded")
                for item in origin.get("localStorage", []):
                    k = item.get("name")
                    v = item.get("value")
                    if k is not None and v is not None:
                        await page.evaluate(
                            "({k,v}) => localStorage.setItem(k,v)",
                            {"k": k, "v": v},
                        )
            except Exception:
                continue
        return {"status": "ok", "restored_from": str(fp)}

    async def localstorage_list(self) -> dict:
        page = self._current_page()
        data = await page.evaluate("() => Object.entries(localStorage)")
        return {"count": len(data), "items": [{"key": k, "value": v} for k, v in data]}

    async def localstorage_get(self, key: str) -> dict:
        page = self._current_page()
        val = await page.evaluate("(k) => localStorage.getItem(k)", key)
        return {"key": key, "value": val}

    async def localstorage_set(self, key: str, value: str) -> dict:
        page = self._current_page()
        await page.evaluate("([k,v]) => localStorage.setItem(k,v)", [key, value])
        return {"status": "ok", "key": key}

    async def localstorage_delete(self, key: str) -> dict:
        page = self._current_page()
        await page.evaluate("(k) => localStorage.removeItem(k)", key)
        return {"status": "ok", "key": key}

    async def localstorage_clear(self) -> dict:
        page = self._current_page()
        await page.evaluate("() => localStorage.clear()")
        return {"status": "ok"}

    async def sessionstorage_list(self) -> dict:
        page = self._current_page()
        data = await page.evaluate("() => Object.entries(sessionStorage)")
        return {"count": len(data), "items": [{"key": k, "value": v} for k, v in data]}

    async def sessionstorage_get(self, key: str) -> dict:
        page = self._current_page()
        val = await page.evaluate("(k) => sessionStorage.getItem(k)", key)
        return {"key": key, "value": val}

    async def sessionstorage_set(self, key: str, value: str) -> dict:
        page = self._current_page()
        await page.evaluate("([k,v]) => sessionStorage.setItem(k,v)", [key, value])
        return {"status": "ok", "key": key}

    async def sessionstorage_delete(self, key: str) -> dict:
        page = self._current_page()
        await page.evaluate("(k) => sessionStorage.removeItem(k)", key)
        return {"status": "ok", "key": key}

    async def sessionstorage_clear(self) -> dict:
        page = self._current_page()
        await page.evaluate("() => sessionStorage.clear()")
        return {"status": "ok"}


SESSION_MANAGER = BrowserSessionManager()
