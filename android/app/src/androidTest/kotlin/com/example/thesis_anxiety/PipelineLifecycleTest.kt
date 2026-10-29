package com.example.thesis_anxiety

import android.content.Intent
import android.os.SystemClock
import android.widget.FrameLayout
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/** Real camera: stop/start, a buffer resize, tracker fallback and disposal with the pipelined frames. */
class PipelineLifecycleTest {
    @Test fun restartsResizesFallsBackAndDisposesCleanly() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val activity = instrumentation.startActivitySync(Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        val packets = CopyOnWriteArrayList<Map<String, Any?>>()
        lateinit var panel: CameraPanel
        instrumentation.runOnMainSync {
            panel = CameraPanel(activity) { packets.add(it) }
            val container = FrameLayout(activity)
            container.addView(panel.view, FrameLayout.LayoutParams(-1, activity.resources.displayMetrics.widthPixels*4/3))
            activity.setContentView(container)
            panel.diagnosticsEnabled = true
        }
        fun scan(name: String, configure: CameraPanel.() -> Unit): Map<String, Any?> {
            packets.clear()
            instrumentation.runOnMainSync { panel.configure(); panel.start() }
            val deadline = SystemClock.elapsedRealtime()+60_000
            // Three diagnostics packets with processed frames: the scan really tracks.
            while (SystemClock.elapsedRealtime() < deadline && packets.count { it["type"] == "diagnostics" && ((it["processedFps"] as? Number)?.toDouble() ?: 0.0) > 0 } < 3) Thread.sleep(200)
            val last = packets.lastOrNull { it["type"] == "diagnostics" }
            assertTrue("$name: no error ${packets.filter { it["type"] == "error" }}",packets.none { it["type"] == "error" })
            assertNotNull("$name: diagnostics with processed frames",last)
            assertTrue("$name: processed frames",(last!!["processedFps"] as Number).toDouble() > 0)
            instrumentation.runOnMainSync { panel.stop() }
            val idleBy = SystemClock.elapsedRealtime()+10_000
            while (!panel.framesIdle && SystemClock.elapsedRealtime() < idleBy) Thread.sleep(50)
            assertTrue("$name: every frame buffer returned after stop",panel.framesIdle)
            return last
        }
        try {
            val first = scan("first") { cameraXRotation = true }
            val restarted = scan("restart, resized buffers") { cameraXRotation = false }
            assertNotEquals("Buffers were resized between scans",(first["input"] as Map<*, *>)["analysis"],(restarted["input"] as Map<*, *>)["analysis"])
            val gpu = scan("GPU request with CPU fallback") { cameraXRotation = true; requestedDelegate = "face,pose,hands" }
            val delegates = gpu["delegates"] as Map<*, *>
            for (task in listOf("face","pose","hands")) assertNotNull("$task delegate reported",delegates[task])
            scan("after fallback") { requestedDelegate = "CPU" }
        } finally {
            instrumentation.runOnMainSync { panel.dispose(); activity.finish() }
        }
    }
}
