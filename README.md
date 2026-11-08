# Phone-Mouse

An app that turns your phone into a mouse.

You run a small server on your computer, open a web page on your iPhone (or
Android phone), and the phone's gyroscope, accelerometer and touchscreen
drive your computer's cursor. You don't need to install anything on the phone.

```
 iPhone (Safari)                          Computer
 ┌─────────────────────┐   WebSocket     ┌───────────────────────────┐
 │ DeviceMotion (IMU)  │ ──────────────► │ phone_mouse server        │
 │ L / R / wheel / pad │   over Wi-Fi    │  motion filters ─► pynput │──► OS cursor
 └─────────────────────┘   (HTTPS/WSS)   └───────────────────────────┘
```

## Quick start

Requires Python 3.10+ on the computer, with the phone on the same Wi-Fi network.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
python -m phone_mouse
```

The terminal prints a URL such as `https://192.168.1.20:8443/?token=AbC123` and
a QR code. Scan the QR code with the iPhone camera, or type the URL into Safari, then:

1. Safari warns that the certificate isn't trusted, because it's a self-signed
   certificate the server generated for your LAN. Tap **Show Details**, then
   **visit this website**. You only need to do this once per certificate.
2. Tap **Tap to connect**, then **Allow** when iOS asks for motion and
   orientation access.
3. The status dot turns green. You're connected.

**macOS:** the first time, macOS blocks synthetic mouse events. Grant
Accessibility access to your terminal app in **System Settings > Privacy &
Security > Accessibility**, then restart the server.

## Modes

| Mode | How you use it | Sensor |
| --- | --- | --- |
| **Air** (default) | Hold the phone like a TV remote and point it at the screen. Sweep left/right and tilt up/down; the cursor moves while your thumb is on the big pad (or a button). Rolling the phone is ignored. | Gyroscope + gravity |
| **Desk** | Lay the phone flat and slide it like a mouse. Lifting it off the desk pauses the cursor on its own. The area below the buttons is empty, so your hand can rest there. | Accelerometer |
| **Touch** | Laptop-style trackpad with L/R buttons underneath and a scroll strip along the edge. Tap to click, two-finger tap to right-click, two-finger drag to scroll. | Touchscreen |

Air mode has two **styles** (gear icon > Style):

- **Air mouse:** the cursor *speed* follows how fast you sweep the phone,
  like a gyro air mouse. An optional acceleration curve keeps slow motions
  precise while fast flicks cross the screen.
- **Laser pointer:** the cursor goes *where the phone points*. Point at the
  cursor and tap **Re-aim here** (or double-tap the pad) once. After that, aiming
  10° right puts the cursor at the same spot every time, and pointing back
  returns it home. Heading drifts slowly (the gyro has no absolute
  reference), so re-aim when it wanders off. Elevation comes from gravity
  and doesn't drift.

In every mode:

- **L** and **R** are the mouse buttons. Hold **L** while moving to drag, for
  example to box-select in CAD or move a component in a schematic editor.
  In Air mode, holding a button also aims, so a drag is one motion. Motion is
  ignored for a moment after each press so clicks land where you pointed.
- The narrow strip is the **scroll wheel**. Drag it up or down to scroll, or
  tap it for a middle-click. Many CAD tools pan with middle-drag.
- The gear icon opens **Settings for the current mode**: sensitivity and
  invert axes are separate for Air, Desk and Touch. Air has style and
  activation options; Touch lets you put the buttons above or below the pad
  and the scroll strip on the left or right.

### Gyro axes (iPhone vs Android)

Browsers don't agree on which axes the gyroscope's `alpha`/`beta`/`gamma`
values refer to. Chrome on Android follows the web standard, while Safari
(and every other browser on iOS) reports them in a different order. If the
axes are read in the wrong order, tilting moves the cursor sideways and turning does
nothing. The server checks which order correctly predicts how gravity rotates
as you move the phone, and picks the right one automatically. Settings
shows the result ("iOS (auto-detected)"), and you can force **Standard** or
**iOS** there if needed.

### What Desk mode can and can't do

A real mouse has an optical sensor that sees the desk move under it. A phone
only has an accelerometer, so position has to come from integrating
acceleration twice, which drifts within a second. Desk mode makes this
usable by treating each push as one *stroke*:

- Velocity is integrated without any damping, so the cursor travels in
  proportion to the *distance* the phone moved, not to how hard you pushed.
- Each slide is one stroke. When the phone stops, the stroke ends: the
  cursor does not keep the last speed, and a weak leftover acceleration
  cannot speed it up. Braking can never pull the cursor backwards.
- While the phone rests, velocity is held at zero and sensor bias is
  re-learned.
- Lifting pauses the cursor. Safari cannot read a pressure sensor on the
  back of the phone, so lift is the phone tipping (gravity leaving the Z
  axis) or a clear upward acceleration. Setting it flat again, with gravity
  back along Z, resumes tracking even if your hand is still on it. A phone
  carried perfectly flat in the air looks the same as one on the desk.

In simulation with realistic sensor noise, strokes from 1 cm to 15 cm move the
cursor 98–100% of the true distance with no bounce back
(`tests/test_motion.py`). The physical limit: very slow, gentle slides
produce less acceleration than the sensor's noise and can't be told apart
from resting. For precise work such as CAD, use Air mode or Touch.

## About the hardware buttons

The prototype uses on-screen buttons because iOS doesn't let web pages read
the hardware buttons:

- **Volume buttons:** not visible to Safari. A native iOS app *can* detect them
  by watching `AVAudioSession.outputVolume`. This is a known workaround, and it
  is the plan for the native version.
- **Home button / side button:** reserved by iOS; no app can intercept them.

## Command-line options

```
python -m phone_mouse [--port 8443] [--token TOKEN] [--no-https] [--dry-run] [-v]
```

- `--token`: fixed pairing token. By default a random one is generated each
  run, so only someone who has the URL or QR code can control your mouse.
- `--dry-run`: print mouse actions instead of moving the cursor (useful for
  development).
- `--no-https`: plain HTTP. iOS hides motion sensors on insecure pages, so only
  Touch mode works.

The certificate is stored in `~/.phone-mouse/` and regenerated automatically
when your computer's LAN IP changes, which means Safari will ask you to trust it again.

## Project layout

```
phone_mouse/
  server.py      aiohttp HTTPS + WebSocket server, pairing token, QR code
  controller.py  wire protocol -> mouse actions (validation, clamping, buttons)
  motion.py      axis detection, air mouse, laser pointer and desk stroke filters
  mouse.py       pynput backend + in-memory backend for tests
  certs.py       self-signed certificate for the LAN IP
  static/        the phone web app (HTML/CSS/JS)
tests/           pytest suite (sim.py synthesises realistic sensor streams)
```

## Development

```bash
pip install -e '.[test]'
pytest
```

## Roadmap

- Native iOS app (SwiftUI + CoreMotion at 100 Hz) with volume-button clicks
  and haptic feedback.
- Bluetooth transport, for use without Wi-Fi. iOS doesn't let third-party apps
  act as a Bluetooth HID mouse, so the computer still needs a small receiver
  app, much like this server.
- Calibration wizard for Desk mode, and per-app sensitivity profiles for CAD
  tools.
