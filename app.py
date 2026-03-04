import math
import random
import threading
import time
from typing import Dict, List, Optional

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "glowswarm-secret-key"

# Threading mode keeps setup simple for local dev. For production, switch to eventlet/gevent and a proper server.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

WORLD_W = 4200
WORLD_H = 4200
TICK_RATE = 30

FOOD_MIN = 750
AI_COUNT = 8

RESPAWN_DELAY = 2.5
SPAWN_PROTECT = 1.4  # seconds of invulnerability after spawn/respawn
BOOST_COOLDOWN = 1.0

# Combat tuning (smoother combat = continuous drain based on overlap and ratio)
COMBAT_BASE_DRAIN = 2.2        # base drain per second at full overlap
COMBAT_MIN_RATIO = 1.06        # must be this much larger to drain
COMBAT_KNOCKBACK = 130.0       # px/s impulse to smaller on contact (smooths "sticking")

COLORS = ["#46d7ff", "#8d7dff", "#ff5fc9", "#7dffb2", "#ffc857", "#ff7a7a", "#b98fff", "#66fff1"]
NAMES = ["Nova", "Pulse", "Lux", "Drift", "Echo", "Halo", "Mira", "Spark", "Orbit", "Vivid", "Glint", "Prism", "Blink", "Rune", "Comet", "Aero"]

state_lock = threading.Lock()
game_thread = None


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def rand(a: float, b: float) -> float:
    return random.uniform(a, b)


def unit(dx: float, dy: float):
    m = math.hypot(dx, dy)
    if m <= 1e-9:
        return 0.0, 0.0
    return dx / m, dy / m


class Swarm:
    def __init__(self, sid: Optional[str], name: str, color: str, x: float, y: float, is_ai: bool = False):
        self.sid = sid
        self.name = name
        self.color = color
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0

        self.mass = 12.0
        self.score = 0

        self.base_speed = 195.0 if not is_ai else rand(150, 180)
        self.tight = False
        self.dead = False
        self.is_ai = is_ai

        self.boost_cd = 0.0
        self.boost_timer = 0.0

        # Player input (server-authoritative)
        self.input_x = 0.0
        self.input_y = 0.0
        self.input_mag = 0.0
        self.input_boost = False
        self.input_tight = False

        # Respawn & spawn protection
        self.respawn_at: Optional[float] = None
        self.protect_until: float = 0.0

        # AI steering
        self.ai_dir_x = rand(-1, 1)
        self.ai_dir_y = rand(-1, 1)
        self.ai_timer = rand(0.4, 1.2)

        self.radius = 40.0
        self.member_count = 12
        self.recalc()

    def recalc(self):
        self.member_count = max(8, int(self.mass))
        self.radius = 18 + self.member_count * 1.3

    def gain(self, value: float):
        self.score += int(value * 10)
        self.mass += value * 0.35
        self.recalc()

    def lose(self, value: float, now: float):
        # Protection (respawn shield)
        if now < self.protect_until:
            return

        self.mass -= value
        if self.mass < 3:
            self.mass = 0
            self.dead = True
            if not self.is_ai:
                self.respawn_at = now + RESPAWN_DELAY
        else:
            self.recalc()

    def respawn(self, now: float):
        self.x = rand(200, WORLD_W - 200)
        self.y = rand(200, WORLD_H - 200)
        self.vx = 0.0
        self.vy = 0.0
        self.mass = 12.0
        self.score = 0
        self.dead = False
        self.tight = False
        self.boost_cd = 0.0
        self.boost_timer = 0.0
        self.respawn_at = None
        self.protect_until = now + SPAWN_PROTECT
        self.recalc()


GAME = {
    "players": {},   # sid -> Swarm (only joined players)
    "waiting": set(),  # sids connected but not joined
    "ais": [],
    "food": [],
    "last_update": time.time(),
}


def make_food(x=None, y=None, value=None, ttl=None):
    if x is None:
        x = rand(30, WORLD_W - 30)
    if y is None:
        y = rand(30, WORLD_H - 30)
    if value is None:
        value = rand(0.8, 1.4)
    hue = int(rand(180, 320))
    return {
        "x": x,
        "y": y,
        "vx": rand(-0.2, 0.2),
        "vy": rand(-0.2, 0.2),
        "r": value * 3.2,
        "value": value,
        "ttl": ttl,
        "color": f"hsla({hue}, 100%, 70%, 0.95)",
    }


def ensure_world():
    # AI
    while len(GAME["ais"]) < AI_COUNT:
        ai = Swarm(
            sid=None,
            name=f"{random.choice(NAMES)}{random.randint(1, 99)}",
            color=random.choice(COLORS),
            x=rand(200, WORLD_W - 200),
            y=rand(200, WORLD_H - 200),
            is_ai=True,
        )
        ai.mass = rand(9, 18)
        ai.score = int(ai.mass * 10)
        ai.recalc()
        GAME["ais"].append(ai)

    # Food
    while len(GAME["food"]) < FOOD_MIN:
        GAME["food"].append(make_food())


def update_food(dt: float):
    for f in list(GAME["food"]):
        f["x"] += f["vx"]
        f["y"] += f["vy"]
        f["vx"] *= 0.995
        f["vy"] *= 0.995
        f["x"] = clamp(f["x"], 12, WORLD_W - 12)
        f["y"] = clamp(f["y"], 12, WORLD_H - 12)
        if f["ttl"] is not None:
            f["ttl"] -= dt
            if f["ttl"] <= 0:
                GAME["food"].remove(f)

    while len(GAME["food"]) < FOOD_MIN:
        GAME["food"].append(make_food())


def apply_boost(sw: Swarm, now: float):
    if sw.boost_cd > 0 or sw.mass <= 8:
        return
    sw.boost_timer = 0.25
    sw.boost_cd = BOOST_COOLDOWN

    # shed food behind
    ang = math.atan2(sw.vy if abs(sw.vy) > 1e-6 else 0.0001, sw.vx if abs(sw.vx) > 1e-6 else 0.0001) + math.pi
    for _ in range(5):
        a = ang + rand(-0.35, 0.35)
        fx = sw.x + math.cos(a) * rand(10, 22)
        fy = sw.y + math.sin(a) * rand(10, 22)
        f = make_food(fx, fy, rand(0.7, 1.2), ttl=8)
        f["vx"] = math.cos(a) * rand(0.25, 0.9)
        f["vy"] = math.sin(a) * rand(0.25, 0.9)
        GAME["food"].append(f)

    sw.lose(0.8, now)


def update_player(sw: Swarm, dt: float, now: float):
    if sw.dead:
        return

    sw.tight = bool(sw.input_tight)

    # speed scaling
    speed = sw.base_speed * (1 - min(sw.mass, 120) * 0.0018)
    speed *= 0.95 if sw.tight else 1.02
    if sw.boost_timer > 0:
        speed *= 1.7

    ux, uy = unit(sw.input_x, sw.input_y)
    throttle = clamp(sw.input_mag, 0.0, 1.0)

    target_vx = ux * speed * throttle
    target_vy = uy * speed * throttle

    # smooth acceleration
    sw.vx = sw.vx + (target_vx - sw.vx) * 0.08
    sw.vy = sw.vy + (target_vy - sw.vy) * 0.08

    sw.x += sw.vx * dt
    sw.y += sw.vy * dt

    sw.x = clamp(sw.x, 20, WORLD_W - 20)
    sw.y = clamp(sw.y, 20, WORLD_H - 20)

    if sw.input_boost:
        apply_boost(sw, now)


def update_ai(sw: Swarm, dt: float, now: float, all_swarms: List[Swarm]):
    if sw.dead:
        return

    sw.tight = False
    sw.ai_timer -= dt

    # Seek: food, avoid threat, chase prey
    nearest_food = None
    nearest_food_d = 1e9
    threat = None
    threat_d = 1e9
    prey = None
    prey_d = 1e9

    for f in GAME["food"]:
        d = dist(sw.x, sw.y, f["x"], f["y"])
        if d < nearest_food_d:
            nearest_food_d = d
            nearest_food = f

    for other in all_swarms:
        if other is sw or other.dead:
            continue
        d = dist(sw.x, sw.y, other.x, other.y)
        if other.mass > sw.mass * 1.22 and d < 320 and d < threat_d:
            threat = other
            threat_d = d
        if sw.mass > other.mass * 1.18 and d < 280 and d < prey_d:
            prey = other
            prey_d = d

    dx, dy = sw.ai_dir_x, sw.ai_dir_y

    if threat is not None:
        dx = sw.x - threat.x
        dy = sw.y - threat.y
        sw.tight = True
        if sw.boost_cd <= 0 and sw.mass > 10 and threat_d < 180:
            apply_boost(sw, now)
    elif prey is not None:
        dx = prey.x - sw.x
        dy = prey.y - sw.y
    elif nearest_food is not None:
        dx = nearest_food["x"] - sw.x
        dy = nearest_food["y"] - sw.y

    if sw.ai_timer <= 0:
        sw.ai_timer = rand(0.7, 1.5)
        if threat is None and prey is None and nearest_food is None:
            sw.ai_dir_x = rand(-1, 1)
            sw.ai_dir_y = rand(-1, 1)

    ux, uy = unit(dx, dy)

    speed = sw.base_speed * (1 - min(sw.mass, 120) * 0.0017)
    if sw.boost_timer > 0:
        speed *= 1.6
    if sw.tight:
        speed *= 0.97

    target_vx = ux * speed
    target_vy = uy * speed

    sw.vx = sw.vx + (target_vx - sw.vx) * 0.04
    sw.vy = sw.vy + (target_vy - sw.vy) * 0.04

    sw.x += sw.vx * dt
    sw.y += sw.vy * dt

    sw.x = clamp(sw.x, 20, WORLD_W - 20)
    sw.y = clamp(sw.y, 20, WORLD_H - 20)


def collect_food(all_swarms: List[Swarm], now: float):
    for sw in all_swarms:
        if sw.dead:
            continue
        collect_radius = sw.radius * (0.85 if sw.tight else 1.28)
        # iterate backwards for safe removal
        for i in range(len(GAME["food"]) - 1, -1, -1):
            f = GAME["food"][i]
            if dist(sw.x, sw.y, f["x"], f["y"]) < collect_radius:
                sw.gain(float(f["value"]))
                GAME["food"].pop(i)


def handle_combat(all_swarms: List[Swarm], dt: float, now: float):
    # Continuous, overlap-scaled drain for smoother combat.
    for i in range(len(all_swarms)):
        a = all_swarms[i]
        if a.dead:
            continue
        for j in range(i + 1, len(all_swarms)):
            b = all_swarms[j]
            if b.dead:
                continue

            d = dist(a.x, a.y, b.x, b.y)
            rng = (a.radius + b.radius) * 0.62  # slightly bigger interaction zone
            if d >= rng:
                continue

            bigger, smaller = (a, b) if a.mass >= b.mass else (b, a)
            # spawn shield prevents immediate griefing on respawn
            if now < smaller.protect_until:
                continue

            ratio = (bigger.mass / max(1e-6, smaller.mass))
            if ratio < COMBAT_MIN_RATIO:
                continue

            overlap = clamp((rng - d) / rng, 0.0, 1.0)
            # scale drain by overlap and ratio (but cap so it doesn't feel instant)
            ratio_scale = clamp((ratio - 1.0) * 1.15, 0.05, 1.25)
            drain = COMBAT_BASE_DRAIN * overlap * ratio_scale * dt

            smaller.lose(drain, now)
            bigger.gain(drain * 0.6)

            # Knockback makes collisions feel less sticky and more readable
            kx, ky = unit(smaller.x - bigger.x, smaller.y - bigger.y)
            smaller.vx += kx * COMBAT_KNOCKBACK * dt
            smaller.vy += ky * COMBAT_KNOCKBACK * dt

            # Scatter a couple of glow particles from the smaller swarm
            burst = 1 + int(overlap * 2)
            for _ in range(burst):
                ang = rand(0, math.pi * 2)
                fx = smaller.x + math.cos(ang) * rand(8, max(8, smaller.radius * 0.65))
                fy = smaller.y + math.sin(ang) * rand(8, max(8, smaller.radius * 0.65))
                f = make_food(fx, fy, rand(0.45, 0.95), ttl=5)
                f["vx"] = math.cos(ang) * rand(0.3, 1.0)
                f["vy"] = math.sin(ang) * rand(0.3, 1.0)
                GAME["food"].append(f)

    # Handle deaths: AI respawns; players wait for respawn input
    for sw in list(GAME["ais"]):
        if not sw.dead:
            continue
        # drop more food
        for _ in range(18):
            ang = rand(0, math.pi * 2)
            fx = sw.x + math.cos(ang) * rand(0, sw.radius)
            fy = sw.y + math.sin(ang) * rand(0, sw.radius)
            f = make_food(fx, fy, rand(0.8, 1.5), ttl=10)
            f["vx"] = math.cos(ang) * rand(0.5, 2.0)
            f["vy"] = math.sin(ang) * rand(0.5, 2.0)
            GAME["food"].append(f)
        # instant AI replacement (keeps action dense)
        GAME["ais"].remove(sw)

    while len(GAME["ais"]) < AI_COUNT:
        ensure_world()


def snapshot_for(sid: str):
    # If not joined yet, only give meta snapshot for menu.
    if sid in GAME["waiting"] and sid not in GAME["players"]:
        return {
            "world": {"width": WORLD_W, "height": WORLD_H},
            "started": False,
            "player": None,
            "swarms": [],
            "food": [],
            "counts": {"players": len(GAME["players"]), "ai": len(GAME["ais"])},
            "leaderboard": [],
            "now": time.time(),
        }

    me = GAME["players"].get(sid)
    now = time.time()

    swarms = []
    for sw in list(GAME["players"].values()) + GAME["ais"]:
        if sw.dead:
            continue
        swarms.append({
            "id": sw.sid if sw.sid else sw.name,
            "name": sw.name,
            "color": sw.color,
            "x": sw.x,
            "y": sw.y,
            "vx": sw.vx,
            "vy": sw.vy,
            "mass": sw.mass,
            "score": sw.score,
            "tight": sw.tight,
            "radius": sw.radius,
            "is_ai": sw.is_ai,
            "protect": max(0.0, sw.protect_until - now),
        })

    leaderboard = sorted(swarms, key=lambda s: s["score"], reverse=True)[:7]
    food = [{"x": f["x"], "y": f["y"], "r": f["r"], "color": f["color"]} for f in GAME["food"]]

    player_payload = None
    if me is not None:
        respawn_in = 0.0
        respawn_ready = False
        if me.dead and me.respawn_at is not None:
            respawn_in = max(0.0, me.respawn_at - now)
            respawn_ready = respawn_in <= 0.01
        player_payload = {
            "id": me.sid,
            "name": me.name,
            "color": me.color,
            "x": me.x,
            "y": me.y,
            "vx": me.vx,
            "vy": me.vy,
            "mass": me.mass,
            "score": me.score,
            "tight": me.tight,
            "dead": me.dead,
            "boost_cd": max(0.0, me.boost_cd),
            "boost_timer": max(0.0, me.boost_timer),
            "protect": max(0.0, me.protect_until - now),
            "respawn_in": respawn_in,
            "respawn_ready": respawn_ready,
        }

    return {
        "world": {"width": WORLD_W, "height": WORLD_H},
        "started": True,
        "player": player_payload,
        "swarms": swarms,
        "food": food,
        "counts": {"players": len(GAME["players"]), "ai": len(GAME["ais"])},
        "leaderboard": leaderboard,
        "now": now,
    }


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    global game_thread
    sid = request.sid

    with state_lock:
        ensure_world()
        GAME["waiting"].add(sid)
        if game_thread is None:
            game_thread = socketio.start_background_task(game_loop)

    emit("connected", {"sid": sid, "world": {"width": WORLD_W, "height": WORLD_H}})


@socketio.on("join")
def on_join(data):
    sid = request.sid
    name = (data or {}).get("name", "").strip()[:16]
    if not name:
        name = f"Player{random.randint(100,999)}"
    color = random.choice(COLORS)

    with state_lock:
        ensure_world()
        # create a swarm for this sid if not already present
        if sid not in GAME["players"]:
            sw = Swarm(sid, name, color, rand(200, WORLD_W - 200), rand(200, WORLD_H - 200), is_ai=False)
            sw.protect_until = time.time() + SPAWN_PROTECT
            GAME["players"][sid] = sw
        else:
            GAME["players"][sid].name = name
        if sid in GAME["waiting"]:
            GAME["waiting"].discard(sid)

    emit("joined", {"ok": True})


@socketio.on("input")
def on_input(data):
    sid = request.sid
    with state_lock:
        sw = GAME["players"].get(sid)
        if sw is None:
            return
        if sw.dead:
            # ignore movement while dead
            sw.input_x = 0.0
            sw.input_y = 0.0
            sw.input_mag = 0.0
            sw.input_boost = False
            sw.input_tight = False
            return

        dx = float((data or {}).get("dx", 0.0))
        dy = float((data or {}).get("dy", 0.0))
        mag = float((data or {}).get("mag", 0.0))
        sw.input_x = clamp(dx, -1.0, 1.0)
        sw.input_y = clamp(dy, -1.0, 1.0)
        sw.input_mag = clamp(mag, 0.0, 1.0)
        sw.input_boost = bool((data or {}).get("boost", False))
        sw.input_tight = bool((data or {}).get("tight", False))


@socketio.on("respawn")
def on_respawn():
    sid = request.sid
    with state_lock:
        sw = GAME["players"].get(sid)
        if sw is None:
            return
        now = time.time()
        if sw.dead and sw.respawn_at is not None and now >= sw.respawn_at:
            sw.respawn(now)
            emit("respawned", {"ok": True})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    with state_lock:
        GAME["waiting"].discard(sid)
        if sid in GAME["players"]:
            del GAME["players"][sid]


def game_loop():
    while True:
        time.sleep(1 / TICK_RATE)
        with state_lock:
            now = time.time()
            dt = min(now - GAME["last_update"], 0.05)
            GAME["last_update"] = now

            ensure_world()
            update_food(dt)

            all_swarms = list(GAME["players"].values()) + GAME["ais"]

            # Update players
            for sw in list(GAME["players"].values()):
                if sw.boost_cd > 0:
                    sw.boost_cd -= dt
                if sw.boost_timer > 0:
                    sw.boost_timer -= dt
                update_player(sw, dt, now)

            # Update AIs
            all_swarms = list(GAME["players"].values()) + GAME["ais"]
            for sw in GAME["ais"]:
                if sw.boost_cd > 0:
                    sw.boost_cd -= dt
                if sw.boost_timer > 0:
                    sw.boost_timer -= dt
                update_ai(sw, dt, now, all_swarms)

            all_swarms = list(GAME["players"].values()) + GAME["ais"]
            collect_food(all_swarms, now)
            handle_combat(all_swarms, dt, now)

            # Broadcast per-player snapshots
            for sid in list(GAME["players"].keys()) + list(GAME["waiting"]):
                socketio.emit("state", snapshot_for(sid), to=sid)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
