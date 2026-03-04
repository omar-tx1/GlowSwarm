import math
import random
import threading
import time
from typing import Optional

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "glowswarm-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

WORLD_W = 4200
WORLD_H = 4200
TICK_RATE = 30
FOOD_MIN = 700
AI_COUNT = 8

COLORS = [
    "#46d7ff",
    "#8d7dff",
    "#ff5fc9",
    "#7dffb2",
    "#ffc857",
    "#ff7a7a",
    "#b98fff",
    "#66fff1",
]

NAMES = [
    "Nova",
    "Pulse",
    "Lux",
    "Drift",
    "Echo",
    "Halo",
    "Mira",
    "Spark",
    "Orbit",
    "Vivid",
    "Glint",
    "Prism",
    "Blink",
    "Rune",
    "Comet",
    "Aero",
]

state_lock = threading.Lock()
game_thread = None


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def rand(a, b):
    return random.uniform(a, b)


class Swarm:
    def __init__(self, sid: Optional[str], name: str, color: str, x: float, y: float, is_ai=False):
        self.sid = sid
        self.name = name
        self.color = color
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.mass = 12.0
        self.score = 0
        self.base_speed = 195 if not is_ai else rand(150, 180)
        self.tight = False
        self.dead = False
        self.is_ai = is_ai
        self.boost_cd = 0.0
        self.boost_timer = 0.0
        self.input_x = 0.0
        self.input_y = 0.0
        self.input_mag = 0.0
        self.input_boost = False
        self.respawn_at = None
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

    def lose(self, value: float):
        self.mass -= value
        if self.mass < 3:
            self.mass = 0
            self.dead = True
            if not self.is_ai:
                self.respawn_at = time.time() + 2.5
        else:
            self.recalc()

    def respawn(self):
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
        self.recalc()


GAME = {
    "players": {},   # sid -> Swarm
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

    hue = random.randint(180, 320)

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


def create_ai():
    ai = Swarm(
        sid=None,
        name=random.choice(NAMES) + str(random.randint(1, 99)),
        color=random.choice(COLORS),
        x=rand(120, WORLD_W - 120),
        y=rand(120, WORLD_H - 120),
        is_ai=True,
    )
    ai.mass = rand(9, 18)
    ai.score = int(ai.mass * 10)
    ai.recalc()
    return ai


def ensure_world():
    if not GAME["ais"]:
        GAME["ais"] = [create_ai() for _ in range(AI_COUNT)]
    while len(GAME["food"]) < FOOD_MIN:
        GAME["food"].append(make_food())


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    global game_thread

    sid = request.sid
    name = f"Player{random.randint(100, 999)}"
    color = random.choice(COLORS)

    with state_lock:
        ensure_world()
        GAME["players"][sid] = Swarm(
            sid=sid,
            name=name,
            color=color,
            x=rand(200, WORLD_W - 200),
            y=rand(200, WORLD_H - 200),
        )

        if game_thread is None:
            game_thread = socketio.start_background_task(game_loop)

    emit(
        "connected",
        {
            "sid": sid,
            "name": name,
            "world": {"width": WORLD_W, "height": WORLD_H},
        },
    )


@socketio.on("set_name")
def set_name(data):
    sid = request.sid
    name = (data or {}).get("name", "").strip()[:16]

    if not name:
        return

    with state_lock:
        if sid in GAME["players"]:
            GAME["players"][sid].name = name


@socketio.on("input")
def on_input(data):
    sid = request.sid

    with state_lock:
        player = GAME["players"].get(sid)
        if not player:
            return

        player.input_x = float((data or {}).get("dx", 0))
        player.input_y = float((data or {}).get("dy", 0))
        player.input_mag = clamp(float((data or {}).get("mag", 0)), 0, 1)
        player.tight = bool((data or {}).get("tight", False))
        player.input_boost = bool((data or {}).get("boost", False))

        if player.dead and bool((data or {}).get("respawn", False)):
            player.respawn()


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    with state_lock:
        GAME["players"].pop(sid, None)


def try_boost(sw: Swarm):
    if sw.boost_cd > 0 or sw.mass <= 8:
        return

    sw.boost_timer = 0.25
    sw.boost_cd = 1.0

    angle = math.atan2(sw.vy if sw.vy else 0.0001, sw.vx if sw.vx else 0.0001)

    for _ in range(5):
        a = angle + math.pi + rand(-0.35, 0.35)
        fx = sw.x + math.cos(a) * rand(10, 22)
        fy = sw.y + math.sin(a) * rand(10, 22)
        food = make_food(fx, fy, rand(0.7, 1.2), 8)
        food["vx"] = math.cos(a) * rand(25, 80) * 0.01
        food["vy"] = math.sin(a) * rand(25, 80) * 0.01
        GAME["food"].append(food)

    sw.lose(0.8)


def update_player(sw: Swarm, dt: float):
    if sw.dead:
        return

    if sw.input_boost and sw.boost_timer <= 0 and sw.boost_cd <= 0:
        try_boost(sw)

    speed = sw.base_speed * (1 - min(sw.mass, 120) * 0.0018)
    speed *= 0.95 if sw.tight else 1.02
    if sw.boost_timer > 0:
        speed *= 1.7

    target_vx = sw.input_x * speed * sw.input_mag
    target_vy = sw.input_y * speed * sw.input_mag

    sw.vx += (target_vx - sw.vx) * 0.08
    sw.vy += (target_vy - sw.vy) * 0.08

    sw.x += sw.vx * dt
    sw.y += sw.vy * dt

    sw.x = clamp(sw.x, 20, WORLD_W - 20)
    sw.y = clamp(sw.y, 20, WORLD_H - 20)


def update_ai(sw: Swarm, dt: float, all_swarms):
    if sw.dead:
        return

    sw.ai_timer -= dt

    nearest_food = None
    nearest_food_d = float("inf")
    threat = None
    threat_d = float("inf")
    prey = None
    prey_d = float("inf")

    for food in GAME["food"]:
        d = dist(sw.x, sw.y, food["x"], food["y"])
        if d < nearest_food_d:
            nearest_food_d = d
            nearest_food = food

    for other in all_swarms:
        if other is sw or other.dead:
            continue

        d = dist(sw.x, sw.y, other.x, other.y)

        if other.mass > sw.mass * 1.2 and d < 320 and d < threat_d:
            threat = other
            threat_d = d

        if sw.mass > other.mass * 1.18 and d < 260 and d < prey_d:
            prey = other
            prey_d = d

    dx = sw.ai_dir_x
    dy = sw.ai_dir_y
    sw.tight = False

    if threat:
        dx = sw.x - threat.x
        dy = sw.y - threat.y
        sw.tight = True
        if sw.boost_cd <= 0 and sw.mass > 10 and threat_d < 180:
            try_boost(sw)
    elif prey:
        dx = prey.x - sw.x
        dy = prey.y - sw.y
    elif nearest_food:
        dx = nearest_food["x"] - sw.x
        dy = nearest_food["y"] - sw.y

    if sw.ai_timer <= 0:
        sw.ai_timer = rand(0.7, 1.5)
        if not threat and not prey and not nearest_food:
            sw.ai_dir_x = rand(-1, 1)
            sw.ai_dir_y = rand(-1, 1)

    mag = math.hypot(dx, dy) or 1
    dx /= mag
    dy /= mag

    speed = sw.base_speed * (1 - min(sw.mass, 120) * 0.0017)
    if sw.boost_timer > 0:
        speed *= 1.6
    if sw.tight:
        speed *= 0.97

    target_vx = dx * speed
    target_vy = dy * speed

    sw.vx += (target_vx - sw.vx) * 0.04
    sw.vy += (target_vy - sw.vy) * 0.04

    sw.x += sw.vx * dt
    sw.y += sw.vy * dt

    sw.x = clamp(sw.x, 20, WORLD_W - 20)
    sw.y = clamp(sw.y, 20, WORLD_H - 20)


def update_food(dt: float):
    i = 0
    while i < len(GAME["food"]):
        food = GAME["food"][i]

        food["x"] += food["vx"]
        food["y"] += food["vy"]
        food["vx"] *= 0.995
        food["vy"] *= 0.995

        food["x"] = clamp(food["x"], 12, WORLD_W - 12)
        food["y"] = clamp(food["y"], 12, WORLD_H - 12)

        if food["ttl"] is not None:
            food["ttl"] -= dt
            if food["ttl"] <= 0:
                GAME["food"].pop(i)
                continue

        i += 1

    while len(GAME["food"]) < FOOD_MIN:
        GAME["food"].append(make_food())


def collect_food(all_swarms):
    for sw in all_swarms:
        if sw.dead:
            continue

        collect_radius = sw.radius * (0.85 if sw.tight else 1.28)

        i = len(GAME["food"]) - 1
        while i >= 0:
            food = GAME["food"][i]
            if dist(sw.x, sw.y, food["x"], food["y"]) < collect_radius:
                sw.gain(food["value"])
                GAME["food"].pop(i)
            i -= 1


def handle_combat(all_swarms, dt: float):
    for i in range(len(all_swarms)):
        a = all_swarms[i]
        if a.dead:
            continue

        for j in range(i + 1, len(all_swarms)):
            b = all_swarms[j]
            if b.dead:
                continue

            d = dist(a.x, a.y, b.x, b.y)
            combat_range = (a.radius + b.radius) * 0.6

            if d < combat_range:
                bigger, smaller = (a, b) if a.mass >= b.mass else (b, a)

                if bigger.mass > smaller.mass * 1.08:
                    drain = 1.35 * dt
                    smaller.lose(drain)
                    bigger.gain(drain * 0.65)

                    for _ in range(2):
                        angle = rand(0, math.pi * 2)
                        fx = smaller.x + math.cos(angle) * rand(8, max(8, smaller.radius * 0.65))
                        fy = smaller.y + math.sin(angle) * rand(8, max(8, smaller.radius * 0.65))
                        food = make_food(fx, fy, rand(0.5, 1.0), 5)
                        food["vx"] = math.cos(angle) * rand(0.4, 1.2)
                        food["vy"] = math.sin(angle) * rand(0.4, 1.2)
                        GAME["food"].append(food)

    new_ais = []
    for ai in GAME["ais"]:
        if ai.dead:
            for _ in range(22):
                angle = rand(0, math.pi * 2)
                fx = ai.x + math.cos(angle) * rand(0, max(10, ai.radius))
                fy = ai.y + math.sin(angle) * rand(0, max(10, ai.radius))
                food = make_food(fx, fy, rand(0.8, 1.5), 10)
                food["vx"] = math.cos(angle) * rand(0.5, 2.0)
                food["vy"] = math.sin(angle) * rand(0.5, 2.0)
                GAME["food"].append(food)

            new_ais.append(create_ai())
        else:
            new_ais.append(ai)

    GAME["ais"] = new_ais


def snapshot_for(sid: str):
    player = GAME["players"].get(sid)

    if player:
        cx = player.x
        cy = player.y
        score = player.score
        mass = int(player.mass)
        tight = player.tight
        boost = max(player.boost_cd, 0)
        dead = player.dead
        respawn_in = max(0, round((player.respawn_at - time.time()), 1)) if player.dead and player.respawn_at else 0
    else:
        cx = WORLD_W / 2
        cy = WORLD_H / 2
        score = 0
        mass = 0
        tight = False
        boost = 0
        dead = True
        respawn_in = 0

    all_swarms = list(GAME["players"].values()) + GAME["ais"]

    leaderboard = sorted(
        [s for s in all_swarms if not s.dead],
        key=lambda s: s.score,
        reverse=True,
    )[:5]

    view_pad = 1100

    swarms_payload = []
    for sw in all_swarms:
        if abs(sw.x - cx) < view_pad and abs(sw.y - cy) < view_pad:
            swarms_payload.append(
                {
                    "id": sw.sid or ("ai-" + sw.name),
                    "x": round(sw.x, 2),
                    "y": round(sw.y, 2),
                    "mass": round(sw.mass, 2),
                    "score": sw.score,
                    "color": sw.color,
                    "name": sw.name,
                    "tight": sw.tight,
                    "dead": sw.dead,
                    "is_ai": sw.is_ai,
                }
            )

    food_payload = []
    for food in GAME["food"]:
        if abs(food["x"] - cx) < view_pad and abs(food["y"] - cy) < view_pad:
            food_payload.append(
                {
                    "x": round(food["x"], 2),
                    "y": round(food["y"], 2),
                    "r": food["r"],
                    "color": food["color"],
                }
            )

    return {
        "you": {
            "x": cx,
            "y": cy,
            "score": score,
            "mass": mass,
            "tight": tight,
            "boost_cd": boost,
            "dead": dead,
            "respawn_in": respawn_in,
        },
        "world": {"width": WORLD_W, "height": WORLD_H},
        "food": food_payload,
        "swarms": swarms_payload,
        "leaderboard": [
            {"name": sw.name, "score": sw.score, "color": sw.color}
            for sw in leaderboard
        ],
        "player_count": len(GAME["players"]),
        "ai_count": len(GAME["ais"]),
    }


def game_loop():
    while True:
        time.sleep(1 / TICK_RATE)

        with state_lock:
            now = time.time()
            dt = min(now - GAME["last_update"], 0.05)
            GAME["last_update"] = now

            ensure_world()

            all_swarms = list(GAME["players"].values()) + GAME["ais"]

            update_food(dt)

            for player in list(GAME["players"].values()):
                if player.boost_cd > 0:
                    player.boost_cd -= dt
                if player.boost_timer > 0:
                    player.boost_timer -= dt
                update_player(player, dt)

            for ai in GAME["ais"]:
                if ai.boost_cd > 0:
                    ai.boost_cd -= dt
                if ai.boost_timer > 0:
                    ai.boost_timer -= dt
                update_ai(ai, dt, all_swarms)

            all_swarms = list(GAME["players"].values()) + GAME["ais"]

            collect_food(all_swarms)
            handle_combat(all_swarms, dt)

            for sid in list(GAME["players"].keys()):
                socketio.emit("state", snapshot_for(sid), to=sid)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
