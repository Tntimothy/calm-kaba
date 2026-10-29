package com.example.thesis_anxiety

import android.content.Context
import android.os.Build
import android.os.PerformanceHintManager
import android.util.Log
import java.util.Locale

/**
 * One ADPF hint session for the camera worker threads (API 31+). A scheduling hint only:
 * it asks for CPU capacity to meet the target, it does not guarantee a frame rate.
 * Every call is safe from any thread; without the service or a session it does nothing.
 */
internal class WorkHints(private val context: Context) {
    private var session: Any? = null
    @Volatile var status = "off"; private set

    /** [tids] are native Linux thread IDs (Process.myTid()), not Java Thread IDs. */
    @Synchronized fun open(tids: IntArray, targetNs: Long) {
        close()
        status = when {
            targetNs <= 0 -> "off"
            Build.VERSION.SDK_INT < 31 -> "unsupported (Android API ${Build.VERSION.SDK_INT})"
            else -> try {
                val manager = context.getSystemService(PerformanceHintManager::class.java)
                session = manager?.createHintSession(tids,targetNs)
                when {
                    manager == null -> "unavailable: no PerformanceHintManager"
                    session == null -> "unavailable: no session"
                    else -> "active target=${String.format(Locale.US,"%.1f",targetNs/1e6)}ms threads=${tids.size}"
                }
            } catch (e: Throwable) {
                Log.w("CalmCamera", "ADPF hint session unavailable", e)
                "failed: ${e.javaClass.simpleName}"
            }
        }
    }

    /** Elapsed work for one processed frame's work cycle. */
    @Synchronized fun report(ns: Long) {
        if (ns <= 0 || Build.VERSION.SDK_INT < 31) return
        val current = session as? PerformanceHintManager.Session ?: return
        try { current.reportActualWorkDuration(ns) } catch (e: Throwable) {
            Log.w("CalmCamera", "ADPF report failed; closing hint session", e)
            close(); status = "failed: ${e.javaClass.simpleName}"
        }
    }

    @Synchronized fun close() {
        if (Build.VERSION.SDK_INT >= 31) runCatching { (session as? PerformanceHintManager.Session)?.close() }
        session = null
        if (status.startsWith("active")) status = "closed"
    }
}
