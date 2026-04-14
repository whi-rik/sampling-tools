#!/usr/bin/env python3
"""
Single Instrument Pipeline: Config → MIDI → Render → Slice

Runs the full pipeline for a single instrument config file.

Usage:
    python run_single.py config.json --output ./output [--reaper "path/to/reaper.exe"]
    python run_single.py config.json --output "\\\\Qnap03\\rf\\HISE\\output_amati"
    python run_single.py config.json --output ./output --no-render  (MIDI only)
    python run_single.py config.json --output ./output --slice-only rendered.wav  (slice existing WAV)
"""

import json
import os
import sys
import subprocess
import time
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DEFAULT_REAPER = r"C:\Program Files\REAPER (x64)\reaper.exe"
RENDER_LUA = str(SCRIPT_DIR / "reaper" / "render_job.lua")
AUTO_BOT = str(SCRIPT_DIR / "__script" / "auto_bot.py")


def get_reaper_resource_path():
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "REAPER")
    else:
        return os.path.expanduser("~/Library/Application Support/REAPER")


def resolve_template_path(raw_path):
    if os.path.isabs(raw_path) and os.path.exists(raw_path):
        return raw_path
    clean = raw_path.lstrip("/\\")
    parts = clean.replace("\\", "/").split("/")
    reaper_idx = None
    for i, p in enumerate(parts):
        if p.lower() == "reaper":
            reaper_idx = i
            break
    if reaper_idx is not None:
        rel = os.path.join(*parts[reaper_idx + 1:])
    else:
        rel = clean
    return os.path.join(get_reaper_resource_path(), rel)


def run_midi_gen(config_path, output_dir):
    cmd = [sys.executable, str(SCRIPT_DIR / "midi_generator.py"), config_path, output_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"[ERROR] MIDI generation: {result.stderr}")
        return False
    return True


def run_render(midi_path, wav_path, template_path, reaper_exe, timeout=600):
    resolved = resolve_template_path(template_path)
    env = os.environ.copy()
    env["RENDER_MIDI_PATH"] = str(midi_path)
    env["RENDER_WAV_PATH"] = str(wav_path)
    env["RENDER_TEMPLATE_PATH"] = resolved

    print(f"  MIDI: {midi_path}")
    print(f"  WAV:  {wav_path}")
    print(f"  Template: {resolved}")

    cmd = [reaper_exe, "-nosplash", "-new", RENDER_LUA]
    proc = subprocess.Popen(cmd, env=env)

    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            if os.path.exists(wav_path):
                return True
            print(f"  [ERROR] Reaper exited but WAV not found")
            return False
        time.sleep(2)

    print(f"  [WARN] Timeout ({timeout}s), killing Reaper")
    proc.kill()
    return os.path.exists(wav_path)


def run_slice(wav_path, slicemap_path, output_dir):
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from audio_slicer import slice_and_save
        count = slice_and_save(wav_path, slicemap_path, output_dir)
        return count
    except Exception as e:
        print(f"[ERROR] Slicing: {e}")
        return 0


def run_envelope(output_dir):
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from envelope_analyzer import analyze_folder
        env = analyze_folder(output_dir)
        env_path = os.path.join(output_dir, "_envelope.json")
        with open(env_path, 'w') as f:
            json.dump(env, f, indent=2)
        return env
    except Exception as e:
        print(f"[WARN] Envelope analysis: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Single instrument sampling pipeline")
    parser.add_argument("config", help="Config JSON file")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--reaper", default=DEFAULT_REAPER, help="Reaper executable")
    parser.add_argument("--no-render", action="store_true", help="MIDI generation only")
    parser.add_argument("--slice-only", type=str, help="Skip render, slice this WAV file")
    parser.add_argument("--individual", action="store_true", help="One MIDI per note, no slicing")
    parser.add_argument("--render-timeout", type=int, default=600, help="Render timeout seconds")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    output = os.path.abspath(args.output)
    os.makedirs(output, exist_ok=True)
    instrument = config['instrument_name']

    print(f"{'='*60}")
    print(f"  {instrument}")
    print(f"  Range: MIDI {config['range']['lo_note']}-{config['range']['hi_note']}")
    print(f"{'='*60}")

    # Start auto_bot in background (Windows)
    bot_proc = None
    if not args.no_render and not args.slice_only and sys.platform == "win32" and os.path.exists(AUTO_BOT):
        print("\nStarting GUI popup bot...")
        bot_proc = subprocess.Popen([sys.executable, AUTO_BOT])

    try:
        for artic in config['articulations']:
            artic_name = artic['name']
            samples_dir = os.path.join(output, "samples", artic_name)

            if args.individual:
                # === INDIVIDUAL MODE: one MIDI per note ===
                print(f"\n[Step 1] Generating individual MIDIs for {artic_name}...")
                cmd = [sys.executable, str(SCRIPT_DIR / "midi_generator.py"), args.config, output, "--individual"]
                result = subprocess.run(cmd, capture_output=True, text=True)
                print(result.stdout)
                if result.returncode != 0:
                    print(f"[ERROR] MIDI generation: {result.stderr}")
                    continue

                # Load manifest
                manifest_path = os.path.join(output, f"{instrument}_{artic_name}_manifest.json")
                if not os.path.exists(manifest_path):
                    print(f"[ERROR] Manifest not found: {manifest_path}")
                    continue
                with open(manifest_path) as f:
                    manifest = json.load(f)

                midi_ind_dir = os.path.join(output, "midi_individual")
                os.makedirs(samples_dir, exist_ok=True)

                if args.no_render:
                    print(f"[Step 2] Skipped (--no-render)")
                    continue

                # Render each MIDI individually
                template = config.get('template_path', '')
                total = len(manifest['samples'])
                for idx, sample in enumerate(manifest['samples']):
                    midi_path = os.path.join(midi_ind_dir, sample['midi_file'])
                    wav_path = os.path.join(samples_dir, sample['wav_file'])

                    if os.path.exists(wav_path):
                        print(f"  [{idx+1}/{total}] Skip (exists): {sample['wav_file']}")
                        continue

                    print(f"  [{idx+1}/{total}] Rendering {sample['wav_file']}...")
                    run_render(midi_path, wav_path, template, args.reaper, args.render_timeout)

                # Normalize all rendered WAVs
                print(f"\n[Step 3] Normalizing samples...")
                normalize_count = 0
                for sample in manifest['samples']:
                    wav_path = os.path.join(samples_dir, sample['wav_file'])
                    if os.path.exists(wav_path):
                        normalize_count += 1
                print(f"  {normalize_count} samples rendered")

                # Envelope analysis
                print(f"\n[Step 4] Analyzing envelope...")
                env = run_envelope(samples_dir)

                print(f"\n{'='*60}")
                print(f"  DONE: {instrument} / {artic_name} (individual)")
                print(f"  Samples: {normalize_count}")
                print(f"  Output: {samples_dir}")
                if env:
                    print(f"  Envelope: A={env.get('attack_ms',0)}ms R={env.get('release_ms',0)}ms Type={env.get('type','?')}")
                print(f"{'='*60}")
                continue

            # === BATCH MODE (original) ===
            midi_dir = os.path.join(output, "midi")

            # Step 1: MIDI
            if not args.slice_only:
                print(f"\n[Step 1] Generating MIDI for {artic_name}...")
                if not run_midi_gen(args.config, midi_dir):
                    continue

            midi_file = f"{instrument}_{artic_name}.mid"
            midi_path = os.path.join(midi_dir, midi_file)
            slicemap_file = f"{instrument}_{artic_name}_slicemap.json"
            slicemap_path = os.path.join(midi_dir, slicemap_file)

            # Step 2: Render
            wav_path = os.path.join(output, "rendered", f"{instrument}_{artic_name}.wav")
            os.makedirs(os.path.dirname(wav_path), exist_ok=True)

            if args.slice_only:
                wav_path = args.slice_only
                print(f"\n[Step 2] Skipped (--slice-only)")
            elif args.no_render:
                print(f"\n[Step 2] Skipped (--no-render)")
                continue
            else:
                print(f"\n[Step 2] Rendering {artic_name}...")
                template = config.get('template_path', '')
                if not run_render(midi_path, wav_path, template, args.reaper, args.render_timeout):
                    print(f"  [ERROR] Render failed for {artic_name}")
                    continue

            # Step 3: Slice
            if not os.path.exists(slicemap_path):
                print(f"  [ERROR] Slicemap not found: {slicemap_path}")
                continue

            print(f"\n[Step 3] Slicing {artic_name}...")
            count = run_slice(wav_path, slicemap_path, samples_dir)

            # Step 4: Envelope
            print(f"\n[Step 4] Analyzing envelope...")
            env = run_envelope(samples_dir)

            # Summary
            print(f"\n{'='*60}")
            print(f"  DONE: {instrument} / {artic_name}")
            print(f"  Samples: {count}")
            print(f"  Output: {samples_dir}")
            if env:
                print(f"  Envelope: A={env.get('attack_ms',0)}ms R={env.get('release_ms',0)}ms Type={env.get('type','?')}")
            print(f"{'='*60}")

    finally:
        if bot_proc:
            bot_proc.terminate()
            print("GUI popup bot stopped.")


if __name__ == '__main__':
    main()
