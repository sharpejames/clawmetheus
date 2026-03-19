"""
Vision module — Gemini Flash as the perception layer for Clawmetheus.
Set GEMINI_API_KEY env var before starting the server.
"""
import os
import re
import json
import time
import httpx

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

_PERCEIVE_PROMPT = """\
Analyze this UI screenshot. Return ONLY a JSON object, no markdown, no explanation.
List EVERY interactive element visible: buttons, tools, icons, menus, tabs, inputs, \
color swatches, checkboxes, dropdowns, scrollbars, canvas areas — everything a user can click or interact with.

{
  "title": "window title or app name",
  "focused": "active element description",
  "elements": [
    {"type": "button|input|menu|icon|checkbox|dropdown|tab|link|tool|color|area|other", "label": "short descriptive label", "x": cx, "y": cy, "w": w, "h": h}
  ]
}

x, y are the CENTER of the element. w, h are its dimensions. All values are integers in image pixels.
Be thorough — include every clickable element. Up to 75 elements."""


def _call_gemini(image_b64: str, prompt: str, max_tokens: int = 2048, json_mode: bool = False) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    gen_config: dict = {"temperature": 0.1, "maxOutputTokens": max_tokens}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
            ]
        }],
        "generationConfig": gen_config,
    }
    for attempt in range(3):
        resp = httpx.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=30.0,
        )
        if resp.status_code == 429:
            wait = 10 * (2 ** attempt)  # 10s, 20s, 40s
            print(f"[vision] Gemini 429 rate limit — waiting {wait}s (attempt {attempt+1}/3)", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    resp.raise_for_status()  # raise the last 429


def perceive(image_b64: str, scale: float, task: str = "") -> dict:
    """
    Send screenshot to Gemini Flash, get structured element map.
    Coordinates are scaled back to actual screen pixels.
    """
    prompt = _PERCEIVE_PROMPT
    if task:
        prompt = f"Task context: {task}\n\n" + prompt

    raw = _call_gemini(image_b64, prompt, max_tokens=8192, json_mode=False)

    # Extract the JSON object, strip trailing commas (common LLM quirk)
    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in response: {raw[:300]}")
    json_str = re.sub(r',\s*([}\]])', r'\1', raw[start:end])

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e} | Raw: {json_str[:400]}")

    # Scale image coords → actual screen coords
    inv = 1.0 / scale
    for el in result.get("elements", []):
        el["x"] = round(el.get("x", 0) * inv)
        el["y"] = round(el.get("y", 0) * inv)
        el["w"] = round(el.get("w", 0) * inv)
        el["h"] = round(el.get("h", 0) * inv)

    return result


def ask(image_b64: str, question: str) -> str:
    """Ask a free-form question about the current screen."""
    return _call_gemini(image_b64, question, max_tokens=512)
