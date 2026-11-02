"""Export the lead's 2026-11-02 personal_weighted LightGBM models (train/02-11-26/light2.py) for the app.

Each bundle uses the v6 features (1,122, unchanged order) followed by robust z-scores of a
symptom-specific subset against the user's calibration windows, plus personal_baseline_available.
Writes the unchanged trees and a manifest to android/app/src/main/assets/behavior_v7/, and a
parity fixture (android/app/src/test/resources/personal_v7.json) computed with light2.py's own
robust_z and the joblib models, so the Kotlin calibration path can be checked without the dataset.

  python -I tools/export_v7_models.py --lead <anxiety_detection_prototype checkout>
"""
import argparse, hashlib, importlib.util, json, subprocess, sys, warnings
from pathlib import Path

import joblib
import numpy as np

APP = Path(__file__).resolve().parents[1]
OUT = APP / "android/app/src/main/assets/behavior_v7"
V6 = APP / "android/app/src/main/assets/behavior_v6/manifest.json"
FIXTURE = APP / "android/app/src/test/resources/personal_v7.json"
SYMPTOMS = ["fidgeting", "hand_tremor", "knee_tremor", "avoidant_gaze", "facial_tension", "heavy_breathing", "fear"]
FLAGS = ["f_face_detected_rate", "f_pose_detected_rate", "f_left_hand_detected_rate", "f_right_hand_detected_rate"]


def light2(lead):
    """Import light2.py unchanged (it star-imports common.py from its sibling folder)."""
    sys.path.insert(0, str(lead / "train/27-10-26 train"))
    spec = importlib.util.spec_from_file_location("light2", lead / "train/02-11-26/light2.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def fixture(bundles, names, l2):
    """Five calibration windows (as in training) and four targets.

    Hands are lost in three calibration windows, so hand symptoms keep only two references and
    get no baseline; the pose is lost in one, so pose symptoms use four of the five windows.
    """
    rng = np.random.default_rng(7)
    track = [names.index(f) for f in FLAGS]
    cal = l2.MAX_CALIBRATION_WINDOWS
    X = rng.normal(0, 1, (cal + 4, len(names))).astype(np.float32) * rng.choice([.01, 1, 30], len(names)).astype(np.float32)
    X[rng.random(X.shape) < .05] = np.nan
    X[:, track] = 1.0
    X[1:4, track[2:]] = 0.0
    X[2, track[1]] = 0.5
    X[cal + 2, :] = X[0] + np.float32(.001)  # a target close to the baseline
    cases = []
    for s in SYMPTOMS:
        b = bundles[s]
        ids = np.asarray(b["personal_feature_indices"])
        t = X[:, track]
        good = {"facial_tension": t[:, 0], "fear": t[:, 0], "avoidant_gaze": t[:, 0], "heavy_breathing": t[:, 1],
                "knee_tremor": t[:, 1]}.get(s, np.maximum(t[:, 2], t[:, 3])) >= l2.TRACK_LIMIT
        ref = X[:cal][good[:cal]][:, ids]
        if len(ref) >= l2.MIN_CALIBRATION_WINDOWS:
            z, available = l2.robust_z(X[cal:, ids], ref), 1.0
        else:
            z, available = np.full((4, len(ids)), np.nan, np.float32), 0.0
        rows = np.column_stack([X[cal:], z, np.full(4, available, np.float32)]).astype(np.float32)
        cases.append(dict(symptom=s, available=available, probabilities=b["model"].predict_proba(rows)[:, 1].tolist(),
                          z=[[None if not np.isfinite(v) else float(v) for v in r] for r in z]))
    clean = lambda r: [None if not np.isfinite(v) else float(v) for v in r]
    return dict(calibration=[clean(r) for r in X[:cal]], targets=[clean(r) for r in X[cal:]], cases=cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=Path, required=True)
    a = ap.parse_args()
    warnings.filterwarnings("ignore")
    l2 = light2(a.lead)
    v6 = json.loads(V6.read_text())
    names = v6["models"][0]["features"]
    commit = subprocess.run(["git", "-C", str(a.lead), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    bundles, models = {}, []
    for s in SYMPTOMS:
        src = a.lead / f"models/02-11-26/{s}/model.joblib"
        b = bundles[s] = joblib.load(src)
        ids = [int(i) for i in b["personal_feature_indices"]]
        assert b["symptom"] == s and b["feature_set"] == "both" and b["uses_personal_control_baseline"]
        assert b["features"] == names + [f"personal_z_{names[i]}" for i in ids] + ["personal_baseline_available"]
        assert b["smoothing_windows"] == l2.SMOOTH_K == 3 and b["min_calibration_windows"] == l2.MIN_CALIBRATION_WINDOWS == 3
        assert ids == l2.personal_columns(names, s).tolist(), "personal subset differs from light2.personal_columns"
        text = b["model"].booster_.model_to_string()
        (OUT / f"{s}.txt").write_bytes(text.encode("utf-8"))  # LF only; LightGBM rejects CRLF
        models.append(dict(symptom=s, file=f"{s}.txt", threshold=float(b["threshold"]), features=b["features"],
                           personal_indices=ids, model_sha256=hashlib.sha256(text.encode()).hexdigest(),
                           source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(), trees=b["model"].booster_.num_trees()))
    manifest = dict(run_id="lead_2026-11-02_lgbm_personal_weighted", feature_version="v6", mode="vs_control",
                    source_repo="Vinzy0/anxiety_detection_prototype", source_commit=commit, window=v6["window"],
                    bs_names=v6["bs_names"], fm_names=v6["fm_names"], personal=dict(
                        min_calibration_windows=l2.MIN_CALIBRATION_WINDOWS,
                        max_calibration_windows=l2.MAX_CALIBRATION_WINDOWS, track_limit=l2.TRACK_LIMIT,
                        smoothing_windows=l2.SMOOTH_K,
                        method="light2.robust_z: median/MAD (std fallback) z-score clipped to [-8, 8]"),
                    models=models, note="Trees unchanged from light2.py's personal_weighted joblib bundles (51 participants).")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    FIXTURE.write_text(json.dumps(fixture(bundles, names, l2)))
    print(json.dumps([{k: m[k] for k in ("symptom", "threshold", "trees")} | {"personal": len(m["personal_indices"])} for m in models]))


if __name__ == "__main__":
    main()
