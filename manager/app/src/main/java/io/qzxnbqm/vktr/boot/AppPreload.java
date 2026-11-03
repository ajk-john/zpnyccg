package io.qzxnbqm.vktr.boot;

import android.app.ZygotePreload;
import android.content.pm.ApplicationInfo;
import android.util.Log;

import androidx.annotation.NonNull;

import java.io.File;

public class AppPreload implements ZygotePreload {
    public static final String TAG = "AppBoot";

    private static native void forkDontCareAndExecDaemon(String ksudPath, String packageName);

    @Override
    public void doPreload(@NonNull ApplicationInfo appInfo) {
        File f = new File(appInfo.nativeLibraryDir, "libnseqsv.so");
        try {
            System.loadLibrary("fshqpk");
            Log.d(TAG, "executing boot daemon ...");
            forkDontCareAndExecDaemon(f.getAbsolutePath(), appInfo.packageName);
        } catch (Throwable t) {
            Log.e(TAG, "failed to late load", t);
        }
    }
}
