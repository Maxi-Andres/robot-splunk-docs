# Registro de mediciones — video del Go2

**Para qué existe:** los números de este proyecto se citaron varias veces como propiedades del
sistema cuando eran fotos de un momento. El caso que lo motivó: *"capacidad observada
0.93 Mbps"* se usó como techo del enlace durante media sesión, y el mismo día el enlace dio
**3.12 Mbps**.

**La regla, entonces:** cada número va con **fecha, hora, configuración y estado del enlace**.
Sin eso no es una medición, es una anécdota. Lo más nuevo arriba.

---

## 2026-09-23 · 13:24–13:45 ART — primera batería completa sobre STARLINK

**Enlace: Starlink, verificado** — `curl https://ipinfo.io/json` desde el robot:
`AS14593 Space Exploration Technologies Corporation`, `customer.bnssarg1.isp.starlink.com`.
Robot en `72fe7b2` (ya con el `RateGate`: pedir 10 fps entrega 9.94).

Config: MJPEG 480x270 `quality=25` `fps_cap=10`; `/h264` todo-intra **QP36** 480x270;
NVR por SRT `LATENCY=150`, `BITRATE=800000`, `NVR_FPS=15`. **Las tres ramas corriendo.**

| | **Starlink hoy** | LTE bueno (16-09 tarde) | fijo (21-09) |
|---|---|---|---|
| ICMP (300 pings) min/medio/máx | **19.3 / 59.3 / 379 ms**, mdev 67 | 22.7 / 43.6 / 63.8 | 3.4 / 7.1 / 11.7 |
| pérdida ICMP | **4%** | 0% | 0% |
| RTT `/cmd` (10 `keepalive`) | **mediana 178, media 239, máx 556 ms** | — | 6.3 ms (cable) |
| `iperf3` robot → HQ, **con el video encima** | **2.62 Mbps**, 108 retrans | 1.67 (+1.45 de video) | 41.5 |
| `iperf3` HQ → robot | **4.39 Mbps**, 16 retrans | — | 89.0 (cable) |
| **MJPEG** latencia captura → HQ | **~74 ms p50** (20 robot + 54), p95 ~225, máx 585 | 89 p50 | 23 |
| MJPEG cuadros / banda / peso | 9.94 fps · 1.47 Mbps · **18 KB** | 14.31 · 0.77 · 6 KB | 7.12 · 0.55 · 9 KB |
| **H.264 manejo** (`/ws/view-h264`, `X-Capture`) | **89 p50 · 356 p95 · 1034 máx** | — | 38 p50 · 40 p95 |
| H.264 manejo cuadros / banda / peso | 14.23 fps · **2.52 Mbps** · **22 KB** | — | 14.3 · 0.61-0.69 · ~5 KB |
| **SRT del NVR**, ventanas 30-60 s | **28-56% retrans, 45-666 descartes** | 7.5%, 8 descartes | 0 descartes |
| **NVR llegando a HQ** | **2.46 fps**, huecos de hasta 1.3 s | 13.5-14.2 | 8.7 (gate viejo) |

**Lectura:** el enlace no está saturado de latencia sino de **pérdida**. Las dos ramas por
TCP (MJPEG y H.264 de manejo) sobreviven con p50 razonable y colas largas (p95 225-356 ms);
la que se cae es **la que depende de UDP con presupuesto de 150 ms**: SRT no llega a
retransmitir a tiempo sobre un RTT medio de 59 con picos de 379, y el NVR queda en 2.5 fps.

**Presupuesto:** MJPEG 1.47 + H.264 manejo 2.52 + NVR ~0.5-0.9 = **~4.5-4.9 Mbps de
video**, más los 2.62 que `iperf3` todavía pudo meter encima. Es un **piso** de ~7 Mbps de
subida, no un techo, y es la primera vez que el video — y no el enlace — es lo que más
ocupa: **las tres ramas a full sobre el mismo enlace, que es justo lo que no hay que hacer.**

> ⚠️ **La escena de hoy es mucho más cara que la de las mediciones anteriores, y eso NO es
> Starlink.** El JPEG pesa 18 KB (era 6-9) y el intra de QP36 pesa **22 KB** (era ~5). A QP
> fijo la banda sigue a la escena: sin anotar dónde estaba mirando la cámara, la columna de
> banda **no es comparable** entre filas. La de latencia y pérdida sí.

> ⚠️ **No es "misma hora, mismo punto" que LTE** (ROADMAP §10): LTE se midió el 16-09 a la
> tarde/noche, esto el 23-09 al mediodía. Para una comparación con las variables aisladas
> hay que alternar LTE ↔ Starlink en la misma sesión.

### ⚠️ Trampa encontrada: el offset de reloj de `field_probe.py` se rompe sobre Starlink

La primera corrida del MJPEG dio **522 ms p50 / 1787 p95 de transporte**. Era el
instrumento: la estimación del offset robot−HQ salió **+441 ms**, cuando el valor real es
**+10 a +16 ms** (medido tres veces esta misma sesión, y coincide con el histórico +9/+11).
Sobre este enlace **las muestras individuales de offset van de −8 a +1032 ms**, y la
**mediana de 9** que usaba la herramienta cayó en la cola.

Arreglado en `field_probe.py`: ahora toma **la muestra de menor RTT** de 21 (el error de
una muestra está acotado por su RTT/2 — la regla de NTP). La segunda corrida, con el offset
bueno, es la que va en la tabla. **Mismo patrón sin arreglar** en `yolo_cost.py:31` y
`tests/_latency_probe.py:65`.

> **Regla:** sobre un enlace con jitter, **mirar el offset antes de creer la latencia**. Un
> offset fuera de +9/+16 ms es la herramienta, no la red.

### 13:39–13:48 — sacar el MJPEG del enlace y bajar el H.264 de manejo a QP40

Dos cambios **en vivo, sin persistir** (un reinicio los deshace):

1. **MJPEG fuera:** bridge `POST /stop` + `/config {"robot":"test"}` en `:8091`
   (`clients: 0` en el `:8093`). ⚠️ **Deja sin cuadros a YOLO y al VLM**, que comen del bridge;
   la imagen de manejo pasa al transporte `intra` del front.
2. **`h264_qp` 36 → 40**, por SSH a `127.0.0.1:8093/config` — el `:8093` ahora sólo acepta
   localhost, y el `/video-config` del relay **todavía no deja pasar `h264_qp`** (acepta
   `bitrate, fps, idr, maxfps, nvr, quality, width`).

| | las 3 ramas, QP36 | sin MJPEG, QP36 | **sin MJPEG, QP40** |
|---|---|---|---|
| H.264 manejo, banda / peso | 2.52 Mbps · 22 KB | 2.34-2.61 · 22-23 KB | **0.86 Mbps · 7.6 KB** |
| H.264 manejo, latencia p50 / p95 | 89 / 356 | 80-123 / 1185-2477 | **79 / 225 ms** |
| H.264 manejo, cuadros | 14.23 | 13.50-14.03 | **14.05 fps** |
| **NVR llegando a HQ** | 2.46 fps | 5.80 | **11.74 fps** |
| SRT retrans / descartes por ventana | 28-56% · 45-666 | 45-56% · 104-640 | **4.8% · 27** (73 s) |

**Sacar el MJPEG solo no alcanzó**: el que pesaba era el intra a QP36 sobre esta escena (22 KB
por cuadro, más que el JPEG). **QP40 lo bajó a un tercio** y con eso el NVR se recuperó solo.

### Los cortes que quedan son del enlace, y vienen en racimos de Starlink

150 s de la rama de manejo con QP40: **10 cortes de más de 250 ms, los 10 del enlace** (la
marca de captura avanza normal, 58-143 ms, mientras la llegada se frena 250-916 ms). **7
de los 10 en 11 s** arrancando en el segundo :27, y otros en :57.2 y :27.6 — bordes del
ciclo de reasignación de satélite de Starlink (cada 15 s, en :12/:27/:42/:57).

El mecanismo: pérdida en ráfaga → TCP congela todo el stream hasta retransmitir.
`ss -tin` del `/h264` en el robot: **cubic, `rto:252`, `retrans 0/4492`**, `dsack_dups 42`.
**BBR no está disponible** en el kernel del Jetson (`available: reno cubic`, sin `tcp_bbr.ko`).
TLP y RACK ya están activos (`tcp_early_retrans=3`, `tcp_recovery=1`).

### 13:50 — cómo pierde Starlink los datagramas (la medición que decidió el diseño UDP)

60 s de UDP robot → HQ `:8895`, **mismo tamaño y cadencia que la rama de manejo a QP40**
(7 × 1200 B cada 70 ms, 854 cuadros, 5978 datagramas). UDP **pasa** del robot a HQ por un
puerto nuevo sin tocar nada (el SRT ya lo sugería).

| | |
|---|---|
| datagramas perdidos | **205 / 5978 = 3.43%** |
| cuadros enteros | **89.8%** |
| cuadros con UN solo datagrama perdido (los recupera una paridad) | **4.4%** |
| cuadros irrecuperables | 5.7%, en rachas de **4, 3, 3, 2, 2, 2, 2, 1…** |

**La pérdida viene en ráfagas**: una paridad por cuadro suma 4.4 puntos (→ ~94%), no más. Lo
que cambia de fondo es la forma: la peor racha son 4 cuadros (~280 ms) **salteados**, contra
916 ms **congelado** por TCP. Implementado ese día — `PLAN-VIDEO.md` §6.i.

### Lo que NO se midió hoy

Los pasos 4 y 5 del protocolo de `FRENO-INYECTADO.md` §7 — sniffer durante 30 s de teleop
y **manejarlo para ver si se siente el tirón** — necesitan a alguien al joystick.

---

## 2026-09-21 — los 92 Mbps del bus interno del Go2: qué son y si molestan

El medidor del IR1101 marcaba **97.81 de 100 Mbps** en `Fa0/0/1` (VLAN 123, ROBOT-GO2) y la
pregunta era de dónde salían, porque lo nuestro son ~2.5 Mbps.

**En el Jetson:** RX **92.12 Mbps**, TX 2.47. De los 8478 paquetes/s de entrada, **8317 son
multicast** (98%), de 1358 B promedio. O sea: **no es tráfico dirigido al Jetson ni al router,
es el bus interno del robot inundando el segmento** — PC1, el Jetson y el puerto del IR1101
cuelgan del mismo dominio de broadcast, y el switch del Go2 replica el multicast a todos.

**Desglose por grupo** (uniéndose a cada uno y contando, sin root, 10 s):

| grupo | tasa | qué es |
|---|---|---|
| **`239.255.0.1:7401`** | **90.98 Mbps** | **datos DDS — el 99%** |
| `239.255.0.1:7400` | 0.01 Mbps | descubrimiento DDS |
| `230.1.1.1:1720` | 1.94 Mbps | el H.264 nativo del Go2 |

Cruzado con `CENSO-GO2.md`, los únicos tópicos que pueden pesar eso son los que ese censo
**no midió a propósito** porque ya iban a la denylist: `/utlidar/cloud`,
`/utlidar/cloud_deskewed`, `/utlidar/voxel_map`, `/uslam/*`. **Nada de eso lo consumimos**: la
telemetría usa `/lf/lowstate` y `/lf/sportmodestate`, y el video sale por el videohub.

### Lo que dice el router, y por qué NO hay que correr a arreglarlo

```
Fa0/0/1: 5 minute input rate 97358000 bits/sec, 8808 packets/sec
         86742334 multicast de 89205710 paquetes de entrada
         Input queue 0/375/0/0 (size/max/drops/flushes)   <- CERO drops
show ip igmp snooping querier  -> tabla VACIA
show ip igmp snooping groups   -> tabla VACIA
```

**Cero descartes**, y el video medido en 38 ms con p95 de 40. Lo que falta es **margen, no
rendimiento**. Y hay una corrección importante sobre cómo se arreglaría: **el snooping en el
IR1101 no frena lo que LLEGA** — el que inunda es el switch interno del Go2, y el router sólo
lo recibe. La única palanca por software sería que el IR1101 haga de **querier en la VLAN 123**
(no en la 1: el puerto del robot está en la 123), y **sólo sirve si el switch del Go2 hace
snooping**; si no, seguirá inundando pase lo que pase en el router.

> ⚠️ **Y apagar el LiDAR para bajar el tráfico tiene un costo que no es de red: el Go2 lo usa
> para evitar obstáculos.** Perderlo mientras se teleopera es peor que 91 Mbps que hoy no
> cuestan nada. No se encontró mecanismo verificado para apagarlo: `utlidar/switch` no existe
> en la copia del SDK ni en el fork de ROS2, y PC1 no tiene SSH.

**Hallazgo lateral, y probablemente más urgente que todo esto:**

```
%CDP-4-DUPLEX_MISMATCH: FastEthernet0/0/4 (not full duplex) with 9500-SILK Te1/0/5 (full)
```

`Fa0/0/4` es la **VLAN 40, el uplink** — por ahí sale el tráfico del robot hacia HQ. Un
desajuste de dúplex ahí produce colisiones y degrada de verdad, y **sí está en el camino del
video**. A diferencia del multicast, eso no cuesta ninguna función del robot arreglarlo.

---

## 2026-09-21 — la rama de manejo en H.264, PUNTA A PUNTA hasta HQ

Camino completo andando: robot (`/h264`) → relay del bridge → backend (`/ws/view-h264`) →
cliente en HQ. Medido con la marca `X-Capture` que viaja en cada mensaje, contra el reloj del
robot con offset SNTP — el mismo método que el MJPEG.

| | MJPEG (lo de hoy) | **H.264 todo-intra** |
|---|---|---|
| **latencia captura → HQ** | **24 ms** | **37 ms** |
| cuadros | 10 fps | **14.35 fps** |
| banda | 0.710 Mbps | **0.439 Mbps** |
| peso por cuadro | ~9000 B | **3821 B** |
| cadencia de llegada | 72 ms | **72 ms** — la de la cámara, sin ráfagas ni backlog |

**+13 ms por 43% más cuadros y 38% menos banda.** Los 13 ms son el encode en el robot (15.5 ms
medidos en lockstep: cierra). El decode en el navegador agrega **0.7 ms**.

> **Lo que esto cambia:** la vista de manejo puede dejar de ser MJPEG sin pagar latencia
> perceptible, y con 38% menos banda — que es justo el seguro que hace falta cuando se vuelve a
> un enlace con pérdida, donde el MJPEG se fue a 815 ms sólo porque la escena se puso texturada.

### El QP es un dial entre dos cosas distintas, y las dos cuestan lo mismo en latencia

| | MJPEG | intra **QP40** | intra **QP36** |
|---|---|---|---|
| latencia captura → HQ | 24 ms | **38 ms** | **38 ms** (p95 40, máx 79) |
| cuadros | 10 fps | 14.3 | **14.3** |
| banda | 0.710 Mbps | **0.42** | 0.61-0.69 |

* **QP40 = la misma imagen que el JPEG por la mitad de los bytes.**
* **QP36 = la misma banda que el JPEG, con 43% más cuadros y bastante mejor imagen.**

Cuál conviene lo decide el enlace del día. Hoy sobra: **capacidad medida con iperf3 = 41.5 Mbps
de subida, cero retransmisiones**, así que 2 Mbps entre las tres ramas no lo rozan.

> ⚠️ **La banda a QP fijo SIGUE A LA ESCENA** (0.61 → 0.69 Mbps entre corridas sin tocar nada),
> igual que el JPEG. Es lo correcto —calidad constante, tamaño variable— pero significa que el
> presupuesto hay que pensarlo sobre la escena peor, no sobre la de la oficina.

### ⚠️ Defecto encontrado y arreglado: una cola que se formaba y NO se iba

Al cambiar el QP en vivo la latencia saltó a **288 ms p50, p95 1033, máx 2680** — y **siguió
ahí los 40 segundos enteros de la medición**, entregando con una cadencia perfecta de 72 ms
todo el tiempo.

**No era el QP** (medido dos veces seguidas después, a QP36: 37 y 38 ms). Era el transitorio:
cambiar el QP **reconstruye el hijo gst**, y la ráfaga del preroll dejó al relay cuatro cuadros
atrás — 288 ms a 72 ms por cuadro, la cuenta cierra.

**Lo grave no fue la ráfaga, fue que no se drenara nunca.** El relay leía un cuadro por pasada
y lo reenviaba: con la fuente produciendo a 14 fps y el consumidor consumiendo a 14 fps,
cualquier hueco que se abra se arrastra para siempre. Arreglado: ahora **vacía el socket y se
queda sólo con el último**, la misma disciplina que `Latest` en el robot y `_put_latest` en el
backend.

> **Y la lección de medición:** una **cadencia estable no prueba frescura**. Los 72 ms se veían
> perfectos mientras la imagen tenía 288 ms de atraso. Lo que distingue es la marca de captura,
> no el ritmo de llegada.

**Defecto encontrado al integrarlo, y estaba documentado en el propio repo:** el relay sólo
EMPUJA, pero uvicorn manda pings de keepalive y `websocket-client` contesta PONG **únicamente
mientras algo está bloqueado en `recv()`**. Sin un hilo que drene el socket, el servidor cierra
a los ~20 s y el siguiente `send` falla con *"socket is already closed"* — un bucle de
reconexión que parece un problema de red y no lo es. El productor JPEG de
`robot_camera_bridge.py` documenta exactamente esa trampa; estaba ahí para copiarla y no se
copió. **Y una prueba de 15 s no lo detecta**: hay que medir más que el timeout del servidor.

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
