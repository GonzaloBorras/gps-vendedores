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
import android.os.IBinder;
import android.util.Log;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class LocationService extends Service implements LocationListener {

    private static final String CHANNEL_ID = "gps_live";
    private static final int NOTIF_ID = 1;
    private static final String TAG = "GPSMerchan";

    private LocationManager lm;
    private String code = "";
    private String session = "";
    private long lastSent = 0;
    private Location lastLoc = null;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        SharedPreferences prefs = getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE);
        code = prefs.getString(MainActivity.KEY_CODE, "");
        session = prefs.getString(MainActivity.KEY_SESSION, "");
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
        } catch (SecurityException e) {
            Log.e(TAG, "permiso de ubicacion no concedido", e);
        }
    }

    private void stopTracking() {
        try {
            if (lm != null) lm.removeUpdates(this);
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onLocationChanged(Location loc) {
        if (loc == null) return;
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
    }

    @Override
    public void onProviderDisabled(String provider) {
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
                            ",\"session\":\"" + session + "\"}";
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

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);

        Notification.Builder b = new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Enviando ubicación en vivo")
                .setContentText("Merchan " + code)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setOngoing(true)
                .setContentIntent(pi);

        Intent stopIntent = new Intent(this, LocationService.class);
        stopIntent.setAction("STOP");
        PendingIntent stopPi = PendingIntent.getService(this, 1, stopIntent, PendingIntent.FLAG_IMMUTABLE);
        b.addAction(new Notification.Action.Builder(
                android.R.drawable.ic_menu_close_clear_cancel, "Detener", stopPi).build());
        return b.build();
    }

    private void createChannel() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "Envío de ubicación",
                NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Mantiene el envío de ubicación con la pantalla apagada");
        nm.createNotificationChannel(ch);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopTracking();
        super.onDestroy();
    }
}
