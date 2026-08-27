# robot-splunk-docs

El registro de ingeniería del ecosistema de robots: las mediciones, las decisiones y los
planes en los que se apoyan los otros repos de `robot-ecosystem/` y AI-VL. No tiene código
ejecutable — salvo los dashboards de Splunk, que son el entregable de la parte de telemetría.

Varios de estos documentos están **citados desde el código** de otros repos (buscá
`robot-splunk-docs/` en `robot-command-relay/relay_server.py`,
`unitree_ros2/robot_camera_bridge/camera_sources.py` y
`unitree_ros2/robot_executor/robot_executor_service.py`). Si renombrás un archivo, grepeá
antes.

## Por dónde empezar

| Si querés saber… | Leé |
|---|---|
| Por qué el DDS no se puede leer desde otra subred, y por qué los dos robots no pueden convivir en un segmento | **`RED-Y-DDS.md`** — el documento fundacional, el más citado |
| En qué estado quedó todo y cómo seguir | **`ESTADO-Y-CONTINUACION.md`** — traspaso del 2026-08-20 |
| Cómo se opera el robot desde cualquier red | `ARQUITECTURA-REMOTA.md` |
| Qué IP es cada una y en qué archivo se cambia | `IPS-Y-DONDE-CAMBIARLAS.md` — regenerable con un grep |

## Los documentos, por tipo

**Mediciones** (datos crudos, no opiniones)

- `CENSO-GO2.md` — censo de los 122 tópicos DDS del Go2, medido el 2026-08-19 con
  `ros2 topic bw`. Es la evidencia detrás de casi todo lo demás.

**Referencia técnica**

- `RED-Y-DDS.md` — por qué el DDS de estos robots no cruza una frontera de subred (122
  tópicos desde su subred, 2 desde otra, 3 con peers unicast explícitos), y qué consecuencias
  tiene para cada componente. Autoridad sobre todo lo de red.
- `IPS-Y-DONDE-CAMBIARLAS.md` — inventario de IPs con el archivo y la línea donde se cambia
  cada una, y qué se rompe si está mal.

**Arquitectura y planes**

- `ARQUITECTURA-REMOTA.md` — el diseño para operar el robot desde cualquier red: objetivo,
  estado real y lo que falta por capacidad.
- `PLAN-CONECTIVIDAD-ROBOTS.md` — plan de conectividad: IR1101, red de robots, transporte
  DDS, pipeline a Splunk.
- `PLAN.md` — plan de telemetría contrastado con el estado real del stack. Autoridad sobre el
  colector y el contrato de datos.
- `Telemetria-Splunk.md` — el plan original (G1 + Go2). Superado por `PLAN.md`; se conserva
  como referencia de lo que se pensaba al principio.

**Ejecución**

- `IMPLEMENTACION.md` — guía paso a paso con criterio de éxito por etapa. La regla que la
  ordena: nada toca el robot hasta la Etapa D.

Relacionado, pero fuera de este repo: el diagnóstico de por qué el robot deja de transmitir
video después de un rato está en `AI-VL-ecosystem/docs/CORTES_DE_VIDEO_Y_SOBRECALENTAMIENTO.md`.

**Entregables de Splunk**

- `dashboard-go2.xml` — dashboard con panel de video incluido.
- `dashboard-go2-sin-video.xml` — la misma vista sin el panel de video, para cuando la
  cadena de video no está levantada.

El panel de video usa un `<img>` apuntado al MJPEG de Frigate, **no** un `<iframe>`: el
sanitizador de Simple XML de Splunk 9 elimina los `<iframe>` y no hay setting que lo
habilite.

## Convención de idioma

Estos documentos están en **castellano** a propósito: son narrativa de planificación y
diagnóstico, y se leen más rápido así. El código y los comentarios de todos los repos van en
**inglés**, sin excepción.
