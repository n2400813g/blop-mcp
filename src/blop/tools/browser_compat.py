"""Playwright-MCP-compatible browser_* tool wrappers for blop."""
from __future__ import annotations

from typing import Literal, Optional

from blop.engine.browser_session_manager import SESSION_MANAGER

__all__ = [
    "browser_navigate",
    "browser_navigate_back",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_hover",
    "browser_select_option",
    "browser_file_upload",
    "browser_tabs",
    "browser_close",
    "browser_console_messages",
    "browser_network_requests",
    "browser_take_screenshot",
    "browser_wait_for",
    "browser_press_key",
    "browser_resize",
    "browser_handle_dialog",
    "browser_route",
    "browser_unroute",
    "browser_route_list",
    "browser_network_state_set",
    "browser_cookie_list",
    "browser_cookie_get",
    "browser_cookie_set",
    "browser_cookie_delete",
    "browser_cookie_clear",
    "browser_storage_state",
    "browser_set_storage_state",
    "browser_localstorage_list",
    "browser_localstorage_get",
    "browser_localstorage_set",
    "browser_localstorage_delete",
    "browser_localstorage_clear",
    "browser_sessionstorage_list",
    "browser_sessionstorage_get",
    "browser_sessionstorage_set",
    "browser_sessionstorage_delete",
    "browser_sessionstorage_clear",
]


async def browser_navigate(url: str, profile_name: Optional[str] = None) -> dict:
    """Navigate the active page to a URL.

    Args:
        url: Destination URL to open.
        profile_name: Optional auth profile name for session setup.
    Returns:
        A tool response payload describing navigation outcome.
    """
    return await SESSION_MANAGER.navigate(url, profile_name=profile_name)


async def browser_navigate_back() -> dict:
    """Navigate back in browser history.

    Args:
        None.
    Returns:
        A tool response payload describing navigation outcome.
    """
    return await SESSION_MANAGER.navigate_back()


async def browser_snapshot(filename: Optional[str] = None, selector: Optional[str] = None) -> dict:
    """Capture a DOM snapshot and optional screenshot.

    Args:
        filename: Optional artifact filename for a screenshot.
        selector: Optional selector to scope snapshot content.
    Returns:
        A tool response payload with snapshot metadata.
    """
    return await SESSION_MANAGER.snapshot(selector=selector, filename=filename)


async def browser_click(
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    double_click: bool = False,
) -> dict:
    """Click an element by snapshot ref or selector.

    Args:
        ref: Optional snapshot element reference.
        selector: Optional CSS selector to target.
        double_click: Whether to perform a double click.
    Returns:
        A tool response payload describing click execution.
    """
    return await SESSION_MANAGER.click(ref=ref, selector=selector, double_click=double_click)


async def browser_type(
    text: str,
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    submit: bool = False,
    slowly: bool = False,
) -> dict:
    """Type text into an element and optionally submit.

    Args:
        text: Text to input.
        ref: Optional snapshot element reference.
        selector: Optional CSS selector to target.
        submit: Whether to submit after typing.
        slowly: Whether to type with delay between keystrokes.
    Returns:
        A tool response payload describing typing execution.
    """
    return await SESSION_MANAGER.type_text(
        ref=ref,
        selector=selector,
        text=text,
        submit=submit,
        slowly=slowly,
    )


async def browser_hover(ref: Optional[str] = None, selector: Optional[str] = None) -> dict:
    """Hover over an element by ref or selector.

    Args:
        ref: Optional snapshot element reference.
        selector: Optional CSS selector to target.
    Returns:
        A tool response payload describing hover execution.
    """
    return await SESSION_MANAGER.hover(ref=ref, selector=selector)


async def browser_select_option(
    values: list[str],
    ref: Optional[str] = None,
    selector: Optional[str] = None,
) -> dict:
    """Select one or more values in a select element.

    Args:
        values: Option values to select.
        ref: Optional snapshot element reference.
        selector: Optional CSS selector to target.
    Returns:
        A tool response payload describing selection execution.
    """
    return await SESSION_MANAGER.select_option(ref=ref, selector=selector, values=values)


async def browser_file_upload(paths: Optional[list[str]] = None) -> dict:
    """Upload files with the active file chooser.

    Args:
        paths: Optional list of file paths to upload.
    Returns:
        A tool response payload describing upload execution.
    """
    return await SESSION_MANAGER.file_upload(paths)


async def browser_tabs(
    action: Literal["list", "new", "select", "close"], index: Optional[int] = None
) -> dict:
    """Manage tabs by listing, creating, selecting, or closing.

    Args:
        action: Tab operation; one of "list", "new", "select", or "close".
        index: Optional tab index for select/close operations.
    Returns:
        A tool response payload with current tab state.
    """
    allowed_actions = {"list", "new", "select", "close"}
    if action not in allowed_actions:
        raise ValueError(
            "browser_tabs received invalid action "
            f"{action!r}; expected one of {sorted(allowed_actions)} for SESSION_MANAGER.tabs"
        )
    return await SESSION_MANAGER.tabs(action=action, index=index)


async def browser_close() -> dict:
    """Close the active browser session.

    Args:
        None.
    Returns:
        A tool response payload describing session shutdown.
    """
    return await SESSION_MANAGER.close()


async def browser_console_messages(level: str = "info", all_messages: bool = False) -> dict:
    """Fetch captured console messages from the active page.

    Args:
        level: Minimum severity to include.
        all_messages: Whether to include all buffered messages.
    Returns:
        A tool response payload containing console entries.
    """
    return await SESSION_MANAGER.console_messages(level=level, all_messages=all_messages)


async def browser_network_requests(include_static: bool = False) -> dict:
    """Fetch captured network requests for the active page.

    Args:
        include_static: Whether to include static asset requests.
    Returns:
        A tool response payload containing network entries.
    """
    return await SESSION_MANAGER.network_requests(include_static=include_static)


async def browser_take_screenshot(
    filename: Optional[str] = None,
    full_page: bool = False,
    ref: Optional[str] = None,
    selector: Optional[str] = None,
    img_type: str = "png",
) -> dict:
    """Take a page or element screenshot.

    Args:
        filename: Optional artifact filename.
        full_page: Whether to capture the full page.
        ref: Optional snapshot element reference.
        selector: Optional CSS selector to target.
        img_type: Image format such as "png" or "jpeg".
    Returns:
        A tool response payload with screenshot metadata.
    """
    return await SESSION_MANAGER.take_screenshot(
        filename=filename,
        full_page=full_page,
        ref=ref,
        selector=selector,
        img_type=img_type,
    )


async def browser_wait_for(
    time_secs: Optional[float] = None,
    text: Optional[str] = None,
    text_gone: Optional[str] = None,
) -> dict:
    """Wait for time or text conditions in the active page.

    Args:
        time_secs: Optional duration to wait in seconds.
        text: Optional text that must appear.
        text_gone: Optional text that must disappear.
    Returns:
        A tool response payload describing wait completion.
    """
    return await SESSION_MANAGER.wait_for(time_secs=time_secs, text=text, text_gone=text_gone)


async def browser_press_key(key: str) -> dict:
    """Press a keyboard key on the active page.

    Args:
        key: Keyboard key identifier.
    Returns:
        A tool response payload describing key press execution.
    """
    return await SESSION_MANAGER.press_key(key)


async def browser_resize(width: int, height: int) -> dict:
    """Resize the browser viewport.

    Args:
        width: Viewport width in pixels.
        height: Viewport height in pixels.
    Returns:
        A tool response payload describing resize execution.
    """
    return await SESSION_MANAGER.resize(width=width, height=height)


async def browser_handle_dialog(accept: bool = True, prompt_text: Optional[str] = None) -> dict:
    """Set behavior for the next native browser dialog.

    Args:
        accept: Whether to accept the dialog.
        prompt_text: Optional text for prompt dialogs.
    Returns:
        A tool response payload describing dialog handler state.
    """
    return await SESSION_MANAGER.handle_dialog(accept=accept, prompt_text=prompt_text)


async def browser_route(
    pattern: str,
    status: int = 200,
    body: Optional[str] = None,
    content_type: Optional[str] = None,
    headers: Optional[list[str]] = None,
) -> dict:
    """Register a mocked network route.

    Args:
        pattern: URL match pattern for interception.
        status: HTTP status code for the mocked response.
        body: Optional response body text.
        content_type: Optional response content type.
        headers: Optional list of "Key: Value" response headers.
    Returns:
        A tool response payload describing route registration.
    """
    normalized_headers: list[str] = []
    if headers:
        for idx, header in enumerate(headers):
            value = header.strip()
            if not value:
                continue
            if ":" not in value:
                raise ValueError(
                    "browser_route headers must use 'Key: Value' format; "
                    f"got invalid entry at index {idx}: {header!r}"
                )
            key, _ = value.split(":", 1)
            if not key.strip():
                raise ValueError(
                    "browser_route headers must include a non-empty key before ':'; "
                    f"got invalid entry at index {idx}: {header!r}"
                )
            normalized_headers.append(value)
    return await SESSION_MANAGER.route_add(
        pattern=pattern,
        status=status,
        body=body,
        content_type=content_type,
        headers=normalized_headers,
    )


async def browser_unroute(pattern: Optional[str] = None) -> dict:
    """Remove mocked routes from the active session.

    Args:
        pattern: Optional URL pattern; clears all when omitted.
    Returns:
        A tool response payload describing route removal.
    """
    return await SESSION_MANAGER.route_remove(pattern=pattern)


async def browser_route_list() -> dict:
    """List currently registered mocked routes.

    Args:
        None.
    Returns:
        A tool response payload containing registered routes.
    """
    return await SESSION_MANAGER.route_list()


async def browser_network_state_set(state: str) -> dict:
    """Set simulated network state for the active session.

    Args:
        state: Target network mode (for example, online/offline).
    Returns:
        A tool response payload describing applied state.
    """
    return await SESSION_MANAGER.network_state_set(state=state)


async def browser_cookie_list(domain: Optional[str] = None, path: Optional[str] = None) -> dict:
    """List cookies in the active browser context.

    Args:
        domain: Optional domain filter.
        path: Optional path filter.
    Returns:
        A tool response payload containing cookie entries.
    """
    return await SESSION_MANAGER.cookie_list(domain=domain, path=path)


async def browser_cookie_get(name: str) -> dict:
    """Get a cookie by name.

    Args:
        name: Cookie name.
    Returns:
        A tool response payload with cookie details.
    """
    return await SESSION_MANAGER.cookie_get(name=name)


async def browser_cookie_set(
    name: str,
    value: str,
    domain: Optional[str] = None,
    path: str = "/",
    expires: Optional[float] = None,
    http_only: bool = True,
    secure: bool = True,
    same_site: Optional[str] = None,
) -> dict:
    """Set or update a cookie with secure defaults.

    Args:
        name: Cookie name.
        value: Cookie value.
        domain: Optional cookie domain.
        path: Cookie path.
        expires: Optional UNIX timestamp expiration.
        http_only: Whether cookie is HTTP-only (defaults to True).
        secure: Whether cookie requires HTTPS (defaults to True).
        same_site: Optional same-site policy.
    Returns:
        A tool response payload describing cookie write status.
    """
    return await SESSION_MANAGER.cookie_set(
        name=name,
        value=value,
        domain=domain,
        path=path,
        expires=expires,
        http_only=http_only,
        secure=secure,
        same_site=same_site,
    )


async def browser_cookie_delete(name: str) -> dict:
    """Delete a cookie by name.

    Args:
        name: Cookie name.
    Returns:
        A tool response payload describing cookie removal.
    """
    return await SESSION_MANAGER.cookie_delete(name=name)


async def browser_cookie_clear() -> dict:
    """Clear all cookies in the active context.

    Args:
        None.
    Returns:
        A tool response payload describing clear operation.
    """
    return await SESSION_MANAGER.cookie_clear()


async def browser_storage_state(filename: Optional[str] = None) -> dict:
    """Persist browser storage state to an artifact file.

    Args:
        filename: Optional output filename.
    Returns:
        A tool response payload with storage state metadata.
    """
    return await SESSION_MANAGER.storage_state_save(filename=filename)


async def browser_set_storage_state(filename: str) -> dict:
    """Restore browser storage state from an artifact file.

    Args:
        filename: Storage state filename to restore.
    Returns:
        A tool response payload describing restore status.
    """
    return await SESSION_MANAGER.storage_state_restore(filename=filename)


async def browser_localstorage_list() -> dict:
    """List localStorage key-value pairs.

    Args:
        None.
    Returns:
        A tool response payload containing localStorage data.
    """
    return await SESSION_MANAGER.localstorage_list()


async def browser_localstorage_get(key: str) -> dict:
    """Get a localStorage value by key.

    Args:
        key: localStorage key.
    Returns:
        A tool response payload with the requested value.
    """
    return await SESSION_MANAGER.localstorage_get(key=key)


async def browser_localstorage_set(key: str, value: str) -> dict:
    """Set a localStorage key-value pair.

    Args:
        key: localStorage key.
        value: Value to store.
    Returns:
        A tool response payload describing write status.
    """
    return await SESSION_MANAGER.localstorage_set(key=key, value=value)


async def browser_localstorage_delete(key: str) -> dict:
    """Delete a localStorage key.

    Args:
        key: localStorage key.
    Returns:
        A tool response payload describing deletion status.
    """
    return await SESSION_MANAGER.localstorage_delete(key=key)


async def browser_localstorage_clear() -> dict:
    """Clear all localStorage entries.

    Args:
        None.
    Returns:
        A tool response payload describing clear status.
    """
    return await SESSION_MANAGER.localstorage_clear()


async def browser_sessionstorage_list() -> dict:
    """List sessionStorage key-value pairs.

    Args:
        None.
    Returns:
        A tool response payload containing sessionStorage data.
    """
    return await SESSION_MANAGER.sessionstorage_list()


async def browser_sessionstorage_get(key: str) -> dict:
    """Get a sessionStorage value by key.

    Args:
        key: sessionStorage key.
    Returns:
        A tool response payload with the requested value.
    """
    return await SESSION_MANAGER.sessionstorage_get(key=key)


async def browser_sessionstorage_set(key: str, value: str) -> dict:
    """Set a sessionStorage key-value pair.

    Args:
        key: sessionStorage key.
        value: Value to store.
    Returns:
        A tool response payload describing write status.
    """
    return await SESSION_MANAGER.sessionstorage_set(key=key, value=value)


async def browser_sessionstorage_delete(key: str) -> dict:
    """Delete a sessionStorage key.

    Args:
        key: sessionStorage key.
    Returns:
        A tool response payload describing deletion status.
    """
    return await SESSION_MANAGER.sessionstorage_delete(key=key)


async def browser_sessionstorage_clear() -> dict:
    """Clear all sessionStorage entries.

    Args:
        None.
    Returns:
        A tool response payload describing clear status.
    """
    return await SESSION_MANAGER.sessionstorage_clear()

