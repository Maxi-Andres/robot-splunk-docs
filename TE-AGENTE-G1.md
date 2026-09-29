# Agente ThousandEyes en el G1 — runbook

**Escrito el 2026-09-29. Nada de esto está ejecutado todavía:** los dos robots estaban
apagados. Es el plan para replicar en el G1 lo que ya corre en el Go2, y el primer paso es
**mirar el Go2 prendido**, no adivinar cómo se instaló.

Lo que alimenta: la sección 2 y tres enlaces de la topología de
`dashboards/g1-telemetria-thousandeyes.xml`.

---

## 0. Qué hay hoy — relevado por API el 2026-09-29

| Agente | Estado | Último contacto | Hostname |
|---|---|---|---|
| `go2-jetson-01` | offline | 2026-09-23 22:13 UTC | `go2-jetson-01` |
| `LAB-IR-1101` | offline | 2026-09-23 22:13 UTC | `IR1101-GO2-01` |
| `TE-ENTERPRISE-SILK` | **online** | — | `TE-ENTERPRISE` |
| `TE-ENTERPRISE-IOT` | **online** | — | `TE-ENTERPRISE-IOT` |
| `IE-3500-RING3` | offline | 2026-08-14 | `Cisco-Docker` |

El Go2 tiene **9 tests** con la convención `Go2 - <letra><n> - <qué>`:

| Letra | Qué mide | Tests del Go2 |
|---|---|---|
| **A** | dentro del robot | A1 Jetson a IR1101 (interno) · A2 Throughput Fa0/0/1 |
| **B** | el camino | B1 WAN desde IR1101 · B2 WAN desde Jetson · B3 Camino a Splunk (`.20.200:8088`) |
| **C** | servicios de la app | C1 robot_executor 8090 · C2 camera_bridge 8091 · C4 Telemetría HEC 8088 |
| **D** | comandos | D1 Relay de comandos 8092 |

A1 es `agent-to-agent`, TCP 49153, cada 60 s. Los B3/C* son `agent-to-server`.

**El `te-poller` no hay que tocarlo:** trae todo test `agent-to-agent` / `agent-to-server`
de la cuenta y el inventario de todo agente `enterprise`. Un `g1-jetson-01` nuevo entra solo.

---

## 1. Con el Go2 prendido: copiar la receta, no inventarla

El agente del Go2 es un contenedor Docker `thousandeyes/enterprise-agent` llamado
`go2-jetson-01` en su Jetson (memoria `go2-jetson-agent-host`). Lo que hay que sacar de ahí
es **la línea de `docker run` exacta**: capabilities, volúmenes, `shm-size`, seccomp/apparmor,
política de reinicio. Con eso el G1 queda idéntico y no hay que depurar dos instalaciones
distintas.

```bash
# desde esta PC; en campo el Go2 está en 10.1.254.18 (túnel), en el lab en .123.18
ssh unitree@10.1.254.18

docker ps --filter name=go2-jetson-01
docker inspect go2-jetson-01 --format '{{.Config.Image}} {{.Config.Hostname}}
restart={{.HostConfig.RestartPolicy.Name}} net={{.HostConfig.NetworkMode}}
mem={{.HostConfig.Memory}} shm={{.HostConfig.ShmSize}}
cap={{.HostConfig.CapAdd}} secopt={{.HostConfig.SecurityOpt}}
binds={{.HostConfig.Binds}}'
# nombres de las variables, SIN valores: TEAGENT_ACCOUNT_TOKEN es un secreto
docker inspect go2-jetson-01 --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
docker image inspect "$(docker inspect -f '{{.Image}}' go2-jetson-01)" --format '{{.Architecture}} {{.Created}}'
docker version --format '{{.Server.Version}}'
ls -la /var/docker/thousandeyes/ /var/docker/configs/ 2>/dev/null
```

> ⚠️ **El account token no se copia del `inspect`.** Se saca de la UI de TE (*Cloud &
> Enterprise Agents → Agent Settings → Add New Enterprise Agent → Docker*) o del mismo env del
> Go2 **a mano**, y no se pega en ningún archivo del repo ni en un comentario.

Anotar el resultado en la §4 de este archivo con fecha.

---

## 2. Precondiciones en el G1

El agente va en **PC2, el Jetson `.123.164`**: es la única computadora del G1 con SSH. PC1
(`.161`) no tiene SSH en ningún puerto (ROADMAP §1) — ahí no se instala nada.

```bash
ssh unitree@192.168.123.164
uname -m; lsb_release -ds                 # esperado: aarch64, Ubuntu 20.04 (igual que el Go2)
df -h / ; free -h
docker version --format '{{.Server.Version}}' || echo "SIN DOCKER"
ip route                                  # ¿la default sale por wlan0 o por eth0?
ip route get 192.168.20.200               # ¿por dónde llega a Splunk?
timedatectl | grep -E 'synchronized|zone'
```

Tres cosas que ya se sabe que muerden en este Jetson (memoria `g1-wireless-vlan-setup`):

1. **La ruta vieja `192.168.123.99/32 via wlan0`.** Si sigue, `.123.164` es inalcanzable
   desde el cable: la respuesta sale por `wlan0` con origen `.123.164` y el Meraki la tira
   como spoofing. Borrarla antes de medir nada.
2. **El agente mide por donde rutea el kernel.** Hoy la default a internet sale por `wlan0`
   (métrica 600) → Meraki, VLAN 51. Eso **no** es CURWB. Si el objetivo es medir el enlace
   CURWB, los destinos de los tests tienen que estar detrás del bridge (`.20.x` vía
   `eth0`), y hay que confirmarlo con `ip route get` antes de confiar en un número.
3. **Un adaptador USB-C de red renombra las interfaces** (`eth0` pasa a ser el adaptador).
   Si hay uno enchufado, sacarlo y reiniciar.

Si no hay Docker: instalar `docker.io` desde apt (20.04 lo trae) — confirmar primero con la
§1 qué versión corre el Go2 para no divergir.

---

## 3. Instalar

Plantilla. **Reemplazar cada flag por lo que dio la §1** — lo de abajo es la forma del
comando oficial de TE, no lo que corre en el Go2.

```bash
NAME=g1-jetson-01
sudo mkdir -p /var/docker/thousandeyes/$NAME/{te-agent,te-browserbot,log}
read -rs -p 'TE account token: ' TOKEN; echo

sudo docker run -d --name "$NAME" --hostname "$NAME" \
  --restart unless-stopped --tty \
  --memory 2g --memory-swap 2g --shm-size 512M \
  --cap-add NET_ADMIN --cap-add NET_RAW --cap-add SYS_ADMIN \
  -e TEAGENT_ACCOUNT_TOKEN="$TOKEN" -e TEAGENT_INET=4 \
  -v /var/docker/thousandeyes/$NAME/te-agent:/var/lib/te-agent \
  -v /var/docker/thousandeyes/$NAME/te-browserbot:/var/lib/te-browserbot \
  -v /var/docker/thousandeyes/$NAME/log:/var/log/agent \
  thousandeyes/enterprise-agent /sbin/my_init
unset TOKEN
```

El nombre `g1-jetson-01` no es decorativo: es el valor del token `te_agent` del dashboard.
Si se enrola con otro, se cambia en **un** lugar, el `<init>` del XML.

---

## 4. Verificar

```bash
# desde esta PC — el agente tiene que aparecer online en 1-2 min
./te-poller/te-api.sh /agents | jq -r '.agents[] | select(.agentName=="g1-jetson-01") | "\(.agentId) \(.agentState) \(.lastSeen)"'
```

Y en Splunk, en la próxima vuelta del poller:

```
index=thousandeyes_alerts sourcetype=thousandeyes:agent agent_name="g1-jetson-01"
```

El panel *Agente ThousandEyes del robot* del tablero del G1 pasa de `NO ENROLADO` a
`ONLINE`. **Ese es el criterio de salida de la instalación.**

---

## 5. Los tests del G1

Mismos nombres que espera el dashboard (`<init>` de `g1-telemetria-thousandeyes.xml`). Si se
crean con otro nombre, los paneles no los ven: el stream OTel no trae los tags de TE y el
filtro es **por nombre**.

| Test | Tipo | Destino | Qué dice |
|---|---|---|---|
| `G1 - A1 - Jetson a TE-ENTERPRISE-SILK` | agent-to-agent, TCP 49153, 60 s | agente `TE-ENTERPRISE-SILK` | el enlace del robot: pérdida, latencia, jitter. Alimenta los singles y el nodo PC2 |
| `G1 - B3 - Camino a Splunk` | agent-to-server | `192.168.20.200:8088` | enlace Splunk de la topología |
| `G1 - C1 - robot_executor 8090` | agent-to-server | `192.168.20.99:8090` | enlace App Telecontrol de la topología |

Opcionales, en el mismo espíritu que los del Go2: `G1 - C2 - camera_bridge 8091`,
`G1 - C4 - Telemetria HEC 8088`. Entran solos a *Tests G1 — resumen*.

> 💡 **A1 contra el Mesh End en vez de contra `TE-ENTERPRISE-SILK`** mediría el aire del
> CURWB solo, sin la red del sitio. Es mejor test, pero hace falta conocer la IP del Mesh
> End, y hoy no está registrada en ningún doc.

Crear los tests es un cambio en la org de TE: se hace desde la UI o con un `POST` a
`/tests/agent-to-agent` y `/tests/agent-to-server`, **cuando el agente ya exista**.

---

## 6. Lo que esta instalación NO resuelve

- **No valida CURWB.** Un agente online con el cable puesto mide el cable. La validación es
  la de ROADMAP §6.3, con el cable **desenchufado físicamente**, y el agente sirve justamente
  para medirla una vez que se haga.
- **No trae telemetría del robot.** Batería, motores y DDS necesitan un lector
  `unitree_hg` que no existe (ROADMAP §6.5).
