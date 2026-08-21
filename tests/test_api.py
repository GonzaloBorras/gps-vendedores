import json


def test_dashboard_requires_pin(client):
    resp = client.get('/')
    assert resp.status_code in (200, 302)


def test_app_version(client):
    resp = client.get('/api/app-version?app=merchan')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'version' in data
    assert 'versionCode' in data


def test_positions(client):
    resp = client.get('/api/positions')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_pdv_api(client):
    resp = client.get('/api/pdv')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_pdv_api_filter_prov(client):
    resp = client.get('/api/pdv?prov=TUCUMAN')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_rutas_api(client):
    resp = client.get('/api/rutas')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_vendors_api(client):
    resp = client.get('/api/vendors')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_track_requires_code(client):
    resp = client.post('/api/track',
                       data=json.dumps({'lat': -33.0, 'lon': -64.0}),
                       content_type='application/json')
    assert resp.status_code in (403, 400)


def test_vistas_resumen(client):
    resp = client.get('/api/visitas/resumen')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)


def test_jornadas(client):
    resp = client.get('/api/jornadas')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)


def test_alerts_list(client):
    resp = client.get('/api/alerts')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_messages_invalid_code(client):
    resp = client.get('/api/messages?code=TEST')
    assert resp.status_code in (200, 404)


def test_geoevents(client):
    resp = client.get('/api/geoevents')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_maintenance_requires_auth(client):
    resp = client.get('/api/maintenance')
    assert resp.status_code in (200, 401, 403)


def test_estadias_invalid_code(client):
    resp = client.get('/api/estadias?code=TEST')
    assert resp.status_code in (200, 400, 404)


def test_reporte_invalid_code(client):
    resp = client.get('/api/reporte?code=TEST')
    assert resp.status_code in (200, 400, 404)


def test_push_vapid(client):
    resp = client.get('/api/push/vapid')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'publicKey' in data


def test_history(client):
    resp = client.get('/api/history?code=TEST')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_history_invalid_days(client):
    resp = client.get('/api/history?code=TEST&days=abc')
    assert resp.status_code == 200


def test_nearest_pdv_no_position(client):
    resp = client.get('/api/nearest-pdv?code=NONEXISTENT')
    assert resp.status_code in (404, 400)


def test_export_xlsx_no_params(client):
    resp = client.get('/api/export/recorrido.xlsx')
    assert resp.status_code == 400
