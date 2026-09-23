# Dashboards de Splunk

Copias versionadas de los dashboards que viven en la instancia
`192.168.20.200:8000` (`silk-ia-server`). Esta carpeta es la referencia: si tocás
un dashboard en la UI, volvé a bajarlo acá.

**El archivo se llama igual que la vista en Splunk.** Es a propósito: antes no
era así y por eso el dashboard bueno se llamaba `go2-robot-v2` mientras la UI
mostraba *"(v4)"*, con dos `(v2)` distintas al lado. Si agregás uno, mantené la
regla.

## Vivos en Splunk

| Archivo / vista | Título en la UI | Sharing |
|---|---|---|
| `go2-telemetria-thousandeyes.xml` | Robot Go2 — Telemetría y ThousandEyes | Private |
| `go2-telemetria.xml` | Robot Go2 — Telemetría | Private |
| `wlc9800-curwb.xml` | Cisco WLC 9800 + CURWB Telemetry Dashboard | Private |
| `silk_hq_meraki.xml` | Silk HQ — Meraki | App |
| `meraki-hq.xml` | Silk HQ — Meraki · Classic | Private |

## La topología viva de `meraki-hq.xml` (23/09/2026)

El classic de Meraki tenía los datos bien y la presentación plana. Se le trajo el lenguaje
visual de `go2-telemetria-thousandeyes.xml`: **encabezados de sección** y, sobre todo, una
**fila 0 con la topología en vivo** — clientes → MR46 / cableado → MS120 → MX68 → WAN1 · WAN2
→ Internet → nube de Meraki.

Cómo funciona, que es igual que en el del Go2: seis búsquedas a nivel dashboard dejan 25
tokens, cada uno un `*_cls` (`up` / `warn` / `down` / `nd`) y un `*_txt`; el SVG usa el
primero como clase CSS y el segundo como texto. **Esto NO se puede hacer en Dashboard Studio**
— no tiene panel HTML, que es la misma razón por la que el panel de video del Go2 es Simple
XML (ver `ROADMAP` §11).

Tres decisiones que conviene no deshacer:

- **`nd` pinta gris y NO anima.** Un diagrama que corre en verde sobre cero eventos miente, y
  hoy hay motivos reales para tener huecos: el MX puede estar dormido, y syslog y webhooks
  todavía no están configurados del lado de Meraki.
- **La pérdida degrada al estado.** Un uplink `active` con 5% de pérdida sale rojo, no verde.
  Es la misma lección que el video sobre LTE: el enlace puede estar arriba y ser inservible.
- **El nodo `Meraki Cloud` no dice si Meraki anda: dice si NUESTRO poller sigue trayendo
  datos** (`age` del último evento). Un dashboard que se ve sano con datos de ayer es peor que
  uno vacío, y es lo único del diagrama que lo delata. Por eso su búsqueda no filtra por red.

La velocidad de los guiones sigue al throughput real de los uplinks: si corren hay tráfico, si
se arrastran no. Sin tráfico quedan casi quietos (6 s por vuelta).

Se respeta `prefers-reduced-motion`: quien pidió menos movimiento no ve el diagrama latir.

`silk_hq_meraki` es el único que conserva el nombre viejo con guiones bajos: está
compartido a nivel App en una instancia que se usa entre varios, así que
renombrarlo le rompería la URL a otra persona. No se tocó.

## `archive/`

Iteraciones que ya **no existen en Splunk**, con la fecha de su último commit al
frente para que se lean en orden. Se guardan porque varios docs del repo citan
números de línea contra ellas.

| Archivo | Qué era |
|---|---|
| `2026-08-19-go2-sin-video.xml` | la vista sin el panel de video, para cuando la cadena de video no estaba levantada |
| `2026-09-04-go2.xml` | primera versión con paneles de ThousandEyes (filas 5 y 6) |

El viejo `dashboard-go2-completo.xml` no está: era byte por byte idéntico al
`go2-telemetria.xml` que está vivo.

## Limpieza del 22/09/2026

Se pasó de 16 dashboards a 4 propios. Se borraron 8 (backups fechados, `(v2)`
duplicada en Private y App, `(v3)`, `zz test color` 1 y 2, `Robot Go2 —
Operación`, `WIRELESS LAN CONTROLLER`).

Además se renombraron vista y título:

| Vista antes | Vista ahora | Título antes |
|---|---|---|
| `go2-robot-v2` | `go2-telemetria-thousandeyes` | Robot Go2 — Telemetría (v4) |
| `go2-robot` | `go2-telemetria` | *(sin cambio)* |
| `wlc_9800` | `wlc9800-curwb` | *(sin cambio)* |

Renombrar la vista **cambia la URL y descarta el favorito** (la estrella). Se
aceptó el costo. Backup previo a todo, en el server:
`/home/silkadmin/dashboards-backup-2026-09-22-1333.tgz` — los 12 XML de entonces.

Quedan 4 dashboards con owner `nobody` que **no se pueden borrar**: viven en
`/opt/splunk/etc/apps/search/default/data/ui/views/` y Splunk no permite borrar
objetos que vienen con el producto. Son `Integrity Check of Installed Files`,
`Job Details Dashboard`, `Orphaned Scheduled Searches, Reports, and Alerts` y
`Scheduled export is now available for Dashboard Studio`.

## Bajar o subir un dashboard

La REST en `:8089` **no** está cerrada — eso valía con Splunk Free y quedó superado
por la Partner NFR del 04/09. Hoy contesta `Unauthorized`: acepta la conexión y
pide credenciales de Splunk. Mientras no las tengas a mano, el camino directo es
el filesystem por SSH:

```bash
# bajar
ssh silkadmin@192.168.20.200 \
  'cat /opt/splunk/etc/users/admin/search/local/data/ui/views/go2-telemetria-thousandeyes.xml' \
  > dashboards/go2-telemetria-thousandeyes.xml
```

Escribir en disco **no alcanza**: splunkweb cachea y sigue mostrando el nombre
viejo. Hay que refrescar desde el browser (ahí sí hay sesión):
`http://192.168.20.200:8000/en-US/debug/refresh` → botón *Refresh*.
