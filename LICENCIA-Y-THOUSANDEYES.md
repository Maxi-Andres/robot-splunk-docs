# Licencia vencida y alta de ThousandEyes en el dashboard

**Escrito el 2026-08-31.** Dispara de esto: el dashboard `go2-robot` mostraba
`Error in 'litsearch' command: Your Splunk license expired...` en los 5 paneles con SPL.

---

## 1. Diagnostico: no era el dashboard, era la licencia

`PLAN.md` §5.1 y §5.2 ya lo tenian anotado: el trial de Splunk Enterprise vencia el
**25/08**. El 31/08 hace 6 dias que vencio, y **un trial vencido deshabilita la busqueda**.

Por eso el sintoma es exactamente ese y no otro:

| Panel | Estado | Por que |
|---|---|---|
| Bateria, Temp, DDS vivo, Enviado hoy, Eventos | rotos | corren SPL, y SPL es lo que se apaga |
| Camara frontal | **sigue andando** | es un `<img>` a Frigate, el browser lo baja directo y nunca toca Splunk |

No hay una sola linea que arreglar en `dashboard-go2.xml`.

**Lo que NO se perdio:** la ingesta sigue entrando. Verificado el 31/08:

```
$ curl -sk https://192.168.20.200:8088/services/collector/health
{"text":"HEC is healthy","code":17}
```

El HEC no depende de la licencia de busqueda. Los datos que mando el agente en estos 6 dias
**estan en el indice**; se ven en cuanto vuelva la busqueda.

---

## 2. Licencia: se paso a Free, y NO alcanzo

> **Resumen, para no leer las 6 subsecciones:** el 31/08 se paso el grupo a Free. Eso
> arreglo el vencimiento pero **no** devolvio la busqueda, porque quedaban **6 violaciones
> acumuladas** (§2.1.d) y Free es la unica modalidad sin reset key. La salida es una
> **licencia Developer** (§2.1.e); si no, la busqueda vuelve sola el **12/09**.

### 2.0. El cambio que se hizo

Decidido el 31/08. Es el unico camino que devuelve la busqueda **hoy**.

```bash
ssh silkadmin@192.168.20.200

# El -auth hace falta AHORA (todavia hay Enterprise con auth).
# Despues de reiniciar ya no, porque Free no tiene autenticacion.
sudo -u splunk /opt/splunk/bin/splunk edit licenser-localslave \
     -active_group Free -auth admin:<PASSWORD>

sudo -u splunk /opt/splunk/bin/splunk restart
```

Por la UI es el mismo cambio: **Settings -> Licensing -> Change license group -> Free
license -> Save**, y reinicia solo.

### 2.1. Lo que se pierde, explicito

Esto no es gratis y conviene tenerlo escrito antes de que alguien lo descubra de golpe:

| Se pierde | Impacto real acá |
|---|---|
| **Autenticacion** | Splunk Free **no tiene login ni roles**: cualquiera en VLAN 20 entra a `:8000` como admin. La instancia queda abierta en la red de servidores |
| **Alerting / busquedas programadas** | Es justo lo que `PLAN.md` §12 queria para temperatura critica del robot. Queda pendiente |
| Busqueda distribuida, aceleracion de reportes | No se usaban |

`PLAN.md` §11 paso 13 ya avisaba *"si el trial cae a Splunk Free, se pierde alerting"*.
Se acepto el trade-off a cambio de recuperar la vista hoy.

> ⚠️ **La instancia es compartida** con otra persona (`PLAN.md` §5.1). Este cambio le
> cambia el entorno a ella tambien: pierde su login y sus alertas. Avisarle antes.

### 2.1.b. Free tambien corta la API remota (descubierto al aplicarlo)

Aplicado el 31/08. Ademas de lo de §2.1, aparecio algo que no estaba previsto: splunkd
contesta a cualquier request remoto a `:8089` con

> *Remote login disabled because you are using a free license which does not provide
> authentication.*

Free desactiva **`allowRemoteLogin`**. Consecuencias:

| Camino | Estado |
|---|---|
| **HEC `:8088`** (telemetria del Go2, y el futuro stream de TE) | ✅ **intacto** — auth por token, no pasa por `allowRemoteLogin` |
| UI `:8000` desde el browser | ✅ anda (splunkweb habla con splunkd localmente) |
| CLI/REST **desde otra maquina** a `:8089` | ❌ **bloqueado** |
| CLI **en la propia caja** (`silkadmin@silk-ia-server`) | ✅ anda, y ya ni pide password |

O sea: `hec_shipper.py` no se entera. Lo que queda cerrado es cualquier cosa futura que
quiera **consultar** Splunk por API desde esta PC o desde el backend de AI-VL.

Se destraba con `allowRemoteLogin = always` en `$SPLUNK_HOME/etc/system/local/server.conf`,
pero eso deja la API **abierta y sin credenciales** — en Free no hay ninguna que pedir. No
tocarlo salvo necesidad real, y si se toca, que sea con un filtro de origen por firewall.

### 2.1.c. Como se aplico realmente

Los comandos del §2 no sirvieron tal cual. Dos correcciones, para el proximo que lea esto:

1. **No hay usuario `splunk`.** La instalacion es del tarball (`splunk-10.4.0-*.tgz` en el
   home de `silkadmin`), no del `.deb`. `/opt/splunk` es de `silkadmin` y `splunkd` corre
   como `silkadmin` bajo la unit **`Splunkd.service`**. `sudo -u splunk` falla con
   `user 'splunk' not found`.
2. **El handler es `licenser-groups`, no `licenser-localslave`.** Este ultimo sirve para
   apuntar a un license manager remoto (`-master_uri`); el grupo activo se cambia asi:

```bash
# como silkadmin, sin sudo:
/opt/splunk/bin/splunk edit licenser-groups Free -is_active 1
sudo systemctl restart Splunkd          # la password acá es la de silkadmin, no la de Splunk
/opt/splunk/bin/splunk list licenser-groups   # confirmar Free -> is_active:1
```

Verificado el 31/08: `Free is_active:1`, `stack_ids: free`.

### 2.1.d. Free NO alcanzo: 6 violaciones acumuladas

Verificado el 31/08 despues de aplicar Free. El dashboard seguia con el mismo error.
`splunk list licenser-messages` mostro **6 `pool_over_quota`**, uno por dia, del **26 al 31
de agosto**, sobre `stack_id:download-trial`, con esta descripcion:

> `This pool has exceeded its configured poolsize=0 bytes`

**Por que se acumularon — no fue consumo de mas.** Al vencer el trial el 25/08 la cuota del
pool paso a **0**. Con cuota 0 no existe "estar dentro": *cualquier* byte ingerido es exceso.
Y el HEC siguio ingiriendo perfecto, porque **el HEC no depende de la licencia de busqueda**.
Una violacion automatica por dia, seis dias, sin que nadie hiciera nada mal.

> ⚠️ Es la trampa del diseño: el camino que te cobra sigue vivo mientras el que te avisa
> esta apagado. Si alguna vez vuelve a vencer una licencia, **cortar el shipper el mismo dia**.

**Las violaciones son del peer, no del stack**, asi que sobrevivieron al cambio de grupo:
`pool_warning_count ... peer(s) with 6 warning(s)` ya aparece sobre `stack_id:free`.

Y ahi esta el problema del camino elegido:

| | Umbral de violacion | Reset key |
|---|---|---|
| **Free** | **3 warnings en 30 dias** -> busqueda deshabilitada | **no existe** |
| Enterprise (<100 GB/dia) | 45 warnings en 60 dias | si, via Splunk |

Free es la unica modalidad **sin** reset key. Con 6 warnings estamos al doble del umbral.

**Los warnings caducan a los 14 dias**, uno por uno: los del 26-31/08 vencen del 09 al
14/09. Se baja de 3 el **12/09** — siempre que no se generen nuevos.

`ActiveEnterTimestamp=2026-08-31 16:54 UTC` confirma que splunkd si reinicio: el restart no
fue el problema.

### 2.1.e. La salida: licencia Developer

Una licencia Enterprise valida **no borra los warnings, pero sube el umbral de 3 a 45**. Con
6 quedamos holgadamente debajo y la violacion se evapora **al instante**, sin esperar al 12/09.
Ademas devuelve lo que Free saco (auth, alerting, API remota) y sube el techo a **10 GB/dia**.

**Como se pide** (gratis):

1. Cuenta de splunk.com — **la misma con la que se bajo el tarball**, no hace falta una nueva.
2. `https://dev.splunk.com/enterprise/dev_license/`
3. *Documentation & Tooling* -> *Request Developer License* -> *Request License*
4. Hasta **3 dias habiles**. Sin respuesta: `devinfo@splunk.com`

**Como se instala cuando llega** — son dos pasos, y olvidar el segundo deja todo igual:

```
Settings -> Licensing -> Add license -> subir el .lic
Settings -> Licensing -> Change license group -> Enterprise -> Save
```

> El grupo activo hoy es **Free**. Si se sube el `.lic` sin cambiar el grupo, Splunk lo
> guarda y lo **ignora**.

**Dos advertencias:**

- La Developer es **para uso no productivo**, y esta en el acuerdo que se firma. El caso de
  acá (PoC de telemetria, dashboard de demo) cae razonablemente en dev/test, pero la
  decision es de Silk, no tecnica.
- **Si Silk ya es cliente de Splunk**, el camino bueno son las *Personalized Dev/Test
  Licenses* via account team: mas rapido y sin la ambiguedad anterior. Preguntar ahi primero.

### 2.1.f. Vigilar que no entren warnings nuevos

Desde el 31/08 la cuota de Free es de **500 MB/dia reales**, asi que solo se genera warning
si de verdad se pasan. El agente del Go2 esta quieto (robot apagado), asi que el unico
consumo vivo es el de la otra persona. Cada warning nuevo **corre la fecha del 12/09 hacia
adelante**.

Durante una violacion **`_internal` si se puede buscar**, justamente para diagnosticar:

```
index=_internal source=*license_usage.log* type=RolloverSummary
| eval MB=round(b/1024/1024,1)
| timechart span=1d sum(MB) as MB_por_dia
```

### 2.1.g. Quien consume el presupuesto — respondido el 31/08

`PLAN.md` §12 tenia abierta la pregunta *"cuanto del presupuesto consume la otra persona"*,
que definia el cap real del agente. **Respondida**, con la busqueda de §2.1.f abierta por
indice/sourcetype/host:

| Indice | Sourcetype | Host | MB/24h |
|---|---|---|---|
| `wlc9800` | `cisco:wlc9800:telemetry` | `WLC` | **83.04** |
| `wlc9800` | `cisco:urwb:telemetry` | `192.168.20.20` | **55.36** |
| `network` | `cisco:ios` | `192.168.20.4` | 0.00 |

**~138 MB/dia, y nada de eso es del robot.** Es telemetria de red de Cisco: un Catalyst
9800 (controlador WiFi) y los radios **URWB = CURWB**.

> 🔎 **Hallazgo lateral, y no menor:** ya hay telemetria de los radios **CURWB** entrando a
> Splunk. Son los mismos que el `ROADMAP.md` §1 da como enlace del G1 e *"instalados pero
> nunca validados sin el cable"*. Es una fuente de datos que no estaba en ningun plan y que
> sirve directo para el pendiente de validacion de CURWB. **Ver que hay en `index=wlc9800`
> apenas vuelva la busqueda.**

**Presupuesto con esto adentro:**

| Consumidor | MB/dia |
|---|---|
| WLC 9800 + CURWB | 138 |
| Agente del Go2 (cap actual) | 150 |
| ThousandEyes (estimado, §3.4) | pocos |
| **Margen libre sobre 500** | **~200** |

Entra todo sin tocar nada. **Estos 138 MB no causaron ninguno de los 6 warnings** — esos
fueron todos por la cuota en 0 del trial vencido (§2.1.d).

Si igual se quiere recortar, el candidato es el WLC (la telemetria model-driven del 9800 es
verborragica). Se abre por `source` con:

```
index=_internal source=*license_usage.log* type=Usage earliest=-24h idx=wlc9800
| stats sum(b) as bytes by s | eval MB=round(bytes/1024/1024,2) | sort - MB
```

> ⚠️ **El recorte va en el WLC, no en `props.conf`.** Splunk licencia los **bytes que
> entran**, no los que sobreviven al filtro (`PLAN.md` §5.1). Filtrar del lado de Splunk no
> ahorra licencia: hay que bajar la cadencia o sacar la subscription en el equipo Cisco.

### 2.1.h. Dos consejos que circulan y son falsos

Aparecieron en una recomendacion externa el 31/08. Quedan anotados porque suenan plausibles:

| Consejo | Realidad |
|---|---|
| *"Pedi un License Reset Key para limpiar el contador"* | **Para Free no existe.** La doc de Splunk dice literalmente *"Reset option: None available"*. Las reset keys se emiten contra una licencia **Enterprise**, via Sales/Support. Es otra razon para la Developer (§2.1.e) |
| *"Borra `$SPLUNK_HOME/var/lib/splunk/fishbucket` para resetear los warnings"* | **El fishbucket no tiene nada que ver con la licencia.** Es donde Splunk guarda hasta que byte leyo cada archivo monitoreado. Borrarlo hace que **se reindexe todo desde cero**: un pico de ingesta enorme con 500 MB/dia de techo. Es la forma mas rapida de generar el warning nº 7 |

### 2.2. El techo sigue siendo 500 MB/dia

Free da **500 MB/dia**, el mismo numero que el trial. Y Free tambien castiga: si te pasas
**mas de 3 dias en una ventana de 30**, vuelve a deshabilitar la busqueda — el mismo error
de la captura, por el otro motivo.

O sea que el reparto ahora tiene **tres** consumidores y no dos:

| Consumidor | Presupuesto |
|---|---|
| Agente del Go2 | cap propio de **150 MB/dia**, ya implementado en el shipper |
| La otra persona | desconocido — sigue siendo la incognita de `PLAN.md` §12 |
| **ThousandEyes (nuevo)** | ver §3.4 |

### 2.3. Si despues de reiniciar sigue sin buscar

Superada por **§2.1.d**: fue exactamente lo que paso, y ahi esta el diagnostico hecho con
`splunk list licenser-messages`. Se deja el puntero porque §4 la referencia.

---

## 3. ThousandEyes en el dashboard

### 3.1. Como entra el dato — los dos caminos oficiales son push

**Verificado el 31/08 contra la doc de TE.** Corrige lo que decia la primera version de este
documento, que daba la app de Splunkbase como un pull: **no lo es**.

| Camino | Que es en realidad |
|---|---|
| **Streaming integration** (Integrations 2.0, conector `splunk-hec`) | TE **empuja** OTel al HEC |
| **Cisco ThousandEyes App for Splunk** (Splunkbase 7719) | **No hace pull.** Se configura con OAuth y macros de indice (`stream_index`, `path_viz_index`, `event_index`, `activity_index`), y **visualiza** lo que llega por streaming + webhooks |

O sea: **los dos piden lo mismo**, un HEC alcanzable desde la nube de TE.

#### Requisitos del endpoint (`url-target-requirements`)

| Requisito | Valor | Estado 31/08 |
|---|---|---|
| Protocolo | **HTTPS obligatorio** | ✅ |
| Certificado | **CA publicamente confiable**. Self-signed y CA privada **rechazados** | ❌ self-signed |
| Puerto | **solo TCP 443** | ❌ el HEC escucha en 8088 |
| DNS | nombre valido y resoluble | ❌ no hay |
| Alcance | **validado al crear el stream**; si falla, se rechaza la creacion | ❌ IP privada |

> ⚠️ **Contradiccion en la propia doc de TE:** la pagina de Splunk muestra
> `https://<host>:8088/services/collector`, pero la de requisitos dice **solo 443**. Ante la
> duda, asumir 443 → **hace falta un reverse proxy**, no alcanza con abrir el puerto.

> 📌 Endpoint correcto por señal: **`/services/collector`** para **metricas**,
> `/services/collector/event` para eventos. No son intercambiables.

> 🚫 TE **no soporta cuentas trial de Splunk**, explicitamente por el tema del certificado
> self-signed.

#### IPs de origen de TE (para el allowlist)

Region confirmada el 31/08 en Account Settings: **US2**. Son **12 IPs**.

| Region | IPs |
|---|---|
| **US1** | 13.56.245.241, 52.9.183.148, 18.232.232.61, 35.168.54.3, 107.22.84.44, 3.220.243.232, 3.218.27.195, 3.221.227.188 |
| **US2** ⬅️ **la nuestra** | 3.141.159.49, 3.17.98.26, 3.134.227.22, 3.18.18.42, 3.13.54.169, 3.138.52.162, 52.27.149.70, 52.32.30.54, 52.89.210.182, 44.227.213.61, 35.155.240.202, 35.81.172.197 |
| **EU1** | 18.157.124.37, 3.70.3.30, 18.158.163.183, 35.158.19.241, 3.127.8.252, 46.51.169.205, 54.75.173.76, 54.217.22.60, 34.243.129.225, 54.216.15.243, 108.128.60.238 |

#### Lo que hay que construir

Decidido el 31/08: **camino oficial**, sin desarrollo propio. Es un trabajo de infra:

1. Nombre **DNS publico** (ej. `hec-splunk.silk-technologies.com`)
2. **Certificado de CA publica** (Let's Encrypt sirve)
3. **Reverse proxy en 443** → `192.168.20.200:8088`, publicando **solo** `/services/collector*`
4. **NAT + allowlist** de las IPs de la region correspondiente
5. Recien ahi, crear el stream en el portal de TE

> 💡 **Atajo posible:** esta PC ya tiene **Tailscale**. *Tailscale Funnel* publica un
> servicio interno en 443 con DNS y certificado de Let's Encrypt, sin tocar NAT ni firewall
> — resuelve 1, 2 y 3 juntos. No es codigo propio, es una feature del producto.

> 🔒 **Secuencia:** **no exponer nada mientras Splunk este en Free.** El HEC va con token,
> asi que un proxy que solo publique `/services/collector` es defendible, pero con la
> instancia **sin autenticacion** cualquier ruta de mas que deje pasar el proxy es grave.
> Licencia posta primero (§2.1.e), exposicion despues.

### 3.1.b. La org y sus agentes — inventario 31/08

**El acceso NO es un problema:** la org es **`SILK TECH SRL - 178`**, propia, con admin. Queda
sin efecto la duda de `PLAN.md` §148 sobre si el agente del Jetson era de Cisco y por lo
tanto inaccesible: esta en la org de Silk.

| Agente | Estado | Ultimo contacto | Hostname |
|---|---|---|---|
| **TE-ENTERPRISE-SILK** | 🟢 Online | 1 min | `TE-ENTERPRISE` |
| **TE-ENTERPRISE-IOT** | 🟢 Online | 1 min | `TE-ENTERPRISE-IOT` |
| go2-jetson-01 | 🔴 Offline | 10 dias | `go2-jetson-01` |
| LAB-IR-1101 | 🔴 Offline | 10 dias | `LAB-IR-1101` |
| IE-3500-RING3 | 🔴 Offline | 17 dias | `Cisco-Docker` |

**Consecuencia practica: no hay que esperar al robot.** Con los dos agentes online se arma y
se valida toda la integracion; cuando el Go2 vuelva, `go2-jetson-01` cae en el mismo indice y
aparece solo en los paneles — estan escritos **por metrica, no por agente**.

> 📌 `LAB-IR-1101` offline hace 10 dias **contradice `PLAN.md` §388**, que lo da como
> *"RUNNING, intocable"*. Coincide con los 10 dias del Jetson: se apagaron juntos, coherente
> con que el IR1101 sea el enlace del robot. Corregir §388 cuando se toque.

### 3.2. Los indices

Dos indices separados, siguiendo el mismo criterio de `IPS-Y-DONDE-CAMBIARLAS.md` §127
(un indice y un token por origen, para poder revocar):

| Indice | Tipo | Que guarda |
|---|---|---|
| `thousandeyes` | **metrics** | `network.loss`, `network.latency`, `network.jitter` |
| `thousandeyes_alerts` | events | el webhook de alertas |

El de metricas **tiene que ser de tipo `metrics`**, no de eventos: el exporter `splunk_hec`
manda metricas y `mstats` no lee un indice de eventos.

```bash
sudo -u splunk /opt/splunk/bin/splunk add index thousandeyes        -datatype metric
sudo -u splunk /opt/splunk/bin/splunk add index thousandeyes_alerts
```

Y un token HEC propio, distinto del `Go2-01`, habilitado **solo** para esos dos indices.

### 3.2.b. Hecho el 31/08 — indices y token

```
Index "thousandeyes" added.          (datatype metric)
Index "thousandeyes_alerts" added.
http://thousandeyes  token=6f59****  index=thousandeyes
                     indexes=thousandeyes,thousandeyes_alerts
                     sourcetype=thousandeyes:otel  disabled=0
```

**Inventario completo de tokens HEC en `.20.200`** (salio del mismo `list`):

| Token | Indice | Sourcetype | De quien |
|---|---|---|---|
| `Go2-01` | `go2-robot-data` | — | agente del robot |
| `thousandeyes` | `thousandeyes` (+`_alerts`) | `thousandeyes:otel` | **nuevo, 31/08** |
| `WLC9800-Telemetry` | `wlc9800` | `cisco:wlc9800:telemetry` | la telemetria Cisco de §2.1.g |

> 🔎 Los 138 MB/dia de §2.1.g entran **por HEC**, igual que el robot. Practico: si hay que
> frenarlos de urgencia, **se deshabilita ese token** y listo, sin tocar el equipo Cisco:
> `splunk http-event-collector update WLC9800-Telemetry -disabled 1 -uri https://localhost:8089`

> 🔐 **Los tres tokens quedaron expuestos en claro** (terminal y `~/.bash_history`; el
> `Go2-01` desde agosto). Un token HEC es credencial de **escritura**: permite inyectar
> eventos y quemar la licencia. En LAN es tolerable, pero **rotar el de `thousandeyes` antes
> de exponer el HEC a internet** — es el que queda del lado publico.

### 3.3. Data Model v2 — los nombres reales

Verificado contra la documentacion de TE (OTel Data Model **v2**), no inventado:

| Metrica | Unidad | Ojo |
|---|---|---|
| `network.loss` | % | — |
| `network.latency` | **segundos** | ⚠️ **v1 la mandaba en ms.** Por eso el dashboard hace `*1000` |
| `network.jitter` | ms | — |

Atributos comunes a todos los tests, utiles como dimensiones:
`thousandeyes.test.name`, `thousandeyes.test.id`, `thousandeyes.test.type`,
`thousandeyes.source.agent.name`, `thousandeyes.source.agent.id`,
`thousandeyes.source.agent.location`, `thousandeyes.account.id`, `thousandeyes.permalink`.

Para un test **Agent-to-Server** se suman `server.address`, `server.port`,
`network.transport`, `error.type`.

Los paneles nuevos de `dashboard-go2.xml` (filas 5 y 6) ya estan escritos contra estos
nombres.

### 3.4. Costo de licencia

Las metricas de TE son chicas: 3 metricas por ronda de test, por agente. Con el agente del
Jetson del Go2 y unos pocos tests cada 1-5 min, el orden es de **pocos MB/dia** — entra
comodo en lo que queda de los 500 MB. Las alertas por webhook son practicamente cero
porque solo disparan cuando hay alerta.

Igual, el numero real hay que **medirlo** a las 24 h con la busqueda de §2.3 antes de
subir la frecuencia de los tests.

### 3.5. Verificacion cuando llegue el primer dato

```
| mcatalog values(metric_name) WHERE index=thousandeyes
```

Eso lista que metricas llegaron de verdad. Si aparecen `net.metrics.latency` /
`net.metrics.loss` / `net.metrics.jitter` en vez de las `network.*`, el stream esta en
**Data Model v1** y hay dos cosas para corregir: los nombres en el dashboard, y el `*1000`
de la latencia (en v1 ya viene en ms, no hay que multiplicar).

Para las alertas, cuando entre la primera:

```
index=thousandeyes_alerts sourcetype=thousandeyes:alert | head 1
```

y con esos campos a la vista, fijar las columnas del `table *` del panel de alertas — hoy
esta sin fijar a proposito porque el payload del webhook no esta verificado.

---

## 4. Orden de ejecucion

Estado al 31/08: pasos 1-3 hechos, **3 fallido** y por eso aparece el 3b.

| # | Paso | Estado | Necesita |
|---|---|---|---|
| 1 | Avisarle a la otra persona que pierde login y alertas | pendiente | — |
| 2 | Pasar a Free y reiniciar (§2.0) | ✅ **hecho 31/08** — `Free is_active:1` | — |
| 3 | Confirmar que el dashboard revive | ❌ **fallo** — 6 violaciones (§2.1.d) | — |
| 3b | **Pedir la licencia Developer** (§2.1.e) | **el bloqueo de hoy** | cuenta splunk.com |
| 3c | Instalarla **y cambiar el grupo a Enterprise** (§2.1.e) | espera 3b | el `.lic` |
| 3d | Vigilar que no entren warnings nuevos (§2.1.f) | continuo | — |
| 4 | Crear los dos indices y el token HEC (§3.2) | ✅ **hecho 31/08** (§3.2.b) | — |
| 5 | ~~Decidir pull vs push~~ | ✅ **decidido 31/08: camino oficial** (push). No hay pull: la app tampoco lo es (§3.1) | — |
| 5b | ~~Confirmar region~~ | ✅ **US2** (31/08) — 12 IPs en §3.1 | — |
| 5c | DNS publico + cert de CA publica + reverse proxy 443 + NAT (§3.1) | **el trabajo grueso**; despues de 3c. Plan completo en **§5** | borde/Meraki + DNS |
| 6 | **Puente `te-poller/`** mientras 5c no exista | ✅ **construido 31/08** (§6) — 12 tests, ruff limpio | falta `--probe` real |
| 6b | Correr `--probe` contra la cuenta real y ajustar fixtures | **el proximo paso concreto** | token de TE |
| 6c | Desplegar el puente en el server de Splunk | espera 6b | acceso a `.20.200` |
| 7 | **Migrar al oficial y borrar el puente** | el destino — receta paso a paso en **§5.4** | 5c |

| 8 | Verificar nombres con `mcatalog` (§3.5) | espera 3c + 6c | — |
| 9 | Medir consumo real a las 24 h (§2.1.f) | espera 6c | — |

**Si no se consigue la Developer:** la busqueda vuelve sola el **12/09**, cuando caduquen 4
de los 6 warnings. Todo lo demas (indices, token, alta de TE) se puede dejar listo antes.

## 5. Plan: migrar al camino OFICIAL

> **Léase primero:** lo que corre hoy es un **puente** (`te-poller/`, §7). El destino es la
> **streaming integration** de ThousandEyes. Este capítulo existe para que la migración no
> dependa de acordarse de nada.

### 5.1. Por qué el puente no es el destino

El poller entrega tres métricas de red. La integración oficial entrega **bastante más**, y
nada de eso se puede agregar al puente sin reescribirlo:

| | Puente (`te-poller`) | Oficial (streaming) |
|---|---|---|
| loss / latency / jitter | ✅ | ✅ |
| **Path visualization** | ❌ | ✅ |
| **Event Detection** | ❌ | ✅ |
| **Activity log** | ❌ | ✅ |
| Tests HTTP, DNS, transacciones, BGP, RTP | ❌ | ✅ |
| Dashboards de la app 7719 | ❌ no aplican | ✅ |
| Código a mantener | ~250 líneas + tests | **cero** |
| Frescura | intervalo de 5 min | push casi en vivo |
| Si falla | lo arreglamos nosotros | lo arregla Cisco |

**El puente se borra, no se extiende.** Cualquier pedido nuevo sobre datos de TE es una razón
para acelerar esta migración, no para agregarle una función al poller.

### 5.2. Los cinco requisitos, y quién los destraba

Ninguno es de Splunk ni de TE: los cinco son de **infraestructura de red**, y ninguno está
en esta PC ni en el server de Splunk.

| # | Requisito | Detalle verificado (§3.1) | Quién |
|---|---|---|---|
| 1 | **Nombre DNS público** | ej. `hec-splunk.silk-technologies.com` | quien administre el dominio |
| 2 | **Certificado de CA pública** | self-signed y CA privada **rechazados** | ídem, o Let's Encrypt |
| 3 | **Puerto 443** | TE **solo** admite 443; el HEC escucha en 8088 → hace falta reverse proxy | quien monte el proxy |
| 4 | **NAT entrante** | 443 → el host del proxy | quien administre el Meraki MX |
| 5 | **Allowlist** | las **12 IPs de US2** (§3.1) | ídem |

**Antes que nada, preguntar: ¿Silk ya tiene un wildcard `*.silk-technologies.com`?** Muchas
empresas lo tienen. Si existe, el punto 2 desaparece y no hay trámite.

Si no: **Let's Encrypt** con desafío **DNS-01** (se agrega un registro TXT, **no** hace falta
ningún puerto abierto para emitir) o HTTP-01 (necesita el 80 abierto).

### 5.3. El proxy

`Caddy` saca y renueva el certificado solo. El archivo entero:

```
hec-splunk.silk-technologies.com {
    reverse_proxy /services/collector* https://192.168.20.200:8088 {
        transport http { tls_insecure_skip_verify }
    }
}
```

Dos cosas deliberadas:

- **`tls_insecure_skip_verify`**: el certificado *interno* de Splunk sigue siendo self-signed
  y no importa — el que ve TE es el de Caddy. El tramo inseguro es LAN.
- **`/services/collector*`**: se publica **solo** el HEC. El resto de Splunk —`:8000` y el
  `:8089` de administración— queda inalcanzable desde afuera. **Esto es lo que hace
  defendible exponer algo**, y no es opcional.

### 5.4. Orden de la migración

| # | Paso | Verificación |
|---|---|---|
| 1 | Licencia posta instalada y grupo en Enterprise (§2.1.e) | la búsqueda anda |
| 2 | **Rotar el token HEC `thousandeyes`** (§3.2.b) | quedó expuesto en claro |
| 3 | DNS + certificado + proxy + NAT + allowlist (§5.2) | `curl` externo al endpoint |
| 4 | Crear el stream en el portal de TE | TE valida el alcance **al crear**: si acepta, la red está bien |
| 5 | **Convivencia**: dejar el poller andando un ciclo | ver que lleguen las dos fuentes al mismo índice |
| 6 | `systemctl disable --now splunk-te-poller` | — |
| 7 | Confirmar que los paneles siguen iguales | mismos nombres y unidades: **no deberían moverse** |
| 8 | **Borrar `te-poller/`** y esta nota | — |
| 9 | Instalar la app 7719 y sus dashboards | opcional, ya con datos oficiales |

> ⚠️ **El paso 1 es un prerrequisito de seguridad, no de comodidad.** Con Splunk en **Free no
> hay autenticación**: cualquier error del proxy que deje pasar una ruta de más expone una
> instancia sin credenciales. **No exponer nada antes de la licencia.**

> 💡 **Atajo para validar sin mover a IT:** esta PC tiene **Tailscale**. *Funnel* publica en
> 443 con DNS y certificado de Let's Encrypt automáticos, sin NAT ni firewall:
> ```
> sudo tailscale funnel --bg --https=443 \
>   --set-path=/services/collector https://192.168.20.200:8088/services/collector
> ```
> Sirve para **probar** que el stream se crea y el dato llega, antes de pedir la infra
> definitiva. **No sirve como destino**: por Funnel no se puede filtrar por IP de origen, así
> que el allowlist de las 12 IPs no aplica y queda todo colgado del token.

### 5.5. Señales de que hay que apurar esto

- Alguien pide path visualization, eventos o tests que no sean de red
- El poller acumula parches
- Aparece un segundo consumidor de datos de TE
- La licencia deja de ser el cuello de botella

---

## 6. El puente: `te-poller/`

**Construido el 31/08 porque los cinco requisitos de §5.2 dependen de terceros y el dato se
necesita antes.** Documentación completa en `te-poller/README.md`.

Qué es: un **productor** que consulta la API v7 de TE y emite envelopes de métricas en
stdout, encadenado al shipper que ya existe:

```
te_poller.py | ../../robot-telemetry-agent/shipper/hec_shipper.py
```

Reusa el spool, el batching, el backoff y el cap de bytes en vez de tener una segunda copia
de cada uno. Corre **en el server de Splunk**, así el salto al HEC es `localhost` y **no se
expone nada**.

**La decisión que hace gratis el cambio:** los nombres de métrica, las claves de dimensión y
las unidades son **exactamente** los del exportador OTel v2 de TE. Mismo índice, mismos
nombres, mismas unidades → **los paneles no distinguen una fuente de la otra**. Migrar es
apagar esto y prender el stream, sin tocar el dashboard.

> ⚠️ La trampa: la API v7 da `avgLatency` en **milisegundos**, OTel v2 da `network.latency`
> en **segundos**. El poller divide por 1000 para parecerse a OTel, **no** a la API que lee.
> Al revés, cada panel de latencia queda mal por 1000x y **se ve plausible**.

**Antes de confiar en un solo panel**, correr contra la cuenta real:

```bash
printf '%s' 'TOKEN-DE-TE' > ~/.te_bearer_token && chmod 600 ~/.te_bearer_token
./te_poller.py --probe
```

Vuelca la respuesta cruda de la API. Los fixtures de los tests están hechos sobre el esquema
`NetworkTestResult` **documentado**; si la respuesta real no coincide, **se arregla el fixture
primero y se deja fallar el test** — para eso está.

### 6.1. Verificado contra la cuenta real — 2026-09-01

`--probe` corrido contra `SILK TECH SRL - 178`. **El esquema documentado coincide con la
respuesta real**: `agent{agentId,agentName,countryId,location}`, `date`, `roundId`, `loss`,
`avgLatency`, `jitter`, `serverIp`. No hubo que tocar ningún fixture.

Una línea real, ya en formato Splunk:

```
test "Webex - silk - us - Video - 5004"  agente TE-ENTERPRISE-SILK
  network.loss    = 0.0        (%)
  network.latency = 0.087      (s)  <- 87 ms de la API, dividido por 1000
  network.jitter  = 0.81632656 (ms)
```

**Dos hallazgos de la corrida:**

1. **El test `Agent to Agent Test` está fallando**, y es el que involucra al robot. Devuelve
   rondas sin métricas, solo
   `errorDetails: "Target: Connection to source agent failed"` — coherente con
   `go2-jetson-01` offline hace 10 días (§3.1.b). El poller **emite cero** para esas rondas,
   que es lo correcto: un `loss=0` inventado ahí sería mentira. **Pero implica que un agente
   caído se ve como un hueco en el panel, no como una alerta.** La detección de caídas es una
   de las cosas que trae el camino oficial (Event Detection) y que el puente no da (§5.1).

2. **Hay tests de otras personas en la org** — los `Webex - silk - *` los creó otro usuario.
   El poller los levanta a todos. Es dato útil, pero **cuenta contra los 500 MB compartidos**:
   conviene mirar cuántos tests hay antes de dejarlo corriendo, y si son muchos, filtrar por
   nombre o subir `TE_POLL_INTERVAL_S`.

### 6.2. Bug encontrado y corregido en la primera corrida

`te_poller.py | head -3` reventaba con `BrokenPipeError` y traceback. En producción el
downstream es `hec_shipper`, así que **un shipper muerto habría parecido un bug del poller**.
Corregido en el entry point, con un test de regresión que **falla sin el arreglo**
(verificado sacándolo a propósito).

### 6.3. Dos tropiezos al desplegar — 2026-09-01

Los dos son de invocación, no del código, y los dos muerden a quien copie un comando a mano:

1. **`hec_shipper.py` está commiteado modo 644**, sin bit de ejecución. Invocarlo por ruta da
   `bash: ... Permission denied` a secas, que no dice nada sobre permisos de ejecución.
   **Siempre `python3 shipper/hec_shipper.py`** — es lo que hace el `run.sh` del agente, y
   ahora también el nuestro. (Corregido: el `run.sh` de `te-poller` tenía el mismo bug.)

2. **`VAR=x cmd1 | cmd2` NO le pasa `VAR` a `cmd2`.** El shipper es el segundo proceso del
   pipe, así que muere con `HEC_URL and HEC_TOKEN are required` mientras el poller arranca
   normal — parece un problema del shipper y es del shell. Hay que **exportar**. `run.sh` ya
   exporta; el problema aparece solo al armar el pipe a mano.

### 6.4. Primera corrida real contra Splunk — 2026-09-01

```
[te-poller] pass complete: 14 point(s)
[shipper] up: url=https://192.168.20.200:8088/services/collector cap=20971520B
[shipper] stdin closed, exiting
```

**Spool vacío = Splunk aceptó todo con 200.** Ningún 4xx, así que el índice quedó bien
creado como `metric` y el token escribe donde debe.

Y el watermark quedó demostrado en vivo: **14 puntos la primera pasada, 1 la segunda** 20 s
después. La ventana de 10 min se solapa a propósito para no perder una ronda tardía, y el
`roundId` es lo que evita que ese solapamiento se pague dos veces en licencia.

### 6.5. Consumo de licencia medido — 2026-09-01

Con el puente ya enviando, el mismo `license_usage.log` de §2.1.f:

| Indice | Sourcetype | MB/24h |
|---|---|---|
| `wlc9800` | `cisco:wlc9800:telemetry` | 83.18 |
| `wlc9800` | `cisco:urwb:telemetry` | 55.39 |
| **`thousandeyes`** | `thousandeyes:otel` | **0.00** |
| **`thousandeyes_alerts`** | `robot:shipper` | **0.00** |

`0.00` a dos decimales = **menos de 5 KB**. ThousandEyes **no mueve la aguja** de la licencia:
el presupuesto sigue siendo el Cisco (138 MB) y el agente del robot (cap 150 MB).

De paso confirma el circuito entero: las métricas entran por `192.168.20.200:8088` a
`thousandeyes`, y el self-health del shipper cae aparte en `thousandeyes_alerts` — que es
exactamente para lo que se separó (§6.3). En el índice de métricas cada beat habría dado 400.

### 6.6. Arranque automático — instalado 2026-09-01

Corre en **esta PC** (`ia-pc-G1-Pro`, `.20.99`), no en el server, como unit de sistema:

```
sudo cp systemd/te-poller-workstation.service /etc/systemd/system/te-poller.service
sudo systemctl daemon-reload && sudo systemctl enable --now te-poller
```

Verificado: `enabled` + `active (running)`, 25 MB de los 128 del tope, los tres procesos
(`run.sh` → `te_poller.py` → `hec_shipper.py`) bajo el mismo cgroup.

```
journalctl -u te-poller -f      # log en vivo
systemctl stop te-poller        # frenarlo
```

> ⚠️ **Falta la prueba real: reiniciar la PC.** Un `systemctl restart` no prueba `enabled`.

> ⚠️ **Esta PC es el lugar equivocado para algo que tiene que estar siempre.** Resuelve el
> arranque automático hoy, pero un escritorio se apaga. El server de Splunk no, y su unit ya
> está escrita (`systemd/splunk-te-poller.service`). Mudarlo = clonar dos repos y copiar un
> archivo. Hacerlo cuando la licencia haga que valga la pena tener el dato firme.

Estado: **13 tests pasan, `ruff` limpio, verificado contra la API real, contra Splunk, y
corriendo como servicio.**

---

## 7. Nota sobre los paneles nuevos

El chart de la fila 6 grafica las tres metricas **agregadas, sin abrir por test**. Es a
proposito: `mstats ... by <dim>` devuelve **filas**, no series en columnas como hace
`timechart`, asi que un chart de lineas con `by` sale mal. Si mas adelante hay varios tests
y hace falta compararlos, va en un panel aparte con la forma:

```
| mstats avg(network.loss) as loss WHERE index=thousandeyes span=1m by "thousandeyes.test.name"
| rename "thousandeyes.test.name" as test
| timechart span=1m avg(loss) by test
```

Mismo criterio con los nombres de dimension que llevan puntos: conviene `rename`-arlos a un
nombre simple antes de usarlos en `stats`/`chart`, en vez de pelearse con el quoting.
