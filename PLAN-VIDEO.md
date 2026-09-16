# Plan de video para teleoperar — lista de trabajo

> Documento único: el plan y el estado real, con casilleros para ir tildando. El detalle de
> cada medición está en [`VIDEO-LATENCIA.md`](VIDEO-LATENCIA.md); acá está el mapa y qué sigue.
>
> Última revisión: **2026-09-15**.

---

## 0. La aclaración que hay que leer antes que nada

**MEDIDA EL 2026-09-15: ~100-235 ms, y el "culpable" que este documento daba por sentado no
existía.** El video nunca tuvo un problema de latencia. Lo que tenía segundos de atraso era
**un cliente nuestro**, no el robot.

| camino | glass-to-glass |
|---|---|
| MJPEG directo del robot | **235-300 ms** (instrumentado) |
| H.264 por WebRTC, multicast | **~100 ms** |
| H.264 por WebRTC, videohub | **~200 ms** |
| H.264 leído por OpenCV/RTSP | **2455 ms** ⚠️ |

Todo lo que sigue se escribió antes de saber esto, así que hay que leerlo con esa luz.

**Lo que se creía:** que el videohub del Go2 (`GetImageSample`, request/response) metía
**~650 ms** y era el 90% del problema.

**Lo que se midió (2026-09-14):**

- Ese número salió de comparar un cronómetro en pantalla contra el reloj del robot. El mismo
  método, repetido, dio **-710 ms** y **-1400 ms**: negativos, o sea imposibles, e
  inconsistentes entre sí. **El 650 no es un dato, es un artefacto de reloj.**
- El camino nuevo (H.264 nativo, que **no pasa por el videohub**) y el viejo (videohub +
  re-encode) tienen **la misma latencia**, por dos métodos independientes. Si saltear el
  videohub no cambia nada, el videohub no era el problema.

**Conclusión (cerrada el 2026-09-15): no había ninguna latencia sin ubicar.** Los dos caminos
medían igual porque los dos son rápidos: el total, de la cámara a la pantalla, son ~200 ms.
El "650" era ruido de reloj y nunca hubo nada grande que encontrar aguas arriba.

El H.264 nativo **sí está resuelto** (§3) y compró cosas reales —2.5× menos bits por cuadro,
3.2× los cuadros, el Jetson sin trabajo de encoder— pero **latencia no**. Se lee por **RTP
multicast en `230.1.1.1:1720`**; el tópico DDS `rt/frontvideostream` que el plan viejo
perseguía **no sirve** (§3.1).

---

## 1. Dónde está la latencia

> ✅ **MEDIDO EL 2026-09-15.** El glass-to-glass ya no es una incógnita. Método en §10: una
> página que servimos nosotros, con un reloj y un panel que parpadea, filmada por la cámara
> del robot. Los dos extremos son nuestro reloj, así que no hay nada que sincronizar.
>
> | camino | glass-to-glass |
> |---|---|
> | **MJPEG directo del robot** (lo que usa `/drive` en modo MJPEG) | **235-300 ms** |
> | **H.264 por WebRTC** (WHEP desde mediamtx) | **~100 ms** multicast · ~200 ms videohub |
> | H.264 leído por **OpenCV/RTSP** (lo que hacía el bridge) | **2455 ms** ⚠️ defecto, §6 |
>
> **No hay ninguna latencia de origen inexplicada.** El "tramo desconocido" que este documento
> daba por sentado no existe: la cámara, el robot y el enlace juntos entregan en ~100-200 ms. Lo
> que costaba segundos era **un cliente**, no el robot.

| etapa | costo | estado |
|---|---|---|
| **el tramo de origen (cámara → primer byte que sale del robot)** | **chico** | el total por MJPEG es 235 ms, y eso incluye la cámara, el robot, el enlace y el visor. El "~650 ms del videohub" era un error de reloj (§0) y además no cabe en el total |
| `mjpeg_server` (el tee en el robot) | 0.2 ms | medido, no es problema |
| cadencia a 5 fps | ~200 ms | perilla: subir `NVR_FPS` |
| presupuesto ARQ de SRT | 150 ms configurados, **1 ms usados** | perilla: bajarlo mucho |
| jitter buffer del browser | 54 ms aislado, 4-26 ms en vivo | perilla: `jitterBufferTarget` |
| mediamtx + WebRTC | ~70 ms | medido, razonable |
| transporte MJPEG (234 KB/cuadro) | **225-240 ms** | eliminado al pasar a H.264 |
| transporte H.264 (~37 KB/cuadro) | ~35 ms | el que corre hoy |

> **Ahora la tabla cierra.** Lo medido acá suma menos de 400 ms y el total real es 235 ms por
> MJPEG y ~200 ms por WebRTC, así que no queda nada grande sin explicar. Las perillas de abajo
> son las que quedan, y son chicas — que es una buena noticia: **el sistema ya está en el
> orden de magnitud que hace falta para manejar.**
>
> El control-to-photon del §9 dio ~1076 ms porque incluye la demora mecánica del robot en
> arrancar, que resultó ser de 0.3 a 1.9 s. Ese número mide otra cosa y no contradice a estos.

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

### 3.3 Videohub vs multicast — el cuadro completo, medido el 2026-09-15

|  | **videohub** (`SOURCE=jpeg`) | **multicast** (`SOURCE=multicast`) |
|---|---|---|
| **resolución** | 1920x1080 | 1280x720 |
| **cuadros (H.264)** | **8.5 fps** con `NVR_FPS=15` · 4.5 fps con `NVR_FPS=5` | **14.0 fps** |
| **bitrate robot→HQ** | 0.80 Mbps a 15 · 1.43 Mbps a 5 | **2.12 Mbps** |
| **latencia H.264** (vista del operador) | **~200 ms** | **~100 ms** |
| **latencia MJPEG** (`/drive`) | **235-300 ms** (instrumentado) | — no hay MJPEG |
| **carga del Jetson** | decodifica JPEG + encodea H.264 | **nada, es passthrough** |
| **perilla de bitrate** | `BITRATE`, `NVR_FPS`, `IDR_FRAMES` | **ninguna** — decide el encoder de Unitree |
| **`/drive` en H.264** | anda | **anda** |
| **`/drive` en MJPEG** | **anda** | **muerto** |
| **YOLO y el VLM** | **andan** | **muertos** |
| **Frigate / NVR** | anda | anda |
| **double free del encoder** | posible (mitigado por el supervisor) | **imposible, no hay encoder** |

**Multicast gana en casi todo:** más cuadros, la mitad de latencia, el Jetson libre y el double
free eliminado por construcción. **Pierde en dos cosas, y las dos importan:**

1. **Mata el MJPEG**, y con él `/drive` en modo MJPEG, YOLO y el VLM — porque los tres comen
   del bridge y el bridge se queda sin fuente. El sustituto obvio (leer `rtsp://mediamtx`)
   **cuesta 2.4 s**, ver §6.b.
2. **No tiene perilla de bitrate.** Manda 2.12 Mbps, por encima del objetivo de 2 Mbps, y no
   hay forma de bajarlo salvo cambiar de escalón de resolución. En cable da igual; sobre LTE
   o Starlink es el único número que no se puede negociar.

> **Los 8.5 fps del videohub con `NVR_FPS=15` son el encoder del Jetson ahogándose**, no un
> defecto de configuración: decodificar JPEG y re-encodear a H.264 a 15 fps en 1080p no le
> entra. Bajando a `NVR_FPS=5` queda estable en 4.5 fps. Ese techo es exactamente lo que el
> multicast elimina.

> ⚠️ **Qué está instrumentado y qué no, porque no es lo mismo:**
>
> - **El MJPEG sí**: `latency_clock.py` da **235 ms** y **300 ms** en dos corridas, con picos
>   de 10.9 y 20.9 sigmas. Número firme.
> - **La rama H.264 NO se puede instrumentar desde acá.** El único cliente H.264 disponible
>   para un script es OpenCV, que es justamente el que arrastra el defecto del §6.b: medirla
>   así da 3550 ms con un pico de 3.5 sigmas, y eso **mide el lector, no la rama**. El propio
>   banco lo marca como no citable.
> - **Para el H.264, el navegador es el único instrumento que hay**, y da ~100 ms con
>   multicast y ~200 con videohub, leídos con las dos vistas en la misma pantalla — método
>   inmune a atrasos de display, pero a ojo.
>
> **Esto no se arregla midiendo mejor: se arregla teniendo un cliente H.264 de baja latencia.**
> Hasta que el bridge (o el banco) hable WHEP, la rama H.264 solo se puede estimar mirándola.

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

      > **El H.264 nativo NO sacó los ~650 ms** — porque esos 650 ms no existían (§0). Los dos
      > caminos midieron igual en aquella comparación porque **los dos son rápidos**: medidos
      > el 2026-09-15, uno da ~100 ms y el otro ~200. La diferencia entre ambos es del orden
      > de un cuadro, que es justo lo que aquel método por correlación no podía resolver.

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

### 6.b ⚠️ DEFECTO ABIERTO: leer mediamtx con OpenCV cuesta 2.4 s de retardo FIJO

Descubierto el 2026-09-15, y es el que arruinó `/drive` durante un día entero.

**El síntoma:** `cv2.VideoCapture("rtsp://127.0.0.1:8554/robot")` entrega cuadros que ya tienen
**2455 ms de antigüedad**, sobre el mismo stream que WebRTC entrega en 200 ms.

**Medido de dos formas independientes:**

- Por correlación con el panel que parpadea: 2455 ms, r=0.95, 17-24 sigmas.
- Leyendo el reloj en cuadros de una misma conexión, a ojo:

  ```
  llegó 38.109 → reloj 35.6xx      llegó 43.161 → reloj 40.6xx
  llegó 40.142 → reloj 37.6xx      llegó 50.121 → reloj 47.6xx
  ```

  **Constante desde el primer cuadro. NO se acumula.**

**Lo que NO es** (todo descartado con medición, no con razonamiento):

| descartado | cómo |
|---|---|
| el robot o el enlace | los mismos bytes salen por WebRTC en 200 ms |
| acumulación del lector | idéntico a los 0, 2, 5 y 12 s de la misma conexión; `lag_s` da 0.00 |
| opciones de FFmpeg | `probesize`, `analyzeduration`, `max_delay`, `reorder_queue_size`, `threads`, TCP vs UDP — **ninguna lo mueve**, y se verificó que se aplicaran de verdad |
| `writeQueueSize` de mediamtx | bajarlo a 32 no cambió la latencia **y rompió a Frigate** con artefactos (`reader is too slow, discarding 45 frames`). Revertido |
| caché de GOP en mediamtx | no la tiene para RTSP — [issue #1209](https://github.com/bluenviron/mediamtx/issues/1209) |

**Escala con el cliente, no con el stream.** Tres consumidores del mismo path, a la vez:

| cliente | latencia |
|---|---|
| WebRTC / WHEP (el navegador) | **200 ms** |
| Frigate (su propio ffmpeg) | 1475 ms |
| OpenCV/FFmpeg (el bridge) | **2455 ms** |

> ⚠️ **Y esto es lo importante para el que venga:** `SOURCE=multicast` apaga el `mjpeg_server`,
> así que es tentador apuntar el bridge a `rtsp://mediamtx` para darle una fuente. **NO LO
> HAGAS.** Eso fue exactamente lo que se hizo el 2026-09-14 (commit `791d9d8`) y metió 2.4 s
> en la vista de manejo. Si hace falta que el bridge coma H.264, el camino es **WHEP**, que es
> el que el navegador ya usa a 200 ms.

**Sin causa raíz.** Queda abierto por qué el mismo mediamtx entrega a RTSP/RTMP 2.3 s más tarde
que a WebRTC.

### 6.b.2 ✅ RESUELTO 2026-09-16 — el bridge come H.264 por WHEP

No se arregló el lector de RTSP: **se dejó de usar**. `WhepStreamSource` en
`unitree_ros2/robot_camera_bridge/camera_sources.py` lee el mismo stream de mediamtx por
**WebRTC/WHEP**, que es el camino que el navegador ya usaba a 200 ms. El lector de RTSP sigue
ahí, con la advertencia de los 2455 ms escrita en su docstring, para consumidores de tipo
grabación.

```
.env:  STREAM_URL=https://127.0.0.1:8889/robot/whep    (era http://10.1.254.18:8093/stream)
       STREAM_RESOLUTION=720p   STREAM_FPS=30
```

**Lo que se midió al cambiarlo, mismo robot, mismo enlace LTE:**

| | MJPEG por TCP (antes) | **WHEP** (ahora) |
|---|---|---|
| cuadros al bridge (`/drive`, YOLO, VLM) | 4.5 fps | **11.9 fps** |
| resolución | 320 de ancho | **1280x720** |
| costo para el robot | 0.170-0.253 Mbps de subida | **cero** |
| deriva del lector | — | **0.00 s** (pico 0.16) |
| primer cuadro | — | 0.62 s |

**Y lo que le hizo al H.264, que es la mitad que no se esperaba.** Las dos ramas compartían el
enlace, y la de MJPEG es la que lo rompía:

| SRT, ventana de 31 s | con el MJPEG compitiendo | sin él |
|---|---|---|
| retransmisiones | 29-48% de los paquetes | **7.5%** |
| descartes irrecuperables | 3 → 39 | **8** |
| bytes útiles | 84% de los recibidos | **97%** |

Los 0.17 Mbps del MJPEG no eran caros por su tamaño: eran **TCP sobre un enlace con pérdida**,
y cada retransmisión suya se comía banda que el SRT necesitaba. Por eso el H.264 "empeoraba"
cuando la escena se ponía movida: el JPEG crece con el detalle, el H.264 es CBR y no puede
ceder.

**Trampas que costaron tiempo y conviene no repetir:**

1. **El WHEP de mediamtx es HTTPS, no HTTP.** `webrtcEncryption: yes` está en el yml desde
   siempre; un POST a `http://...:8889` devuelve **400 Bad Request** sin explicar nada. El
   README de `video-bench` todavía dice `http://` en su ejemplo.
2. **La perilla de fps del bridge se volvió el límite.** Con `STREAM_FPS=15` reenviaba 7.9 fps
   de una fuente de 13.5. WebRTC entrega **en ráfagas** y la compuerta exige 0.9 períodos de
   separación mínima (60 ms a 15 fps), así que descartaba el segundo cuadro de cada ráfaga. En
   30 fps: 11.9. **La compuerta tiene que quedar arriba de la fuente, no cerca.**
3. El certificado es autofirmado (`auto.crt`): la verificación TLS queda **prendida** para
   cualquier host remoto y solo se saltea en loopback, donde no hay red que interceptar.
   `STREAM_TLS_CA` fija la CA para un mediamtx en otra máquina.

**Falta:** `aiortc` se instala con pip dentro del devcontainer y **se pierde al reconstruir la
imagen**. `run_camera_bridge.sh` lo verifica y lo instala si falta; lo prolijo es que entre en
el Dockerfile.

> ⚠️ **Estado al cierre del 2026-09-16: el `.env` quedó VUELTO AL MJPEG a propósito**, para
> comparar a ojo la latencia contra el `/drive` en H.264 (que va del navegador a mediamtx
> directo, sin bridge). El código de WHEP está entero y la vuelta es una línea, anotada en el
> propio `.env`.
>
> **La pregunta abierta es la latencia, no los cuadros.** WHEP entrega 2.6× los cuadros, pero
> el camino pasa por el buffer de recepción de SRT: `msBuf` reporta **92-101 ms** con
> `LATENCY=150` en los dos extremos. El MJPEG no pasa por ahí. Lo que NO es: el decode, medido
> en **6.0 ms por cuadro** (YUV→BGR 2.0, resize 1.8, encode JPEG 2.0). Si la diferencia a ojo
> es de ~100 ms, la perilla es `LATENCY`, no el lector.
>
> Y la medición de esa vuelta atrás, mismo enlace, ventanas de 60 y 31 s:
>
> | | WHEP (sin MJPEG) | MJPEG de vuelta |
> |---|---|---|
> | al bridge (`/drive`, YOLO, VLM) | 11.9 fps, 720p | **4.25 fps**, 320 de ancho |
> | H.264 llegando a HQ | 13.5-14.2 fps | **11.4 fps** |
> | MJPEG en el enlace | 0 | **0.365 Mbps** |
> | retransmisiones SRT | 7.5% | **38.4%** |
> | descartes SRT / 31 s | 8 | **47** |
> | bytes útiles SRT | 97% | **81%** |
>
> **Los dos caminos no son independientes: mientras el MJPEG corre, el H.264 que se compara
> contra él está degradado.** Una comparación de latencia a ojo sigue valiendo; una de fps, no.

### 6.b.3 ⚠️ MEDIDO 2026-09-16, y refuta la recomendación de arriba: el MJPEG gana por 260 ms

Con el MJPEG **destapado** (`POST /config {width:480, fps:0}` en vivo al `mjpeg_server`, sin
reiniciar nada) y **un solo lector** —la misma carga que tiene el operador— los dos caminos,
medidos en la misma ventana de 45 s:

| | **MJPEG 480x270 sin cap** | H.264 por WHEP |
|---|---|---|
| cuadros | **14.31 fps** (la tasa entera de la cámara) | 11.44 fps |
| **latencia absoluta** | **89 ms** p50 · 127 ms p95 | **~349 ms** |
| banda | 0.77 Mbps | 0.71 Mbps |

Dos métodos independientes y coincidentes:

* **El stamp del propio robot** (`STAMP=1`, COM del JPEG) da los 89 ms directo, contra el
  reloj del robot con +11 ms de offset medido estilo SNTP. No depende de ninguna correlación.
* **Correlación por contenido** entre los dos streams (`path_race.py`, mismo método que
  `correlate.py`): pico en **+260 ms a favor del MJPEG**, **3.7 sigmas, r=1.00**. De ahí sale
  el 349 ms del H.264, que es 89 + 260.

**De dónde salen esos 260 ms**, y ninguno es el lector: el buffer de recepción de SRT retiene
**105 ms** (`msBuf`, con `LATENCY=150` en los dos extremos), más el encode en el Jetson, más
mediamtx, más el jitter buffer de WebRTC y el decode. El decode del bridge son **6.0 ms**
medidos; no es ahí.

**Por qué esto no contradice al §6.d, y es la parte que importa:** el §6.d se midió con el
enlace a **RTT 165-384 ms y ~0.93 Mbps**, y ahí el MJPEG sobre TCP colapsaba a 1.90-4.5 fps.
Hoy el enlace está a **RTT ~45 ms y mueve 1.48 Mbps entre las dos ramas**. **La conclusión
"TCP es el transporte equivocado" vale para un enlace con pérdida, no para cualquier enlace.**

> **La regla que queda, entonces, es condicional y hay que medir el enlace antes de elegir:**
> con el enlace sano, el MJPEG es el camino corto y gana por 260 ms; con el enlace con
> pérdida, el MJPEG se cae solo y WHEP es el único que sigue entregando. Las dos
> configuraciones están a **una línea** del `.env` del bridge.

**Lo que NO se puede hacer es dejar las dos ramas a full a la vez.** Con el MJPEG destapado,
el SRT pierde **433 paquetes irrecuperables cada 31 s (21.7%)** contra 8 sin él: la vista de
H.264 se ve rota justo mientras se la compara. Si el operador maneja por MJPEG, la rama H.264
hay que **bajarla** (es la del NVR, ahí la latencia no importa), no dejarla peleando.

**Efecto observador, para no volver a caer:** `mjpeg_server` sirve **una copia completa por
viewer**. Medir el MJPEG abriendo una segunda conexión mientras el bridge lee duplica la
subida y atrasa lo que se está midiendo — pasó en la primera corrida (latencia 618-711 ms y el
MJPEG "perdiendo" por 110 ms) y se arregló pasando el bridge a `robot=test` durante la prueba.

### 6.c El reescalado del MJPEG costaba 105 ms · **ARREGLADO 2026-09-16, era la cuota de CPU**

Servir el MJPEG a 1080p tal como llega de la cámara cuesta **13.79 Mbps**, que no pasa por un
enlace de campo. Reducirlo a 640 lo deja en **1.56 Mbps** — pero reducirlo costaba **105 ms**.

**No era el reescalado.** El trabajo real, medido en banco sobre el propio Jetson:

```
decode 1080p   20.7 ms
resize a 640    6.1 ms
encode JPEG     2.1 ms
               ───────
               28.1 ms        ...contra 105 ms medidos en producción
```

**Los 77 ms que faltaban eran CPU congelada**, y hay prueba directa del kernel.
`robot-video.service` tenía `CPUQuota=50%` y `Nice=10`, aplicado al **cgroup entero** — porque
`run-video.sh` lanza `go2_jpeg_stream`, `mjpeg_server.py` y `gst-launch` como un solo pipeline,
así que comparten un único presupuesto:

```
nr_periods     30418
nr_throttled   14059      ← el 46% de los períodos, CONGELADOS
throttled_time 1909 s acumulados
```

`CPUQuota` es un techo **absoluto**: cuando el cgroup gasta sus 50 ms de cada 100, CFS frena
todas sus tareas hasta el período siguiente. Un stall de hasta ~100 ms que **no aparece como
espera en ningún lock ni profiler** — por eso el banco medía 28 ms y producción 105.

**Cómo llegamos acá**, que es la parte que conviene no repetir. El comentario del archivo decía:

> *"Video encode is hardware-accelerated, so this stays cheap — but cap it anyway: nothing on
> this machine may compete with the robot's control stack."*

La premisa era cierta mientras el pipeline fue **todo hardware** (`nvjpegdec` → `nvvidconv` →
`nvv4l2h264enc`). El día que se agregó el reescalado con OpenCV —que es CPU— la cuota que
sobraba pasó a estrangular. La intención estaba bien; la forma de expresarla dejó de servir.

**El arreglo: `CPUQuota` → `CPUWeight`.** `CPUWeight` es un peso **relativo**: solo muerde bajo
contención real, que es lo que "no debe competir con el control" significa de verdad. Y el
stack de control no dependía de esta cuota: `robot-telemetry-agent` y `robot-command-relay`
tienen la suya.

| | antes | después |
|---|---|---|
| costo del reescalado | 105.1 ms p50 | **29.4 ms** |
| máximo | 194.1 ms | **34.5 ms** |
| `/drive` | 10.2 fps | **11.8 fps** |
| rama H.264 / Frigate | 1.34 Mbps | sin cambios |
| `nr_throttled` | creciendo | **congelado** |

Los 29.4 ms que quedan son trabajo real (28.1 medidos en banco), no espera.

> ⚠️ **En el robot está aplicado con `systemctl set-property --runtime`, que se borra al
> reiniciar.** Para que quede: `git pull` en el robot y
> `sudo cp robot/robot-video.service /etc/systemd/system/ && sudo systemctl daemon-reload`.

**Descartado con medición:** `cv2.IMREAD_REDUCED_COLOR_2/4/8` parecía el atajo obvio (libjpeg
decodifica en el dominio DCT a escala reducida). **OpenCV 4.2.0 en este Jetson ignora el
flag** — los tres devuelven 1920x1080 y tardan lo mismo. No hay atajo por software.

### El reescalado por hardware · **IMPLEMENTADO Y CORRIENDO 2026-09-16**

`mjpeg_server.py` ahora reescala en los motores NVJPG del Orin NX, por un subproceso
`gst-launch` persistente alimentado por pipes. El proceso Python volvió a ser lo que su
docstring fundacional dice — *"no decode, no re-encode, no resize"*.

| | al empezar | sin la cuota de CPU | **por hardware** |
|---|---|---|---|
| costo del reescalado (p50) | 105.1 ms | 29.4 ms | **12.8 ms** |
| máximo | 194.1 ms | 34.5 ms | **17.8 ms** |
| CPU del `mjpeg_server` | 22.4% | 22.4% | **3.0%** |

**8.2× mejor que al empezar, y el máximo 10.9×.** La instrumentación nueva de `/health` prueba
la atribución sin lugar a dudas: `wait_ms_p50` **0.2**, `work_ms_p50` **12.7**. Ya no queda
nada escondido — es todo trabajo, y el trabajo es hardware.

**Tres trampas que costaron encontrarlas y están cableadas en el código:**

1. **Los caps tienen que ser `video/x-raw(memory:NVMM)`.** Con memoria de sistema el pipeline
   **arranca y produce 0 bytes en silencio** (`not-negotiated`). Validado fuera del servicio
   antes de escribir una línea de Python, que es lo que lo hizo barato de descubrir.
2. **`nvvidconv` no preserva aspect ratio** — hay que fijar ancho **y** alto, los dos pares.
   La altura sale de parsear el marcador SOF del JPEG, sin decodificarlo.
3. **`quality=55` de `nvjpegenc` NO es el `quality=55` de libjpeg.** Usan tablas de
   cuantización distintas: a 55 el cuadro pasó de 20 a 24 KB y el tráfico de 1.56 a 1.94 Mbps.
   **Con `quality=35` vuelve exactamente a 1.56 Mbps.** Ya está persistido en el robot.

**La red de seguridad**, porque este es el camino que mira el operador:

- Cascada `hardware → cv2 → bytes originales`. Ningún nivel puede lanzar hacia afuera.
- **Lockstep, un cuadro en vuelo**: escribir uno, esperar uno. Hace imposible que se acumule
  latencia dentro de GStreamer, que era la única forma en que esto podía salir peor que cv2.
- **Deadline de 200 ms en escritura Y lectura.** Un JPEG de 1080p son ~200 KB y un pipe tiene
  64: escribir a un hijo que no lee **bloquea**. Al vencer se mata el hijo y se pasa a cv2 —
  no se intenta resincronizar, porque el stream ya quedó a destiempo.
- **El stderr del hijo se drena en un hilo**; si no, se llena el pipe y el hijo se cuelga.
- Tras **3 fallos seguidos** el camino de hardware se abandona para siempre en ese proceso.
- **`hw` es parámetro en vivo**: `POST /video-config {"hw":0}` lo apaga desde el relay, **sin
  SSH y sin reiniciar**. Es la reversión más barata posible.

> **Invariante que el código respeta y hay que seguir respetando:** el payload COM del
> `stamp()` sigue siendo `AVL1 <float> <float>`. Tres lectores lo parsean con
> `a, b = body.split()` y un tercer número los rompe **en silencio** — los cuadros pasarían a
> contar como "unstamped". Toda métrica nueva va a `/health`.

## 6.d LTE, medido por primera vez · **el transporte es el problema, no las perillas**

Primera sesión del proyecto sobre LTE (2026-09-16). Enlace: RTT **165-384 ms**, capacidad
observada ~**0.93 Mbps**.

### El hallazgo central: mandar más entrega menos

Curva medida del MJPEG, variando solo el tope de cuadros:

```
cap  3 fps  ->  llegan 2.45      cap  8 fps  ->  llegan 3.35
cap  4 fps  ->  llegan 2.70      cap 15 fps  ->  llegan 1.90
cap  5 fps  ->  llegan 3.85  ← optimo
```

**Y no es ancho de banda.** A 320x180 y calidad 25 el cuadro pesa **4.2 KB**, así que 3.85 fps
son 0.09 Mbps de un enlace que mueve 0.93. Lo que limita es la **pérdida**: `dsack_dups` 41,
42 retransmisiones.

**El mecanismo, que es lo que hay que entender:** el MJPEG va por **una sola conexión TCP**.
Cada pérdida cuesta un viaje de ida y vuelta para retransmitir (~200-380 ms acá), **bloquea
todo lo que venía detrás** (TCP entrega en orden), y encima TCP corta a la mitad su ventana.
Mientras el cliente no lee, el `mjpeg_server` saltea cuadros por diseño. Más cuadros = más
paquetes = más pérdidas = más frenadas.

### La trampa que no tiene salida por configuración

Bajar los fps reduce la pérdida **pero agrega hueco entre cuadros**: a 4 fps cada cuadro está
250 ms del siguiente, así que aunque el transporte fuera instantáneo el operador ve los cambios
con un cuarto de segundo de atraso. **Se cambia una latencia por otra.** No hay valor de
`MJPEG_FPS` que resuelva las dos.

> **La conclusión: sobre un enlace con pérdida, MJPEG sobre TCP es estructuralmente el
> transporte equivocado.** No es cuestión de sintonizarlo mejor. Hace falta uno que **descarte
> y siga** en vez de retransmitir y bloquear.

### El camino SRT ya está construido y nunca se enchufó

Es exactamente el transporte que falta: UDP con ARQ acotado a un presupuesto (`LATENCY=150`),
o sea que recupera lo que entra en 150 ms y **descarta el resto en vez de frenar todo**.

Lo que ya existe, verificado el 2026-09-16:

| pieza | estado |
|---|---|
| `srtsink` en el robot | ✅ presente (libsrt 1.4.0) |
| `PROTO=srt` en `run-video.sh` | ✅ apunta al 8891 por default |
| `srt-bridge` en HQ (`srt-live-transmit`) | ✅ **corriendo hace 2 días**, escucha `srt://:8891?latency=150` y reenvía a `udp://127.0.0.1:9000` |
| el path que ingiere ese UDP | ❌ **SOLO está en el `mediamtx.yml` de PRUEBA** |

La línea que falta en producción, ya verificada contra la doc de mediamtx v1.19.2:

```yaml
  robot:
    source: udp+mpegts://127.0.0.1:9000     # en vez de `source: publisher`
```

El puente existe porque **mediamtx rechaza el handshake de libsrt 1.4.0** del robot;
`srt-live-transmit` sí lo acepta y convierte. Por eso hay tres saltos en vez de uno.

### ✅ PROBADO EL 2026-09-16 SOBRE LTE — funciona, y triplica los cuadros

```
robot (srtsink, libsrt 1.4.0) → LTE → srt-bridge :8891 → udp:9000 → mediamtx → app
```

| | RTMP/TCP | **SRT** |
|---|---|---|
| H.264 | 3.71 fps | **11.00 fps** (3×) |
| `/drive` (MJPEG, sigue por TCP) | 4.00 fps | **4.50 fps** |
| Frigate `skipped_fps` | 2.0 | 0.0-1.1 |
| tráfico total del robot | 0.71 Mbps | **0.74 Mbps** |
| colas TCP en el robot | crecían sin parar | **0** |

**Triple de cuadros por el mismo ancho de banda.** La banda que antes se gastaba en
retransmisiones bloqueantes ahora lleva video.

**Y la curva se dio vuelta**, que es la confirmación de que el diagnóstico era correcto:

| tope pedido | entrega con TCP | entrega con SRT |
|---|---|---|
| 5 fps | 3.85 | 4.56 |
| 10 fps | — | 7.00 |
| 15 fps | **1.90** | **11.00** |

Con TCP, pedir más entregaba menos. Con SRT, pedir más entrega más. **Mismo enlace, mismo
robot, misma hora.**

Las estadísticas del puente muestran el mecanismo:

```json
"recv": {"packets":2707, "packetsLost":15, "packetsRetransmitted":21, "packetsDropped":6}
```

Recupera lo que entra en los 150 ms de presupuesto (21) y **descarta lo que no** (6), en vez
de frenar el stream entero. 0.22% perdido de verdad.

### Lo que hubo que destrabar, para no perder tiempo la próxima

1. **La línea faltante en `mediamtx.yml` de producción.** Estaba escrita y verificada en el
   yml de PRUEBA desde hacía días, y nunca se copió:
   ```yaml
   robot:
     source: udp+mpegts://127.0.0.1:9000     # en vez de `source: publisher`
   ```
   Un path de mediamtx **no puede ser publisher y pull a la vez**: el robot en `PROTO=srt` y
   este `source` van juntos, o el robot en `PROTO=rtmp` y `source: publisher`. Mezclarlos da
   `can't publish to path 'robot' since 'source' is not 'publisher'`.

2. **La instancia de PRUEBA de mediamtx tenía tomado el udp:9000.** Corría desde sesiones
   anteriores con su propio path `srtin` leyendo el mismo puerto, y producción fallaba con
   `bind: address already in use` — un error que no dice nada sobre quién lo tiene. Se bajó.
   Si se vuelve a levantar `mediamtx tests/video-bench/mediamtx-test.yml`, **va a robar el
   puerto otra vez** mientras SRT esté en producción.

### ⚠️ Sin explicar: B-frames al arrancar

Dos veces, **al reiniciar el servicio de video**, mediamtx cerró las sesiones WebRTC con
`WebRTC doesn't support H264 streams with B-frames`. Las dos veces **se resolvió solo a los
~30 segundos** y las sesiones siguientes conectaron bien.

Lo que hace que no cierre: `nvv4l2h264enc` tiene `num-B-Frames` **con default 0** y perfil
**Baseline**, que ni siquiera permite B-frames. Verificado con `gst-inspect`.

Primera hipótesis (el reescalado a 640x360) **descartada**: volvió a pasar cambiando solo fps
y bitrate. Sospecha actual: mediamtx los detecta mal mientras el stream arranca. **No probado.**

> **Si la vista en vivo se congela justo después de reiniciar el video, esperá 30 segundos
> antes de tocar nada.** Y si se quiere blindar, setear `num-B-Frames=0` explícito en
> `run-video.sh` en vez de confiar en el default.

### Defecto encontrado en el camino: B-frames rompen WebRTC

Ajustando el H.264 para LTE (640x360, 10 fps, keyframe cada 1 s, 400 kbps) **la vista en vivo
se congeló**. La causa no era el enlace:

```
[WebRTC] session closed: WebRTC doesn't support H264 streams with B-frames
```

Alguno de esos cambios hizo que `nvv4l2h264enc` emitiera B-frames, y mediamtx cierra la sesión
WebRTC al detectarlos. **Cuál de los cuatro fue, no está determinado** — el sospechoso es el
reescalado, por ser el único que toca la cadena antes del encoder.

> ⚠️ **Restricción que no estaba escrita en ningún lado:** cualquier cambio en el encoder tiene
> que mantener B-frames apagados o la vista en vivo deja de existir. `nvv4l2h264enc` tiene la
> propiedad para forzarlo y **debería estar seteada explícitamente en `run-video.sh`**, en vez
> de depender de un default que evidentemente cambia según otros parámetros.

### Configuración con la que quedó (LTE, 2026-09-16)

```
SOURCE=jpeg   PROTO=srt   LATENCY=150   BITRATE=600000   NVR_FPS=15   IDR_FRAMES=15
MJPEG_WIDTH=320   MJPEG_QUALITY=25   MJPEG_FPS=5
```
más `robot: source: udp+mpegts://127.0.0.1:9000` en el `mediamtx.yml` de HQ.

Rinde: **H.264 1080p a 11 fps**, `/drive` MJPEG a 4.5, total del robot **0.74 Mbps**, colas TCP
en cero y cero descartes en SRT. **Con margen para seguir subiendo.**

> ~~**Lo que queda mal: el MJPEG sigue por TCP**~~ — **hecho el mismo día, ver §6.b.2.** El
> bridge pasó a WHEP: `/drive`, YOLO y el VLM subieron de 4.5 a 11.9 fps y a 720p, el robot
> dejó de mandar la segunda copia, y el SRT bajó de 29-48% de retransmisiones a 7.5%.
>
> Con el MJPEG afuera, **el techo de cuadros pasó a ser la cámara**: el videohub entrega 14.3
> fps y el H.264 llega a HQ a **14.2**. Subir `NVR_FPS` por encima de eso no hace nada; lo que
> queda por gastar es **bits por cuadro**, que hoy son 40 kbit para 1080p (`BITRATE=800000`
> dividido por un `NVR_FPS=20` que no existe). Puesto en 15 —la tasa real— son 53 kbit.

## 7. Lo que no se resolvió

- **El double free de `nvv4l2h264enc`** sigue sin causa. Descartados con medición: bitrate, CBR,
  la cadena `nvjpegdec` con bytes reales, decode por software, 4:2:0 vs 4:2:2, restart markers,
  el segmento COM y `rtmpsink` contra dos servidores. Mitigado por el supervisor, no arreglado.
- **Nada se midió sobre LTE ni Starlink.** Todo es cable, o sea el mejor caso. ⚠️ **Y con
  `multicast` ese riesgo creció**, por dos motivos que conviene tener claros antes de salir
  a campo:
  - **El multicast NO cruza la red, así que NAT no es problema.** `udpsrc` se suscribe al
    grupo en `$NIC`, que es el bus interno del robot (`eth0`, 192.168.123.18/24), y lo que
    sale hacia HQ es el **mismo RTMP unicast sobre TCP de siempre**. Verificado en
    `run-video.sh`: la única diferencia entre las dos fuentes está antes del muxer.
  - **Pero se perdió la perilla.** En `multicast` el encoder es el de Unitree, adentro del
    robot: `BITRATE`, `NVR_FPS`, `MAXFPS` e `IDR_FRAMES` **quedan inertes** (`run-video.sh`
    los pone en 0 para esta fuente). Y manda **más**: 1.78 Mbps contra 1.43, 14.25 fps contra
    4.5. Sobre un enlace con pérdida eso va para el lado equivocado — el congelamiento acá es
    pérdida con retransmisión TCP, y **mandar menos pierde menos**.
  - **La salida es `SOURCE=jpeg`**, que sigue entera y a una variable de distancia. Si un
    enlace de campo empieza a congelarse, ese es el movimiento.
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

Y la más importante, en `robot-video-pipeline/tests/video-bench/`:

| archivo | qué mide |
|---|---|
| **`latency_clock.py`** | **el glass-to-glass absoluto**, sirviendo el reloj nosotros mismos. Ver §9 |
| `control_to_photon.py` | lo que el operador siente al manejar. **No** sirve para latencia de video (§9) |

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

## 9. Estado exacto al 2026-09-15, para retomar sin contexto previo

### La decisión que hay que tomar primero: `SOURCE`

**No hay una configuración que gane en todo.** Las dos están medidas:

| | `SOURCE=jpeg` | `SOURCE=multicast` |
|---|---|---|
| H.264 | 1080p, **8.5 fps**, 798 kbps | 720p, **14.0 fps**, 2.12 Mbps |
| Jetson | decodifica JPEG + encodea | **nada, es passthrough** |
| Frigate / NVR | anda | **anda** |
| `/drive` en **H.264** (WebRTC) | anda, ~200 ms | **anda, ~200 ms** |
| `/drive` en **MJPEG** | **anda, 235 ms** | **muerto** |
| YOLO y el VLM | **andan** | **muertos** |

**Por qué muere la mitad con multicast:** no hay JPEG en ese camino, así que `run-video.sh`
apaga el `mjpeg_server`, y el bridge —que alimenta el modo MJPEG, YOLO y el VLM— se queda sin
fuente. `/drive` en H.264 **no** pasa por el bridge (va por WebRTC directo), por eso sobrevive.

**Los 8.5 fps del modo `jpeg` son el encoder del Jetson ahogándose** a 1080p con `NVR_FPS=15`.
No es un defecto: es que decodificar JPEG y re-encodear a H.264 a 15 fps no le entra.

> **Elegí así:** si vas a teleoperar y querés YOLO o el VLM, `SOURCE=jpeg`. Si solo necesitás
> la vista H.264 y el NVR, `multicast` da más cuadros por menos trabajo.

### Cómo se mide la latencia ahora (y por qué esta vez sí funcionó)

`robot-video-pipeline/tests/video-bench/latency_clock.py`. Sirve una página con dos canales:

1. **Un reloj enorme, blanco sobre negro.** Para leer a ojo, y para el truco de la captura:
   poner la vista del robot y la página en la misma pantalla y sacar UNA foto — la diferencia
   entre los dos relojes es el glass-to-glass, sin suponer nada sobre relojes.
2. **Un panel que parpadea** entre negro y gris medio siguiendo una secuencia que el servidor
   reparte. Se mide por brillo promedio y se correlaciona. **No hace falta leer ningún dígito**
   —que es lo que hundió todos los intentos anteriores, porque los milisegundos salen borrosos—
   ni sincronizar ningún reloj ajeno: el servidor y el lector son la misma máquina.

```bash
python3 latency_clock.py serve --port 8100          # abrir en una pantalla que la camara vea
python3 latency_clock.py measure --seconds 45 \
    --source mjpeg=http://10.1.254.18:8093/stream \
    --source h264=rtsp://127.0.0.1:8554/robot
```

Tres cosas que aprendió a los golpes y están cableadas adentro:

- **Gris medio, no blanco.** Una pantalla blanca satura la cámara; es la otra mitad de por qué
  el cronómetro fracasó.
- **La página reporta su propia sincronización** al servidor por HTTP, y `measure` **se niega a
  dar un número** si no la tiene. Una página sin sincronizar mide *su* reloj y el resultado se
  ve igual de creíble — así salieron los -710 ms y los -1400 ms de antes.
- **Las fuentes `http://` se leen SIN FFmpeg**, con un escáner de JPEG crudo. Medir el MJPEG a
  través de FFmpeg le cobraría el defecto del §6.b, que es del lector y no de la rama.

### Qué está corriendo ahora mismo

**En el robot** (`10.1.254.18`, Jetson, `robot-video.service`):

```
SOURCE=multicast    NVR_FPS=15    MJPEG_FPS=15    BITRATE=1500000    STAMP=1
```

1280x720 a 14.00 fps, **2.12 Mbps** hacia HQ — por encima del objetivo de 2 Mbps del plan, y
sin perilla para bajarlo (§7).

**En HQ:** mediamtx bajo systemd (`robot-video-pipeline.service`), Frigate grabando, y el
bridge en el devcontainer **sin fuente** (multicast apaga el MJPEG).

### Lo que se arregló el 2026-09-15

- [x] **`/drive` volvió a 235 ms** revirtiendo el `STREAM_URL` a `http://<robot>:8093/stream`.
      El cambio a `rtsp://` del commit `791d9d8` metía 2.4 s (§6.b).
- [x] **`robot-video-pipeline.service` estaba en bucle de reinicio con el contador en 17.822.**
      Alguien había levantado mediamtx a mano y la unidad chocaba con el puerto 8000 en cada
      intento, ~1 por segundo. Ahora mediamtx corre bajo systemd y la unidad quedó `active`.
- [x] **Bug del guardián de atraso**: había quedado copiado en `HttpStreamSource` además de en
      `RtspStreamSource` (un `str.replace` sin contar ocurrencias), así que `/status` reventaba
      con `AttributeError: '_lag_peak'` apenas el bridge volvía a MJPEG. Corregido, 24 tests.

### Cambios sin commitear

| repo | archivo | qué |
|---|---|---|
| `robot-video-pipeline` | `tests/video-bench/latency_clock.py` | **nuevo** — el medidor de glass-to-glass |
| `robot-video-pipeline` | `tests/video-bench/control_to_photon.py` | **nuevo** — el método que no sirvió, documentado con su límite |
| `robot-video-pipeline` | `tests/video-bench/correlate.py`, `README.md` | de la sesión anterior |
| `robot-video-pipeline` | `robot/run-video.sh`, `robot/video.env.example` | sacado el "650 ms" falso; anotado que multicast no cruza la red pero pierde la perilla de bitrate |
| `robot-video-pipeline` | `mediamtx.yml` | solo el `webrtcEncryption` de la sesión anterior (el `writeQueueSize` se revirtió) |
| `unitree_ros2` | `robot_camera_bridge/camera_sources.py` | guardián de atraso, sin duplicar |
| `unitree_ros2` | `robot_camera_bridge/tests/` | `test_rtsp_lag.py`, `reader_bench.py` |
| `robot-splunk-docs` | `PLAN-VIDEO.md`, `VIDEO-LATENCIA.md` | estos documentos |

En el robot cambió `video.env` (`SOURCE`, `NVR_FPS`, `MJPEG_FPS`), que es config local y no va
al repo. `robot_camera_bridge/.env` también, por lo mismo.

### La latencia absoluta: RESUELTA el 2026-09-15

Era el pendiente más viejo del documento. **~100-235 ms** según el camino, ver §1. El método y sus tres
trampas están arriba; acá quedan los intentos que fallaron, para no repetirlos:

| intento | por qué falló |
|---|---|
| Cronómetro en pantalla vs reloj del robot | dio **-710 ms** y **-1400 ms**: negativos, o sea imposibles. El reloj de la otra máquina no es el nuestro y además deriva |
| Offset por `ssh` | la conexión tarda cientos de ms y el punto medio no representa cuándo corrió el comando |
| Leer los dígitos de ms en el video | salen un borrón. Los segundos sí se leen, y alcanzan |
| Pantalla blanca | satura la cámara. Gris medio resuelve |
| **Control-to-photon** (mover el robot y detectar el arranque) | la idea es sana pero el marcador no: el robot tarda entre **311 y 1855 ms** en arrancar visiblemente. Ese desvío es del orden del número buscado y **no se promedia** — es un sesgo con varianza. Sirve para "lo que el operador siente" (~1076 ms), no para latencia de video. Herramienta en `control_to_photon.py`, con su límite documentado |

**Lo que sí funcionó, y por qué:** los dos extremos del intervalo son **nuestro propio reloj**
en esta misma máquina. No se lee ningún dígito (el panel se mide por brillo) y no se
sincroniza ningún reloj ajeno. Cuando la pantalla filmada está en otro dispositivo, esa página
se disciplina contra nuestro servidor y **le reporta su sincronización**, y la medición se
niega a dar un número sin eso.

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
