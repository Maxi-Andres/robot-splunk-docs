# Registro de mediciones — video del Go2

**Para qué existe:** los números de este proyecto se citaron varias veces como propiedades del
sistema cuando eran fotos de un momento. El caso que lo motivó: *"capacidad observada
0.93 Mbps"* se usó como techo del enlace durante media sesión, y el mismo día el enlace dio
**3.12 Mbps**.

**La regla, entonces:** cada número va con **fecha, hora, configuración y estado del enlace**.
Sin eso no es una medición, es una anécdota. Lo más nuevo arriba.

---

## 2026-09-21 — la rama de manejo en H.264 todo-intra, corriendo en el robot

Primera medición de la rama nueva (`/h264` en el `mjpeg_server`, `POST /config {"h264":1}`),
con las otras dos ramas funcionando al mismo tiempo. Enlace fijo, MJPEG en 480x270 cap 10.

| rama | cuadros | banda |
|---|---|---|
| MJPEG (`/drive` hoy) | 9.99 fps | 0.710 Mbps |
| H.264 del NVR (SRT) | 13.2 fps | 0.767 Mbps · 0 descartes |
| **H.264 todo-intra (`/h264`)** | **14.48 fps** | **0.487 Mbps** · 4207 B/cuadro |

**La rama nueva entrega 45% MÁS cuadros por 34% MENOS banda que el MJPEG que reemplazaría**
(14.48 fps a 0.487 Mbps contra 10 fps a 0.710). El peso por cuadro —4207 B— cae clavado sobre
la predicción offline de 4151.

**Qué le cuesta al robot el tercer encode**, que era la duda que frenaba el resto:

| | antes | con la rama nueva |
|---|---|---|
| `work_ms_p50` (resize JPEG) | 12.2 ms | **16.9 ms** |
| `nvr_dropped` | 55 | **55 — sin descartes nuevos** |
| MJPEG entregado | 10 fps | **10 fps** |
| H.264 del NVR | 13.7 fps | 13.2 fps |

**+4.7 ms sobre el resize y nada más.** El NVR no perdió un cuadro. Con la cámara a 70 ms de
período hay margen de sobra para las tres.

Y la instrumentación funciona: **`X-Capture` presente en 175/175 cuadros**, con una edad al
salir del robot de **31 ms** (medido por loopback, así que es captura → disponible en el HTTP
del robot; el equivalente del MJPEG son los ~13 ms del resize).

> **Lo que esto NO mide todavía:** la latencia punta a punta hasta el navegador. Falta el relay
> y el canvas con `VideoDecoder` (PLAN-VIDEO.md §6.g D). Pero el riesgo que frenaba todo —que
> el tercer encode ahogara al robot— queda descartado.

---

## 2026-09-21 — enlace FIJO (no LTE), y el H.264 perdía cuadros DENTRO del robot

**Enlace: `AS16814 NSS S.A.`, fijo.** No es LTE ni Starlink — verificado con
`ssh unitree@10.1.254.18 'curl -s https://ipinfo.io/json'`.
**RTT 3.4 / 7.1 / 11.7 ms**, 0% de pérdida. Diez veces mejor que el mejor LTE que medimos.

Config: MJPEG 480x270 `quality=25` `fps_cap=10`; robot con `NVR_FPS=15`, `BITRATE=800000`,
`LATENCY=150`. El robot **todavía sin** el arreglo del gate (`git log` en `121f1d2`).

### El presupuesto, que era la pregunta

| rama | peso | cuadros | latencia |
|---|---|---|---|
| **MJPEG** (`/drive`) | **0.552 Mbps** · 9 kB/cuadro | 7.12 fps | **23 ms** (13.1 robot + 10 transporte) |
| **H.264** (NVR) | **0.513 Mbps** | 8.7 fps | buffer SRT 129 ms |
| **TOTAL** | **1.07 Mbps = 482 MB/hora** | | |

Cero frenadas en la fuente y cero en el lector; cero descartes de SRT. **23 ms de latencia en
la vista de manejo**, contra 89 ms del mejor LTE y 832 ms cuando se saturó.

> El techo de **3.12 Mbps era de LTE y no aplica acá**. Con RTT de 7 ms este enlace es de otra
> clase; su capacidad **no está medida** todavía.

### ⚠️ Y el hallazgo: el H.264 perdía 40% de los cuadros dentro del robot

La contradicción que lo delató: la cámara entrega **14.3 fps**, el robot **no descarta**
(`nvr_dropped` clavado en 148), SRT reporta **0 descartes**… y llegan **8.7 fps**.

`nvr_offer()` tenía una **segunda copia** de la compuerta defectuosa —la misma que se arregló
el 16-09 en la rama del viewer, que se pasó por alto acá:

```python
if now - _nvr_last < 1.0 / NVR_FPS: return
_nvr_last = now
```

Con `NVR_FPS=15` el hueco mínimo es **66.7 ms** contra una cámara que entrega cada **70 ms**:
tan al borde que el temblor normal de cadencia empuja cuadros por debajo del umbral, y cada
uno se lleva puesto al siguiente. De ahí el 8.7, que es una mezcla de 14.3 y 7.15.

**Y lo causamos nosotros el 16-09.** Con `NVR_FPS=20` el hueco era de 50 ms, holgado contra
los 70, y el H.264 llegaba a **14.2 fps**; lo bajamos a 15 para corregir el divisor del
bitrate y pusimos la compuerta justo en el borde. **Todas las caídas del H.264 que se
atribuyeron al enlace desde entonces —10.9, 9.2, 8.7— empiezan ahí.**

> **El defecto de diseño detrás:** `NVR_FPS` hace **dos trabajos con óptimos opuestos** — es
> el tope de la compuerta (quiere estar holgadamente POR ENCIMA de la tasa de la cámara) y el
> divisor que pre-calcula el bitrate por cuadro (quiere SER la tasa de la cámara). El gate por
> vencimiento elimina el conflicto: un tope de 15 sobre una fuente de 14.3 ahora pasa todo, así
> que el divisor puede ser honesto.

Arreglado con el mismo `RateGate`, + 2 tests. **Requiere `git pull` y restart en el robot.**

---

## 2026-09-16 · noche (LTE, escena nueva) — el MJPEG sin cap se comió el enlace

**Enlace: LTE de Telefónica, NO Starlink.** Se creía que había pasado a Starlink; Splunk decía
"Telefónica" y Splunk tenía razón. Verificado desde el robot:

```
curl https://ipinfo.io/json   ->   "org": "AS22927 Telefonica de Argentina"
```

> **Cómo verificar de qué enlace estás colgado**, porque a ojo no se distingue y el RTT no
> alcanza: `ssh unitree@10.1.254.18 'curl -s https://ipinfo.io/json'`. El ASN lo dice.
> Starlink sería `AS14593 SPACEX-STARLINK`.

**RTT: 23.6 / 79.2 / 451 ms** (min/prom/max), mdev 82, 0% de pérdida en 60 pings. Mucho más
jitter que a la tarde (43.6 de promedio).

Config: robot igual que a la tarde (`SOURCE=jpeg`, `PROTO=srt`, `LATENCY=150`,
`BITRATE=800000`, `NVR_FPS=15`), MJPEG **480x270, calidad 25, SIN CAP** (`MJPEG_FPS=0`).

| | tarde (oficina) | **noche (escena nueva)** |
|---|---|---|
| peso del cuadro MJPEG | 6 KB | **17 KB** |
| MJPEG en el enlace | 0.77 Mbps | **1.73 Mbps** por viewer |
| **transporte del MJPEG** | **70 ms** p50 | **815 ms** p50 (p95 989, máx 1203) |
| `mjpeg_server` | 16.8 ms | 17.1 ms |
| `/drive` | 14.31 fps | 12.54 fps |
| H.264 llegando a HQ | 13.5-14.2 fps | **7.6 fps** |
| frenadas del lector / 35 s | 3 | **24** |
| descartes SRT / ventana | 8 | **42-56** |

**Nada se tocó entre las dos filas.** Lo que cambió es **la escena**: MJPEG no comprime entre
cuadros, así que el peso lo decide el detalle de lo que mira la cámara. A 12.5 fps, 17 KB por
cuadro son 1.73 Mbps de MJPEG más 0.7 del H.264 = **2.4 Mbps sobre un enlace que midió 3.12**.
Al borde, y encolando.

> **`MJPEG_FPS=0` no es una configuración, es una apuesta** a que la escena no se ponga
> detallada. Andaba en la oficina y se rompe afuera **sin que nadie haya tocado nada**. Si la
> rama MJPEG es la vista de manejo, el cap es lo que la protege — y se pone en vivo:
> `POST /config {"fps":N}` al `:8093` del robot, sin reiniciar.

### El mismo enlace y la misma escena, con `fps_cap=10` — y el gate que no entrega lo que dice

Puesto en vivo (`POST /config {"fps":10}` al `:8093`), sin reiniciar nada:

| | sin cap | **cap 10** |
|---|---|---|
| **latencia total del MJPEG** | **832 ms** | **109 ms** (14.3 del robot + 95 de transporte) |
| transporte p95 / máx | 989 / 1203 ms | **135 / 363 ms** |
| MJPEG en el enlace | 1.73 Mbps | **0.86-0.92 Mbps** |
| frenadas del lector / 35 s | 24 | **1** |
| frenadas en la fuente | 17 | **0** |
| H.264 llegando a HQ | 7.6 fps | **10.9 fps** |
| cuadros entregados | 12.54 fps | **7.13 fps** ⚠️ |

La latencia volvió a los ~100 ms de la tarde y **el H.264 se recuperó solo** con la banda
liberada. Pero se pidieron 10 fps y llegan **7.13**, y no es el enlace:

⚠️ **DEFECTO: el gate del `mjpeg_server` sólo puede entregar `fuente/k`.**
`mjpeg_server.py:663` usa una separación mínima estricta:

```python
min_gap = (1.0 / FPS) if FPS > 0 else 0.0
if now - last < min_gap: continue
last = now
```

Con la cámara a 14.3 fps (70 ms) y un cap de 10 (100 ms), el cuadro de los 70 ms llega
"temprano" y se descarta; el siguiente cae a los 140 → **7.15 fps**. Las únicas tasas
posibles son **14.3, 7.15, 4.77, 3.58…** y **cualquier cap entre 7.2 y 14.2 entrega
exactamente 7.15**.

Es el mismo defecto que el bridge ya documenta y arregló en su gate (`_ParamSource._due`:
*"source 14.8 fps, cap 15, delivered 8.3 fps"*); al `mjpeg_server` nunca le llegó. El arreglo
es llevar un vencimiento que avanza un período por cuadro aceptado, en vez de comparar contra
el último aceptado — así acepta 2 de cada 3 y entrega los 10 de verdad. Necesita `git pull` y
restart en el robot.

> ⚠️ **Y hacia atrás:** los números de "cap 5 → llegan 3.85" de la mañana están contaminados
> por esto. La columna "tope pedido" de esa tabla **no es la tasa entregada**, y parte de la
> caída que se atribuyó al enlace era el gate.

### Y la tercera prueba, que es la que gana: bajar la RESOLUCIÓN en vez de capear los fps

Mismo enlace, misma escena, `width=320` y **sin cap** (`POST /config {"width":320,"fps":0}`):

| | 480 sin cap | 480 cap 10 | **320 sin cap** |
|---|---|---|---|
| `/drive` | 12.54 fps | 7.13 fps | **13.94 fps** (la cámara entera) |
| **latencia total** | 832 ms | 109 ms | **92 ms** (14.6 + 77) |
| transporte p95 / máx | 989 / 1203 | 135 / 363 | **102 / 289 ms** |
| peso del cuadro | 17 kB | 15 kB | **9 kB** |
| MJPEG en el enlace | 1.73 Mbps | 0.90 Mbps | **0.93 Mbps** |
| frenadas del lector / 35 s | 24 | 1 | 4 |
| H.264 llegando a HQ | 7.6 fps | 10.9 fps | 9.2 fps |

**Por el mismo ancho de banda: el doble de cuadros y menos latencia.** 0.93 Mbps contra 0.90,
pero 13.94 fps en vez de 7.13 y 92 ms en vez de 109.

El mecanismo es directo: **capear tira cuadros enteros; achicar abarata todos los cuadros.** Y
de 480 a 320 el cuadro cayó de 17 a 9 kB —menos que proporcional a los píxeles— porque lo que
se va primero es el detalle fino del pedregullo, que era el que más bits comía y el que menos
sirve para manejar. El cap, además, arrastraba el defecto del gate (pedir 10 entregaba 7.15);
sin cap ese problema no existe.

> **La regla: con un presupuesto de banda fijo, en una vista para MANEJAR, gastalo en cuadros
> y no en píxeles.** Bajar resolución es la perilla buena; capear fps es la mala.

**El costo:** el H.264 bajó de 10.9 a 9.2 fps, porque el MJPEG volvió a tasa completa y le
disputa el enlace. Recuperarlo sería 256 de ancho o calidad 20 — pero es la rama del NVR, así
que no vale cambiar una vista de manejo de 92 ms por una grabación más fluida.

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
| `curl https://ipinfo.io/json` **desde el robot** | **de qué enlace estás colgado de verdad** | el RTT NO alcanza para distinguirlos: el 16-09 a la noche se creía Starlink y era `AS22927 Telefonica`. Starlink es `AS14593 SPACEX-STARLINK` |

> ⚠️ **Efecto observador, la trampa que más costó:** `mjpeg_server` manda **una copia completa
> por viewer**. Medir el MJPEG abriendo una segunda conexión mientras el bridge lee **duplica
> la subida** y atrasa justo lo que estás midiendo — dio 618-711 ms y "el MJPEG pierde por
> 110 ms", al revés de la realidad. **Antes de medir, pasá el bridge a `robot=test`.**
