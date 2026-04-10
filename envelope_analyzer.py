#!/usr/bin/env python3
"""
Envelope Analyzer for Sampled Instruments

Analyzes WAV samples to automatically determine ADSR envelope settings.
Reads all samples in a folder and returns median envelope values.

Usage:
    python3 envelope_analyzer.py <samples_dir>

As module:
    from envelope_analyzer import analyze_folder
    envelope = analyze_folder("/path/to/samples")
"""

import os
import sys
import wave
import struct
import math
import json


def read_wav_mono_float(path):
    """Read WAV file and return mono float samples normalized to -1..1"""
    with wave.open(path, 'rb') as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    if sampwidth == 2:
        max_val = 32767.0
        samples = struct.unpack(f"<{nframes * nchannels}h", raw)
    elif sampwidth == 3:
        max_val = 8388607.0
        samples = []
        for i in range(0, len(raw), 3):
            val = int.from_bytes(raw[i:i+3], 'little', signed=True)
            samples.append(val)
    elif sampwidth == 4:
        max_val = 2147483647.0
        samples = struct.unpack(f"<{nframes * nchannels}i", raw)
    else:
        return None, 0

    # Convert to mono float
    if nchannels == 2:
        mono = [(samples[i] + samples[i+1]) / 2 / max_val for i in range(0, len(samples), 2)]
    else:
        mono = [s / max_val for s in samples]

    return mono, framerate


def compute_envelope(samples, framerate, window_ms=10):
    """Compute amplitude envelope using RMS windowing"""
    window_size = int(framerate * window_ms / 1000)
    if window_size < 1:
        window_size = 1

    envelope = []
    for i in range(0, len(samples), window_size):
        chunk = samples[i:i + window_size]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0
        envelope.append(rms)

    return envelope, window_ms / 1000


def analyze_sample(path):
    """Analyze a single sample and return envelope characteristics in ms"""
    samples, framerate = read_wav_mono_float(path)
    if not samples or framerate == 0:
        return None

    envelope, step_sec = compute_envelope(samples, framerate, window_ms=5)
    if not envelope:
        return None

    peak_val = max(envelope)
    if peak_val < 0.001:
        return None  # silent sample

    # Normalize envelope
    env_norm = [e / peak_val for e in envelope]

    # Noise floor: -40dB = 0.01
    noise_floor = 0.01

    # Onset: first frame above noise floor
    onset_idx = 0
    for i, v in enumerate(env_norm):
        if v > noise_floor:
            onset_idx = i
            break

    # Peak: highest value after onset (search first 30% of sample for transient peak)
    search_end = max(onset_idx + 1, len(env_norm) // 3)
    early_peak_val = max(env_norm[onset_idx:search_end])
    early_peak_idx = onset_idx + env_norm[onset_idx:search_end].index(early_peak_val)

    # Attack: time from onset to 90% of early peak
    attack_ms = 0
    for i in range(onset_idx, search_end):
        if env_norm[i] >= early_peak_val * 0.9:
            attack_ms = (i - onset_idx) * step_sec * 1000
            break

    # Sustain level: average RMS in middle 50% (after onset)
    active_len = len(env_norm) - onset_idx
    mid_start = onset_idx + active_len // 4
    mid_end = onset_idx + active_len * 3 // 4
    if mid_end > mid_start:
        sustain_level = sum(env_norm[mid_start:mid_end]) / (mid_end - mid_start)
    else:
        sustain_level = 0.5

    # Decay: time from early peak to sustain level
    decay_ms = 0
    for i in range(early_peak_idx, len(env_norm)):
        if env_norm[i] <= sustain_level * 1.1:
            decay_ms = (i - early_peak_idx) * step_sec * 1000
            break

    # Release: analyze last 20% of sample, measure decay rate
    release_start = int(len(env_norm) * 0.8)
    tail = env_norm[release_start:]
    if tail and tail[0] > noise_floor:
        tail_start_val = tail[0]
        release_ms = len(tail) * step_sec * 1000  # default: full tail
        for i, v in enumerate(tail):
            if v < tail_start_val * 0.1:
                release_ms = i * step_sec * 1000
                break
    else:
        release_ms = 100

    # Total duration where signal is above noise floor
    active_frames = sum(1 for v in env_norm if v > noise_floor)
    active_duration_ms = active_frames * step_sec * 1000

    return {
        "attack_ms": round(attack_ms, 1),
        "decay_ms": round(decay_ms, 1),
        "sustain_level": round(sustain_level, 3),
        "release_ms": round(release_ms, 1),
        "active_duration_ms": round(active_duration_ms, 1),
        "peak_amplitude": round(peak_val, 4),
    }


def analyze_folder(folder_path, max_samples=20):
    """Analyze samples in a folder and return median envelope settings"""
    wav_files = sorted([f for f in os.listdir(folder_path) if f.endswith('.wav') and not f.startswith('_')])

    if not wav_files:
        return default_envelope()

    # Sample evenly across the folder
    if len(wav_files) > max_samples:
        step = len(wav_files) // max_samples
        wav_files = wav_files[::step][:max_samples]

    results = []
    for f in wav_files:
        r = analyze_sample(os.path.join(folder_path, f))
        if r:
            results.append(r)

    if not results:
        return default_envelope()

    # Median of each parameter
    def median(values):
        s = sorted(values)
        n = len(s)
        if n % 2 == 0:
            return (s[n // 2 - 1] + s[n // 2]) / 2
        return s[n // 2]

    envelope = {
        "attack_ms": round(median([r["attack_ms"] for r in results]), 1),
        "decay_ms": round(median([r["decay_ms"] for r in results]), 1),
        "sustain_level": round(median([r["sustain_level"] for r in results]), 3),
        "release_ms": round(median([r["release_ms"] for r in results]), 1),
        "active_duration_ms": round(median([r["active_duration_ms"] for r in results]), 1),
        "samples_analyzed": len(results),
    }

    # Classify instrument type
    envelope["type"] = classify_envelope(envelope)

    # Convert to HISE SimpleEnvelope values
    envelope["hise_attack"] = max(0, min(20000, envelope["attack_ms"] / 1000 * 100))
    envelope["hise_release"] = max(1, min(20000, envelope["release_ms"] / 1000 * 100))

    return envelope


def classify_envelope(env):
    """Classify instrument type based on envelope shape"""
    attack = env["attack_ms"]
    sustain = env["sustain_level"]
    release = env["release_ms"]

    if attack < 5 and sustain < 0.1:
        return "percussive"  # drums, plucks
    elif attack < 20 and sustain < 0.3:
        return "short"  # staccato, pizzicato
    elif attack > 100 and sustain > 0.5:
        return "pad"  # pads, ambient
    elif sustain > 0.6:
        return "sustain"  # sustained instruments
    else:
        return "natural"  # default


def default_envelope():
    return {
        "attack_ms": 5.0,
        "decay_ms": 50.0,
        "sustain_level": 0.8,
        "release_ms": 200.0,
        "active_duration_ms": 3000.0,
        "samples_analyzed": 0,
        "type": "natural",
        "hise_attack": 5,
        "hise_release": 10,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 envelope_analyzer.py <samples_dir>")
        sys.exit(1)

    folder = sys.argv[1]
    result = analyze_folder(folder)

    print(json.dumps(result, indent=2))
    print(f"\nType: {result['type']}")
    print(f"HISE Attack: {result['hise_attack']:.0f}")
    print(f"HISE Release: {result['hise_release']:.0f}")


if __name__ == '__main__':
    main()
