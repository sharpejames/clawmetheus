"""
safety.py — Hardcoded safety checks for Clawmetheus tasks.

Rules:
  - Never draw on the wrong window
  - Always verify browser tab before interacting
  - Screenshot-verify at key checkpoints
  - Adapt when things aren't where expected
"""
import time
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from platform_windows import WindowsBackend
_backend = WindowsBackend()


def get_active_window() -> str:
    return _backend.get_active_window()


def assert_window_active(title_substr: str, context: str = ""):
    """Hard stop if expected window is not active."""
    active = get_active_window()
    if title_substr.lower() not in active.lower():
        raise RuntimeError(
            f"SAFETY STOP: expected '{title_substr}' active, got '{active}' [{context}]"
        )
    return active


def ensure_window_active(title_substr: str, max_attempts: int = 3) -> bool:
    """
    Bring window to front and verify. Uses pywinauto.
    Returns True if successful, False if window not found.
    """
    from pywinauto import Desktop
    for attempt in range(max_attempts):
        active = get_active_window()
        if title_substr.lower() in active.lower():
            return True
        try:
            win = Desktop(backend="uia").window(title_re=f".*{title_substr}.*")
            win.maximize()
            time.sleep(0.3)
            win.set_focus()
            time.sleep(0.6)
            # Click title bar to cement focus
            from task_runner import get_window_rect, click
            rect = get_window_rect(title_substr)
            if rect:
                click((rect["left"] + rect["right"]) // 2, rect["top"] + 15)
                time.sleep(0.4)
        except Exception as e:
            print(f"  [safety] ensure_window_active attempt {attempt+1} failed: {e}")
        time.sleep(0.3)
    active = get_active_window()
    return title_substr.lower() in active.lower()


def ensure_browser_tab(tab_title_substr: str, max_tabs: int = 9) -> bool:
    """
    Switch Chrome to the tab matching tab_title_substr.
    Tries Ctrl+1 through Ctrl+N until the right tab is active.
    Returns True if found.
    """
    from pywinauto import Desktop
    from task_runner import key

    # Make sure Chrome is focused first
    try:
        Desktop(backend="uia").window(title_re=".*Chrome.*").set_focus()
        time.sleep(0.5)
    except Exception:
        pass

    for i in range(1, max_tabs + 1):
        key("control", str(i))
        time.sleep(0.6)
        active = get_active_window()
        if tab_title_substr.lower() in active.lower():
            print(f"  [safety] Tab found at Ctrl+{i}: {active}")
            return True

    print(f"  [safety] Tab '{tab_title_substr}' not found in {max_tabs} tabs")
    return False


def find_element_with_fallback(uia_name: str, cache_key: str, cache_element: str,
                                screenshot_fn=None) -> tuple:
    """
    Find a UI element using layered approach:
      1. UIA find_element (fast, <10ms)
      2. ui_cache known coords (instant)
      3. Screenshot + Gemini perceive (slow, last resort)
    Returns (x, y) or raises RuntimeError.
    """
    from task_runner import find_element
    import ui_cache

    # Layer 1: UIA
    el = find_element(uia_name)
    if el:
        print(f"  [safety] '{uia_name}' via UIA: ({el['cx']}, {el['cy']})")
        return el["cx"], el["cy"]

    # Layer 2: Cache
    cached = ui_cache.coords(cache_key, cache_element)
    if cached:
        print(f"  [safety] '{uia_name}' via cache: {cached}")
        return cached[0], cached[1]

    # Layer 3: Gemini perceive
    if screenshot_fn:
        try:
            import requests
            r = requests.get(
                f"http://127.0.0.1:7331/perceive?task=find+{uia_name.replace(' ', '+')}",
                timeout=25
            ).json()
            for el in r.get("elements", []):
                if uia_name.lower() in el.get("label", "").lower():
                    x, y = el["x"], el["y"]
                    print(f"  [safety] '{uia_name}' via Gemini: ({x}, {y})")
                    ui_cache.put(cache_key, cache_element, [x, y], f"Found via Gemini on {time.strftime('%Y-%m-%d')}")
                    return x, y
        except Exception as e:
            print(f"  [safety] Gemini perceive failed: {e}")

    raise RuntimeError(f"Could not find element '{uia_name}' via any method")
