const socket = io();

const canvas = document.getElementById("game");
const ctx = canvas.getContext("2d");

const menu = document.getElementById("menu");
const deathScreen = document.getElementById("deathScreen");
const playBtn = document.getElementById("playBtn");
const respawnBtn = document.getElementById("respawnBtn");
const nameInput = document.getElementById("nameInput");

const finalScoreEl = document.getElementById("finalScore");
const respawnTimerEl = document.getElementById("respawnTimer");

const scoreEl = document.getElementById("score");
const swarmSizeEl = document.getElementById("swarmSize");
const formationEl = document.getElementById("formation");
const boostStatusEl = document.getElementById("boostStatus");
const shieldStatusEl = document.getElementById("shieldStatus");
const playerCountEl = document.getElementById("playerCount");
const aiCountEl = document.getElementById("aiCount");
const leaderboardEl = document.getElementById("leaderboard");

const mobileControls = document.getElementById("mobileControls");
const joyBase = document.getElementById("joyBase");
const joyKnob = document.getElementById("joyKnob");
const tightBtn = document.getElementById("tightBtn");
const boostBtn = document.getElementById("boostBtn");

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener("resize", resize);
resize();

const WORLD = { width: 4200, height: 4200 };

const state = {
  connected: false,
  started: false,
  joined: false,
  sid: null,
  now: 0,

  camera: { x: 0, y: 0 },
  mouse: { x: canvas.width / 2, y: canvas.height / 2 },

  // Input signals (client -> server)
  input: { dx: 0, dy: 0, mag: 0, tight: false, boost: false },

  // Touch joystick
  isTouch: false,
  joy: { active: false, id: null, baseX: 0, baseY: 0, knobX: 0, knobY: 0 },

  // Render data from server
  snapshot: null,

  // Simple visual particles for collect feedback
  fx: [],
};

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function lerp(a, b, t) { return a + (b - a) * t; }
function dist(ax, ay, bx, by) { return Math.hypot(ax - bx, ay - by); }

function isLikelyTouchDevice() {
  return ("ontouchstart" in window) || (navigator.maxTouchPoints || 0) > 0;
}

// ----------------- UI FLOW -----------------
function showMenu() {
  menu.classList.remove("hidden");
  menu.classList.add("visible");
  deathScreen.classList.add("hidden");
  respawnBtn.disabled = true;
  state.joined = false;
}

function hideMenu() {
  menu.classList.add("hidden");
  menu.classList.remove("visible");
}

function showDeath(finalScore, respawnIn, ready) {
  finalScoreEl.textContent = String(finalScore || 0);
  respawnTimerEl.textContent = String(Math.ceil(respawnIn || 0));
  deathScreen.classList.remove("hidden");
  respawnBtn.disabled = !ready;
}

function hideDeath() {
  deathScreen.classList.add("hidden");
}

// ----------------- INPUT (DESKTOP) -----------------
window.addEventListener("mousemove", (e) => {
  state.mouse.x = e.clientX;
  state.mouse.y = e.clientY;
});

window.addEventListener("contextmenu", (e) => e.preventDefault());

window.addEventListener("mousedown", (e) => {
  if (e.button === 2) state.input.boost = true;
});

window.addEventListener("mouseup", (e) => {
  if (e.button === 2) state.input.boost = false;
});

window.addEventListener("keydown", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    state.input.tight = true;
  }
  if (e.code === "ShiftLeft" || e.code === "ShiftRight") {
    state.input.boost = true;
  }
  if (e.code === "KeyR" && deathScreen && !deathScreen.classList.contains("hidden")) {
    // If dead and ready, respawn
    socket.emit("respawn");
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space") state.input.tight = false;
  if (e.code === "ShiftLeft" || e.code === "ShiftRight") state.input.boost = false;
});

// ----------------- INPUT (MOBILE) -----------------
function enableMobileControls() {
  state.isTouch = true;
  mobileControls.classList.remove("hidden");
  mobileControls.setAttribute("aria-hidden", "false");

  // Buttons
  tightBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); state.input.tight = true; });
  tightBtn.addEventListener("pointerup", () => { state.input.tight = false; });
  tightBtn.addEventListener("pointercancel", () => { state.input.tight = false; });

  boostBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); state.input.boost = true; });
  boostBtn.addEventListener("pointerup", () => { state.input.boost = false; });
  boostBtn.addEventListener("pointercancel", () => { state.input.boost = false; });

  // Joystick (left)
  joyBase.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    joyBase.setPointerCapture(e.pointerId);
    state.joy.active = true;
    state.joy.id = e.pointerId;

    const rect = joyBase.getBoundingClientRect();
    state.joy.baseX = rect.left + rect.width / 2;
    state.joy.baseY = rect.top + rect.height / 2;

    state.joy.knobX = state.joy.baseX;
    state.joy.knobY = state.joy.baseY;
    updateJoystick(e.clientX, e.clientY);
  });

  joyBase.addEventListener("pointermove", (e) => {
    if (!state.joy.active || e.pointerId !== state.joy.id) return;
    e.preventDefault();
    updateJoystick(e.clientX, e.clientY);
  });

  joyBase.addEventListener("pointerup", (e) => {
    if (e.pointerId !== state.joy.id) return;
    e.preventDefault();
    resetJoystick();
  });

  joyBase.addEventListener("pointercancel", (e) => {
    if (e.pointerId !== state.joy.id) return;
    e.preventDefault();
    resetJoystick();
  });
}

function updateJoystick(px, py) {
  const maxR = 52;
  const dx = px - state.joy.baseX;
  const dy = py - state.joy.baseY;
  const m = Math.hypot(dx, dy) || 1;

  const ndx = dx / m;
  const ndy = dy / m;
  const mag = clamp(m / maxR, 0, 1);

  const kx = state.joy.baseX + ndx * maxR * mag;
  const ky = state.joy.baseY + ndy * maxR * mag;

  state.joy.knobX = kx;
  state.joy.knobY = ky;

  // Update knob UI
  const rect = joyBase.getBoundingClientRect();
  const localX = kx - rect.left;
  const localY = ky - rect.top;
  joyKnob.style.left = `${(localX / rect.width) * 100}%`;
  joyKnob.style.top = `${(localY / rect.height) * 100}%`;
  joyKnob.style.transform = "translate(-50%, -50%)";

  // Input vector (normalized)
  state.input.dx = ndx;
  state.input.dy = ndy;
  state.input.mag = mag;
}

function resetJoystick() {
  state.joy.active = false;
  state.joy.id = null;

  joyKnob.style.left = "50%";
  joyKnob.style.top = "50%";
  joyKnob.style.transform = "translate(-50%, -50%)";

  state.input.dx = 0;
  state.input.dy = 0;
  state.input.mag = 0;
}

// ----------------- JOIN / RESPAWN -----------------
playBtn.addEventListener("click", () => {
  const name = (nameInput.value || "").trim().slice(0, 16) || `Player${Math.floor(100 + Math.random() * 900)}`;
  socket.emit("join", { name });
  hideMenu();
});

nameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") playBtn.click();
});

respawnBtn.addEventListener("click", () => {
  socket.emit("respawn");
});

// ----------------- SOCKET EVENTS -----------------
socket.on("connected", (msg) => {
  state.connected = true;
  state.sid = msg.sid;
  if (msg.world) {
    WORLD.width = msg.world.width;
    WORLD.height = msg.world.height;
  }
  // Touch UI
  if (isLikelyTouchDevice()) {
    enableMobileControls();
  }
  showMenu();
});

socket.on("joined", () => {
  state.joined = true;
  hideMenu();
});

socket.on("respawned", () => {
  hideDeath();
});

socket.on("state", (snap) => {
  state.snapshot = snap;
  state.started = !!snap.started;
  state.now = snap.now || 0;

  // Handle menu: if not started (server says not joined)
  if (!snap.started) {
    if (menu.classList.contains("hidden")) showMenu();
    return;
  }

  const me = snap.player;
  if (!me) return;

  // death / respawn flow
  if (me.dead) {
    showDeath(me.score, me.respawn_in, me.respawn_ready);
    respawnTimerEl.textContent = String(Math.ceil(me.respawn_in));
    respawnBtn.disabled = !me.respawn_ready;
  } else {
    hideDeath();
  }

  updateHUD(snap);
});

// ----------------- INPUT SEND LOOP -----------------
function computeDesktopInput() {
  // If mobile joystick is active or touch device, we use joystick inputs directly.
  if (state.isTouch) return;

  const me = state.snapshot?.player;
  if (!me || me.dead) {
    state.input.dx = 0;
    state.input.dy = 0;
    state.input.mag = 0;
    return;
  }

  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const dx = state.mouse.x - cx;
  const dy = state.mouse.y - cy;

  const m = Math.hypot(dx, dy) || 1;
  const ndx = dx / m;
  const ndy = dy / m;

  const capped = Math.min(m, 240);
  const mag = capped / 240;

  state.input.dx = ndx;
  state.input.dy = ndy;
  state.input.mag = mag;
}

setInterval(() => {
  if (!state.snapshot || !state.snapshot.started) return;
  computeDesktopInput();
  socket.emit("input", state.input);
}, 1000 / 30);

// ----------------- RENDERING -----------------
function updateHUD(snap) {
  const me = snap.player;
  if (!me) return;

  scoreEl.textContent = String(me.score);
  swarmSizeEl.textContent = String(Math.floor(me.mass));
  formationEl.textContent = me.tight ? "Tight" : "Wide";

  // Boost status
  if (me.boost_cd > 0) boostStatusEl.textContent = `${me.boost_cd.toFixed(1)}s`;
  else if (me.boost_timer > 0) boostStatusEl.textContent = "Boosting";
  else boostStatusEl.textContent = "Ready";

  // Spawn shield
  shieldStatusEl.textContent = me.protect > 0 ? `${me.protect.toFixed(1)}s` : "0.0s";

  playerCountEl.textContent = String(snap.counts?.players ?? 0);
  aiCountEl.textContent = String(snap.counts?.ai ?? 0);

  // Leaderboard
  leaderboardEl.innerHTML = "";
  (snap.leaderboard || []).forEach((s) => {
    const li = document.createElement("li");
    li.textContent = `${s.name} — ${s.score}`;
    li.style.color = s.color;
    leaderboardEl.appendChild(li);
  });
}

function drawBackground() {
  const bg = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
  bg.addColorStop(0, "#050815");
  bg.addColorStop(1, "#0b1030");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // subtle vignette
  const v = ctx.createRadialGradient(canvas.width/2, canvas.height/2, Math.min(canvas.width, canvas.height)*0.15,
                                    canvas.width/2, canvas.height/2, Math.min(canvas.width, canvas.height)*0.70);
  v.addColorStop(0, "rgba(0,0,0,0)");
  v.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function updateCamera(dt) {
  const me = state.snapshot?.player;
  if (!me) return;

  const targetX = me.x - canvas.width / 2;
  const targetY = me.y - canvas.height / 2;

  // smoother camera
  state.camera.x = lerp(state.camera.x, targetX, 0.10);
  state.camera.y = lerp(state.camera.y, targetY, 0.10);

  state.camera.x = clamp(state.camera.x, 0, WORLD.width - canvas.width);
  state.camera.y = clamp(state.camera.y, 0, WORLD.height - canvas.height);
}

function drawWorldGrid() {
  ctx.save();
  ctx.translate(-state.camera.x, -state.camera.y);

  ctx.strokeStyle = "rgba(110, 140, 255, 0.06)";
  ctx.lineWidth = 1;
  const gridSize = 120;

  for (let x = 0; x <= WORLD.width; x += gridSize) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, WORLD.height);
    ctx.stroke();
  }

  for (let y = 0; y <= WORLD.height; y += gridSize) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(WORLD.width, y);
    ctx.stroke();
  }

  // boundary
  ctx.strokeStyle = "rgba(255,255,255,0.10)";
  ctx.lineWidth = 10;
  ctx.strokeRect(0, 0, WORLD.width, WORLD.height);

  ctx.restore();
}

function drawFood() {
  const snap = state.snapshot;
  if (!snap) return;

  ctx.save();
  ctx.translate(-state.camera.x, -state.camera.y);

  for (const f of snap.food || []) {
    ctx.beginPath();
    ctx.shadowBlur = 12;
    ctx.shadowColor = f.color;
    ctx.fillStyle = f.color;
    ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

function drawSwarm(sw, timeMs) {
  const cx = sw.x - state.camera.x;
  const cy = sw.y - state.camera.y;

  // organic ring
  const count = Math.max(8, Math.floor(sw.mass));
  const baseR = 18 + count * 1.3;
  const spread = sw.tight ? 0.72 : 1.18;

  for (let i = 0; i < count; i++) {
    const t = timeMs * 0.002 + i * 0.21;
    const ang = (Math.PI * 2 * i) / count + Math.sin(t * 0.15) * 0.25;
    const wiggle = Math.sin(t * 2.1) * 4 + Math.cos(t * 1.4) * 2;
    const r = baseR * spread + wiggle;

    const mx = cx + Math.cos(ang) * r;
    const my = cy + Math.sin(ang) * r;

    ctx.beginPath();
    ctx.shadowBlur = 16;
    ctx.shadowColor = sw.color;
    ctx.fillStyle = sw.color;
    ctx.arc(mx, my, 4.6, 0, Math.PI * 2);
    ctx.fill();
  }

  // core
  const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, 20);
  coreGrad.addColorStop(0, "#ffffff");
  coreGrad.addColorStop(0.35, sw.color);
  coreGrad.addColorStop(1, "rgba(255,255,255,0)");

  ctx.beginPath();
  ctx.shadowBlur = 26;
  ctx.shadowColor = sw.color;
  ctx.fillStyle = coreGrad;
  ctx.arc(cx, cy, 16, 0, Math.PI * 2);
  ctx.fill();

  ctx.beginPath();
  ctx.shadowBlur = 12;
  ctx.fillStyle = "#ffffff";
  ctx.arc(cx, cy, 6.5, 0, Math.PI * 2);
  ctx.fill();

  // spawn shield ring
  if (sw.protect && sw.protect > 0) {
    ctx.beginPath();
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 2;
    ctx.shadowBlur = 18;
    ctx.shadowColor = "rgba(180,240,255,0.85)";
    ctx.arc(cx, cy, 24, 0, Math.PI * 2);
    ctx.stroke();
  }

  // label
  ctx.save();
  ctx.font = "bold 13px Arial";
  ctx.textAlign = "center";
  ctx.shadowBlur = 0;
  ctx.fillStyle = "rgba(230,240,255,0.92)";
  ctx.fillText(sw.name, cx, cy - (baseR * spread) - 18);
  ctx.restore();
}

function drawMinimap() {
  const snap = state.snapshot;
  if (!snap || !snap.started) return;

  const pad = 14;
  const w = 180;
  const h = 130;

  const x0 = pad;
  const y0 = pad;

  // frame
  ctx.save();
  ctx.globalAlpha = 0.95;
  ctx.fillStyle = "rgba(8, 12, 28, 0.62)";
  ctx.strokeStyle = "rgba(145, 190, 255, 0.18)";
  ctx.lineWidth = 1;
  roundRect(ctx, x0, y0, w, h, 14);
  ctx.fill();
  ctx.stroke();

  // content rect
  const inset = 10;
  const rx = x0 + inset;
  const ry = y0 + inset;
  const rw = w - inset * 2;
  const rh = h - inset * 2;

  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.strokeRect(rx, ry, rw, rh);

  function mapX(wx) { return rx + (wx / WORLD.width) * rw; }
  function mapY(wy) { return ry + (wy / WORLD.height) * rh; }

  // camera viewport
  const vx = mapX(state.camera.x);
  const vy = mapY(state.camera.y);
  const vw = (canvas.width / WORLD.width) * rw;
  const vh = (canvas.height / WORLD.height) * rh;

  ctx.strokeStyle = "rgba(70, 215, 255, 0.50)";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(vx, vy, vw, vh);

  // swarms
  for (const sw of snap.swarms || []) {
    const px = mapX(sw.x);
    const py = mapY(sw.y);

    ctx.beginPath();
    ctx.fillStyle = sw.color;
    ctx.shadowBlur = 8;
    ctx.shadowColor = sw.color;
    ctx.arc(px, py, sw.is_ai ? 2.5 : 3.2, 0, Math.PI * 2);
    ctx.fill();
  }

  // me highlight
  if (snap.player && !snap.player.dead) {
    const px = mapX(snap.player.x);
    const py = mapY(snap.player.y);
    ctx.beginPath();
    ctx.fillStyle = "#ffffff";
    ctx.shadowBlur = 10;
    ctx.shadowColor = "rgba(255,255,255,0.8)";
    ctx.arc(px, py, 2.4, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function render(timeMs) {
  drawBackground();
  if (!state.snapshot) {
    requestAnimationFrame(render);
    return;
  }

  // If not started (menu), we still render a nice background.
  if (!state.snapshot.started) {
    requestAnimationFrame(render);
    return;
  }

  // Smooth camera based on server player pos
  updateCamera(1 / 60);

  drawWorldGrid();
  drawFood();

  // draw swarms by mass so big ones appear on top
  const swarms = [...(state.snapshot.swarms || [])].sort((a, b) => a.mass - b.mass);
  for (const sw of swarms) drawSwarm(sw, timeMs);

  drawMinimap();

  requestAnimationFrame(render);
}

requestAnimationFrame(render);
