// WHAT THIS FILE DOES
// The 7 LightGBM yes/no models (one per symptom), loaded from assets/behavior_v7.
// Calibration: the first ~13 s (5 windows of 4 s) become YOUR baseline,
// and later windows are compared against it, like a doctor checking your
// resting heart rate first. Each scan calibrates fresh.

package com.example.thesis_anxiety

import org.json.JSONObject
import java.io.Closeable
import java.security.MessageDigest
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Seven independent classifiers from one bundle directory:
 *  behavior/    822-feature full models, symptom vs everything (2026-10-27 run)
 *  behavior_v6/ the lead's 1,122-feature LightGBM models, symptom vs control
 *  behavior_v7/ the lead's 2026-11-02 personal_weighted models (train/02-11-26/light2.py): the v6
 *               features plus robust z-scores against the user's own calibration windows
 */
class ModelBank(val folder: String = "behavior_v7", read: (String) -> ByteArray) : Closeable {
    /** [personal]: engine indices z-scored against the calibration windows (empty without calibration). */
    data class Model(val symptom: String, val threshold: Double, val handle: Long, val indices: IntArray, val personal: IntArray)
    val manifest = JSONObject(String(read("$folder/manifest.json"), Charsets.UTF_8))
    val runId: String = manifest.getString("run_id")
    val mode: String = manifest.getString("mode")
    val engine: FeatureEngine
    private val models = mutableListOf<Model>()
    private val minCalibration = manifest.optJSONObject("personal")?.getInt("min_calibration_windows") ?: 0
    private val maxCalibration = manifest.optJSONObject("personal")?.getInt("max_calibration_windows") ?: 0
    private val trackLimit = manifest.optJSONObject("personal")?.getDouble("track_limit") ?: 0.0
    init {
        val version = manifest.getString("feature_version")
        require(runId == "2026-10-27_51participants_full_vs_restricted" && version == "full" && mode == "vs_everything" ||
            runId == "lead_2026-10-27_lgbm_vs_control_both" && version == "v6" && mode == "vs_control" ||
            runId == "lead_2026-11-02_lgbm_personal_weighted" && version == "v6" && mode == "vs_control") { "Unknown model bundle $runId" }
        fun strings(key: String) = manifest.getJSONArray(key).let { a -> List(a.length()) { a.getString(it) } }
        engine = FeatureEngine(strings("bs_names"), strings("fm_names"), pooled = version == "v6")
        try {
            val list = manifest.getJSONArray("models")
            require(list.length() == 7)
            for (i in 0 until list.length()) {
                val m = list.getJSONObject(i)
                val schema = m.getJSONArray("features")
                val personal = m.optJSONArray("personal_indices")?.let { a -> IntArray(a.length()) { a.getInt(it) } } ?: IntArray(0)
                val base = schema.length()-if (personal.isEmpty()) 0 else personal.size+1
                val indices = IntArray(base) { j -> engine.names.indexOf(schema.getString(j)).also { require(it >= 0) } }
                require(indices.size == engine.names.size && indices.toSet().size == indices.size)
                // Model order: base features, personal_z_<base feature> for each personal index, then the availability flag.
                personal.forEachIndexed { k, p -> require(schema.getString(base+k) == "personal_z_${schema.getString(p)}") }
                require(personal.isEmpty() || schema.getString(schema.length()-1) == "personal_baseline_available" &&
                    minCalibration in 1..maxCalibration)
                val bytes = read("$folder/${m.getString("file")}")
                require(sha256(bytes) == m.getString("model_sha256")) { "Model checksum failed" }
                models.add(Model(m.getString("symptom"), m.getDouble("threshold"), NativeLgbm.load(String(bytes, Charsets.UTF_8)), indices,
                    IntArray(personal.size) { indices[personal[it]] }))
            }
        } catch (e: Throwable) { close(); throw e }
    }

    private val needsBaseline = models.any { it.personal.isNotEmpty() }
    /** Each scan (one bank) calibrates afresh: light2.py keeps one baseline per participant and camera angle. */
    private var calibrated = false
    private val calibration = mutableListOf<DoubleArray>()
    private var lastStart = Double.NaN
    /** Windows overlapping the calibration windows are skipped, as light2.py excludes them. */
    private var guardUntil = Double.NEGATIVE_INFINITY
    /** Per symptom: (median, scale) of its personal features, or null when too few calibration windows tracked it. */
    private var references = emptyMap<String, Pair<FloatArray, FloatArray>?>()
    val calibrating get() = needsBaseline && !calibrated

    /** Calibration windows: trim plus [maxCalibration] windows of 4 s, 2 s apart (13 s for five, as in training). */
    fun calibrationProgress(elapsed: Double): Double? =
        if (calibrating) (elapsed/(1+4+2*(maxCalibration-1))).coerceIn(0.0, 1.0) else null

    internal fun useBaseline(windows: List<DoubleArray>) {
        val flag = listOf("face", "pose", "left_hand", "right_hand").map { engine.names.indexOf("f_${it}_detected_rate") }
        fun rate(w: DoubleArray, k: Int) = w[flag[k]].takeIf { !it.isNaN() } ?: 0.0
        references = models.filter { it.personal.isNotEmpty() }.associate { m ->
            // light2.quality_masks: the tracker each symptom depends on must cover 60% of the window.
            val good = windows.filter { w ->
                when (m.symptom) {
                    "facial_tension", "fear", "avoidant_gaze" -> rate(w, 0)
                    "heavy_breathing", "knee_tremor" -> rate(w, 1)
                    else -> maxOf(rate(w, 2), rate(w, 3))
                } >= trackLimit
            }
            m.symptom to if (good.size < minCalibration) null else robustReference(good, m.personal)
        }
    }

    internal fun model(symptom: String) = models.first { it.symptom == symptom }

    /** Inputs in model order: base features, robust z-scores, then personal_baseline_available. */
    internal fun inputs(m: Model, features: DoubleArray): DoubleArray {
        val out = DoubleArray(m.indices.size+if (m.personal.isEmpty()) 0 else m.personal.size+1) { Double.NaN }
        for (i in m.indices.indices) out[i] = features[m.indices[i]]
        if (m.personal.isEmpty()) return out
        val ref = references[m.symptom]
        if (ref != null) for ((k, j) in m.personal.withIndex()) out[m.indices.size+k] = robustZ(features[j].toFloat(), ref.first[k], ref.second[k]).toDouble()
        out[out.lastIndex] = if (ref == null) 0.0 else 1.0
        return out
    }

    /** Developer view: the three inputs that pushed this detection up most (LightGBM contributions, log-odds). */
    private fun topReasons(m: Model, x: DoubleArray): List<String> {
        val c = NativeLgbm.contributions(m.handle,x)
        fun name(i: Int) = when {
            i < m.indices.size -> engine.names[m.indices[i]]
            i < m.indices.size+m.personal.size -> "vs calibration: ${engine.names[m.personal[i-m.indices.size]]}"
            else -> "calibration available"
        }
        return x.indices.sortedByDescending { c[it] }.take(3).map { String.format(java.util.Locale.US,"%s %+.2f",name(it),c[it]) }
    }

    fun predict(records: List<Observation>, start: Double, explain: Boolean = false, timing: (String, Long) -> Unit = { _, _ -> }): Map<String, Any> {
        val inside = records.filter { it.time >= start && it.time < start+4 }
        fun rate(frames: List<Observation>) = if (frames.size > 1) (frames.size-1)/(frames.last().time-frames.first().time) else 0.0
        val fps = rate(inside)
        val handFps = rate(inside.filter { it.handsRan })
        val maxGap = inside.zipWithNext { a,b -> b.time-a.time }.maxOrNull() ?: Double.POSITIVE_INFINITY
        val coverage = LandmarkAdapter.trackKeys.associateWith { key ->
            val n = inside.attempted(key); if (n == 0) 0.0 else inside.count { it.tracking[key] == true }.toDouble()/n }
        val begin = System.nanoTime()
        val features = engine.extract(records,start)
        require(features.none { it.isInfinite() })
        val extracted = System.nanoTime(); timing("features",extracted-begin)
        val calibratingNow = calibrating || start < guardUntil-1e-9
        if (calibrating) {
            // Calibration windows come from one uninterrupted stretch; a window reset starts over.
            if (!(abs(start-lastStart-2) <= .5)) calibration.clear()
            lastStart = start
            calibration.add(features)
            if (calibration.size >= maxCalibration) { useBaseline(calibration); calibrated = true; guardUntil = start+4 }
        }
        val results = models.map { model ->
            val reason = if (calibratingNow) CALIBRATING else if (maxGap > .5) "Tracking was interrupted; collect a fresh window" else trackingReason(model.symptom,coverage,fps)
            val warning = if (model.symptom in listOf("hand_tremor","knee_tremor") && handFps < 20 && reason.isEmpty()) "Low FPS (${String.format(java.util.Locale.US,"%.1f",handFps)}); tremor score may be unreliable" else ""
            val x = if (reason.isEmpty()) inputs(model,features) else null
            val score = x?.let { NativeLgbm.predict(model.handle,it) }
            mapOf("symptom" to model.symptom, "score" to score, "threshold" to model.threshold,
                "reason" to reason, "warning" to warning,
                "why" to if (explain && x != null && score!! >= model.threshold) topReasons(model,x) else emptyList<String>())
        }
        timing("classifiers",System.nanoTime()-extracted)
        return mapOf("results" to results, "fps" to fps, "coverage" to coverage, "runId" to runId, "mode" to mode,
            "calibrating" to calibratingNow,
            "featureCoverage" to features.count { it.isFinite() }.toDouble()/features.size, "start" to start, "end" to start+4)
    }
    override fun close() { models.forEach { NativeLgbm.free(it.handle) }; models.clear() }
    companion object {
        const val CALIBRATING = "Calibrating"
        fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        /**
         * light2.robust_z on float32 columns: median and 1.4826*MAD, falling back to the std when
         * MAD <= 1e-4 and to 1 when the scale is still <= 1e-3; z is clipped to [-8, 8].
         */
        internal fun robustReference(windows: List<DoubleArray>, columns: IntArray): Pair<FloatArray, FloatArray> {
            val med = FloatArray(columns.size); val scale = FloatArray(columns.size)
            for ((k, j) in columns.withIndex()) {
                val r = windows.map { it[j].toFloat() }.filter { !it.isNaN() }
                val m = median(r); med[k] = m
                val mad = 1.4826f*median(r.map { abs(it-m) })
                var s = if (mad.isFinite() && mad > 1e-4f) mad else std(r)
                if (!(s.isFinite() && s > 1e-3f)) s = 1f
                scale[k] = s
            }
            return med to scale
        }
        internal fun robustZ(v: Float, median: Float, scale: Float) = ((v-median)/scale).coerceIn(-8f, 8f)
        private fun median(r: List<Float>): Float {
            if (r.isEmpty()) return Float.NaN
            val s = r.sorted(); val n = s.size
            return if (n%2 == 1) s[n/2] else (s[n/2-1]+s[n/2])/2f
        }
        private fun std(r: List<Float>): Float {
            if (r.isEmpty()) return Float.NaN
            var total = 0f; for (v in r) total += v
            val mean = total/r.size
            var sq = 0f; for (v in r) sq += (v-mean)*(v-mean)
            return sqrt(sq/r.size)
        }
    }
}
