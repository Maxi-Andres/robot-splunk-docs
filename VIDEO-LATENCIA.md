# Video de mínima latencia para teleoperar — estado al 2026-09-11

> Continuación del plan `shimmering-plotting-possum`. Este documento **reemplaza** su
> sección de estado: mantiene el objetivo y el plan A/B, corrige las premisas que la
> medición tumbó, y registra lo que se hizo. Todo número acá es medido; donde algo es
> inferencia, lo dice.

---

## 0. El objetivo no cambió

1. **Mínima latencia SIN pérdida** — plan A, SRT con ARQ.
2. **Mínima latencia con POCA pérdida** — plan B, UDP directo, si A no se consigue.

Inaceptable en los dos casos: el **congelamiento de medio segundo**.
Metas: glass-to-glass < 250 ms · cero huecos > 200 ms en 60 s · robot→HQ < 2 Mbps ·
Frigate grabando · imagen sin bloques persistentes.

---

## 1. Lo que se cerró el 2026-09-11

### 1.1 Paso 1 de Verificación — ¿mediamtx → WebRTC bufferea como Frigate? NO

Medido en **loopback aislado** (gemela de mediamtx v1.19.2 en puertos corridos, sin robot y
sin enlace), una sola fuente sintética alimentando las dos ramas:

| rama | p50 | p95 | max | fps entregado |
|---|---|---|---|---|
| JPEG por HTTP | 7 ms | 8 ms | 10 ms | 15.00 |
| H.264 → RTMP → mediamtx → WebRTC | **127 ms** | 176 ms | 182 ms | 15.00, `framesDropped=0` |

De los 127 ms, **54 ms son el jitter buffer del browser**. Cero freezes, cero pérdida.

**El fantasma de los 7 s era de Frigate, no de mediamtx.** El plan A pasa su compuerta.

### 1.2 Paso 2 — el H.264 ahora sí comprime

Medido **por cable**, robot→HQ, bytes en el socket RTMP:

| | antes | después |
|---|---|---|
| H.264 robot→HQ | 7.0 Mbps | **1.28 Mbps** |
| MJPEG robot→HQ | 8.6 Mbps | 8.61 Mbps |
| **relación** | 1.2× | **6.7×** |
| jitter buffer WebRTC | 43–69 ms | **8–10 ms** |
| freezes WebRTC | 1 (708 ms) | **0** |
| peor hueco WebRTC | 715 / 1002 ms | **279 / 318 ms** |
| frenadas MJPEG > 2× cadencia | 1 (547 ms) | **0** |

El plan prometía *"7× menos datos"*. **Hasta este arreglo eso era falso.** Ahora es 6.7×,
medido, y 1.28 Mbps entra en los 2.8 Mbps del enlace LTE — que era la condición aritmética
de todo el plan A.

### 1.3 Línea de base por cable, 9 minutos continuos (2026-09-11)

Las dos ramas en paralelo, con los tres arreglos puestos. **Las ventanas cortas de 60 s no
mostraban nada; hizo falta el rato largo.**

| | MJPEG `:8093` | WebRTC `:8889/robot` |
|---|---|---|
| cuadros | 2486 en 540 s | 2630 en 540 s |
| fps | 4.60 | 4.62 |
| resolución | 1920×1080 | 1920×1080 |
| bitrate **por visor** | **8.91 Mbps** | **1.39 Mbps** |
| latencia captura→acá | p50 233, p95 328, **max 5231 ms** | sin stamp |
| cadencia | 216 ms | — |
| frenadas | **15** > 2× cadencia, peor **1952 ms** | **3 freezes**, 3.49 s acumulados, peor hueco **1697 ms** |
| **pérdida de paquetes** | — | **0 / 84.898** |
| jitter buffer | — | 7 ms |

#### Hallazgo 1 — MJPEG escala por visor, H.264 no

Contadores de `eth0` del robot durante la misma ventana:

```
robot eth0 TX total : 20.02 Mbps
  de eso RTMP/H.264 :  1.40 Mbps
  MJPEG             :  8.91 Mbps x 2 visores (el bridge + la sonda) = 17.8 Mbps
  suma explicada    : 19.2 Mbps  vs  20.02 medidos   (el resto es SSH y telemetría)
```

**El robot sirve una copia completa del MJPEG a cada visor.** Dos visores son 17.8 Mbps. El
H.264 sale **una sola vez** a 1.40 Mbps y mediamtx lo reparte en HQ, así que no cambia con la
cantidad de visores.

Es un argumento a favor del plan A que el plan **no hace**: la ventaja no es 6.7×, es
6.7× **por visor**. Con el drive abierto y el dashboard de Splunk mirando a la vez, hoy son
dos copias; mañana con un segundo operador serían tres.

#### Hallazgo 2 — el congelamiento está en el transporte, y es reordenamiento, no pérdida

> **Corrección.** La primera lectura de esta línea de base decía "el congelamiento no es del
> enlace" y apuntaba al videohub, porque las dos ramas se frenaban con cero pérdida. Una
> segunda corrida que **separa las etapas** lo desmiente. Queda acá el resultado bueno.

El stamp de `mjpeg_server` trae dos tiempos del robot que nadie había usado. Con ellos, 540 s
y 2539 cuadros estampados:

| etapa | qué es | medido |
|---|---|---|
| cadencia de origen (`t_in`) | el videohub entregando a `mjpeg_server` | 210 ms mediana, **0 frenadas**, peor 289 ms |
| costo de `mjpeg_server` (`t_out - t_in`) | nuestro proceso | **p50 0.2 ms**, max 2.8 ms |
| transporte (`t_out` → acá) | enlace y lectura | p50 229, p95 278, **max 1525 ms** |
| llegada al visor | lo que se ve | 215 ms mediana, **7 frenadas**, peor 836 ms |

**De las 7 frenadas en el visor, 0 coinciden con una frenada de origen.** El videohub es
regular y nuestro tee cuesta dos décimas de milisegundo: **el congelamiento nace aguas abajo
de `mjpeg_server`**.

Y el mecanismo aparece en los contadores TCP de los propios sockets:

```
socket MJPEG :8093  ->  rcv_ooopack: 15455     (sobre 2.59 GB recibidos)
socket RTMP  :1935  ->  rcv_ooopack:  2888
```

**Reordenamiento masivo, por cable.** TCP no puede entregar un byte hasta tener los
anteriores, así que un paquete que llega fuera de orden frena todo lo que venía detrás: es
*head-of-line blocking*, y explica que la rama MJPEG (TCP) se congele mientras la rama
WebRTC (UDP) **no se congela** — 0 freezes y 0 pérdida en 300 s limpios.

**Qué cambia respecto del plan.** El §2.1 tiene razón en la conclusión —hay que salirse de
TCP— pero no en la causa: lo atribuye a **retransmisión por pérdida**, y lo medido es
**reordenamiento sin pérdida** (`packetsLost = 0` sobre 48.397 en WebRTC). La diferencia
importa para elegir: el ARQ de SRT recupera paquetes perdidos, que acá no faltan. Lo que
sirve de SRT contra este síntoma es su **tolerancia al reordenamiento** dentro del
presupuesto de `latency`, no el ARQ. Y el plan B (UDP crudo) también lo evitaría, porque el
problema no es la pérdida sino el orden.

---

## 2. Los tres defectos que encontramos (ninguno estaba en el plan)

### 2.1 El supervisor era ciego a la muerte del encoder · ARREGLADO

La cadena es `go2_jpeg_stream | mjpeg_server | gst-launch`. Cuando `gst-launch` muere,
`mjpeg_server` atrapa el EPIPE, loguea `downstream (NVR) closed` y **sigue vivo a propósito**
— la vista en vivo tiene que sobrevivir. Pero entonces la pipeline de bash nunca termina, el
`while` nunca reitera, y **la rama de grabación queda muerta para siempre** hasta que alguien
reinicia la unidad a mano.

**Esto explica el `NVR_ENABLE=0` que encontramos: era la consecuencia, no la causa.** Alguien
apagó la rama porque no arrancaba. El `BITRATE=60000` es probablemente del mismo episodio.

`encode_and_publish()` ahora supervisa **solo el encoder**: salida 0 es EOS limpio y desarma
hacia afuera; una caída aislada se reintenta con backoff de 2 s; cinco caídas seguidas en
menos de 30 s cada una reconstruyen la captura entera. Probado con stubs sobre el archivo
real: con 3 crashes intermitentes el encoder arranca 4 veces y **la captura 1 sola**.

### 2.2 `control-rate` ausente · ARREGLADO (pero no alcanzaba)

`nvv4l2h264enc` corría en VBR. Se agregó `CONTROL_RATE=1` (CBR). **No bastó**: el bitrate
siguió en 7.2 Mbps.

### 2.3 La base de tiempo era 1/1 · ARREGLADO — este es el que importaba

`fdsrc ! jpegparse` negocia **`framerate=1/1`**, porque un stream JPEG crudo no lleva timing.
El encoder gastaba todo el `bitrate` en **cada cuadro**. La aritmética cierra exacta:

```
1.5 Mbps × 4.7 fps = 7.05 Mbps  ·  medido: 6.97 Mbps
```

O sea `bitrate` **sí** se respetaba, con el reloj equivocado. Y CBR no ayuda porque es
constante contra ese mismo reloj roto.

**Fallaron todas las formas honestas de corregirlo** (GStreamer 1.16, L4T), cada una medida:

| intento | resultado |
|---|---|
| framerate en los caps NVMM | 0 cuadros — `nvvidconv` no convierte framerate, no negocia |
| `capssetter` (join y replace) | **1 solo cuadro**, las dos veces |
| `videorate` en memoria de sistema | 0 cuadros — `Internal data stream error` |
| **`bitrate` dividido por los fps** | **1452 kbps contra 1500, con los mismos 94 cuadros** |

Se implementó la división, **explícita y ruidosa**: `ENC_BITRATE = BITRATE / ENC_FPS`, con el
valor efectivo impreso al arrancar. El divisor sale de `NVR_FPS` cuando `mjpeg_server` es el
tee, de `MAXFPS` si no hay tee, y si no hay ninguno asume 15 **y avisa por stderr**.

> ⚠️ **Riesgo introducido.** Si `NVR_FPS` cambia desde el panel de video sin reiniciar el
> servicio, el encoder sigue con el divisor viejo y el bitrate queda mal por ese factor, sin
> más síntoma que el consumo. O `NVR_FPS` pasa a la sección de "requiere reinicio" del panel,
> o el encoder se reinicia cuando cambia.

---

## 3. Lo que NO se resolvió

### 3.1 El double free del encoder — mitigado, no arreglado

`nvv4l2h264enc` aborta con `free(): double free detected in tcache 2` a intervalos
impredecibles: **2 s, 10 s y más de 20 s** en tres corridas del mismo pipeline. El mensaje
aparece justo después del banner de NVENC porque esa es la última línea que el encoder
imprime; el abort es en su *teardown*, una falla conocida de L4T.

**Descartados con medición**, todos PASS: bitrate (60k/1.5M/2M), CBR, la cadena `nvjpegdec`
con bytes reales de la cámara, `jpegdec` por software, 4:2:0 vs 4:2:2, restart markers, el
segmento COM del stamp, y `rtmpsink` contra dos servidores distintos.

**Candidato vivo:** el `Corrupt JPEG data` cada 20-30 s del journal del robot, que el plan
había puesto fuera de alcance (§6). Encaja con que los tiempos de muerte sean irregulares.

Hoy el supervisor lo convierte en un hueco de segundos en la grabación en vez de una rama
muerta. **La causa sigue abierta.**

### 3.2 Nada se midió sobre LTE ni Starlink

**Todo lo de arriba es por cable, o sea el mejor caso.** Y la rama MJPEG **se congela igual**:
15 frenadas en 9 minutos, la peor de 1952 ms, con cero pérdida de paquetes — por
reordenamiento TCP (§1.3, Hallazgo 2). La rama WebRTC, en cambio, no se congeló en 300 s
limpios.

Sobre LTE hay que esperar pérdida **además** del reordenamiento, y ahí sí el ARQ de SRT
aporta algo que hoy no aporta. Es la medición que falta para decidir entre el plan A y el B.

---

## 4. Premisas del plan: cuáles sobreviven

| premisa | estado |
|---|---|
| El enlace de campo son ~2.8 Mbps (§2.2) | **En pie.** Se midió 15 Mbps un rato, pero **estábamos por cable**, no por LTE. El plan mide ~45 Mbps por cable en §2.1, consistente. |
| MJPEG no entra en el enlace de campo | **En pie y reforzado.** 236 KB por cuadro medidos: a 353 kB/s un solo cuadro tarda **0.62 s**. Ese es literalmente el congelamiento de medio segundo. |
| H.264 da 7× menos datos | **Ahora sí**, 6.7× medido — pero solo después del arreglo 2.3. Antes era 1.2×. |
| mediamtx+WebRTC podría bufferear como Frigate | **Refutada.** ~127 ms, no segundos. |
| `whipsink` no está disponible (inferencia) | **Confirmada.** El robot tiene GStreamer **1.16.3**; `whipsink` llegó en 1.22. |
| El congelamiento lo causa la **pérdida** del enlace | **Corregida, no refutada.** Es del transporte, pero por **reordenamiento**: 15.455 paquetes fuera de orden en el socket MJPEG, con `packetsLost = 0`. Salirse de TCP sigue siendo la respuesta; el ARQ no es lo que la da. |
| El videohub es el cuello de botella del congelamiento | **Refutada.** Cadencia de origen regular, **0 frenadas** en 540 s; `mjpeg_server` cuesta 0.2 ms. Sigue siendo cierto que aporta ~650 ms de latencia, que es otra cosa. |
| La ventaja de H.264 es ~7× | **Es 6.7× POR VISOR.** El robot sirve una copia entera del MJPEG a cada cliente (17.8 Mbps con dos); el H.264 sale una vez a 1.40 Mbps y mediamtx reparte. El plan no hace este argumento y es el más fuerte. |
| La meta "cero huecos > 200 ms" | **Inalcanzable a 5 fps**: 5 fps *son* 200 ms de cadencia. Hoy `MJPEG_FPS=5` y `NVR_FPS=5`. Si WebRTC va a ser la vista en vivo, `NVR_FPS` tiene que subir, y eso multiplica el bitrate. |

---

## 5. Estado del sistema

**El drive sigue 100% MJPEG:** robot `:8093` → `robot_camera_bridge` → backend → canvas del
front. **No hay `RTCPeerConnection` en nuestro código** (verificado: los únicos hits del
workspace están en `node_modules`, son los tipos del DOM de TypeScript). WebRTC existe hoy
solo dentro de mediamtx y en el banco de medición.

`video.env` en el robot:

```
BITRATE=1500000   NVR_ENABLE=1   NVR_FPS=5   MJPEG_FPS=5   MJPEG_WIDTH=0
STAMP=1           MAXFPS=0       IDR_FRAMES=15   PROTO=rtmp   PUBLISH_HOST=192.168.20.99
```

Backup del estado previo en el robot: `robot/video.env.pre-latency-2026-09-11`.

Commits desplegados: `b48ca3b` (supervisor), `b1289f2` (CBR), `e551511` (base de tiempo).

---

## 6. Las herramientas, para no rearmarlas

En `robot-video-pipeline/tests/video-bench/` — ver su `README.md` para cómo correr cada una.

| archivo | qué mide |
|---|---|
| `field_probe.py` | rama MJPEG: latencia por el stamp COM, cadencia, frenadas, bitrate |
| `rtmp_bitrate.sh` | robot→HQ real, bytes del socket RTMP leídos del kernel |
| `measure.html` + `show.py` | rama H.264 vista por el browser: freezes, jitter buffer, pérdida, fps |
| `synthetic_source.py` + `mediamtx-test.yml` | aislar una etapa del robot y del enlace |

**El método nuevo y por qué hacía falta:** `_latency_probe.py` manda sus timestamps en un
segmento COM del JPEG, que el encode a H.264 destruye — solo puede medir la rama MJPEG. El
reemplazo es un **código de barras quemado en los píxeles**, que sobrevive H.264: verificado
**109/109 cuadros** decodificados tras codificar a 1.5 Mbps.

**Tres trampas de método, encontradas a los golpes:**

1. **No medir la salida RTSP con ffmpeg.** Da ~272 ms que son el buffer del propio ffmpeg, y
   se delata leyendo a 17.8 fps mientras recupera backlog en vez de a los 15 reales.
2. **`requestVideoFrameCallback` subcuenta** (marcaba 11.7 de 15 fps) porque coalesce cuando
   dos cuadros caen en el mismo ciclo del compositor. Las latencias valen; el fps no. Usar
   `framesDecoded` de `getStats()`.
3. **Un umbral absoluto de "huecos > 200 ms" no significa nada en una rama capada a 5 fps**,
   donde 200 ms *es* la cadencia. Contar intervalos mayores a 2× la cadencia medida.

---

## 7. Qué sigue

**Hecho:** la línea de base por cable está en el §1.3.

**Hecho también:** la atribución por etapa (§1.3, Hallazgo 2). El congelamiento está en el
transporte TCP, por reordenamiento.

**Lo inmediato, y no necesita LTE:** el reordenamiento de 15.455 paquetes **por cable** no es
normal y no lo explicamos. El plan lo dejó fuera de alcance (§6, "arreglar el enlace"), pero
ahora es la causa medida del síntoma que originó todo. Vale mirar el camino: el túnel del
IR1101, el bonding o el balanceo por si reparte paquetes de un mismo flujo entre rutas.
Si se corrigiera ahí, el MJPEG actual dejaría de congelarse sin tocar nada más.

**Lo que necesita LTE**, cuando vuelva: repetir exactamente esas dos mediciones. Con 1.28 Mbps
la rama H.264 por fin entra en el enlace de campo, así que la comparación es posible por
primera vez.

**Pasos del plan todavía sin empezar:**

- **3** — `srt-live-transmit` en HQ (`apt install srt-tools`, 1.5.4) y el path de mediamtx a
  `udp+mpegts://127.0.0.1:9000`.
- **4-6** — barrer el `LATENCY` de SRT, y el frontend WHEP.

**Antes de montar SRT**, dos cosas deberían decidirse con datos y no con el plan de entrada:

1. **El congelamiento es reordenamiento, no pérdida** (§1.3, Hallazgo 2). De SRT lo que
   sirve contra esto es su tolerancia al reordenamiento dentro del presupuesto de `latency`,
   **no el ARQ**. Conviene decidirlo con ese criterio: si el objetivo es solo dejar de
   congelarse, el plan B (UDP crudo) ya lo consigue y tiene menos piezas. El ARQ recién se
   justifica cuando haya pérdida real, o sea sobre LTE.
2. `NVR_FPS=5` hace que la rama H.264 no pueda ser la vista en vivo (§4, última fila).
   Subirla es condición previa a que WebRTC reemplace al MJPEG, y hay que re-medir el bitrate
   después de subirla — el divisor del §2.3 depende de ese número.

**El punto B del plan (H.264 nativo del `rt/frontvideostream`) sigue siendo el de mayor
techo**, y ahora tiene un motivo más: se saltea el `nvjpegdec` y por lo tanto el double free
del §3.1, además de los ~650 ms del videohub y de la doble copia.
