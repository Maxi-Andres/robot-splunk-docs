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
- [x] `run-video.sh`: `SOURCE=jpeg|multicast`, default `jpeg` hasta validar la latencia.
- [x] **`encode_and_publish()` intacto** — el supervisor cubre las dos fuentes.
- [x] `MJPEG_ENABLE=0` automático en `multicast` (no hay JPEG que servir), **sin borrar nada**:
      MJPEG sigue entero como fallback y para comparar sobre cable, LTE y Starlink.
- [x] `tests/test_video_source.sh`: verifica que `multicast` **no contenga decoder ni encoder**,
      que `jpeg` tenga el bitrate resuelto, y que una fuente desconocida falle fuerte.
- [x] **Desplegado y medido en producción (2026-09-14).** Antes contra después, mismo robot,
      mismo enlace:

      |               | `jpeg` (videohub) | `multicast` (nativo) |
      |---|---|---|
      | resolución    | 1920x1080         | 1280x720             |
      | cuadros       | 4.5 fps           | **14.25 fps**        |
      | bitrate       | 1.43 Mbps         | 1.78 Mbps            |
      | bits/cuadro   | 0.318 Mbit        | **0.125 Mbit**       |

      **3.2x los cuadros por 1.24x el ancho de banda**, 2.5x más eficiente por cuadro.
- [x] **Frigate sigue grabando bien**: la grabación real es 1280x720 a **14.30 fps**. El
      `camera_fps: 5.1` de su API es la tasa de DETECCIÓN (`detect.fps: 5` en su config), no la
      de grabación — su propio comentario lo dice: *"recording keeps the full stream fps"*.
- [x] **MEDIDO 2026-09-14: los dos caminos tienen la MISMA latencia.** Dos métodos
      independientes, con movimiento en cuadro:
      *(a)* **correlación temporal** de las dos series capturadas en paralelo, que no necesita
      relojes: mejor alineación **-10 ms**, meseta plana de -60 a -10 ms, pico de **3.2 sigmas**
      (confiable). A 13.9 fps un cuadro son 72 ms, así que el método resuelve de sobra una
      diferencia de 650 ms — **no está**.
      *(b)* el **mismo reloj leído por los dos caminos** en cuadros que llegaron en el mismo
      milisegundo: los dos marcan `48:24.4xx`.

      > **El H.264 nativo NO sacó los ~650 ms.** Como los dos caminos son iguales, esa latencia
      > está **aguas arriba de los dos** — en la cámara, el ISP o el encoder del propio robot —
      > y ningún cambio de fuente ni de transporte la toca.

- [ ] ⚠️ **Y los ~650 ms del videohub quedan EN DUDA como número.** Se midieron comparando un
      cronómetro en pantalla contra el `t_in` del robot, que es exactamente el método que hoy
      falló dos veces: el reloj de la notebook dio **-710 ms** en un intento y **-1400 ms** en
      otro — los dos **negativos** (imposible) e **inconsistentes entre sí**. Un desfasaje de
      reloj de ese orden explicaría el "650 ms" entero. **Hay que re-medirlo bien antes de
      seguir persiguiéndolo.**
- [ ] **Cómo medirlo sin depender de ningún reloj:** mostrar el cronómetro **y el video** en la
      MISMA pantalla, y sacar una foto de las dos cosas juntas. La diferencia entre los dos
      números es el glass-to-glass, sin ninguna suposición sobre relojes. Es lo único que
      faltó hoy.
- [x] Intentos que fallaron antes de llegar a esto, anotados para no repetirlos:
      *(a)* cronómetro en cuadro: los dígitos de milisegundos salen un borrón ilegible, y
      comparar contra el reloj de la notebook dio **latencia negativa** — su reloj no está
      sincronizado con el nuestro, y el offset por `ssh` tampoco sirve porque la conexión
      tarda cientos de ms.
      *(b)* correlación temporal de los dos caminos, que no necesita relojes: el pico salió con
      **1.7 sigmas y la curva plana en ±70 ms** — la escena estaba demasiado quieta.
      **Lo que falta es movimiento en cuadro durante la captura**, y el método (b) da el número
      sin depender de ningún reloj.
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
- [x] **Arreglar el lector RTSP del bridge** (ver §6). Hecho: no era el lector, era el
      `STREAM_URL` apuntando al MJPEG apagado. Queda mover YOLO y el VLM (abajo).
- [ ] Que el switch mueva también **YOLO y el VLM** (frontend → backend → bridge).
- [ ] `udpReadBufferSize` en el `mediamtx.yml` de **producción**, antes de mover SRT ahí.

---

## 6. El lector RTSP del bridge · **CERRADO 2026-09-14 — no era lo que parecía**

**El síntoma fue real**: con `STREAM_URL=rtsp://...` la vista de manejo llegó a ~8 s y
revertir al MJPEG lo normalizó. **La causa anotada no.** Se volvió a medir en serio y el
lector **no acumula**.

### El número "3.39 de 4.5" / "12.91 de 14" era un artefacto de arranque

Medido contra el stream vivo, tres corridas idénticas:

```
abrir VideoCapture        2.35 s   (handshake RTSP + probing)
primer cuadro tras abrir  2.39 s   (espera el primer IDR; el keyframe va cada ~2 s)
régimen                  14.23 fps  ← exactamente la tasa de la fuente
```

Son **4.7 s de transitorio**. Contados dentro de un promedio acumulado desde que arranca el
proceso, sobre una ventana de ~50 s dan `14.23 × (50.7-4.7)/50.7` = **12.91 fps**. Ese es el
número del handoff, al decimal. La tasa instantánea entre baldes de 10 s es 14.1 / 14.2 /
14.3 / 14.2 / 14.2 — la de la fuente, siempre.

### Lo que sí mide si un lector se atrasa

fps promedio no distingue "la fuente va lenta" de "me estoy quedando atrás": los dos dan
bajo. Lo que distingue es la **deriva**, y no necesita sincronizar ningún reloj:

```
deriva = (segundos monotónicos nuestros desde el primer cuadro)
       - (segundos de presentación del stream sobre esos mismos cuadros)
```

Las dos series arrancan en el mismo cuadro, así que el offset desconocido entre relojes se
cancela y solo se comparan las **velocidades**. Plana = al día. Creciente = se acumula, y
crece exactamente lo que se está acumulando.

### Resultados

| prueba | resultado |
|---|---|
| `read` viejo, 60 s | 13.68 fps, deriva **+0.0 ms/s** |
| `grab`/`retrieve`, 60 s | 13.68 fps, deriva **+0.0 ms/s** |
| hilo drenador aparte, 60 s | 13.70 fps, deriva **+0.0 ms/s** |
| camino completo con envío por WS, 180 s | 14.05 fps, deriva **+0.0 ms/s** |
| demora de **12 s** inyectada | pico +5001 ms, **recuperado en < 2 s** |
| bridge en producción, ~5 min | `lag_s` 0.00-0.01, **pico 0.10 s** |

**Por qué no se atrasa, y esto es lo que hay que entender**: mediamtx corre en **esta misma
máquina** y el devcontainer es `network_mode: host`, así que el salto RTSP es **loopback y
nunca cruza el enlace del robot**. Decodificar 720p H.264 acá drena un backlog varias veces
más rápido que tiempo real — por eso 12 s de demora se recuperan en 2.

### Lo que se cambió

- [x] `grab()`/`retrieve()` **se queda**, pero por otro motivo: en el backend FFmpeg de
      OpenCV **`grab()` igual decodifica**; `retrieve()` es solo la conversión YUV→BGR. O sea
      que ahorra la conversión y el encode JPEG de los cuadros descartados —el encode bajó de
      12.91 a 4.28 fps con gate de 5— pero **no acelera el consumo**, y medido no movió la
      tasa consumida ni un poco (13.7 en los dos casos). El comentario del código decía lo
      contrario; está corregido.
- [x] **Guardián de atraso en `RtspStreamSource`.** Calcula la deriva de arriba por cuadro,
      avisa al pasar 1 s (con throttle de 30 s) y **expone `lag_s` / `lag_peak_s` en
      `/status`**, para no tener que entrar al contenedor a leer logs. El pico **no** se
      borra al reconectar: es el único registro de que el stream anterior salió mal.
- [x] `tests/test_rtsp_lag.py` — 5 tests del guardián, sin sockets ni robot.
- [x] `tests/reader_bench.py` — puntúa **cualquier** candidato contra su fuente antes de
      activarlo: estrategias `read`/`grab`/`thread`/`gst`/`ws`, demora inyectable
      (`--stall-at`/`--stall-for`) y perfil de deriva por balde (`--profile --bucket`).
- [x] `.env`: `STREAM_URL` pasa a `rtsp://127.0.0.1:8554/robot`. **Esto era lo único que
      tenía roto a `/drive`**: apuntaba al MJPEG del robot, que `SOURCE=multicast` apaga. El
      log del bridge lo decía literal, en bucle: `stream http://10.1.254.18:8093/stream
      failed: <urlopen error timed out>; retry in 1s`.

### GStreamer con `appsink drop=true max-buffers=1`: **NO hace falta**

Era el arreglo anotado. Verificado en el contenedor: OpenCV trae `GStreamer: YES (1.19.90)`,
pero **solo están `libgstcoreelements` y `libgstcoretracers`** — faltan `appsink`, `rtspsrc`,
`h264parse` y `avdec_h264`, o sea `gst-plugins-{base,good,bad,libav}` enteros. Aplicarlo pide
rebuildear la imagen del devcontainer **para un problema que no reproduce**. La estrategia
`gst` quedó implementada en el banco: el día que el lector se atrase de verdad, se instalan
los plugins y se la mide contra las otras con un comando.

> **La regla sigue en pie, y es la que ordenó esta medición**: cualquier lector *pull* más
> lento que su fuente acumula sin límite. Lo que cambió es cómo se comprueba —
> `tests/reader_bench.py` antes de activar, `lag_s` en `/status` después— porque **fps
> promedio no sirve para comprobarlo** y fue justamente lo que hizo perder el tiempo.

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

Y en `unitree_ros2/robot_camera_bridge/tests/`:

| archivo | qué mide |
|---|---|
| `reader_bench.py` | **si un lector le sigue el ritmo a su fuente**, por deriva y sin relojes |

```
python3 tests/reader_bench.py --url rtsp://127.0.0.1:8554/robot --seconds 180 --fps 15 grab
python3 tests/reader_bench.py ... --stall-at 30 --stall-for 12 --profile --bucket 2 grab
python3 tests/reader_bench.py ... all        # read | grab | thread | gst  (`ws` es opt-in:
                                             # manda cuadros de verdad al backend vivo)
```

La columna que decide es **`drift ms/s`**: ~0 sigue el ritmo, positivo acumula sin límite.
`consumed fps` **no** decide nada por sí sola — es la que hizo perder el tiempo, porque un
promedio acumulado se come el transitorio de arranque y parece un déficit.

El método nuevo: `_latency_probe.py` manda sus timestamps en un segmento COM del JPEG, que el
encode a H.264 destruye. El reemplazo es un **código de barras quemado en los píxeles**,
verificado 109/109 tras codificar a 1.5 Mbps.

---

## 9. Estado exacto al 2026-09-14, para retomar sin contexto previo

### Qué está corriendo ahora mismo

**En el robot** (`10.1.254.18`, Jetson, `robot-video`, commit `a603628`):

```
SOURCE=multicast    ← el H.264 nativo, passthrough, sin decodificar ni encodear
PROTO=rtmp          MJPEG_ENABLE=1 (pero multicast lo apaga solo)
NVR_FPS=5   MJPEG_FPS=5   BITRATE=1500000   STAMP=1   MAXFPS=0
```

El pipeline vivo es `udpsrc 230.1.1.1:1720 ! rtph264depay ! h264parse ! flvmux ! rtmpsink`.
1280x720, ~14 fps, 1.78 Mbps hacia HQ.

**En HQ:** mediamtx de producción con el path `robot`, Frigate grabando (720p, 14.3 fps
reales; su `camera_fps: 5` es la tasa de DETECCIÓN, no la de grabación), y el bridge
`robot_camera_bridge` corriendo en el devcontainer de `unitree_ros2`.

### Lo que está roto ahora y por qué

~~**`/drive` en MJPEG no muestra nada.**~~ **ARREGLADO 2026-09-14.** Era exactamente eso:
`SOURCE=multicast` apaga `mjpeg_server` y el `.env` del bridge seguía apuntando a
`http://10.1.254.18:8093/stream`. Ahora lee `rtsp://127.0.0.1:8554/robot` (mediamtx local,
loopback). Verificado en vivo: **~13.8 fps al backend**, `robot_cam.live: true`, `lag_s` 0.01.
`/drive`, YOLO y el VLM vuelven a tener fuente. Detalle completo en §6.

**Con el botón `H.264` sí se ve**, pero hay que aceptar el certificado **una vez**: la app se
sirve por HTTPS y mediamtx ahora habla TLS en el 8889 con un autofirmado (`CN=mediamtx`, sin
SAN). Abrir `https://<host>:8889/` y aceptar la excepción — el browser la pide **por puerto**.

### Cambios sin commitear

| repo | archivo | qué |
|---|---|---|
| `robot-video-pipeline` | `mediamtx.yml` | `webrtcEncryption: yes` + `auto.crt`/`auto.key`. Sin esto el switch H.264 falla: la página es HTTPS y el browser bloquea contenido mixto. |
| `robot-video-pipeline` | `tests/video-bench/correlate.py` | **nuevo** — compara dos caminos por contenido, sin relojes |
| `robot-video-pipeline` | `tests/video-bench/README.md` | documenta `correlate.py` (§4) y renumera la sección de aislamiento a §5 |
| `unitree_ros2` | `robot_camera_bridge/camera_sources.py` | `grab()`/`retrieve()` (se queda, con el comentario corregido) + **guardián de atraso**: `lag_s`/`lag_peak_s` en `/status` |
| `unitree_ros2` | `robot_camera_bridge/tests/test_rtsp_lag.py` | **nuevo** — 5 tests del guardián, sin sockets ni robot |
| `unitree_ros2` | `robot_camera_bridge/tests/reader_bench.py` | **nuevo** — puntúa un lector contra su fuente antes de activarlo |
| `robot-splunk-docs` | `PLAN-VIDEO.md` | este documento |

`robot_camera_bridge/.env` también cambió (`STREAM_URL` → el RTSP), pero está gitignoreado:
es config local, no entra al commit. `ruff check` limpio y `pytest` en verde (24 pasan, 1
`xfail` que es el techo de buffer del scanner MJPEG, defecto viejo y distinto).

### El lector RTSP: cerrado, y por qué el número engañaba

Ver §6 para el detalle. En una línea: **el lector nunca se atrasó**. Los 12.91 fps eran un
promedio acumulado que se comía los 4.7 s de arranque (2.35 s de abrir el RTSP + 2.39 s
esperando el primer IDR); en régimen consume **14.23 fps, la tasa exacta de la fuente**, con
deriva +0.0 ms/s sobre 180 s y recuperación de una demora de 12 s en menos de 2.

Lo que llevó `/drive` a ~8 s queda **sin causa confirmada**. Hay dos candidatos vivos, los dos
del código de esa época y los dos ya arreglados desde entonces, así que no se puede reproducir
sin volver atrás a propósito:

- el gate `_due()` viejo, que con fuente apenas bajo el tope entregaba la mitad de los cuadros
  (medido entonces: fuente 14.8, tope 15, salida 8.3 fps). Arreglado el 2026-09-11 con
  `_JITTER_TOLERANCE`.
- la fuente de entonces era el camino videohub re-encodeado, con la base de tiempo `1/1`
  (7.0 Mbps con 1.5 configurados) y el 15% de cuadros perdidos en el salto UDP por loopback.
  Los dos arreglados también.

**No vale la pena perseguirlo**: lo que importa es que hoy hay un instrumento que lo habría
visto el primer minuto, y avisa solo.

### Lo que falta medir, y cómo hacerlo bien

**La latencia absoluta sigue sin número confiable.** Todos los intentos de hoy fallaron por la
misma razón: comparar contra un reloj que no es el nuestro.

- Cronómetro en pantalla vs reloj del robot: dio **-710 ms** y **-1400 ms**, negativos e
  inconsistentes. El reloj de la notebook no está sincronizado y además deriva.
- Offset por `ssh`: inservible, la conexión tarda cientos de ms y el punto medio no
  representa cuándo corrió el comando.

**El cronómetro en cuadro queda DESCARTADO para este robot.** No es solo que los dígitos de
milisegundos se borronean: la pantalla **satura la cámara** y no se lee nada útil. Probado.

**Tres alternativas, ninguna necesita leer un dígito ni sincronizar relojes ajenos:**

1. **Control-to-photon — la más práctica, y posiblemente la que más importa.** Mandar un `move`
   por el relay en el instante T (lo controlamos nosotros) y **detectar automáticamente el
   inicio del movimiento en el video** en T'. `T' - T` es lo que el operador realmente siente al
   manejar. No hace falta ninguna pantalla ni reloj externo: los dos extremos son nuestros.
   Incluye la latencia del comando, que se mide aparte por el relay y se resta.

2. **Pantalla que destella, servida por nosotros.** El problema del brillo desaparece si en vez
   de *leer* la pantalla se *detecta un cambio*: una página a pantalla completa que alterne
   negro/blanco en instantes exactos. Como la página la servimos nosotros, **puede sincronizar
   su reloj contra nuestro servidor** — que es justo lo que faltaba. El destello se detecta por
   brillo promedio, y el brillo es lo que sobra.

3. **RTCP Sender Reports del RTP multicast.** Los SR mapean el timestamp RTP al reloj de pared
   del emisor, así que darían la latencia captura→llegada sin nada en cuadro. Es lo más limpio,
   pero hay que confirmar que el publicador del Go2 los emita y resolver contra qué reloj está
   ese emisor (probablemente el controlador de bajo nivel, no el Jetson).

**Recomendación: empezar por la 1.** Es la que no depende de nada externo y mide el número que
de verdad decide si se puede manejar.

### Herramientas que quedaron listas y funcionan

- `tests/video-bench/` — `field_probe.py`, `rtmp_bitrate.sh`, `measure.html`+`show.py`,
  `synthetic_source.py`. Ver su `README.md`, que documenta cuatro trampas de método.
- `tests/test_video_source.sh` — verifica que `multicast` **no contenga decoder ni encoder**.
- `tests/test_supervisor.sh` — el supervisor del encoder, tres escenarios.
- **`unitree_ros2/robot_camera_bridge/tests/reader_bench.py`** — puntúa un lector contra su
  fuente **antes** de activarlo. Ver §8 para los comandos.
- **`lag_s` / `lag_peak_s` en `http://localhost:8091/status`** — el atraso del lector RTSP en
  vivo, sin entrar al contenedor. `0.00-0.01` es lo normal; si sube y no baja, acumula.
- `tests/video-bench/correlate.py` — mide la diferencia de latencia entre dos caminos **por
  correlación de contenido**, sin relojes. Necesita **movimiento en cuadro** o el pico sale
  débil (escena quieta: 1.7 sigmas; con una mano moviéndose: 3.2). Ya está en el repo, con su
  sección en el `README.md` del banco — **sin commitear**.
