# El freno inyectado — por qué el robot caminaba a tirones

**Medido el 2026-09-10 contra el robot real, con el enlace por cable.** Documento de datos:
todo lo que está acá salió de capturar tráfico y cronometrar, no de leer código.

Compañero de `PUERTOS.md` (que dice qué cruza el enlace) y de `RED-Y-DDS.md` (que dice por
qué el DDS no cruza). Éste dice **por qué un defecto de 2 líneas se disfrazó de problema de
red durante semanas**.

---

## 1. El resumen, para el apurado

El ejecutor mandaba un **`stop_move` antes de cada `move`**. En LAN eso cuesta 15 ms y no se
nota. Sobre LTE cuesta ~55 ms de un ciclo de 155 ms, y **eso es lo que se veía como micro
tirones**.

El defecto no es independiente de la red: **convierte la latencia del enlace en tiempo de
frenado, y la multiplica**. Por eso el síntoma desapareció solo al pasar a cable, sin que
nadie arreglara nada — y por eso iba a volver apenas el robot volviera al campo.

**Arreglado el 2026-09-10** (§6). **Falta re-medirlo sobre LTE y sobre Starlink** (§7).

---

## 2. Qué se creía antes

`PUERTOS.md` §1 y el ROADMAP §2 lo tenían diagnosticado como jitter puro:

> *"el RTT al robot es 46 ms de media, 95 de pico (…) el ejecutor manda `Move()` a 10 Hz
> (cada 100 ms) y el jitter del enlace es del mismo orden. Eso es la causa de los micro
> tirones al caminar."*

Era una hipótesis razonable y **resultó ser media verdad**: el enlace es el combustible, pero
el que enciende el fuego es el código.

Dos cosas que ese diagnóstico no explicaba, y que ahora sí cierran:

- **Apagar el video no mejoraba nada.** El video aporta 11 ms de los 46 (medido el 09-09).
  Si el problema fuera saturación, apagarlo tendría que haberse notado.
- **El `MOVE_RATE_HZ=10` no es la cadencia real.** El ejecutor no manda a 10 Hz por el relay:
  manda a **6.5 Hz**, y cada envío arrastra un freno. Ver §4.

---

## 3. El mecanismo

En `unitree_ros2/robot_executor/robot_executor_service.py`, clase `RelayTransport`.

Cuando llega un `move` nuevo, `_start_move` hace esto:

```python
def _start_move(self, ...):
    with self._move_lock:
        self._stop_move_loop()      # (1) mata el loop anterior... y ESPERA
        ...
        self._move_thread.start()   # (3) recién ahora sale el move nuevo
```

y `_stop_move_loop` hace `t.join(timeout=2.0)`, o sea **espera a que el hilo viejo termine**.
El hilo viejo, antes de morir, ejecutaba:

```python
        self._move_stop.wait(self._REFRESH_S)
    self._post({"verb": "stop_move"})   # (2) ← POST HTTP BLOQUEANTE, sin guarda
```

Los tres pasos en orden: **el `move` nuevo no puede salir hasta que el `stop_move` viejo
completó su round trip por la red.**

Los otros dos transportes **no** tienen ese defecto. `Go2Ros2Transport` y `G1Ros2Transport`
guardan el frenazo con `if reached_deadline`, y su docstring lo dice explícito:

> *"StopMove is published ONLY when the deadline is actually reached — NOT when the loop is
> superseded by a newer move. That lets a teleop pad refresh a short bounded move every tick
> for smooth continuous motion."*

El relay se escribió copiando esa lógica **y se le perdió la guarda en la copia**. Es la
tercera copia del dead-man, la que el estándar §5 marca como el límite duro.

### La fórmula

```
ventana de freno  ≈  RTT completo de un comando  +  ~9 ms de handoff entre hilos
```

Los 9 ms son constantes (join + crear hilo + serializar el POST). El RTT **no**: lo pone el
enlace. Por eso el defecto escala con la red.

---

## 4. Los números medidos — cable, 2026-09-10

Capturados con un sniffer pasivo sobre `tcp/8092` (§8), 30 s de teleop real con joystick.

### Latencia del enlace

| Medición | Valor |
|---|---|
| ICMP, 300 paquetes @10 Hz **durante el movimiento** | min 1.16 · **medio 4.93** · max 16.97 ms · mdev 3.12 · **0% pérdida** |
| TCP connect al relay | 1.9 ms p50 |
| **RTT de `/cmd`** (el camino real del comando) | **6.3 ms p50**, 19.5 ms max |
| Throughput en curso | RX 425 KB/s · TX 75 KB/s |

Referencia histórica: **46 ms medio / 95 pico** sobre LTE (09-09), **0.25 ms** en LAN directa
(`PLAN-CONECTIVIDAD-ROBOTS.md` §124). El túnel por cable da 4.9 ms: ~10× mejor que LTE,
~20× peor que L2 directo.

### El freno, contado

| | |
|---|---|
| Comandos en 30 s | **320** (`move`=155, `stop_move`=164) — 10.7 cmd/s |
| Comandos realmente necesarios | 155 |
| **Tráfico de más** | **2.06×** |
| **`move` precedidos por un `stop_move`** | **154 de 155 = 99%** |
| Ventana de freno | **15.0 ms** p50 (min 5.0) |
| Cadencia real de teleop | 154.5 ms = **6.5 Hz** |
| **Porción del ciclo con el robot frenando** | **10%** |

La traza cruda, tal como se ve:

```
stop_move
move  vx=-0.292 vy=-0.009    (+  6.3ms)
stop_move                    (+154.1ms)
move  vx=-0.291 vy=-0.024    (+ 23.0ms)
stop_move                    (+122.1ms)
move  vx=-0.291 vy=-0.031    (+  6.3ms)
```

Verificación de la fórmula: 6.3 ms de RTT + ~9 ms de handoff = **15.3 ms** previsto contra
**15.0 ms** medido.

---

## 5. Lo que esto predice para el campo

Aplicando la fórmula a los RTT ya medidos. **Son predicciones, no mediciones** — por eso §7.

| Enlace | RTT de comando | Ventana de freno | Sobre un ciclo de 155 ms |
|---|---|---|---|
| L2 directo (histórico) | 0.25 ms | ~9 ms | 6% |
| **Cable + túnel (hoy, medido)** | **6.3 ms** | **15 ms** | **10%** — imperceptible |
| **LTE, medio** | 46 ms | **~55 ms** | **35%** |
| **LTE, pico** | 95 ms | **~104 ms** | **67%** |
| Starlink | sin medir | ? | ? |

A 67% del ciclo frenando, el robot pasa más tiempo recibiendo "pará" que "andá". Eso es
exactamente el síntoma que se reportó.

> ⚠️ **Corrección de una estimación previa.** En la sesión del 10-09 se estimó primero la
> ventana de freno como *"2.5× el RTT"*, lo que daba 115–240 ms sobre LTE. El modelo bueno es
> **RTT + 9 ms constantes**, que da 55–104 ms. La conclusión no cambia (el defecto domina el
> ciclo sobre LTE); la magnitud sí. Se anota porque este documento es evidencia y la
> aritmética tiene que poder auditarse.

---

## 6. El arreglo

Aplicado el 2026-09-10 en `robot_executor_service.py`, clase `RelayTransport`. Es copiar lo
que los otros dos transportes ya hacían:

1. **`_run_move_loop`** — el `stop_move` va detrás de `if reached_deadline`, dentro de un
   `try/finally`, igual que Go2 (`:439`) y G1 (`:751`).
2. **`_start_move`** — se agrega el clamp `max(0.1, min(step, MAX_STEP_S))`, que faltaba solo
   acá. Sin él, `duration_s=60` era una caminata de 60 s que el dead-man del robot **no**
   cortaba, porque el propio loop lo seguía alimentando cada `_REFRESH_S`.

**La seguridad no se debilita.** Lo que frena al robot cuando se suelta el control sigue
siendo, en este orden: el `stop` explícito (que postea `stop_move` por su cuenta), el
`shutdown()` del ejecutor, el deadline del propio move, y como red final el **dead-man de
1500 ms del relay en el robot** — que es el único que sigue funcionando si se corta el
enlace. Lo que se sacó es el frenazo que se mandaba *aunque nadie lo hubiera pedido*.

**Los dos tests que lo afirmaban ya existían**, como `xfail(strict=True)`:

- `test_a_new_move_supersedes_the_previous_one_without_injecting_a_halt[relay]`
- `test_an_absurd_duration_is_clamped_to_max_step[relay]`

Al aplicar el arreglo pasaron a `XPASS(strict)` → **pytest falló**, que es exactamente para
lo que sirve `strict=True`. Se borraron los marcadores. Los dos tests quedan parametrizados
sobre los **tres** transportes, así que la divergencia no puede volver sin ponerse en rojo.

Suite después del arreglo: **61 passed, 2 xfailed** en el executor (los 2 que quedan son el
P0 de `continuous`, otro tema). `ruff` limpio.

### 6.1. Verificado contra el robot real — 2026-09-10, cable

Se reinició el ejecutor con el código nuevo y se capturaron **90 s de teleop real** con el
mismo sniffer y el mismo método que la medición de §4, para que las dos sean comparables.

| | Antes (30 s) | **Después (90 s)** |
|---|---:|---:|
| `move` | 155 | 495 |
| `stop_move` | 164 | **19** |
| Comandos por segundo | 10.7 | **5.7** |
| **Moves precedidos por un freno** | **99%** | **4%** |
| Ratio comandos / necesarios | 2.06× | **1.04×** |
| Ventana de freno | 15.0 ms | **ninguna** |
| Cadencia de teleop | 6.6 Hz | 6.7 Hz (igual) |

**Los 19 `stop_move` que quedan son todos legítimos y eso se verificó uno por uno**: cada
uno está a 142–900 ms del `move` siguiente, o sea a un ciclo completo de teleop o más. Son
el deadline venciendo porque el refresh no llegó — el dead-man haciendo exactamente su
trabajo cuando se suelta el stick. **Inyecciones: 0.**

El patrón, ahora, es el que tenía que ser desde el principio:

```
move  vx=0.295 vy=-0.023          (+150.0ms)
move  vx=0.295 vy=-0.022          (+150.4ms)
move  vx=0.295 vy=-0.022 vyaw=0.448  (+152.5ms)
move  vx=0.295 vy=-0.023 vyaw=0.385  (+145.8ms)
```

y cuando se suelta:

```
move  vx=0.203 vy=-0.044          (+148.1ms)
stop_move                         (+150.5ms)   <- deadline vencido, correcto
move  vx=0.295 vy=-0.019          (+900.1ms)   <- se vuelve a empujar el stick
```

Enlace durante la prueba: 900 paquetes ICMP, **medio 4.29 ms**, mdev 2.79, **0% pérdida**,
con un pico aislado de 51.8 ms. **La cadencia de teleop no cambió** (6.6 → 6.7 Hz): el
arreglo no aceleró nada, sacó el freno. Y el tráfico hacia el robot **cayó a la mitad**,
que sobre un enlace de campo tarifado no es un detalle menor.

---

## 7. Lo que falta medir — LTE y Starlink

**Esto es lo importante que queda abierto.** El arreglo está hecho y verificado en cable,
donde el defecto casi no se notaba. **La prueba que vale es sobre el enlace donde sí dolía.**

Protocolo, para que las tres mediciones sean comparables:

- [x] **Cable** — línea de base tomada (§4) y **verificación post-fix hecha** (§6.1):
      inyecciones 0, tráfico a la mitad. Es la referencia con la que comparar las otras dos.
- [ ] **LTE** — con el robot en campo, detrás del IR1101.
- [ ] **Starlink** — mismo punto, misma hora del día si se puede. Ojo con lo de §10 del
      ROADMAP: la comparación LTE/Starlink nunca tuvo las variables aisladas.

Para cada enlace, y **en este orden**:

1. `ping -c 300 -i 0.1 <robot>` → medio, pico, mdev, pérdida.
2. RTT de `/cmd`: 10 POST de `keepalive` cronometrados con `curl -w '%{time_total}'`.
   ⚠️ **`keepalive` refresca el dead-man si el robot se está moviendo.** Medir con el robot
   **quieto**, o asumir el efecto a conciencia.
3. `iperf3` en los dos sentidos — es el único número de capacidad que falta; sin eso lo
   único que hay es utilización (RX 425 KB/s), que no es lo mismo.

   **Instalado el 2026-09-10 en las dos puntas.** Ojo con las versiones, porque **no
   coinciden y no pueden coincidir**: esta PC (Ubuntu 26.04 amd64) tiene **3.20**, y el
   Jetson (focal arm64, glibc 2.31, kernel 5.10.104-tegra) tiene **3.7**, que es lo último
   que hay para focal — el `.deb` arm64 de 3.20 está compilado contra una glibc que el
   Jetson no tiene. El robot **ya lo traía instalado**; se reinstaló igual para dejarlo
   verificado.

   Los `.deb` se sideloadearon desde esta PC (`ports.ubuntu.com`, 84 KB) en vez de correr
   `apt` en el robot, para no bajar listas de paquetes por el enlace de campo. Si hay que
   rehacerlo: `iperf3_3.7-3_arm64.deb` + `libiperf0_3.7-3_arm64.deb`.

   Topología: **servidor en esta PC** (`192.168.20.99:5201`), **cliente en el robot**, que
   es el que marca sentido con `-R`. El robot marcando hacia afuera es además lo que va a
   funcionar en el campo detrás del IR1101.
   - sin `-R` → mide **robot → HQ** (el sentido del video y la telemetría)
   - con `-R` → mide **HQ → robot** (el sentido de los comandos)
4. Sniffer (§8) durante 30 s de teleop → contar `stop_move` y la ventana de freno.
5. **Manejarlo y anotar si se siente el tirón**, que es el único juez que importa.

**Lo que hay que confirmar:** que después del arreglo la cuenta de `stop_move` durante teleop
sostenido caiga a ~0 y que la ventana de freno desaparezca, en los tres enlaces. Si sobre LTE
sigue habiendo tirones con `stop_move`=0, entonces sí es jitter puro y hay que volver a
`PUERTOS.md` §1 — pero ahora con la variable de código eliminada.

Tabla para llenar:

Normalizado a comandos **por segundo**, porque las corridas duran distinto.

| Enlace | ICMP medio/pico | RTT `/cmd` | iperf3 ↑ / ↓ | `stop_move`/s | Inyecciones | Ventana de freno | ¿Tirón? |
|---|---|---|---|---|---|---|---|
| Cable, **pre-fix** | 4.93 / 16.97 ms | 6.3 ms | — | 5.5 | **99%** | 15 ms | no (pero el defecto estaba) |
| Cable, **post-fix** | 4.29 / 51.83 ms | 6.3 ms | **42.5 / 89.0 Mbps** | **0.21** | **0** | ninguna | no |
| LTE | (46 / 95 el 09-09) | | | | | | |
| Starlink | | | | | | | |

### 7.1. Capacidad sobre cable — medido el 2026-09-10

Primera medición de capacidad del proyecto: hasta ahora solo había **utilización**
(RX 425 KB/s), que no dice cuánto entra sino cuánto está entrando.

| Sentido | Bitrate | Retransmisiones |
|---|---|---|
| **robot → HQ** (video, telemetría) | **42.5 Mbps** | **136** |
| **HQ → robot** (comandos) | **89.0 Mbps** | 20 |

Cuatro cosas que salen de acá:

1. **La interoperabilidad 3.7 ↔ 3.20 no es problema.** Era el riesgo que se quería descartar
   antes de ir al campo, y quedó descartado: la 3.7 del Jetson habla con la 3.20 de la PC
   sin una queja.
2. **El enlace es asimétrico, 2:1 en contra del sentido que más usamos.** Los ~89 Mbps de
   bajada están sospechosamente cerca del techo práctico de **100BASE-TX** (~94 Mbps), así
   que en algún punto del camino hay un tramo de 100 Mbps, no gigabit. Vale identificarlo.
3. **La subida tiene 136 retransmisiones y la bajada 20.** La ventana de congestión creció
   hasta **2.33 MB** y se derrumbó a 309 KB en el último segundo. Para un RTT de 4 ms a
   45 Mbps el BDP son ~22 KB: una ventana de 2.33 MB es **dos órdenes de magnitud** de más,
   o sea buffers enormes en el camino (bufferbloat) que se llenan y descartan de golpe. Es
   la misma clase de problema que ya mordió con el MJPEG ahogando al RTMP.
4. **Hay muchísimo margen en cable.** El video son ~450 KB/s = 3.6 Mbps, o sea **8.5% de la
   subida disponible**. En cable la capacidad no es la restricción — por eso los tirones no
   eran de ancho de banda ni siquiera antes del arreglo.

> 📌 **Dato de red anotado al pasar:** el cliente reportó `local 192.168.123.18`, o sea que
> el Jetson sale con su dirección de la subred baja del robot, no con una `10.1.254.x`. La
> `.18` del túnel es NAT. Relevante para `IPS-Y-DONDE-CAMBIARLAS.md`.

> ⚠️ **Nada de esto predice el campo.** Son los números del cable, que es el mejor caso.
> Sirven como techo y como control: si sobre LTE la subida da 3 Mbps, ya sabemos que el
> video solo entra si se lo capa.

---

## 8. Cómo se midió — el sniffer

`unitree_ros2/robot_executor/tests/_relay_sniff.py`. Es **pasivo**: un raw socket
`AF_PACKET`, no genera tráfico y no toca el robot.

```bash
docker exec unitree_ros2_devcontainer-devcontainer-humble-1 \
  python3 /workspace/robot_executor/tests/_relay_sniff.py
```

Corre **dentro del devcontainer** por una razón concreta: el container es `privileged` y
`NetworkMode=host`, así que ve las interfaces del host y tiene `CAP_NET_RAW` — mientras que
en el host `tcpdump` pide `sudo` y en el container no está instalado. Es el único camino sin
privilegios nuevos.

Imprime una línea por comando con el gap desde el anterior, así el freno inyectado se ve como
un `stop_move` metido a 6-15 ms de un `move`.

No lo colecta pytest (empieza con `_`).

---

## 9. Hallazgos laterales de la misma sesión

Los tres salieron de mirar el relay de cerca. Ninguno es de este tema.

- **`GET /health` del relay sigue sin autenticar** y devuelve la URL del HEC, el índice, el
  host de publicación del video y la interfaz DDS. Es el hallazgo que el estándar §2 ya cita
  (*"authenticate every method, not just the one you were thinking about"*), ahora verificado
  contra el robot vivo.
- **`/health` tarda 181 ms p50 dentro del relay** (máx 472), porque `_proc_env` escanea
  `/proc` tres veces por request. Es `ThreadingHTTPServer`, así que **no** bloquea `/cmd` —
  se confirmó midiendo los dos. Pero el dashboard lo pollea.
- **El robot corre con `MAXFPS=0`.** El default de `run-video.sh` y el `video.env.example`
  dicen `15`. En cable no molesta; en campo es la captura sin tope, que es justo lo que
  `PUERTOS.md` documenta como la causa del ahogo del RTMP.
- **`apt install iperf3` deja un servidor abierto y habilitado al boot.** El paquete de
  Ubuntu trae `iperf3.service` con `preset: enabled`, así que instalarlo levantó solo un
  listener en `*:5201`, **todas las interfaces, sin autenticación**. Verificado: responde
  también por la IP de tailscale (`100.109.28.67:5201`), no solo en la LAN — y `ufw` está
  apagado a propósito (`ENABLED=no`) porque prenderlo corta el DDS de los robots, así que
  no hay contención de red. Cualquiera en el tailnet puede saturar el enlace a voluntad.
  Es el patrón que el estándar §2 marca como blocker, sin excepción por *"está solo en la
  LAN"*.

  **Lo correcto es `sudo systemctl disable --now iperf3` y levantarlo a mano con
  `iperf3 -s -1` solo mientras se mide** (`-1` atiende una conexión y sale).

  **Afecta solo a esta PC. El robot está limpio, verificado el 2026-09-10:** el paquete de
  focal (`iperf3 3.7-3`) **no trae unit de systemd** — la unit se agregó en versiones
  posteriores de Debian/Ubuntu. `systemctl is-enabled iperf3` en el Jetson responde
  *"No such file or directory"*, y `10.1.254.18:5201` está cerrado. O sea que en el robot
  el binario está disponible y **nada queda escuchando**, que es exactamente lo que se
  quiere en un equipo que sale a campo.

  Consecuencia práctica para las mediciones: **el servidor va en la PC y el cliente en el
  robot** (que es la topología de §7 punto 3). Si alguna vez hace falta al revés, hay que
  levantarlo a mano en el Jetson con `iperf3 -s -1` y se cierra solo al terminar.
