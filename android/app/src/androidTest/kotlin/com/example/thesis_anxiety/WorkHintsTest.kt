package com.example.thesis_anxiety

import android.os.Build
import android.os.Process
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class WorkHintsTest {
    @Test fun opensWithNativeThreadIdsAndSurvivesConcurrentReportsAndClose() {
        val hints = WorkHints(InstrumentationRegistry.getInstrumentation().targetContext)
        val workers = List(3) { Executors.newSingleThreadExecutor() }
        try {
            val tids = intArrayOf(Process.myTid())+workers.map { it.submit<Int> { Process.myTid() }.get() }
            assertTrue("Native thread IDs, not Java IDs",tids.all { it > 0 } && tids.toSet().size == 4)
            hints.open(tids,0); assertEquals("off",hints.status)
            hints.open(tids,33_333_333)
            val status = hints.status
            if (Build.VERSION.SDK_INT < 31) assertTrue(status,status.startsWith("unsupported"))
            else assertTrue(status,status.startsWith("active") || status.startsWith("unavailable"))
            // Reports from every worker while the session closes must neither throw nor hang.
            val done = CountDownLatch(workers.size)
            workers.forEach { w -> w.execute { repeat(200) { hints.report(20_000_000) }; hints.report(0); done.countDown() } }
            hints.close()
            assertTrue(done.await(5,TimeUnit.SECONDS))
            assertFalse(hints.status,hints.status.startsWith("active"))
            hints.report(20_000_000); hints.close()
        } finally { workers.forEach { it.shutdown() } }
    }
}
