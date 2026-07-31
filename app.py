# -*- coding: utf-8 -*-
import json
import os
import re
import sqlite3
import time

from flask import Flask, g, jsonify, redirect, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'gps.db'))
VENDORS_FILE = os.path.join(BASE_DIR, 'vendors.json')
PDV_FILE = os.path.join(BASE_DIR, 'pdv.json')
OVERRIDES_FILE = os.path.join(BASE_DIR, 'overrides.json')

with open(VENDORS_FILE, encoding='utf-8') as f:
    VENDORS = json.load(f)

OVERRIDES = {}
if os.path.exists(OVERRIDES_FILE):
    try:
        with open(OVERRIDES_FILE, encoding='utf-8') as f:
            OVERRIDES = json.load(f)
    except Exception:
        OVERRIDES = {}

for v in VENDORS:
    o = OVERRIDES.get(v['code'])
    if o:
        v['name'] = o.get('name', v['name'])
        v['color'] = o.get('color', v['color'])

PDV = []
if os.path.exists(PDV_FILE):
    with open(PDV_FILE, encoding='utf-8') as f:
        PDV = json.load(f)

VENDOR_BY_CODE = {v['code'].upper(): v for v in VENDORS}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cambiar-esta-clave-por-una-segura')
DASH_PIN = os.environ.get('DASH_PIN', '1234')

ACTIVE_MS = 120000  # 2 minutos: se considera activo si mandó posición hace menos que esto


def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=5000')
    return db


@app.teardown_appcontext
def close_db(exc=None):
    db = getattr(g, '_db', None)
    if db is not None:
        db.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript('''
        CREATE TABLE IF NOT EXISTS vendors (
            code  TEXT PRIMARY KEY,
            name  TEXT NOT NULL,
            prov  TEXT NOT NULL,
            color TEXT NOT NULL,
            grupo TEXT NOT NULL DEFAULT 'rutas'
        );
        CREATE TABLE IF NOT EXISTS positions (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            code    TEXT NOT NULL,
            name    TEXT NOT NULL,
            lat     REAL NOT NULL,
            lon     REAL NOT NULL,
            session TEXT,
            ts      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_positions_code_ts ON positions(code, ts);
        CREATE TABLE IF NOT EXISTS visitas (
            fecha   TEXT NOT NULL,
            code    TEXT NOT NULL,
            cliente TEXT NOT NULL,
            razon   TEXT NOT NULL DEFAULT '',
            calle   TEXT NOT NULL DEFAULT '',
            vta     TEXT NOT NULL DEFAULT '',
            ts      INTEGER NOT NULL,
            PRIMARY KEY (fecha, code, cliente)
        );
        CREATE INDEX IF NOT EXISTS idx_visitas_fecha_code ON visitas(fecha, code);
    ''')
    cols = [r[1] for r in con.execute('PRAGMA table_info(vendors)')]
    if 'grupo' not in cols:
        con.execute("ALTER TABLE vendors ADD COLUMN grupo TEXT NOT NULL DEFAULT 'rutas'")
    con.execute('DELETE FROM vendors')
    for v in VENDORS:
        con.execute(
            'INSERT INTO vendors(code, name, prov, color, grupo) VALUES (?,?,?,?,?)',
            (v['code'], v['name'], v['prov'], v['color'], v.get('grupo', 'rutas')))
    con.commit()
    con.close()


init_db()


# ---------------- API ----------------

@app.route('/api/track', methods=['POST'])
def track():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    vendor = VENDOR_BY_CODE.get(code)
    if not vendor:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 403
    try:
        lat = float(body.get('lat'))
        lon = float(body.get('lon'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'coordenadas invalidas'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'ok': False, 'error': 'coordenadas fuera de rango'}), 400

    db = get_db()
    ts = int(time.time() * 1000)
    db.execute(
        'INSERT INTO positions(code, name, lat, lon, session, ts) VALUES (?,?,?,?,?,?)',
        (code, vendor['name'], lat, lon, str(body.get('session'))[:64], ts))
    db.commit()
    return jsonify({'ok': True, 'ts': ts})


@app.route('/api/positions')
def positions():
    now = int(time.time() * 1000)
    rows = get_db().execute('''
        SELECT p.code, p.name, p.lat, p.lon, p.ts, v.prov, v.color, v.grupo
        FROM positions p
        JOIN (SELECT code, MAX(ts) AS mts FROM positions GROUP BY code) mx
          ON p.code = mx.code AND p.ts = mx.mts
        JOIN vendors v ON v.code = p.code
    ''').fetchall()
    out = []
    for r in rows:
        out.append({
            'code': r['code'],
            'name': r['name'],
            'prov': r['prov'],
            'color': r['color'],
            'grupo': r['grupo'],
            'lat': r['lat'],
            'lon': r['lon'],
            'ts': r['ts'],
            'active': (now - r['ts']) < ACTIVE_MS,
            'last': now - r['ts'],
        })
    return jsonify(out)


@app.route('/api/history')
def history():
    code = request.args.get('code', '').strip().upper()
    days = max(1, min(int(request.args.get('days', 1)), 14))
    since = int(time.time() * 1000) - days * 86400000
    rows = get_db().execute(
        'SELECT lat, lon, ts FROM positions WHERE code = ? AND ts >= ? ORDER BY ts',
        (code, since)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/vendors')
def vendors_api():
    rows = get_db().execute(
        'SELECT code, name, prov, color, grupo FROM vendors ORDER BY prov, name').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/config/vendor', methods=['POST'])
def config_vendor():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    v = VENDOR_BY_CODE.get(code)
    if not v:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404

    name = body.get('name')
    color = body.get('color')
    if name is not None:
        name = str(name).strip()
        if not name or len(name) > 60:
            return jsonify({'ok': False, 'error': 'nombre invalido'}), 400
        v['name'] = name
    if color is not None:
        color = str(color).strip()
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            return jsonify({'ok': False, 'error': 'color invalido'}), 400
        v['color'] = color

    OVERRIDES[code] = {'name': v['name'], 'color': v['color']}
    try:
        with open(OVERRIDES_FILE, 'w', encoding='utf-8') as f:
            json.dump(OVERRIDES, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    db = get_db()
    db.execute('UPDATE vendors SET name=?, color=? WHERE code=?', (v['name'], v['color'], code))
    db.commit()
    return jsonify({'ok': True, 'vendor': {'code': code, 'name': v['name'], 'color': v['color']}})


@app.route('/api/pdv')
def pdv_api():
    prov = request.args.get('prov', '').strip().upper()
    q = request.args.get('q', '').strip().lower()
    vta = request.args.get('vta', '').strip().lower()
    items = PDV
    if prov:
        items = [p for p in items if p['prov'] == prov]
    if vta:
        items = [p for p in items if vta in (p.get('vta') or '').lower()]
    if q:
        items = [p for p in items
                 if q in (p.get('r') or '').lower()
                 or q in (p.get('c') or '').lower()
                 or q in (p.get('calle') or '').lower()
                 or q in (p.get('vta') or '').lower()]
        items = items[:50]
    return jsonify(items)


@app.route('/api/visitas', methods=['GET'])
def visitas_get():
    code = request.args.get('code', '').strip().upper()
    fecha = request.args.get('fecha', '').strip()
    sql = 'SELECT fecha, code, cliente, razon, calle, vta, ts FROM visitas'
    conds, params = [], []
    if code:
        conds.append('code = ?')
        params.append(code)
    if fecha:
        conds.append('fecha = ?')
        params.append(fecha)
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    sql += ' ORDER BY ts'
    rows = get_db().execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/visitas', methods=['POST'])
def visitas_post():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    v = VENDOR_BY_CODE.get(code)
    if not v:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 403
    cliente = str(body.get('cliente', '')).strip()
    if not cliente:
        return jsonify({'ok': False, 'error': 'falta cliente'}), 400
    fecha = str(body.get('fecha', '')).strip() or time.strftime('%Y-%m-%d')
    if len(fecha) != 10:
        return jsonify({'ok': False, 'error': 'fecha invalida'}), 400
    pdv = next((p for p in PDV if p.get('c') == cliente), None)
    if not pdv:
        return jsonify({'ok': False, 'error': 'cliente no encontrado en PDV'}), 404
    db = get_db()
    ts = int(time.time() * 1000)
    db.execute(
        'INSERT OR REPLACE INTO visitas(fecha, code, cliente, razon, calle, vta, ts) VALUES (?,?,?,?,?,?,?)',
        (fecha, code, cliente, pdv.get('r', ''), pdv.get('calle', ''), pdv.get('vta', ''), ts))
    db.commit()
    return jsonify({'ok': True, 'ts': ts})


@app.route('/api/visitas', methods=['DELETE'])
def visitas_delete():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    cliente = str(body.get('cliente', '')).strip()
    fecha = str(body.get('fecha', '')).strip() or time.strftime('%Y-%m-%d')
    db = get_db()
    db.execute('DELETE FROM visitas WHERE code = ? AND cliente = ? AND fecha = ?',
               (code, cliente, fecha))
    db.commit()
    return jsonify({'ok': True})


# ---------------- Páginas ----------------

@app.route('/tracker/<code>')
def tracker(code):
    vendor = VENDOR_BY_CODE.get(code.strip().upper())
    if not vendor:
        return '<h3>Código inválido</h3><p>Revisá el enlace con tu coordinador.</p>', 404
    return render_template('tracker.html', vendor=vendor)


@app.route('/', methods=['GET', 'POST'])
def dashboard():
    if request.method == 'POST':
        if request.form.get('pin') == DASH_PIN:
            session['auth'] = True
        return redirect('/')
    if not session.get('auth'):
        return render_template('dashboard.html', locked=True, wrong=request.args.get('w') == '1')
    vendors = get_db().execute(
        'SELECT code, name, prov, color, grupo FROM vendors ORDER BY prov, name').fetchall()
    return render_template('dashboard.html', locked=False, vendors=[dict(v) for v in vendors])


@app.route('/logout')
def logout():
    session.pop('auth', None)
    return redirect('/')


@app.route('/manifest.json')
def manifest():
    code = request.args.get('code', '').strip().upper()
    if code in VENDOR_BY_CODE:
        start_url = '/tracker/' + code
    else:
        start_url = '/tracker/'
    return jsonify({
        'name': 'GPS Merchan',
        'short_name': 'GPS Merchan',
        'start_url': start_url,
        'scope': '/',
        'display': 'standalone',
        'background_color': '#0f1420',
        'theme_color': '#0f1420',
        'icons': [
            {'src': '/static/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
            {'src': '/static/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
            {'src': '/static/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    })


@app.route('/sw.js')
def sw():
    with open(os.path.join(BASE_DIR, 'sw.js'), encoding='utf-8') as f:
        return (f.read(), 200, {'Content-Type': 'application/javascript; charset=utf-8', 'Service-Worker-Allowed': '/'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=True)
