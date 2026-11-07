"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const token = new URLSearchParams(location.search).get("token") || "";

  const MODE_TEXT = {
    air: {
      title: "Hold here to aim",
      hint: "Point the phone at the screen. The cursor follows while your thumb is on this pad.",
    },
    desk: {
      title: "Slide the phone",
      hint: "Lay the phone flat and slide it like a mouse. Hold this pad to \"lift\" and reposition. (Experimental)",
    },
    touch: {
      title: "Trackpad",
      hint: "Drag to move. Tap to click, two-finger tap to right-click, two-finger drag to scroll.",
    },
  };

  const settings = Object.assign(
    { mode: "air", sensitivity: 1.0, invertX: false, invertY: false },
    JSON.parse(localStorage.getItem("phoneMouseSettings") || "{}"),
  );

  let ws = null;
  let reconnectDelay = 500;
  let padHeld = false;
  let lastMotionTs = null;
  let readoutAt = 0;

  // ---------------------------------------------------------------- socket

  function setStatus(state, text) {
    $("status").className = "status " + state;
    $("status-text").textContent = text;
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    setStatus("wait", "Connecting…");

    ws.onopen = () => {
      reconnectDelay = 500;
      setStatus("ok", "Connected");
      send(Object.assign({ type: "settings" }, settings));
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
      }
    };
  }

  setInterval(() => send({ type: "ping", t: performance.now() }), 2000);

  // ---------------------------------------------------------------- motion

  const r1 = (v) => (v == null ? "–" : v.toFixed(1).padStart(6));
  const vec = (o, a, b, c) => (o && o[a] != null ? [o[a], o[b], o[c]] : null);

  function onMotion(e) {
    const ts = e.timeStamp;
    const dt = lastMotionTs == null ? 0.016 : (ts - lastMotionTs) / 1000;
    lastMotionTs = ts;

    const rot = vec(e.rotationRate, "alpha", "beta", "gamma");
    const acc = vec(e.acceleration, "x", "y", "z");
    const grav = vec(e.accelerationIncludingGravity, "x", "y", "z");

    if (ts - readoutAt > 150) {
      readoutAt = ts;
      $("readout").textContent =
        `gyro  °/s  ${rot ? rot.map(r1).join(" ") : "unavailable"}\n` +
        `accel m/s² ${acc ? acc.map(r1).join(" ") : "unavailable"}\n` +
        `grav  m/s² ${grav ? grav.map(r1).join(" ") : "unavailable"}\n` +
        `rate  ${dt > 0 ? Math.round(1 / dt) : "–"} Hz`;
    }

    if (settings.mode === "touch" || !rot) return;
    // Drop samples rather than queue them if the network stalls.
    if (ws && ws.bufferedAmount > 4096) return;

    const active = settings.mode === "air" ? padHeld : !padHeld;
    send({ type: "motion", dt: Math.min(Math.max(dt, 0), 0.1), rot, acc, grav, active });
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
      el.classList.remove("down");
      send({ type: "button", button, action: "up" });
    };
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (pointer !== null) return;
      pointer = e.pointerId;
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
    let gestureStart = 0, maxTouches = 0, travel = 0;

    const centroid = () => {
      let x = 0, y = 0;
      touches.forEach((p) => { x += p.x; y += p.y; });
      return { x: x / touches.size, y: y / touches.size };
    };

    el.addEventListener("pointerdown", (e) => {
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
      if (!touches.has(e.pointerId) || settings.mode !== "touch") return;
      const before = centroid();
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const after = centroid();
      const dx = after.x - before.x;
      const dy = after.y - before.y;
      travel += Math.abs(dx) + Math.abs(dy);
      if (touches.size >= 2) send({ type: "scroll", dx: 0, dy });
      else send({ type: "move", dx, dy });
    });

    const end = (e) => {
      if (!touches.delete(e.pointerId)) return;
      if (touches.size > 0) return;
      padHeld = false;
      el.classList.remove("down");
      const quick = performance.now() - gestureStart < 250 && travel < 10;
      if (settings.mode === "touch" && quick && e.type === "pointerup") {
        haptic();
        send({ type: "button", button: maxTouches >= 2 ? "right" : "left", action: "click" });
      }
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  })();

  // ------------------------------------------------------------- settings

  function saveSettings() {
    localStorage.setItem("phoneMouseSettings", JSON.stringify(settings));
    send(Object.assign({ type: "settings" }, settings));
  }

  function renderMode() {
    document.querySelectorAll(".mode").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === settings.mode);
    });
    $("pad-title").textContent = MODE_TEXT[settings.mode].title;
    $("pad-hint").textContent = MODE_TEXT[settings.mode].hint;
  }

  document.querySelectorAll(".mode").forEach((b) => {
    b.addEventListener("click", () => {
      settings.mode = b.dataset.mode;
      renderMode();
      saveSettings();
    });
  });

  $("sens").value = settings.sensitivity;
  $("sens-out").textContent = Number(settings.sensitivity).toFixed(1);
  $("invx").checked = settings.invertX;
  $("invy").checked = settings.invertY;

  $("sens").addEventListener("input", (e) => {
    settings.sensitivity = parseFloat(e.target.value);
    $("sens-out").textContent = settings.sensitivity.toFixed(1);
    saveSettings();
  });
  $("invx").addEventListener("change", (e) => { settings.invertX = e.target.checked; saveSettings(); });
  $("invy").addEventListener("change", (e) => { settings.invertY = e.target.checked; saveSettings(); });

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
      renderMode();
      $("start-msg").textContent = warning;
      setTimeout(() => $("start").classList.add("hidden"), 1500);
    } else {
      $("start").classList.add("hidden");
    }
    keepAwake();
    connect();
  });

  renderMode();
})();
