#!/usr/bin/env python3
"""
Audio Slicer for Kontakt Sampling Pipeline

Slices a recorded WAV file into individual samples using the
deterministic timing from the MIDI generator's slice map.
Also performs basic QA checks on each sample.

Usage:
    python3 audio_slicer.py recorded.wav slicemap.json [output_dir]
"""

import json
import sys
import os
import struct
import wave
import array


def read_wav(path):
    with wave.open(path, 'rb') as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    if sampwidth == 2:
        fmt = f"<{nframes * nchannels}h"
        samples = list(struct.unpack(fmt, raw))
        max_val = 32767.0
    elif sampwidth == 3:
        samples = []
        for i in range(0, len(raw), 3):
            b = raw[i:i+3]
            val = int.from_bytes(b, byteorder='little', signed=True)
            samples.append(val)
        max_val = 8388607.0
    elif sampwidth == 4:
        fmt = f"<{nframes * nchannels}i"
        samples = list(struct.unpack(fmt, raw))
        max_val = 2147483647.0
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    return samples, nchannels, sampwidth, framerate, max_val


def write_wav(path, samples, nchannels, sampwidth, framerate):
    with wave.open(path, 'wb') as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)

        if sampwidth == 2:
            raw = struct.pack(f"<{len(samples)}h", *[max(-32768, min(32767, s)) for s in samples])
        elif sampwidth == 3:
            raw = b''
            for s in samples:
                s = max(-8388608, min(8388607, s))
                raw += s.to_bytes(3, byteorder='little', signed=True)
        elif sampwidth == 4:
            raw = struct.pack(f"<{len(samples)}i", *samples)
        else:
            raise ValueError(f"Unsupported sample width: {sampwidth}")

        w.writeframes(raw)


def rms_db(samples, max_val):
    if not samples:
        return -100.0
    sum_sq = sum((s / max_val) ** 2 for s in samples)
    rms = (sum_sq / len(samples)) ** 0.5
    if rms < 1e-10:
        return -100.0
    import math
    return 20 * math.log10(rms)


def peak_db(samples, max_val):
    if not samples:
        return -100.0
    peak = max(abs(s) for s in samples) / max_val
    if peak < 1e-10:
        return -100.0
    import math
    return 20 * math.log10(peak)


def slice_audio(wav_samples, nchannels, framerate, start_sec, duration_sec):
    start_frame = int(start_sec * framerate)
    end_frame = int((start_sec + duration_sec) * framerate)

    start_idx = start_frame * nchannels
    end_idx = end_frame * nchannels

    start_idx = max(0, min(start_idx, len(wav_samples)))
    end_idx = max(0, min(end_idx, len(wav_samples)))

    return wav_samples[start_idx:end_idx]


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 audio_slicer.py recorded.wav slicemap.json [output_dir]")
        sys.exit(1)

    wav_path = sys.argv[1]
    slicemap_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else None

    with open(slicemap_path) as f:
        slicemap = json.load(f)

    instrument = slicemap['instrument']
    articulation = slicemap['articulation']

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(wav_path), f"{instrument}_{articulation}")

    os.makedirs(output_dir, exist_ok=True)

    print(f"Reading {wav_path}...")
    wav_samples, nchannels, sampwidth, framerate, max_val = read_wav(wav_path)
    total_duration = len(wav_samples) / nchannels / framerate
    print(f"  Duration: {total_duration:.1f}s, {nchannels}ch, {sampwidth*8}bit, {framerate}Hz")

    samples_list = slicemap['samples']
    print(f"  Slicing {len(samples_list)} samples...")
    print()

    qa_issues = []
    success_count = 0

    for i, s in enumerate(samples_list):
        filename = s['filename']
        start = s['start_sec']
        duration = s['total_duration_sec']

        sliced = slice_audio(wav_samples, nchannels, framerate, start, duration)

        if not sliced:
            qa_issues.append(f"EMPTY: {filename} (start={start:.2f}s)")
            continue

        # QA checks
        rms = rms_db(sliced, max_val)
        peak = peak_db(sliced, max_val)

        issues = []
        if rms < -50.0:
            issues.append(f"QUIET (RMS={rms:.1f}dB)")
        if peak > -0.5:
            issues.append(f"CLIPPING (Peak={peak:.1f}dB)")

        out_path = os.path.join(output_dir, filename)
        write_wav(out_path, sliced, nchannels, sampwidth, framerate)
        success_count += 1

        status = "OK" if not issues else ", ".join(issues)
        if issues:
            qa_issues.append(f"{filename}: {status}")

        if (i + 1) % 20 == 0 or i == len(samples_list) - 1:
            print(f"  [{i+1}/{len(samples_list)}] sliced")

    print()
    print(f"Results:")
    print(f"  Output dir: {output_dir}")
    print(f"  Samples: {success_count}/{len(samples_list)}")

    if qa_issues:
        print(f"  QA Issues ({len(qa_issues)}):")
        for issue in qa_issues:
            print(f"    - {issue}")
    else:
        print(f"  QA: All samples passed")

    # Save QA report
    report_path = os.path.join(output_dir, "_qa_report.json")
    with open(report_path, 'w') as f:
        json.dump({
            "instrument": instrument,
            "articulation": articulation,
            "total_samples": len(samples_list),
            "success": success_count,
            "issues": qa_issues
        }, f, indent=2)

    print(f"  QA report: {report_path}")


if __name__ == '__main__':
    main()
