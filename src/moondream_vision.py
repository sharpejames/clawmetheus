"""
Moondream cloud vision backend — fast, no rate limit issues.
API key loaded from MOONDREAM_API_KEY env var (set in .env via start.ps1).
"""
import io
import os
import base64
import logging
from PIL import Image

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        import moondream as md
        api_key = os.environ.get("MOONDREAM_API_KEY", "")
        if not api_key:
            raise RuntimeError("MOONDREAM_API_KEY not set — check .env and use start.ps1")
        _model = md.vl(api_key=api_key)
        logger.info("Moondream cloud model ready.")
    return _model


def _load_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def ask(image_b64: str, question: str) -> str:
    """Answer a free-form question about the screenshot."""
    model = _get_model()
    img = _load_image(image_b64)
    result = model.query(img, question)
    return result.get("answer", "")


def perceive(image_b64: str, scale: float, task: str = "") -> dict:
    """
    Structured element map using Moondream query(). Replaces Gemini /perceive.
    Coordinates are scaled back to actual screen pixels.
    """
    import re, json
    model = _get_model()
    img = _load_image(image_b64)

    prompt = """\
Analyze this UI screenshot. Return ONLY a JSON object, no markdown, no explanation.
List every interactive element visible: buttons, tools, icons, menus, tabs, inputs, color swatches, checkboxes, dropdowns, scrollbars, canvas areas.

{
  "title": "window title or app name",
  "focused": "active element description",
  "elements": [
    {"type": "button|input|menu|icon|checkbox|dropdown|tab|link|tool|color|area|other", "label": "short descriptive label", "x": cx, "y": cy, "w": w, "h": h}
  ]
}

x, y are the CENTER of the element. w, h are its dimensions. All values are integers in image pixels.
Include every clickable element. Up to 75 elements."""

    if task:
        prompt = f"Task context: {task}\n\n" + prompt

    result = model.query(img, prompt)
    raw = result.get("answer", "")

    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in response: {raw[:300]}")
    json_str = re.sub(r',\s*([}\]])', r'\1', raw[start:end])

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e} | Raw: {json_str[:400]}")

    inv = 1.0 / scale
    for el in parsed.get("elements", []):
        el["x"] = round(el.get("x", 0) * inv)
        el["y"] = round(el.get("y", 0) * inv)
        el["w"] = round(el.get("w", 0) * inv)
        el["h"] = round(el.get("h", 0) * inv)

    return parsed


def point(image_b64: str, target: str, scale: float = 0.5) -> tuple[int, int]:
    """
    Find a UI element by description. Returns (x, y) in actual screen coords.
    Returns (0, 0) if not found.
    """
    model = _get_model()
    img = _load_image(image_b64)
    w, h = img.size
    inv = 1.0 / scale

    result = model.point(img, target)
    points = result.get("points", [])
    if not points:
        logger.debug(f"Moondream point: '{target}' not found")
        return 0, 0

    x = round(points[0]["x"] * w * inv)
    y = round(points[0]["y"] * h * inv)
    logger.debug(f"Moondream point: '{target}' → ({x}, {y})")
    return x, y
