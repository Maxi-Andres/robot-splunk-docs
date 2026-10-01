# Qué corre en cada robot

**Escrito el 2026-09-30.** El mapa de qué código va en el Go2, qué va en el G1, qué es
compartido y qué corre en HQ. Si agregás un archivo a cualquiera de los tres repos del robot,
tiene que caer en una de estas filas — y su nombre tiene que decir en cuál.

---

## 1. La convención de nombres

| El archivo se llama… | Corre en… | Ejemplo |
|---|---|---|
| **`go2_*`** | solo el Go2 | `go2_telemetry_reader.cpp`, `go2_h264_stream.cpp` |
| **`g1_*`** o vive bajo **`host/g1/`** | solo el G1 | `g1_telemetry_reader.cpp`, `host/g1/uplink-failover.sh` |
| **sin prefijo** | los dos, el mismo archivo | `hec_shipper.py`, `ndjson.hpp`, `relay_server.py`, `videohub_jpeg_stream.cpp` |
| `*.g1.service` | unit del G1, se instala **con el nombre sin `.g1`** | `robot-telemetry-agent.g1.service` → `/etc/systemd/system/robot-telemetry-agent.service` |

Qué robot es lo decide **`ROBOT_MODEL`** (`go2` por defecto, `g1`) en el `run.sh` o el unit
de cada repo. **Una sola variable elige el binario; todo lo que viene después del binario es
compartido.** Si un repo necesita otra cosa además de `ROBOT_MODEL` para distinguir el robot,
está mal diseñado.

> ⚠️ **Deuda de nombres, a saldar cuando se escriba la variante del G1:**
> `robot-command-relay/src/command_sender.cpp` es **del Go2** aunque no tenga prefijo.
> El video ya no tiene deuda: `robot-video-pipeline/robot/` resultó **compartido** — el G1 lee
> su cámara por el mismo videohub de Unitree — y `go2_jpeg_stream` se renombró a
> **`videohub_jpeg_stream`** (2026-10-01) porque corre en los dos. No se renombraron todavía porque hoy no hay otra
> variante con la que confundirlos, y renombrar sin tener la del G1 solo mueve el problema.
> El lector de telemetría sí se renombró (`telemetry_reader` → `go2_telemetry_reader`,
> 2026-09-30) porque ahí ya había dos.

---

## 2. Los dos robots, lado a lado

| | **Go2** | **G1 Pro (BENDER)** |
|---|---|---|
| Máquina donde se instala todo | el Jetson | **PC2**, el Jetson. **PC1 (`.161`) no se toca: no tiene SSH** |
| IP del bus interno (DDS) | `192.168.123.18` | `192.168.123.164` |
| IP por la que se llega desde HQ | `10.1.254.18` (túnel del IR1101) | cable: `192.168.123.164` · WiFi: `192.168.51.115` |
| Enlace | IR1101 → LTE / Starlink → IPsec | cable o WiFi "ROBOTS ONLY" (VLAN 51); después CURWB |
| Cómo elige la salida | la rutea el IR1101 | **`uplink-failover`**: cable si el gateway contesta, WiFi si no |
| IDL del SDK | `unitree_go` | `unitree_hg` |
| SDK | `~/unitree_sdk2` | `~/unitree_sdk2` @ `63096d0`. **No** el `~/unitree_sdk2-main` de fábrica: no trae `unitree_hg` |
| Cámara | videohub por DDS (JPEG) + H.264 multicast `230.1.1.1:1720` | **RealSense D435i por USB en PC2** (`/dev/video0-5`) |
| Motores | 12 | 29 |

---

## 3. Qué corre en cada uno

Estado al 2026-09-30. ✅ corriendo · 🟡 escrito, sin desplegar · ❌ falta.

### `robot-telemetry-agent`

| Pieza | Go2 | G1 |
|---|---|---|
| Lector DDS | ✅ `src/go2_telemetry_reader.cpp` — `rt/lf/lowstate` + `rt/lf/sportmodestate` | ✅ `src/g1_telemetry_reader.cpp` — `rt/lf/lowstate` + `rt/lf/bmsstate` |
| Shipper | ✅ `shipper/hec_shipper.py` (compartido) | ✅ el mismo |
| Salida JSON | `src/ndjson.hpp` (compartido) | el mismo |
| Unit | `systemd/robot-telemetry-agent.service` | `systemd/robot-telemetry-agent.g1.service` |
| Índice / token HEC | `go2-robot-data` / el del Go2 | `g1-robot-data` / **`g1-robot-telemetry`** (propio, revocable por robot) |

### `robot-command-relay`

| Pieza | Go2 | G1 |
|---|---|---|
| Servidor HTTP | ✅ `relay_server.py` | ❌ el mismo archivo, falta desplegarlo |
| Sender DDS | ✅ `src/command_sender.cpp` — `go2::SportClient` | ❌ falta la variante con `g1::LocoClient` |
| Failover de salida | — (lo resuelve el IR1101) | ✅ `host/g1/uplink-failover.sh` + `.service` |

El failover vive en este repo porque el relay es lo que más depende del enlace: si la salida
se cae, el hombre muerto frena al robot, pero el operador pierde el control. Es config del
host del G1, no del relay — por eso `host/g1/` y no `src/`.

### `robot-video-pipeline`

| Pieza | Go2 | G1 |
|---|---|---|
| Fuente | ✅ `src/videohub_jpeg_stream.cpp` (compartido) o `src/go2_h264_stream.cpp` (multicast, solo Go2) | 🟡 el mismo `videohub_jpeg_stream`: lee el `videohub_pc4` de Unitree, que es el dueño de la RealSense. Medido 2026-10-01: 1920×1080 a 15 fps |
| Encode + push | ✅ `robot/run-video.sh` → SRT `:8891` | 🟡 el mismo `run-video.sh` → SRT `:8893` |
| Configuración | `robot/video.env` desde `video.env.example` | `robot/video.env` desde **`video.g1.env.example`** (7 claves distintas) |
| Vista de manejo | ✅ `robot/mjpeg_server.py` `:8093` | 🟡 el mismo, `:8093` |
| Camino en mediamtx / Frigate | `robot` / `robot` | ✅ `g1` / `g1` — HQ armado 2026-10-01 |

> **La RealSense directa por V4L2** (`SOURCE=realsense`) **no se hizo**: en el G1 la cámara la
> tiene el `videohub_pc4` de Unitree (`/dev/video4` "busy"), y abrirla rompería su video. Sí
> sirve para la **RealSense que se le puede agregar al Go2**, donde nadie la usa — queda para
> entonces, y va **sin prefijo** porque la misma cámara existe en los dos robots. La
> **profundidad** del G1 (`/dev/video2`) está libre: es la que va a usar la percepción 3D.

### Fuera de los repos, en el robot

| | Go2 | G1 |
|---|---|---|
| Agente ThousandEyes | ✅ `go2-jetson-01`, Docker **bridge** | ✅ `g1-jetson-01`, Docker **host** (`TE-AGENTE-G1.md`) |

---

## 4. Lo que corre en HQ (esta PC, `192.168.20.99`), para los dos

| | Qué |
|---|---|
| `mediamtx` | caminos `robot` (Go2) y `g1` |
| `srt-bridge` / `srt-bridge-g1` | Go2 `:8891` → `udp:9000`; G1 `:8893` → `udp:9001` (unit de usuario en esta PC) |
| Frigate | cámaras `robot` y `g1` |
| `te-poller` | trae los dos agentes TE sin configuración: es por métrica, no por agente |
| Dashboards (Splunk `.20.200`) | `go2-telemetria-thousandeyes.xml` y `g1-telemetria-thousandeyes.xml` |

---

## 5. Cómo desplegar en cada robot

> **Los comandos al día, por robot, están en la app:** página **Robot** (`/robot`), elegido el
> robot arriba a la derecha. Salen de `AI-VL-frontend/src/pages/robotDeploy.ts`, que tiene un
> test que falla si un comando del G1 nombra un archivo `go2_*` (o al revés). **Si cambiás
> qué corre en un robot, cambiá ese archivo y esta tabla juntos.**

```bash
# Go2 — como siempre
ssh unitree@10.1.254.18
cd ~/robot-telemetry-agent && git pull && ./build.sh && sudo systemctl restart robot-telemetry-agent

# G1 — PC2, por la IP que responda (cable .164, WiFi .51.115)
ssh unitree@192.168.51.115
cd ~/robot-telemetry-agent && git pull && UNITREE_SDK2_DIR=~/unitree_sdk2 ./build.sh \
  && sudo systemctl restart robot-telemetry-agent
```

> ⚠️ El 2026-09-30 el código del G1 se copió a PC2 con `rsync` porque no estaba commiteado.
> Hasta que se commitee y se reemplace esa copia por un `git clone`, **`git pull` en PC2 no
> funciona**: `~/robot-telemetry-agent` no es un repo ahí.
