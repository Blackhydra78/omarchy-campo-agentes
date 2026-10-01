#!/usr/bin/env python3
"""campo_feed.py — backend LOCAL del plugin campo.agentes (Omarchy) para el feed v2 del VPS (14/09/2026).

Por qué existe (hallazgo Codex 14/09): el plugin seguía llamando `campo-agentes alertas --desde 0`; el VPS devuelve
los PRIMEROS 50 y, pasado ese número, el cliente recibía siempre la misma página. Este backend:

  · recorre el feed v2 (`alertas --v2 --cursor EPOCH:ID`) hasta `mas=false`, guardando el estado DESPUÉS DE CADA
    PÁGINA: un corte a mitad conserva lo bajado y la próxima sincronización sigue desde ahí sin repetir;
  · re-pide un solape de SOLAPE_SEG al arrancar cada sync (ítems que cambian de estado, relojes) y deduplica por id;
  · persiste avisos, lecturas y cursor en ~/.local/state/omarchy/campo-agentes/feed-v2.json (escritura atómica,
    0600, lock) — sobrevive reinicios del shell, de la máquina y desconexiones;
  · importa (sin borrar) el estado del plugin 1.1.0/1.2.0: ids leídos e ítems guardados;
  · agrupa por el menú que manda el VPS (`campo-agentes list`): alias → agente canónico, ocultos → hermes.
    Celeste es agente propio.

Lectura: SOLO `leer <id>` (abrir el detalle de un aviso) marca leído. `resumen` (contadores), `avisos <agente>`
(lista del contador) y abrir un chat NO marcan nada.

Uso (siempre imprime JSON; exit 0 salvo argumentos inválidos o id inexistente en `leer`):
  campo_feed.py resumen [--sin-sync]   → {"agentes": [{id, nombre, no_leidos, total, ...}], "error", "offline"}
  campo_feed.py sync                   → {"nuevos", "paginas", "total", "error"}
  campo_feed.py avisos <agente>        → {"agente", "items": [... con "leido"]}   (nuevo → viejo)
  campo_feed.py leer <item_id>         → {"item": {...}, "leido": true}
Entorno: CAMPO_AGENTES_SSH (destino ssh, default vps), CAMPO_AGENTES_STATE_DIR (default XDG),
         CAMPO_AGENTES_CMD (pruebas: comando que reemplaza `ssh <destino> campo-agentes`), CAMPO_FEED_PAGINA.
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 3
ARCHIVO = "feed-v2.json"
SOLAPE_SEG = 600
MAX_PAGINAS = 400
RETENER_LEIDOS_DIAS = 90
MAX_LEIDOS = 20000
REPASO_COMPLETO_SEG = 3600
TTL_AGENTES_SEG = 300
# Respaldo si el VPS no responde y no hay lista cacheada (mismo menú que campo_chat.MENU).
MENU_RESPALDO = [
    {"id": "hermes", "nombre": "Hermes", "aliases": []}, {"id": "fernando", "nombre": "Fernando", "aliases": []},
    {"id": "celeste", "nombre": "Celeste", "aliases": []}, {"id": "sofia", "nombre": "Sofía", "aliases": []},
    {"id": "admin", "nombre": "Administración y Compras", "aliases": ["martin", "pablo"]},
    {"id": "mecanico", "nombre": "El Mecánico", "aliases": []},
]


def _dir() -> Path:
    if os.environ.get("CAMPO_AGENTES_STATE_DIR"):
        return Path(os.environ["CAMPO_AGENTES_STATE_DIR"])
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "omarchy" / "campo-agentes"


def _pagina_tam() -> int:
    try:
        return max(1, min(500, int(os.environ.get("CAMPO_FEED_PAGINA", "200"))))
    except ValueError:
        return 200


# ───────────────────────── VPS ─────────────────────────
def remoto(args: list[str], timeout: int = 60) -> str:
    cmd = os.environ.get("CAMPO_AGENTES_CMD")
    base = shlex.split(cmd) if cmd else [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        os.environ.get("CAMPO_AGENTES_SSH", "vps"), "campo-agentes"]
    r = subprocess.run(base + [str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"campo-agentes {' '.join(map(str, args[:1]))} salió {r.returncode}: {(r.stderr or '').strip()[:300]}")
    if args and args[0] == 'alertas' and r.stderr.strip():
        raise RuntimeError('El feed informó un error; no se considera una respuesta vacía válida')
    return r.stdout


# ───────────────────────── estado ─────────────────────────
@contextmanager
def bloqueo():
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".feed.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _vacio() -> dict:
    return {"version": VERSION, "cursor": None, "desde_inicial": None, "items": {}, "leidos": {},
            "agentes": None, "agentes_ts": 0, "ultima_sync": None, "ultimo_error": None, "migrado_de": []}


def guardar(estado: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".feed-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(estado, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, d / ARCHIVO)
        dirfd = os.open(d, os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cargar() -> dict:
    p = _dir() / ARCHIVO
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), dict):
                return {**_vacio(), **d}
        except (OSError, ValueError):
            roto = p.with_name(f"{ARCHIVO}.roto-{int(time.time())}")
            shutil.copy2(p, roto)            # nunca se pisa en silencio: queda la copia para revisar
    estado = _vacio()
    importar_legacy(estado)
    return estado


def _recorrer(obj, ruta=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _recorrer(v, f"{ruta}.{k}".lower())
    yield ruta, obj


def importar_legacy(estado: dict) -> None:
    """Estado del plugin 1.1.0/1.2.0 (formato no fijo): copia de respaldo y se importan ids LEÍDOS e ítems.
    'vistos'/'seen' solo deduplican: no se marcan como leídos. Los archivos originales no se tocan."""
    d = _dir()
    if not d.exists():
        return
    viejos = [p for p in d.glob("*.json") if p.name != ARCHIVO]
    if not viejos:
        return
    resp = d / f"backup-pre-feed-v2-{time.strftime('%Y%m%dT%H%M%S')}"
    resp.mkdir(exist_ok=True)
    max_epoch = None
    for p in viejos:
        shutil.copy2(p, resp / p.name)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for ruta, v in _recorrer(data):
            clave = ruta.rsplit(".", 1)[-1]
            if any(x in clave for x in ("leid", "read")):
                ids = v if isinstance(v, list) else list(v) if isinstance(v, dict) else []
                for i in ids:
                    if isinstance(i, str) and i:
                        estado["leidos"].setdefault(i, int(time.time()))
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and isinstance(it.get("id"), str) and it.get("texto") is not None:
                        estado["items"].setdefault(it["id"], it)
                        if isinstance(it.get("epoch"), (int, float)):
                            max_epoch = max(max_epoch or 0, int(it["epoch"]))
        estado["migrado_de"].append(p.name)
    # Sin hueco: se pide desde el último ítem que ya tenía el plugin viejo (>=, dedupe por id).
    estado["desde_inicial"] = max_epoch


# ───────────────────────── sincronización ─────────────────────────
def _cursor_mayor(a: str | None, b: str | None) -> str | None:
    def k(c):
        ep, _, iid = str(c).partition(":")
        return int(ep), iid
    if not a:
        return b
    if not b:
        return a
    return a if k(a) >= k(b) else b


def sincronizar(estado: dict) -> dict:
    tam = _pagina_tam()
    completo = estado.get("repaso_en_curso") or time.time() - estado.get("ultimo_repaso", 0) >= REPASO_COMPLETO_SEG
    if completo:
        estado["repaso_en_curso"] = True
        previo = estado.get("repaso_cursor")
        pedido = ["alertas", "--v2", "--limite", tam]
        pedido += ["--cursor", previo] if previo else ["--desde", 0]
    elif estado.get("cursor"):
        ep = int(str(estado["cursor"]).partition(":")[0])
        pedido = ["alertas", "--v2", "--cursor", f"{max(0, ep - SOLAPE_SEG)}:0", "--limite", tam]
    else:
        desde = estado.get("desde_inicial")
        desde = int(desde) if desde is not None else int(time.time() - 24 * 3600)
        pedido = ["alertas", "--v2", "--desde", desde, "--limite", tam]
    nuevos = paginas = 0
    while True:
        d = json.loads(remoto(pedido))
        if not isinstance(d, dict) or d.get("version") != 2 or not isinstance(d.get("items"), list):
            raise RuntimeError("el VPS no devolvió el feed v2 (¿campo-agentes viejo?)")
        if d.get("mas"):
            anterior = pedido[pedido.index("--cursor") + 1] if "--cursor" in pedido else None
            nuevo = d.get("cursor")
            if not nuevo or nuevo == anterior or (anterior and _cursor_mayor(anterior, nuevo) != nuevo):
                raise RuntimeError("El cursor del feed no avanza")
        for it in d["items"]:
            iid = it.get("id") if isinstance(it, dict) else None
            if not iid:
                continue
            if iid not in estado["items"]:
                nuevos += 1
            estado["items"][iid] = {**estado["items"].get(iid, {}), **it}
        estado["cursor"] = _cursor_mayor(estado.get("cursor"), d.get("cursor"))
        if completo:
            estado["repaso_cursor"] = d.get("cursor")
        paginas += 1
        guardar(estado)                                     # progreso por página
        if not d.get("mas"):
            break
        if paginas >= MAX_PAGINAS or not d.get("cursor"):
            raise RuntimeError(f"feed sin fin tras {paginas} páginas")
        pedido = ["alertas", "--v2", "--cursor", d["cursor"], "--limite", tam]
    podar(estado)
    if completo:
        estado["repaso_en_curso"] = False
        estado["repaso_cursor"] = None
        estado["ultimo_repaso"] = int(time.time())
    estado["ultima_sync"] = int(time.time())
    estado["ultimo_error"] = None
    guardar(estado)
    return {"nuevos": nuevos, "paginas": paginas, "total": len(estado["items"])}


def podar(estado: dict) -> None:
    """Saca ítems LEÍDOS viejos (el id leído queda: si el VPS lo reenviara no vuelve a contar)."""
    corte = time.time() - RETENER_LEIDOS_DIAS * 86400
    for iid in [i for i, it in estado["items"].items()
                if i in estado["leidos"] and float(it.get("epoch") or 0) < corte]:
        del estado["items"][iid]
    # Read IDs are durable tombstones: dropping them would resurrect old alerts
    # when a full reconciliation receives those IDs again.


def agentes(estado: dict, refrescar: bool) -> list[dict]:
    if refrescar or not estado.get("agentes") or time.time() - estado.get("agentes_ts", 0) > TTL_AGENTES_SEG:
        lista = json.loads(remoto(["list"]))
        if isinstance(lista, list) and lista:
            estado["agentes"], estado["agentes_ts"] = lista, int(time.time())
    return estado.get("agentes") or MENU_RESPALDO


def agente_de(item: dict, menu: list[dict]) -> str:
    ids = {a["id"] for a in menu}
    alias = {al: a["id"] for a in menu for al in a.get("aliases") or []}
    for cand in (item.get("agente_id_original"), item.get("agente_id")):
        if cand in ids:
            return cand
        if cand in alias:
            return alias[cand]
    return "hermes" if "hermes" in ids else next(iter(ids), "hermes")


# ───────────────────────── comandos ─────────────────────────
def cmd_resumen(sin_sync: bool) -> dict:
    with bloqueo():
        estado = cargar()
        error = None
        if not sin_sync:
            try:
                sincronizar(estado)
            except Exception as e:                        # sin red: se muestran los contadores guardados
                error = f"{type(e).__name__}: {e}"
                estado["ultimo_error"] = error
        try:
            menu = agentes(estado, refrescar=False)
        except Exception as e:
            error = error or f"{type(e).__name__}: {e}"
            menu = estado.get("agentes") or MENU_RESPALDO
        guardar(estado)
    cuenta = {a["id"]: [0, 0] for a in menu}
    for iid, it in estado["items"].items():
        aid = agente_de(it, menu)
        c = cuenta.setdefault(aid, [0, 0])
        c[1] += 1
        if iid not in estado["leidos"]:
            c[0] += 1
    salida = [{**a, "no_leidos": cuenta.get(a["id"], [0, 0])[0], "total": cuenta.get(a["id"], [0, 0])[1]}
              for a in menu]
    return {"version": VERSION, "agentes": salida, "ultima_sync": estado.get("ultima_sync"),
            "error": error, "offline": error is not None}


def cmd_sync() -> dict:
    with bloqueo():
        estado = cargar()
        try:
            return {**sincronizar(estado), "error": None}
        except Exception as e:
            estado["ultimo_error"] = f"{type(e).__name__}: {e}"
            guardar(estado)
            return {"nuevos": None, "paginas": None, "total": len(estado["items"]), "error": estado["ultimo_error"]}


def cmd_avisos(agente: str) -> dict:
    with bloqueo():
        estado = cargar()
    menu = estado.get("agentes") or MENU_RESPALDO
    items = [{**it, "leido": iid in estado["leidos"]} for iid, it in estado["items"].items()
             if agente_de(it, menu) == agente]
    items.sort(key=lambda x: (int(x.get("epoch") or 0), str(x.get("id"))), reverse=True)
    return {"agente": agente, "items": items}


def cmd_leer(item_id: str) -> tuple[dict, int]:
    with bloqueo():
        estado = cargar()
        it = estado["items"].get(item_id)
        if not it:
            return {"error": f"no hay un aviso {item_id}"}, 1
        estado["leidos"].setdefault(item_id, int(time.time()))
        guardar(estado)
    return {"item": {**it, "leido": True}, "leido": True}, 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("resumen", "sync", "avisos", "leer"):
        print(json.dumps({"error": "uso: campo_feed.py resumen [--sin-sync] | sync | avisos <agente> | leer <id>"}))
        return 2
    if argv[0] == "resumen":
        print(json.dumps(cmd_resumen("--sin-sync" in argv), ensure_ascii=False))
        return 0
    if argv[0] == "sync":
        print(json.dumps(cmd_sync(), ensure_ascii=False))
        return 0
    if len(argv) != 2:
        print(json.dumps({"error": f"uso: campo_feed.py {argv[0]} <valor>"}))
        return 2
    if argv[0] == "avisos":
        print(json.dumps(cmd_avisos(argv[1]), ensure_ascii=False))
        return 0
    out, rc = cmd_leer(argv[1])
    print(json.dumps(out, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
