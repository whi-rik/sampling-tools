#!/usr/bin/env python3
"""
Audio Slicer for Kontakt Sampling Pipeline

Slices a recorded WAV file into individual samples using the
deterministic timing from the MIDI generator's slice map.
Uses raw byte slicing for speed (no sample-by-sample conversion).

Usage:
    python3 audio_slicer.py recorded.wav slicemap.json [output_dir]
"""

import json
import sys
import os
import wave
import math


def slice_and_save(wav_path, slicemap_path, output_dir=None):
    with open(slicemap_path) as f:
        slicemap = json.load(f)

    instrument = slicemap['instrument']
    articulation = slicemap['articulation']

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(wav_path), f"{instrument}_{articulation}")
    os.makedirs(output_dir, exist_ok=True)

    # Read WAV header only first
    with wave.open(wav_path, 'rb') as w:
        nchannels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    bytes_per_frame = nchannels * sampwidth
    total_duration = nframes / framerate
    print(f"Reading {wav_path}...")
    print(f"  Duration: {total_duration:.1f}s, {nchannels}ch, {sampwidth*8}bit, {framerate}Hz")

    samples_list = slicemap['samples']
    print(f"  Slicing {len(samples_list)} samples...")

    qa_issues = []
    success_count = 0

    for i, s in enumerate(samples_list):
        filename = s['filename']
        start_sec = s['start_sec']
        duration_sec = s['total_duration_sec']

        start_frame = int(start_sec * framerate)
        end_frame = int((start_sec + duration_sec) * framerate)
        start_frame = max(0, min(start_frame, nframes))
        end_frame = max(0, min(end_frame, nframes))

        start_byte = start_frame * bytes_per_frame
        end_byte = end_frame * bytes_per_frame
        chunk = raw[start_byte:end_byte]

        if not chunk:
            qa_issues.append(f"EMPTY: {filename} (start={start_sec:.2f}s)")
            continue

        # QA: peak check on raw bytes (fast)
        num_samples = len(chunk) // sampwidth
        peak_val = 0
        if sampwidth == 2:
            for j in range(0, len(chunk), 2):
                val = int.from_bytes(chunk[j:j+2], 'little', signed=True)
                a = abs(val)
                if a > peak_val:
                    peak_val = a
            max_val = 32767
        elif sampwidth == 3:
            # Sample every 10th sample for speed
            step = max(3, (len(chunk) // 3 // 1000) * 3)
            for j in range(0, len(chunk), step):
                val = int.from_bytes(chunk[j:j+3], 'little', signed=True)
                a = abs(val)
                if a > peak_val:
                    peak_val = a
            max_val = 8388607
        elif sampwidth == 4:
            for j in range(0, len(chunk), 4):
                val = int.from_bytes(chunk[j:j+4], 'little', signed=True)
                a = abs(val)
                if a > peak_val:
                    peak_val = a
            max_val = 2147483647
        else:
            max_val = 1

        issues = []
        if peak_val == 0:
            issues.append("SILENT")
        else:
            peak_db = 20 * math.log10(peak_val / max_val)
            if peak_db < -50.0:
                issues.append(f"QUIET (Peak={peak_db:.1f}dB)")
            if peak_db > -0.5:
                issues.append(f"CLIPPING (Peak={peak_db:.1f}dB)")

        # Write WAV directly from raw bytes
        out_path = os.path.join(output_dir, filename)
        with wave.open(out_path, 'wb') as w:
            w.setnchannels(nchannels)
            w.setsampwidth(sampwidth)
            w.setframerate(framerate)
            w.writeframes(chunk)

        success_count += 1
        if issues:
            qa_issues.append(f"{filename}: {', '.join(issues)}")

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
    return success_count


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 audio_slicer.py recorded.wav slicemap.json [output_dir]")
        sys.exit(1)

    wav_path = sys.argv[1]
    slicemap_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else None
    slice_and_save(wav_path, slicemap_path, output_dir)


if __name__ == '__main__':
    main()
