"""
platform_windows.py — Windows perception backend.
Uses pywinauto (UIA) for element finding, pytesseract for OCR.
All imports are optional — graceful degradation if not installed.
"""
import ctypes
from typing import Optional, Tuple


class WindowsBackend:
    def __init__(self):
        self._has_uia = self._check_uia()
        self._ocr = self._check_ocr()

    def _check_uia(self) -> bool:
        try:
            from pywinauto import Desktop  # noqa
            return True
        except ImportError:
            return False

    def _check_ocr(self) -> Optional[str]:
        try:
            import pytesseract
            import os
            # Common Windows install locations
            candidates = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            for path in candidates:
                if os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break
            pytesseract.get_tesseract_version()
            return "tesseract"
        except Exception:
            pass
        return None

    # ── Element finding ───────────────────────────────────────────────────────

    def find_element(self, name: str, role: str = None, app: str = None) -> Optional[dict]:
        if not self._has_uia:
            return None
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            windows = desktop.windows()

            if app:
                windows = [w for w in windows if app.lower() in w.window_text().lower()]

            exact_match = None
            partial_match = None

            for win in windows:
                try:
                    for el in win.descendants():
                        el_name = el.window_text().strip()
                        if not el_name:
                            continue
                        ctrl = el.element_info.control_type or role or "unknown"
                        if el_name.lower() == name.lower() and exact_match is None:
                            rect = el.rectangle()
                            exact_match = _make_element(el_name, ctrl, rect, el_name, True)
                            break  # stop searching this window
                        elif name.lower() in el_name.lower() and partial_match is None:
                            rect = el.rectangle()
                            partial_match = _make_element(el_name, ctrl, rect, el_name, True)
                except Exception:
                    pass
                if exact_match:
                    break

            return exact_match or partial_match

        except Exception:
            pass
        return None

    def ask_element(self, question: str) -> Optional[str]:
        q = question.lower()
        try:
            # Specific patterns first — before generic "is/find" matching
            if any(kw in q for kw in ["foreground", "active window", "focused", "front"]):
                win = self.get_active_window()
                return win if win else "No active window found"

            for kw in ["is ", "find ", "where is ", "locate "]:
                if kw in q:
                    idx = q.find(kw) + len(kw)
                    subject = q[idx:].split("?")[0].split(" visible")[0].split(" open")[0].strip()
                    if subject and len(subject) > 2:
                        el = self.find_element(subject)
                        if el:
                            return f"Yes, '{el['text'] or subject}' is visible at ({el['cx']}, {el['cy']})"
                        return f"No, '{subject}' is not visible"
        except Exception:
            pass
        return None

    # ── OCR ───────────────────────────────────────────────────────────────────

    def read_text(self, region: Tuple = None) -> str:
        if self._ocr == "tesseract":
            return self._tesseract(region)
        return ""

    def _tesseract(self, region: Tuple = None) -> str:
        try:
            import pytesseract
            import mss
            from PIL import Image
            with mss.mss() as sct:
                mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]} \
                    if region else sct.monitors[1]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return pytesseract.image_to_string(img).strip()
        except Exception:
            return ""

    # ── Utilities ─────────────────────────────────────────────────────────────

    def get_active_window(self) -> str:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def list_windows(self) -> list:
        try:
            from pywinauto import Desktop
            return [w.window_text() for w in Desktop(backend="uia").windows() if w.window_text()]
        except Exception:
            return []


def _make_element(name, role, rect, text, enabled) -> dict:
    return {
        "name": name,
        "role": role,
        "cx": (rect.left + rect.right) // 2,
        "cy": (rect.top + rect.bottom) // 2,
        "rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
        "text": text,
        "enabled": enabled,
        "visible": True,
    }
