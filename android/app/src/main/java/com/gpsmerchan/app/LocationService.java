package com.gpsmerchan.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import org.json.JSONObject;

public class LocationService extends Service implements LocationListener {

    private static final String CHANNEL_ID = "gps_live";
    private static final String CHANNEL_ALERTA = "gps_alerta";
    private static final int NOTIF_ID = 1;
    private static final int GPS_ALERT_ID = 2;
    private static final String TAG = "GPSMerchan";

    public static volatile boolean alive = false;

    private LocationManager lm;
    private String code = "";
    private String session = "";
    private long lastSent = 0;
    private Location lastLoc = null;

    private boolean lastGpsState = true;
    private long lastGpsSent = 0;
    private Handler handler;
    private Runnable gpsCheck;
    private Runnable shiftRefresh;

    private long jornadaStart = 0;
    private long jornadaEnd = 0;
    private boolean jornadaKnown = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        SharedPreferences prefs = getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE);
        code = prefs.getString(MainActivity.KEY_CODE, "");
        session = prefs.getString(MainActivity.KEY_SESSION, "");
        handler = new Handler(Looper.getMainLooper());
        gpsCheck = new Runnable() {
            @Override
            public void run() {
                reportGps(anyProviderEnabled());
                handler.postDelayed(this, 60000);
            }
        };
        shiftRefresh = new Runnable() {
            @Override
            public void run() {
                refreshShift();
                handler.postDelayed(this, 300000);
            }
        };
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = (intent != null && intent.getAction() != null) ? intent.getAction() : "START";
        if ("STOP".equals(action)) {
            MainActivity.notifyStopped();
            stopTracking();
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null) {
            String c = intent.getStringExtra("code");
            if (c != null && !c.isEmpty()) {
                code = c;
                session = intent.getStringExtra("session") == null ? "" : intent.getStringExtra("session");
                getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE)
                        .edit()
                        .putString(MainActivity.KEY_CODE, code)
                        .putString(MainActivity.KEY_SESSION, session)
                        .apply();
            }
        }
        if (code.isEmpty()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        Notification n = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } else {
            startForeground(NOTIF_ID, n);
        }
        alive = true;
        startUpdates();
        return START_STICKY;
    }

    private void startUpdates() {
        try {
            if (lm == null) return;
            try {
                lm.removeUpdates(this);
            } catch (Exception ignored) {
            }
            if (lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 10000L, 3f, this);
            }
            if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 15000L, 10f, this);
            }
            reportGps(anyProviderEnabled());
            if (handler != null && gpsCheck != null) {
                handler.removeCallbacks(gpsCheck);
                handler.postDelayed(gpsCheck, 60000);
            }
            if (handler != null && shiftRefresh != null) {
                handler.removeCallbacks(shiftRefresh);
                handler.post(shiftRefresh);
            }
        } catch (SecurityException e) {
            Log.e(TAG, "permiso de ubicacion no concedido", e);
        }
    }

    private void stopTracking() {
        try {
            if (lm != null) lm.removeUpdates(this);
        } catch (Exception ignored) {
        }
        if (handler != null && gpsCheck != null) handler.removeCallbacks(gpsCheck);
        if (handler != null && shiftRefresh != null) handler.removeCallbacks(shiftRefresh);
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm != null) nm.cancel(GPS_ALERT_ID);
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onLocationChanged(Location loc) {
        if (loc == null) return;
        if (!jornadaActiva()) return;
        if (lastLoc == null || loc.getTime() > lastLoc.getTime()) {
            lastLoc = loc;
        }
        long now = System.currentTimeMillis();
        if (now - lastSent < 9000) return;
        lastSent = now;
        sendPosition(lastLoc);
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {
    }

    @Override
    public void onProviderEnabled(String provider) {
        if (anyProviderEnabled()) reportGps(true);
    }

    @Override
    public void onProviderDisabled(String provider) {
        if (!anyProviderEnabled()) reportGps(false);
    }

    private boolean anyProviderEnabled() {
        try {
            return lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
                    || lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER);
        } catch (Exception e) {
            return true;
        }
    }

    private void reportGps(final boolean gps) {
        if (code.isEmpty()) return;
        long now = System.currentTimeMillis();
        boolean changed = gps != lastGpsState;
        if (!changed && now - lastGpsSent < 15000) return;
        lastGpsState = gps;
        lastGpsSent = now;
        if (changed) updateGpsNotif(gps);
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(MainActivity.BASE_URL + "/api/gps-status");
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(10000);
                    conn.setRequestProperty("Content-Type", "application/json");
                    String body = "{\"code\":\"" + code + "\",\"gps\":" + gps +
                            ",\"app\":\"android\",\"version\":\"" + BuildConfig.VERSION_NAME +
                            "\",\"versionCode\":" + BuildConfig.VERSION_CODE + "}";
                    OutputStream os = conn.getOutputStream();
                    os.write(body.getBytes(StandardCharsets.UTF_8));
                    os.close();
                    conn.getResponseCode();
                } catch (Exception e) {
                    Log.e(TAG, "gps status envio fallido", e);
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        }).start();
    }

    private void updateGpsNotif(boolean gps) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null) return;
        if (!gps) {
            Intent open = new Intent(this, MainActivity.class);
            PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
            Intent gpsSet = new Intent(android.provider.Settings.ACTION_LOCATION_SOURCE_SETTINGS);
            PendingIntent gpsPi = PendingIntent.getActivity(this, 2, gpsSet, PendingIntent.FLAG_IMMUTABLE);
            Notification n = new Notification.Builder(this, CHANNEL_ALERTA)
                    .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                    .setContentTitle("GPS apagado")
                    .setContentText("Apagaste la ubicación. Volvé a prenderla para que el administrador vea tu posición.")
                    .setContentIntent(pi)
                    .addAction(0, "Prender GPS", gpsPi)
                    .setAutoCancel(true)
                    .setPriority(Notification.PRIORITY_HIGH)
                    .build();
            nm.notify(GPS_ALERT_ID, n);
        } else {
            nm.cancel(GPS_ALERT_ID);
        }
    }

    private void sendPosition(final Location loc) {
        if (code.isEmpty() || loc == null) return;
        final double lat = loc.getLatitude();
        final double lon = loc.getLongitude();
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(MainActivity.BASE_URL + "/api/track");
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(15000);
                    conn.setRequestProperty("Content-Type", "application/json");
                    String body = "{\"code\":\"" + code + "\",\"lat\":" + lat + ",\"lon\":" + lon +
                            ",\"session\":\"" + session + "\",\"app\":\"android\",\"version\":\"" +
                            BuildConfig.VERSION_NAME + "\",\"versionCode\":" + BuildConfig.VERSION_CODE + "}";
                    OutputStream os = conn.getOutputStream();
                    os.write(body.getBytes(StandardCharsets.UTF_8));
                    os.close();
                    conn.getResponseCode();
                } catch (Exception e) {
                    Log.e(TAG, "envio fallido", e);
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        }).start();
    }

    private void refreshShift() {
        if (code.isEmpty()) return;
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL(MainActivity.BASE_URL + "/api/shift?code=" + code);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(10000);
                    int st = conn.getResponseCode();
                    if (st == 200) {
                        InputStream is = conn.getInputStream();
                        BufferedReader r = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line);
                        r.close();
                        JSONObject o = new JSONObject(sb.toString());
                        if (o.optBoolean("ok", false)) {
                            final long s = o.optLong("start_ts", 0);
                            final long e = o.optLong("end_ts", 0);
                            handler.post(new Runnable() {
                                @Override
                                public void run() {
                                    jornadaStart = s;
                                    jornadaEnd = e;
                                    jornadaKnown = true;
                                }
                            });
                        }
                    }
                } catch (Exception e) {
                    Log.e(TAG, "shift consulta fallida", e);
                } finally {
                    if (conn != null) conn.disconnect();
                }
            }
        }).start();
    }

    private boolean jornadaActiva() {
        if (!jornadaKnown) return true;
        long now = System.currentTimeMillis();
        return now >= jornadaStart && now < jornadaEnd;
    }

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Enviando ubicación en vivo")
                .setContentText("Merchan " + code)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setOngoing(true)
                .setContentIntent(pi);

        return b.build();
    }

    private void createChannel() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "Envío de ubicación",
                NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Mantiene el envío de ubicación con la pantalla apagada");
        nm.createNotificationChannel(ch);
        NotificationChannel ach = new NotificationChannel(CHANNEL_ALERTA, "Alertas de GPS",
                NotificationManager.IMPORTANCE_HIGH);
        ach.setDescription("Aviso cuando apagás la ubicación");
        nm.createNotificationChannel(ach);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        alive = false;
        stopTracking();
        super.onDestroy();
    }
}
