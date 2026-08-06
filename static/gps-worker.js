var cfg = null;
var watchId = null;
var lastLat = null;
var lastLon = null;

if (!self.navigator || !self.navigator.geolocation) {
  self.postMessage({ type: 'unsupported' });
}

self.onmessage = function (e) {
  var d = e.data;
  if (d.action === 'start') {
    cfg = { code: d.code, session: d.session, base: d.base };
    if (watchId !== null) self.navigator.geolocation.clearWatch(watchId);
    watchId = self.navigator.geolocation.watchPosition(onPos, onErr, {
      enableHighAccuracy: true, timeout: 20000, maximumAge: 5000
    });
    setInterval(send, 10000);
  } else if (d.action === 'stop') {
    if (watchId !== null) self.navigator.geolocation.clearWatch(watchId);
    watchId = null;
    cfg = null;
  }
};

function onPos(p) {
  lastLat = p.coords.latitude;
  lastLon = p.coords.longitude;
  self.postMessage({
    type: 'pos',
    lat: lastLat,
    lon: lastLon,
    acc: Math.round(p.coords.accuracy)
  });
}

function onErr(e) {
  self.postMessage({ type: 'err', code: e.code });
}

function send() {
  if (lastLat === null || !cfg) return;
  fetch(cfg.base + '/api/track', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: cfg.code, lat: lastLat, lon: lastLon, session: cfg.session })
  }).then(function (r) { return r.json(); }).then(function (d) {
    self.postMessage({
      type: 'sent',
      ok: !!d.ok,
      error: d.error,
      fin: !!d.jornada_fin,
      ts: new Date().toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    });
  }).catch(function () {
    self.postMessage({ type: 'sent', ok: false, error: 'sin conexion' });
  });
}
