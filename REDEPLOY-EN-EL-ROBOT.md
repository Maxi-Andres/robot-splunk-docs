# Redeploy en el robot después del renombre

Escrito el **2026-08-27**, con el robot apagado. El 27 de agosto se renombraron los tres
proyectos y se partió el viejo `robot-splunk-bridge` en dos, así que los clones que están en
el robot tienen nombres, rutas y unidades systemd que ya no existen del lado del escritorio.
Este documento es el procedimiento para poner el robot al día cuando vuelva a estar
disponible.

**Nada de esto se probó contra el robot** — no estaba accesible (`192.168.123.18` y
`192.168.123.161` sin respuesta). El procedimiento sale de leer las unidades systemd y los
`.gitignore`, no de ejecutarlo allá. Verificá cada paso.

> ⛔ **Correcciones del 2026-09-09/10 — el runbook original fallaba en tres cosas.**
>
> **1. La rama. RESUELTO el 2026-09-10.** Durante un tiempo todo el trabajo vivió en `dev`
> mientras la rama por defecto quedaba atrás — 5, 4 y 8 commits en cada repo — así que un
> `git clone` pelado dejaba el robot con código viejo **sin fallar en el momento**. Se
> unificó: `dev` se mergeó a la rama principal por PR y **el robot vive en la principal**
> — verificado en el robot el 10-09, los tres repos en `main`/`master` y los tres
> servicios `active` sin recompilar (el contenido era idéntico). El `git clone` de abajo
> vuelve a ser correcto.
>
> Igual, la lección queda: **verificá qué rama clonaste antes de compilar** (paso 3), porque
> el modo en que esto falla es silencioso.
>
> ⚠️ `robot-command-relay` usa **`master`** y los otros dos `main`. Un `for` sobre los tres
> se rompe en ese. Pendiente unificarlo desde GitHub.
>
> **2. La IP de SSH depende de dónde esté el robot.** `192.168.123.18` sirve solo con el
> robot en la LAN local. En campo, detrás del IR1101, se entra por el NAT del túnel:
> **`10.1.254.18`**. Verificado el 09-09: `.123.18` sin respuesta, `10.1.254.18:22` abierto.

> ⛔ **Tercera corrección del 2026-09-09 — la que más costó encontrar.**
>
> **Los `.env` que rescatás traen rutas del repo VIEJO adentro.** El runbook los trata como
> si fueran solo valores propios del robot, y no: `relay.env` tenía
>
> ```
> SENDER_BIN=/home/unitree/robot-splunk-bridge/command_sender
> ```
>
> apuntando al repo que el `rm -rf` acababa de borrar. El relay arrancaba, no encontraba el
> binario, salía con `FileNotFoundError`, y systemd lo bloqueaba tras cinco reintentos con
> *"Start request repeated too quickly"*.
>
> Y es más sutil de lo que parece: **la unidad systemd ya traía la ruta correcta** en
> `Environment=SENDER_BIN=...`, pero `EnvironmentFile=` viene después y **gana el último**.
> O sea que el `.env` rescatado pisaba el valor bueno.
>
> **Después de restaurar los `.env`, buscá rutas muertas:**
>
> ```bash
> grep -nE "robot-splunk-bridge|robot-nvr-bridge" \
>   ~/robot-command-relay/relay.env ~/robot-video-pipeline/robot/video.env
> ```
>
> Lo que se hizo el 09-09 fue más simple y quedó más limpio: **copiar el `.example` encima**.
> Los valores de seguridad coincidían (`MAX_VX`, `MAX_VY`, `MAX_VYAW`, `DEADMAN_MS`) y el
> ejemplo ya trae la ruta nueva. Verificá el diff antes: si alguien había bajado un límite de
> velocidad a mano, el ejemplo te lo sube de vuelta sin avisar.
>
> ```bash
> cd ~/robot-command-relay
> cp relay.env ~/env-backup/relay.env.pre-limpieza
> diff <(grep -E "^[A-Z_]+=" relay.env | sort) <(grep -E "^[A-Z_]+=" relay.env.example | sort)
> cp relay.env.example relay.env
> sudo systemctl reset-failed robot-command-relay   # destraba el limite de reintentos
> sudo systemctl restart robot-command-relay
> ```

### Lo que se pierde si no lo rescatás

| Archivo | Qué guarda | Por qué no está en git |
|---|---|---|
| `robot/video.env` | `PUBLISH_HOST`, protocolo, bitrate, fps | Gitignoreado a propósito: un `git pull` nunca debe sobreescribir la dirección a la que este robot publica |
| `relay/relay.env` | Configuración del relay | Ídem. Y **cambió de ruta**: ahora es `relay.env` en la raíz |

### Lo que sobrevive, porque vive fuera de los repos

- `~/.relay_token` y `~/.splunk_hec_token` — los tokens están en `$HOME`, intactos.
- `/var/tmp/robot-splunk-spool/` — la telemetría encolada. El agente nuevo la drena sola.
- `/var/tmp/robot-relay-audit.log` — el log de auditoría del relay.

## Paso 0 — antes de tocar el robot

El robot clona de GitHub, así que primero tiene que estar todo pusheado desde el escritorio:

```bash
cd ~/Desktop/robot-ecosystem
for r in robot-splunk-docs robot-video-pipeline robot-telemetry-agent robot-command-relay; do
  echo "== $r  (rama: $(git -C $r branch --show-current))"
  git -C $r status --short
  git -C $r log --oneline @{u}..HEAD          # commits sin pushear
done
```

Y **confirmá que la rama que vas a clonar tiene lo último**, que es donde falla el
procedimiento original:

```bash
for r in robot-video-pipeline robot-telemetry-agent robot-command-relay; do
  git -C $r fetch -q origin
  def=$(git -C $r symbolic-ref --short refs/remotes/origin/HEAD | sed 's|origin/||')
  printf "%-24s default=%-7s dev adelante por %s commits\n" \
    "$r" "$def" "$(git -C $r log --oneline origin/$def..origin/dev | wc -l)"
done
```

Si alguno da distinto de 0, **cloná `dev`** (paso 3) o mergeá antes.

## Paso 1 — rescatar y auditar antes de borrar

```bash
# En LAN local: 192.168.123.18 · En campo, por el tunel: 10.1.254.18
ssh unitree@10.1.254.18

mkdir -p ~/env-backup
cp ~/robot-nvr-bridge/robot/video.env    ~/env-backup/ 2>/dev/null
cp ~/robot-splunk-bridge/relay/relay.env ~/env-backup/ 2>/dev/null
ls -l ~/env-backup/

# ¿hay commits hechos a mano en el robot que nunca se pushearon?
for d in ~/robot-splunk-bridge ~/robot-nvr-bridge; do
  echo "== $d"; git -C $d status --short; git -C $d log --oneline @{u}..HEAD 2>/dev/null
done
```

Si ese último comando imprime algo, **pará ahí**: hay trabajo hecho en el robot que el
`rm -rf` se lleva. Rescatalo con `git format-patch` o `git bundle` antes de seguir.

## Paso 2 — parar y deshabilitar los servicios viejos

```bash
sudo systemctl disable --now robot-splunk-bridge robot-command-relay robot-video
sudo rm -f /etc/systemd/system/robot-splunk-bridge.service
sudo systemctl daemon-reload
```

`robot-command-relay.service` y `robot-video.service` conservan el nombre, así que se
sobreescriben en el paso 6. El único que desaparece es `robot-splunk-bridge.service`.

## Paso 3 — borrar y clonar

```bash
rm -rf ~/robot-splunk-bridge ~/robot-nvr-bridge
cd ~
git clone https://github.com/Maxi-Andres/robot-telemetry-agent.git
git clone https://github.com/Maxi-Andres/robot-command-relay.git
git clone https://github.com/Maxi-Andres/robot-video-pipeline.git

# Verifica la rama ANTES de compilar. El robot va en la principal (`main`, y `master` en el
# relay); `dev` es desarrollo y NO va al robot. Si alguno cae en otra rama, algo se
# desincronizo del lado de GitHub y hay que mirarlo antes de seguir.
for d in robot-telemetry-agent robot-command-relay robot-video-pipeline; do
  echo "$d -> $(git -C ~/$d branch --show-current)"
done
```

## Paso 4 — restaurar los `.env`

```bash
cp ~/env-backup/video.env ~/robot-video-pipeline/robot/video.env
cp ~/env-backup/relay.env ~/robot-command-relay/relay.env    # antes: relay/relay.env
bash ~/robot-video-pipeline/robot/sync-env.sh                # agrega claves nuevas sin pisar tus valores
```

## Paso 5 — compilar los tres

Los binarios están gitignoreados, así que un clone nuevo no trae ninguno.

```bash
cd ~/robot-telemetry-agent && ./build.sh     # telemetry_reader
cd ~/robot-command-relay   && ./build.sh     # command_sender
cd ~/robot-video-pipeline  && ./build.sh     # go2_jpeg_stream
```

Si no encuentra el SDK: `UNITREE_SDK2_DIR=~/unitree_sdk2 ./build.sh`.

## Paso 6 — instalar las unidades nuevas

```bash
sudo cp ~/robot-telemetry-agent/systemd/robot-telemetry-agent.service /etc/systemd/system/
sudo cp ~/robot-command-relay/systemd/robot-command-relay.service     /etc/systemd/system/
sudo cp ~/robot-video-pipeline/robot/robot-video.service              /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robot-telemetry-agent robot-command-relay robot-video
```

## Paso 7 — verificar

```bash
systemctl is-active robot-telemetry-agent robot-command-relay robot-video
curl -s localhost:8092/health | head -c 400     # relay: verbos, límites, video, telemetría
curl -s localhost:8093/health                    # el MJPEG del robot
journalctl -u robot-telemetry-agent -n 20 --no-pager
ls /var/tmp/robot-splunk-spool/ | wc -l          # debería ir bajando
```

### El video no llega: mirá primero el lado del servidor

Ejecutado el 2026-09-09, y esto costó una hora. Con los tres servicios `active` y el robot
capturando a 14 fps, **el video igual no llegaba**. Dos causas encadenadas:

1. **La unidad de usuario `robot-video-pipeline` de la PC de HQ corría en modo captura
   local** y publicaba al mismo path `robot` de mediamtx. **Un path admite un solo
   publisher**, así que el `rtmpsink` del robot conectaba y moría con
   `Could not write to resource / Failed to write data`.
2. ⚠️ **Y `run.sh` de esa unidad NO corre solo la captura: también levanta mediamtx.**
   Pararla para liberar el path **mata al receptor** y deja al robot publicando contra nada.

**El arreglo correcto es `SERVER_ONLY=1`**, que es el modo que corresponde desde que la
captura vive en el robot: mediamtx sí, captura local no.

```bash
mkdir -p ~/.config/systemd/user/robot-video-pipeline.service.d
printf '[Service]\nEnvironment=SERVER_ONLY=1\n' \
  > ~/.config/systemd/user/robot-video-pipeline.service.d/override.conf
systemctl --user daemon-reload && systemctl --user restart robot-video-pipeline
```

Verificación desde HQ, en este orden:

```bash
pgrep -af mediamtx                                   # el receptor tiene que estar vivo
pgrep -af go2_jpeg_stream                            # NO debe haber captura local
ss -tn | grep :1935                                  # ESTAB desde la IP del robot
curl -s localhost:5000/api/stats | python3 -c "import sys,json;print(json.load(sys.stdin)['cameras'])"
```

Resultado del 09-09: `ESTAB 192.168.20.99:1935 ← 10.1.254.18`, Frigate en **5.1 fps** estables.

---

Detalle a tener en cuenta: el `/health` del relay contesta **sin token**, que es justamente
el hallazgo P0·5 de la auditoría. Cuando eso se arregle, ese `curl` va a necesitar el
`Authorization: Bearer`.

## Después, desde el escritorio

La pestaña **Robot** de Max's Control Panel lee la configuración del robot a través del
relay, así que en cuanto los tres servicios estén arriba y el transporte esté en modo
`relay`, esa página vuelve a mostrar valores reales. Sus instrucciones ya están actualizadas
con los nombres nuevos.
