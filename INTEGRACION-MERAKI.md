# Integración Meraki → Splunk

Qué hay que hacer para que el dashboard **`Silk HQ — Meraki`** (vista `silk_hq_meraki`)
deje de mostrar *"No search results returned"*.

Estado al 22/09/2026: el dashboard está completo (27 paneles, 21 búsquedas) y **el índice
`meraki` tiene cero eventos desde que se creó**. No está roto: nunca le llegó un dato.

## 1. Lo que ya está hecho en Splunk

Verificado en `192.168.20.200` el 22/09/2026. No hay que rehacer nada de esto:

| Objeto | Estado |
|---|---|
| Índice `meraki` | existe, 50 GB máx., retención 365 d — **0 buckets** |
| Input HEC `MERAKI-HQ` | habilitado, apunta a `meraki` |
| Input `udp://5515` | **escuchando**, → `meraki:syslog` |
| `props.conf` | los 9 sourcetypes, con parseo de tiempo y extracción de campos |
| Vista `silk_hq_meraki` | compartida a nivel app |

El token del HEC **no se escribe acá**. Se consulta en *Settings → Data inputs → HTTP Event
Collector → MERAKI-HQ* y se guarda en el server con permisos 600:

```bash
printf '%s' 'EL-TOKEN' > ~/.splunk_hec_meraki_token && chmod 600 ~/.splunk_hec_meraki_token
```

## 2. Las tres patas

El dashboard lee 8 sourcetypes que vienen de tres fuentes **independientes**. Ninguna
depende de las otras, y se pueden encender de a una.

| Pata | Sourcetypes | Quién lo manda | Qué paneles enciende |
|---|---|---|---|
| **API Dashboard** | `meraki:api:*` (6) | `meraki-hq/meraki_poller.py` | uplinks, dispositivos, clientes, wireless, aplicaciones |
| **Syslog** | `meraki:syslog` | el equipo Meraki → `udp/5515` | flujos, IDS/IPS, filtrado de contenido, Air Marshal |
| **Webhooks** | `meraki:webhook` | la nube de Meraki → HEC | alertas por tipo, tabla de eventos |

> Consecuencia práctica: si encendés solo la API, los paneles de seguridad y eventos siguen
> vacíos, y **eso no es un error**. Ver §6.

## 3. Pata A — API Dashboard

### Lo que se encontró al correrlo de verdad (22/09/2026)

El informe original asumía una organización `Silk Technologies` y una red `Silk HQ`.
**Ninguna de las dos existe con ese nombre.** Medido con `--probe`:

| | Real |
|---|---|
| Organización | **`Silk-Technologies`** con guión, id **610420**. La key ve **4 orgs**, dos de otros clientes (`MIRANDA `, `Simon Sociedad Militar SRL`) — por eso `MERAKI_ORG_ID` no es opcional |
| Redes | `Silk Lab` (9 equipos, la viva) · `Itinerante` (un MX68 dormido) · `Demo Meraki Cisco` y `ROBOT` (vacías). **No hay ninguna `Silk HQ`** |

El dashboard igual no se rompe: su token de red viene en `*`, así que el desplegable se
llena solo con lo que haya. `Silk HQ` es el título, no un filtro.

Equipos de `Silk Lab`: 2 APs CW9162I/CW9164I y un switch MS220-8P **online**, un C9500-16X
en alerta, y 4 dormidos (C9200L, IE-3500, MR42, MV2).

### a) Generar la API key

*My Profile → API access → Generate new API key*. Se muestra **una sola vez**.

Usar una cuenta **read-only** sobre la organización: el poller solo lee, y una key con
permiso de escritura en el server de Splunk es una key que puede reconfigurar la red desde
una caja que no es la que administra la red.

### b) Dejarla en el server

```bash
ssh silkadmin@192.168.20.200
printf '%s' 'LA-KEY' > ~/.meraki_api_key && chmod 600 ~/.meraki_api_key
```

### c) Confirmar el esquema antes de confiar en un panel

El mapeo de campos se construyó contra el esquema **publicado** de la API v1, no contra la
organización de Silk. Un tier de licencia o un modelo de equipo puede no devolver un campo.

```bash
cd ~/robot-splunk-docs/meraki-hq
./meraki_poller.py --probe orgs        # qué organizaciones ve la key
./meraki_poller.py --probe networks    # escribir MERAKI_NETWORKS igual que sale acá
./meraki_poller.py --probe uplink      # la respuesta cruda, antes de darle forma
```

Si lo que devuelve la API real contradice los fixtures de `tests/`, **corregir el fixture
primero y dejar que el test falle**. Ese fallo es para qué existe `--probe`.

### d) Ensayo sin escribir en Splunk

```bash
cp .env.example .env     # editar: MERAKI_ORG, MERAKI_NETWORKS
./meraki_poller.py --once --dry-run
```

`--dry-run` cuenta eventos y muestra una muestra por familia, pero no escribe nada en
stdout, así que el pipe al shipper no lleva nada. Es seguro contra el Splunk productivo.

### e) Servicio

```bash
sudo cp systemd/meraki-poller.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meraki-poller
journalctl -u meraki-poller -f
```

Corre en el **server de Splunk**, no en el robot ni en la workstation: la pata de la API
necesita internet, la del HEC queda en localhost, y esa caja no se apaga.

## 4. Pata B — Syslog

*Network-wide → General → Reporting → Syslog servers → Add a syslog server*:

| Campo | Valor |
|---|---|
| Server IP | `192.168.20.200` |
| Port | `5515` |
| Roles | Event log, Security events, URLs, Air Marshal events, **Flows** (ver abajo) |

**El 5515 es a propósito.** El `5514` ya lo ocupa el IR1101 con `cisco:ios`; mandar Meraki
ahí mezcla dos sourcetypes en un input y el parseo de los dos se rompe.

> ⚠️ **Flows es el único rol que cuesta licencia de verdad.** Los otros cuatro son
> despreciables; Flows solo puede sumar entre 0,3 y 1,5 GB/día. Hoy la instancia consume
> 0,086 GB/día sobre 50 GB, así que entra — pero encenderlo tiene que ser una decisión, no
> un descuido. El panel de *top de conversaciones* es el único que lo necesita.

Verificar que el `5515/udp` esté abierto desde la VLAN de gestión hacia el Splunk.

## 5. Pata C — Webhooks de alertas

*Network-wide → Alerts → Webhooks → Add an HTTP server*:

- **URL:** `http://192.168.20.200:8088/services/collector/event`
- **Custom payload template** — Meraki postea el JSON crudo y el HEC espera un sobre, así
  que hay que envolverlo:

```
{"time": {{unixTimestamp}}, "host": "{{deviceName}}", "sourcetype": "meraki:webhook",
 "index": "meraki", "event": {{{json}}}}
```

- El token va en *Additional headers*: `Authorization: Splunk <token>`

El `{{{json}}}` de tres llaves es literal: dos llaves escapan el contenido y lo indexan
como un string, no como un objeto, y los campos del evento (`alertType`, `alertData`) no se
extraen.

## 6. Verificación

```spl
index=meraki | stats count by sourcetype
index=meraki sourcetype=meraki:api:uplink | head 5
index=meraki sourcetype=meraki:syslog | stats count by mk_type
```

Resultado real de la primera pasada (22/09/2026), con solo la pata de API encendida:

| Familia | Eventos | Estado |
|---|---|---|
| `device` | 10 | ✅ |
| `uplink` | 4 | ✅ status; loss/latency vacíos porque el único MX está dormido |
| `client` | 50 | ✅ clientes reales del SSID `Silk Technologies` |
| `wireless` | 2 | ✅ utilización de canal de los dos APs vivos |
| `uplinkusage` | 8 | ✅ (en ceros: no hay tráfico WAN) |
| `apptraffic` | **0** | ❌ *Traffic analytics* está **apagado** en las cuatro redes |

66 eventos entregados, spool vacío, cero errores. El índice `meraki` pasó de 12 KB a 108 KB.

Dos ceilings de la API que se descubrieron acá y están corregidos en el poller — los dos
devolvían 400 y dejaban su panel en blanco sin más señal que una línea en el journal:

- `uplinksLossAndLatency` rechaza `timespan` mayor a **300 s**.
- `channelUtilization/byDevice` exige `interval` explícito y `timespan >= interval`; sin él
  asume 3600 y rechaza cualquier ventana más corta.

Qué esperar según qué patas encendiste:

| Encendido | Paneles que se llenan | Paneles que siguen vacíos, y está bien |
|---|---|---|
| Solo API | uplinks, dispositivos, clientes, wireless, aplicaciones | alertas, IDS/IPS, flujos, tabla de eventos |
| Solo syslog | flujos, seguridad, parte de la tabla de eventos | todos los KPI de arriba |
| Las tres | todo | — |

Dos que dependen de una opción en Meraki y no del código:

- **Top de aplicaciones** necesita *Traffic analytics* con detalle de hostnames
  (*Network-wide → General → Traffic analysis*). Sin eso la API contesta 400 y el poller lo
  dice explícito en el log: `traffic analytics is OFF for <red>`.
- **Top de conversaciones** necesita el rol *Flows* del syslog (§4).

## 6.b. Dos defectos encontrados al ver datos reales (22/09/2026)

### La regex de `mk_type` nunca matcheaba

`props.conf` traía:

```
EXTRACT-meraki_type = ^<\d+>\d\s+\S+\s+(?<mk_host>\S+)\s+(?<mk_type>[\w\-]+)
```

Anclada a `^<\d+>\d`, o sea al `<134>1` del syslog crudo. **Pero el input `udp://` de Splunk
consume el `<pri>` y antepone su propio encabezado**, así que el evento indexado empieza así:

```
Sep 22 14:35:43 192.168.20.1 1 1790087743.320029814 GW_Silk_Tech firewall src=...
```

El ancla no matchea nunca, `mk_type` no se extrae nunca, y **todo panel que filtre por
`mk_type` queda vacío para siempre** — sin error en ningún lado. Corregido a:

```
EXTRACT-meraki_type = \d{10}\.\d+\s+(?<mk_host>\S+)\s+(?<mk_type>[\w\-]+)
```

El epoch de Meraki es el único punto fijo del mensaje. Como `EXTRACT-` es de **tiempo de
búsqueda**, el arreglo aplica retroactivamente a lo ya indexado: no hay que reindexar. Sí hay
que recargar la config (`/en-US/debug/refresh` desde el browser).

Verificado contra 19.384 eventos reales: extrae 19.381. Los 3 que no son paquetes de prueba
manuales con timestamp ISO en vez de epoch.

Tipos que realmente manda esta red:

| `mk_type` | Eventos en la primera hora |
|---|---|
| `ip_flow_start` | 5368 |
| `flows` | 5141 |
| `ip_flow_end` | 4290 |
| `urls` | 2620 |
| `vpn_firewall` | 1121 |
| `firewall` | 832 |
| `events` | 9 |

### El panel de IDS/IPS pide campos que no existen

Su búsqueda agrupa `by mk_type Firma priority` y arma `Firma` con
`coalesce(signature, disposition, url, ssid)`. **Ninguno de esos cinco campos existe** en los
eventos de esta red (0 ocurrencias de cada uno). Un `stats by` con un campo inexistente
devuelve vacío, así que el panel no puede funcionar con ningún dato. Es un defecto de la
definición del dashboard, no de la ingesta.

### La conversión de unidades de `uplinkusage` estaba mal

El poller multiplicaba por 1000 asumiendo kilobytes. **`usageHistory` devuelve bytes**: un
bucket real de 5 min traía 193.764.098, que como KB serían 193 GB en cinco minutos y como
bytes son 5,17 Mbps. El panel de throughput llegó a marcar 7,5 Gbps en un enlace de 7,5 Mbps.
Corregido; el test ahora usa ese bucket real como fixture. Los clientes **sí** son kilobytes,
verificado aparte — Meraki no es consistente entre endpoints y su esquema publicado no avisa.

## 7. Costo en licencia

| Fuente | Estimado |
|---|---|
| API (las 6 familias) | ~8 MB/día |
| Webhooks | despreciable |
| Syslog sin Flows | despreciable |
| Syslog **con Flows** | 0,3–1,5 GB/día |

Consumo actual de toda la instancia: 0,086 GB/día sobre **50 GB/día** de la licencia
Enterprise NFR. Hay margen de sobra incluso con Flows encendido.

## 8. Archivos

Todo en `meraki-hq/`:

| Archivo | Qué es |
|---|---|
| `meraki_poller.py` | el poller — 6 familias, rate limit, reintento ante 429, `--probe`, `--dry-run` |
| `run.sh` | el pipe a `hec_shipper.py` de `robot-telemetry-agent` |
| `systemd/meraki-poller.service` | unit para el server de Splunk |
| `.env.example` | todas las variables, con el porqué de cada número |
| `tests/test_meraki_poller.py` | 21 tests, cada uno nombra el defecto que atrapa |
| `README.md` | la tabla campo→panel, que es el contrato que no se puede romper |
