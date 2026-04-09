#!/usr/bin/env python3
"""
Full Sampling Pipeline: MIDI Generation → Reaper Render → Slice

Picks instruments from kontakt_templates.json, generates MIDI files,
renders via Reaper, and slices into individual samples.

Usage:
    python run_pipeline.py [--count 10] [--output ./output] [--reaper "C:\Program Files\REAPER\reaper.exe"]

Requirements:
    - Windows (Reaper rendering + auto_bot.py)
    - Reaper installed
    - Kontakt 8 installed
    - Track templates in place (from kontakt_templates.json paths)
"""

import json
import os
import sys
import subprocess
import time
import argparse
import signal
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DEFAULT_REAPER = r"C:\Program Files\REAPER (x64)\reaper.exe"
RENDER_LUA = str(SCRIPT_DIR / "reaper" / "render_job.lua")
AUTO_BOT = str(SCRIPT_DIR / "__script" / "auto_bot.py")
TEMPLATES_JSON = str(SCRIPT_DIR / "kontakt_templates.json")

# Sampling defaults
SAMPLING_INTERVAL = 3
VELOCITY_LAYERS = [
    {"name": "dynamic1", "midi_vel": 30, "lo_vel": 0, "hi_vel": 42},
    {"name": "dynamic2", "midi_vel": 75, "lo_vel": 43, "hi_vel": 84},
    {"name": "dynamic3", "midi_vel": 115, "lo_vel": 85, "hi_vel": 127},
]
NOTE_DURATION = 3.0
RELEASE_TAIL = 0.5
SILENCE_GAP = 1.5
ROUND_ROBINS = 1
RENDER_WAIT_TIMEOUT = 600  # seconds max wait per render


def load_templates(path=TEMPLATES_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pick_instruments(templates, count=10):
    """Pick diverse instruments - one per category, prefer version 1.1"""
    seen_categories = {}
    for t in templates:
        cat = t["inst_name"]
        if cat not in seen_categories:
            seen_categories[cat] = t
        elif t.get("version", 0) > seen_categories[cat].get("version", 0):
            seen_categories[cat] = t

    # Sort by category name, pick first N
    sorted_cats = sorted(seen_categories.values(), key=lambda x: x["inst_name"])
    return sorted_cats[:count]


def make_config(instrument, output_dir):
    """Generate sampling config from template DB entry"""
    safe_name = instrument["vst_name"].replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")

    config = {
        "instrument_name": safe_name,
        "kontakt_inst": instrument["kontakt_inst"],
        "template_path": instrument["template_path"],
        "vst_seq": instrument["vst_seq"],
        "sample_rate": 48000,
        "bit_depth": 24,
        "range": {
            "lo_note": instrument["lowest_note"],
            "hi_note": instrument["highest_note"],
        },
        "sampling_interval": SAMPLING_INTERVAL,
        "articulations": [
            {
                "name": "sustain",
                "keyswitch": None,
                "note_duration_sec": NOTE_DURATION,
                "release_tail_sec": RELEASE_TAIL,
                "silence_gap_sec": SILENCE_GAP,
                "velocity_layers": VELOCITY_LAYERS,
                "round_robins": ROUND_ROBINS,
            }
        ],
    }

    config_path = os.path.join(output_dir, safe_name, "config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    return config, config_path


def run_midi_generator(config_path, output_dir):
    """Run midi_generator.py"""
    cmd = [sys.executable, str(SCRIPT_DIR / "midi_generator.py"), config_path, output_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [ERROR] MIDI generation failed: {result.stderr}")
        return False
    return True


def run_reaper_render(midi_path, wav_path, template_path, reaper_exe):
    """Launch Reaper with render_job.lua and wait for completion"""
    env = os.environ.copy()
    env["RENDER_MIDI_PATH"] = str(midi_path)
    env["RENDER_WAV_PATH"] = str(wav_path)
    env["RENDER_TEMPLATE_PATH"] = str(template_path)

    cmd = [reaper_exe, "-nosplash", "-nonewinst"]

    # Check if render_job.lua needs to be passed as action
    # Reaper runs scripts via -script flag or as startup action
    lua_path = RENDER_LUA
    if os.path.exists(lua_path):
        cmd.extend(["-script", lua_path])

    print(f"  Launching Reaper...")
    proc = subprocess.Popen(cmd, env=env)

    # Wait for WAV to appear and Reaper to exit
    start = time.time()
    while time.time() - start < RENDER_WAIT_TIMEOUT:
        if proc.poll() is not None:
            # Reaper exited
            if os.path.exists(wav_path):
                return True
            else:
                print(f"  [ERROR] Reaper exited but WAV not found: {wav_path}")
                return False
        time.sleep(2)

    # Timeout
    print(f"  [WARN] Render timeout ({RENDER_WAIT_TIMEOUT}s), killing Reaper")
    proc.kill()
    return os.path.exists(wav_path)


def run_slicer(wav_path, slicemap_path, output_dir):
    """Run audio_slicer.py"""
    cmd = [sys.executable, str(SCRIPT_DIR / "audio_slicer.py"), wav_path, slicemap_path, output_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"  [ERROR] Slicing failed: {result.stderr}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Full sampling pipeline")
    parser.add_argument("--count", type=int, default=10, help="Number of instruments to sample")
    parser.add_argument("--output", type=str, default="./output", help="Output directory")
    parser.add_argument("--reaper", type=str, default=DEFAULT_REAPER, help="Reaper executable path")
    parser.add_argument("--templates", type=str, default=TEMPLATES_JSON, help="Templates JSON path")
    parser.add_argument("--no-render", action="store_true", help="Skip Reaper render (MIDI + config only)")
    parser.add_argument("--select", type=str, nargs="*", help="Select specific inst_name(s)")
    args = parser.parse_args()

    output_root = os.path.abspath(args.output)
    os.makedirs(output_root, exist_ok=True)

    # Load templates
    templates = load_templates(args.templates)
    print(f"Loaded {len(templates)} templates")

    # Pick instruments
    if args.select:
        selected = [t for t in templates if t["inst_name"] in args.select]
        # Deduplicate by inst_name (pick highest version)
        by_name = {}
        for t in selected:
            name = t["inst_name"]
            if name not in by_name or t.get("version", 0) > by_name[name].get("version", 0):
                by_name[name] = t
        instruments = list(by_name.values())[:args.count]
    else:
        instruments = pick_instruments(templates, args.count)

    print(f"Selected {len(instruments)} instruments:\n")
    for i, inst in enumerate(instruments):
        note_count = (inst["highest_note"] - inst["lowest_note"]) // SAMPLING_INTERVAL + 1
        sample_count = note_count * len(VELOCITY_LAYERS) * ROUND_ROBINS
        print(f"  {i+1}. {inst['inst_name']} / {inst['vst_name']}")
        print(f"     Range: {inst['lowest_note_name']}({inst['lowest_note']}) - {inst['highest_note_name']}({inst['highest_note']})")
        print(f"     Samples: {sample_count} ({note_count} notes x {len(VELOCITY_LAYERS)} vel x {ROUND_ROBINS} RR)")
    print()

    # Start auto_bot.py in background (Windows only)
    bot_proc = None
    if not args.no_render and sys.platform == "win32" and os.path.exists(AUTO_BOT):
        print("Starting GUI popup bot...")
        bot_proc = subprocess.Popen([sys.executable, AUTO_BOT])

    results = []

    try:
        for i, inst in enumerate(instruments):
            print(f"\n{'='*60}")
            print(f"[{i+1}/{len(instruments)}] {inst['inst_name']} / {inst['vst_name']}")
            print(f"{'='*60}")

            # 1. Generate config
            config, config_path = make_config(inst, output_root)
            inst_dir = os.path.dirname(config_path)
            midi_dir = os.path.join(inst_dir, "midi")
            samples_dir = os.path.join(inst_dir, "samples")

            # 2. Generate MIDI
            print("\n  [Step 1] Generating MIDI...")
            if not run_midi_generator(config_path, midi_dir):
                results.append({"instrument": inst["vst_name"], "status": "MIDI_FAIL"})
                continue

            # Find generated files
            midi_files = [f for f in os.listdir(midi_dir) if f.endswith(".mid")]
            slicemap_files = [f for f in os.listdir(midi_dir) if f.endswith("_slicemap.json")]

            if not midi_files:
                results.append({"instrument": inst["vst_name"], "status": "NO_MIDI"})
                continue

            if args.no_render:
                print("  [Step 2] Skipped (--no-render)")
                print("  [Step 3] Skipped (--no-render)")
                results.append({"instrument": inst["vst_name"], "status": "MIDI_ONLY", "midi_dir": midi_dir})
                continue

            # 3. Render each articulation
            for midi_file in midi_files:
                artic_name = midi_file.replace(config["instrument_name"] + "_", "").replace(".mid", "")
                midi_path = os.path.join(midi_dir, midi_file)
                wav_path = os.path.join(inst_dir, "rendered", f"{config['instrument_name']}_{artic_name}.wav")
                os.makedirs(os.path.dirname(wav_path), exist_ok=True)

                slicemap_file = f"{config['instrument_name']}_{artic_name}_slicemap.json"
                slicemap_path = os.path.join(midi_dir, slicemap_file)

                print(f"\n  [Step 2] Rendering {artic_name}...")
                if not run_reaper_render(midi_path, wav_path, inst["template_path"], args.reaper):
                    results.append({"instrument": inst["vst_name"], "status": "RENDER_FAIL", "articulation": artic_name})
                    continue

                # 4. Slice
                print(f"  [Step 3] Slicing {artic_name}...")
                slice_out = os.path.join(samples_dir, artic_name)
                if run_slicer(wav_path, slicemap_path, slice_out):
                    sample_count = len([f for f in os.listdir(slice_out) if f.endswith(".wav")])
                    results.append({
                        "instrument": inst["vst_name"],
                        "status": "OK",
                        "articulation": artic_name,
                        "samples": sample_count,
                        "output": slice_out,
                    })
                else:
                    results.append({"instrument": inst["vst_name"], "status": "SLICE_FAIL", "articulation": artic_name})

    finally:
        # Stop auto_bot
        if bot_proc:
            bot_proc.terminate()
            print("\nGUI popup bot stopped.")

    # Summary
    print(f"\n{'='*60}")
    print("PIPELINE SUMMARY")
    print(f"{'='*60}")
    for r in results:
        status = r["status"]
        name = r["instrument"]
        if status == "OK":
            print(f"  OK  {name} [{r.get('articulation','')}] → {r.get('samples',0)} samples")
        elif status == "MIDI_ONLY":
            print(f"  MIDI {name} → {r.get('midi_dir','')}")
        else:
            print(f"  FAIL {name} [{r.get('articulation','')}] → {status}")

    # Save results
    results_path = os.path.join(output_root, "pipeline_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
