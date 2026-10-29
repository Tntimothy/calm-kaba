package androidx.camera.core

import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.PixelFormat
import android.graphics.Rect
import android.media.Image
import androidx.camera.core.impl.ImageReaderProxy
import androidx.camera.core.impl.TagBundle
import androidx.test.platform.app.InstrumentationRegistry
import com.example.thesis_anxiety.RgbaFrameBuffer
import com.example.thesis_anxiety.uprightInput
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer

/**
 * ImageAnalysis.setOutputImageRotationEnabled(true) must give the trackers the same pixels as
 * the existing crop + rotate of the unrotated frame. This uses CameraX's own YUV -> RGBA
 * conversion and its own crop-rect mapping (package-private, hence this package).
 */
class CameraXRotationParityTest {
    private fun plane(buffer: ByteBuffer, rowStride: Int, pixelStride: Int) = object : ImageProxy.PlaneProxy {
        override fun getBuffer() = buffer
        override fun getRowStride() = rowStride
        override fun getPixelStride() = pixelStride
    }

    /** Semi-planar YUV_420_888 like phone cameras, with optional padding at the end of each row. */
    private fun yuv(w: Int, h: Int, pad: Int): ImageProxy {
        val stride = w+pad
        fun noise(i: Int, j: Int, salt: Int) = ((i*73856093) xor (j*19349663) xor salt).ushr(7).toByte()
        val y = ByteBuffer.allocateDirect(stride*h)
        for (j in 0 until h) for (i in 0 until w) y.put(j*stride+i,noise(i,j,0))
        val uv = ByteBuffer.allocateDirect(stride*(h/2))
        for (j in 0 until h/2) for (i in 0 until w/2) { uv.put(j*stride+2*i,noise(i,j,0x55)); uv.put(j*stride+2*i+1,noise(i,j,0x2aa)) }
        val v = uv.duplicate().apply { position(1) }.slice()
        val planes = arrayOf(plane(y,stride,1),plane(uv.duplicate(),stride,2),plane(v,stride,2))
        return object : ImageProxy {
            private var crop = Rect(0,0,w,h)
            override fun close() {}
            override fun getCropRect() = crop
            override fun setCropRect(rect: Rect?) { crop = rect ?: Rect(0,0,w,h) }
            override fun getFormat() = ImageFormat.YUV_420_888
            override fun getWidth() = w
            override fun getHeight() = h
            override fun getPlanes() = planes
            override fun getImageInfo(): ImageInfo = ImmutableImageInfo.create(TagBundle.emptyBundle(),0L,0,Matrix())
            @ExperimentalGetImage override fun getImage(): Image? = null
        }
    }

    private fun rgba(source: ImageProxy, rotation: Int): Pair<ImageProxy, ImageReaderProxy> {
        val swap = rotation%180 != 0
        val reader = ImageReaderProxys.createIsolatedReader(if (swap) source.height else source.width,
            if (swap) source.width else source.height,PixelFormat.RGBA_8888,2)
        val out = ImageProcessingUtil.convertYUVToRGB(source,reader,ByteBuffer.allocateDirect(source.width*source.height*4),rotation,false)
        return checkNotNull(out) { "CameraX conversion failed at $rotation" } to reader
    }

    private fun pixels(b: Bitmap) = IntArray(b.width*b.height).also { b.getPixels(it,0,b.width,0,0,b.width,b.height) }

    @Test fun cameraXRotationMatchesExistingCropAndRotate() {
        val cases = JSONArray(); var failures = 0
        for ((w,h,pad) in listOf(Triple(640,480,0),Triple(640,480,64),Triple(480,360,32)))
            for (crop in listOf(Rect(0,0,w,h),Rect(16,8,w-24,h-4),Rect(1,3,w-3,h-1)))
                for (rotation in listOf(0,90,180,270)) {
                    val source = yuv(w,h,pad)
                    val (base,baseReader) = rgba(source,0)
                    val (rotated,rotatedReader) = rgba(source,rotation)
                    val oldBuffer = RgbaFrameBuffer(); val newBuffer = RgbaFrameBuffer()
                    try {
                        // Existing app path: unrotated RGBA, then crop + rotate with filtering.
                        val original = oldBuffer.copy(base)
                        val expected = Bitmap.createBitmap(original,crop.left,crop.top,crop.width(),crop.height(),
                            Matrix().apply { postRotate(rotation.toFloat()) },true)
                        // CameraX path: rotated RGBA and the crop rect CameraX reports for it.
                        val mappedCrop = ImageAnalysisAbstractAnalyzer.getUpdatedCropRect(crop,
                            ImageAnalysisAbstractAnalyzer.getAdditionalTransformMatrixAppliedByProcessor(w,h,rotated.width,rotated.height,rotation))
                        val actual = uprightInput(newBuffer.copy(rotated),mappedCrop,0)
                        val same = expected.width == actual.width && expected.height == actual.height && pixels(expected).contentEquals(pixels(actual))
                        if (!same) failures++
                        cases.put(JSONObject(mapOf("size" to "${w}x$h","rowPadding" to pad,"crop" to crop.flattenToString(),
                            "rotation" to rotation,"mappedCrop" to mappedCrop.flattenToString(),"rotatedRowStride" to rotated.planes[0].rowStride,
                            "expected" to "${expected.width}x${expected.height}","actual" to "${actual.width}x${actual.height}","pixelExact" to same)))
                    } finally {
                        oldBuffer.close(); newBuffer.close()
                        base.close(); rotated.close(); baseReader.close(); rotatedReader.close()
                    }
                }
        val report = JSONObject().put("cases",cases).put("failures",failures)
        File(InstrumentationRegistry.getInstrumentation().targetContext.filesDir,"camerax_rotation_parity.json").writeText(report.toString(2))
        assertTrue(report.toString(),failures == 0)
    }
}
