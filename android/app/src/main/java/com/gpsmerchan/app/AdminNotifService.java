package com.gpsmerchan.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import org.json.JSONArray;
import org.json.JSONObject;

public class AdminNotifService extends Service {

    private static final String CHANNEL_ID = "admin_alerts";
    private static final String CHANNEL_MONITOR = "admin_monitor";
    private static final int NOTIF_MONITOR = 10;
    private static final long POLL_MS = 20000;

    private Handler handler;
    private Runnable poller;
    private final Map<String, Long> known = new HashMap<>();

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
        handler = new Handler(Looper.getMainLooper());
        poller = new Runnable() {
            @Override
            public void run() {
                poll();
                handler.postDelayed(this, POLL_MS);
            }
        };
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = monitorNotif();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_MONITOR, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIF_MONITOR, n);
        }
        if (handler != null && poller != null) {
            handler.removeCallbacks(poller);
            handler.post(poller);
        }
        return START_STICKY;
    }

    private void poll() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection c = null;
                try {
                    URL u = new URL(MainActivity.BASE_URL + "/api/alerts");
                    c = (HttpURLConnection) u.openConnection();
                    c.setConnectTimeout(10000);
                    c.setReadTimeout(10000);
                    if (c.getResponseCode() != 200) return;
                    InputStream is = c.getInputStream();
                    BufferedReader r = new BufferedReader(new InputStreamReader(is, StandardCharsets.UTF_8));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = r.readLine()) != null) sb.append(line);
                    r.close();
                    final JSONArray arr = new JSONArray(sb.toString());
                    handler.post(new Runnable() {
                        @Override
                        public void run() {
                            process(arr);
                        }
                    });
                } catch (Exception ignored) {
                } finally {
                    if (c != null) c.disconnect();
                }
            }
        }).start();
    }

    private void process(JSONArray arr) {
        Set<String> active = new HashSet<>();
        for (int i = 0; i < arr.length(); i++) {
            try {
                JSONObject o = arr.getJSONObject(i);
                String code = o.optString("code", "");
                String name = o.optString("name", code);
                long ts = o.optLong("ts", 0);
                if (code.isEmpty()) continue;
                active.add(code);
                Long last = known.get(code);
                if (last == null || last.longValue() != ts) {
                    known.put(code, ts);
                    notifyAlert(code, name);
                }
            } catch (Exception ignored) {
            }
        }
        for (String code : new HashSet<>(known.keySet())) {
            if (!active.contains(code)) {
                known.remove(code);
                cancelAlert(code);
            }
        }
    }

    private void notifyAlert(String code, String name) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null) return;
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentTitle("GPS apagado: " + name)
                .setContentText("El merchan apagó la ubicación. Tocá para ver el panel.")
                .setContentIntent(pi)
                .setAutoCancel(true)
                .setPriority(Notification.PRIORITY_HIGH)
                .build();
        nm.notify(code.hashCode(), n);
    }

    private void cancelAlert(String code) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) nm.cancel(code.hashCode());
    }

    private Notification monitorNotif() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_MONITOR)
                .setContentTitle("Monitoreo de alertas activo")
                .setContentText("Recibís avisos si un merchan apaga la ubicación")
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentIntent(pi)
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_LOW)
                .build();
    }

    private void createChannels() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel ch = new NotificationChannel(CHANNEL_ID, "Alertas de ubicación",
                NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("Aviso cuando un merchan apaga la ubicación");
        nm.createNotificationChannel(ch);
        NotificationChannel mch = new NotificationChannel(CHANNEL_MONITOR, "Monitoreo",
                NotificationManager.IMPORTANCE_LOW);
        nm.createNotificationChannel(mch);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        if (handler != null && poller != null) handler.removeCallbacks(poller);
        super.onDestroy();
    }
}
