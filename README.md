# Agentes de Campo — plugin para Omarchy

Widget de barra para [Omarchy](https://omarchy.org) que muestra tus agentes de IA que corren en un servidor remoto (VPS) y te deja:

- **Chatear** con cada agente: un clic en el nombre abre una terminal con la charla por SSH (vive en `tmux`, así que cerrar la ventana no la corta).
- **Ver avisos** por agente, con contador de no leídos.
- **Lecturas persistentes**: lo leído se guarda localmente (`~/.local/state/omarchy/campo-agentes/`) y sobrevive reinicios y cortes de conexión.

## Instalación

```bash
omarchy plugin add https://github.com/Blackhydra78/omarchy-campo-agentes.git --enable
```

Requisitos: `python3`, `ssh` y acceso por clave (sin contraseña) al servidor.

## Configuración

| Opción | Default | Qué es |
|---|---|---|
| `sshHost` | `vps` | Alias de `~/.ssh/config` (o `usuario@host`) del servidor |
| `refreshIntervalSec` | `60` | Cada cuánto se refresca (mínimo 15) |

Ejemplo de `~/.ssh/config`:

```
Host vps
  HostName tu.servidor.com
  User tu_usuario
```

## Lo que tiene que haber en el servidor

El plugin es solo el cliente. En el servidor tiene que existir un comando `campo-agentes` en el `PATH` que responda a esto:

| Comando | Devuelve |
|---|---|
| `campo-agentes list` | JSON: lista de agentes `[{"id", "nombre", "aliases": [], "estado", "avatar"}]` |
| `campo-agentes chat <id> [--previa]` | Chat interactivo en la terminal (se invoca con `ssh -t`) |
| `campo-agentes alertas --v2 [--desde EPOCH \| --cursor EPOCH:ID] --limite N` | JSON paginado: `{"version": 2, "items": [{"id", "epoch", ...}], "cursor": "EPOCH:ID", "mas": true\|false}` |

El cliente pagina `alertas` hasta `mas=false`, deduplica por `id` y guarda el cursor después de cada página. `id` de agente: solo `a-z` y `_`.

Avatares (opcional): poné PNGs en `assets/avatars/` y devolvé el nombre de archivo en el campo `avatar` de `list`. Sin avatar se muestra la inicial.

## Licencia

MIT
