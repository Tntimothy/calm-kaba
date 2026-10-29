package com.example.thesis_anxiety

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

class FramePipelineTest {
    private class Buffer(val name: String) { var seq = -1L; val readers = AtomicInteger(); val writers = AtomicInteger() }

    @Test fun newerFrameReplacesPendingAndBuffersAreNeverShared() {
        val freed = mutableListOf<String>(); var drops = 0
        val pipe = FramePipeline(listOf(Buffer("a"),Buffer("b"),Buffer("c")),{ freed.add(it.name) },{ drops++ })
        val first = pipe.acquire()
        assertTrue("First frame schedules a drain",pipe.publish(first))
        val tracked = pipe.take()!!
        assertSame(first,tracked)
        val second = pipe.acquire(); assertFalse("Drain still running",pipe.publish(second))
        val third = pipe.acquire()
        assertEquals(setOf("a","b","c"),setOf(tracked.name,second.name,third.name))
        assertThrows(IllegalStateException::class.java) { pipe.acquire() }
        assertFalse(pipe.publish(third))
        assertEquals("Replaced frame is freed, not queued",listOf(second.name),freed); assertEquals(1,drops)
        // The tracked buffer stays out of the free list until it is released.
        val reused = pipe.acquire(); assertSame(second,reused); pipe.release(reused)
        pipe.release(tracked)
        assertSame("Newest frame is tracked next",third,pipe.take())
        assertNull("Drain ends when nothing is pending",pipe.take())
        pipe.release(third)
        assertTrue(pipe.idle())
        assertTrue("Next frame schedules a new drain",pipe.publish(pipe.acquire()))
        pipe.clear(); assertTrue("Stop drops the pending frame",pipe.idle())
        assertThrows(IllegalStateException::class.java) { pipe.release(third) }
    }

    @Test fun concurrentPreparationAndTrackingKeepOrderAndOwnership() {
        val buffers = List(3) { Buffer("$it") }
        val tracked = AtomicInteger(); val dropped = AtomicInteger(); val lastSeq = AtomicLong(-1); val errors = AtomicInteger()
        val pipe = FramePipeline(buffers,onDrop = { dropped.incrementAndGet() })
        val tracker = Executors.newSingleThreadExecutor()
        fun drain() {
            while (true) {
                val b = pipe.take() ?: return
                if (b.writers.get() != 0) errors.incrementAndGet()
                b.readers.incrementAndGet()
                if (b.seq <= lastSeq.get()) errors.incrementAndGet()
                lastSeq.set(b.seq)
                Thread.sleep(0,200_000)
                if (b.writers.get() != 0) errors.incrementAndGet()
                b.readers.decrementAndGet()
                tracked.incrementAndGet()
                pipe.release(b)
            }
        }
        val frames = 5000L
        for (seq in 0 until frames) {
            val b = pipe.acquire()
            b.writers.incrementAndGet()
            if (b.readers.get() != 0) errors.incrementAndGet()
            b.seq = seq
            b.writers.decrementAndGet()
            if (pipe.publish(b)) tracker.execute { drain() }
        }
        tracker.shutdown(); assertTrue(tracker.awaitTermination(10,TimeUnit.SECONDS))
        assertEquals("No shared buffer or out-of-order frame",0,errors.get())
        assertEquals("Every frame is tracked or dropped",frames,(tracked.get()+dropped.get()).toLong())
        assertEquals("The newest frame is always tracked",frames-1,lastSeq.get())
        assertTrue(pipe.idle())
    }
}
