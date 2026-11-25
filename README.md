# FollowEye

FollowEye is a Windows desktop assistant that follows where you look and offers help before you ask for it. It can propose values for the form you are filling in, surface a file that relates to what you are reading, or explain what is on screen. It does this without a prompt. A small frosted-glass card appears in a fixed corner of the screen only when there is something worth showing.

## How it works

| # | Layer | Technology | Responsibility |
|---|-------|------------|----------------|
| 1 | Gaze tracking | MediaPipe Face Mesh, OpenCV, 9-point calibration | Determines which window the user is looking at (30 fps). |
| 2 | Screen capture | MSS, Windows UI Automation, Windows OCR | Captures the gazed window: pixels, visible text and form fields. |
| 3 | Local model | Ollama, Foundry Local, ONNX Runtime GenAI, any OpenAI-compatible server | First-stage analysis: classifies the context and drafts a suggestion. |
| 4 | Decision layer | Rules plus a learned classifier | Decides whether the local result is enough or the cloud model is needed. |
| 5 | Cloud model | Claude (Anthropic API), OpenAI API, or a plugin | Handles cases the local model cannot answer reliably. |
| 6 | Desktop assistant | PySide6 | Frosted-glass card, tray menu, global hotkeys and settings. |

Without a calibration, or when the webcam is unavailable, FollowEye follows the active window instead of the gaze.

### Checking the eye tracking

Two tools in the tray menu help verify that tracking works:

- **Camera preview** shows the mirrored webcam image with the tracked eye corners, lids and irises marked, and states whether a face and open eyes are detected. Use it to check lighting and seating position, also before calibrating.
- **Show gaze dot** displays a small dot where FollowEye estimates the gaze to be. It turns grey while the eyes are not tracked (blinking, looking away). The dot ignores clicks and is excluded from FollowEye's own screenshots. It requires a calibration.

Both keep the webcam running while they are open, even when observation is paused.

**Camera number:** `0` is the first camera Windows reports, which is the built-in webcam on most laptops. If the preview reports that the camera cannot be opened, close other applications that use the camera, allow desktop apps to use it under *Windows Settings → Privacy & security → Camera*, or try `1` for an external webcam.

The face model (`followeye/assets/face_landmarker.task`) is MediaPipe's Face Landmarker, distributed under the Apache License 2.0.

### When FollowEye looks at the screen

The webcam is sampled continuously, but the screen is only captured and analysed when one of these triggers fires in a watched window:

| Trigger | Default |
|---------|---------|
| Window switch: the gaze stays on a new window | 0.5 s |
| Typing starts | immediate |
| Typing stops | after 2 s without a key press |
| Scrolling | once scrolling settles |
| Idle dwell: looking without interacting | 5 s |
| Clipboard change | immediate |

Redundant analysis is avoided per window: a capture is skipped when fewer than 5 % of the pixels changed. The model is skipped when the extracted text is identical. Either check expires after 30 s.

### Modes

A mode defines which programs are watched and how FollowEye reacts. Full, Work, Focus and Off are provided as editable presets, and any number of custom modes can be created under **Settings → Modes**. The mode editor contains:

- **Programs:** a searchable checklist of installed and currently running programs, an option to watch all programs, and an option to watch only the foreground window.
- **Websites and titles:** rules that watch or ignore browser tabs by domain, or windows by title (`*` acts as a wildcard).
- **Behaviour:** which triggers fire, their thresholds, and whether analysis may use the local model only, the local model with cloud escalation, or the cloud model first.
- **Per-program overrides:** any ticked program can have its own behaviour, which replaces the mode defaults for that program.

Modes are switched from the tray menu.

### Hotkeys

All hotkeys can be rebound under **Settings → Notifications and hotkeys**. FollowEye rejects combinations that Windows reserves, that another program already holds, or that a different FollowEye action already uses.

| Action | Default |
|--------|---------|
| Pause or resume observation | `Ctrl+Shift+F` |
| Accept the suggestion (only while shown) | `Ctrl+Shift+Enter` |
| Dismiss the suggestion (only while shown) | `Ctrl+Shift+Backspace` |

## Privacy

- **Blocklist:** a global blocklist of programs and websites is never captured in any mode. Password managers are included by default.
- **Password fields:** these are never read or filled. Clipboard content that password managers mark as private is ignored.
- **Keyboard:** the keyboard hook records only the time of a key press, never the key.
- **Cloud data:** screenshots and the personal profile used for form filling are sent to cloud models only when explicitly allowed under **Settings → Models**. Cloud usage is capped per hour.
- **History:** the history log, profile and learning data are encrypted with a key held in Windows Credential Manager. The history can be reviewed and deleted under **Settings → History**. Entries expire after a configurable retention period.
- **Own windows:** FollowEye's own card is excluded from screen captures.
- **Untrusted text:** screen text is passed to models as untrusted data. Every action, such as filling a field or opening a file, requires the user to accept it.

## Choosing models

Local and cloud models are selected independently under **Settings → Models**. Use **Test connection** to verify a model before saving.

| Provider | Kind | Notes |
|----------|------|-------|
| Ollama | local | Install Ollama and pull a model, for example `ollama pull llama3.2`. |
| Foundry Local | local | Install with `winget install Microsoft.FoundryLocal` and `pip install followeye[foundry]`. |
| ONNX Runtime GenAI | local | Requires `pip install followeye[onnx]` and a GenAI model folder. Text only. |
| Claude (Anthropic API) | cloud | Default model `claude-opus-5-5`. Supports screenshots. |
| OpenAI API | cloud | Enter the model name. |
| Custom (OpenAI-compatible) | either | LM Studio, vLLM, llama.cpp or a self-hosted model. |

API keys are stored in Windows Credential Manager, never in the settings file.

**Own models as plugins:** place a `.py` file in `%APPDATA%\FollowEye\plugins` that subclasses `followeye.models.base.ModelProvider`. Plugins run with the user's rights, so only add files you trust. A minimal example:

```python
from followeye.models.base import ModelProvider, ProviderField

class MyModel(ModelProvider):
    provider_id = "my_model"
    display_name = "My model"
    fields = (ProviderField("endpoint", "Endpoint", "text", "http://localhost:9000"),)

    def complete_json(self, request):
        # request.system, request.prompt, request.schema, request.image_png
        return {...}  # an object matching request.schema
```

## Running from source

Requires Windows 10 (1803 or later) or Windows 11, Python 3.11 or 3.12, and a webcam for gaze tracking.

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m followeye
```

After the first start:

1. Choose models in **Settings → Models**.
2. Fill in **Settings → Profile** if form filling should use personal details.
3. Calibrate eye tracking from the tray icon, on the main display.

Settings, logs and encrypted data are stored in `%APPDATA%\FollowEye`.

## Building the executable

```bash
pyinstaller --noconfirm followeye.spec
```

The result is written to `dist\FollowEye\FollowEye.exe`. The GitHub Actions workflow runs the tests on Windows and publishes the zipped build as an artifact on every push to `main`.

## Tests

```bash
python -m pytest
```

The core logic and the settings UI are tested on any platform. Win32 integration tests (hotkeys, OCR, window lookups) run only on Windows.
