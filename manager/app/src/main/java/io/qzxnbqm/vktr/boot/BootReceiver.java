package io.qzxnbqm.vktr.boot;

import static io.qzxnbqm.vktr.boot.ZygotePreload.TAG;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import io.qzxnbqm.vktr.ui.util.KsuCliKt;

public class BootReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        var action = intent.getAction();
        if (!Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !"io.qzxnbqm.vktr.boot.LAUNCH".equals(action)) {
            return;
        }
        if (KsuCliKt.rootAvailable()) return;
        try {
            context.startService(new Intent(context, ZygoteService.class));
            Log.i(TAG, "ZygoteService started from boot action: " + action);
        } catch (Throwable e) {

            Log.e(TAG, "Failed to start ZygoteService from boot action: " + action, e);
        }
    }
}
