# Clawmetheus

Desktop control server for AI agents. Provides mouse, keyboard, vision, and DOM inspection via a simple HTTP API.

Built for [Helm](https://github.com/sharpejames/helm) but works with any AI agent that can make HTTP requests.

## What It Does

- **Mouse & Keyboard** — click, type, drag, scroll, hotkeys
- **Screenshots** — full screen, window-specific, with optional coordinate grid overlay
- **Vision** — ask questions about the screen via Gemini Flash (e.g. "what app is open?")
- **DOM Inspection** — find web page elements by CSS selector, text, or role (auto-detects CDP or DevTools mode)
- **UI Discovery** — enumerate any app's controls via Windows UIA accessibility APIs
- **Drawing** — smooth line/circle/arc/polygon drawing with cursor verification
- **Window Management** — open, focus, maximize, close apps
- **Save Dialogs** — reliable Save As handling for any app via pywinauto

## Requirements

- **Python 3.11+**
- **Windows 10/11** (macOS partial support)
- **Gemini API key** (for vision features — get one at [aistudio.google.com](https://aistudio.google.com))

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/sharpejames/clawmetheus.git
cd clawmetheus
pip install -r requirements.txt
```

### 2. Set up your Gemini API key

Create a `.env` file:

```
GEMINI_API_KEY=your_gemini_api_key_here
```

### 3. Start the server

```powershell
.\start.ps1
```

Or manually:

```powershell
$env:PYTHONUTF8 = 1
python main.py
```

The server runs at `http://127.0.0.1:7331`.

### 4. Verify it's running

```bash
curl http://127.0.0.1:7331/status
# {"status":"ok","screen":{"width":1920,"height":1080}}
```

## API Reference

### Core

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Health check + screen dimensions |
| `/screenshot/base64?scale=0.5` | GET | Screenshot as base64 JPEG |
| `/screenshot/grid?scale=0.5&spacing=100` | GET | Screenshot with coordinate grid overlay |
| `/screenshot/window?title=Paint` | GET | Capture specific window (works even if not foreground) |
| `/cursor` | GET | Current mouse position |
| `/action` | POST | Execute mouse/keyboard action |
| `/kill` | POST | Emergency stop — release all keys/buttons |

### Vision

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ask?q=...&scale=0.5` | GET | Ask a question about the current screen |
| `/perceive?scale=0.5&task=...` | GET | Screenshot → Gemini → structured element map |
| `/point?target=...&scale=0.5` | GET | Find UI element by description → screen coords |

### DOM Inspection

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dom-result` | POST | Receive DOM query results from browser JS |
| `/dom-result/{id}?timeout=10` | GET | Poll for DOM query result |

### Action Types

POST to `/action` with JSON body:

```json
{"type": "click", "x": 500, "y": 300}
{"type": "doubleClick", "x": 500, "y": 300}
{"type": "move", "x": 500, "y": 300}
{"type": "type", "text": "hello world"}
{"type": "typeKeys", "text": "hello", "interval": 0.01}
{"type": "key", "keys": ["ctrl", "s"]}
{"type": "scroll", "x": 500, "y": 300, "direction": "down", "amount": 3}
{"type": "smoothDrag", "points": [{"x":100,"y":100}, {"x":200,"y":200}], "speed": 900}
```

## task_runner.py — Helper Module

For writing automation scripts, import `task_runner.py` instead of calling the API directly:

```python
import sys
sys.path.insert(0, r'/path/to/clawmetheus')
from task_runner import *

# Open an app
open_app("mspaint", wait_title="Paint")
ensure_maximized("Paint")

# Draw something
select_color("red")
use_pencil()
draw_circle(500, 400, 100)

# Save
app_save(r"C:\Users\me\Pictures\drawing.png", "Paint")

# Web interaction
open_browser("https://example.com")
info = web_page_info()
el = web_find("button.submit")
if el:
    click(el['x'], el['y'])

# Verify results
ok, reason = verify_result("a red circle drawing in Paint")
```

### Key Functions

**Input:** `click()`, `type_text()`, `type_text_keys()`, `key()`, `drag()`, `scroll()`

**Window:** `open_app()`, `kill_app()`, `focus_window()`, `ensure_maximized()`, `get_active_window()`

**UI Discovery:** `discover_ui()`, `find_tool()`, `find_element()`, `find_content_area()`

**Web/DOM:** `open_browser()`, `web_find()`, `web_find_all()`, `web_find_text()`, `web_page_info()`, `web_eval()`, `close_devtools()`

**Drawing:** `draw_line()`, `draw_circle()`, `draw_arc()`, `draw_rect()`, `draw_polygon()`

**Paint:** `select_color()`, `use_pencil()`, `get_canvas_bounds()`, `new_canvas()`

**Save:** `app_save()`, `save_via_dialog()`

**Vision:** `ask()`, `screenshot()`, `map_screen()`

**Validation:** `verify_result()`, `validate_image()`

## Architecture

```
Client (Helm, OpenClaw, any HTTP client)
    │
    ▼
Clawmetheus Server (FastAPI, port 7331)
    ├── Mouse/Keyboard (pyautogui + ctypes)
    ├── Vision (Gemini Flash via google-genai)
    ├── UI Discovery (pywinauto UIA)
    ├── DOM Inspection (CDP WebSocket or DevTools Console + fetch relay)
    └── Screenshots (mss + PIL)
```

## Configuration

| Environment Variable | Description | Required |
|---------------------|-------------|----------|
| `GEMINI_API_KEY` | Google Gemini API key for vision | Yes (for vision features) |
| `CLAWMETHEUS_PORT` | Server port (default: 7331) | No |

## License

MIT
