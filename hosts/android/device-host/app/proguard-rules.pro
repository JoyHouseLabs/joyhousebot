# Keep the Shizuku UserService entry point; it is bound by name from the
# shell-uid process and must not be renamed or stripped.
-keep class dev.porthouse.host.android.executor.ShellUserService { *; }
-keep class dev.porthouse.host.android.shizuku.IDeviceShellService { *; }
-keep class dev.porthouse.host.android.shizuku.IDeviceShellService$* { *; }

# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class dev.porthouse.host.android.**$$serializer { *; }
