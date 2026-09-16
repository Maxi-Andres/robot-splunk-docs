# Registro de mediciones — video del Go2

**Para qué existe:** los números de este proyecto se citaron varias veces como propiedades del
sistema cuando eran fotos de un momento. El caso que lo motivó: *"capacidad observada
0.93 Mbps"* se usó como techo del enlace durante media sesión, y el mismo día el enlace dio
**3.12 Mbps**.

**La regla, entonces:** cada número va con **fecha, hora, configuración y estado del enlace**.
Sin eso no es una medición, es una anécdota. Lo más nuevo arriba.

---

## 2026-09-16 · tarde (LTE bueno) — la sesión que dio vuelta varias conclusiones

**Enlace:** RTT 22.7–63.8 ms (prom. 43.6), **0% de pérdida** en 15 pings.
**Capacidad real: 3.12 Mbps de subida**, medida con `iperf3` robot→HQ **con el video corriendo
encima** (1.67 de iperf + 0.76 del MJPEG + 0.69 del H.264). Es un **piso**, no un techo.
Comando: `iperf3 -s -1 -p 5201` en HQ, `iperf3 -c 192.168.20.99 -p 5201 -t 10` en el robot.

### Los dos caminos, cara a cara

Robot: `SOURCE=jpeg`, `PROTO=srt`, `LATENCY=150`, `BITRATE=800000`, `NVR_FPS=20`.
MJPEG: **480x270, `quality=25`, sin cap** (puesto en vivo con `POST /config` al `:8093`).
Un solo lector de MJPEG (el bridge pasado a `robot=test` durante la prueba).

| | **MJPEG por HTTP/TCP** | **H.264 por WHEP** |
|---|---|---|
| cuadros | **14.31 fps** (la tasa entera de la cámara) | 11.44 fps |
| **latencia absoluta** | **89 ms** p50 · 127 p95 | **~349 ms** |
| banda | 0.77 Mbps | 0.71 Mbps |

**Dos métodos independientes y coincidentes:** el stamp del propio robot (`STAMP=1`, COM del
JPEG, offset de reloj +9 a +11 ms estilo SNTP) da los 89 ms directo; la correlación por
contenido entre los dos streams da el pico en **+260 ms a favor del MJPEG** con **3.7 sigmas,
r=1.00**. El 349 del H.264 es 89 + 260.

### Desglose de esos 89 ms (`field_probe.py`, 503 cuadros en 35 s)

```
cadencia de la fuente (videohub)   73 ms entre cuadros (13.7 fps)
mjpeg_server (resize por HW)       16.8 ms p50   (máx 17.8)
transporte  t_out -> HQ            70 ms p50     (p95 91, máx 354)
cadencia de llegada                72 ms  = la de la fuente: sin ráfagas, sin backlog
frenadas del lector                3 en 35 s (la peor 367 ms), 0 en la fuente
```

Los 70 ms de transporte son **física, no TCP**: ~17 ms de propagación (RTT 33.7/2) + 25-30 ms
de serialización de un cuadro de 6 kB sobre el uplink + encolado. Cambiar de transporte no
toca ninguno de los tres.

### Costo por cuadro del bridge leyendo WHEP (120 cuadros)

```
YUV->BGR      2.0 ms p50      resize a 720p  1.8 ms      JPEG encode  2.0 ms
TOTAL         6.0 ms p50
```

El decode **no** es la latencia del camino H.264. Los 260 ms son el buffer de recepción de SRT
(`msBuf` 92-120 ms medidos, con `LATENCY=150`) más el encode, mediamtx y el jitter buffer.

### Las dos ramas compiten, y está cuantificado

| SRT, ventanas de ~31 s | sin MJPEG | MJPEG 320@5fps | MJPEG 480 sin cap | durante `iperf3` |
|---|---|---|---|---|
| retransmisiones | **7.5%** | 38.4% | — | 23% |
| descartes irrecuperables | **8** | 47 | **433** | 41 |
| bytes útiles | 97% | 81% | — | — |

**Nunca las dos ramas a full sobre el mismo enlace.**

### Después de subir el bitrate del H.264 (23:00, ya con el enlace empeorando)

`NVR_FPS` 20 → **15** (el divisor real; ver abajo) con `BITRATE=800000`:
**40000 → 53333 bits por cuadro (+33%)**, aplicado y confirmado en el log del encoder.

| | antes (22:00) | después (23:00) |
|---|---|---|
| H.264 llegando a HQ | 13.9-14.2 fps | **10.9 fps** ⚠️ |
| SRT | 0.69 Mbps | 0.73-0.82 Mbps |
| `/drive` MJPEG | 11.2-12.2 fps | **13.93 fps** |
| MJPEG en el enlace | 0.77 Mbps | 0.72-0.87 Mbps |
| RTT | 43.6 ms prom. | **77.8 prom., máx 312, mdev 83** ⚠️ |

⚠️ **El H.264 bajó a 10.9, pero el enlace se degradó al mismo tiempo**, así que las dos cosas
no son separables con estos datos. Lo que SÍ está descartado es que sea el robot: el stream
sale a 14.17 fps y `nvr_dropped` quedó clavado en 15 sin crecer. **Queda por re-medir con el
enlace estable.** Sospecha: con congestión el `srtsink` descarta del lado emisor (paquetes que
no entran en el presupuesto de 150 ms), y esas pérdidas **no se ven** en las estadísticas del
receptor — habría que loguear las del robot.

### La aritmética del bitrate, que no es obvia

```
bits por cuadro = BITRATE / NVR_FPS          <- NVR_FPS es DIVISOR, no solo tope
consumo real    = bits por cuadro × 14.3     <- los cuadros que la cámara realmente da
```

Poner `NVR_FPS` por encima de la tasa real **baja el bitrate en silencio**: con 20 contra 14.3
reales, se entrega el 71% de lo pedido. `600000/15` y `800000/20` dan **exactamente los mismos
40000 por cuadro** — por eso "subir el bitrate" el 16-09 a la mañana no cambió nada.

---

## 2026-09-16 · mañana (LTE malo) — `PLAN-VIDEO.md` §6.d

**Enlace:** RTT **165-384 ms**, ~0.93 Mbps **observados** (nunca se saturó para medirlo).

| MJPEG, tope pedido | entrega con RTMP/TCP | entrega con SRT |
|---|---|---|
| 5 fps | 3.85 | 4.56 |
| 10 fps | — | 7.00 |
| 15 fps | **1.90** | **11.00** |

Con TCP, **pedir más entregaba menos**. Config del momento: MJPEG 320x180 `quality=25`,
cuadro de 4.2 kB. `dsack_dups` 41, 42 retransmisiones.

> **Esto NO es una propiedad de TCP ni del sistema: es lo que pasa con RTT 165-384 ms y
> pérdida.** Con el enlace sano (misma tarde) el mismo MJPEG por TCP entrega 14.31 fps a 89 ms.

---

## Herramientas, y una trampa por herramienta

| herramienta | mide | trampa |
|---|---|---|
| `tests/video-bench/field_probe.py` | MJPEG por etapas + latencia absoluta | usaba `read()` en vez de `read1()` y reportaba **436 ms de "transporte"** que eran su propio buffer. **Arreglado 2026-09-16.** Cualquier número de transporte anterior a esa fecha está inflado; los de *tasa* no |
| correlación por contenido (`correlate.py`, `path_race.py`) | cuánto más fresco es un camino que el otro | necesita movimiento en la escena; por debajo de ~3 sigmas no dice nada |
| `iperf3` | capacidad real | satura: el SRT pasa a 23% de retransmisiones mientras dura |
| `ss -tin` / stats de `srt-live-transmit` | banda y pérdida reales | `delivery_rate` con `app_limited` es un piso, no capacidad |
| stamp `STAMP=1` + `/health` del `:8093` | latencia absoluta del MJPEG | el H.264 destruye el stamp: solo sirve para la rama MJPEG |

> ⚠️ **Efecto observador, la trampa que más costó:** `mjpeg_server` manda **una copia completa
> por viewer**. Medir el MJPEG abriendo una segunda conexión mientras el bridge lee **duplica
> la subida** y atrasa justo lo que estás midiendo — dio 618-711 ms y "el MJPEG pierde por
> 110 ms", al revés de la realidad. **Antes de medir, pasá el bridge a `robot=test`.**
