#!/usr/bin/env python3
"""
MIDI Generator for Kontakt Sampling Pipeline

Generates MIDI files for automated sampling from Kontakt instruments.
One MIDI file per articulation, with all notes/velocities/round-robins
sequenced with precise timing for deterministic slicing.

Usage:
    python3 midi_generator.py config.json [output_dir]
"""

import json
import sys
import os
from pathlib import Path

import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def midi_to_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def seconds_to_ticks(seconds, tempo, ticks_per_beat):
    beats = seconds * (1_000_000 / tempo)
    return int(beats * ticks_per_beat)


def generate_root_notes(lo_note, hi_note, interval):
    notes = []
    n = lo_note
    while n <= hi_note:
        notes.append(n)
        n += interval
    if notes[-1] < hi_note and (hi_note - notes[-1]) > 1:
        notes.append(hi_note)
    return notes


def generate_midi_for_articulation(config, articulation, output_path):
    tempo = 500_000  # 120 BPM
    ticks_per_beat = 480

    mid = MidiFile(ticks_per_beat=ticks_per_beat)
    track = MidiTrack()
    mid.tracks.append(track)

    track.append(MetaMessage('set_tempo', tempo=tempo))
    track.append(MetaMessage('track_name', name=f"{config['instrument_name']}_{articulation['name']}"))

    lo_note = config['range']['lo_note']
    hi_note = config['range']['hi_note']
    interval = config['sampling_interval']
    root_notes = generate_root_notes(lo_note, hi_note, interval)

    note_dur = articulation['note_duration_sec']
    release_tail = articulation.get('release_tail_sec', 0.3)
    silence_gap = articulation['silence_gap_sec']
    rr_count = articulation['round_robins']

    use_sustain_pedal = articulation.get('sustain_pedal', True)
    hold_cc = articulation.get('hold_cc', {})
    note_ticks = seconds_to_ticks(note_dur, tempo, ticks_per_beat)
    gap_ticks = seconds_to_ticks(silence_gap + release_tail, tempo, ticks_per_beat)

    # Send hold CCs at the beginning (CC1=mod wheel, CC11=expression, etc.)
    for cc_name, cc_val in hold_cc.items():
        cc_num = int(cc_name.replace('cc', ''))
        track.append(Message('control_change', control=cc_num, value=cc_val, time=0))

    # Keyswitch at the beginning if needed
    ks = articulation.get('keyswitch')
    if ks is not None:
        track.append(Message('note_on', note=ks, velocity=100, time=0))
        ks_dur = seconds_to_ticks(0.1, tempo, ticks_per_beat)
        track.append(Message('note_off', note=ks, velocity=0, time=ks_dur))
        pre_gap = seconds_to_ticks(0.5, tempo, ticks_per_beat)
        # Add silence after keyswitch
        track.append(Message('note_on', note=0, velocity=0, time=pre_gap))
        track.append(Message('note_off', note=0, velocity=0, time=0))

    total_samples = 0
    current_time = 0.0
    slice_map = []

    for vel_layer in articulation['velocity_layers']:
        vel = vel_layer['midi_vel']
        vel_name = vel_layer['name']

        for root in root_notes:
            for rr in range(1, rr_count + 1):
                # Sustain pedal ON before note
                if use_sustain_pedal:
                    track.append(Message('control_change', control=64, value=127, time=0 if total_samples == 0 else gap_ticks))
                    track.append(Message('note_on', note=root, velocity=vel, time=0))
                else:
                    track.append(Message('note_on', note=root, velocity=vel, time=0 if total_samples == 0 else gap_ticks))

                # Note OFF then pedal OFF (pedal holds the sound during note_dur)
                track.append(Message('note_off', note=root, velocity=0, time=note_ticks))
                if use_sustain_pedal:
                    track.append(Message('control_change', control=64, value=0, time=0))

                # Record slice info
                rr_suffix = f"_rr{rr}" if rr_count > 1 else ""
                filename = f"{config['instrument_name']}_{articulation['name']}_{root}_{vel_name}{rr_suffix}.wav"

                slice_info = {
                    "filename": filename,
                    "root_note": root,
                    "note_name": midi_to_name(root),
                    "velocity_layer": vel_name,
                    "midi_velocity": vel,
                    "lo_vel": vel_layer['lo_vel'],
                    "hi_vel": vel_layer['hi_vel'],
                    "round_robin": rr,
                    "start_sec": current_time,
                    "note_duration_sec": note_dur,
                    "total_duration_sec": note_dur + release_tail,
                }
                slice_map.append(slice_info)

                if total_samples == 0:
                    pass  # first note starts at 0
                else:
                    current_time += silence_gap + release_tail

                current_time += note_dur
                total_samples += 1

    # End of track
    track.append(MetaMessage('end_of_track', time=gap_ticks))

    mid.save(output_path)

    total_time = current_time + release_tail
    return root_notes, total_samples, total_time, slice_map


def generate_individual_midis(config, articulation, output_dir):
    """Generate one MIDI file per note — no slicing needed"""
    tempo = 500_000
    ticks_per_beat = 480

    lo_note = config['range']['lo_note']
    hi_note = config['range']['hi_note']
    interval = config['sampling_interval']
    root_notes = generate_root_notes(lo_note, hi_note, interval)

    note_dur = articulation['note_duration_sec']
    release_tail = articulation.get('release_tail_sec', 0.3)
    rr_count = articulation['round_robins']
    use_sustain_pedal = articulation.get('sustain_pedal', True)
    hold_cc = articulation.get('hold_cc', {})

    note_ticks = seconds_to_ticks(note_dur, tempo, ticks_per_beat)
    instrument = config['instrument_name']
    artic_name = articulation['name']

    midi_dir = os.path.join(output_dir, "midi_individual")
    os.makedirs(midi_dir, exist_ok=True)

    manifest = []
    total = 0

    for vel_layer in articulation['velocity_layers']:
        vel = vel_layer['midi_vel']
        vel_name = vel_layer['name']

        for root in root_notes:
            for rr in range(1, rr_count + 1):
                rr_suffix = f"_rr{rr}" if rr_count > 1 else ""
                basename = f"{instrument}_{artic_name}_{root}_{vel_name}{rr_suffix}"

                mid = MidiFile(ticks_per_beat=ticks_per_beat)
                track = MidiTrack()
                mid.tracks.append(track)
                track.append(MetaMessage('set_tempo', tempo=tempo))
                track.append(MetaMessage('track_name', name=basename))

                # Hold CCs (layer_cc overrides hold_cc for this velocity layer)
                effective_cc = {**hold_cc, **vel_layer.get('layer_cc', {})}
                for cc_name, cc_val in effective_cc.items():
                    cc_num = int(cc_name.replace('cc', ''))
                    track.append(Message('control_change', control=cc_num, value=cc_val, time=0))

                # Small lead-in silence (200ms)
                lead_in = seconds_to_ticks(0.2, tempo, ticks_per_beat)

                # Sustain pedal + note
                if use_sustain_pedal:
                    track.append(Message('control_change', control=64, value=127, time=lead_in))
                    track.append(Message('note_on', note=root, velocity=vel, time=0))
                else:
                    track.append(Message('note_on', note=root, velocity=vel, time=lead_in))

                track.append(Message('note_off', note=root, velocity=0, time=note_ticks))
                if use_sustain_pedal:
                    track.append(Message('control_change', control=64, value=0, time=0))

                # Tail silence
                tail_ticks = seconds_to_ticks(release_tail, tempo, ticks_per_beat)
                track.append(MetaMessage('end_of_track', time=tail_ticks))

                midi_path = os.path.join(midi_dir, f"{basename}.mid")
                mid.save(midi_path)

                manifest.append({
                    "midi_file": f"{basename}.mid",
                    "wav_file": f"{basename}.wav",
                    "root_note": root,
                    "note_name": midi_to_name(root),
                    "velocity_layer": vel_name,
                    "midi_velocity": vel,
                    "lo_vel": vel_layer['lo_vel'],
                    "hi_vel": vel_layer['hi_vel'],
                    "round_robin": rr,
                })
                total += 1

    # Save manifest
    manifest_path = os.path.join(output_dir, f"{instrument}_{artic_name}_manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump({
            "instrument": instrument,
            "articulation": artic_name,
            "mode": "individual",
            "sample_rate": config['sample_rate'],
            "total": total,
            "samples": manifest,
        }, f, indent=2)

    total_time = total * (note_dur + release_tail + 0.2)
    return root_notes, total, total_time, manifest


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 midi_generator.py config.json [output_dir] [--individual]")
        sys.exit(1)

    config_path = sys.argv[1]
    individual = '--individual' in sys.argv
    args = [a for a in sys.argv[2:] if not a.startswith('--')]
    output_dir = args[0] if args else os.path.dirname(config_path) or "."

    with open(config_path) as f:
        config = json.load(f)

    os.makedirs(output_dir, exist_ok=True)
    instrument = config['instrument_name']

    print(f"Instrument: {instrument}")
    print(f"Range: MIDI {config['range']['lo_note']}-{config['range']['hi_note']}")
    print(f"Mode: {'individual (1 MIDI per note)' if individual else 'batch (1 MIDI per articulation)'}")
    print(f"Interval: {config['sampling_interval']} semitones")
    print()

    for artic in config['articulations']:
        if individual:
            root_notes, count, total_time, manifest = generate_individual_midis(
                config, artic, output_dir
            )
            minutes = int(total_time // 60)
            seconds = total_time % 60
            print(f"[{artic['name']}] (individual mode)")
            print(f"  MIDI dir: {os.path.join(output_dir, 'midi_individual')}")
            print(f"  Root notes: {len(root_notes)} ({midi_to_name(root_notes[0])}-{midi_to_name(root_notes[-1])})")
            print(f"  Velocity layers: {len(artic['velocity_layers'])}")
            print(f"  Round robins: {artic['round_robins']}")
            print(f"  Total MIDI files: {count}")
            print(f"  Total recording time: {minutes}m {seconds:.1f}s")
            print()
            print("Done! Next steps:")
            print("1. Render each MIDI individually in Reaper")
            print("2. Each rendered WAV = one sample (no slicing needed)")
        else:
            midi_path = os.path.join(output_dir, f"{instrument}_{artic['name']}.mid")
            slice_map_path = os.path.join(output_dir, f"{instrument}_{artic['name']}_slicemap.json")

            root_notes, count, total_time, slice_map = generate_midi_for_articulation(
                config, artic, midi_path
            )

            with open(slice_map_path, 'w') as f:
                json.dump({
                    "instrument": instrument,
                    "articulation": artic['name'],
                    "sample_rate": config['sample_rate'],
                    "samples": slice_map
                }, f, indent=2)

            minutes = int(total_time // 60)
            seconds = total_time % 60
            print(f"[{artic['name']}] (batch mode)")
            print(f"  MIDI: {midi_path}")
            print(f"  Root notes: {len(root_notes)} ({midi_to_name(root_notes[0])}-{midi_to_name(root_notes[-1])})")
            print(f"  Velocity layers: {len(artic['velocity_layers'])}")
            print(f"  Round robins: {artic['round_robins']}")
            print(f"  Total samples: {count}")
            print(f"  Recording time: {minutes}m {seconds:.1f}s")
            print()
            print("Done! Next steps:")
            print("1. Import .mid file and record audio output")
            print("2. Run audio_slicer.py with the recorded WAV")


if __name__ == '__main__':
    main()
