// WHAT THIS FILE DOES
// The Android side's front door. Receives commands from Flutter
// (start, stop, saveText, settings) and sends results back.
// Pipeline: CameraPanel -> FeatureEngine -> ModelBank -> back to Flutter.

package com.example.thesis_anxiety

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.StandardMessageCodec
import io.flutter.plugin.platform.PlatformView
import io.flutter.plugin.platform.PlatformViewFactory
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.provider.MediaStore
import java.io.File

class MainActivity : FlutterActivity() {
    private var panel: CameraPanel? = null
    private var sink: EventChannel.EventSink? = null
    private var diagnostics = false
    private var modelFolder = "behavior_v7"
    private var trackingDelegate = "CPU"
    private var lockFps30 = true
    private var hidePreview = false
    private var lowResAnalysis = false
    private var handsHalfRate = false
    private var hintTargetMs = 0.0
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        EventChannel(messenger,"calm/detection/events").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink) { sink = events }
            override fun onCancel(arguments: Any?) { panel?.stop(); sink = null }
        })
        flutterEngine.platformViewsController.registry.registerViewFactory("calm/camera",object : PlatformViewFactory(StandardMessageCodec.INSTANCE) {
            override fun create(context: Context, viewId: Int, args: Any?): PlatformView {
                panel?.stop()
                return CameraPanel(this@MainActivity) { sink?.success(it) }.also { panel = it; it.diagnosticsEnabled = diagnostics; it.modelFolder = modelFolder; it.requestedDelegate = trackingDelegate; it.lockFps30 = lockFps30; it.hidePreview = hidePreview; it.lowResAnalysis = lowResAnalysis; it.handsHalfRate = handsHalfRate; it.hintTargetMs = hintTargetMs }
            }
        })
        MethodChannel(messenger,"calm/detection").setMethodCallHandler { call,result ->
            when (call.method) {
                "start" -> if (panel == null) result.error("camera_not_ready","Wait for the preview to open",null) else { panel!!.start(); result.success(null) }
                "stop" -> { panel?.stop(); result.success(null) }
                "models" -> { modelFolder = when (call.arguments) { "v4" -> "behavior"; "v6" -> "behavior_v6"; else -> "behavior_v7" }; panel?.modelFolder = modelFolder; result.success(null) }
                // "CPU", "GPU", or a comma list of tasks for GPU ("face,pose,hands").
                "delegate" -> { trackingDelegate = (call.arguments as? String)?.ifBlank { "CPU" } ?: "CPU"; panel?.requestedDelegate = trackingDelegate; result.success(null) }
                "fpsLock" -> { lockFps30 = call.arguments == true; panel?.lockFps30 = lockFps30; result.success(null) }
                "lowResAnalysis" -> { lowResAnalysis = call.arguments == true; panel?.lowResAnalysis = lowResAnalysis; result.success(null) }
                "handsHalfRate" -> { handsHalfRate = call.arguments == true; panel?.handsHalfRate = handsHalfRate; result.success(null) }
                "hintTarget" -> { hintTargetMs = (call.arguments as? Number)?.toDouble() ?: 0.0; panel?.hintTargetMs = hintTargetMs; result.success(null) }
                "hidePreview" -> { hidePreview = call.arguments == true; panel?.hidePreview = hidePreview; result.success(null) }
                "saveText" -> try {
                    val a = call.arguments as Map<*,*>
                    result.success(saveText(a["name"] as String, a["text"] as String))
                } catch (e: Exception) { result.error("save_failed",e.message,null) }
                "diagnostics" -> { diagnostics = call.arguments == true; panel?.diagnosticsEnabled = diagnostics; result.success(null) }
                else -> result.notImplemented()
            }
        }
    }
    /** Writes a text file the user can open: Download/Calm Kaba (Android 10+), else the app's external files folder. */
    private fun saveText(name: String, text: String): String {
        if (Build.VERSION.SDK_INT >= 29) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME,name); put(MediaStore.Downloads.MIME_TYPE,"text/plain")
                put(MediaStore.Downloads.RELATIVE_PATH,"Download/Calm Kaba")
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI,values) ?: error("Could not create $name")
            contentResolver.openOutputStream(uri)!!.use { it.write(text.toByteArray()) }
            return "Download/Calm Kaba/$name"
        }
        return File(getExternalFilesDir(null),name).apply { writeText(text) }.path
    }
    fun detachPanel(value: CameraPanel) { if (panel === value) panel = null }
    fun requestCameraPermission() { requestPermissions(arrayOf(android.Manifest.permission.CAMERA),701) }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode,permissions,grantResults)
        if (requestCode == 701) {
            // Restart requires a fresh user action, so a late permission response
            // cannot restart a scan after the page has been left.
            val granted = grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED
            sink?.success(mapOf("type" to "stopped", "message" to if (granted) "Camera allowed. Tap Scan Now to begin." else "Camera permission denied. Enable it in Android Settings, then retry."))
        }
    }
    override fun onPause() { panel?.stop(); sink?.success(mapOf("type" to "stopped", "message" to "Scan paused. Tap Scan Now to restart.")); super.onPause() }
    override fun onConfigurationChanged(newConfig: Configuration) {
        panel?.stop(); sink?.success(mapOf("type" to "stopped", "message" to "Orientation changed. Tap Scan Now to restart."))
        super.onConfigurationChanged(newConfig)
    }
}
