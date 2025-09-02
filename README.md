# 🐍 Snake – Local Client/Server (Python sockets, terminal UI)

Mic joc **Snake** client/server pe **sockets TCP** (Python 3) cu UI în **terminal**.  
Are login, start-game cu mod **input_only** / **prediction**, trimitere **input**, **update** de poziții de la server, **spawn/consum fruct** pe server, **lockstep 30 FPS**, **ping/pong** pentru detectarea conexiunii, **disconnect** grațios, **simulare lag/packet loss + ACK/retry**, și **2 clienți** (șerpii se blochează reciproc).

> Rulare rapidă: pornești `server.py` într-un terminal și 1–2 instanțe de `client.py` în alte terminale. Miști cu **WASD**.  

---

## ✅ Cum bifează cerințele temei

- **App client-server locală (sockets, orice limbaj/engine)** – Python 3 + sockets TCP, fără dependențe externe.  
- **Doar cod, UI de terminal** – randare ASCII, fără grafică.  
- **Snake de 4 celule, nu crește** – lungime fixă `SNAKE_LEN=4`.  
- **+10 puncte pe fruct, la 100 → WIN** – `POINTS_PER_FRUIT=10`, `WIN_SCORE=100`.  
- **Coliziune cu pereții → LOSE** – verificare la fiecare mutare, serverul trimite `{"type":"lose"}` și închide.  
- **Login numeric** – clientul trimite `{"type":"login","key":4242}`, serverul validează `LOGIN_KEY`, altfel `disconnect`.  
- **Start Game & tip de mișcare** – clientul trimite `{"type":"start","mode":"input_only"|"prediction"}`; în modul **prediction** serverul folosește ultimul input cunoscut dacă nu vine input nou.  
- **Mesaj input client→server** – `{"type":"input","dir":"UP|DOWN|LEFT|RIGHT"}` (+ id pentru ACK când simulăm UDP).  
- **Mesaj update poziții server→clienți** – `{"type":"update","players":[...],"fruit":[x,y],...}` (toți jucătorii).  
- **Spawn/consum fruct pe server** – serverul gestionează fructul și scorul.  
- **Fixed simulation lockstep – 30 FPS** – buclă la 30 tichete/s; mișcarea snake-ului se face la `FPS/move_interval` (implicit ~6 Hz) pentru stabilitate.  
- **Extra – client prediction** – clientul mișcă instant vizual șarpele local și corectează la următorul `update`.  
- **Extra – lag/packet loss + retry + ACK** – toggle din client cu `u`; inputurile și update-urile folosesc `id/seq` + `ack` + retry.  
- **Extra – detectare disconnect** – ping/pong la 1s, dacă lipsesc >5s → `disconnect`. Serverul trimite și mesaj explicit de `disconnect`.  
- **Extra – 2 clienți, blocking** – un singur fruct global; șerpii **nu trec** unul prin altul (nici head-swap); dacă ținta e ocupată, rămâi pe loc.

---

## 📦 Structură repo

```
/snake-sockets
 ├─ server.py    # logică joc, lockstep, ping/pong, ACK la updates, 2 jucători
 ├─ client.py    # UI terminal, input, prediction, ACK/retry la inputuri
 └─ README.md
```

---

## ⚙️ Instalare & rulare

1) **Python 3.9+** recomandat (merge și pe 3.8+).  
2) În două terminale:

```bash
# Terminal 1
python server.py

# Terminal 2 (cheia implicită e 4242)
python client.py prediction 4242
# sau
python client.py input_only 4242
```

Pentru 2 jucători, deschide încă un terminal și rulează încă o instanță de client.

**Controale:**  
- **W A S D** – mișcare  
- **u** – toggle simulare **UNRELIABLE** (lag/pierderi + retry/ACK)  
- **q** – ieșire

---

## 🧠 Cum funcționează (pe scurt)

- **Protocol JSON over TCP** (delimitat cu un newline).  
- **Server** rulează o **buclă la 30 FPS**. La fiecare tick:
  - Procesează inputurile primite (sau **prediction** dacă nu există input nou).
  - Mută fiecare șarpe la `move_interval` ticks (implicit ~6 mutări/s) → stabilitate & mai puțin flicker în terminal.
  - Verifică **pereți** (lose), **blocking între șerpi** (stay-put dacă ținta e ocupată sau head-swap), **fruct** (score, respawn, win).
  - Trimite un `update` cu întreaga lume către toți clienții. Cu modul nesigur ON, update-urile folosesc `seq` + `ack` + retry.
- **Client**:
  - Trimite **ping** la 1s; dacă nu primește **pong** >5s → se închide.
  - Trimite **input** cu `id` și așteaptă **ack**; dacă modul nesigur e ON poate simula **lag/pierderi** și face **retry**.
  - Face **prediction local** imediat la tastă și corectează la următorul `update`.
  - Randare ASCII a grilei (capul = `H`, corpul = `O`, fructul = `*`).

---

## 📡 Mesaje (schemă)

- **Login (client→server)**
  ```json
  {"type":"login","key":4242}
  ```
  Răspuns:
  ```json
  {"type":"login_resp","ok":true,"pid":1}
  ```

- **Start Game (client→server)**
  ```json
  {"type":"start","mode":"prediction"}   // sau "input_only"
  ```

- **Input (client→server)**
  ```json
  {"type":"input","dir":"UP","id":123}   // id pentru ACK & retry
  ```
  ACK:
  ```json
  {"type":"ack","ack":"input","id":123}
  ```

- **Update (server→client)**
  ```json
  {
    "type":"update",
    "grid_w":20, "grid_h":15,
    "fruit":[fx,fy],
    "players":[ {"pid":1,"snake":[[x,y],...],"score":10,"alive":true}, ... ],
    "seq":45   // pentru ACK la update
  }
  ```
  ACK:
  ```json
  {"type":"ack","ack":"update","seq":45}
  ```

- **Ping/Pong**
  ```json
  {"type":"ping","t": 17123456.0}  // client→server
  {"type":"pong","t": 17123456.0}  // server→client
  ```

- **Disconnect (server→client)**
  ```json
  {"type":"disconnect","reason":"ping_timeout"|"auth_failed"|"win"|"game_over"}
  ```

- **Toggle rețea nesigură (client→server)**
  ```json
  {"type":"net_mode","mode":"unreliable","loss":0.3,"lag_ms":200}
  ```

---

## 🧪 Testare rapidă

- **Single-player**: rulează un client.  
- **Two-player**: rulează doi clienți; încearcă să te „împingi” în celălalt – nu treci prin el, trebuie să îl ocolești.  
- **Lag/Loss**: apasă **`u`** pe client pentru a activa simularea. Vezi în server log:
  ```
  [SERVER] pid=1 net_mode=unreliable loss=0.3 lag_ms=200
  ```

---

## 🛠️ Probleme întâlnite & fixuri

- **Spam cu „Input N dropped after 5 retries”** după închidere  
  – Inițial `sys.exit()` din thread-uri nu omora procesul → am introdus un **shutdown global** pe client care închide socketul și face **hard-exit** (`os._exit(0)`), oprind toate loop-urile (recv/ping/resend/input).

- **`ConnectionAbortedError` la server pe Windows**  
  – Normal când clientul închide socketul în timp ce threadul e în `recv()`. Am prins excepția și ieșim curat din handler.

- **Jitter vizual în terminal**  
  – Terminalul e „greu” (clear + print mare). Ca să fie suportabil:
    - serverul mută la ~6 Hz (nu la 30), reducând flicker;
    - (opțional) activați `TCP_NODELAY` pe socketuri dacă simțiți burst-uri (în `accept()` pe server și la client înainte de `connect()`).

- **Tăiere string pentru `b'\n'` la copy/paste**  
  – Ca să nu mai apară, în unele versiuni am folosit `NL = bytes([10])` sau am verificat să rămână exact `b'\\n'` în cod.

---

## 🧩 Sfaturi & depanare

- **Nu primești update-uri?** Verifică să fie aceeași **schemă** (clientul așteaptă `players:[...]`).  
- **Se deconectează aleator?** Ține `unreliable` **OFF** (default). Pentru test, setează `loss=0.05`, `lag_ms=50`.  
- **„Ping timeout (>5s)”** – serverul nu răspunde la `pong` (server oprit sau blocat) → clientul se închide singur.  
- **Windows „10053/10054”** – sunt normale la închidere; sunt prinse.

---

## 🧭 Roadmap scurt

- Render separat într-o **buclă de 30 FPS** pe client  
- **TCP_NODELAY** by default pe ambele părți  
- Mini-HUD cu latență și FPS client  
- Replay / log inputs

---

## 📝 Licență

Public domain / MIT – cum preferi.  

---

## 👇 Cum rulezi exact (exemple)

```bash
# 1) pornește serverul
python server.py

# 2) pornește un client în prediction (cheie 4242)
python client.py prediction 4242

# 3) pornește al doilea client (alt terminal)
python client.py prediction 4242

# 4) în client: WASD, 'u' pentru lag/loss simulare, 'q' pentru quit
```

