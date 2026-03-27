"""
Qwen2.5-VL -- fully local vision backend via Hugging Face Transformers.
Replaces Moondream 2. Runs on GPU (CUDA) with ~2GB VRAM (3B model, 4-bit NF4 quantized).
No API key required.
"""
import io
import base64
import logging
import re
import json

import torch
from PIL import Image

logger = logging.getLogger(__name__)

_model = None
_processor = None

MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"


def _get_model():
    global _model, _processor
    if _model is None:
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

        from transformers import BitsAndBytesConfig
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )
        logger.info(f"Loading {MODEL_ID} in 4-bit (NF4)...")

        _model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID,
            quantization_config=bnb_config,
            device_map="cuda",
        )
        _processor = AutoProcessor.from_pretrained(
            MODEL_ID,
            min_pixels=256 * 28 * 28,
            max_pixels=1024 * 28 * 28,
        )
        mem = torch.cuda.memory_allocated() / 1024**3
        logger.info(f"{MODEL_ID} loaded on CUDA. GPU mem: {mem:.1f} GB")
    return _model, _processor


def _load_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _chat(image: Image.Image, prompt: str, max_tokens: int = 1024) -> str:
    """Run a single-turn chat with Qwen2.5-VL given an image and text prompt."""
    model, processor = _get_model()

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ],
        }
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    from qwen_vl_utils import process_vision_info
    image_inputs, video_inputs = process_vision_info(messages)

    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(model.device)

    with torch.no_grad():
        ids = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)

    generated = ids[:, inputs.input_ids.shape[1]:]
    return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()


def ask(image_b64: str, question: str) -> str:
    """Answer a free-form question about the screenshot."""
    img = _load_image(image_b64)
    return _chat(img, question, max_tokens=512)


def perceive(image_b64: str, scale: float, task: str = "") -> dict:
    """
    Structured element map. Coordinates are scaled back to actual screen pixels.
    """
    img = _load_image(image_b64)

    prompt = (
        "Analyze this UI screenshot. Return ONLY a JSON object, no markdown, no explanation.\n"
        "List every interactive element visible: buttons, tools, icons, menus, tabs, inputs, "
        "color swatches, checkboxes, dropdowns, scrollbars, canvas areas.\n"
        '{\n'
        '  "title": "window title or app name",\n'
        '  "focused": "active element description",\n'
        '  "elements": [\n'
        '    {"type": "button|input|menu|icon|checkbox|dropdown|tab|link|tool|color|area|other", '
        '"label": "short descriptive label", "x": cx, "y": cy, "w": w, "h": h}\n'
        '  ]\n'
        '}\n\n'
        "x, y are the CENTER of the element. w, h are its dimensions. "
        "All values are integers in image pixels.\n"
        "Include every clickable element. Up to 75 elements."
    )
    if task:
        prompt = f"Task context: {task}\n\n" + prompt

    raw = _chat(img, prompt, max_tokens=2048)

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
    img = _load_image(image_b64)
    w, h = img.size
    inv = 1.0 / scale

    prompt = (
        f"Find the UI element: '{target}'\n"
        f"The image is {w}x{h} pixels.\n"
        "Return ONLY the center coordinates as: x,y\n"
        "Example: 350,120\n"
        "If the element is not visible, return: 0,0"
    )

    raw = _chat(img, prompt, max_tokens=32)

    m = re.search(r'(\d+)\s*,\s*(\d+)', raw)
    if not m:
        logger.debug(f"Qwen point: '{target}' not found in response: {raw[:100]}")
        return 0, 0

    px, py = int(m.group(1)), int(m.group(2))
    if px == 0 and py == 0:
        return 0, 0

    x = round(px * inv)
    y = round(py * inv)
    logger.debug(f"Qwen point: '{target}' -> ({x}, {y})")
    return x, y


