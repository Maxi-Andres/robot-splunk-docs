# Plan de video para teleoperar — qué falta, qué está hecho

> El plan de trabajo, con el estado real de cada punto. Los números son medidos; donde algo
> es inferencia, lo dice. El detalle de cada medición y de cada defecto está en
> [`VIDEO-LATENCIA.md`](VIDEO-LATENCIA.md); este documento es el mapa.
>
> Última revisión: **2026-09-14**.

---

## 0. La aclaración que hay que leer antes que nada

**No estamos leyendo el H.264 nativo del robot.** Es fácil creer lo contrario, porque todo lo
que sale del robot hoy es H.264. Lo que pasa en realidad es:

```
cámara → videohub del Go2 (GetImageSample, request/response)  ← ~650 ms, ACÁ ESTÁ EL PROBLEMA
       → JPEG
       → nvjpegdec (decodificar en el Jetson)
       → nvv4l2h264enc (re-encodear a H.264)
       → mediamtx → Frigate / drive / YOLO
```

El cambio a H.264 compró **ancho de banda**, no latencia de origen: los ~650 ms ya vienen
dentro del JPEG que pedimos, así que ningún transporte los toca.

El H.264 **nativo** es otra cosa: el Go2 publica `rt/frontvideostream` por DDS a 30 fps, ya
comprimido. Leer eso saltearía el videohub entero. **Es el punto B del plan y no está
empezado.**

---

## 1. Dónde está la latencia, medida

| etapa | costo | estado |
|---|---|---|
| **cámara → videohub (`GetImageSample`)** | **~650 ms** | **sin tocar — es el 90%** |
| `mjpeg_server` (el tee en el robot) | 0.2 ms | medido, no es problema |
| cadencia a 5 fps | ~200 ms | perilla: subir `NVR_FPS` |
| presupuesto ARQ de SRT | 150 ms configurados, **1 ms usados** | perilla: bajarlo mucho |
| jitter buffer del browser | 54 ms (aislado), 4-26 ms en vivo | perilla: `jitterBufferTarget` |
| mediamtx + WebRTC | ~70 ms | medido, razonable |
| transporte MJPEG (234 KB por cuadro) | **225-240 ms** | eliminado al pasar a H.264 |
| transporte H.264 (~37 KB por cuadro) | ~35 ms | el que corre hoy |

> **Todo lo de abajo del videohub junto suma menos de la mitad que el videohub solo.** Por eso
> el punto B es "la mejora de mayor techo" y no una optimización más.

---

## 2. Lo que está hecho

### 2.1 El robot manda una sola copia · **LISTO Y ANDANDO**

Verificado 2026-09-14: **0 clientes en el MJPEG del robot**, **1.40 Mbps** por RTMP, y
mediamtx repartiendo a Frigate y a YOLO. Antes salían las dos cosas a la vez.

La diferencia no es solo 6.7×: **MJPEG escala por visor** porque el robot sirve una copia
entera a cada cliente (8.9 Mbps cada uno), mientras el H.264 sale **una vez** y mediamtx lo
reparte en HQ.

### 2.2 Tres defectos del encoder, arreglados y desplegados

Ninguno estaba en el plan; los tres bloqueaban todo lo demás.

| defecto | efecto | commit |
|---|---|---|
| Supervisor ciego a la muerte del encoder | un crash intermitente mataba la rama **para siempre** | `b48ca3b` |
| `control-rate` ausente | el encoder ignoraba `bitrate` | `b1289f2` |
| Base de tiempo `1/1` | gastaba el bitrate **por cuadro**: 7.0 Mbps con 1.5 configurados | `e551511` |

El `NVR_ENABLE=0` que encontramos era **consecuencia** del primero, no la causa: alguien apagó
la rama porque no arrancaba.

### 2.3 SRT probado punto a punto · **ANDA, NO DESPLEGADO**

El robot conecta a `srt-live-transmit` 1.5.4 sin problema — la hipótesis central del plan A
era correcta, lo único roto era el receptor. Sobre 353 s: **642 paquetes perdidos, 685
retransmitidos, 0 descartados**, usando **1 ms de los 150** de presupuesto.

Dos defectos más aparecieron acá, y los dos fallan en silencio:

- **`mpegtsmux` sin `alignment=7`**: SRT lleva 1316 bytes por mensaje y `srtsink` descarta
  los buffers más grandes — o sea **los keyframes**. 0 IDR contra 8 en 26 s.
- **`udpReadBufferSize` de mediamtx**: el salto UDP por loopback perdía el **15% de los
  cuadros**. 20 freezes (64 s) contra **0**.

Resultado final: **0 freezes en 180 s**, 0 pérdida sobre 31.446 paquetes, 1.40 Mbps.

> Hoy corre **RTMP**, no SRT. SRT queda validado y esperando la prueba sobre LTE, que es
> donde tiene sentido.

### 2.4 YOLO pasa a H.264 · **LISTO**

`RtspStreamSource` en el bridge: lee el H.264 de mediamtx y decodifica en HQ (3.9 ms por
cuadro). El lector se elige por el **esquema de la URL**, así que volver a MJPEG es cambiar
una línea del `.env`.

No va por Frigate a propósito: es un NVR y bufferea ~7 s, y cajas de hace 7 segundos sobre
un video de 200 ms son peores que no tener cajas.

### 2.5 Switch MJPEG ↔ H.264 en la interfaz · **LISTO, PARCIAL**

Contexto compartido, **una sola conexión WebRTC** para toda la app, presente en `/drive` y en
Live con las estadísticas del camino activo al lado. Está en la interfaz y no en un archivo
de configuración porque los dos caminos **solo se comparan honestamente alternándolos sobre
el mismo enlace**, y eso hay que repetirlo en cable, LTE y Starlink.

⚠️ **Mueve la imagen, no YOLO ni el VLM.** Esos se alimentan del bridge, que tiene su propia
fuente. Falta la ruta frontend → backend → bridge.

### 2.6 Página de video, arreglada

- `Feed the recorder` era un slider para un on/off. Ahora es On/Off.
- Los `step` impedían valores legales: **calidad no llegaba a 75** (el default del robot) ni
  **keyframes a 15** (el valor corriendo).
- Los rangos de reserva inventaban 0–100; ahora copian la tabla del relay.
- **La página afirmaba estados que nadie le informó.** El relay desplegado es una build vieja
  que solo reporta `fps`/`width`/`quality`; las otras cuatro perillas llegaban vacías y la
  página las dibujaba como 0 — de ahí el `off` sobre un robot que estaba grabando. Ahora dice
  **unknown** y no deja moverlas.

---

## 3. Lo que falta, en orden

### A corto plazo, sin depender de nada

1. **Desplegar el relay nuevo en el robot.** Es lo que devuelve `nvr`, `bitrate`, `maxfps` e
   `idr` a la página de video. Hoy cuatro de siete perillas están ciegas.
2. **Que el switch mueva también YOLO y el VLM** (frontend → backend → bridge). Hay una skill
   `cross-tier-feature` en el repo justo para este tipo de cambio.
3. **Subir `NVR_FPS`** de 5 a 10-15. A 5 fps la cadencia sola son 200 ms, y la meta de "cero
   huecos > 200 ms" es inalcanzable por construcción. Hay que re-medir el bitrate después:
   el divisor del §2.3 de `VIDEO-LATENCIA.md` depende de ese número.
4. **Bajar el jitter buffer del browser** con `jitterBufferTarget`.

### Cuando haya LTE o Starlink

5. **Comparar las dos ramas sobre el enlace real.** Es lo único que decide de verdad: por
   cable el ARQ casi no trabaja, así que SRT y RTMP se parecen demasiado.
6. **Barrer el `LATENCY` de SRT hacia abajo.** Por cable no es medible con sentido — usa 1 ms
   de 150, cualquier valor daría "sin artefactos" y elegiríamos un número que después falla.
7. **Mover SRT a producción** si gana. ⚠️ Hay que ponerle `udpReadBufferSize` al `mediamtx.yml`
   de producción, o el defecto del §2.3 vuelve idéntico.

### El grande

8. **Punto B: leer `rt/frontvideostream`.** Es lo único que ataca los ~650 ms, y de paso se
   saltea el `nvjpegdec` (con su double free sin resolver) y la doble copia.

   Lo que se sabe: existe `robot-video-pipeline/src/go2_h264_stream.cpp` de un intento previo.
   Los dos fracasos registrados fueron **siempre leyendo desde afuera** — por el puente ROS2
   llega corrupto, por el SDK nativo empareja pero no reensambla. **Nunca se probó adentro del
   Jetson**, que es un escenario distinto: bus interno, sin fragmentación por red. Tres
   trampas concretas: la NIC por defecto es `enp4s0` y en el Jetson es `eth0`; hace falta
   `CYCLONEDDS_URI` con `<NetworkInterface>`; y no hay `ffmpeg` en el Jetson.

   ⚠️ Usar la escalera **`video360p`**, no `video720p`: 130 KB × 30 fps ≈ 31 Mbps. *Ese número
   es inferencia, no medición.*

---

## 4. Lo que no se resolvió

- **El double free de `nvv4l2h264enc`** sigue sin causa. Descartados con medición: bitrate,
  CBR, la cadena `nvjpegdec` con bytes reales, decode por software, 4:2:0 vs 4:2:2, restart
  markers, el segmento COM y `rtmpsink` contra dos servidores. Está **mitigado** por el
  supervisor, no arreglado. Candidato vivo: el `Corrupt JPEG data` cada 20-30 s del journal.
- **Nada se midió sobre LTE ni Starlink.** Todo es por cable, o sea el mejor caso.
- **El glass-to-glass absoluto del camino H.264 no es medible en remoto.** Marcar el cuadro en
  origen costaría re-encodear el JPEG en el Jetson — los ~100 ms que justamente sacamos. Hace
  falta alguien al lado del robot con un cronómetro en cuadro.
- **Riesgo introducido:** si `NVR_FPS` cambia desde el panel sin reiniciar el servicio, el
  divisor del bitrate queda viejo y el bitrate sale mal por ese factor, sin síntoma visible.

---

## 5. Herramientas

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
