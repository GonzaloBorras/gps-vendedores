package com.gpsmerchan.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

public class BootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !"android.intent.action.MY_PACKAGE_REPLACED".equals(action)
                && !"android.intent.action.TIME_SET".equals(action)) {
            return;
        }
        if (BuildConfig.IS_ADMIN) return;
        SharedPreferences prefs = context.getSharedPreferences(MainActivity.PREFS, android.content.Context.MODE_PRIVATE);
        String code = prefs.getString(MainActivity.KEY_CODE, "");
        if (code.isEmpty()) return;
        Intent i = new Intent(context, LocationService.class);
        i.setAction("START");
        i.putExtra("code", code);
        i.putExtra("session", prefs.getString(MainActivity.KEY_SESSION, ""));
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i);
        else context.startService(i);
        WatchdogReceiver.schedule(context);
    }
}
