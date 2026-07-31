# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import time

from flask import Flask, g, jsonify, redirect, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'gps.db'))
VENDORS_FILE = os.path.join(BASE_DIR, 'vendors.json')

with open(VENDORS_FILE, encoding='utf-8') as f:
    VENDORS = json.load(f)

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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=True)
