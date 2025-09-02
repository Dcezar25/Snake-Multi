#!/usr/bin/env python3

import socket
import threading
import json
import sys
import time
import os
import random
import getpass

HOST = '127.0.0.1'
PORT = 50007

NL = bytes([10])

STOP = threading.Event()
sock_ref_lock = threading.Lock()
sock_ref = None

def shutdown(reason=""):
    """Stop everything and exit the whole process immediately."""
    if reason:
        print(reason)
    STOP.set()

    global sock_ref
    with sock_ref_lock:
        s = sock_ref
    try:
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            s.close()
    except Exception:
        pass

    os._exit(0)


if os.name == 'nt':
    import msvcrt
    def getch():
        ch = msvcrt.getch()
        try:
            return ch.decode(errors='ignore')
        except Exception:
            return ''
else:
    import tty, termios
    def getch():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

KEY_TO_DIR = {
    'w': 'UP', 'W': 'UP',
    's': 'DOWN', 'S': 'DOWN',
    'a': 'LEFT', 'A': 'LEFT',
    'd': 'RIGHT', 'D': 'RIGHT',
}

def send_json(sock, obj):
    if STOP.is_set():
        return
    data = json.dumps(obj).encode('utf-8') + NL
    try:
        sock.sendall(data)
    except Exception:

        shutdown("Connection closed while sending.")


last_state = None
state_lock = threading.Lock()
last_pong = time.perf_counter()
client_pid = None


unreliable = False
loss_prob = 0.3
lag_ms = 200


next_input_id = 0
pending_inputs = {}
pending_lock = threading.Lock()
retry_interval_s = 0.3
max_retries = 5


from threading import Lock
_display_lock = Lock()

def clear_screen():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def render_state(state, banner: str = ""):
    if not state or STOP.is_set():
        return
    w = state['grid_w']; h = state['grid_h']
    fruit = state['fruit']
    players = state['players']
    grid = [[' ' for _ in range(w)] for __ in range(h)]
    for p in players:
        for i, (x, y) in enumerate(p['snake']):
            if 0 <= y < h and 0 <= x < w:
                grid[y][x] = 'H' if i == 0 else 'O'
    fx, fy = fruit
    if 0 <= fy < h and 0 <= fx < w:
        grid[fy][fx] = '*'
    border_h = '+' + '-' * w + '+'
    with _display_lock:
        clear_screen()
        print(border_h)
        for row in grid:
            print('|' + ''.join(row) + '|')
        print(border_h)
        # HUD
        scoreline = ' | '.join([f"P{p['pid']}: {p['score']} {'(dead)' if not p['alive'] else ''}" for p in players])
        print(scoreline)
        mode = 'UNRELIABLE' if unreliable else 'RELIABLE'
        if banner:
            print(banner)
        print(f"Net: {mode}  loss={loss_prob}  lag_ms={lag_ms}  (press 'u' to toggle)")
        print("Controls: W A S D (q = quit)")


def handle_msg(sock, msg):
    global last_state, last_pong, client_pid
    t = msg.get('type')
    if t == 'login_resp':
        if not msg.get('ok'):
            shutdown(f"Login failed: {msg.get('reason')}")
        client_pid = msg.get('pid')
    elif t == 'start_resp':
        print("Game started, mode:", msg.get('mode'))
    elif t == 'update':
        if 'seq' in msg:
            send_json(sock, {"type": "ack", "ack": "update", "seq": msg['seq']})
        with state_lock:
            last_state = msg
        render_state(msg)
    elif t == 'win':
        shutdown("YOU WIN!")
    elif t == 'lose':
        shutdown(f"YOU LOSE: {msg.get('reason')}")
    elif t == 'pong':
        last_pong = time.perf_counter()
    elif t == 'disconnect':
        shutdown(f"Disconnected by server: {msg.get('reason')}")
    elif t == 'ack' and msg.get('ack') == 'input':
        mid = msg.get('id')
        with pending_lock:
            pending_inputs.pop(mid, None)

def recv_loop(sock):
    buf = b''
    while not STOP.is_set():
        try:
            data = sock.recv(4096)
            if not data:
                shutdown("Disconnected from server.")
            buf += data
            while NL in buf:
                line, buf = buf.split(NL, 1)
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode('utf-8'))
                except Exception:
                    continue
                handle_msg(sock, msg)
        except Exception as e:
            shutdown(f"Connection error: {e}")

def ping_loop(sock):
    global last_pong
    while not STOP.is_set():
        send_json(sock, {"type": "ping", "t": time.time()})

        for _ in range(10):
            if STOP.is_set(): return
            time.sleep(0.1)
        if (time.perf_counter() - last_pong) > 5.0:
            shutdown("Ping timeout (no pong in >5s).")

def resend_loop(sock):
    while not STOP.is_set():
        time.sleep(retry_interval_s)
        if STOP.is_set(): break
        with pending_lock:
            now = time.perf_counter()
            to_retry = []
            for mid, (msg, last, tries) in list(pending_inputs.items()):
                if now - last >= retry_interval_s and tries < max_retries:
                    to_retry.append(mid)
                elif tries >= max_retries:
                    print(f"Input {mid} dropped after {tries} retries")
                    pending_inputs.pop(mid, None)
            for mid in to_retry:
                msg, last, tries = pending_inputs[mid]
                send_json(sock, msg)
                pending_inputs[mid] = (msg, now, tries + 1)


def predict_once(direction: str):
    if STOP.is_set(): return
    global last_state
    with state_lock:
        if not last_state:
            return
        st = json.loads(json.dumps(last_state))
    w = st['grid_w']; h = st['grid_h']
    my = None
    for p in st['players']:
        if p['pid'] == client_pid:
            my = p; break
    if not my: return
    snake = my['snake']
    if len(snake) >= 2:
        hx, hy = snake[0]; nx, ny = snake[1]
        cur_dir = 'RIGHT' if hx > nx else 'LEFT' if hx < nx else 'DOWN' if hy > ny else 'UP'
    else:
        cur_dir = 'RIGHT'
    opposite = {'UP':'DOWN','DOWN':'UP','LEFT':'RIGHT','RIGHT':'LEFT'}
    if opposite.get(cur_dir) == direction:
        direction = cur_dir
    dx = dy = 0
    if direction == 'UP': dy = -1
    elif direction == 'DOWN': dy = 1
    elif direction == 'LEFT': dx = -1
    elif direction == 'RIGHT': dx = 1
    hx, hy = snake[0]
    new_head = [max(0, min(w-1, hx+dx)), max(0, min(h-1, hy+dy))]
    my['snake'] = [new_head] + snake[:-1]
    render_state(st, banner="(predicted)")

def send_input(sock, dirc):
    if STOP.is_set(): return
    global next_input_id
    if unreliable and random.random() < loss_prob:
        return
    delay = random.uniform(0, lag_ms) / 1000.0 if unreliable else 0.0
    next_input_id += 1
    mid = next_input_id
    msg = {"type": "input", "dir": dirc, "id": mid}
    def do_send():
        if STOP.is_set(): return
        send_json(sock, msg)
        with pending_lock:
            pending_inputs[mid] = (msg, time.perf_counter(), 0)
    if delay > 0:
        t = threading.Timer(delay, do_send); t.daemon = True; t.start()
    else:
        do_send()

def input_loop(sock):
    print("Ready for input. Use WASD. Press 'u' for unreliable net. Press q to quit.")
    while not STOP.is_set():
        ch = getch()
        if STOP.is_set():
            break
        if ch == 'q':
            shutdown("Bye.")
        if ch == 'u':
            toggle_unreliable(sock); continue
        dirc = KEY_TO_DIR.get(ch)
        if dirc:
            send_input(sock, dirc)
            predict_once(dirc)

def toggle_unreliable(sock):
    global unreliable
    unreliable = not unreliable
    mode = 'unreliable' if unreliable else 'reliable'
    send_json(sock, {"type": "net_mode", "mode": mode, "loss": loss_prob, "lag_ms": lag_ms})

def main():

    mode = 'prediction'
    if len(sys.argv) >= 2 and sys.argv[1] in ('input_only', 'prediction'):
        mode = sys.argv[1]


    key = None

    if len(sys.argv) >= 3:
        try:
            key = int(sys.argv[2])
        except Exception:
            key = None

    while key is None:
        raw = getpass.getpass("Enter numeric login key: ").strip()
        if raw.isdigit():
            key = int(raw)
        else:
            print("Please enter digits only (e.g., 4242).")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with sock_ref_lock:
            global sock_ref
            sock_ref = s
        s.connect((HOST, PORT))
        send_json(s, {"type": "login", "key": key})
        send_json(s, {"type": "start", "mode": mode})


        threading.Thread(target=recv_loop, args=(s,), daemon=True).start()
        threading.Thread(target=ping_loop, args=(s,), daemon=True).start()
        threading.Thread(target=resend_loop, args=(s,), daemon=True).start()


        input_loop(s)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        shutdown("\nBye!")
