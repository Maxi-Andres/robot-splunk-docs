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

### 3.1. Como entra el dato hoy (y por que no es un pull)

ThousandEyes **Integrations 2.0** empuja OpenTelemetry a un **HEC de Splunk** con un
conector `splunk-hec`. No hay modular input ni pull desde Splunk: **es TE el que manda**,
a `https://<host>:8088/services/collector/event`.

Esto tiene una consecuencia buena y una mala.

**Buena:** el HEC **no depende del nivel de licencia**. Este camino funciona igual en Free.

**Mala — y es el bloqueo real:**

> 🚧 **TE es cloud y `192.168.20.200` es privada.** El stream sale de la plataforma de
> ThousandEyes en internet hacia el HEC. Hoy ese HEC vive en VLAN 20, sin NAT entrante.
> **ThousandEyes no lo puede alcanzar.** No es un tema de token ni de configuracion: no hay
> camino de red.

Opciones, de menos a mas expuesto:

| Opcion | Que implica |
|---|---|
| **Publicar el HEC** con NAT entrante + TLS valido + token dedicado | Es lo que pide TE. Pone `:8088` en internet — con Splunk Free y sin auth atras, hay que pensarlo bien |
| **Reverse proxy** que solo exponga `/services/collector/event` | Reduce la superficie a un endpoint y permite filtrar por IP de origen de TE |
| **App de Splunkbase** (`Cisco ThousandEyes App for Splunk`, app 7719, v0.9.0 del 24/08/2026) | Va al reves: **Splunk sale** a la API de TE. Solo necesita egress HTTPS, que la caja privada probablemente ya tiene. **Es el unico camino que no expone nada** |

**Recomendacion:** empezar por la app (pull). Es la que encaja con una Splunk privada.
El streaming a HEC queda para cuando haya una decision sobre exponer `:8088`.

> Ojo con la app en Free: pide **CIM 6.x**, y sus dashboards propios usan modelos de datos
> acelerados. La **aceleracion es Enterprise**, asi que en Free los dashboards que trae la
> app pueden venir degradados. Los paneles nuestros de §3.3 son SPL plano y no dependen de eso.

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
| 4 | Crear los dos indices y el token HEC (§3.2) | **se puede hacer ya** — no depende de la busqueda | acceso a la caja |
| 5 | Decidir pull (app) vs push (exponer `:8088`) (§3.1) | pendiente | decision, no tecnica |
| 6 | Dar de alta el origen de TE segun el paso 5 | espera 5 | token de la org de TE |
| 7 | Verificar nombres con `mcatalog` y ajustar si es v1 (§3.5) | espera 3c + 6 | — |
| 8 | Medir consumo real a las 24 h (§2.1.f) | espera 6 | — |

**Si no se consigue la Developer:** la busqueda vuelve sola el **12/09**, cuando caduquen 4
de los 6 warnings. Todo lo demas (indices, token, alta de TE) se puede dejar listo antes.

## 5. Nota sobre los paneles nuevos

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
