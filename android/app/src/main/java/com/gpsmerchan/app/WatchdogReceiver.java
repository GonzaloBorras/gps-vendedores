package com.gpsmerchan.app;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

public class WatchdogReceiver extends BroadcastReceiver {

    public static final String ACTION_TICK = "com.gpsmerchan.app.WATCHDOG_TICK";
    private static final long INTERVAL_MS = 10 * 60 * 1000L;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent != null && ACTION_TICK.equals(intent.getAction())) {
            maybeStart(context);
        }
        schedule(context);
    }

    static void maybeStart(Context context) {
        if (BuildConfig.IS_ADMIN) return;
        if (LocationService.alive) return;
        SharedPreferences prefs = context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
        String code = prefs.getString(MainActivity.KEY_CODE, "");
        if (code.isEmpty()) return;
        if (Build.VERSION.SDK_INT >= 23) {
            int fine = context.checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION);
            int coarse = context.checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION);
            if (fine != android.content.pm.PackageManager.PERMISSION_GRANTED
                    && coarse != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                return;
            }
        }
        try {
            Intent i = new Intent(context, LocationService.class);
            i.setAction("START");
            i.putExtra("code", code);
            i.putExtra("session", prefs.getString(MainActivity.KEY_SESSION, ""));
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i);
            else context.startService(i);
        } catch (Exception e) {
            Log.w("GPSMerchan", "watchdog start failed", e);
        }
    }

    public static void schedule(Context context) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        Intent i = new Intent(context, WatchdogReceiver.class);
        i.setAction(ACTION_TICK);
        PendingIntent pi = PendingIntent.getBroadcast(context, 0, i, PendingIntent.FLAG_IMMUTABLE);
        try {
            am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    android.os.SystemClock.elapsedRealtime() + INTERVAL_MS, pi);
        } catch (Exception e) {
            Log.w("GPSMerchan", "watchdog schedule failed", e);
        }
    }
}
