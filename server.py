

import socket, threading, json, time, random
from collections import deque

HOST = '127.0.0.1'
PORT = 50007
LOGIN_KEY = 4242

FPS = 30.0
GRID_W, GRID_H = 20, 15
SNAKE_LEN = 4
POINTS_PER_FRUIT = 10
WIN_SCORE = 100
PING_TIMEOUT = 5.0

NL = bytes([10])

def send_json(conn, obj):
    try:
        conn.sendall(json.dumps(obj).encode('utf-8') + NL)
    except Exception:
        pass

class PlayerState:
    _next_id = 0
    _id_lock = threading.Lock()

    def __init__(self, conn, addr):
        with PlayerState._id_lock:
            PlayerState._next_id += 1
            self.pid = PlayerState._next_id

        self.conn, self.addr = conn, addr
        self.authenticated = False
        self.mode = 'input_only'
        self.input_lock = threading.Lock()
        self.input_queue = deque()
        self.last_input = None
        self.direction = 'RIGHT'
        self.alive = False
        self.score = 0

        midx = GRID_W // 2
        midy = GRID_H // 2 + (self.pid - 1) * 2 - 1
        self.snake = deque([[midx - i, midy] for i in range(SNAKE_LEN)][::-1])

        # movement throttling
        self.move_interval = 5
        self._move_counter = 0

        # heartbeat
        self.last_ping = time.perf_counter()


        self.s2c_seq = 0
        self.pending_update_seq = None
        self.pending_update_payload = None
        self.last_update_send_time = 0.0
        self.update_retry_s = 0.3


        self.net_unreliable = False
        self.net_loss_prob = 0.3
        self.net_lag_ms = 200

    def pub(self):
        return {"pid": self.pid, "snake": list(self.snake), "score": self.score, "alive": self.alive}

players = {}
players_lock = threading.Lock()
fruit = [random.randrange(1, GRID_W - 1), random.randrange(1, GRID_H - 1)]

def spawn_fruit():
    global fruit
    occ = set()
    with players_lock:
        for ps in players.values():
            for x, y in ps.snake:
                occ.add((x, y))
    while True:
        x = random.randrange(1, GRID_W - 1)
        y = random.randrange(1, GRID_H - 1)
        if (x, y) not in occ:
            fruit = [x, y]
            return

def safe_close(ps, reason):
    try: send_json(ps.conn, {"type": "disconnect", "reason": reason})
    except: pass
    try: ps.conn.close()
    except: pass
    print(f"[SERVER] Closed {ps.addr} (pid={ps.pid}) — {reason}")

def maybe_send_update(ps, payload):
    ps.s2c_seq += 1
    out = dict(payload); out["seq"] = ps.s2c_seq

    def do_send():
        send_json(ps.conn, out)
        ps.pending_update_seq = out["seq"]
        ps.pending_update_payload = out
        ps.last_update_send_time = time.perf_counter()

    if ps.net_unreliable:
        if random.random() < ps.net_loss_prob:
            ps.pending_update_seq = out["seq"]
            ps.pending_update_payload = out
            ps.last_update_send_time = time.perf_counter()
            return
        lag = random.uniform(0, ps.net_lag_ms) / 1000.0
        if lag > 0:
            threading.Timer(lag, do_send).start()
        else:
            do_send()
    else:
        do_send()

def retry_unacked_updates(ps):
    if ps.pending_update_seq is None: return
    if time.perf_counter() - ps.last_update_send_time >= ps.update_retry_s:
        send_json(ps.conn, ps.pending_update_payload)
        ps.last_update_send_time = time.perf_counter()

def client_handler(conn, addr):
    print(f"[SERVER] Incoming TCP handshake from {addr}…")
    with conn:
        ps = PlayerState(conn, addr)
        with players_lock:
            players[conn] = ps
        print(f"[SERVER] Accepted {addr} as pid={ps.pid}")

        buf = b''
        try:
            while True:
                data = conn.recv(4096)
                if not data: break
                buf += data
                while NL in buf:
                    line, buf = buf.split(NL, 1)
                    if not line: continue
                    try:
                        msg = json.loads(line.decode('utf-8'))
                    except Exception:
                        continue
                    handle_message(ps, msg)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            with players_lock:
                players.pop(conn, None)
            print(f"[SERVER] Disconnected {addr} (pid={ps.pid})")

def handle_message(ps, msg):
    t = msg.get("type")

    if t == "login":
        key = msg.get("key")
        print(f"[SERVER] Login attempt pid={ps.pid}, key={key}")
        if key == LOGIN_KEY:
            ps.authenticated = True
            send_json(ps.conn, {"type": "login_resp", "ok": True, "pid": ps.pid})
        else:
            send_json(ps.conn, {"type": "login_resp", "ok": False, "reason": "bad_key"})
            safe_close(ps, "auth_failed")

    elif t == "ping":
        ps.last_ping = time.perf_counter()
        send_json(ps.conn, {"type": "pong", "t": msg.get("t", 0.0)})

    elif t == "net_mode":
        mode = msg.get("mode", "reliable")
        ps.net_unreliable = (mode == "unreliable")
        ps.net_loss_prob = float(msg.get("loss", 0.3))
        ps.net_lag_ms = int(msg.get("lag_ms", 200))
        print(f"[SERVER] pid={ps.pid} net_mode={mode} loss={ps.net_loss_prob} lag_ms={ps.net_lag_ms}")

    elif not ps.authenticated:
        send_json(ps.conn, {"type": "login_resp", "ok": False, "reason": "not_authenticated"})
        safe_close(ps, "not_authenticated")

    elif t == "start":
        mode = msg.get("mode", "input_only")
        if mode not in ("input_only", "prediction"): mode = "input_only"
        ps.mode = mode
        ps.alive = True
        ps.score = 0
        ps.last_input = ps.direction
        ps._move_counter = 0
        ps.last_ping = time.perf_counter()
        print(f"[SERVER] pid={ps.pid} start mode={ps.mode}")
        snapshot_and_send()

    elif t == "input":
        dirc = msg.get("dir")
        mid  = msg.get("id")
        if dirc in ("UP","DOWN","LEFT","RIGHT"):
            with ps.input_lock:
                ps.input_queue.append(dirc)
                ps.last_input = dirc
            print(f"[SERVER] input pid={ps.pid}: {dirc}")
            if mid is not None:
                send_json(ps.conn, {"type": "ack", "ack": "input", "id": mid})

    elif t == "ack" and msg.get("ack") == "update":
        if msg.get("seq") == ps.pending_update_seq:
            ps.pending_update_seq = None
            ps.pending_update_payload = None

def poll_dir(ps):
    d = None
    with ps.input_lock:
        if ps.input_queue:
            d = ps.input_queue.popleft()
    if d is None:
        d = ps.last_input if (ps.mode == "prediction" and ps.last_input) else ps.direction
    opposite = {"UP":"DOWN","DOWN":"UP","LEFT":"RIGHT","RIGHT":"LEFT"}
    if opposite.get(ps.direction) == d:
        d = ps.direction
    return d

def snapshot_and_send():
    with players_lock:
        plist = list(players.values())
        world = {"type":"update","grid_w":GRID_W,"grid_h":GRID_H,"fruit":fruit,
                 "players":[p.pub() for p in plist]}
        for p in plist:
            maybe_send_update(p, world)

def update_all():
    with players_lock:
        plist = list(players.values())
    now = time.perf_counter()
    for ps in plist:
        if (now - ps.last_ping) > PING_TIMEOUT:
            safe_close(ps, "ping_timeout")
            ps.alive = False


    intents = {}
    heads_before = {ps.pid: tuple(ps.snake[0]) for ps in plist}
    for ps in plist:
        if not ps.alive: continue
        ps._move_counter += 1
        if ps._move_counter % ps.move_interval != 0:
            intents[ps.pid] = None
            continue
        d = poll_dir(ps)
        ps.direction = d
        dx=dy=0
        if d=="UP": dy=-1
        elif d=="DOWN": dy=1
        elif d=="LEFT": dx=-1
        elif d=="RIGHT": dx=1
        hx,hy = ps.snake[0]
        intents[ps.pid] = (hx+dx, hy+dy)

    def blocked(pid, target):
        if target is None: return True
        x,y = target
        for other in plist:
            if other.pid == pid: continue
            if (x,y) in {(px,py) for px,py in other.snake}: return True

            if intents.get(other.pid) == heads_before.get(pid) and target == heads_before.get(other.pid):
                return True
        return False

    global fruit
    for ps in plist:
        target = intents.get(ps.pid)
        if target is None or not ps.alive: continue
        x,y = target
        if not (0 <= x < GRID_W and 0 <= y < GRID_H):
            ps.alive = False
            send_json(ps.conn, {"type":"lose","reason":"wall_collision"})
            safe_close(ps, "game_over")
            continue
        if blocked(ps.pid, target):
            continue
        ps.snake.appendleft([x,y])
        while len(ps.snake) > SNAKE_LEN: ps.snake.pop()
        if [x,y] == fruit:
            ps.score += POINTS_PER_FRUIT
            spawn_fruit()
            if ps.score >= WIN_SCORE:
                send_json(ps.conn, {"type":"win"})
                safe_close(ps, "win")
                ps.alive = False

    snapshot_and_send()
    for ps in plist: retry_unacked_updates(ps)

def simulation_loop():
    tick = 1.0 / FPS
    last = time.perf_counter()
    while True:
        now = time.perf_counter()
        dt = now - last
        if dt < tick:
            time.sleep(tick - dt)
            now = time.perf_counter()
        last = now
        try:
            update_all()
        except Exception as e:
            print("[SERVER] sim error:", e)

def start_server():
    print(f"Server listening on {HOST}:{PORT}, login key = {LOGIN_KEY}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT)); s.listen()
        threading.Thread(target=simulation_loop, daemon=True).start()
        while True:
            conn, addr = s.accept()
            print("Accepted", addr)
            threading.Thread(target=client_handler, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    start_server()
