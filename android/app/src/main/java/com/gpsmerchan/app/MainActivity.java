package com.gpsmerchan.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.View;
import android.view.WindowManager;
import android.webkit.GeolocationPermissions;
import android.webkit.DownloadListener;
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

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.lang.ref.WeakReference;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class MainActivity extends Activity {

    public static final String BASE_URL = "https://gps-vendedores.onrender.com";
    public static final String PREFS = "gps_merchan";
    public static final String KEY_CODE = "code";
    public static final String KEY_SESSION = "session";

    private static final int FILE_CHOOSER_REQ = 1001;
    private static final int PERM_REQ_LOCATION = 2002;
    private static final int PERM_REQ_CAMERA = 2003;
    private static final long UPDATE_COOLDOWN_MS = 10 * 60 * 1000;
    private static final long UPDATE_GUIDE_COOLDOWN_MS = 30 * 60 * 1000;
    private static final long UPDATE_CHECK_PERIOD_MS = 5 * 60 * 1000;

    private WebView web;
    private ValueCallback<Uri[]> filePathCallback;
    private Uri cameraImageUri;
    private static WeakReference<MainActivity> sInstance;
    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private volatile boolean mChecking = false;
    private boolean lockNavigation = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        sInstance = new WeakReference<>(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        if (!BuildConfig.IS_ADMIN) {
            requestLocationPermissions();
            requestCameraPermission();
        }

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2001);
        }

        if (BuildConfig.IS_ADMIN) {
            loadAdmin();
        } else {
            SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
            String code = prefs.getString(KEY_CODE, "");
            if (code.isEmpty()) {
                showSetup();
            } else {
                loadTracker(code);
            }
        }

        checkForUpdate();
        scheduleUpdateChecks();
    }

    @Override
    protected void onResume() {
        super.onResume();
        checkForUpdate();
    }

    private void scheduleUpdateChecks() {
        mHandler.postDelayed(new Runnable() {
            @Override
            public void run() {
                checkForUpdate();
                mHandler.postDelayed(this, UPDATE_CHECK_PERIOD_MS);
            }
        }, UPDATE_CHECK_PERIOD_MS);
    }

    private void checkForUpdate() {
        synchronized (MainActivity.this) {
            if (mChecking) return;
            mChecking = true;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    URL u = new URL(BASE_URL + "/api/app-version?app=" + (BuildConfig.IS_ADMIN ? "admin" : "merchan"));
                    HttpURLConnection c = (HttpURLConnection) u.openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(8000);
                    c.setRequestProperty("User-Agent", "GPSMerchan/" + BuildConfig.VERSION_CODE);
                    int st = c.getResponseCode();
                    if (st == 200) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line);
                        r.close();
                        JSONObject o = new JSONObject(sb.toString());
                        int remote = o.optInt("versionCode", 0);
                        if (remote > BuildConfig.VERSION_CODE) {
                            final SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
                            long lastAttempt = prefs.getLong("last_attempt_ms", 0);
                            if (System.currentTimeMillis() - lastAttempt >= UPDATE_COOLDOWN_MS) {
                                final String apkUrl = o.optString("apkUrl", "");
                                if (!apkUrl.isEmpty()) {
                                    File dir = getExternalFilesDir(null);
                                    if (dir != null) {
                                        final File existing = new File(dir, "GPS-Merchan.apk");
                                        if (prefs.getInt("downloaded_version", 0) == remote
                                                && existing.exists() && existing.length() > 100000) {
                                            runOnUiThread(new Runnable() {
                                                @Override
                                                public void run() { installApk(existing, remote, prefs); }
                                            });
                                        } else {
                                            downloadAndInstall(apkUrl, remote, prefs);
                                        }
                                    }
                                }
                            }
                        }
                    }
                } catch (Exception ignored) {
                } finally {
                    synchronized (MainActivity.this) {
                        mChecking = false;
                    }
                }
            }
        }).start();
    }

    private void downloadAndInstall(final String url, final int remoteVersion, final SharedPreferences prefs) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    File dir = getExternalFilesDir(null);
                    if (dir == null) return;
                    final File apk = new File(dir, "GPS-Merchan.apk");
                    HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                    c.setInstanceFollowRedirects(true);
                    c.setConnectTimeout(15000);
                    c.setReadTimeout(60000);
                    c.setRequestProperty("User-Agent", "GPSMerchan");
                    InputStream is = c.getInputStream();
                    FileOutputStream fos = new FileOutputStream(apk);
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = is.read(buf)) != -1) fos.write(buf, 0, n);
                    fos.close();
                    is.close();
                    if (apk.length() > 100000) {
                        prefs.edit().putInt("downloaded_version", remoteVersion).apply();
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() { installApk(apk, remoteVersion, prefs); }
                        });
                    }
                } catch (Exception ignored) {
                }
            }
        }).start();
    }

    private void installApk(File apk, int remoteVersion, SharedPreferences prefs) {
        try {
            if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
                long lastGuide = prefs.getLong("last_guide_ms", 0);
                if (System.currentTimeMillis() - lastGuide >= UPDATE_GUIDE_COOLDOWN_MS) {
                    prefs.edit().putLong("last_guide_ms", System.currentTimeMillis()).apply();
                    Toast.makeText(this, "Para actualizarte solo, activá «Permitir instalar apps desconocidas» para GPS Merchan.", Toast.LENGTH_LONG).show();
                    Intent i = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:" + getPackageName()));
                    startActivity(i);
                } else {
                    Toast.makeText(this, "Falta activar «Permitir instalar apps desconocidas» para actualizar.", Toast.LENGTH_LONG).show();
                }
                return;
            }
            prefs.edit().putLong("last_attempt_ms", System.currentTimeMillis()).apply();
            Uri uri = FileProvider.getUriForFile(this, BuildConfig.APPLICATION_ID + ".fileprovider", apk);
            Intent i = new Intent(Intent.ACTION_VIEW);
            i.setDataAndType(uri, "application/vnd.android.package-archive");
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            Toast.makeText(this, "Nueva versión disponible. Descargala del link del administrador.", Toast.LENGTH_LONG).show();
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

    private void loadAdmin() {
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        ImageButton change = findViewById(R.id.change_btn);
        change.setVisibility(View.GONE);
        lockNavigation = false;

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);

        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient());
        web.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                try {
                    Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(i);
                } catch (Exception ignored) {
                    Toast.makeText(MainActivity.this, "No se pudo abrir la descarga.", Toast.LENGTH_SHORT).show();
                }
            }
        });
        web.loadUrl(BASE_URL);
    }

    private void loadTracker(final String code) {
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        ImageButton change = findViewById(R.id.change_btn);
        change.setContentDescription("Cerrar aplicación");
        change.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        lockNavigation = true;

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
                cameraImageUri = null;

                Intent takePicture = new Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE);
                File photoFile = null;
                try {
                    File storageDir = new File(getCacheDir(), "photos");
                    storageDir.mkdirs();
                    String timeStamp = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
                    photoFile = File.createTempFile("PHOTO_" + timeStamp + "_", ".jpg", storageDir);
                } catch (IOException ignored) {}
                if (photoFile != null) {
                    cameraImageUri = FileProvider.getUriForFile(MainActivity.this,
                            BuildConfig.APPLICATION_ID + ".fileprovider", photoFile);
                    takePicture.putExtra(android.provider.MediaStore.EXTRA_OUTPUT, cameraImageUri);
                    takePicture.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                }

                Intent contentSelection = params.createIntent();

                Intent[] intentArray;
                if (takePicture.resolveActivity(getPackageManager()) != null) {
                    intentArray = new Intent[]{takePicture};
                } else {
                    intentArray = new Intent[0];
                }

                Intent chooserIntent = new Intent(Intent.ACTION_CHOOSER);
                chooserIntent.putExtra(Intent.EXTRA_INTENT, contentSelection);
                chooserIntent.putExtra(Intent.EXTRA_INITIAL_INTENTS, intentArray);

                try {
                    startActivityForResult(chooserIntent, FILE_CHOOSER_REQ);
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
            if (resultCode == RESULT_OK) {
                if (data != null && data.getData() != null) {
                    result = new Uri[]{data.getData()};
                } else if (cameraImageUri != null) {
                    result = new Uri[]{cameraImageUri};
                }
            }
            filePathCallback.onReceiveValue(result);
            filePathCallback = null;
            cameraImageUri = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web == null) {
            super.onBackPressed();
            return;
        }
        web.evaluateJavascript("window.closeAnyOverlay && window.closeAnyOverlay()", new ValueCallback<String>() {
            @Override
            public void onReceiveValue(String value) {
                if (value != null && "true".equals(value)) return;
                if (web.canGoBack()) web.goBack();
                else if (lockNavigation) {
                    Toast.makeText(MainActivity.this, "Para cerrar la app usá el botón X de arriba. Tu sesión sigue activa.", Toast.LENGTH_LONG).show();
                } else {
                    moveTaskToBack(true);
                }
            }
        });
    }

    private boolean hasLocationPermission() {
        if (Build.VERSION.SDK_INT < 23) return true;
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestLocationPermissions() {
        if (Build.VERSION.SDK_INT >= 23 && !hasLocationPermission()) {
            requestPermissions(new String[]{
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION
            }, PERM_REQ_LOCATION);
        }
    }

    private void requestCameraPermission() {
        if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, PERM_REQ_CAMERA);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERM_REQ_LOCATION) {
            if (!hasLocationPermission()) {
                Toast.makeText(this, "Sin permiso de ubicación no se puede enviar la posición. Aceptá el permiso y probá de nuevo.", Toast.LENGTH_LONG).show();
            }
        }
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
            if (!hasLocationPermission()) {
                requestLocationPermissions();
                return;
            }
            getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putString(KEY_CODE, code).putString(KEY_SESSION, session).apply();
            Intent i = new Intent(MainActivity.this, LocationService.class);
            i.setAction("START");
            i.putExtra("code", code);
            i.putExtra("session", session == null ? "" : session);
            if (Build.VERSION.SDK_INT >= 26) MainActivity.this.startForegroundService(i);
            else MainActivity.this.startService(i);
            maybeAskBattery();
        }

        @android.webkit.JavascriptInterface
        public void stopService() {
            try {
                MainActivity.this.stopService(new Intent(MainActivity.this, LocationService.class));
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

    static void notifyJornadaEnd() {
        MainActivity a = sInstance == null ? null : sInstance.get();
        if (a != null) {
            a.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    if (a.web != null) {
                        a.web.evaluateJavascript("try{if(window.finishShift)window.finishShift();}catch(e){}", null);
                    }
                }
            });
        }
    }
}
