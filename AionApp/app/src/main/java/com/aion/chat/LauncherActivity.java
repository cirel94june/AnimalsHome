package com.aion.chat;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

import com.aion.chat.homecoming.HomecomingActivity;
import com.aion.chat.homecoming.HomecomingBackupScheduler;
import com.aion.chat.homecoming.HomecomingModeStore;
import com.aion.chat.homecoming.HomecomingReturnPackageRepository;

/**
 * 启动页 — 填写服务器地址后连接；服务器不可用时可进归巢模式
 */
public class LauncherActivity extends AppCompatActivity {
    private HomecomingBackupScheduler homecomingBackupScheduler;

    public static final String EXTRA_FORCE_ADDRESS_PICKER = "force_address_picker";
    private static final String PREFS       = "aion_prefs";
    private static final String KEY_URL     = "saved_url";
    private static final String KEY_AUTO    = "auto_connect";

    // 服务器地址由用户在启动页填写（部署完成时会给出），不再写死

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        boolean forceAddressPicker = getIntent().getBooleanExtra(
                EXTRA_FORCE_ADDRESS_PICKER, false);
        HomecomingModeStore homecomingMode = new HomecomingModeStore(this);
        if (homecomingMode.isActive()
                || homecomingMode.isFrozen()
                || homecomingMode.isFreezing()) {
            startActivity(new Intent(this, HomecomingActivity.class));
            finish();
            return;
        }
        if (LauncherResumePolicy.shouldFinishWithoutLaunching(
                isTaskRoot(), forceAddressPicker)) {
            finish();
            return;
        }

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        prepareHomecomingBackup();

        String savedUrl = ConnectionEndpoint.normalizeServerInput(prefs.getString(KEY_URL, ""));

        // 如果上次勾选了"记住地址"，直接跳转
        if (prefs.getBoolean(KEY_AUTO, false) && savedUrl != null) {
            prefs.edit().putString(KEY_URL, savedUrl).apply();
            refreshHomecomingBackup(savedUrl, false);
            launchWebView(savedUrl);
            return;
        }
        if (savedUrl != null) {
            refreshHomecomingBackup(savedUrl, false);
        }

        setContentView(R.layout.activity_launcher);

        EditText etServerUrl  = findViewById(R.id.etServerUrl);
        TextView tvHomecomingHint = findViewById(R.id.tvHomecomingHint);
        Button   btnConnect   = findViewById(R.id.btnConnect);
        Button   btnHomecoming= findViewById(R.id.btnHomecoming);
        CheckBox cbRemember   = findViewById(R.id.cbRemember);

        if (savedUrl != null) {
            etServerUrl.setText(savedUrl);
        }
        cbRemember.setChecked(prefs.getBoolean(KEY_AUTO, false));
        boolean pendingReturn = false;
        try {
            pendingReturn = !new HomecomingReturnPackageRepository(this)
                    .pendingInSequence().isEmpty();
        } catch (Exception ignored) {
            // The normal address picker must remain available.
        }
        final boolean hasPendingReturn = pendingReturn;
        if (hasPendingReturn) {
            btnHomecoming.setText("🏠  归巢模式 · 待回传");
            tvHomecomingHint.setText("有归巢数据等待手动回传");
        }

        btnConnect.setOnClickListener(v -> {
            String url = ConnectionEndpoint.normalizeServerInput(etServerUrl.getText().toString());
            if (url == null) {
                etServerUrl.setError("地址不对，例如 https://名字.xxx.ts.net");
                return;
            }
            saveIfNeeded(prefs, cbRemember.isChecked(), url);
            refreshHomecomingBackup(url, true);
            launchWebView(url);
        });
        etServerUrl.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO) {
                btnConnect.performClick();
                return true;
            }
            return false;
        });

        btnHomecoming.setOnClickListener(v -> {
            Intent intent = new Intent(this, HomecomingActivity.class);
            intent.putExtra(HomecomingActivity.EXTRA_SHOW_CONFIRMATION, true);
            intent.putExtra(HomecomingActivity.EXTRA_OPEN_RETURN, hasPendingReturn);
            startActivity(intent);
        });
    }

    private void prepareHomecomingBackup() {
        try {
            homecomingBackupScheduler = HomecomingBackupScheduler.create(this);
        } catch (RuntimeException ignored) {
            homecomingBackupScheduler = null;
        }
    }

    private void refreshHomecomingBackup(String url, boolean routeChanged) {
        try {
            if (homecomingBackupScheduler == null) {
                return;
            }
            if (routeChanged) {
                homecomingBackupScheduler.onNormalRouteSelected(url);
            } else {
                homecomingBackupScheduler.onLauncherForeground(url);
            }
        } catch (RuntimeException ignored) {
            // Homecoming backup is best-effort and never blocks normal launch.
        }
    }

    private void saveIfNeeded(SharedPreferences prefs, boolean remember, String url) {
        SharedPreferences.Editor editor = prefs.edit();
        editor.putString(KEY_URL, url);
        editor.putBoolean(KEY_AUTO, remember);
        editor.apply();
    }

    private void launchWebView(String url) {
        url = ConnectionEndpoint.normalizePageUrl(url);
        // 启动前台推送服务
        startPushService(url);

        Intent intent = new Intent(this, WebViewActivity.class);
        intent.putExtra("url", url);
        startActivity(intent);
        finish();
    }

    private void startPushService(String url) {
        // 启动前台服务（权限请求移到 WebViewActivity，因为本 Activity 会立即 finish）
        Intent serviceIntent = new Intent(this, AionPushService.class);
        serviceIntent.putExtra("url", url);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent);
        } else {
            startService(serviceIntent);
        }
    }
}
