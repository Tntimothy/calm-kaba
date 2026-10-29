# Performance sprint - 1.2.0+7

Branch `codex/live-scores-and-tuning`, from `dd106c6` (1.1.3+6). Goal: sustained 30 processed
landmark fps on the Xiaomi 14T and Infinix Note 12 with reliable face, pose and hand tracking.

**Status: emulator results only. No phone was connected, so phone acceptance is outstanding.**
Nothing here shows 30 fps, and nothing reaches it by skipping tracker frames, reusing results or
counting preview smoothness. The models, landmark schema, normalization, features and window
behavior are unchanged. The only intentional input change is the opt-in 480x360 experiment.

## Changes

| # | Commit | Change | Default |
| --- | --- | --- | --- |
| 1 | `7b4f1ca` | Separate GPU switches for face, pose and hands. They reuse the comma-separated tracker list and per-task CPU fallback, apply on the next scan, and DELEGATE shows what runs plus any fallback reason. The device GPU-vs-CPU test now also compares hands. | all CPU |
| 2 | `2558bc9` | Tracking packets rebuild only the progress bar and the Face/Body line. Native tracking packets go out at about 5 Hz; state changes and interruptions are sent at once. Landmarks, predictions and error/stop events are never throttled. | on |
| 3 | `76e9f2b`, `c1056f8` | Hide-preview A/B switch. Preview frames go to a discarded `ImageReader`, and the PreviewView and overlay become INVISIBLE (still laid out, so the ViewPort crop is unchanged). Use cases, stream sizes, rotation and tracker inputs are unchanged. | off |
| 4 | `89463c5`, `86b57d9` | 480x360 ImageAnalysis experiment; the preview keeps 640x480. If 480x360 is missing, CameraX takes the next *larger* size. INPUT shows `analysisRequested`, actual `analysis`, `crop` and upright `modelInput`. | off |
| 5 | `0079c1a` | `setOutputImageRotationEnabled(true)`: CameraX delivers upright RGBA, and an upright uncropped frame goes to the trackers without a copy. Cropped frames still use the old filtered `createBitmap`. Accepted on both gates (below); the Canvas fallback was not needed. | on |
| - | `89b4895` | Benchmark harness records every sample after warmup, giving whole-run means and p95s (percentiles are never averaged) and sustained fps = frames / elapsed time. | test only |
| 6 | `d6c5921`, `eb78fe0` | ADPF: one `PerformanceHintManager` session (API 31+) for the analysis, face, pose and hands threads, using native TIDs. Opened at scan start, closed at stop. Targets Off / 50 / 33.3 ms. Reports one work cycle per processed frame. PHONE shows `adpf=`. | Off |
| 7 | `baa67ec` | Pipelining: copy pixels + metadata into one of 3 owned buffers and close the ImageProxy at once. Frame N+1 is prepared while the tracking thread runs frame N, with a latest-frame hand-off (an older pending frame is replaced, nothing queues). A results thread does adaptation, window, events and predictions in capture order. | on |
| - | `538d746` | Version 1.2.0+7. | |

### Step 5 correctness gate

`CameraXRotationParityTest` runs CameraX's own YUV->RGBA conversion and its own crop-rect
mapping (`ImageAnalysisAbstractAnalyzer.getUpdatedCropRect`) on synthetic semi-planar frames:
640x480 and 480x360, Y/UV rows padded by 0, 32 or 64 bytes, full, even and odd non-full crops,
at 0/90/180/270 degrees. It requires the result to be pixel-identical to the old
`createBitmap(crop, rotate, filter=true)`. **36/36 cases matched** (`camerax_rotation_parity.json`).
Front-camera inputs stay unmirrored, as before; the overlay still mirrors. On the emulator the
rotated RGBA rows were packed (rowStride = width x 4), so bitmap reuse stays on. A phone that pads
these rows falls back to CameraX `toBitmap()`; check `rowStride` and `bitmapReuse` in INPUT.
MediaPipe `ImageProcessingOptions` rotation was not revisited.

### Step 7 ownership and lifecycle

* The ImageProxy is closed inside `prepare` right after the copy. Nothing reads it afterwards.
* A buffer returns to the pool only after all three tracker futures finish, including when a
  tracker fails (`awaitTrackerReaders`). A replaced pending frame is freed without being tracked.
* Timestamps for `detectForVideo` are assigned on the single tracking thread. Window updates and
  predictions run on the single results thread in capture order. Predictions use the copied
  window snapshot, so window boundaries and prediction timestamps are unchanged.
* Stop: generation++, drop the pending frame, wait for in-flight tracking and queued results,
  then close trackers, buffers and models. Dispose shuts the threads down after that.

## Emulator measurements

Android 15 Pixel 6a-profile x86_64 AVD, 4 vCPUs, host GPU, back emulated camera (scene has no
person, so tracking rates are 0% and the face/pose/hand stages measure detection only). Native
`CameraPanel` harness (`CameraPerformanceTest`, debug build), 30 fps lock on, CPU delegates, v4
models, 10 s warmup, then 30 s per run. Values are whole-run **mean / p95 latency in ms** (lower
is better). Processed fps = processed frames / elapsed time.

| Run | Processed fps | Capture fps | YUV/RGBA→bitmap | Crop+rotate | Face | Pose | Hands | Trackers wall | Total* | Capture→result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline run 1 | 18.5 | 29.4 | 0.3 / 0.7 | 4.6 / 7.5 | 10.3 / 16.3 | 34.3 / 41.4 | 45.2 / 52.9 | 45.7 / 53.4 | 52.0 / 61.1 | 38.3 / 56.7 |
| Baseline run 2 | 18.7 | 29.4 | 0.3 / 0.7 | 4.5 / 7.5 | 10.2 / 15.8 | 34.6 / 41.6 | 44.7 / 51.8 | 45.2 / 52.3 | 51.4 / 60.1 | 38.9 / 57.0 |
| CameraX rotation run 1 | 21.1 | 29.4 | 0.4 / 0.7 | 0.0 / 0.1 | 8.9 / 14.5 | 33.6 / 40.8 | 43.2 / 50.4 | 43.8 / 50.9 | 45.4 / 52.9 | 31.9 / 49.3 |
| CameraX rotation run 2 | 20.3 | 29.4 | 0.4 / 0.7 | 0.0 / 0.1 | 9.6 / 16.0 | 34.6 / 43.1 | 45.1 / 53.6 | 45.7 / 53.9 | 47.4 / 57.2 | 35.7 / 53.7 |
| Before pipelining run 1 | 18.5 | 29.4 | 0.4 / 0.6 | 0.0 / 0.1 | 11.0 / 17.7 | 38.6 / 48.9 | 49.6 / 60.3 | 50.2 / 61.1 | 51.9 / 64.6 | 39.3 / 59.1 |
| Before pipelining run 2 | 18.9 | 29.4 | 0.4 / 0.7 | 0.0 / 0.1 | 10.5 / 16.8 | 37.2 / 46.2 | 48.4 / 58.1 | 49.0 / 58.9 | 50.9 / 62.3 | 38.0 / 59.0 |
| Pipelined run 1 | 19.1 | 29.4 | 0.9 / 2.1 | 0.1 / 0.1 | 11.4 / 18.9 | 40.4 / 52.5 | 51.3 / 62.6 | 52.2 / 63.5 | 70.2 / 90.7 | 42.7 / 63.5 |
| Pipelined run 2 | 19.6 | 29.4 | 0.9 / 2.0 | 0.0 / 0.1 | 11.7 / 19.6 | 38.9 / 49.2 | 50.1 / 59.2 | 50.8 / 60.2 | 69.3 / 88.2 | 41.6 / 62.0 |
| Hide preview (+rotation) | 23.6 | 29.5 | 0.3 / 0.4 | 0.1 / 0.1 | 8.1 / 12.6 | 28.1 / 36.5 | 38.9 / 49.8 | 39.4 / 50.2 | 40.8 / 52.6 | 26.7 / 48.5 |
| ADPF 33.3 ms (+rotation) | 18.2 | 29.4 | 0.4 / 0.8 | 0.0 / 0.1 | 11.3 / 19.1 | 39.3 / 49.0 | 50.4 / 61.1 | 51.0 / 61.7 | 52.8 / 63.9 | 39.7 / 60.7 |

\* Before pipelining, "total" is the serial analyzer time. After pipelining it runs from frame
arrival to result and includes waiting for the trackers, so it is now labelled
"arrival → result" and is not comparable across that change.

How to read this:

* **Only interleaved pairs are fair comparisons.** "CameraX rotation" and "Before pipelining" run
  the same code path, yet the later pair was about 10% slower: the emulator drifted during the
  session. Baseline vs CameraX rotation ran interleaved (off, on, off, on); before vs after
  pipelining ran interleaved with reinstalls between runs.
* **CameraX rotation:** +11% processed fps in its interleaved pair. Crop+rotate fell from 4.6 to
  0.05 ms. The upstream rotation adds about 0.5–0.8 ms to capture→analyzer.
* **Pipelining:** +3–4% processed fps in its pair. About 10 prepared frames/s are replaced
  (CameraX now converts every frame), and on 4 vCPUs that work slows the trackers by about 2 ms.
  Phones have more cores, so they should lose less to this.
* **Capture→result on the emulator is not trustworthy.** Its camera stamps frames 15–25 ms *ahead*
  of the clock. Once pipelining delivers frames without queueing, every capture→analyzer age is
  negative and discarded, and capture→result is understated by the same offset. Judge the latency
  effect of pipelining on a phone (REALTIME clock), not here.
* **ADPF:** the session opens (`active target=33.3ms threads=4`). The emulator has no real
  CPU-frequency control, so its effect is unknown until tested on a phone.
* **GPU:** on the emulator all three GPU tasks are created but fail at inference
  (`GL_INVALID_ENUM`), and each switches to CPU on its own thread. GPU speed and CPU/GPU numerical
  agreement (`gpuDelegateMatchesCpuOnReferenceImage`, now including hands) need a phone.
* **480x360:** the emulator camera has no 480x360 stream, so it now falls back to 640x480 and
  INPUT reports that. Tracking quality at 480x360 has not been tested.

### Ten-minute sustained run

Final build, default settings, 10 s warmup, then 600 s, same harness and emulator (host GPU):

| Run | Processed fps | Capture fps | YUV/RGBA→bitmap | Crop+rotate | Face | Pose | Hands | Trackers wall | Total* | Capture→result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Final, 10 min | 20.3 | 29.4 | 0.8 / 1.6 | 0.0 / 0.1 | 10.8 / 18.2 | 36.1 / 45.9 | 48.4 / 59.2 | 49.1 / 59.9 | 67.3 / 87.4 | 39.1 / 60.0 |

The 2-second rolling processed rate was 20.2 fps at 1 minute, 20.5 at 5 and 19.4 at 10. Heat
stayed `none` throughout, but the emulator gives no information about phone heating. About 9–10
prepared frames/s were replaced. The 20 fps milestone held on the emulator; 30 fps was not reached.

### Actual release app (x86_64, 1.2.0)

With the host GPU, every release build crashed at startup in Flutter's raster thread
(`SIGSEGV`, `libflutter.so`). That included the previous 1.1.2 release, which had run on this
AVD before, so this is the emulator's GPU path in this session, not the sprint. With
`-gpu swiftshader_indirect` the release app ran:

* Continuous Scan with Developer diagnostics: no crashes, `pipelined=true`, `cameraXRotation=true`,
  `adpf=active target=33.3ms threads=4`, all delegates CPU.
* Software rendering makes the visible preview CPU-bound: 2.3 processed fps, trackers 243 ms.
  With **Hide preview** on, the same app processed 21.0 fps, and no stale frame was shown.
  This proves the switch isolates rendering cost; the size of the gap is an artifact of
  software rendering.
* Home, then return: the scan stopped and showed Scan Now. The next scan ran at 20.0 fps with
  ADPF re-armed. No `CalmCamera` failures and no "buffers still in use".

## Validation

* `flutter analyze`: no issues. `flutter test`: 5/5 passed.
* `app:testDebugUnitTest`: 8 ran, 4 passed (FramePipelineTest x2, TrackerReadersTest,
  `windowsResetAndTrackingDoesNotBecomeZeroScore`). 4 failed because the ignored research fixtures are absent, the
  same as before the sprint: `adapter_pixel.json`, `windows.json`, `windows_v6.json`,
  `predictions.json`. They were not fabricated or skipped.
* Emulator instrumentation, all passed: CameraXRotationParityTest, RgbaFrameBufferTest x2,
  TrackingThreadTest, WorkHintsTest, PipelineLifecycleTest (start/stop, restart with resized
  buffers, GPU inference failure falling back to CPU, all buffers idle after every stop, dispose).
* R8/ProGuard rules unchanged. Release arm64 APK: `versionName 1.2.0`, versionCode 2007,
  `arm64-v8a` native code only.

## Phone test (Xiaomi 14T, Infinix Note 12)

Install `calm-kaba-1.2.0+7-arm64-v8a-release.apk`. Use the same lighting, distance, orientation,
battery level and charging state for every run, with face, hands and knees in view. Choose
**Continuous Scan**, turn on **Developer diagnostics**, keep **Lock camera at 30 fps** on, and wait
10 s after each Scan Now before reading. Every switch applies on the **next** scan (Stop, then Scan Now).

1. **Default (all CPU, preview on, ADPF Off):** 2 minutes, then **Copy diagnostics**. Repeat once.
   Check INPUT: `cameraXRotation=true pipelined=true`, plus `bitmapReuse` and `rowStride`.
2. **GPU:** test all three on, then each tracker alone (face / pose / hands), 2 minutes each.
   DELEGATE must show GPU for the chosen trackers, and `fallback` must be empty. Watch the overlay
   for jitter or misplaced points.
3. **Hide preview:** on vs off, 2 minutes each. The difference is the rendering cost.
4. **ADPF:** 50 ms, then 33.3 ms, vs Off. PHONE should show `adpf=active ...`; anything else is the reason it is not active.
5. **480x360 (experiment):** INPUT must show `analysisRequested=480x360` and the size actually
   used. Compare the tracking % for face, hands and knees with run 1.
6. **Sustained:** the best configuration for 10 minutes. Copy diagnostics at 1, 5 and 10 minutes and note `heat`/`heatHeadroom`.
7. **Lifecycle:** Stop/Start, Home and return, rotate the phone, Instant scan, and Your Log.

For each run, record: processed fps, capture fps, the mean/p95 rows (YUV/RGBA→bitmap,
crop+rotate, face, pose, hands, trackers wall, arrival→result, capture→result), tracking %, and heat.
