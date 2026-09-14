# Plan de video para teleoperar — lista de trabajo

> Documento único: el plan y el estado real, con casilleros para ir tildando. El detalle de
> cada medición está en [`VIDEO-LATENCIA.md`](VIDEO-LATENCIA.md); acá está el mapa y qué sigue.
>
> Última revisión: **2026-09-14**.

---

## 0. La aclaración que hay que leer antes que nada

**No estamos leyendo el H.264 nativo del robot.** Es fácil creer lo contrario, porque todo lo
que sale del robot hoy es H.264. Lo que pasa en realidad:

```
cámara → videohub del Go2 (GetImageSample, request/response)  ← ~650 ms, ACÁ ESTÁ EL PROBLEMA
       → JPEG
       → nvjpegdec (decodificar en el Jetson)
       → nvv4l2h264enc (re-encodear a H.264)
       → mediamtx → Frigate / drive / YOLO
```

El cambio a H.264 compró **ancho de banda**, no latencia de origen: los ~650 ms ya vienen
dentro del JPEG que pedimos, así que ningún transporte los toca.

El H.264 **nativo** es otra cosa, y **ya está resuelto** (§3): el Go2 lo publica por **RTP
multicast en `230.1.1.1:1720`**, a 720p y 13.9 fps, y leerlo saltea el videohub entero. El
tópico DDS `rt/frontvideostream` que el plan viejo perseguía **no sirve** — ver §3.1.

---

## 1. Dónde está la latencia, medida

| etapa | costo | estado |
|---|---|---|
| **cámara → videohub (`GetImageSample`)** | **~650 ms** | **sin tocar — es el 90%** |
| `mjpeg_server` (el tee en el robot) | 0.2 ms | medido, no es problema |
| cadencia a 5 fps | ~200 ms | perilla: subir `NVR_FPS` |
| presupuesto ARQ de SRT | 150 ms configurados, **1 ms usados** | perilla: bajarlo mucho |
| jitter buffer del browser | 54 ms aislado, 4-26 ms en vivo | perilla: `jitterBufferTarget` |
| mediamtx + WebRTC | ~70 ms | medido, razonable |
| transporte MJPEG (234 KB/cuadro) | **225-240 ms** | eliminado al pasar a H.264 |
| transporte H.264 (~37 KB/cuadro) | ~35 ms | el que corre hoy |

> **Todo lo de abajo del videohub junto suma menos de la mitad que el videohub solo.**

---

## 2. Hecho

- [x] **El robot manda una sola copia.** Verificado: 0 clientes en su MJPEG, **1.40 Mbps** por
      RTMP, mediamtx repartiendo a Frigate y a YOLO. Antes salían las dos cosas.
      La ventaja no es solo 6.7×: **MJPEG escala por visor** (8.9 Mbps cada uno), el H.264 sale
      una vez.
- [x] **Supervisor ciego al encoder** — un crash intermitente mataba la rama para siempre.
      `b48ca3b`. *(El `NVR_ENABLE=0` que encontramos era consecuencia de esto, no la causa.)*
- [x] **`control-rate` ausente** — el encoder ignoraba `bitrate`. `b1289f2`.
- [x] **Base de tiempo `1/1`** — gastaba el bitrate por cuadro: 7.0 Mbps con 1.5 configurados.
      `e551511`.
- [x] **SRT validado punto a punto.** 642 paquetes perdidos, 685 retransmitidos, **0
      descartados**, usando 1 ms de los 150 de presupuesto. *(Hoy corre RTMP; SRT espera LTE.)*
- [x] **`mpegtsmux alignment=7`** — sin eso `srtsink` descartaba los buffers grandes, o sea
      **los keyframes**: 0 IDR contra 8 en 26 s. `c18e7df`.
- [x] **`udpReadBufferSize` en mediamtx** — el salto UDP por loopback perdía el 15% de los
      cuadros. 20 freezes (64 s) contra **0**.
- [x] **Switch MJPEG↔H.264 en la interfaz**, en `/drive` y en Live, contexto compartido con una
      sola conexión WebRTC. *(Mueve la imagen, todavía no YOLO ni el VLM.)*
- [x] **Página de video arreglada**: `Feed the recorder` pasó de slider a On/Off; los `step`
      impedían valores legales (calidad no llegaba a 75, keyframes no llegaba a 15); y la
      página **afirmaba estados que nadie le informó** — ahora dice `unknown`.

---

## 3. La misión principal: el H.264 nativo · **RESUELTA, POR OTRO CAMINO**

### 3.1 El camino DDS es un callejón sin salida

- [x] **La hipótesis central del plan era falsa.** Decía: "el fracaso fue siempre leyéndolo
      desde afuera, nunca adentro, que es un escenario distinto". **Se probó adentro del Jetson
      y falla igual.**
- [x] Medido, en este orden: el tópico **existe** (`/frontvideostream`, entre 121 visibles,
      tipo `unitree_go/msg/Go2FrontVideoData`, 1 publicador **RELIABLE/VOLATILE**); nuestro
      lector **empareja** (la cuenta de suscriptores sube de 1 a 2 al correrlo); **y aun así
      recibe 0 bytes y el callback nunca se ejecuta**.
- [x] No es pérdida ni buffers: subir el socket de recepción a 64 MB y tocar `FragmentSize` no
      cambió nada, y suscribirse **no agrega tráfico** — los datos ya están en el cable.
- [x] La comunidad reporta lo mismo con errores de deserialización
      (`invalid data size`, `unable initialize generic sequence`, `std_bad_alloc`).

> **Conclusión: el problema es la deserialización del mensaje, no la red.** Perseguirlo no vale
> la pena, porque hay un camino mucho mejor.

### 3.2 El camino bueno: RTP/H.264 por multicast · **ANDA**

La documentación de **Multimedia Services** de Unitree indica que el Go2 publica su video como
**RTP/H.264 por multicast en `230.1.1.1:1720`**. No pasa por DDS ni por el SDK.

Medido en el Jetson, 15 s:

```
1280x720   H.264 High profile   206 cuadros = 13.7 fps
7 IDR + 7 SPS + 7 PPS   (keyframe cada ~2 s)   1.69 Mbps   decodifica sin un solo error
```

El pipeline es **passthrough puro**, sin decodificar ni re-encodear nada:

```
udpsrc address=230.1.1.1 port=1720 multicast-iface=eth0
  ! application/x-rtp,media=video,encoding-name=H264,payload=96
  ! rtph264depay ! h264parse config-interval=-1 ! <mux> ! <sink>
```

- [x] **Probado de punta a punta** robot→RTMP→mediamtx: **627 cuadros en 45 s (13.9 fps),
      decodificando limpio, sin huecos**.

### 3.3 Cómo se compara con lo que corre hoy

| | hoy (videohub + re-encode) | multicast nativo |
|---|---|---|
| resolución | 1080p | **720p** |
| cuadros | ~4.7 fps | **13.9 fps** |
| robot→HQ | 1.42 Mbps | **2.02 Mbps** |
| **por cuadro** | 0.30 Mbit | **0.145 Mbit** — la mitad |
| CPU/GPU del Jetson | decode JPEG + encode H.264 | **nada, es passthrough** |
| latencia del videohub | **~650 ms** | **no pasa por ahí** |

**3× los cuadros por 1.4× el ancho de banda**, y el doble de eficiencia por cuadro. El costo es
720p en vez de 1080p, y que **el bitrate lo decide el encoder de Unitree** — supera por poco el
objetivo de 2 Mbps y no tenemos perilla para bajarlo salvo cambiar de escalón.

### 3.4 Lo que falta para ponerlo en producción

- [ ] **Medir la latencia.** Es el número que justifica todo esto y **todavía no está medido**.
      Necesita el NAL SEI o un cronómetro en cuadro con alguien al lado del robot.
- [ ] `run-video.sh`: agregar `SOURCE=multicast`, default `jpeg` hasta validar la latencia.
- [ ] **Conservar `encode_and_publish()` tal como está** — ese supervisor costó dos intentos.
- [ ] `MJPEG_ENABLE=0` en `multicast`, **sin borrar nada**: MJPEG queda como fallback y para
      comparar sobre cable, LTE y Starlink.
- [ ] Extender `tests/test_supervisor.sh` a la fuente nueva.
- [ ] Revisar si hay otros grupos multicast con otras resoluciones o tasas.

> **Sin dueño el binario `go2_h264_stream`**: con este camino ya no hace falta. Decidir si se
> borra o se deja documentado como intento fallido — hoy `build.sh` lo compila siempre, así que
> un error ahí rompe el build de producción.

---

## 4. Las perillas que quedan

- [ ] **Cadencia**: el multicast nativo da **13.9 fps** contra los ~4.7 de hoy, así que la
      cuantización cae de ~200 ms a ~72 ms sin tocar nada más. *(Los "30 fps" que citaba el plan
      viejo eran del tópico DDS y nunca se verificaron; lo medido es 13.9.)*
- [ ] **Jitter buffer del browser** (`jitterBufferTarget`): ~54 ms medidos en aislamiento.
- [ ] **Presupuesto de SRT**: 150 ms configurados, 1 ms usados. **Solo se puede barrer sobre
      LTE** — por cable no hay pérdida que recuperar y cualquier valor daría "sin artefactos".

---

## 5. Pendientes que no dependen de nada

- [ ] **Desplegar el relay nuevo al robot.** Hoy reporta solo `fps`/`width`/`quality` y deja
      **cuatro de siete perillas ciegas** en el panel de video.
- [ ] **Arreglar el lector RTSP del bridge** (ver §6) y recién ahí volver a mover YOLO a H.264.
- [ ] Que el switch mueva también **YOLO y el VLM** (frontend → backend → bridge).
- [ ] `udpReadBufferSize` en el `mediamtx.yml` de **producción**, antes de mover SRT ahí.

---

## 6. Defecto abierto: el lector RTSP del bridge

**Síntoma:** con `STREAM_URL=rtsp://...`, la vista de manejo llegó a **~8 segundos de latencia**
y creciendo. Revertir a `http://<robot>:8093/stream` lo normalizó de inmediato.

**Causa:** `RtspStreamSource` decodifica **todos** los cuadros y solo reenvía los que pasan el
gate de fps. Medido al implementarlo: **3.39 fps leídos de un stream de ~4.5**. Un lector más
lento que la fuente hace que el backlog de ffmpeg crezca **sin límite** — la latencia no se
estabiliza, sube para siempre. `CAP_PROP_BUFFERSIZE=1` no alcanza: para RTSP por FFMPEG
OpenCV suele ignorarlo.

> El dato estaba a la vista cuando lo implementé y lo anoté como "no sé si es el buffer o
> variación de la fuente". Era la señal y la subestimé.

**Arreglo:** drenar con `grab()` —que demuxea sin decodificar— y llamar `retrieve()` solo
cuando se va a reenviar un cuadro. Así el lector nunca queda atrás de la fuente.

- [ ] Implementarlo, y **verificar que lee al ritmo de la fuente** antes de volver a activarlo.
- [ ] Medir la latencia del camino completo antes de dejarlo puesto, no después.

---

## 7. Lo que no se resolvió

- **El double free de `nvv4l2h264enc`** sigue sin causa. Descartados con medición: bitrate, CBR,
  la cadena `nvjpegdec` con bytes reales, decode por software, 4:2:0 vs 4:2:2, restart markers,
  el segmento COM y `rtmpsink` contra dos servidores. Mitigado por el supervisor, no arreglado.
- **Nada se midió sobre LTE ni Starlink.** Todo es cable, o sea el mejor caso.
- **El glass-to-glass absoluto del camino H.264 no es medible en remoto** sin el NAL SEI.
- **Riesgo introducido:** si `NVR_FPS` cambia desde el panel sin reiniciar el servicio, el
  divisor del bitrate queda viejo y el bitrate sale mal por ese factor, sin síntoma visible.

---

## 8. Herramientas

En `robot-video-pipeline/tests/video-bench/` — ver su `README.md`.

| archivo | qué mide |
|---|---|
| `field_probe.py` | rama MJPEG: latencia por etapa, cadencia, frenadas, bitrate |
| `rtmp_bitrate.sh` | robot→HQ real, bytes del socket leídos del kernel |
| `measure.html` + `show.py` | rama H.264 desde el browser: freezes, jitter buffer, pérdida |
| `synthetic_source.py` | aislar una etapa del robot y del enlace |

El método nuevo: `_latency_probe.py` manda sus timestamps en un segmento COM del JPEG, que el
encode a H.264 destruye. El reemplazo es un **código de barras quemado en los píxeles**,
verificado 109/109 tras codificar a 1.5 Mbps.
