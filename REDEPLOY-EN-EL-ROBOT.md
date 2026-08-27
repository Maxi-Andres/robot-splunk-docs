# Redeploy en el robot después del renombre

Escrito el **2026-08-27**, con el robot apagado. El 27 de agosto se renombraron los tres
proyectos y se partió el viejo `robot-splunk-bridge` en dos, así que los clones que están en
el robot tienen nombres, rutas y unidades systemd que ya no existen del lado del escritorio.
Este documento es el procedimiento para poner el robot al día cuando vuelva a estar
disponible.

**Nada de esto se probó contra el robot** — no estaba accesible (`192.168.123.18` y
`192.168.123.161` sin respuesta). El procedimiento sale de leer las unidades systemd y los
`.gitignore`, no de ejecutarlo allá. Verificá cada paso.

## Qué cambió

| Antes, en el robot | Ahora |
|---|---|
| `~/robot-splunk-bridge` | **partido en dos:** `~/robot-telemetry-agent` y `~/robot-command-relay` |
| `~/robot-nvr-bridge` | `~/robot-video-pipeline` |
| `robot-splunk-bridge.service` | `robot-telemetry-agent.service` |
| `robot-command-relay.service` | mismo nombre, rutas nuevas |
| `robot-video.service` | mismo nombre, rutas nuevas |
| `relay/relay.env` | `relay.env`, en la raíz del repo del relay |

En el robot los repos van **sueltos en `~`**, sin el paraguas `robot-ecosystem/` que existe en
el escritorio. Las unidades apuntan a `/home/unitree/<repo>`.

## Por qué borrar y reclonar, y no renombrar

Renombrar in situ implicaría arreglar a mano los remotos, las rutas de tres unidades y el
`relay/` que dejó de existir. Reclonar es más corto y deja el robot idéntico a lo que hay en
GitHub. El único cuidado es que hay **dos archivos que no están en git** y que el `rm -rf` se
lleva.

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
  echo "== $r"; git -C $r status --short
done
```

## Paso 1 — rescatar y auditar antes de borrar

```bash
ssh unitree@192.168.123.18

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

Detalle a tener en cuenta: el `/health` del relay contesta **sin token**, que es justamente
el hallazgo P0·5 de la auditoría. Cuando eso se arregle, ese `curl` va a necesitar el
`Authorization: Bearer`.

## Después, desde el escritorio

La pestaña **Robot** de Max's Control Panel lee la configuración del robot a través del
relay, así que en cuanto los tres servicios estén arriba y el transporte esté en modo
`relay`, esa página vuelve a mostrar valores reales. Sus instrucciones ya están actualizadas
con los nombres nuevos.
