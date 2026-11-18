import os
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import argparse
import json
import math

import numpy as np
import pandas as pd
import tensorflow as tf

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

from common import *

TRACK_LIMIT = 0.60
MIN_CALIBRATION_WINDOWS = 3
MAX_CALIBRATION_WINDOWS = 5
MIN_EVALUATION_CONTROL_WINDOWS = 3
SMOOTH_K = 3
THRESHOLDS = np.arange(0.25, 0.751, 0.01)
PRIMARY_VARIANT = "smooth3_nested_threshold"
EPOCHS = 120
PATIENCE = 12
BATCH = 128
SYMPTOM_ORDER = [
    "hand_tremor",
    "knee_tremor",
    "heavy_breathing",
    "fidgeting",
    "avoidant_gaze",
    "facial_tension",
    "fear",
]
DISPLAY_NAMES = {
    "fidgeting": "Fidgeting",
    "hand_tremor": "Hand Tremor",
    "knee_tremor": "Knee Tremor",
    "avoidant_gaze": "Avoidant Gaze",
    "facial_tension": "Facial Tension",
    "heavy_breathing": "Heavy Breathing",
    "fear": "Fear",
}

def display_name(sym):
    return DISPLAY_NAMES.get(sym, sym.replace("_", " ").title())

def ordered_records(records):
    order = {sym: i for i, sym in enumerate(SYMPTOM_ORDER)}
    return sorted(records, key=lambda r: order.get(r["symptom"], 99))

def normalize_angle(value):
    return str(value).strip().casefold()

# Participant-Specific Control Calibration

def personal_columns(names, sym):
    selected = []
    for i, raw in enumerate(names):
        n = raw.lower()
        if "detected" in n or n.endswith("_rate"):
            continue
        if sym in ("facial_tension", "fear"):
            use = n.startswith(("f_face_", "p_face_")) or "bs_" in n
        elif sym == "avoidant_gaze":
            use = any(k in n for k in (
                "face_head_", "face_gaze_", "face_nose_",
                "p_face_head", "p_face_all"
            ))
        elif sym == "heavy_breathing":
            use = any(k in n for k in (
                "pose_shoulder", "pose_torso", "pose_11_",
                "pose_12_", "p_pose_torso", "p_pose_all"
            ))
        elif sym == "knee_tremor":
            use = any(k in n for k in (
                "pose_25_", "pose_26_", "pose_27_",
                "pose_28_", "pose_29_", "pose_30_",
                "pose_31_", "pose_32_", "pose_leg",
                "p_pose_leg"
            ))
        else:
            use = (
                n.startswith((
                    "f_left_hand_", "f_right_hand_",
                    "p_lh_", "p_rh_"
                ))
                or "left_hand_" in n
                or "right_hand_" in n
            )
        if use:
            selected.append(i)
    if not selected:
        raise ValueError(
            f"No personal-baseline features found for {sym!r}."
        )
    return np.asarray(selected, dtype=int)

# Robust Standardization

def robust_z(values, reference):
    med = np.nanmedian(reference, axis=0)
    mad = 1.4826 * np.nanmedian(np.abs(reference - med), axis=0)
    sd = np.nanstd(reference, axis=0)
    scale = np.where(
        np.isfinite(mad) & (mad > 1e-4),
        mad,
        sd,
    )
    scale = np.where(
        np.isfinite(scale) & (scale > 1e-3),
        scale,
        1.0,
    )
    return np.clip((values - med) / scale, -8.0, 8.0)

# Calibration Window Selection

def choose_calibration_segments(meta, seed):
    cls = (
        meta["symptom"].astype(str).str.strip().str.lower()
        .to_numpy()
    )
    pid = meta["participant_id"].astype(str).to_numpy()
    vid = meta["video_id"].astype(str).to_numpy()
    angle = np.asarray(
        [normalize_angle(x) for x in meta["angle"].astype(str)],
        dtype=object,
    )
    starts = pd.to_numeric(
        meta["win_start_s"], errors="coerce"
    ).to_numpy(float)
    calibration = np.zeros(len(meta), dtype=bool)
    guard = np.zeros(len(meta), dtype=bool)
    assignments = []
    rng = np.random.default_rng(seed)
    win_seconds = float(WINDOW["win_s"])
    for person in sorted(np.unique(pid)):
        person_rows = np.flatnonzero(pid == person)
        for key in sorted(set(angle[person_rows])):
            angle_rows = person_rows[angle[person_rows] == key]
            control_rows = angle_rows[cls[angle_rows] == "control"]
            candidates = []
            for video in sorted(np.unique(vid[control_rows])):
                rows = control_rows[vid[control_rows] == video]
                rows = rows[
                    np.argsort(starts[rows], kind="stable")
                ]
                video_starts = starts[rows]
                if len(rows) < (
                    MIN_CALIBRATION_WINDOWS
                    + 1
                    + MIN_EVALUATION_CONTROL_WINDOWS
                ):
                    continue
                chosen_count = None
                chosen_guard = None
                chosen_end = None
                max_count = min(MAX_CALIBRATION_WINDOWS, len(rows))
                for count in range(
                    max_count,
                    MIN_CALIBRATION_WINDOWS - 1,
                    -1,
                ):
                    calibration_end = (
                        video_starts[count - 1] + win_seconds
                    )
                    later = (
                        video_starts >= calibration_end - 1e-9
                    )
                    if int(later.sum()) >= MIN_EVALUATION_CONTROL_WINDOWS:
                        chosen_count = count
                        chosen_guard = rows[
                            (np.arange(len(rows)) >= count) & ~later
                        ]
                        chosen_end = calibration_end
                        break
                if chosen_count is None:
                    continue
                cal_rows = rows[:chosen_count]
                eval_rows = rows[
                    video_starts >= chosen_end - 1e-9
                ]
                candidates.append((
                    video,
                    cal_rows,
                    chosen_guard,
                    len(eval_rows),
                    chosen_end,
                    len(rows),
                ))
            if not candidates:
                assignments.append(dict(
                    participant_id=person,
                    calibration_angle=key.title(),
                    calibration_video_id="",
                    calibration_windows=0,
                    guard_windows=0,
                    control_videos=int(
                        len(np.unique(vid[control_rows]))
                    ) if len(control_rows) else 0,
                    calibration_candidates=0,
                    evaluation_control_windows=int(len(control_rows)),
                    calibration_start_s=np.nan,
                    calibration_end_s=np.nan,
                    baseline_available=False,
                ))
                continue
            longest = max(item[5] for item in candidates)
            finalists = [
                item for item in candidates if item[5] == longest
            ]
            (
                video,
                cal_rows,
                guard_rows,
                eval_in_video,
                cal_end,
                _,
            ) = finalists[int(rng.integers(0, len(finalists)))]
            calibration[cal_rows] = True
            guard[guard_rows] = True
            other_control_rows = control_rows[
                vid[control_rows] != video
            ]
            assignments.append(dict(
                participant_id=person,
                calibration_angle=key.title(),
                calibration_video_id=video,
                calibration_windows=int(len(cal_rows)),
                guard_windows=int(len(guard_rows)),
                control_videos=int(
                    len(np.unique(vid[control_rows]))
                ),
                calibration_candidates=int(len(candidates)),
                evaluation_control_windows=int(
                    len(other_control_rows) + eval_in_video
                ),
                calibration_start_s=float(starts[cal_rows[0]]),
                calibration_end_s=float(cal_end),
                baseline_available=True,
            ))
    return calibration, guard, pd.DataFrame(assignments)

# Tracking Quality

def quality_masks(D):
    t = np.nan_to_num(
        np.asarray(D["track"], dtype=np.float32),
        nan=0.0,
    )
    face, pose, left, right = t.T
    hands = np.maximum(left, right)
    return t, {
        "facial_tension": face >= TRACK_LIMIT,
        "fear": face >= TRACK_LIMIT,
        "avoidant_gaze": face >= TRACK_LIMIT,
        "heavy_breathing": pose >= TRACK_LIMIT,
        "knee_tremor": pose >= TRACK_LIMIT,
        "hand_tremor": hands >= TRACK_LIMIT,
        "fidgeting": hands >= TRACK_LIMIT,
    }

# Personal Feature Generation

def make_personal_matrix(D, sym, calibration_mask, excluded_mask):
    X = D["X"]
    meta = D["meta"]
    ids = personal_columns(D["names"], sym)
    cls = (
        meta["symptom"].astype(str).str.strip().str.lower()
        .to_numpy()
    )
    pid = meta["participant_id"].astype(str).to_numpy()
    angle = np.asarray(
        [normalize_angle(x) for x in meta["angle"].astype(str)],
        dtype=object,
    )
    z = np.full((len(X), len(ids)), np.nan, dtype=np.float32)
    available = np.zeros(len(X), dtype=np.float32)
    _, tracking_ok = quality_masks(D)
    good_reference = tracking_ok[sym]
    for person in np.unique(pid):
        person_rows = np.flatnonzero(pid == person)
        for angle_key in np.unique(angle[person_rows]):
            angle_rows = person_rows[
                angle[person_rows] == angle_key
            ]
            ref_rows = angle_rows[
                calibration_mask[angle_rows]
                & (cls[angle_rows] == "control")
            ]
            ref_rows = ref_rows[good_reference[ref_rows]]
            if len(ref_rows) < MIN_CALIBRATION_WINDOWS:
                continue
            target_rows = angle_rows[
                ~excluded_mask[angle_rows]
            ]
            if not len(target_rows):
                continue
            reference = X[ref_rows][:, ids]
            z[target_rows] = robust_z(
                X[target_rows][:, ids],
                reference,
            )
            available[target_rows] = 1.0
    names = [
        f"personal_z_{D['names'][i]}" for i in ids
    ] + ["personal_baseline_available"]
    return (
        np.column_stack([z, available]).astype(np.float32),
        names,
        ids,
    )

def subset_dataset(D, rows):
    out = dict(D)
    out["meta"] = (
        D["meta"].iloc[rows].reset_index(drop=True)
    )
    out["X"] = D["X"][rows]
    out["track"] = D["track"][rows]
    if "seq" in D:
        out["seq"] = D["seq"][rows]
    return out

def smooth_probabilities(meta, idx, probabilities, k=SMOOTH_K):
    probabilities = np.asarray(probabilities, dtype=float)
    if k <= 1 or len(idx) == 0:
        return probabilities.copy()
    frame = meta.iloc[idx][[
        "participant_id",
        "video_id",
        "win_start_s",
    ]].copy()
    frame["probability"] = probabilities
    frame["position"] = np.arange(len(frame))
    frame = frame.sort_values([
        "participant_id",
        "video_id",
        "win_start_s",
    ])
    frame["smoothed"] = frame.groupby(
        ["participant_id", "video_id"],
        sort=False,
    )["probability"].transform(
        lambda s: s.rolling(k, min_periods=1).mean()
    )
    output = np.empty(len(frame), dtype=float)
    output[frame["position"].to_numpy()] = (
        frame["smoothed"].to_numpy()
    )
    return output

def equal_prior_f1(y, probabilities, threshold):
    y = np.asarray(y, dtype=int)
    predicted = np.asarray(probabilities) >= float(threshold)
    tp = np.sum(predicted & (y == 1))
    fn = np.sum((~predicted) & (y == 1))
    fp = np.sum(predicted & (y == 0))
    tn = np.sum((~predicted) & (y == 0))
    tpr = tp / max(tp + fn, 1)
    fpr = fp / max(fp + tn, 1)
    return float(
        2.0 * tpr / max(1.0 + tpr + fpr, 1e-12)
    )

def choose_threshold(y, probabilities):
    if len(np.unique(y)) < 2:
        return 0.5, float("nan")
    scores = np.asarray([
        equal_prior_f1(y, probabilities, t)
        for t in THRESHOLDS
    ])
    best = np.flatnonzero(
        scores >= scores.max() - 1e-12
    )
    chosen = best[
        np.argmin(np.abs(THRESHOLDS[best] - 0.5))
    ]
    return float(THRESHOLDS[chosen]), float(scores[chosen])

def participant_video_weights(meta, rows, y):
    m = meta.iloc[rows]
    g = pd.DataFrame({
        "person": m["participant_id"].astype(str).to_numpy(),
        "video": m["video_id"].astype(str).to_numpy(),
        "label": np.asarray(y, dtype=int),
    })
    video_windows = g.groupby(
        ["person", "label", "video"]
    )["video"].transform("size").to_numpy()
    person_videos = g.groupby(
        ["person", "label"]
    )["video"].transform("nunique").to_numpy()
    weights = 1.0 / (video_windows * person_videos)
    return (weights / weights.mean()).astype(np.float32)

# Model Development and Comparison

class Prep:
    def __init__(self, D, X, rows, kind, personal_idx):
        self.D = D
        self.X = X
        self.kind = kind

        # MLP Input Preparation

        if kind == "mlp":
            self.cols = np.arange(X.shape[1])
        else:

            # CNN Input Preparation

            self.cols = np.asarray(
                D["extra_idx"] + D["ind_idx"],
                dtype=int,
            )
        self.missing_cols = np.asarray(
            sorted(set(D["ind_idx"] + personal_idx)),
            dtype=int,
        )
        train = X[rows][:, self.cols]
        self.mu = np.nan_to_num(
            np.nanmean(train, axis=0),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
        sd = np.nanstd(train, axis=0)
        self.sd = np.where(
            np.isfinite(sd) & (sd > 1e-6),
            sd,
            1.0,
        )
        if kind == "cnn":
            sequence = self.demean(D["seq"][np.sort(rows)])
            self.cm = np.nan_to_num(
                np.nanmean(sequence, axis=(0, 2))
            )
            cs = np.nanstd(sequence, axis=(0, 2))
            self.cs = np.where(
                np.isfinite(cs) & (cs > 1e-6),
                cs,
                1.0,
            )
    @staticmethod
    def demean(sequence):
        sequence = sequence.astype(np.float32)
        return sequence - np.nanmean(
            sequence,
            axis=2,
            keepdims=True,
        )
    def __call__(self, idx):
        X = self.X[idx]
        selected = X[:, self.cols]
        standardized = (selected - self.mu) / self.sd
        standardized = np.clip(
            np.nan_to_num(
                standardized,
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            ),
            -10,
            10,
        )
        missing = np.isnan(
            X[:, self.missing_cols]
        ).astype(np.float32)
        extra = np.hstack([
            standardized,
            missing,
        ]).astype(np.float32)
        if self.kind == "mlp":
            return extra
        sequence = self.demean(self.D["seq"][idx])
        sequence = np.clip(
            np.nan_to_num(
                (
                    sequence - self.cm[None, :, None]
                ) / self.cs[None, :, None],
                nan=0.0,
                posinf=0.0,
                neginf=0.0,
            ),
            -10,
            10,
        )
        return [
            sequence.transpose(0, 2, 1).astype(np.float32),
            extra,
        ]
    def state(self):
        state = {
            "mu": self.mu,
            "sd": self.sd,
            "cols": self.cols,
            "missing_cols": self.missing_cols,
        }
        if self.kind == "cnn":
            state.update({
                "cm": self.cm,
                "cs": self.cs,
            })
        return state

def build(kind, shapes, k=2):
    reg = tf.keras.regularizers.L2(1e-4)
    layers = tf.keras.layers

    # MLP Architecture

    if kind == "mlp":
        inputs = tf.keras.Input(shape=(shapes[0],))
        x = layers.Dropout(0.2)(inputs)
        x = layers.BatchNormalization()(
            layers.Dense(
                256,
                activation="relu",
                kernel_regularizer=reg,
            )(x)
        )
        x = layers.Dropout(0.4)(x)
        x = layers.Dropout(0.3)(
            layers.Dense(
                64,
                activation="relu",
                kernel_regularizer=reg,
            )(x)
        )
        return tf.keras.Model(
            inputs,
            layers.Dense(k, activation="softmax")(x),
        )

    # Temporal CNN Architecture

    sequence_input = tf.keras.Input(shape=shapes[0])
    extra_input = tf.keras.Input(shape=(shapes[1],))
    x = layers.GaussianNoise(0.05)(sequence_input)
    for filters in (48, 64, 96):
        x = layers.Conv1D(
            filters,
            5,
            padding="same",
            kernel_regularizer=reg,
        )(x)
        x = layers.MaxPool1D(2)(
            layers.SpatialDropout1D(0.2)(
                layers.ReLU()(
                    layers.BatchNormalization()(x)
                )
            )
        )
    extra = layers.Dense(
        64,
        activation="relu",
        kernel_regularizer=reg,
    )(
        layers.Dropout(0.2)(extra_input)
    )
    x = layers.Concatenate()([
        layers.GlobalAveragePooling1D()(x),
        layers.GlobalMaxPooling1D()(x),
        extra,
    ])
    x = layers.Dropout(0.3)(
        layers.Dense(
            64,
            activation="relu",
            kernel_regularizer=reg,
        )(
            layers.Dropout(0.4)(x)
        )
    )
    return tf.keras.Model(
        [sequence_input, extra_input],
        layers.Dense(k, activation="softmax")(x),
    )

def make_model(kind, inputs):
    if kind == "mlp":
        return build(kind, [inputs.shape[1]])
    return build(
        kind,
        [
            inputs[0].shape[1:],
            inputs[1].shape[1],
        ],
    )

# Model Training and Validation

class Tracker:
    def __init__(self, y, patience, verbose=False, tag=""):
        self.y = y
        self.patience = patience
        self.verbose = verbose
        self.tag = tag
        self.best = -1.0
        self.epoch = 0
        self.state = None
        self.wait = 0
    def step(self, epoch, probabilities, state_fn, extra=""):
        predicted = np.asarray(probabilities).argmax(axis=1)
        precision, recall, f1 = (
            float(x[1])
            for x in prf(self.y, predicted, 2)
        )
        if self.verbose:
            print(
                f"{self.tag}epoch {epoch:3d} {extra} | "
                f"val precision={precision:.3f} "
                f"recall={recall:.3f} F1={f1:.3f}",
                flush=True,
            )
        if f1 > self.best:
            self.best = f1
            self.epoch = epoch
            self.state = state_fn()
            self.wait = 0
        else:
            self.wait += 1
        return self.wait >= self.patience

def training_weights(D, rows, y, weighted):
    if weighted:
        return participant_video_weights(
            D["meta"],
            rows,
            y[rows],
        )
    return class_weights(y[rows], 2)[y[rows]]

def fit_nn(
    kind,
    D,
    X,
    sym,
    tr,
    va,
    sets,
    y,
    weighted,
    filter_tracking,
    personal_idx,
    verbose=False,
    tag="",
):
    _, masks = quality_masks(D)
    quality = masks[sym]
    inner_train = np.asarray(tr, dtype=int)
    inner_valid = np.asarray(va, dtype=int)
    fit_inner = (
        inner_train[quality[inner_train]]
        if filter_tracking
        else inner_train
    )
    if len(np.unique(y[fit_inner])) < 2:
        fit_inner = inner_train
    if len(inner_valid):
        prep = Prep(
            D, X, fit_inner, kind, personal_idx
        )
        Xtr = prep(fit_inner)
        Xva = prep(inner_valid)
        tf.keras.backend.clear_session()
        tf.keras.utils.set_random_seed(SEED)
        model = make_model(kind, Xtr)
        model.compile(
            optimizer=tf.keras.optimizers.Adam(1e-3),
            loss="sparse_categorical_crossentropy",
        )
        tracker = Tracker(
            y[inner_valid],
            PATIENCE,
            verbose,
            tag,
        )
        class Callback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                probabilities = self.model.predict(
                    Xva,
                    batch_size=512,
                    verbose=0,
                )
                loss = float((logs or {}).get("loss", 0.0))
                if tracker.step(
                    epoch + 1,
                    probabilities,
                    self.model.get_weights,
                    f"loss={loss:.3f}",
                ):
                    self.model.stop_training = True
        weights = training_weights(
            D, fit_inner, y, weighted
        )
        model.fit(
            Xtr,
            y[fit_inner],
            sample_weight=weights,
            batch_size=BATCH,
            epochs=EPOCHS,
            verbose=0,
            callbacks=[Callback()],
        )
        best_epoch = max(int(tracker.epoch), 1)
        p_valid = model.predict(
            Xva,
            batch_size=512,
            verbose=0,
        )
        p_valid_smooth = smooth_probabilities(
            D["meta"],
            inner_valid,
            p_valid[:, 1],
        )
        threshold_raw, score_raw = choose_threshold(
            y[inner_valid],
            p_valid[:, 1],
        )
        threshold_smooth, score_smooth = choose_threshold(
            y[inner_valid],
            p_valid_smooth,
        )
    else:
        best_epoch = 30
        threshold_raw = threshold_smooth = 0.5
        score_raw = score_smooth = float("nan")
    outer_train = np.r_[inner_train, inner_valid]
    fit_final = (
        outer_train[quality[outer_train]]
        if filter_tracking
        else outer_train
    )
    if len(np.unique(y[fit_final])) < 2:
        fit_final = outer_train
    final_prep = Prep(
        D,
        X,
        fit_final,
        kind,
        personal_idx,
    )
    Xfinal = final_prep(fit_final)
    tf.keras.backend.clear_session()
    tf.keras.utils.set_random_seed(SEED)
    final_model = make_model(kind, Xfinal)
    final_model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
    )
    final_weights = training_weights(
        D,
        fit_final,
        y,
        weighted,
    )
    final_model.fit(
        Xfinal,
        y[fit_final],
        sample_weight=final_weights,
        batch_size=BATCH,
        epochs=best_epoch,
        verbose=0,
    )
    probabilities = []
    for rows in sets:
        if len(rows):
            probabilities.append(
                final_model.predict(
                    final_prep(rows),
                    batch_size=512,
                    verbose=0,
                )
            )
        else:
            probabilities.append(
                np.zeros((0, 2), dtype=np.float32)
            )
    info = {
        "threshold_raw": threshold_raw,
        "threshold_smooth3": threshold_smooth,
        "inner_val_f1_50_50_raw": score_raw,
        "inner_val_f1_50_50_smooth3": score_smooth,
        "best_epoch": best_epoch,
        "tracking_filtered": int(
            len(outer_train) - len(fit_final)
        ),
    }
    return probabilities, info

# Five-fold Participant-Grouped Cross Validation

def run_cv(D, sym, fit_predict, tag=""):
    meta = D["meta"]
    cls = meta["symptom"].astype(str).to_numpy()
    pid = meta["participant_id"].astype(str).to_numpy()
    fm = fold_map(D["people"])
    fold_ids = np.asarray([fm[p] for p in pid], dtype=int)
    binary = (cls == sym) | (cls == "control")
    y = (cls == sym).astype(int)
    rng = np.random.default_rng(SEED)
    all_idx = []
    all_prob = []
    all_other_idx = []
    all_other_prob = []
    infos = []
    for fold in range(N_FOLDS):
        test = np.flatnonzero(binary & (fold_ids == fold))
        other = np.flatnonzero(~binary & (fold_ids == fold))
        training = np.flatnonzero(
            binary & (fold_ids != fold)
        )
        people = np.asarray(sorted(set(pid[training])))
        if len(people) > 1:
            count = max(
                1,
                int(round(0.2 * len(people))),
            )
            validation_people = set(
                rng.choice(
                    people,
                    count,
                    replace=False,
                ).tolist()
            )
            validation_mask = np.asarray([
                p in validation_people
                for p in pid[training]
            ])
            train = training[~validation_mask]
            validation = training[validation_mask]
        else:
            train = training
            validation = np.array([], dtype=int)
        if (
            len(np.unique(y[np.r_[train, validation]])) < 2
            or len(test) == 0
        ):
            prior = (
                np.bincount(
                    y[np.r_[train, validation]],
                    minlength=2,
                )
                / max(len(train) + len(validation), 1)
            )
            test_prob = np.tile(prior, (len(test), 1))
            other_prob = np.tile(prior, (len(other), 1))
            info = {
                "threshold_raw": 0.5,
                "threshold_smooth3": 0.5,
                "best_epoch": 1,
                "inner_val_f1_50_50_raw": float("nan"),
                "inner_val_f1_50_50_smooth3": float("nan"),
                "note": "Single-class fold; prior prediction used.",
            }
        else:
            outputs, info = fit_predict(
                train,
                validation,
                [test, other],
            )
            test_prob = outputs[0]
            other_prob = outputs[1]
        info["fold"] = fold
        p3 = smooth_probabilities(
            meta,
            test,
            test_prob[:, 1],
        )
        threshold = float(
            info.get("threshold_smooth3", 0.5)
        )
        all_idx.append(test)
        all_prob.append(test_prob[:, 1])
        all_other_idx.append(other)
        all_other_prob.append(other_prob[:, 1])
        infos.append(info)
    return {
        "idx": np.concatenate(all_idx),
        "prob": np.concatenate(all_prob),
        "oidx": np.concatenate(all_other_idx),
        "oprob": np.concatenate(all_other_prob),
        "infos": infos,
    }

# Model Evaluation

def metrics_at_threshold(y, probabilities, threshold):
    y = np.asarray(y, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    threshold = np.asarray(threshold, dtype=float)
    predicted = probabilities >= threshold
    tp = int(np.sum(predicted & (y == 1)))
    fp = int(np.sum(predicted & (y == 0)))
    tn = int(np.sum((~predicted) & (y == 0)))
    fn = int(np.sum((~predicted) & (y == 1)))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    specificity = tn / max(tn + fp, 1)
    f1 = (
        2 * precision * recall
        / max(precision + recall, 1e-12)
    )
    balanced_accuracy = (recall + specificity) / 2
    fpr = fp / max(fp + tn, 1)
    f1_50 = (
        2 * recall
        / max(1 + recall + fpr, 1e-12)
    )
    return dict(
        n=len(y),
        n_pos=int(y.sum()),
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        precision=precision,
        recall=recall,
        specificity=specificity,
        f1=f1,
        accuracy=(tp + tn) / max(len(y), 1),
        balanced_accuracy=balanced_accuracy,
        f1_50_50=f1_50,
        accuracy_50_50=balanced_accuracy,
    )

def fold_thresholds(D, res, key):
    fm = fold_map(D["people"])
    idx = res["idx"]
    pid = (
        D["meta"].iloc[idx]["participant_id"]
        .astype(str).to_numpy()
    )
    threshold_by_fold = {
        int(info["fold"]): float(info.get(key, 0.5))
        for info in res["infos"]
    }
    fold_ids = np.asarray([
        fm[p] for p in pid
    ], dtype=int)
    return np.asarray([
        threshold_by_fold[f]
        for f in fold_ids
    ], dtype=float)

def evaluate_variants(D, sym, res):
    idx = res["idx"]
    probabilities = res["prob"]
    cls = (
        D["meta"]["symptom"].astype(str).str.strip().str.lower()
        .to_numpy()
    )
    y = (cls[idx] == sym).astype(int)
    smoothed = smooth_probabilities(
        D["meta"],
        idx,
        probabilities,
    )
    raw_thresholds = fold_thresholds(
        D, res, "threshold_raw"
    )
    smooth_thresholds = fold_thresholds(
        D, res, "threshold_smooth3"
    )
    specs = [
        (
            "raw_fixed_050",
            probabilities,
            np.full(len(probabilities), 0.5),
        ),
        (
            "smooth3_fixed_050",
            smoothed,
            np.full(len(smoothed), 0.5),
        ),
        (
            "raw_nested_threshold",
            probabilities,
            raw_thresholds,
        ),
        (
            PRIMARY_VARIANT,
            smoothed,
            smooth_thresholds,
        ),
    ]
    rows = []
    for name, probs, thresholds in specs:
        metrics = metrics_at_threshold(
            y,
            probs,
            thresholds,
        )
        rows.append(dict(
            symptom=sym,
            variant=name,
            **metrics,
            threshold_median=float(np.median(thresholds)),
            threshold_min=float(np.min(thresholds)),
            threshold_max=float(np.max(thresholds)),
        ))
    return rows

def json_clean(value):
    if isinstance(value, dict):
        return {
            str(k): json_clean(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_clean(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value

# Evaluation Results and Reports

def export_symptom(D, sym, res, arm_dir):
    idx = res["idx"]
    raw = res["prob"]
    cls = (
        D["meta"]["symptom"].astype(str).str.strip().str.lower()
        .to_numpy()
    )
    y = (cls[idx] == sym).astype(int)
    smoothed = smooth_probabilities(
        D["meta"],
        idx,
        raw,
    )
    thresholds = fold_thresholds(
        D,
        res,
        "threshold_smooth3",
    )
    raw_thresholds = fold_thresholds(
        D,
        res,
        "threshold_raw",
    )
    deployment_threshold = float(np.median([
        info.get("threshold_smooth3", 0.5)
        for info in res["infos"]
    ]))
    fold_ids = np.asarray([
        fold_map(D["people"])[str(p)]
        for p in D["meta"].iloc[idx]["participant_id"]
    ], dtype=int)
    variants = evaluate_variants(D, sym, res)
    primary = next(
        row for row in variants
        if row["variant"] == PRIMARY_VARIANT
    )
    directory = arm_dir / sym
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "TN": primary["tn"],
        "FP": primary["fp"],
        "FN": primary["fn"],
        "TP": primary["tp"],
    }]).to_csv(
        directory / "confusion_matrix.csv",
        index=False,
    )
    predictions = D["meta"].iloc[idx][[
        "video_id",
        "participant_id",
        "angle",
        "win_start_s",
    ]].copy()
    predictions["true"] = y
    predictions["prob"] = raw
    predictions["prob_raw"] = raw
    predictions["prob_smooth3"] = smoothed
    predictions["fold"] = fold_ids
    predictions["threshold_raw"] = raw_thresholds
    predictions["threshold_smooth3"] = thresholds
    predictions["predicted"] = (
        smoothed >= thresholds
    ).astype(int)
    predictions.to_csv(
        directory / "oof_predictions.csv",
        index=False,
    )
    fold_info = [
        dict(info) for info in res["infos"]
    ]
    metrics_json = {
        **primary,
        "primary_variant": PRIMARY_VARIANT,
        "deployment_threshold": deployment_threshold,
        "variants": {
            row["variant"]: row
            for row in variants
        },
        "folds": fold_info,
    }
    (directory / "metrics.json").write_text(
        json.dumps(
            json_clean(metrics_json),
            indent=2,
        ),
        encoding="utf-8",
    )
    pd.DataFrame(variants).to_csv(
        directory / "evaluation_variants.csv",
        index=False,
    )
    record = {
        "symptom": sym,
        "y": y,
        "prob": smoothed,
        "raw_prob": raw,
        "thresholds": thresholds,
        "fold_thresholds": [
            float(info.get("threshold_smooth3", 0.5))
            for info in res["infos"]
        ],
        "deployment_threshold": deployment_threshold,
        "metrics": primary,
        "variants": variants,
    }
    return record

def curve_metrics(y, probabilities, threshold):
    predicted = probabilities >= threshold
    tp = np.sum(predicted & (y == 1))
    fp = np.sum(predicted & (y == 0))
    tn = np.sum((~predicted) & (y == 0))
    fn = np.sum((~predicted) & (y == 1))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall else 0.0
    )
    total = tp + fp + tn + fn
    accuracy = (tp + tn) / total if total else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
    }

def save_confusion_png(record, path):
    m = record["metrics"]
    name = display_name(record["symptom"])
    counts = np.asarray([
        [m["tn"], m["fp"]],
        [m["fn"], m["tp"]],
    ], dtype=float)
    row_totals = counts.sum(axis=1, keepdims=True)
    percentages = np.divide(
        counts,
        row_totals,
        out=np.zeros_like(counts),
        where=row_totals != 0,
    )
    fig, ax = plt.subplots(figsize=(7, 6))
    image = ax.imshow(
        percentages,
        cmap="Blues",
        vmin=0,
        vmax=1,
    )
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label("Percentage")
    colorbar.ax.yaxis.set_major_formatter(
        PercentFormatter(1.0)
    )
    labels = ["Control", name]
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labels)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted Label")
    ax.set_ylabel("Actual Label")
    ax.set_title(f"{name} Confusion Matrix", pad=15)
    for row in range(2):
        for col in range(2):
            rgba = image.cmap(
                image.norm(percentages[row, col])
            )
            brightness = (
                0.2126 * rgba[0]
                + 0.7152 * rgba[1]
                + 0.0722 * rgba[2]
            )
            text_color = (
                "white" if brightness < 0.50 else "black"
            )
            ax.text(
                col,
                row,
                f"{format_number(counts[row, col])}\n"
                f"({percentages[row, col] * 100:.1f}%)",
                ha="center",
                va="center",
                fontsize=13,
                fontweight="bold",
                color=text_color,
            )
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)

def format_number(value):
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"

def save_combined_confusions(records, path):
    records = ordered_records(records)
    count = len(records)
    columns = min(4, count)
    rows = math.ceil(count / columns)
    fig, axes = plt.subplots(
        rows,
        columns,
        figsize=(4.0 * columns, 3.8 * rows),
        squeeze=False,
    )
    for ax in axes.ravel():
        ax.axis("off")
    for ax, record in zip(axes.ravel(), records):
        ax.axis("on")
        m = record["metrics"]
        name = display_name(record["symptom"])
        counts = np.asarray([
            [m["tn"], m["fp"]],
            [m["fn"], m["tp"]],
        ], dtype=float)
        totals = counts.sum(axis=1, keepdims=True)
        pct = np.divide(
            counts,
            totals,
            out=np.zeros_like(counts),
            where=totals != 0,
        )
        ax.imshow(
            pct,
            cmap="Blues",
            vmin=0,
            vmax=1,
        )
        for i in range(2):
            for j in range(2):
                brightness = pct[i, j]
                rgba = plt.get_cmap("Blues")(brightness)
                luminance = (
                    0.2126 * rgba[0]
                    + 0.7152 * rgba[1]
                    + 0.0722 * rgba[2]
                )
                ax.text(
                    j,
                    i,
                    f"{format_number(counts[i, j])}\n"
                    f"{pct[i, j] * 100:.1f}%",
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if luminance < 0.50 else "black",
                )
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Control", name], fontsize=8)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["Control", name], fontsize=8)
        ax.set_xlabel("Predicted", fontsize=8)
        ax.set_ylabel("Actual", fontsize=8)
        ax.set_title(
            f"{name}\nF1: {m['f1'] * 100:.2f}%",
            fontsize=10,
        )
    fig.tight_layout()
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)

def save_metric_plot(records, directory):
    records = ordered_records(records)
    labels = [
        display_name(r["symptom"])
        for r in records
    ]
    fields = [
        ("precision", "Precision"),
        ("recall", "Recall"),
        ("f1", "F1 Score"),
        ("accuracy", "Accuracy"),
    ]
    values = np.asarray([
        [
            r["metrics"][field]
            for field, _ in fields
        ]
        for r in records
    ])
    macro = values.mean(axis=0)
    labels.append("Macro Average")
    values = np.vstack([values, macro])
    x = np.arange(len(labels))
    width = 0.19
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, (field, label) in enumerate(fields):
        bars = ax.bar(
            x + (i - 1.5) * width,
            values[:, i] * 100,
            width,
            label=label,
        )
        ax.bar_label(
            bars,
            fmt="%.1f",
            padding=2,
            fontsize=7,
            rotation=90,
        )
    ax.set_ylabel("Score (%)")
    ax.set_xlabel("Symptom Classifier")
    ax.set_title("Model Evaluation Metrics by Symptom")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylim(0, 110)
    ax.legend(loc="upper center", ncols=4)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(
        directory / "model_evaluation_metrics.png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

def save_threshold_plot(records, directory):
    records = ordered_records(records)
    labels = [
        display_name(r["symptom"])
        for r in records
    ]
    values = [
        r["deployment_threshold"]
        for r in records
    ]
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(labels, values)
    ax.bar_label(
        bars,
        fmt="%.2f",
        padding=3,
    )
    ax.set_ylim(0, 1)
    ax.set_ylabel("Positive-Class Probability Threshold")
    ax.set_xlabel("Symptom Classifier")
    ax.set_title("Selected Decision Thresholds by Symptom")
    ax.grid(axis="y", alpha=0.25)
    plt.setp(
        ax.get_xticklabels(),
        rotation=25,
        ha="right",
    )
    fig.tight_layout()
    fig.savefig(
        directory / "selected_thresholds.png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)

def save_confidence_curves(records, directory):
    records = ordered_records(records)
    threshold_values = np.linspace(0, 1, 101)
    curve_rows = []
    metric_names = [
        ("precision", "Precision", "precision_confidence.png"),
        ("recall", "Recall", "recall_confidence.png"),
        ("f1", "F1 Score", "f1_confidence.png"),
        ("accuracy", "Accuracy", "accuracy_confidence.png"),
    ]
    for record in records:
        for threshold in threshold_values:
            values = curve_metrics(
                record["y"],
                record["prob"],
                threshold,
            )
            curve_rows.append({
                "symptom": record["symptom"],
                "threshold": threshold,
                **values,
            })
    pd.DataFrame(curve_rows).to_csv(
        directory / "confidence_curve_data.csv",
        index=False,
    )
    for metric, label, filename in metric_names:
        fig, ax = plt.subplots(figsize=(10, 6))
        for record in records:
            y = record["y"]
            p = record["prob"]
            selected = record["deployment_threshold"]
            scores = [
                curve_metrics(y, p, t)[metric]
                for t in threshold_values
            ]
            line, = ax.plot(
                threshold_values,
                scores,
                label=display_name(record["symptom"]),
                linewidth=1.8,
            )
            selected_score = curve_metrics(
                y,
                p,
                selected,
            )[metric]
            ax.axvline(
                selected,
                color=line.get_color(),
                linestyle="--",
                alpha=0.35,
                linewidth=1,
            )
            ax.scatter(
                [selected],
                [selected_score],
                color=line.get_color(),
                s=35,
                zorder=5,
            )
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
        ax.xaxis.set_major_formatter(PercentFormatter(1.0))
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_xlabel("Positive-Class Probability Threshold")
        ax.set_ylabel(label)
        ax.set_title(f"{label} Confidence Curves")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        fig.savefig(
            directory / filename,
            dpi=300,
            bbox_inches="tight",
        )
        plt.close(fig)

def write_evaluation_outputs(records, arm_dir):
    records = ordered_records(records)
    directory = arm_dir / "evaluation"
    confusion_dir = directory / "confusion_matrices"
    directory.mkdir(parents=True, exist_ok=True)
    confusion_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    threshold_rows = []
    confusion_rows = []
    for record in records:
        sym = record["symptom"]
        metrics = record["metrics"]
        threshold = record["deployment_threshold"]
        summary_rows.append({
            "symptom": sym,
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "accuracy": metrics["accuracy"],
            "specificity": (
                metrics["tn"]
                / max(metrics["tn"] + metrics["fp"], 1)
            ),
            "balanced_accuracy": metrics["balanced_accuracy"],
            "tn": metrics["tn"],
            "fp": metrics["fp"],
            "fn": metrics["fn"],
            "tp": metrics["tp"],
            "windows": metrics["n"],
        })
        threshold_rows.append({
            "symptom": sym,
            "deployment_threshold": threshold,
            "fold_thresholds": ";".join(
                f"{x:.2f}"
                for x in record["fold_thresholds"]
            ),
        })
        confusion_rows.append({
            "symptom": sym,
            "TN": metrics["tn"],
            "FP": metrics["fp"],
            "FN": metrics["fn"],
            "TP": metrics["tp"],
        })
        png_path = (
            confusion_dir / f"{sym}_confusion_matrix.png"
        )
        save_confusion_png(record, png_path)
        save_confusion_png(
            record,
            arm_dir / sym / "confusion_matrix.png",
        )
    summary = pd.DataFrame(summary_rows)
    thresholds = pd.DataFrame(threshold_rows)
    confusion = pd.DataFrame(confusion_rows)
    summary.to_csv(
        directory / "metrics_by_symptom.csv",
        index=False,
    )
    thresholds.to_csv(
        directory / "selected_thresholds_by_symptom.csv",
        index=False,
    )
    confusion.to_csv(
        directory / "confusion_matrices_by_symptom.csv",
        index=False,
    )
    macro = {
        metric: float(summary[metric].mean())
        for metric in [
            "precision",
            "recall",
            "f1",
            "accuracy",
            "specificity",
            "balanced_accuracy",
        ]
    }
    pd.DataFrame([macro]).to_csv(
        directory / "macro_summary.csv",
        index=False,
    )
    save_metric_plot(records, directory)
    save_threshold_plot(records, directory)
    save_confidence_curves(records, directory)
    save_combined_confusions(
        records,
        directory / "confusion_matrices_all_symptoms.png",
    )

# Final Model Training and Export

def save_final_model(
    kind,
    D,
    X,
    sym,
    rows,
    y,
    personal_idx,
    weighted,
    epochs,
    threshold,
    directory,
):
    directory.mkdir(parents=True, exist_ok=True)
    prep = Prep(
        D,
        X,
        rows,
        kind,
        personal_idx,
    )
    inputs = prep(rows)
    tf.keras.backend.clear_session()
    tf.keras.utils.set_random_seed(SEED)
    model = make_model(kind, inputs)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
    )
    weights = training_weights(
        D,
        rows,
        y,
        weighted,
    )
    model.fit(
        inputs,
        y[rows],
        sample_weight=weights,
        batch_size=BATCH,
        epochs=max(int(epochs), 1),
        verbose=0,
    )
    model.save(directory / "model.keras")
    try:
        converter = tf.lite.TFLiteConverter.from_keras_model(model)
        tflite = converter.convert()
        (directory / "model.tflite").write_bytes(tflite)
    except Exception as error:
        print(f"[{sym}] TFLite conversion failed: {error}")
    np.savez(
        directory / "preprocessing.npz",
        **prep.state(),
    )
    manifest = {
        "kind": kind,
        "symptom": sym,
        "feature_set": D["features"],
        "window": WINDOW,
        "epochs": int(epochs),
        "threshold": float(threshold),
        "smoothing_windows": SMOOTH_K,
        "threshold_selection": (
            "Nested participant-grouped inner validation; "
            "equal-prior F1; threshold range 0.25-0.75."
        ),
        "uses_personal_control_baseline": bool(personal_idx),
        "personal_feature_indices": list(personal_idx),
        "feature_names": list(D["names"]),
        "sequence_channels": list(D.get("channel_names", [])),
        "personal_method": (
            "Matching participant-angle Control baseline "
            "using median/MAD robust z-scores."
        ),
        "calibration_protocol": (
            "Initial Control segment of up to five windows "
            "per participant-angle. Overlapping guard windows "
            "are excluded."
        ),
        "weighted_by_participant_video": bool(weighted),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

# Experiment Runner

def run(kind):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--features",
        choices=FEATURE_SETS,
        default="both",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        choices=SYMPTOMS,
    )
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=["base", "personal", "personal_weighted"],
        default=["personal_weighted"],
    )
    parser.add_argument(
        "--calibration-seed",
        type=int,
        default=SEED,
    )
    parser.add_argument(
        "--filter-poor-tracking",
        action="store_true",
    )
    parser.add_argument(
        "--no-final",
        action="store_true",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
    )
    args = parser.parse_args()
    D_full = load(args.features)
    meta_full = D_full["meta"].reset_index(drop=True)
    cls_full = (
        meta_full["symptom"].astype(str).str.strip().str.lower()
        .to_numpy()
    )
    tag = (
        f"vs_control_{kind}_angle_segment_nested_v1_"
        f"{args.features}_seed{args.calibration_seed}"
    )
    root = out_dir(tag)
    symptoms = args.only or SYMPTOMS
    calibration_mask, guard_mask, assignments = (
        choose_calibration_segments(
            meta_full,
            args.calibration_seed,
        )
    )
    excluded_mask = calibration_mask | guard_mask
    eligible_rows = np.flatnonzero(~excluded_mask)
    if not len(eligible_rows):
        raise RuntimeError(
            "No rows remain after Control calibration."
        )
    D = subset_dataset(D_full, eligible_rows)
    cls = cls_full[eligible_rows]
    _, quality = quality_masks(D)
    eval_control_count = int((cls == "control").sum())
    if eval_control_count == 0:
        raise RuntimeError(
            "No Control windows remain for evaluation."
        )
    fold_assignment = fold_map(D["people"])
    participant_ids = (
        D["meta"]["participant_id"].astype(str).to_numpy()
    )
    row_folds = np.asarray([
        fold_assignment[p]
        for p in participant_ids
    ], dtype=int)
    excluded_ids = np.isin(
        participant_ids,
        list(EXCLUDED_TRAINING_PARTICIPANTS),
    )
    for sym in symptoms:
        positive_count = int((cls == sym).sum())
        if positive_count == 0:
            raise RuntimeError(
                f"No evaluation windows for {sym}."
            )
        binary_rows = (
            (cls == sym) | (cls == "control")
        )
        for fold_id in range(N_FOLDS):
            test_rows = binary_rows & (row_folds == fold_id)
            train_rows = (
                binary_rows
                & (row_folds != fold_id)
                & ~excluded_ids
            )
            if len(np.unique(
                (cls[test_rows] == sym).astype(int)
            )) < 2:
                raise RuntimeError(
                    f"{sym}: fold {fold_id} has only one test class."
                )
            if len(np.unique(
                (cls[train_rows] == sym).astype(int)
            )) < 2:
                raise RuntimeError(
                    f"{sym}: fold {fold_id} has only one training class."
                )
    assignments.to_csv(
        root / "personal_calibration_assignments.csv",
        index=False,
    )
    personal = {}
    if any(arm != "base" for arm in args.arms):
        for sym in symptoms:
            Z_full, z_names, ids = make_personal_matrix(
                D_full,
                sym,
                calibration_mask,
                excluded_mask,
            )
            personal[sym] = (
                Z_full[eligible_rows],
                z_names,
                ids,
            )
    all_variants = []
    all_threshold_rows = []
    for arm in args.arms:
        arm_dir = root / arm
        arm_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n========== {arm.upper()} ==========")
        arm_records = []
        for sym in symptoms:
            y = (cls == sym).astype(int)
            if arm == "base":
                X = D["X"]
                names = list(D["names"])
                personal_idx = []
            else:
                Z, z_names, _ = personal[sym]
                X = np.hstack([
                    D["X"],
                    Z,
                ]).astype(np.float32)
                names = list(D["names"]) + z_names
                personal_idx = list(range(
                    D["X"].shape[1],
                    X.shape[1],
                ))
            D_model = dict(D)
            D_model["X"] = X
            D_model["names"] = names
            if kind == "cnn":
                D_model["extra_idx"] = (
                    list(D["extra_idx"]) + personal_idx
                )
            weighted = arm == "personal_weighted"
            def fit_predict(tr, va, sets):
                return fit_nn(
                    kind,
                    D_model,
                    X,
                    sym,
                    tr,
                    va,
                    sets,
                    y,
                    weighted,
                    args.filter_poor_tracking,
                    personal_idx,
                    args.verbose,
                    f"[{sym}|{arm}] ",
                )
            res = run_cv(
                D_model,
                sym,
                fit_predict,
                f"[{sym}|{arm}] ",
            )
            record = export_symptom(
                D_model,
                sym,
                res,
                arm_dir,
            )
            arm_records.append(record)
            all_variants.extend([
                dict(
                    model=kind,
                    arm=arm,
                    **row,
                )
                for row in record["variants"]
            ])
            for info in res["infos"]:
                all_threshold_rows.append({
                    "model": kind,
                    "arm": arm,
                    "symptom": sym,
                    "fold": info["fold"],
                    "threshold_raw": info.get("threshold_raw", 0.5),
                    "threshold_smooth3": info.get(
                        "threshold_smooth3", 0.5
                    ),
                    "best_epoch": info.get("best_epoch", np.nan),
                })
            if not args.no_final:
                keep = (
                    ((cls == sym) | (cls == "control"))
                    & ~excluded_ids
                )
                rows = np.flatnonzero(keep)
                _, masks = quality_masks(D)
                quality_mask = masks[sym]
                if args.filter_poor_tracking:
                    candidate = rows[quality_mask[rows]]
                    if (
                        len(candidate)
                        and len(np.unique(y[candidate])) == 2
                    ):
                        rows = candidate
                epochs = int(np.median([
                    info.get("best_epoch", 30)
                    for info in res["infos"]
                ]))
                threshold = record["deployment_threshold"]
                save_final_model(
                    kind,
                    D_model,
                    X,
                    sym,
                    rows,
                    y,
                    personal_idx,
                    weighted,
                    epochs,
                    threshold,
                    arm_dir / sym,
                )
        write_evaluation_outputs(
            arm_records,
            arm_dir,
        )
    variants_df = pd.DataFrame(all_variants)
    thresholds_df = pd.DataFrame(all_threshold_rows)
    variants_df.to_csv(
        root / "evaluation_variants_by_symptom.csv",
        index=False,
    )
    thresholds_df.to_csv(
        root / "nested_fold_thresholds.csv",
        index=False,
    )
    macro = variants_df.groupby(
        ["model", "arm", "variant"],
        as_index=False,
    ).agg(
        symptoms=("symptom", "nunique"),
        macro_precision=("precision", "mean"),
        macro_recall=("recall", "mean"),
        macro_f1=("f1", "mean"),
        macro_accuracy=("accuracy", "mean"),
        macro_f1_50_50=("f1_50_50", "mean"),
        macro_accuracy_50_50=("accuracy_50_50", "mean"),
    )
    macro.to_csv(
        root / "evaluation_variants_macro.csv",
        index=False,
    )
    print("\nEVALUATION COMPARISON")
    print(macro.round(4).to_string(index=False))
    primary = macro[
        macro["variant"] == PRIMARY_VARIANT
    ]
    print("\nPRIMARY VARIANT")
    print(
        primary[[
            "model",
            "arm",
            "macro_precision",
            "macro_recall",
            "macro_f1",
            "macro_accuracy",
            "macro_f1_50_50",
            "macro_accuracy_50_50",
        ]].round(4).to_string(index=False)
    )
    print(f"\nResults: {root}")

if __name__ == "__main__":
    run("mlp")
