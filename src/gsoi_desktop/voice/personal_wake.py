"""A wake-word detector tuned to the user's own voice and accent.

The stock "hey jarvis" model was trained on English speakers; an Italian pronunciation often scores
low. Here a small classifier learns, from a few recordings of the user, what *their* "Hey Jarvis"
looks like in the same embedding space the stock model uses, and what their normal speech and room
noise look like (so ordinary conversation does not trigger it). Everything stays on the PC.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from gsoi_desktop.voice.audio import FRAME, rms
from gsoi_desktop.voice.wake import WAKE_MODEL, OpenWakeWordDetector, VoiceUnavailableError

STEPS = 16  # embeddings per decision (the model's input window)
PERSONAL_THRESHOLD = 0.9
PERSONAL_SCALE = 0.5 / PERSONAL_THRESHOLD  # maps "personal >= 0.9" onto the pipeline's 0.5 default
FILE_NAME = "personal_wake.npz"
Progress = Callable[[str, float], None]


class Extractor(Protocol):
    def stream(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Feed audio like the live microphone; return (features (T,16,96), stock scores (T,))."""


class OpenWakeWordExtractor:
    def __init__(self) -> None:
        try:
            from openwakeword.model import Model

            self._model: Any = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception as exc:
            raise VoiceUnavailableError(f"Parola di attivazione non disponibile: {exc}") from exc

    def stream(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        self._model.reset()
        feats: list[np.ndarray] = []
        base: list[float] = []
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            scores = self._model.predict(audio[i : i + FRAME])
            base.append(max(scores.values()) if scores else 0.0)
            feats.append(self._model.preprocessor.get_features(STEPS)[0].copy())
        if not feats:
            return np.zeros((0, STEPS, 96), np.float32), np.zeros(0, np.float32)
        return np.array(feats, dtype=np.float32), np.array(base, dtype=np.float32)


# ---- the model ----------


@dataclass
class PersonalModel:
    mean: np.ndarray
    std: np.ndarray
    weights: np.ndarray
    bias: float

    def score(self, features: np.ndarray) -> np.ndarray:
        """Probability (0..1) that each (16, 96) window is the user's wake phrase."""
        flat = features.reshape(len(features), -1).astype(np.float32)
        logits = ((flat - self.mean) / self.std) @ self.weights + self.bias
        return np.asarray(1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30))))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(
            tmp, mean=self.mean, std=self.std, weights=self.weights, bias=np.float64(self.bias)
        )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> PersonalModel | None:
        try:
            with np.load(path) as data:
                model = cls(data["mean"], data["std"], data["weights"], float(data["bias"]))
        except (OSError, ValueError, KeyError):
            return None
        return model if model.weights.shape[0] == STEPS * 96 else None


def train_logistic(x: np.ndarray, y: np.ndarray, *, iterations: int = 500) -> PersonalModel:
    mean = x.mean(axis=0)
    std = x.std(axis=0) + 1e-6
    z = (x - mean) / std
    weights = np.zeros(z.shape[1], dtype=np.float32)
    bias = 0.0
    positives = max(1.0, float(y.sum()))
    class_weight = (len(y) - positives) / positives * 0.5
    sample_weight = np.where(y == 1, class_weight, 1.0).astype(np.float32)
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(z @ weights + bias, -30, 30)))
        grad = (p - y) * sample_weight
        weights -= 0.05 * (z.T @ grad / len(y) + 0.02 * weights)
        bias -= 0.05 * float(grad.mean())
    return PersonalModel(mean.astype(np.float32), std.astype(np.float32), weights, float(bias))


# ---- training data ----------


def augment(audio: np.ndarray, seed: int = 0) -> list[np.ndarray]:
    """The same recording at other volumes, speeds and with some noise: the user will not say it
    exactly the same way twice."""
    rng = np.random.default_rng(seed)
    data = audio.astype(np.float64)
    out = [
        audio,
        (data * 0.5).astype(np.int16),
        np.clip(data * 1.8, -32768, 32767).astype(np.int16),
    ]
    for factor in (0.92, 1.08):
        n = max(1, int(len(audio) / factor))
        out.append(
            np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), data).astype(
                np.int16
            )
        )
    out.append(
        np.clip(data + rng.standard_normal(len(audio)) * 300, -32768, 32767).astype(np.int16)
    )
    return out


def speech_end_frame(audio: np.ndarray) -> int:
    """Index (in frames) just after the last loud frame."""
    levels = [rms(audio[i : i + FRAME]) for i in range(0, len(audio) - FRAME + 1, FRAME)]
    if not levels:
        return 0
    threshold = max(150.0, 0.15 * max(levels))
    loud = [i for i, v in enumerate(levels) if v > threshold]
    return loud[-1] + 1 if loud else 0


def _padded(audio: np.ndarray, pre: float = 1.0, post: float = 1.2) -> np.ndarray:
    return np.concatenate(
        [np.zeros(int(pre * 16000), np.int16), audio, np.zeros(int(post * 16000), np.int16)]
    )


def phrase_examples(clip: np.ndarray, extractor: Extractor) -> tuple[np.ndarray, np.ndarray]:
    """Windows ending right after the phrase are positives; silence around it is negative."""
    feats, _ = extractor.stream(_padded(clip))
    end = speech_end_frame(clip) + int(1.0 * 16000 / FRAME)
    xs: list[np.ndarray] = []
    ys: list[int] = []
    for t in range(len(feats)):
        if end <= t <= end + 3:
            xs.append(feats[t].ravel())
            ys.append(1)
        elif t < end - 6 or t > end + 8:
            xs.append(feats[t].ravel())
            ys.append(0)
    return np.array(xs, np.float32), np.array(ys, np.float32)


def background_examples(audio: np.ndarray, extractor: Extractor) -> np.ndarray:
    feats, _ = extractor.stream(audio)
    return feats.reshape(len(feats), -1).astype(np.float32)


@dataclass
class EnrollmentResult:
    model: PersonalModel
    clips: int
    recall: float  # held-out estimate (cross-validation)
    false_alarms: int  # on held-out ordinary speech and noise
    held_out_seconds: float
    stock_recall: float  # what the stock model alone achieved on the same recordings


def _fit(pos: list[tuple[np.ndarray, np.ndarray]], neg: list[np.ndarray]) -> PersonalModel:
    xs = [x for x, _ in pos] + neg
    ys = [y for _, y in pos] + [np.zeros(len(n), np.float32) for n in neg]
    return train_logistic(np.concatenate(xs), np.concatenate(ys))


def enroll(
    phrases: list[np.ndarray],
    speech: np.ndarray,
    ambient: np.ndarray,
    extractor: Extractor,
    progress: Progress | None = None,
    *,
    folds: int = 4,
) -> EnrollmentResult:
    """Learn the user's wake phrase from recordings. `speech` is ordinary talking, `ambient` is the
    quiet room. Returns the model plus honest, cross-validated numbers."""
    report = progress or (lambda what, fraction: None)
    if len(phrases) < 4:
        raise ValueError("servono almeno 4 registrazioni della frase")

    per_clip: list[list[tuple[np.ndarray, np.ndarray]]] = []
    stock_hits = 0
    for i, clip in enumerate(phrases):
        report("Studio le tue registrazioni...", 0.05 + 0.4 * i / len(phrases))
        per_clip.append([phrase_examples(v, extractor) for v in augment(clip, seed=i)])
        _, base = extractor.stream(_padded(clip))
        stock_hits += int(base.max() >= 0.5) if len(base) else 0

    # hold out the last 30% of the ordinary speech to measure false alarms honestly
    cut = int(len(speech) * 0.7)
    speech_train, speech_test = speech[:cut], speech[cut:]
    report("Studio il tuo parlato normale...", 0.5)
    negatives = [background_examples(v, extractor) for v in augment(speech_train, seed=99)]
    negatives.append(background_examples(ambient, extractor))
    held_feats, _ = extractor.stream(np.concatenate([speech_test, ambient]))

    order = list(range(len(phrases)))
    random.Random(7).shuffle(order)  # noqa: S311  # nosec B311
    hits = 0
    for k in range(folds):
        report("Verifico che funzioni...", 0.6 + 0.25 * k / folds)
        test_ids = set(order[k::folds])
        train_pos = [
            pe for i, variants in enumerate(per_clip) if i not in test_ids for pe in variants
        ]
        model_k = _fit(train_pos, negatives)
        for i in test_ids:
            feats, _ = extractor.stream(_padded(phrases[i]))
            hits += int(len(feats) > 0 and model_k.score(feats).max() >= PERSONAL_THRESHOLD)

    report("Addestro il tuo rilevatore...", 0.9)
    model = _fit([pe for variants in per_clip for pe in variants], negatives)
    false_alarms = 0
    if len(held_feats):
        scores = model.score(held_feats)
        false_alarms = int(_count_runs(scores >= PERSONAL_THRESHOLD))
    report("Fatto", 1.0)
    return EnrollmentResult(
        model=model,
        clips=len(phrases),
        recall=hits / len(phrases),
        false_alarms=false_alarms,
        held_out_seconds=(len(speech_test) + len(ambient)) / 16000,
        stock_recall=stock_hits / len(phrases),
    )


def _count_runs(flags: np.ndarray) -> int:
    """Number of separate runs of True (one false alarm per episode, not per frame)."""
    padded = np.concatenate([[False], flags, [False]])
    return int(np.sum(padded[1:] & ~padded[:-1]))


# ---- live detection ----------


class PersonalWakeDetector:
    """Stock model plus the user's own classifier: whichever is more convinced wins."""

    def __init__(self, base: OpenWakeWordDetector, personal: PersonalModel | None) -> None:
        self._base = base
        self._personal = personal
        self.last_personal = 0.0

    @classmethod
    def create(cls, threshold: float, personal_path: Path | None) -> PersonalWakeDetector:
        personal = PersonalModel.load(personal_path) if personal_path is not None else None
        return cls(OpenWakeWordDetector(threshold), personal)

    @property
    def personalised(self) -> bool:
        return self._personal is not None

    def predict(self, frame: np.ndarray) -> float:
        score = self._base.predict(frame)
        if self._personal is None:
            return score
        features = self._base.embeddings()
        self.last_personal = float(self._personal.score(features[None, ...])[0])
        return max(score, self.last_personal * PERSONAL_SCALE)

    def reset(self) -> None:
        self._base.reset()
        self.last_personal = 0.0
