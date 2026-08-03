package com.gpsmerchan.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.View;
import android.view.WindowManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.Toast;

import java.lang.ref.WeakReference;

public class MainActivity extends Activity {

    public static final String BASE_URL = "https://gps-vendedores.onrender.com";
    public static final String PREFS = "gps_merchan";
    public static final String KEY_CODE = "code";
    public static final String KEY_SESSION = "session";

    private static final int FILE_CHOOSER_REQ = 1001;

    private WebView web;
    private ValueCallback<Uri[]> filePathCallback;
    private static WeakReference<MainActivity> sInstance;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        sInstance = new WeakReference<>(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2001);
        }

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String code = prefs.getString(KEY_CODE, "");
        if (code.isEmpty()) {
            showSetup();
        } else {
            loadTracker(code);
        }
    }

    private void showSetup() {
        setContentView(R.layout.activity_setup);
        final EditText input = findViewById(R.id.setup_code);
        Button go = findViewById(R.id.setup_go);
        go.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String code = input.getText().toString().trim().toUpperCase();
                if (code.isEmpty()) {
                    Toast.makeText(MainActivity.this, "Ingresá tu código de merchan", Toast.LENGTH_SHORT).show();
                    return;
                }
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_CODE, code).apply();
                loadTracker(code);
            }
        });
    }

    private void loadTracker(final String code) {
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        ImageButton change = findViewById(R.id.change_btn);
        change.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(KEY_CODE).apply();
                showSetup();
            }
        });

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);

        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                callback.invoke(origin, true, false);
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                request.grant(request.getResources());
            }

            @Override
            public boolean onShowFileChooser(WebView wv, ValueCallback<Uri[]> cb, FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                }
                filePathCallback = cb;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_REQ);
                } catch (Exception e) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.addJavascriptInterface(new Bridge(), "AndroidBridge");
        web.loadUrl(BASE_URL + "/tracker/" + code);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_REQ) {
            if (filePathCallback == null) {
                super.onActivityResult(requestCode, resultCode, data);
                return;
            }
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null) {
                Uri uri = data.getData();
                if (uri != null) result = new Uri[]{uri};
            }
            filePathCallback.onReceiveValue(result);
            filePathCallback = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    private void maybeAskBattery() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (prefs.getBoolean("batt_asked", false)) return;
        prefs.edit().putBoolean("batt_asked", true).apply();
        try {
            if (Build.VERSION.SDK_INT >= 23) {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                    Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            Uri.parse("package:" + getPackageName()));
                    startActivity(i);
                }
            }
        } catch (Exception ignored) {
        }
    }

    class Bridge {
        @android.webkit.JavascriptInterface
        public void startService(String code, String session) {
            getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putString(KEY_CODE, code).putString(KEY_SESSION, session).apply();
            Intent i = new Intent(MainActivity.this, LocationService.class);
            i.setAction("START");
            i.putExtra("code", code);
            i.putExtra("session", session == null ? "" : session);
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
            else startService(i);
            maybeAskBattery();
        }

        @android.webkit.JavascriptInterface
        public void stopService() {
            try {
                stopService(new Intent(MainActivity.this, LocationService.class));
            } catch (Exception ignored) {
            }
        }
    }

    static void notifyStopped() {
        MainActivity a = sInstance == null ? null : sInstance.get();
        if (a != null) {
            a.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    if (a.web != null) {
                        a.web.evaluateJavascript("try{if(window.stop)window.stop();}catch(e){}", null);
                    }
                }
            });
        }
    }
}
