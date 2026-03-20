from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Any
import base64
import io
import time
import math
import ctypes
import platform as _platform
import sys
import os

import mss
import pyautogui
import pyperclip
from PIL import Image

from src import vision
from src import moondream_vision

app = FastAPI(title="Clawmetheus", version="2.0")

# Speed: no pause between actions
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False  # Disabled â€” corner trigger breaks automation
pyautogui.MINIMUM_DURATION = 0  # Allow sub-0.1s moves for faster drawing

_SYSTEM = _platform.system()

# Screen dimensions â€” cross-platform
if _SYSTEM == "Windows":
    import ctypes
    user32 = ctypes.windll.user32
    SCREEN_W = user32.GetSystemMetrics(0)
    SCREEN_H = user32.GetSystemMetrics(1)
else:
    with mss.mss() as _sct:
        _mon = _sct.monitors[1]
        SCREEN_W = _mon["width"]
        SCREEN_H = _mon["height"]

# Perception layer (lazy-loaded)
_perception = None

def _get_perception():
    global _perception
    if _perception is None:
        sys.path.insert(0, os.path.dirname(__file__))
        from perception import PerceptionLayer
        _perception = PerceptionLayer(gemini_fn=lambda q, img: moondream_vision.ask(img, q))
    return _perception

# Key name normalization
KEY_MAP = {
    "control": "ctrl",
    "windows": "win",
    "return": "enter",
    "escape": "esc",
    "pageup": "pgup",
    "pagedown": "pgdn",
}

def map_key(k: str) -> str:
    return KEY_MAP.get(k.lower(), k.lower())


def _grab_screenshot(scale: float) -> tuple[str, int, int]:
    """Grab screen â†’ (base64_jpeg, img_w, img_h)."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return b64, img.width, img.height


def _add_grid_overlay(img: Image.Image, scale: float, spacing: int = 100) -> Image.Image:
    """
    Overlay a coordinate grid on a screenshot. Labels show ACTUAL screen coords.
    spacing = grid interval in actual screen pixels (default 100px).
    Helps AI models identify element positions accurately.
    """
    from PIL import ImageDraw
    img = img.copy()
    draw = ImageDraw.Draw(img)
    step = max(1, int(spacing * scale))  # grid step in screenshot pixels

    for x in range(0, img.width, step):
        actual_x = round(x / scale)
        draw.line([(x, 0), (x, img.height)], fill=(220, 50, 50), width=1)
        draw.text((x + 2, 2), str(actual_x), fill=(220, 50, 50))

    for y in range(0, img.height, step):
        actual_y = round(y / scale)
        draw.line([(0, y), (img.width, y)], fill=(50, 50, 220), width=1)
        draw.text((2, y + 2), str(actual_y), fill=(50, 50, 220))

    return img


def _b64_from_image(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# â”€â”€ Endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/status")
def status():
    return {"status": "ok", "screen": {"width": SCREEN_W, "height": SCREEN_H}}


@app.get("/screenshot/base64")
def screenshot(scale: float = 0.5):
    b64, w, h = _grab_screenshot(scale)
    return {"image": b64, "width": w, "height": h, "scale": scale,
            "screen": {"width": SCREEN_W, "height": SCREEN_H}}


@app.get("/screenshot/grid")
def screenshot_grid(scale: float = 0.5, spacing: int = 100):
    """Screenshot with coordinate grid overlay. Labels show actual screen coords."""
    b64, w, h = _grab_screenshot(scale)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img = _add_grid_overlay(img, scale=scale, spacing=spacing)
    return {"image": _b64_from_image(img), "width": w, "height": h, "scale": scale}


@app.get("/screenshot/window")
def screenshot_window(title: str, scale: float = 1.0):
    """Capture a specific window by title using PrintWindow â€” works even if not in foreground."""
    gdi32 = ctypes.windll.gdi32

    found = []
    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title.lower() in buf.value.lower():
                found.append((hwnd, buf.value))
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(_cb), 0)

    if not found:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"Window '{title}' not found"})

    hwnd, win_title = found[0]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    if w <= 0 or h <= 0:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Window has zero size"})

    hwndDC = user32.GetWindowDC(hwnd)
    mfcDC = gdi32.CreateCompatibleDC(hwndDC)
    saveBitMap = gdi32.CreateCompatibleBitmap(hwndDC, w, h)
    gdi32.SelectObject(mfcDC, saveBitMap)
    user32.PrintWindow(hwnd, mfcDC, 2)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    raw = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mfcDC, saveBitMap, 0, h, raw, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (w, h), raw.raw, "raw", "BGRA", 0, 1).convert("RGB")
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    b64 = base64.b64encode(out.getvalue()).decode()

    gdi32.DeleteObject(saveBitMap)
    gdi32.DeleteDC(mfcDC)
    user32.ReleaseDC(hwnd, hwndDC)

    return {"ok": True, "image": b64, "width": img.width, "height": img.height,
            "window_title": win_title, "window_size": {"w": w, "h": h}}


@app.get("/cursor")
def cursor():
    x, y = pyautogui.position()
    return {"x": x, "y": y}


# â”€â”€ Unicode typing via SendInput (Windows) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if _SYSTEM == "Windows":
    import ctypes.wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _fields_ = [("type", ctypes.wintypes.DWORD), ("_input", _INPUT)]

    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1

    def _send_unicode_char(ch):
        """Type a single Unicode character via SendInput â€” no clipboard needed."""
        code = ord(ch)
        inputs = (INPUT * 2)()
        # Key down
        inputs[0].type = INPUT_KEYBOARD
        inputs[0]._input.ki.wVk = 0
        inputs[0]._input.ki.wScan = code
        inputs[0]._input.ki.dwFlags = KEYEVENTF_UNICODE
        # Key up
        inputs[1].type = INPUT_KEYBOARD
        inputs[1]._input.ki.wVk = 0
        inputs[1]._input.ki.wScan = code
        inputs[1]._input.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))


# â”€â”€ Action execution â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _execute(action: dict):
    t = action.get("type", "")

    if t == "click":
        pyautogui.click(action["x"], action["y"], button=action.get("button", "left"))

    elif t == "doubleClick":
        pyautogui.doubleClick(action["x"], action["y"])

    elif t == "move":
        pyautogui.moveTo(action["x"], action["y"])

    elif t == "type":
        # Use clipboard to handle all characters including unicode
        text = action.get("text", "")
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")

    elif t == "typeKeys":
        # Type via keyboard events â€” does NOT touch clipboard.
        # Use this when clipboard contains data you need to preserve (e.g. DOM data).
        text = action.get("text", "")
        interval = action.get("interval", 0.01)
        for ch in text:
            if ch == '\n':
                pyautogui.press('enter')
            elif ch == '\t':
                pyautogui.press('tab')
            elif ord(ch) < 128:
                # ASCII â€” use typewrite for speed
                pyautogui.typewrite(ch, interval=0)
            else:
                # Unicode â€” use Win32 SendInput with KEYEVENTF_UNICODE
                if _SYSTEM == "Windows":
                    _send_unicode_char(ch)
                else:
                    # Fallback: brief clipboard borrow for non-ASCII
                    old = pyperclip.paste()
                    pyperclip.copy(ch)
                    pyautogui.hotkey("ctrl", "v")
                    pyperclip.copy(old)
            if interval > 0:
                time.sleep(interval)

    elif t == "key":
        keys = [map_key(k) for k in action.get("keys", [])]
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)

    elif t == "scroll":
        amount = action.get("amount", 3)
        if action.get("direction", "down") == "up":
            amount = -amount
        x, y = action.get("x"), action.get("y")
        if x is not None and y is not None:
            pyautogui.scroll(amount, x=x, y=y)
        else:
            pyautogui.scroll(amount)

    elif t == "smoothDrag":
        points = action.get("points", [])
        speed = action.get("speed", 600)  # pixels/sec
        button = action.get("button", "left")
        if len(points) < 2:
            return
        # Use Win32 mouse_event for reliable drawing in UWP/WinUI3 apps (new Paint).
        # pyautogui mouseDown+moveTo doesn't register as drawing in some modern apps.
        import ctypes

        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010
        MOUSEEVENTF_ABSOLUTE = 0x8000

        me = ctypes.windll.user32.mouse_event

        def _abs_xy(sx, sy):
            return int(sx * 65536 / SCREEN_W), int(sy * 65536 / SCREEN_H)

        down_flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP

        # Move to start point
        ax, ay = _abs_xy(points[0]["x"], points[0]["y"])
        me(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0)
        time.sleep(0.06)

        # Press button down at start point
        me(down_flag | MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0)
        time.sleep(0.06)

        # Drag through all points — each move includes MOVE flag while button stays held
        for i in range(1, len(points)):
            p = points[i]
            prev = points[i - 1]
            dist = math.sqrt((p["x"] - prev["x"]) ** 2 + (p["y"] - prev["y"]) ** 2)
            ax, ay = _abs_xy(p["x"], p["y"])
            me(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0)
            delay = max(dist / speed, 0.005) if speed > 0 else 0.005
            time.sleep(delay)

        # Release button
        ax, ay = _abs_xy(points[-1]["x"], points[-1]["y"])
        me(up_flag | MOUSEEVENTF_ABSOLUTE, ax, ay, 0, 0)

    elif t == "wait":
        time.sleep(action.get("ms", 1000) / 1000)

    elif t == "batch":
        for a in action.get("actions", []):
            _execute(a)

    else:
        raise ValueError(f"Unknown action type: {t}")


class ActionRequest(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = "left"
    text: Optional[str] = None
    keys: Optional[List[str]] = None
    direction: Optional[str] = "down"
    amount: Optional[int] = 3
    points: Optional[List[Any]] = None
    speed: Optional[int] = 600
    ms: Optional[int] = 1000
    actions: Optional[List[Any]] = None
    interval: Optional[float] = 0.01


@app.post("/action")
def action(req: ActionRequest):
    try:
        _execute(req.model_dump())
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/perceive")
def perceive(scale: float = 0.5, task: str = ""):
    """Screenshot with grid overlay â†’ Gemini â†’ structured element map with actual screen coords."""
    b64, w, h = _grab_screenshot(scale)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    img = _add_grid_overlay(img, scale=scale, spacing=100)
    grid_b64 = _b64_from_image(img)
    try:
        result = moondream_vision.perceive(grid_b64, scale=scale, task=task)
    except Exception as e:
        img.save(os.path.join(os.path.dirname(__file__), "map_screen_debug.png"))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return {"ok": True, "scale": scale, "image_size": {"w": w, "h": h}, **result}


@app.get("/ask")
def ask_screen(q: str, scale: float = 0.5):
    """Ask a free-form question about the current screen. Uses Moondream locally."""
    b64, _, _ = _grab_screenshot(scale)
    try:
        answer = moondream_vision.ask(b64, q)
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    return {"ok": True, "answer": answer}


@app.get("/point")
def point_element(target: str, scale: float = 0.5):
    """Find a UI element by description using Moondream. Returns screen coords."""
    b64, _, _ = _grab_screenshot(scale)
    try:
        x, y = moondream_vision.point(b64, target, scale)
        return {"ok": x > 0, "x": x, "y": y, "target": target}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/element")
def element(name: str, role: str = "", app: str = ""):
    """Find UI element by name using platform accessibility APIs."""
    try:
        el = _get_perception().find_element(name, role=role or None, app=app or None)
        if el:
            return {"ok": True, "element": el}
        return {"ok": False, "error": f"Element '{name}' not found"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/perceive/fast")
def perceive_fast(q: str, region: str = ""):
    """Layered perception: UIA/OCR first, Gemini fallback. region=x,y,w,h"""
    try:
        reg = None
        if region:
            parts = [int(x.strip()) for x in region.split(",")]
            if len(parts) == 4:
                reg = tuple(parts)
        answer = _get_perception().ask(q, region=reg)
        return {"ok": True, "answer": answer}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# â”€â”€ DOM result exchange (for DevTools fallback) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Browser JS sends DOM query results here via fetch(); Python reads them back.
# This avoids clipboard corruption that killed the original DevTools approach.

import threading

_dom_results = {}       # request_id -> {"data": ..., "ts": ...}
_dom_results_lock = threading.Lock()


class DomResultPayload(BaseModel):
    request_id: str
    data: Any


@app.post("/dom-result")
def dom_result_post(payload: DomResultPayload):
    """Receive DOM query results from browser JS (via fetch from DevTools console)."""
    with _dom_results_lock:
        _dom_results[payload.request_id] = {
            "data": payload.data,
            "ts": time.time(),
        }
        # Prune old results (>60s)
        cutoff = time.time() - 60
        stale = [k for k, v in _dom_results.items() if v["ts"] < cutoff]
        for k in stale:
            del _dom_results[k]
    return {"ok": True}


@app.get("/dom-result/{request_id}")
def dom_result_get(request_id: str, timeout: float = 10.0):
    """Poll for a DOM query result. Blocks up to timeout seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _dom_results_lock:
            if request_id in _dom_results:
                result = _dom_results.pop(request_id)
                return {"ok": True, "data": result["data"]}
        time.sleep(0.1)
    return JSONResponse(status_code=408, content={"ok": False, "error": "timeout"})


import subprocess

@app.get("/state")
def state():
    """Full system state: screenshot + running processes + active window."""
    b64, w, h = _grab_screenshot(0.5)
    # Active window title
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    active_window = buf.value
    # Running processes (name + pid)
    result = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True)
    procs = []
    for line in result.stdout.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2:
            procs.append({"name": parts[0], "pid": parts[1]})
    return {"screenshot": b64, "width": w, "height": h,
            "active_window": active_window, "processes": procs}



@app.get("/window_rect")
def window_rect(title: str):
    """Get window rect by title substring. Returns left/top/right/bottom/title or 404."""
    user32 = ctypes.windll.user32
    found = []
    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                wt = buf.value
                if title.lower() in wt.lower():
                    class RECT(ctypes.Structure):
                        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                    r = RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                    found.append({"left": r.left, "top": r.top, "right": r.right,
                                  "bottom": r.bottom, "title": wt, "pid": 0})
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    if found:
        return {"ok": True, **found[0]}
    return JSONResponse(status_code=404, content={"ok": False, "error": f"Window \"{title}\" not found"})

@app.get("/focus")
def focus(app_name: str):
    """Ensure an app is open and in the foreground. Restores if minimized, opens if not running."""
    SW_RESTORE = 9
    user32 = ctypes.windll.user32
    found_hwnd = []

    def _enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if app_name.lower() in buf.value.lower():
                found_hwnd.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

    if found_hwnd:
        hwnd = found_hwnd[0]
        # Only restore if minimized â€” SW_RESTORE un-maximizes, which breaks coords
        SW_SHOW = 5
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            action = "restored"
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
            action = "shown"
        user32.SetForegroundWindow(hwnd)
        return {"action": action, "app": app_name}
    else:
        subprocess.Popen(app_name)
        return {"action": "opened", "app": app_name}


@app.post("/kill")
def kill():
    try:
        pyautogui.mouseUp(button="left")
        pyautogui.mouseUp(button="right")
        for key in ["shift", "ctrl", "alt"]:
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        pyautogui.moveTo(0, 0)
    except Exception:
        pass
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    print(f"Clawmetheus v2 â€” screen {SCREEN_W}x{SCREEN_H} â€” http://127.0.0.1:7331")
    uvicorn.run(app, host="127.0.0.1", port=7331, log_level="warning")