# Puertos — quién escucha qué, y qué cruza el enlace de campo

**Relevado el 2026-09-09**, con el robot **en campo** (LTE de Telefónica, detrás del IR1101)
y todo el stack corriendo. Compañero de `IPS-Y-DONDE-CAMBIARLAS.md`: ese dice *dónde está
cada cosa*, éste dice *por qué puerto habla*.

Todo lo de acá salió de `ss -lntup` en cada máquina, no de leer configuraciones.

---

## 1. Lo único que importa para QoS: los cuatro flujos que cruzan el LTE

Si vas a marcar tráfico, es esto y nada más. El resto es local a cada lado.

| Sentido | Puerto | Medido | Si se demora |
|---|---|---|---|
| robot → HQ | **1935** RTMP → mediamtx | ~218 KB/s | el video se entrecorta |
| robot → HQ | **8093** MJPEG → `camera_bridge` | **~105 KB/s** (eran 228 hasta el 10-09) | ídem — **y es la misma imagen, duplicada** |
| robot → Splunk | **8088** HEC | KB/s | llega tarde, con su timestamp correcto |
| **HQ → robot** | **8092** relay de comandos | bytes | ⚠️ **el robot no frena cuando se lo pedís** |
| HQ → robot | 22 SSH | — | administración |

> 🔑 **La asimetría es el punto.** El flujo crítico es el **único que va de HQ hacia el
> robot** y es el más chico por varios órdenes de magnitud. Los otros tres suman ~450 KB/s
> subiendo. El marcado que corresponde: comandos en **EF/CS5**, telemetría en **AF21**, los
> dos videos en **best-effort**.

> ⚡ **10-09: `MJPEG_FPS=5` + `MJPEG_QUALITY=55`.** Ese stream salía **sin tope** a 14 fps y
> se comía 218 KB/s, ahogando al RTMP: la cola del NVR vivía llena (8) con 15.228 frames
> descartados y Frigate en 0 fps. Con el tope bajó a 105 KB/s, la cola quedó en 0, los
> descartes se detuvieron y Frigate volvió a 5.1 fps estables. **`MJPEG_FPS=0` es el default
> y es el valor equivocado en campo.**

> 📉 **Medido el 09-09, con y sin video:** el RTT al robot es **46 ms de media, 95 de pico,
> 0% de pérdida** con el enlace descargado, y **57 ms de media** con los 450 KB/s de video.
> O sea que **el video aporta ~11 ms**: el jitter es del LTE, no de la saturación propia.
> Antes, con el robot en la LAN, el ping era de **0,25 ms** (`PLAN-CONECTIVIDAD-ROBOTS.md`
> §124). Eso es lo que cambió, y es la causa de los micro tirones al caminar: el ejecutor
> manda `Move()` a 10 Hz (cada 100 ms) y el jitter del enlace es del mismo orden.

---

## 2. Esta PC — `192.168.20.99` (VLAN 20, servidores)

### Lo que consume el robot

| Puerto | Proto | Servicio |
|---|---|---|
| **1935** | tcp | **mediamtx — RTMP, acá publica el robot** |
| **8090** | tcp | **`robot_executor`** — origina los comandos al relay |
| **8091** | tcp | **`robot_camera_bridge`** — tira del MJPEG del robot (8093) |

### mediamtx — el resto de sus escuchas

| Puerto | Proto | Para qué |
|---|---|---|
| 8554 | tcp | RTSP |
| 8888 / 8889 | tcp | HLS / WebRTC |
| 8890 / 8892 | udp | SRT / WebRTC ICE |
| 8000 / 8001 / 8189 | udp | RTP / RTCP |

> `8890` es SRT y **no se usa**: `libsrt 1.4.0` del robot es incompatible con el SRT de
> mediamtx, por eso el robot publica por RTMP. Está en el comentario de `mediamtx.yml`.

### AI-VL y NVR

| Puerto | Proto | Servicio |
|---|---|---|
| 5000 | tcp | Frigate (UI y `/api/robot`, el MJPEG que embebe el dashboard de Splunk) |
| 8971 | tcp | Frigate autenticado |
| 8443 | tcp | AI-VL backend (uvicorn + TLS) |
| 8001 | tcp | AI-VL core |
| 11434 | tcp | Ollama — **solo `127.0.0.1`** |

### Sistema y acceso

| Puerto | Proto | Servicio |
|---|---|---|
| 3389 / 3390 | tcp | RDP de GNOME por Tailscale (headless / espejo) |
| 9749 | tcp | MCP `codebase-memory` |
| **7400 / 7401** | udp | **DDS discovery — inútil hoy**: el robot está en campo y el DDS no cruza L3 |
| 53 · 323 · 631 · 5353 · 3702 | — | DNS, chrony, CUPS, mDNS, WSD |

---

## 3. El robot — Jetson `192.168.123.18`, visto desde HQ como `10.1.254.18`

### Lo nuestro

| Puerto | Proto | Proceso |
|---|---|---|
| **8092** | tcp | **relay de comandos** (`relay_server.py`) |
| **8093** | tcp | **MJPEG** (`mjpeg_server.py`) |
| 22 | tcp | SSH — usuario `unitree` ⚠️ password `123` |

### DDS — lo que hay que saber antes de escribir una regla de firewall

Los tres binarios nuestros abren **cada uno** su participante DDS:

| Puerto | Proto | Quién |
|---|---|---|
| **7400 / 7401** | udp | `command_sender`, `go2_jpeg_stream`, `telemetry_reader` — los tres |
| efímeros (34129, 48591, 56824, 49621, 50916, 51137…) | udp | uno por participante |

> ⚠️ **Los efímeros cambian en cada arranque.** Una regla de firewall por número de puerto se
> rompe con el próximo reinicio. Y no hace falta ninguna: **el DDS nunca sale del robot** —
> es el principio rector de `RED-Y-DDS.md`. Si alguna vez ves DDS cruzando el IR1101, algo
> está mal configurado.

### De Unitree, no tocar

| Puerto | Proto | Qué |
|---|---|---|
| 80 · 4000 | tcp | servicios de fábrica |
| 111 | tcp/udp | rpcbind |
| 7001 · 12001 · 20543 · 20544 · 46781 | tcp | internos, solo `127.0.0.1` |
| 5353 | udp | mDNS |

---

## 4. Splunk — `192.168.20.200` (`silk-ia-server`)

| Puerto | Servicio |
|---|---|
| 8000 | UI |
| **8088** | **HEC** — acá entra la telemetría del robot, la del Cisco y la de ThousandEyes |
| 8089 | API de administración |

> El **8088 es el único que alguna vez habría que exponer** hacia afuera, y solo si se hace
> la integración oficial de ThousandEyes — que además exige 443 con certificado público, o
> sea un reverse proxy. Ver `LICENCIA-Y-THOUSANDEYES.md` §5. **El 8089 no se expone nunca.**

---

## 5. Puertos que aparecen y no son de nadie del proyecto

Para no perder tiempo la próxima vez que alguien mire un `ss`:

| Puerto | Qué es |
|---|---|
| 5353 / 3702 | descubrimiento de red del escritorio (mDNS, WS-Discovery) |
| 631 | CUPS |
| 323 | chrony |
| 29684 · 32875 | VS Code |
| 9277 | warp-terminal |
