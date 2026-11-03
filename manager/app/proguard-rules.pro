# Protobuf
-shrinkunusedprotofields

# Commons-compress
-dontwarn com.github.luben.zstd.**

# JNI-bound classes: names are part of the native ABI (jni.cc symbols / FindClass)
-keep class io.qzxnbqm.vktr.Natives { *; }
-keep class io.qzxnbqm.vktr.Natives$Profile { *; }
-keep class io.qzxnbqm.vktr.boot.AppPreload { *; }
