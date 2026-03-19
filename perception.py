"""
perception.py — Layered perception for Clawmetheus.

Fast-first stack:
  1. UI Automation (Windows UIA / macOS Accessibility API)  — <10ms
  2. Template matching (OpenCV)                             — <20ms
  3. OCR (pytesseract)                                      — 50-200ms
  4. Gemini Flash fallback                                  — 300-800ms

Usage in task scripts:
    from task_runner import find_element, is_visible, wait_for, read_text, perceive
"""

import platform
import time
import io
import base64
from typing import Optional, Tuple, Callable

_SYSTEM = platform.system()


class PerceptionLayer:
    def __init__(self, gemini_fn: Optional[Callable] = None):
        """
        gemini_fn: callable(question: str, image_b64: str) -> str
        """
        self._gemini_fn = gemini_fn
        self._backend = self._load_backend()
        self._cv2 = self._try_import("cv2")

    def _load_backend(self):
        try:
            if _SYSTEM == "Windows":
                from platform_windows import WindowsBackend
                return WindowsBackend()
            elif _SYSTEM == "Darwin":
                from platform_macos import MacOSBackend
                return MacOSBackend()
        except Exception:
            pass
        return None

    def _try_import(self, name):
        try:
            import importlib
            return importlib.import_module(name)
        except ImportError:
            return None

    def _grab_b64(self, region: Tuple = None) -> str:
        try:
            import mss
            from PIL import Image
            with mss.mss() as sct:
                mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]} \
                    if region else sct.monitors[1]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""

    # ── Public API ────────────────────────────────────────────────────────────

    def find_element(self, name: str, role: str = None, app: str = None,
                     timeout: float = 0) -> Optional[dict]:
        """Find UI element by name. Returns dict(cx, cy, rect, text, role, enabled) or None."""
        if not self._backend:
            return None
        deadline = time.time() + max(timeout, 0)
        while True:
            result = self._backend.find_element(name, role=role, app=app)
            if result is not None:
                return result
            if time.time() >= deadline:
                break
            time.sleep(0.1)
        return None

    def is_visible(self, name: str, role: str = None) -> bool:
        return self.find_element(name, role=role) is not None

    def wait_for(self, name: str, timeout: float = 5.0, role: str = None) -> dict:
        """Wait for element to appear. Raises TimeoutError if not found."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            el = self.find_element(name, role=role)
            if el:
                return el
            time.sleep(0.15)
        raise TimeoutError(f"Element '{name}' not found after {timeout}s")

    def read_text(self, region: Tuple = None) -> str:
        """OCR text from screen or region (x, y, w, h)."""
        if self._backend:
            return self._backend.read_text(region=region)
        return ""

    def find_image(self, template_path: str, threshold: float = 0.8) -> Optional[Tuple[int, int]]:
        """Template matching. Returns (cx, cy) center or None."""
        if not self._cv2:
            return None
        try:
            import numpy as np
            import mss
            from PIL import Image

            template = self._cv2.imread(template_path)
            if template is None:
                return None

            with mss.mss() as sct:
                raw = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            screen = self._cv2.cvtColor(np.array(img), self._cv2.COLOR_RGB2BGR)

            res = self._cv2.matchTemplate(screen, template, self._cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = self._cv2.minMaxLoc(res)
            if max_val >= threshold:
                h, w = template.shape[:2]
                return (max_loc[0] + w // 2, max_loc[1] + h // 2)
        except Exception:
            pass
        return None

    def ask(self, question: str, region: Tuple = None) -> str:
        """Layered visual QA. Fast first, Gemini last."""
        q = question.lower()

        # Layer 1: UI Automation for element/visibility questions
        if self._backend and any(kw in q for kw in [
            "visible", "open", "exist", "find", "where", "button", "dialog",
            "window", "focused", "active", "foreground", "enabled", "appear", "show",
        ]):
            answer = self._backend.ask_element(question)
            if answer is not None:
                return answer

        # Layer 2: OCR for text questions
        if any(kw in q for kw in [
            "text", "say", "read", "written", "label", "title",
            "content", "value", "display", "name",
        ]):
            text = self.read_text(region)
            if text and text.strip():
                return text.strip()

        # Layer 3: Gemini fallback
        if self._gemini_fn:
            b64 = self._grab_b64(region)
            if b64:
                try:
                    return self._gemini_fn(question, b64)
                except Exception as e:
                    return f"Gemini error: {e}"

        return "No perception backend available"
