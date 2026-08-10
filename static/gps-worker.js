var cfg = null;
var watchId = null;
var lastLat = null;
var lastLon = null;
var lastSendLat = null;
var lastSendLon = null;
var lastSendTs = 0;

if (!self.navigator || !self.navigator.geolocation) {
  self.postMessage({ type: 'unsupported' });
}

self.onmessage = function (e) {
  var d = e.data;
  if (d.action === 'start') {
    cfg = { code: d.code, session: d.session, base: d.base };
    if (watchId !== null) self.navigator.geolocation.clearWatch(watchId);
    watchId = self.navigator.geolocation.watchPosition(onPos, onErr, {
      enableHighAccuracy: true, timeout: 20000, maximumAge: 10000
    });
    setInterval(maybeSend, 10000);
  } else if (d.action === 'stop') {
    if (watchId !== null) self.navigator.geolocation.clearWatch(watchId);
    watchId = null;
    cfg = null;
  }
};

function distM(aLat, aLon, bLat, bLon) {
  var R = 6371000;
  var dLat = (bLat - aLat) * Math.PI / 180;
  var dLon = (bLon - aLon) * Math.PI / 180;
  var x = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(aLat * Math.PI / 180) * Math.cos(bLat * Math.PI / 180) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return R * 2 * Math.asin(Math.sqrt(x));
}
function maybeSend() {
  if (lastLat === null || !cfg) return;
  if (lastSendLat === null || Date.now() - lastSendTs >= 60000 ||
      distM(lastSendLat, lastSendLon, lastLat, lastLon) >= 25) {
    send();
  }
}

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
  lastSendTs = Date.now();
  lastSendLat = lastLat;
  lastSendLon = lastLon;
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
