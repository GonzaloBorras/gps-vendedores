# -*- coding: utf-8 -*-
import base64
import json
import math
import os
import re
import sqlite3
import time
from datetime import date, datetime, timezone, timedelta

from flask import Flask, g, jsonify, redirect, render_template, request, Response, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'gps.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
PG = DATABASE_URL.startswith('postgres')
VENDORS_FILE = os.path.join(BASE_DIR, 'vendors.json')
PDV_FILE = os.path.join(BASE_DIR, 'pdv.json')
RUTAS_FILE = os.path.join(BASE_DIR, 'rutas.json')
OVERRIDES_FILE = os.path.join(BASE_DIR, 'overrides.json')

APP_VERSION = '2.0'
APP_VERSION_CODE = 2
APK_URL = 'https://github.com/GonzaloBorras/gps-vendedores/releases/download/apk-v1.0/GPS-Merchan.apk'

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

PDV_BY_CODE = {p['c']: p for p in PDV}

RUTAS = {}
if os.path.exists(RUTAS_FILE):
    with open(RUTAS_FILE, encoding='utf-8') as f:
        RUTAS = json.load(f)

VENDOR_BY_CODE = {v['code'].upper(): v for v in VENDORS}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cambiar-esta-clave-por-una-segura')
DASH_PIN = os.environ.get('DASH_PIN', '1234')

ACTIVE_MS = 120000  # 2 minutos: se considera activo si mandó posición hace menos que esto
VISIT_RADIUS_M = 150  # radio por defecto para validar que el merchan está en el PDV
START_RADIUS_M = 400  # radio para permitir iniciar en vivo (tolerancia a coords aproximadas)
VISIT_MAX_MS = 5 * 60 * 1000  # la posición para validar la visita no puede tener más de 5 min
ACTIVE_GAP_MS = 5 * 60 * 1000  # para reportes: salto de más de 5 min no cuenta como actividad
SHIFT_DEFAULT_MS = 8 * 60 * 60 * 1000  # jornada por defecto: 8 hs
VAPID_MAILTO = 'mailto:admin@gps-merchan.com'

RUTAS_DIA_NAMES = {
    'LUNES': 'Lunes', 'MARTES': 'Martes', 'MIERCOLES': 'Miércoles',
    'JUEVES': 'Jueves', 'VIERNES': 'Viernes', 'SABADO': 'Sábado', 'DOMINGO': 'Domingo',
}
WEEKDAY_KEYS = ['LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES', 'SABADO', 'DOMINGO']


# ---------------- Conexión a base de datos (SQLite local / PostgreSQL en Render) ----------------

def _q(sql):
    return sql.replace('?', '%s') if PG else sql


def _raw_conn():
    if PG:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def get_db():
    db = getattr(g, '_db', None)
    if db is not None:
        return db
    if PG:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        db = g._db = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
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


def _exec(con, sql, params=None):
    if PG:
        cur = con.cursor()
        cur.execute(_q(sql), params or ())
        return cur
    return con.execute(_q(sql), params or ())


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _today_str():
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime('%Y-%m-%d')


def _today_key():
    wd = (datetime.now(timezone.utc) - timedelta(hours=3)).weekday()
    return WEEKDAY_KEYS[wd]


def _day_range(fecha):
    start = datetime.strptime(fecha, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(hours=3)
    start_ms = int(start.timestamp() * 1000)
    return start_ms, start_ms + 86400000


# ---------------- Esquema ----------------

def init_db():
    con = _raw_conn()
    if PG:
        cur = con.cursor()
        for s in [
            'CREATE TABLE IF NOT EXISTS vendors (code TEXT PRIMARY KEY, name TEXT NOT NULL, prov TEXT NOT NULL, color TEXT NOT NULL, grupo TEXT NOT NULL DEFAULT \'rutas\')',
            'CREATE TABLE IF NOT EXISTS positions (id SERIAL PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, session TEXT, ts BIGINT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS idx_positions_code_ts ON positions(code, ts)',
            'CREATE TABLE IF NOT EXISTS visitas (fecha TEXT NOT NULL, code TEXT NOT NULL, cliente TEXT NOT NULL, razon TEXT NOT NULL DEFAULT \'\', calle TEXT NOT NULL DEFAULT \'\', vta TEXT NOT NULL DEFAULT \'\', ts BIGINT NOT NULL, foto TEXT, foto_ts BIGINT, PRIMARY KEY (fecha, code, cliente))',
            'CREATE INDEX IF NOT EXISTS idx_visitas_fecha_code ON visitas(fecha, code)',
            'CREATE TABLE IF NOT EXISTS alerts (code TEXT PRIMARY KEY, tipo TEXT NOT NULL DEFAULT \'gps_off\', ts BIGINT NOT NULL, msj TEXT NOT NULL DEFAULT \'\')',
            'CREATE TABLE IF NOT EXISTS shifts (code TEXT PRIMARY KEY, shift_ms BIGINT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS absence (fecha TEXT NOT NULL, code TEXT NOT NULL, motivo TEXT NOT NULL DEFAULT \'\', ts BIGINT NOT NULL, PRIMARY KEY (fecha, code))',
            'CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, code TEXT NOT NULL, msj TEXT NOT NULL, ts BIGINT NOT NULL, visto INTEGER NOT NULL DEFAULT 0)',
            'CREATE INDEX IF NOT EXISTS idx_messages_code ON messages(code)',
            'CREATE TABLE IF NOT EXISTS pdv_radius (cliente TEXT PRIMARY KEY, radius_m INTEGER NOT NULL)',
            'CREATE TABLE IF NOT EXISTS pdvs_extra (cliente TEXT PRIMARY KEY, razon TEXT NOT NULL, calle TEXT NOT NULL DEFAULT \'\', altura TEXT NOT NULL DEFAULT \'\', vta TEXT NOT NULL DEFAULT \'\', prov TEXT NOT NULL DEFAULT \'\', lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL, creado_por TEXT NOT NULL DEFAULT \'\', ts BIGINT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS geoevents (id SERIAL PRIMARY KEY, code TEXT NOT NULL, cliente TEXT NOT NULL DEFAULT \'\', razon TEXT NOT NULL DEFAULT \'\', tipo TEXT NOT NULL DEFAULT \'\', dist_m INTEGER, ts BIGINT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS idx_geoevents_code_ts ON geoevents(code, ts)',
            'CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)',
            'CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, code TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL, ts BIGINT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS idx_push_subs_code ON push_subs(code)',
        ]:
            cur.execute(s)
        cur.execute('ALTER TABLE visitas ADD COLUMN IF NOT EXISTS foto TEXT')
        cur.execute('ALTER TABLE visitas ADD COLUMN IF NOT EXISTS foto_ts BIGINT')
        con.commit()
    else:
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
            CREATE TABLE IF NOT EXISTS alerts (
                code TEXT PRIMARY KEY,
                tipo TEXT NOT NULL DEFAULT 'gps_off',
                ts INTEGER NOT NULL,
                msj TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS shifts (
                code TEXT PRIMARY KEY,
                shift_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS absence (
                fecha TEXT NOT NULL,
                code TEXT NOT NULL,
                motivo TEXT NOT NULL DEFAULT '',
                ts INTEGER NOT NULL,
                PRIMARY KEY (fecha, code)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                msj TEXT NOT NULL,
                ts INTEGER NOT NULL,
                visto INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_code ON messages(code);
            CREATE TABLE IF NOT EXISTS pdv_radius (
                cliente TEXT PRIMARY KEY,
                radius_m INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pdvs_extra (
                cliente    TEXT PRIMARY KEY,
                razon      TEXT NOT NULL,
                calle      TEXT NOT NULL DEFAULT '',
                altura     TEXT NOT NULL DEFAULT '',
                vta        TEXT NOT NULL DEFAULT '',
                prov       TEXT NOT NULL DEFAULT '',
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                creado_por TEXT NOT NULL DEFAULT '',
                ts         INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS geoevents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                cliente TEXT NOT NULL DEFAULT '',
                razon TEXT NOT NULL DEFAULT '',
                tipo TEXT NOT NULL DEFAULT '',
                dist_m INTEGER,
                ts INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_geoevents_code_ts ON geoevents(code, ts);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS push_subs (
                endpoint TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                ts INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_push_subs_code ON push_subs(code);
        ''')
        vcols = [r[1] for r in con.execute('PRAGMA table_info(visitas)')]
        if 'foto' not in vcols:
            con.execute('ALTER TABLE visitas ADD COLUMN foto TEXT')
        if 'foto_ts' not in vcols:
            con.execute('ALTER TABLE visitas ADD COLUMN foto_ts INTEGER')
        vcols2 = [r[1] for r in con.execute('PRAGMA table_info(vendors)')]
        if 'grupo' not in vcols2:
            con.execute("ALTER TABLE vendors ADD COLUMN grupo TEXT NOT NULL DEFAULT 'rutas'")

    _exec(con, 'DELETE FROM vendors')
    for v in VENDORS:
        _exec(con, 'INSERT INTO vendors(code, name, prov, color, grupo) VALUES (?,?,?,?,?)',
              (v['code'], v['name'], v['prov'], v['color'], v.get('grupo', 'rutas')))
    con.commit()
    con.close()
    _refresh_pdv_radius()


_PDV_RADIUS = {}


def _refresh_pdv_radius():
    global _PDV_RADIUS
    try:
        con = _raw_conn()
        _PDV_RADIUS = {r['cliente']: r['radius_m'] for r in _exec(con, 'SELECT cliente, radius_m FROM pdv_radius')}
        con.close()
    except Exception:
        _PDV_RADIUS = {}


def _radius_for(cliente):
    return _PDV_RADIUS.get(str(cliente), VISIT_RADIUS_M)


init_db()


# ---------------- PDV adicionales (creados por los merchans desde el mapa) ----------------

def _load_pdv_extra():
    extra = []
    try:
        con = _raw_conn()
        rows = _exec(con, 'SELECT cliente, razon, calle, altura, vta, prov, lat, lon FROM pdvs_extra').fetchall()
        con.close()
        for r in rows:
            extra.append({'c': r['cliente'], 'r': r['razon'], 'calle': r['calle'], 'altura': r['altura'],
                          'vta': r['vta'], 'prov': r['prov'], 'lat': r['lat'], 'lon': r['lon']})
    except Exception:
        pass
    return extra


PDV_EXTRA = _load_pdv_extra()
PDV.extend(PDV_EXTRA)
PDV_BY_CODE.update({p['c']: p for p in PDV_EXTRA})


# ---------------- Geocercas ----------------

def check_geofence(db, code, lat, lon, now):
    items = RUTAS.get(code, {}).get(_today_key())
    if not items:
        return
    near = []
    for cliente, _fb in items:
        p = PDV_BY_CODE.get(cliente)
        if not p:
            continue
        pl, po = p.get('lat'), p.get('lon')
        if not pl or not po or (pl == 0 and po == 0):
            continue
        d = _haversine(lat, lon, pl, po)
        if d <= _radius_for(cliente):
            near.append((d, cliente, p.get('r', '')))
    near.sort(key=lambda x: x[0])
    last = _exec(db, 'SELECT tipo, cliente, razon, ts FROM geoevents WHERE code=? ORDER BY ts DESC LIMIT 1',
                 (code,)).fetchone()
    if near:
        d, cliente, razon = near[0]
        if last is None or last['tipo'] != 'enter' or last['cliente'] != cliente or now - last['ts'] > 30000:
            _exec(db, 'INSERT INTO geoevents(code, cliente, razon, tipo, dist_m, ts) VALUES (?,?,?,?,?,?)',
                  (code, cliente, razon, 'enter', int(d), now))
    elif last is not None and last['tipo'] == 'enter' and now - last['ts'] > 10000:
        _exec(db, 'INSERT INTO geoevents(code, cliente, razon, tipo, dist_m, ts) VALUES (?,?,?,?,?,?)',
              (code, last['cliente'], last['razon'], 'leave', None, now))


# ---------------- API ----------------

@app.route('/api/app-version')
def app_version():
    return jsonify({
        'version': APP_VERSION,
        'versionCode': APP_VERSION_CODE,
        'apkUrl': APK_URL,
        'notes': ''
    })


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
    _exec(db, 'INSERT INTO positions(code, name, lat, lon, session, ts) VALUES (?,?,?,?,?,?)',
          (code, vendor['name'], lat, lon, str(body.get('session'))[:64], ts))
    _exec(db, 'DELETE FROM alerts WHERE code = ?', (code,))
    check_geofence(db, code, lat, lon, ts)
    db.commit()
    return jsonify({'ok': True, 'ts': ts})


@app.route('/api/gps-status', methods=['POST'])
def gps_status():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    db = get_db()
    ts = int(time.time() * 1000)
    if body.get('gps'):
        _exec(db, 'DELETE FROM alerts WHERE code = ?', (code,))
    else:
        _exec(db,
              '''INSERT INTO alerts(code, tipo, ts, msj) VALUES (?, 'gps_off', ?, ?)
                 ON CONFLICT(code) DO UPDATE SET ts = excluded.ts, msj = excluded.msj''',
              (code, ts, 'Ubicación apagada o sin señal GPS'))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/start-check', methods=['POST'])
def start_check():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    db = get_db()
    aus = _exec(db, 'SELECT motivo FROM absence WHERE code=? AND fecha=?',
                (code, _today_str())).fetchone()
    if aus:
        return jsonify({'ok': False, 'error': 'Hoy no es tu día de trabajo (%s).' % aus['motivo']}), 403
    try:
        lat = float(body.get('lat'))
        lon = float(body.get('lon'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'coordenadas invalidas'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'ok': False, 'error': 'coordenadas fuera de rango'}), 400
    best = None
    for p in PDV:
        pl, po = p.get('lat'), p.get('lon')
        if pl and po and (pl != 0 or po != 0):
            d = _haversine(lat, lon, pl, po)
            if best is None or d < best[0]:
                best = (d, p)
    if not best:
        return jsonify({'ok': False, 'error': 'sin PDV'}), 404
    d, p = best
    return jsonify({
        'ok': True,
        'dist_m': int(d),
        'dentro': d <= max(START_RADIUS_M, _radius_for(p['c'])),
        'pdv': {'cliente': p['c'], 'razon': p['r']}
    })


@app.route('/api/shift')
def shift_get():
    code = request.args.get('code', '').strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    row = _exec(get_db(), _q('SELECT shift_ms FROM shifts WHERE code=?'), (code,)).fetchone()
    return jsonify({'ok': True, 'code': code, 'shift_ms': row['shift_ms'] if row else SHIFT_DEFAULT_MS})


@app.route('/api/config/shift', methods=['POST'])
def config_shift():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    try:
        hours = float(body.get('shift_hours'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'horas invalidas'}), 400
    if not (1 <= hours <= 24):
        return jsonify({'ok': False, 'error': 'la jornada debe ser entre 1 y 24 horas'}), 400
    shift_ms = int(hours * 3600 * 1000)
    db = get_db()
    _exec(db, '''INSERT INTO shifts(code, shift_ms) VALUES (?,?)
                 ON CONFLICT(code) DO UPDATE SET shift_ms = excluded.shift_ms''', (code, shift_ms))
    db.commit()
    return jsonify({'ok': True, 'shift_ms': shift_ms})


@app.route('/api/absence')
def absence_get():
    code = request.args.get('code', '').strip().upper()
    fecha = request.args.get('fecha', '').strip()
    sql = 'SELECT fecha, code, motivo, ts FROM absence'
    conds, params = [], []
    if code:
        conds.append('code = ?')
        params.append(code)
    if fecha:
        conds.append('fecha = ?')
        params.append(fecha)
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    sql += ' ORDER BY fecha DESC, ts DESC LIMIT 100'
    rows = _exec(get_db(), sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/absence', methods=['POST'])
def absence_post():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    fecha = str(body.get('fecha', '')).strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
        return jsonify({'ok': False, 'error': 'fecha invalida'}), 400
    motivo = str(body.get('motivo', '')).strip()[:60] or 'Día libre'
    db = get_db()
    _exec(db, '''INSERT INTO absence(fecha, code, motivo, ts) VALUES (?,?,?,?)
                 ON CONFLICT(fecha, code) DO UPDATE SET motivo = excluded.motivo''',
          (fecha, code, motivo, int(time.time() * 1000)))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/absence', methods=['DELETE'])
def absence_delete():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    fecha = str(body.get('fecha', '')).strip()
    db = get_db()
    _exec(db, 'DELETE FROM absence WHERE code=? AND fecha=?', (code, fecha))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/positions')
def positions():
    db = get_db()
    now = int(time.time() * 1000)
    rows = _exec(db, '''
        SELECT p.code, p.name, p.lat, p.lon, p.ts, v.prov, v.color, v.grupo
        FROM positions p
        JOIN (SELECT code, MAX(ts) AS mts FROM positions GROUP BY code) mx
          ON p.code = mx.code AND p.ts = mx.mts
        JOIN vendors v ON v.code = p.code
    ''').fetchall()
    alerts = {a['code']: a['ts'] for a in _exec(db, 'SELECT code, ts FROM alerts').fetchall()}
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
            'gpsAlert': r['code'] in alerts,
            'gpsAlertTs': alerts.get(r['code']),
        })
    for code, a_ts in alerts.items():
        if code in {r['code'] for r in rows}:
            continue
        v = VENDOR_BY_CODE.get(code)
        if not v:
            continue
        out.append({
            'code': code,
            'name': v.get('name', code),
            'prov': v.get('prov', ''),
            'color': v.get('color', '#666666'),
            'grupo': v.get('grupo', 'rutas'),
            'lat': None,
            'lon': None,
            'ts': None,
            'active': False,
            'last': None,
            'gpsAlert': True,
            'gpsAlertTs': a_ts,
        })
    return jsonify(out)


@app.route('/api/history')
def history():
    code = request.args.get('code', '').strip().upper()
    fecha = request.args.get('fecha', '').strip()
    days = max(1, min(int(request.args.get('days', 1)), 14))
    if fecha:
        s, e = _day_range(fecha)
        rows = _exec(get_db(), _q(
            'SELECT lat, lon, ts FROM positions WHERE code = ? AND ts >= ? AND ts < ? ORDER BY ts'),
            (code, s, e)).fetchall()
    else:
        since = int(time.time() * 1000) - days * 86400000
        rows = _exec(get_db(), _q(
            'SELECT lat, lon, ts FROM positions WHERE code = ? AND ts >= ? ORDER BY ts'),
            (code, since)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/vendors')
def vendors_api():
    rows = _exec(get_db(),
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
    _exec(db, 'UPDATE vendors SET name=?, color=? WHERE code=?', (v['name'], v['color'], code))
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
    out = []
    for p in items:
        out.append(dict(p, radius=_radius_for(p.get('c', ''))))
    return jsonify(out)


@app.route('/api/pdv', methods=['POST'])
def pdv_post():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    v = VENDOR_BY_CODE.get(code)
    if not v:
        return jsonify({'ok': False, 'error': 'codigo de merchan invalido'}), 403
    try:
        lat = float(body.get('lat'))
        lon = float(body.get('lon'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'ubicacion invalida'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return jsonify({'ok': False, 'error': 'ubicacion fuera de rango'}), 400
    razon = str(body.get('razon', '')).strip()
    if not razon:
        return jsonify({'ok': False, 'error': 'falta la razon social'}), 400
    calle = str(body.get('calle', '')).strip()
    altura = str(body.get('altura', '')).strip()
    vta = str(body.get('vta', '')).strip()
    cliente = 'M' + str(int(time.time() * 1000))
    db = get_db()
    _exec(db, 'INSERT INTO pdvs_extra(cliente, razon, calle, altura, vta, prov, lat, lon, creado_por, ts) VALUES (?,?,?,?,?,?,?,?,?,?)',
          (cliente, razon, calle, altura, vta, v.get('prov', ''), lat, lon, code, int(time.time() * 1000)))
    db.commit()
    nuevo = {'c': cliente, 'r': razon, 'calle': calle, 'altura': altura, 'vta': vta,
             'prov': v.get('prov', ''), 'lat': lat, 'lon': lon}
    PDV.append(nuevo)
    PDV_BY_CODE[cliente] = nuevo
    PDV_EXTRA.append(nuevo)
    return jsonify({'ok': True, 'cliente': cliente, 'razon': razon, 'lat': lat, 'lon': lon})


@app.route('/api/pdv/radius', methods=['POST'])
def pdv_radius_post():
    body = request.get_json(force=True, silent=True) or {}
    cliente = str(body.get('cliente', '')).strip()
    p = PDV_BY_CODE.get(cliente)
    if not p:
        return jsonify({'ok': False, 'error': 'PDV no encontrado'}), 404
    try:
        radius = int(body.get('radius_m'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'radio invalido'}), 400
    if not (20 <= radius <= 2000):
        return jsonify({'ok': False, 'error': 'el radio debe estar entre 20 y 2000 m'}), 400
    db = get_db()
    _exec(db, '''INSERT INTO pdv_radius(cliente, radius_m) VALUES (?,?)
                 ON CONFLICT(cliente) DO UPDATE SET radius_m = excluded.radius_m''', (cliente, radius))
    db.commit()
    _PDV_RADIUS[cliente] = radius
    return jsonify({'ok': True, 'radius_m': radius})


@app.route('/api/rutas')
def rutas_api():
    merchan = request.args.get('merchan', '').strip().upper()
    dia = request.args.get('dia', '').strip().upper()
    out = []
    for m, dias in RUTAS.items():
        if merchan and m != merchan:
            continue
        for d, items in dias.items():
            if dia and d != dia:
                continue
            for i, (c, razon_fb) in enumerate(items, 1):
                p = PDV_BY_CODE.get(c) or {}
                out.append({
                    'merchan': m,
                    'dia': d,
                    'orden': i,
                    'cliente': c,
                    'razon': p.get('r') or razon_fb,
                    'calle': p.get('calle', ''),
                    'altura': p.get('altura', ''),
                    'vta': p.get('vta', ''),
                    'lat': p.get('lat'),
                    'lon': p.get('lon'),
                    'radius': _radius_for(c),
                })
    return jsonify(out)


@app.route('/api/nearest-pdv')
def nearest_pdv():
    code = request.args.get('code', '').strip().upper()
    now = int(time.time() * 1000)
    row = _exec(get_db(), _q(
        'SELECT lat, lon, ts FROM positions WHERE code = ? AND ts >= ? ORDER BY ts DESC LIMIT 1'),
        (code, now - ACTIVE_MS)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'sin posicion reciente (el vendedor no inició el envío en vivo)'}), 404
    lat, lon = row['lat'], row['lon']
    best = None
    for p in PDV:
        pl, po = p.get('lat'), p.get('lon')
        if pl and po and (pl != 0 or po != 0):
            d = _haversine(lat, lon, pl, po)
            if best is None or d < best[0]:
                best = (d, p)
    if not best:
        return jsonify({'ok': False, 'error': 'sin pdv'}), 404
    d, p = best
    return jsonify({
        'ok': True,
        'dist_m': int(d),
        'cliente': p['c'],
        'razon': p['r'],
        'calle': p.get('calle', ''),
        'altura': p.get('altura', ''),
        'vta': p.get('vta', ''),
        'lat': p['lat'],
        'lon': p['lon'],
        'radius': _radius_for(p['c']),
        'ts': row['ts'],
    })


@app.route('/api/visitas', methods=['GET'])
def visitas_get():
    code = request.args.get('code', '').strip().upper()
    fecha = request.args.get('fecha', '').strip()
    sql = 'SELECT fecha, code, cliente, razon, calle, vta, ts, foto_ts FROM visitas'
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
    rows = _exec(get_db(), sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['has_foto'] = 1 if (d.get('foto_ts') and d['foto_ts'] > 0) else 0
        d.pop('foto_ts', None)
        out.append(d)
    return jsonify(out)


@app.route('/api/visitas/foto')
def visitas_foto():
    code = request.args.get('code', '').strip().upper()
    cliente = request.args.get('cliente', '').strip()
    fecha = request.args.get('fecha', '').strip() or _today_str()
    row = _exec(get_db(), _q(
        'SELECT foto, foto_ts FROM visitas WHERE code=? AND cliente=? AND fecha=?'),
        (code, cliente, fecha)).fetchone()
    if not row or not row['foto']:
        return jsonify({'ok': False, 'error': 'sin foto'}), 404
    return jsonify({'ok': True, 'foto': row['foto'], 'foto_ts': row['foto_ts']})


@app.route('/api/visitas/resumen')
def visitas_resumen():
    fecha = request.args.get('fecha', '').strip() or date.today().isoformat()
    rows = _exec(get_db(),
                 'SELECT code, cliente FROM visitas WHERE fecha = ? ORDER BY ts', (fecha,)).fetchall()
    out = {}
    for r in rows:
        item = out.setdefault(r['code'], {'n': 0, 'clientes': []})
        item['n'] += 1
        item['clientes'].append(r['cliente'])
    return jsonify(out)


@app.route('/api/merchan-pdv')
def merchan_pdv():
    now = int(time.time() * 1000)
    db = get_db()
    rows = _exec(db, '''
        SELECT p.code, p.lat, p.lon FROM positions p
        JOIN (SELECT code, MAX(ts) AS mts FROM positions GROUP BY code) mx
          ON p.code = mx.code AND p.ts = mx.mts
        WHERE p.ts >= ?
    ''', (now - ACTIVE_MS,)).fetchall()
    dia = _today_key()
    fecha = _today_str()
    out = {}
    for r in rows:
        lat, lon = r['lat'], r['lon']
        best = None
        for p in PDV:
            pl, po = p.get('lat'), p.get('lon')
            if pl and po and (pl != 0 or po != 0):
                d = _haversine(lat, lon, pl, po)
                if best is None or d < best[0]:
                    best = (d, p)
        if best:
            d, p = best
            route_set = {c for c, _ in RUTAS.get(r['code'], {}).get(dia, [])}
            vis = set(row['cliente'] for row in _exec(
                db, 'SELECT cliente FROM visitas WHERE code=? AND fecha=?', (r['code'], fecha)).fetchall())
            cliente = p['c']
            out[r['code']] = {
                'cliente': cliente,
                'razon': p['r'],
                'calle': p.get('calle', ''),
                'altura': p.get('altura', ''),
                'dist_m': int(d),
                'radius': _radius_for(cliente),
                'dentro': d <= _radius_for(cliente),
                'pendiente': cliente in route_set and cliente not in vis,
                'lat': p['lat'],
                'lon': p['lon'],
            }
    return jsonify(out)


@app.route('/api/geoevents')
def geoevents():
    code = request.args.get('code', '').strip().upper()
    limit = max(1, min(int(request.args.get('limit', 20)), 200))
    db = get_db()
    if code:
        rows = _exec(db, 'SELECT code, cliente, razon, tipo, dist_m, ts FROM geoevents WHERE code=? ORDER BY ts DESC LIMIT ?',
                     (code, limit)).fetchall()
    else:
        rows = _exec(db, 'SELECT code, cliente, razon, tipo, dist_m, ts FROM geoevents ORDER BY ts DESC LIMIT ?',
                     (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/visitas', methods=['POST'])
def visitas_post():
    foto = None
    foto_ts = None
    if request.content_type and 'multipart' in request.content_type:
        body = {k: request.form.get(k, '') for k in request.form}
        f = request.files.get('foto')
        if f and f.filename:
            foto = base64.b64encode(f.read()).decode('ascii')
            foto_ts = int(time.time() * 1000)
    else:
        body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    v = VENDOR_BY_CODE.get(code)
    if not v:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 403
    cliente = str(body.get('cliente', '')).strip()
    if not cliente:
        return jsonify({'ok': False, 'error': 'falta cliente'}), 400
    fecha = str(body.get('fecha', '')).strip() or _today_str()
    if len(fecha) != 10:
        return jsonify({'ok': False, 'error': 'fecha invalida'}), 400
    pdv = next((p for p in PDV if p.get('c') == cliente), None)
    razon = pdv.get('r', '') if pdv else str(body.get('razon', '')).strip()
    calle = pdv.get('calle', '') if pdv else str(body.get('calle', '')).strip()
    vta = pdv.get('vta', '') if pdv else str(body.get('vta', '')).strip()
    if not pdv and not razon:
        return jsonify({'ok': False, 'error': 'cliente no encontrado en PDV'}), 404
    db = get_db()
    if body.get('validate'):
        row = _exec(db, 'SELECT lat, lon, ts FROM positions WHERE code = ? ORDER BY ts DESC LIMIT 1',
                    (code,)).fetchone()
        now = int(time.time() * 1000)
        if not row or now - row['ts'] > VISIT_MAX_MS:
            return jsonify({'ok': False, 'error': 'No hay una posición reciente para validar. Iniciá el envío en vivo y acercate al PDV.'}), 400
        if pdv and pdv.get('lat') and pdv.get('lon') and (pdv['lat'] != 0 or pdv['lon'] != 0):
            try:
                acc = float(body.get('accuracy') or 0)
            except (TypeError, ValueError):
                acc = 0
            limit = _radius_for(cliente) + max(0, acc - 50)  # tolerancia al ruido del GPS
            d = _haversine(row['lat'], row['lon'], pdv['lat'], pdv['lon'])
            if d > limit:
                return jsonify({'ok': False, 'error': 'No estás en el PDV (%s). Estás a %d m de ese PDV.' % (pdv.get('r', ''), int(d))}), 400
    ts = int(time.time() * 1000)
    if PG:
        _exec(db, '''INSERT INTO visitas(fecha, code, cliente, razon, calle, vta, ts, foto, foto_ts)
                     VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(fecha, code, cliente) DO UPDATE SET
                       razon=excluded.razon, calle=excluded.calle, vta=excluded.vta,
                       ts=excluded.ts, foto=excluded.foto, foto_ts=excluded.foto_ts''',
              (fecha, code, cliente, razon, calle, vta, ts, foto, foto_ts))
    else:
        _exec(db, 'INSERT OR REPLACE INTO visitas(fecha, code, cliente, razon, calle, vta, ts, foto, foto_ts) VALUES (?,?,?,?,?,?,?,?,?)',
              (fecha, code, cliente, razon, calle, vta, ts, foto, foto_ts))
    db.commit()
    return jsonify({'ok': True, 'ts': ts, 'has_foto': 1 if foto else 0})


@app.route('/api/visitas', methods=['DELETE'])
def visitas_delete():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    cliente = str(body.get('cliente', '')).strip()
    fecha = str(body.get('fecha', '')).strip() or _today_str()
    db = get_db()
    _exec(db, 'DELETE FROM visitas WHERE code = ? AND cliente = ? AND fecha = ?',
          (code, cliente, fecha))
    db.commit()
    return jsonify({'ok': True})


# ---------------- Mensajes panel -> merchan ----------------

@app.route('/api/messages')
def messages_get():
    code = request.args.get('code', '').strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    rows = _exec(get_db(),
                 'SELECT id, code, msj, ts, visto FROM messages WHERE code=? AND visto=0 ORDER BY ts DESC LIMIT 20',
                 (code,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/messages/visto', methods=['POST'])
def messages_visto():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    db = get_db()
    _exec(db, 'UPDATE messages SET visto=1 WHERE code=? AND visto=0', (code,))
    db.commit()
    return jsonify({'ok': True})


def _store_message(db, code, msj, now):
    _exec(db, 'INSERT INTO messages(code, msj, ts, visto) VALUES (?,?,?,0)', (code, msj, now))


def _send_push_to(code, msj):
    try:
        from pywebpush import webpush
    except Exception:
        return
    keys = _vapid_keys()
    db = get_db()
    subs = _exec(db, 'SELECT endpoint, p256dh, auth FROM push_subs WHERE code=?', (code,)).fetchall()
    for s in subs:
        try:
            webpush(
                subscription_info={'endpoint': s['endpoint'],
                                   'keys': {'p256dh': s['p256dh'], 'auth': s['auth']}},
                data=json.dumps({'title': 'GPS Merchan', 'body': msj}),
                vapid_private_key=keys['priv'],
                vapid_claims={'sub': VAPID_MAILTO},
            )
        except Exception:
            pass


@app.route('/api/comunicar', methods=['POST'])
def comunicar():
    body = request.get_json(force=True, silent=True) or {}
    msj = str(body.get('msj', '')).strip()
    if not msj:
        return jsonify({'ok': False, 'error': 'escribí un mensaje'}), 400
    if len(msj) > 500:
        return jsonify({'ok': False, 'error': 'el mensaje no puede superar 500 caracteres'}), 400
    target = str(body.get('code', '')).strip().upper()
    if target == 'ALL':
        codes = [v['code'] for v in VENDORS if v.get('grupo') == 'merchan']
    elif target in VENDOR_BY_CODE:
        codes = [target]
    else:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    db = get_db()
    now = int(time.time() * 1000)
    for c in codes:
        _store_message(db, c, msj, now)
    db.commit()
    for c in codes:
        _send_push_to(c, msj)
    return jsonify({'ok': True, 'n': len(codes)})


# ---------------- Push (VAPID) ----------------

def _vapid_keys():
    db = get_db()
    row = _exec(db, 'SELECT value FROM settings WHERE key=?', ('vapid_priv',)).fetchone()
    if row:
        pub = _exec(db, 'SELECT value FROM settings WHERE key=?', ('vapid_pub',)).fetchone()
        return {'priv': row['value'], 'pub': pub['value'] if pub else ''}
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    v = Vapid()
    v.generate_keys()
    priv_pem = v.private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pub_raw = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64 = base64.urlsafe_b64encode(pub_raw).decode().rstrip('=')
    _exec(db, '''INSERT INTO settings(key, value) VALUES ('vapid_priv', ?)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value''', (priv_pem,))
    _exec(db, '''INSERT INTO settings(key, value) VALUES ('vapid_pub', ?)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value''', (pub_b64,))
    db.commit()
    return {'priv': priv_pem, 'pub': pub_b64}


@app.route('/api/push/vapid')
def push_vapid():
    return jsonify({'publicKey': _vapid_keys()['pub']})


@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get('code', '')).strip().upper()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    sub = body.get('subscription') or {}
    endpoint = str(sub.get('endpoint', '')).strip()
    keys = sub.get('keys') or {}
    p256dh = str(keys.get('p256dh', '')).strip()
    auth = str(keys.get('auth', '')).strip()
    if not endpoint or not p256dh or not auth:
        return jsonify({'ok': False, 'error': 'suscripción invalida'}), 400
    db = get_db()
    _exec(db, '''INSERT INTO push_subs(endpoint, code, p256dh, auth, ts) VALUES (?,?,?,?,?)
                 ON CONFLICT(endpoint) DO UPDATE SET code = excluded.code, p256dh = excluded.p256dh,
                   auth = excluded.auth, ts = excluded.ts''',
          (endpoint, code, p256dh, auth, int(time.time() * 1000)))
    db.commit()
    return jsonify({'ok': True})


# ---------------- Reportes y export ----------------

def _report_code(db, code, desde, hasta):
    d0 = datetime.strptime(desde, '%Y-%m-%d').date()
    d1 = datetime.strptime(hasta, '%Y-%m-%d').date()
    days = []
    tot_sched = tot_vis_ruta = tot_vis_all = 0
    tot_km = tot_h = 0.0
    tot_inicio = tot_fin = None
    dia = d0
    while dia <= d1:
        fecha = dia.isoformat()
        dia_key = WEEKDAY_KEYS[dia.weekday()]
        sched = RUTAS.get(code, {}).get(dia_key, [])
        sched_set = {c for c, _ in sched}
        vis_rows = _exec(db, 'SELECT cliente FROM visitas WHERE code=? AND fecha=?',
                         (code, fecha)).fetchall()
        vis_set = {r['cliente'] for r in vis_rows}
        vis_ruta = len(sched_set & vis_set)
        vis_all = len(vis_set)
        s, e = _day_range(fecha)
        pos = _exec(db, 'SELECT lat, lon, ts FROM positions WHERE code=? AND ts>=? AND ts<? ORDER BY ts',
                    (code, s, e)).fetchall()
        km = 0.0
        act_ms = 0
        prev = None
        for r in pos:
            if prev is not None:
                gap = r['ts'] - prev['ts']
                if 0 <= gap <= ACTIVE_GAP_MS:
                    km += _haversine(prev['lat'], prev['lon'], r['lat'], r['lon'])
                    act_ms += gap
            prev = r
        if pos:
            if tot_inicio is None or pos[0]['ts'] < tot_inicio:
                tot_inicio = pos[0]['ts']
            if tot_fin is None or pos[-1]['ts'] > tot_fin:
                tot_fin = pos[-1]['ts']
        pct = round(vis_ruta * 100.0 / len(sched)) if sched else (100 if vis_all else 0)
        days.append({
            'fecha': fecha,
            'dia': RUTAS_DIA_NAMES[dia_key],
            'scheduled': len(sched),
            'visitados_ruta': vis_ruta,
            'visitas_totales': vis_all,
            'pct': pct,
            'km': round(km / 1000, 1),
            'horas': round(act_ms / 3600000.0, 2),
            'inicio': pos[0]['ts'] if pos else None,
            'fin': pos[-1]['ts'] if pos else None,
        })
        tot_sched += len(sched)
        tot_vis_ruta += vis_ruta
        tot_vis_all += vis_all
        tot_km += km
        tot_h += act_ms
        dia += timedelta(days=1)
    return {
        'code': code,
        'nombre': VENDOR_BY_CODE.get(code, {}).get('name', code),
        'desde': desde,
        'hasta': hasta,
        'days': days,
        'totals': {
            'scheduled': tot_sched,
            'visitados_ruta': tot_vis_ruta,
            'visitas_totales': tot_vis_all,
            'pct': round(tot_vis_ruta * 100.0 / tot_sched) if tot_sched else 0,
            'km': round(tot_km / 1000, 1),
            'horas': round(tot_h / 3600000.0, 2),
            'inicio': tot_inicio,
            'fin': tot_fin,
        },
    }


def _estadias_dia(db, code, fecha):
    dia_key = WEEKDAY_KEYS[datetime.strptime(fecha, '%Y-%m-%d').date().weekday()]
    s, e = _day_range(fecha)
    pos = _exec(db, 'SELECT lat, lon, ts FROM positions WHERE code=? AND ts>=? AND ts<? ORDER BY ts',
                (code, s, e)).fetchall()
    if not pos:
        return []
    candidatos = {}
    for c, _ in RUTAS.get(code, {}).get(dia_key, []):
        p = PDV_BY_CODE.get(c)
        if p and p.get('lat') and p.get('lon'):
            candidatos[c] = p
    for r in _exec(db, 'SELECT DISTINCT cliente FROM visitas WHERE code=? AND fecha=?', (code, fecha)).fetchall():
        p = PDV_BY_CODE.get(r['cliente'])
        if p and p.get('lat') and p.get('lon'):
            candidatos[r['cliente']] = p
    if not candidatos:
        return []
    MERGE_GAP_MS = 3 * 60 * 1000
    MIN_SEG_MS = 60 * 1000
    out = []
    for c, p in candidatos.items():
        rad = _radius_for(c)
        seg_inicio = None
        seg_fin = None
        segs = []
        for r in pos:
            d = _haversine(r['lat'], r['lon'], p['lat'], p['lon'])
            if d <= rad:
                if seg_inicio is None:
                    seg_inicio = r['ts']
                seg_fin = r['ts']
            elif seg_inicio is not None and r['ts'] - seg_fin > MERGE_GAP_MS:
                if seg_fin - seg_inicio >= MIN_SEG_MS:
                    segs.append((seg_inicio, seg_fin))
                seg_inicio = None
                seg_fin = None
        if seg_inicio is not None and seg_fin - seg_inicio >= MIN_SEG_MS:
            segs.append((seg_inicio, seg_fin))
        if not segs:
            continue
        vis = _exec(db, 'SELECT COUNT(*) AS n FROM visitas WHERE code=? AND fecha=? AND cliente=?',
                    (code, fecha, c)).fetchone()
        out.append({
            'cliente': c,
            'razon': p.get('r', ''),
            'calle': p.get('calle', ''),
            'vta': p.get('vta', ''),
            'fecha': fecha,
            'dia': RUTAS_DIA_NAMES[dia_key],
            'minutos': round(sum(b - a for a, b in segs) / 60000.0, 1),
            'entradas': len(segs),
            'inicio': segs[0][0],
            'fin': segs[-1][1],
            'visitas': int(vis['n']) if vis else 0,
        })
    out.sort(key=lambda x: x['inicio'])
    return out


def _estadias_dias(db, code, desde, hasta):
    d0 = datetime.strptime(desde, '%Y-%m-%d').date()
    d1 = datetime.strptime(hasta, '%Y-%m-%d').date()
    fechas = set()
    for r in _exec(db, 'SELECT DISTINCT fecha FROM visitas WHERE code=? AND fecha>=? AND fecha<=?',
                   (code, desde, hasta)):
        fechas.add(r['fecha'])
    dia = d0
    while dia <= d1:
        s, e = _day_range(dia.isoformat())
        has = _exec(db, 'SELECT 1 AS x FROM positions WHERE code=? AND ts>=? AND ts<? LIMIT 1',
                    (code, s, e)).fetchone()
        if has:
            fechas.add(dia.isoformat())
        dia += timedelta(days=1)
    for f in sorted(fechas):
        filas = _estadias_dia(db, code, f)
        if filas:
            yield f, filas


@app.route('/api/estadias')
def estadias():
    code = request.args.get('code', '').strip().upper()
    desde = request.args.get('desde', '').strip()
    hasta = request.args.get('hasta', '').strip()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    if not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', desde or '') and re.fullmatch(r'\d{4}-\d{2}-\d{2}', hasta or '')):
        return jsonify({'ok': False, 'error': 'rango de fechas invalido'}), 400
    db = get_db()
    dias = []
    total = 0.0
    for f, filas in _estadias_dias(db, code, desde, hasta):
        total += sum(x['minutos'] for x in filas)
        dias.append({'fecha': f, 'filas': filas})
    return jsonify({'ok': True, 'code': code, 'nombre': VENDOR_BY_CODE[code].get('name', code),
                    'desde': desde, 'hasta': hasta, 'dias': dias,
                    'totales': {'minutos': round(total, 1), 'horas': round(total / 60.0, 2)}})


@app.route('/api/export/estadias.xlsx')
def export_estadias_xlsx():
    code = request.args.get('code', '').strip().upper()
    desde = request.args.get('desde', '').strip()
    hasta = request.args.get('hasta', '').strip()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    if not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', desde or '') and re.fullmatch(r'\d{4}-\d{2}-\d{2}', hasta or '')):
        return jsonify({'ok': False, 'error': 'rango de fechas invalido'}), 400
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from io import BytesIO
    except ImportError:
        return jsonify({'ok': False, 'error': 'el generador de Excel no está instalado'}), 500
    db = get_db()
    wb = Workbook()
    ws = wb.active
    ws.title = 'Tiempo por PDV'
    headers = ['Merchan', 'Codigo', 'Fecha', 'Dia', 'Cliente', 'Razon social', 'Calle', 'Ruta',
               'Entrada', 'Salida', 'Minutos', 'Veces', 'Visitas registradas']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor='E5E7EB')
        cell.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    total_min = 0.0
    for f, filas in _estadias_dias(db, code, desde, hasta):
        for x in filas:
            total_min += x['minutos']
            ws.append([
                VENDOR_BY_CODE[code].get('name', code), code, f, x['dia'], x['cliente'],
                x['razon'], x['calle'], x['vta'] or '',
                (datetime.fromtimestamp(x['inicio'] / 1000, timezone.utc) - timedelta(hours=3)).strftime('%H:%M:%S'),
                (datetime.fromtimestamp(x['fin'] / 1000, timezone.utc) - timedelta(hours=3)).strftime('%H:%M:%S'),
                x['minutos'], x['entradas'], x['visitas'],
            ])
    if total_min > 0:
        ws.append(['TOTAL', '', '', '', '', '', '', '', '', '', round(total_min, 1), '', ''])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    widths = [18, 8, 11, 10, 10, 34, 24, 12, 10, 10, 9, 8, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', code)
    return Response(buf.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename=estadias_%s_%s_%s.xlsx' % (safe, desde, hasta)})


@app.route('/api/reporte')
def reporte():
    code = request.args.get('code', '').strip().upper()
    desde = request.args.get('desde', '').strip()
    hasta = request.args.get('hasta', '').strip()
    if code not in VENDOR_BY_CODE:
        return jsonify({'ok': False, 'error': 'codigo invalido'}), 404
    if not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', desde or '') and re.fullmatch(r'\d{4}-\d{2}-\d{2}', hasta or '')):
        return jsonify({'ok': False, 'error': 'rango de fechas invalido'}), 400
    data = _report_code(get_db(), code, desde, hasta)
    data['ok'] = True
    return jsonify(data)


@app.route('/api/export/visitas.csv')
def export_visitas_csv():
    desde = request.args.get('desde', '').strip()
    hasta = request.args.get('hasta', '').strip()
    code = request.args.get('code', '').strip().upper()
    db = get_db()
    sql = 'SELECT fecha, code, cliente, razon, calle, vta, ts, foto_ts FROM visitas'
    conds, params = [], []
    if code:
        conds.append('code = ?')
        params.append(code)
    if desde:
        conds.append('fecha >= ?')
        params.append(desde)
    if hasta:
        conds.append('fecha <= ?')
        params.append(hasta)
    if conds:
        sql += ' WHERE ' + ' AND '.join(conds)
    sql += ' ORDER BY fecha, code, ts'
    rows = _exec(db, sql, params).fetchall()
    lines = ['fecha;code;colaborador;cliente;razon_social;calle;ruta;hora;con_foto']
    for r in rows:
        v = VENDOR_BY_CODE.get(r['code'])
        ts = r['ts']
        hora = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime('%H:%M:%S') if ts else ''
        cf = 'si' if (r['foto_ts'] and r['foto_ts'] > 0) else 'no'
        vals = [r['fecha'], r['code'], (v.get('name', '') if v else ''), r['cliente'],
                r['razon'], r['calle'], r['vta'], hora, cf]
        lines.append(';'.join(str(x).replace('\n', ' ').replace(';', ',') for x in vals))
    csv_text = '\r\n'.join(lines) + '\r\n'
    return Response(csv_text, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=visitas.csv'})


@app.route('/api/export/recorrido.csv')
def export_recorrido_csv():
    code = request.args.get('code', '').strip().upper()
    fecha = request.args.get('fecha', '').strip()
    s, e = _day_range(fecha)
    rows = _exec(get_db(),
                 'SELECT lat, lon, ts FROM positions WHERE code=? AND ts>=? AND ts<? ORDER BY ts',
                 (code, s, e)).fetchall()
    lines = ['fecha;hora;lat;lon']
    for r in rows:
        dt = datetime.fromtimestamp(r['ts'] / 1000, timezone.utc) - timedelta(hours=3)
        lines.append('%s;%s;%.6f;%.6f' % (fecha, dt.strftime('%H:%M:%S'), r['lat'], r['lon']))
    csv_text = '\r\n'.join(lines) + '\r\n'
    return Response(csv_text, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=recorrido_%s_%s.csv' % (code, fecha)})


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
    vendors = _exec(get_db(),
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
