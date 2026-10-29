package com.example.thesis_anxiety

import android.content.Intent
import android.util.Log
import android.widget.FrameLayout
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Real CameraX frames and the same Developer diagnostics emitted by the app. */
class CameraPerformanceTest {
    @Test fun cameraDeveloperDiagnostics() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val activity = instrumentation.startActivitySync(Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        // A/B options: -e cameraXRotation false, -e hidePreview true, -e lowResAnalysis true, -e seconds 600.
        val args = InstrumentationRegistry.getArguments()
        val seconds = args.getString("seconds")?.toInt() ?: 30
        val packets = JSONArray()
        val done = CountDownLatch(10+seconds)
        var panel: CameraPanel? = null
        var recordingSince = 0L
        instrumentation.runOnMainSync {
            panel = CameraPanel(activity) { packet ->
                if (packet["type"] == "diagnostics" && packet["processedFps"] is Number && done.count > 0) {
                    if (done.count <= seconds) packets.put(JSONObject().apply { packet.forEach { (k, v) -> put(k, JSONObject.wrap(v)) } })
                    done.countDown()
                    // After 10 s of warmup, keep every stage sample for whole-run means and p95s.
                    if (done.count == seconds.toLong()) { panel!!.diag.stages.values.forEach { it.record(true) }; recordingSince = System.nanoTime() }
                }
                if (packet["type"] == "error") Log.e("CalmPerf", packet.toString())
            }.also {
                // Same 3:4 preview/analysis ViewPort as HomePage.
                val container = FrameLayout(activity)
                container.addView(it.view, FrameLayout.LayoutParams(-1, activity.resources.displayMetrics.widthPixels*4/3))
                activity.setContentView(container)
                it.diagnosticsEnabled = true
                it.cameraXRotation = args.getString("cameraXRotation") != "false"
                it.hidePreview = args.getString("hidePreview") == "true"
                it.lowResAnalysis = args.getString("lowResAnalysis") == "true"
                it.start()
            }
        }
        try {
            assertTrue("${10+seconds} diagnostics packets", done.await(seconds+120L, TimeUnit.SECONDS))
            val run = JSONObject()
            instrumentation.runOnMainSync {
                val elapsed = (System.nanoTime()-recordingSince)/1e9
                for ((name, series) in panel!!.diag.stages) {
                    val s = series.recorded().also { it.sort() }
                    if (s.isEmpty()) continue
                    fun ms(q: Double) = s[((s.size-1)*q).toInt()]/1e6
                    run.put(name, JSONObject(mapOf("mean" to s.average()/1e6, "p50" to ms(.5), "p95" to ms(.95), "n" to s.size)))
                }
                // One "total" sample per processed frame.
                run.put("sustainedProcessedFps", (run.optJSONObject("total")?.getInt("n") ?: 0)/elapsed).put("seconds", elapsed)
            }
            val report = JSONObject().put("packets", packets).put("warmupSeconds", 10).put("run", run)
            File(context.filesDir, "camera_performance.json").writeText(report.toString(2))
            Log.i("CalmPerf", "Collected ${packets.length()} Developer diagnostics packets")
        } finally {
            instrumentation.runOnMainSync { panel?.dispose(); activity.finish() }
        }
    }
}
