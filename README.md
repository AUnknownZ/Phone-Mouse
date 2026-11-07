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
| **Air** (default) | Hold your thumb on the big pad and point the phone at the screen like a laser pointer. Lift your thumb to freeze the cursor. | Gyroscope |
| **Desk** (experimental) | Lay the phone flat and slide it like a mouse. Hold the pad to "lift" it and reposition. | Accelerometer |
| **Touch** | The pad is a laptop-style trackpad: tap to click, two-finger tap to right-click, two-finger drag to scroll. | Touchscreen |

In every mode:

- **L** and **R** are the mouse buttons. Hold **L** while moving to drag, for
  example to box-select in CAD or move a component in a schematic editor.
- The narrow strip between them is the **scroll wheel**. Drag it up or down to
  scroll, or tap it for a middle-click. Many CAD tools pan with middle-drag.
- The gear icon opens **Settings**: sensitivity, invert axes, and a live
  readout of the raw sensor values, which helps when tuning.

### Why Air mode is the default

Getting *position* from an IMU means integrating acceleration twice, and any
small sensor bias grows quadratically into drift. Within a second the phone
"thinks" it has moved centimetres while it sits still. Desk mode works around
this with zero-velocity updates: when the phone stops, velocity snaps to zero
and the sensor bias is re-estimated. That makes it usable for coarse
pushes, but it's not precise.

The gyroscope measures *rotation rate* directly, which only needs one
integration step to become cursor motion, so it's accurate and doesn't drift.
Air mode projects the rotation onto the world's vertical axis using gravity,
so it works whether you hold the phone flat like a remote or upright like a
camera. A small deadzone removes hand tremor, and an acceleration curve keeps
slow motions precise while fast flicks cross the screen.

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
  motion.py      GyroPointer (air) and SlidePointer (desk) signal processing
  mouse.py       pynput backend + in-memory backend for tests
  certs.py       self-signed certificate for the LAN IP
  static/        the phone web app (HTML/CSS/JS)
tests/           pytest suite
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
