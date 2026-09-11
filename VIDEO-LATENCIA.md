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

**Todo lo de arriba es por cable, o sea el mejor caso.** Y aun así aparecieron, antes de los
arreglos, una frenada de 547 ms en MJPEG y un freeze de 708 ms en WebRTC **con 6 paquetes
perdidos de 45.054**.

> Que el congelamiento se reproduzca sin LTE es un dato **en contra** de la hipótesis central
> del plan, que lo atribuye a la pérdida del enlace. Vale entenderlo antes de construir ARQ
> para tapar algo que puede no ser pérdida.

---

## 4. Premisas del plan: cuáles sobreviven

| premisa | estado |
|---|---|
| El enlace de campo son ~2.8 Mbps (§2.2) | **En pie.** Se midió 15 Mbps un rato, pero **estábamos por cable**, no por LTE. El plan mide ~45 Mbps por cable en §2.1, consistente. |
| MJPEG no entra en el enlace de campo | **En pie y reforzado.** 236 KB por cuadro medidos: a 353 kB/s un solo cuadro tarda **0.62 s**. Ese es literalmente el congelamiento de medio segundo. |
| H.264 da 7× menos datos | **Ahora sí**, 6.7× medido — pero solo después del arreglo 2.3. Antes era 1.2×. |
| mediamtx+WebRTC podría bufferear como Frigate | **Refutada.** ~127 ms, no segundos. |
| `whipsink` no está disponible (inferencia) | **Confirmada.** El robot tiene GStreamer **1.16.3**; `whipsink` llegó en 1.22. |
| El congelamiento lo causa la pérdida del enlace | **Dudosa.** Se reprodujo por cable con ~0 pérdida. |
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

**Lo inmediato, y no necesita LTE:** dejar corriendo la medición de las dos ramas por cable
para tener la línea de base completa y estable con los tres arreglos puestos. Las
herramientas del §6 ya hacen las dos ramas; falta correrlas juntas en ventanas largas y
anotar el resultado acá.

**Lo que necesita LTE**, cuando vuelva: repetir exactamente esas dos mediciones. Con 1.28 Mbps
la rama H.264 por fin entra en el enlace de campo, así que la comparación es posible por
primera vez.

**Pasos del plan todavía sin empezar:**

- **3** — `srt-live-transmit` en HQ (`apt install srt-tools`, 1.5.4) y el path de mediamtx a
  `udp+mpegts://127.0.0.1:9000`.
- **4-6** — barrer el `LATENCY` de SRT, y el frontend WHEP.

**Antes de montar SRT**, dos cosas deberían decidirse con datos y no con el plan de entrada:

1. Si el congelamiento aparece también sin pérdida (§3.2), el ARQ de SRT no es la respuesta
   a ese síntoma, y la investigación debería ir a la cadencia con que el tee alimenta al
   encoder.
2. `NVR_FPS=5` hace que la rama H.264 no pueda ser la vista en vivo (§4, última fila).
   Subirla es condición previa a que WebRTC reemplace al MJPEG, y hay que re-medir el bitrate
   después de subirla — el divisor del §2.3 depende de ese número.

**El punto B del plan (H.264 nativo del `rt/frontvideostream`) sigue siendo el de mayor
techo**, y ahora tiene un motivo más: se saltea el `nvjpegdec` y por lo tanto el double free
del §3.1, además de los ~650 ms del videohub y de la doble copia.
