"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const token = new URLSearchParams(location.search).get("token") || "";
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  const STORAGE_KEY = "phoneMouseSettings.v2";
  const DEFAULTS = {
    mode: "air",
    axes: "auto",
    modes: {
      air: { sensitivity: 1, invertX: false, invertY: false, style: "mouse", activation: "hold", pointerAccel: true },
      desk: { sensitivity: 1, invertX: false, invertY: false },
      touch: { sensitivity: 1, invertX: false, invertY: false, buttons: "bottom", wheelSide: "right", tapToClick: true },
    },
  };

  function loadSettings() {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const s = JSON.parse(JSON.stringify(DEFAULTS));
    if (["air", "desk", "touch"].includes(saved.mode)) s.mode = saved.mode;
    if (["auto", "w3c", "ios"].includes(saved.axes)) s.axes = saved.axes;
    for (const m of Object.keys(s.modes)) Object.assign(s.modes[m], (saved.modes || {})[m]);
    return s;
  }

  const settings = loadSettings();
  const cur = () => settings.modes[settings.mode];

  let ws = null;
  let reconnectDelay = 500;
  let padHeld = false;
  let buttonsHeld = 0;
  let lastMotionTs = null;
  let readoutAt = 0;
  let axesStatus = { axes: isIOS ? "ios" : "w3c", source: "assumed" };

  // ---------------------------------------------------------------- socket

  function setStatus(state, text) {
    $("status").className = "status " + state;
    $("status-text").textContent = text;
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function sendSettings() {
    send({
      type: "settings",
      mode: settings.mode,
      axes: settings.axes,
      platform: isIOS ? "ios" : "other",
      modes: settings.modes,
    });
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    setStatus("wait", "Connecting…");

    ws.onopen = () => {
      reconnectDelay = 500;
      setStatus("ok", "Connected");
      sendSettings();
    };
    ws.onclose = () => {
      setStatus("", token ? "Disconnected – retrying" : "Missing pairing token in URL");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 5000);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "pong") {
        setStatus("ok", `Connected · ${Math.round(performance.now() - msg.t)} ms`);
      } else if (msg.type === "status" && msg.axes) {
        axesStatus = { axes: msg.axes, source: msg.axesSource };
      }
    };
  }

  setInterval(() => send({ type: "ping", t: performance.now() }), 2000);

  // ---------------------------------------------------------------- motion

  const f1 = (v) => (v == null ? "    –" : v.toFixed(1).padStart(6));
  const g1 = (v) => v.toFixed(0).padStart(4);
  const vec = (o, a, b, c) => (o && o[a] != null ? [o[a], o[b], o[c]] : null);

  function renderReadout(rot, acc, grav, dt) {
    const src = { manual: "set manually", detected: "auto-detected", assumed: "guess, move the phone" }[axesStatus.source];
    const name = axesStatus.axes === "ios" ? "iOS" : "Standard";
    let gyro = "unavailable";
    if (rot) {
      const [a, b, g] = rot;
      const [x, y, z] = axesStatus.axes === "ios" ? [a, b, g] : [b, g, a];
      gyro = `tilt ${g1(x)}  roll ${g1(y)}  turn ${g1(z)}`;
    }
    $("readout").textContent =
      `axes    ${name} (${src})\n` +
      `gyro°/s ${gyro}\n` +
      `accel   ${acc ? acc.map(f1).join(" ") : "unavailable"}  m/s²\n` +
      `grav    ${grav ? grav.map(f1).join(" ") : "unavailable"}  m/s²\n` +
      `rate    ${dt > 0 ? Math.round(1 / dt) : "–"} Hz`;
  }

  function motionActive() {
    if (settings.mode === "desk") return !padHeld;
    if (cur().activation === "always") return true;
    // Holding a mouse button also aims, so you can drag in one motion.
    return padHeld || buttonsHeld > 0;
  }

  function onMotion(e) {
    const ts = e.timeStamp;
    let dt = lastMotionTs == null ? 0.016 : (ts - lastMotionTs) / 1000;
    lastMotionTs = ts;
    // After a gap (app switch, dropped events) this sample's rotation rate
    // says nothing about the missing time, so treat it as a fresh start.
    if (dt > 0.1) dt = 0.016;

    const rot = vec(e.rotationRate, "alpha", "beta", "gamma");
    const acc = vec(e.acceleration, "x", "y", "z");
    const grav = vec(e.accelerationIncludingGravity, "x", "y", "z");

    if (ts - readoutAt > 150) {
      readoutAt = ts;
      renderReadout(rot, acc, grav, dt);
    }

    if (settings.mode === "touch" || !rot) return;
    // Drop samples rather than queue them if the network stalls.
    if (ws && ws.bufferedAmount > 4096) return;

    send({ type: "motion", dt: Math.max(dt, 0), rot, acc, grav, active: motionActive() });
  }

  async function enableMotion() {
    if (typeof DeviceMotionEvent === "undefined") {
      return "Motion sensors unavailable – Touch mode only.";
    }
    if (typeof DeviceMotionEvent.requestPermission === "function") {
      try {
        const res = await DeviceMotionEvent.requestPermission();
        if (res !== "granted") return "Motion permission denied – Touch mode only.";
      } catch (err) {
        return "Motion needs HTTPS – Touch mode only.";
      }
    }
    window.addEventListener("devicemotion", onMotion);
    return "";
  }

  // ------------------------------------------------------------- buttons

  function haptic() {
    if (navigator.vibrate) navigator.vibrate(8);
  }

  // Capture can throw if the pointer already ended (very fast taps); the
  // gesture should still register in that case.
  function capture(el, e) {
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  }

  document.querySelectorAll(".mbtn").forEach((el) => {
    const button = el.dataset.button;
    let pointer = null;
    const up = (e) => {
      if (pointer !== e.pointerId) return;
      pointer = null;
      buttonsHeld = Math.max(0, buttonsHeld - 1);
      el.classList.remove("down");
      send({ type: "button", button, action: "up" });
    };
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (pointer !== null) return;
      pointer = e.pointerId;
      buttonsHeld += 1;
      capture(el, e);
      el.classList.add("down");
      haptic();
      send({ type: "button", button, action: "down" });
    });
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  });

  // Scroll wheel strip: a short tap is a middle click, a drag scrolls.
  (() => {
    const el = $("wheel");
    let pointer = null, lastY = 0, startY = 0, startT = 0, moved = false;
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (pointer !== null) return;
      pointer = e.pointerId;
      capture(el, e);
      lastY = startY = e.clientY;
      startT = performance.now();
      moved = false;
      el.classList.add("down");
    });
    el.addEventListener("pointermove", (e) => {
      if (e.pointerId !== pointer) return;
      const dy = e.clientY - lastY;
      lastY = e.clientY;
      if (Math.abs(e.clientY - startY) > 6) moved = true;
      if (moved && dy) send({ type: "scroll", dx: 0, dy });
    });
    const end = (e) => {
      if (e.pointerId !== pointer) return;
      pointer = null;
      el.classList.remove("down");
      if (!moved && performance.now() - startT < 250 && e.type === "pointerup") {
        haptic();
        send({ type: "button", button: "middle", action: "click" });
      }
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  })();

  // ------------------------------------------------------------------ pad

  (() => {
    const el = $("pad");
    const touches = new Map();
    let gestureStart = 0, maxTouches = 0, travel = 0, lastTapEnd = 0;

    const centroid = () => {
      let x = 0, y = 0;
      touches.forEach((p) => { x += p.x; y += p.y; });
      return { x: x / touches.size, y: y / touches.size };
    };

    el.addEventListener("pointerdown", (e) => {
      if (e.target.closest("#reaim")) return;
      e.preventDefault();
      capture(el, e);
      if (touches.size === 0) {
        gestureStart = performance.now();
        maxTouches = 0;
        travel = 0;
      }
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      maxTouches = Math.max(maxTouches, touches.size);
      padHeld = true;
      el.classList.add("down");
    });

    el.addEventListener("pointermove", (e) => {
      if (!touches.has(e.pointerId)) return;
      const before = centroid();
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const after = centroid();
      const dx = after.x - before.x;
      const dy = after.y - before.y;
      travel += Math.abs(dx) + Math.abs(dy);
      if (settings.mode !== "touch") return;
      if (touches.size >= 2) send({ type: "scroll", dx: 0, dy });
      else send({ type: "move", dx, dy });
    });

    const end = (e) => {
      if (!touches.delete(e.pointerId)) return;
      if (touches.size > 0) return;
      padHeld = false;
      el.classList.remove("down");
      const now = performance.now();
      const quick = now - gestureStart < 250 && travel < 10 && e.type === "pointerup";
      if (!quick) return;
      if (settings.mode === "touch" && cur().tapToClick) {
        haptic();
        send({ type: "button", button: maxTouches >= 2 ? "right" : "left", action: "click" });
      } else if (settings.mode === "air" && cur().style === "laser" && now - lastTapEnd < 350) {
        reaim();
      }
      lastTapEnd = now;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  })();

  function reaim() {
    haptic();
    send({ type: "reaim" });
  }
  $("reaim").addEventListener("click", reaim);

  // ------------------------------------------------------------- settings

  const PAD_TEXT = {
    "air-mouse": ["Hold here to aim", "Sweep the phone left/right and tilt it up/down. The cursor moves while your thumb is on this pad (or a button)."],
    "air-laser": ["Laser pointer", "Point at the cursor and tap Re-aim (or double-tap the pad). Then the cursor goes wherever you point while you hold the pad."],
    "desk": ["Slide the phone", "Lay the phone flat and slide it like a mouse in short strokes. Hold this pad to \"lift\" and reposition."],
    "touch": ["Trackpad", "Drag to move. Tap to click, two-finger tap to right-click, two-finger drag to scroll."],
  };
  const STYLE_NOTES = {
    mouse: "Cursor speed follows how fast you sweep the phone, like a gyro air mouse.",
    laser: "The cursor goes where the phone points. Re-aim whenever it drifts off.",
  };

  function saveSettings() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    sendSettings();
  }

  function render() {
    const m = settings.mode;
    const ms = cur();
    const app = $("app");
    app.className = `mode-${m}`;
    if (m === "touch") {
      app.classList.toggle("buttons-top", ms.buttons === "top");
      app.classList.toggle("wheel-left", ms.wheelSide === "left");
    }
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));

    const key = m === "air" ? `air-${ms.style}` : m;
    $("pad-title").textContent = PAD_TEXT[key][0];
    $("pad-hint").textContent = PAD_TEXT[key][1];
    $("reaim").classList.toggle("hidden", key !== "air-laser");

    $("settings-title").textContent = { air: "Air mode", desk: "Desk mode", touch: "Touch mode" }[m];
    $("sens").value = ms.sensitivity;
    $("sens-out").textContent = Number(ms.sensitivity).toFixed(1);
    $("invx").checked = ms.invertX;
    $("invy").checked = ms.invertY;
    document.querySelectorAll(".only-air").forEach((el) => el.classList.toggle("hidden", m !== "air"));
    document.querySelectorAll(".only-touch").forEach((el) => el.classList.toggle("hidden", m !== "touch"));
    document.querySelectorAll(".only-air-mouse").forEach((el) => el.classList.toggle("hidden", ms.style !== "mouse"));
    if (m === "air") {
      $("accel").checked = ms.pointerAccel;
      $("style-note").textContent = STYLE_NOTES[ms.style];
    }
    if (m === "touch") $("tapclick").checked = ms.tapToClick;

    document.querySelectorAll(".seg").forEach((seg) => {
      const value = seg.dataset.global ? settings[seg.dataset.setting] : ms[seg.dataset.setting];
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.value === value));
    });
  }

  document.querySelectorAll(".mode").forEach((b) => {
    b.addEventListener("click", () => {
      settings.mode = b.dataset.mode;
      render();
      saveSettings();
    });
  });

  document.querySelectorAll(".seg").forEach((seg) => {
    seg.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        const target = seg.dataset.global ? settings : cur();
        target[seg.dataset.setting] = b.dataset.value;
        render();
        saveSettings();
      });
    });
  });

  $("sens").addEventListener("input", (e) => {
    cur().sensitivity = parseFloat(e.target.value);
    $("sens-out").textContent = cur().sensitivity.toFixed(1);
    saveSettings();
  });
  $("invx").addEventListener("change", (e) => { cur().invertX = e.target.checked; saveSettings(); });
  $("invy").addEventListener("change", (e) => { cur().invertY = e.target.checked; saveSettings(); });
  $("accel").addEventListener("change", (e) => { cur().pointerAccel = e.target.checked; saveSettings(); });
  $("tapclick").addEventListener("change", (e) => { cur().tapToClick = e.target.checked; saveSettings(); });

  $("settings-btn").addEventListener("click", () => $("settings").classList.remove("hidden"));
  $("settings-close").addEventListener("click", () => $("settings").classList.add("hidden"));

  // ---------------------------------------------------------------- start

  async function keepAwake() {
    try {
      if (navigator.wakeLock) await navigator.wakeLock.request("screen");
    } catch (err) { /* not fatal */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") keepAwake();
  });

  $("start-btn").addEventListener("click", async () => {
    const warning = await enableMotion();
    if (warning) {
      settings.mode = "touch";
      render();
      $("start-msg").textContent = warning;
      setTimeout(() => $("start").classList.add("hidden"), 1500);
    } else {
      $("start").classList.add("hidden");
    }
    keepAwake();
    connect();
  });

  render();
})();
