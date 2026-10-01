#!/usr/bin/env python3
"""Adapt Claude's paginated feed to the compact panel's existing JSON contract."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import campo_feed as feed


def load_state(root, host):
    path = feed._dir() / feed.ARCHIVO
    if path.exists():
        # Fail closed on corruption; do not replace saved readings with old data.
        state = json.loads(path.read_text())
        if not all(isinstance(state.get(k), dict) for k in ('items', 'leidos')):
            raise ValueError('invalid state')
        return state
    state = feed._vacio()
    state.update(desde_inicial=0, listStatus='sin comprobar', alertsStatus='sin comprobar')
    digest = hashlib.sha256(host.encode()).hexdigest()[:16]
    legacy = root / ('inbox-' + digest + '.json')
    if legacy.exists():
        old = json.loads(legacy.read_text())
        state['items'] = {a['id']: a for a in old['alerts']}
        state['leidos'] = {i: int(time.time()) for i in old['readIds']}
        state['agentes'] = old['agents']
        state['listStatus'] = old.get('listStatus', 'sin comprobar')
        state['alertsStatus'] = old.get('alertsStatus', 'sin comprobar')
        state['migrado_de'] = [legacy.name]
    # Start at zero: a latest legacy timestamp does not prove all older events arrived.
    return state


def execute(root, host, action, ids):
    digest = hashlib.sha256(host.encode()).hexdigest()[:16]
    os.environ['CAMPO_AGENTES_SSH'] = host
    os.environ['CAMPO_AGENTES_STATE_DIR'] = str(root / ('v2-' + digest))
    with feed.bloqueo():
        state = load_state(root, host)
        stats = None
        if action == 'refresh':
            try:
                rows = json.loads(feed.remoto(['list']))
                if not isinstance(rows, list) or any(not isinstance(a, dict) or not a.get('id') for a in rows):
                    raise ValueError('invalid roster')
                state['agentes'] = rows
                state['agentes_ts'] = int(time.time())
                state['listStatus'] = 'ok'
            except Exception:
                state['listStatus'] = 'sin conexión'
            try:
                stats = feed.sincronizar(state)
                state['alertsStatus'] = 'ok'
            except Exception:
                state['alertsStatus'] = 'sin conexión'
        if action == 'read':
            for iid in ids:
                if iid in state['items']:
                    state['leidos'].setdefault(iid, int(time.time()))
        feed.guardar(state)
        menu = state.get('agentes') or []
        alerts = [{**a, 'agente_id': feed.agente_de(a, menu)} for a in state['items'].values()]
        alerts.sort(key=lambda a: (a.get('epoch', 0), a['id']), reverse=True)
        # 17/09: resuelto en el VPS (CENTINEL legítimo) cuenta como leído para el panel.
        resueltos = [a['id'] for a in alerts if a.get('resuelto') and a['id'] not in state['leidos']]
        return dict(agents=menu, alerts=alerts, readIds=list(state['leidos']) + resueltos,
                    listStatus=state.get('listStatus', 'sin comprobar'),
                    alertsStatus=state.get('alertsStatus', 'sin comprobar'), sync=stats)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--state-dir', type=Path, required=True)
    p.add_argument('--host', default='vps')
    p.add_argument('action', choices=['snapshot', 'refresh', 'read'])
    p.add_argument('ids', nargs='*')
    a = p.parse_args()
    try:
        print(json.dumps(execute(a.state_dir, a.host, a.action, a.ids), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error': 'No se pudo leer o guardar el estado local; lectura no confirmada.'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
