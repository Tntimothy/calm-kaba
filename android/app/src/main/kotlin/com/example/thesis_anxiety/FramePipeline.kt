// WHAT THIS FILE DOES
// Passes only the NEWEST camera frame to the tracker and drops older ones,
// so the app never falls behind or piles up frames.

package com.example.thesis_anxiety

/**
 * Bounded latest-frame hand-off from the preparing thread to the tracking thread.
 * Three buffers suffice: one being tracked, one pending and one being prepared. A newer
 * pending frame replaces (and frees) the older one, so nothing accumulates, and a buffer
 * is only reused after the tracking thread has released it.
 */
internal class FramePipeline<B : Any>(private val buffers: List<B>, private val onFree: (B) -> Unit = {}, private val onDrop: () -> Unit = {}) {
    private val free = ArrayDeque(buffers)
    private var pending: B? = null
    private var draining = false

    /** Preparing thread: a buffer nobody else reads or writes. */
    @Synchronized fun acquire(): B = checkNotNull(free.removeFirstOrNull()) { "All frame buffers are in use" }

    /** Preparing thread: true when the caller must schedule [take] on the tracking thread. */
    @Synchronized fun publish(buffer: B): Boolean {
        pending?.let { onDrop(); free(it) }
        pending = buffer
        if (draining) return false
        draining = true; return true
    }

    /** Tracking thread: the newest pending frame, or null when the drain should end. */
    @Synchronized fun take(): B? = pending.also { pending = null; if (it == null) draining = false }

    /** Return a buffer that was acquired or taken (after its readers have finished). */
    @Synchronized fun release(buffer: B) = free(buffer)

    /** Drop the pending frame; the tracked one is returned by its own [release]. */
    @Synchronized fun clear() { pending?.let { free(it) }; pending = null }

    @Synchronized fun idle() = pending == null && free.size == buffers.size

    private fun free(buffer: B) {
        check(buffer !in free) { "Frame buffer released twice" }
        onFree(buffer); free.addLast(buffer)
    }
}
