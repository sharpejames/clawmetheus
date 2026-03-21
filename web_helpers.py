"""
Browser DOM inspection — dual mode: CDP (fast/invisible) + DevTools Console (universal).

MODE 1 — CDP (preferred):
  Connects via Chrome DevTools Protocol on port 9222.
  Requires browser started with --remote-debugging-port=9222.
  Invisible, fast, no clipboard interference.

MODE 2 — DevTools Console (fallback):
  Opens F12, pastes JS into Console that fetch()es results back to
  Clawmetheus at http://127.0.0.1:7331/dom-result.
  Works with ANY browser, no special flags, no clipboard corruption.
  DevTools stays open between queries to avoid constant F12 toggling.

Auto-detection: tries CDP first (fast port check), falls back to DevTools.

Usage:
    from web_helpers import web_find, web_find_all, web_find_text, web_page_info, web_eval
"""

import json
import time
import os
import requests
import pyperclip
import pyautogui

# ── Configuration ─────────────────────────────────────────────────────────────

CDP_PORT = 9222
CDP_BASE = f"http://localhost:{CDP_PORT}"

# ── Browser detection ─────────────────────────────────────────────────────────

BROWSERS = {
    "chrome": {
        "name": "Google Chrome",
        "paths": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "exe": "chrome.exe",
    },
    "edge": {
        "name": "Microsoft Edge",
        "paths": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "exe": "msedge.exe",
    },
    "brave": {
        "name": "Brave Browser",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "exe": "brave.exe",
    },
}


def detect_browser():
    """Detect which Chromium-based browser is installed. Returns (key, exe_path) or (None, None)."""
    for key, info in BROWSERS.items():
        for path in info["paths"]:
            if os.path.exists(path):
                return key, path
    import shutil
    for key, info in BROWSERS.items():
        found = shutil.which(info["exe"])
        if found:
            return key, found
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
#  MODE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_mode = None  # "cdp" | "devtools" | None


def cdp_available():
    """Check if browser CDP is reachable on port 9222."""
    try:
        r = requests.get(f"{CDP_BASE}/json/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _detect_mode():
    """Auto-detect best available mode. Caches result."""
    global _mode
    if _mode is not None:
        return _mode
    if cdp_available():
        _mode = "cdp"
        print("[web] Using CDP mode (fast/invisible)", flush=True)
    else:
        _mode = "devtools"
        print("[web] CDP not available — using DevTools Console mode", flush=True)
    return _mode


def reset_mode():
    """Force re-detection on next call (e.g. after starting Chrome with CDP flag)."""
    global _mode, _devtools_open
    _mode = None
    _devtools_open = False


# ══════════════════════════════════════════════════════════════════════════════
#  CDP MODE — fast, invisible, WebSocket-based
# ══════════════════════════════════════════════════════════════════════════════

_ws_cache = {}  # target_id -> ws
_msg_id = 0


def _get_tabs():
    """List open browser tabs via CDP."""
    try:
        r = requests.get(f"{CDP_BASE}/json/list", timeout=3)
        return [t for t in r.json() if t.get("type") == "page"]
    except Exception as e:
        raise ConnectionError(
            f"Cannot connect to browser CDP on port {CDP_PORT}. "
            f"Start browser with --remote-debugging-port={CDP_PORT}. "
            f"Error: {e}"
        )


def _get_active_tab():
    tabs = _get_tabs()
    if not tabs:
        raise ConnectionError("No browser tabs found via CDP")
    return tabs[0]


def _connect(tab=None):
    import websocket
    if tab is None:
        tab = _get_active_tab()
    tid = tab["id"]
    if tid in _ws_cache:
        try:
            _ws_cache[tid].ping()
            return _ws_cache[tid]
        except Exception:
            try:
                _ws_cache[tid].close()
            except Exception:
                pass
            del _ws_cache[tid]
    ws_url = tab["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=10)
    _ws_cache[tid] = ws
    return ws


def _send(ws, method, params=None):
    global _msg_id
    _msg_id += 1
    msg = {"id": _msg_id, "method": method, "params": params or {}}
    ws.send(json.dumps(msg))
    deadline = time.time() + 15
    while time.time() < deadline:
        raw = ws.recv()
        resp = json.loads(raw)
        if resp.get("id") == _msg_id:
            if "error" in resp:
                raise RuntimeError(f"CDP error: {resp['error']}")
            return resp.get("result", {})
    raise TimeoutError(f"CDP command timed out: {method}")


def _cdp_eval(js_code, tab=None):
    """Execute JS via CDP WebSocket. Returns the result value."""
    ws = _connect(tab)
    result = _send(ws, "Runtime.evaluate", {
        "expression": js_code,
        "returnByValue": True,
        "awaitPromise": False,
    })
    val = result.get("result", {})
    if val.get("type") == "undefined":
        return None
    if "value" in val:
        return val["value"]
    if val.get("subtype") == "error":
        raise RuntimeError(f"JS error: {val.get('description', val)}")
    return val


def _cdp_viewport_offset():
    """Get browser viewport offset for CDP mode."""
    info = _cdp_eval("""
    (() => ({
        winX: window.screenX,
        winY: window.screenY,
        outerW: window.outerWidth,
        outerH: window.outerHeight,
        innerW: window.innerWidth,
        innerH: window.innerHeight,
        dpr: window.devicePixelRatio || 1
    }))()
    """)
    if info:
        toolbar_h = info["outerH"] - info["innerH"]
        border_w = (info["outerW"] - info["innerW"]) // 2
        return {
            "x": info["winX"] + border_w,
            "y": info["winY"] + toolbar_h,
            "dpr": info.get("dpr", 1),
        }
    return {"x": 0, "y": 80, "dpr": 1}


# ══════════════════════════════════════════════════════════════════════════════
#  DEVTOOLS CONSOLE MODE — universal, no flags needed
# ══════════════════════════════════════════════════════════════════════════════

_devtools_open = False
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False


def _open_devtools():
    """Open DevTools Console if not already open. Tracks state to avoid toggling."""
    global _devtools_open
    if _devtools_open:
        return
    # Ctrl+Shift+J opens Console directly (not Elements tab)
    pyautogui.hotkey("ctrl", "shift", "j")
    time.sleep(1.5)
    _devtools_open = True
    print("[web/devtools] Opened DevTools Console", flush=True)


def close_devtools():
    """Close DevTools. Call this when done with web interactions."""
    global _devtools_open
    if not _devtools_open:
        return
    pyautogui.hotkey("ctrl", "shift", "j")
    time.sleep(0.5)
    _devtools_open = False
    print("[web/devtools] Closed DevTools Console", flush=True)


def _devtools_eval(js_code):
    """
    Execute JS via DevTools Console.

    Primary: fetch() relay to Clawmetheus /dom-result (fast, no clipboard).
    Fallback: clipboard relay when fetch() is blocked by CSP (e.g. grok.com).
    """
    import uuid
    _open_devtools()

    request_id = uuid.uuid4().hex[:12]

    # Primary: fetch() relay
    wrapped_js = (
        f'(async()=>{{try{{const _r=(()=>{{ return {js_code} }})();'
        f'await fetch("http://127.0.0.1:7331/dom-result",{{method:"POST",'
        f'headers:{{"Content-Type":"application/json"}},'
        f'body:JSON.stringify({{request_id:"{request_id}",data:_r}})}});'
        f'console.log("dom-ok:{request_id}")'
        f'}}catch(e){{await fetch("http://127.0.0.1:7331/dom-result",{{method:"POST",'
        f'headers:{{"Content-Type":"application/json"}},'
        f'body:JSON.stringify({{request_id:"{request_id}",data:null}})}});'
        f'console.error("dom-err",e)}}}})()'
    )

    old_clipboard = ""
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        pass

    pyperclip.copy(wrapped_js)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(0.1)
    try:
        pyperclip.copy(old_clipboard)
    except Exception:
        pass

    # Poll for fetch() result
    try:
        import requests as _req
        r = _req.get(f"http://127.0.0.1:7331/dom-result/{request_id}", timeout=12)
        if r.status_code == 200:
            return r.json().get("data")
        elif r.status_code == 408:
            print("[web/devtools] fetch blocked by CSP, using clipboard fallback", flush=True)
            return _devtools_eval_clipboard(js_code)
        else:
            return None
    except TimeoutError:
        print("[web/devtools] fetch timed out, using clipboard fallback", flush=True)
        return _devtools_eval_clipboard(js_code)
    except Exception as e:
        print(f"[web/devtools] fetch error: {e}, using clipboard fallback", flush=True)
        return _devtools_eval_clipboard(js_code)


def _devtools_eval_clipboard(js_code):
    """
    Fallback: execute JS via DevTools Console, relay result through clipboard.
    Used when fetch() to localhost is blocked by CSP (e.g. grok.com, x.com).
    Uses DevTools copy() utility function which is always available.
    """
    import json as _json

    clipboard_js = (
        f'(()=>{{try{{const _r=(()=>{{ return {js_code} }})();'
        f'copy(JSON.stringify(_r));console.log("clipboard-ok")'
        f'}}catch(e){{copy(JSON.stringify(null));console.error("clipboard-err",e)}}}})() '
    )

    sentinel = "__DEVTOOLS_PENDING__"
    pyperclip.copy(sentinel)
    time.sleep(0.05)

    pyperclip.copy(clipboard_js)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    pyautogui.press("enter")

    # Wait for copy() to update clipboard
    deadline = time.time() + 8
    time.sleep(0.5)
    while time.time() < deadline:
        try:
            clip = pyperclip.paste()
            if clip and clip != sentinel and clip != clipboard_js:
                try:
                    result = _json.loads(clip)
                    print("[web/devtools] clipboard fallback OK", flush=True)
                    return result
                except (_json.JSONDecodeError, ValueError):
                    pass
        except Exception:
            pass
        time.sleep(0.3)

    print("[web/devtools] clipboard fallback timed out", flush=True)
    raise TimeoutError("DevTools eval timed out (fetch blocked by CSP, clipboard fallback failed)")



def _devtools_viewport_offset():
    """Get browser viewport offset for DevTools mode."""
    info = _devtools_eval("""
    (() => ({
        winX: window.screenX,
        winY: window.screenY,
        outerW: window.outerWidth,
        outerH: window.outerHeight,
        innerW: window.innerWidth,
        innerH: window.innerHeight,
        dpr: window.devicePixelRatio || 1
    }))()
    """)
    if info:
        toolbar_h = info["outerH"] - info["innerH"]
        border_w = (info["outerW"] - info["innerW"]) // 2
        return {
            "x": info["winX"] + border_w,
            "y": info["winY"] + toolbar_h,
            "dpr": info.get("dpr", 1),
        }
    return {"x": 0, "y": 80, "dpr": 1}


# ══════════════════════════════════════════════════════════════════════════════
#  UNIFIED PUBLIC API — auto-selects mode
# ══════════════════════════════════════════════════════════════════════════════

def _js_eval(js_code):
    """Execute JS using whichever mode is available."""
    mode = _detect_mode()
    if mode == "cdp":
        return _cdp_eval(js_code)
    else:
        return _devtools_eval(js_code)


def _get_viewport_offset():
    """Get viewport offset using whichever mode is available."""
    mode = _detect_mode()
    if mode == "cdp":
        return _cdp_viewport_offset()
    else:
        return _devtools_viewport_offset()


# ── Coordinate conversion ─────────────────────────────────────────────────────

def _to_screen(elements, offset=None):
    """Convert viewport-relative positions to screen coordinates."""
    if offset is None:
        offset = _get_viewport_offset()
    dpr = offset["dpr"]
    for el in elements:
        el["x"] = int(offset["x"] + (el["_left"] + el["_width"] / 2) * dpr)
        el["y"] = int(offset["y"] + (el["_top"] + el["_height"] / 2) * dpr)
        el["width"] = int(el["_width"] * dpr)
        el["height"] = int(el["_height"] * dpr)
        for k in ["_left", "_top", "_width", "_height"]:
            if k in el:
                del el[k]
    return elements


# ── Public API ────────────────────────────────────────────────────────────────

def web_eval(js_code):
    """Execute JavaScript in the active browser tab. Returns the result value.

    Example:
        title = web_eval("document.title")
        count = web_eval("document.querySelectorAll('button').length")
    """
    return _js_eval(js_code)


def web_find(selector):
    """Find a DOM element by CSS selector. Returns screen coordinates and info.

    Returns dict: {x, y, width, height, tag, text, visible} or None if not found.
    x, y are the CENTER of the element in SCREEN coordinates.

    Example:
        el = web_find("input[placeholder*='How can I help']")
        if el:
            click(el['x'], el['y'])
    """
    js = f"""
    (() => {{
        const el = document.querySelector({json.dumps(selector)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return null;
        return {{
            _left: r.left, _top: r.top, _width: r.width, _height: r.height,
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').trim().substring(0, 100),
            placeholder: el.placeholder || '',
            type: el.type || '',
            role: el.getAttribute('role') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            visible: r.width > 0 && r.height > 0 && window.getComputedStyle(el).display !== 'none'
        }};
    }})()
    """
    result = _js_eval(js)
    if not result:
        return None
    offset = _get_viewport_offset()
    return _to_screen([result], offset)[0]


def web_find_all(selector, limit=20):
    """Find all matching DOM elements. Returns list of dicts with screen coordinates.

    Example:
        buttons = web_find_all("button")
        for btn in buttons:
            print(f"{btn['text']} at ({btn['x']}, {btn['y']})")
    """
    js = f"""
    (() => {{
        const els = document.querySelectorAll({json.dumps(selector)});
        const results = [];
        for (let i = 0; i < Math.min(els.length, {limit}); i++) {{
            const el = els[i];
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            results.push({{
                _left: r.left, _top: r.top, _width: r.width, _height: r.height,
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 100),
                placeholder: el.placeholder || '',
                type: el.type || '',
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                visible: r.width > 0 && r.height > 0 && window.getComputedStyle(el).display !== 'none'
            }});
        }}
        return results;
    }})()
    """
    results = _js_eval(js)
    if not results:
        return []
    offset = _get_viewport_offset()
    return _to_screen(results, offset)


def web_find_text(text, tag=None):
    """Find a visible DOM element containing the given text. Returns screen coords or None.

    Finds the SMALLEST matching element (most specific).

    Example:
        el = web_find_text("Submit")
        if el: click(el['x'], el['y'])
    """
    tag_filter = f"'{tag}'" if tag else "null"
    js = f"""
    (() => {{
        const searchText = {json.dumps(text)}.toLowerCase();
        const tagFilter = {tag_filter};
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
        let best = null, bestArea = Infinity, node;
        while (node = walker.nextNode()) {{
            if (tagFilter && node.tagName.toLowerCase() !== tagFilter.toLowerCase()) continue;
            const t = (node.textContent || '').trim().toLowerCase();
            const a = (node.getAttribute('aria-label') || '').toLowerCase();
            const p = (node.placeholder || '').toLowerCase();
            if (t.includes(searchText) || a.includes(searchText) || p.includes(searchText)) {{
                const r = node.getBoundingClientRect();
                const area = r.width * r.height;
                if (r.width > 0 && r.height > 0 && area < bestArea && window.getComputedStyle(node).display !== 'none') {{
                    bestArea = area;
                    best = {{
                        _left: r.left, _top: r.top, _width: r.width, _height: r.height,
                        tag: node.tagName.toLowerCase(),
                        text: (node.textContent || '').trim().substring(0, 100),
                        placeholder: node.placeholder || '',
                        role: node.getAttribute('role') || '',
                        ariaLabel: node.getAttribute('aria-label') || '',
                        visible: true
                    }};
                }}
            }}
        }}
        return best;
    }})()
    """
    result = _js_eval(js)
    if not result:
        return None
    offset = _get_viewport_offset()
    return _to_screen([result], offset)[0]


def web_page_info():
    """Get current page info: URL, title, and all interactive elements with screen coords.

    Example:
        info = web_page_info()
        print(f"Page: {info['title']} ({info['url']})")
        for el in info['elements']:
            print(f"  {el['tag']} '{el['text']}' at ({el['x']},{el['y']})")
    """
    js = """
    (() => {
        const sel = 'button, a, input, textarea, select, [role="button"], [role="textbox"], [role="link"], [contenteditable="true"]';
        const els = document.querySelectorAll(sel);
        const results = [];
        for (let i = 0; i < Math.min(els.length, 50); i++) {
            const el = els[i];
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            if (r.width < 5 || r.height < 5 || s.display === 'none' || s.visibility === 'hidden') continue;
            results.push({
                _left: r.left, _top: r.top, _width: r.width, _height: r.height,
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 60),
                placeholder: el.placeholder || '',
                type: el.type || '',
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                name: el.name || '',
                id: el.id || '',
            });
        }
        return { url: window.location.href, title: document.title, _elements: results };
    })()
    """
    result = _js_eval(js)
    if not result:
        return {"url": "", "title": "", "elements": []}
    offset = _get_viewport_offset()
    elements = _to_screen(result.get("_elements", []), offset)
    return {"url": result["url"], "title": result["title"], "elements": elements}


def get_mode():
    """Return current mode: 'cdp', 'devtools', or None if not yet detected."""
    return _mode
