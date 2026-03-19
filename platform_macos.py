"""
platform_macos.py — macOS perception backend.
Uses atomacos (Accessibility API) for element finding, pytesseract for OCR.
All imports are optional — graceful degradation if not installed.
"""
import subprocess
from typing import Optional, Tuple


class MacOSBackend:
    def __init__(self):
        self._has_ax = self._check_ax()
        self._ocr = self._check_ocr()

    def _check_ax(self) -> bool:
        try:
            import atomacos  # noqa
            return True
        except ImportError:
            return False

    def _check_ocr(self) -> Optional[str]:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return "tesseract"
        except Exception:
            pass
        return None

    # ── Element finding ───────────────────────────────────────────────────────

    def find_element(self, name: str, role: str = None, app: str = None) -> Optional[dict]:
        if not self._has_ax:
            return None
        try:
            import atomacos
            if app:
                try:
                    ax_app = atomacos.getAppRefByLocalizedName(app)
                    apps = [ax_app] if ax_app else []
                except Exception:
                    apps = []
            else:
                try:
                    ax_app = atomacos.getFrontmostApp()
                    apps = [ax_app] if ax_app else []
                except Exception:
                    apps = []

            for ax_app in apps:
                el = self._search(ax_app, name, role)
                if el:
                    return el
        except Exception:
            pass
        return None

    def _search(self, ax_app, name: str, role: str = None) -> Optional[dict]:
        searches = [
            {"AXTitle": name},
            {"AXValue": name},
            {"AXDescription": name},
        ]
        if role:
            searches = [{**s, "AXRole": role} for s in searches]
        for kwargs in searches:
            try:
                el = ax_app.findFirst(**kwargs)
                if el:
                    return self._to_dict(el, name)
            except Exception:
                pass
        return None

    def _to_dict(self, el, name: str) -> Optional[dict]:
        try:
            frame = el.AXFrame
            x, y = frame.origin.x, frame.origin.y
            w, h = frame.size.width, frame.size.height
            return {
                "name": name,
                "role": getattr(el, "AXRole", "unknown"),
                "cx": int(x + w / 2),
                "cy": int(y + h / 2),
                "rect": {"left": int(x), "top": int(y), "right": int(x + w), "bottom": int(y + h)},
                "text": getattr(el, "AXTitle", "") or getattr(el, "AXValue", "") or name,
                "enabled": getattr(el, "AXEnabled", True),
                "visible": True,
            }
        except Exception:
            return None

    def ask_element(self, question: str) -> Optional[str]:
        q = question.lower()
        try:
            for kw in ["is ", "find ", "where is ", "locate "]:
                if kw in q:
                    idx = q.find(kw) + len(kw)
                    subject = q[idx:].split("?")[0].split(" visible")[0].split(" open")[0].strip()
                    if subject:
                        el = self.find_element(subject)
                        if el:
                            return f"Yes, '{el['text'] or subject}' is visible at ({el['cx']}, {el['cy']})"
                        return f"No, '{subject}' is not visible"
            if any(kw in q for kw in ["focused", "active window", "foreground"]):
                return self.get_active_window()
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
            script = 'tell application "System Events" to get name of first process whose frontmost is true'
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
            return r.stdout.strip()
        except Exception:
            return ""

    def list_windows(self) -> list:
        try:
            script = 'tell application "System Events" to get name of every process whose background only is false'
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
            return [x.strip() for x in r.stdout.split(",") if x.strip()]
        except Exception:
            return []
