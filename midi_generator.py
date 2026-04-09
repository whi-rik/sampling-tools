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

    note_ticks = seconds_to_ticks(note_dur, tempo, ticks_per_beat)
    gap_ticks = seconds_to_ticks(silence_gap + release_tail, tempo, ticks_per_beat)

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
                # Note ON
                track.append(Message('note_on', note=root, velocity=vel, time=0 if total_samples == 0 else gap_ticks))

                # Note OFF
                track.append(Message('note_off', note=root, velocity=0, time=note_ticks))

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


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 midi_generator.py config.json [output_dir]")
        sys.exit(1)

    config_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(config_path) or "."

    with open(config_path) as f:
        config = json.load(f)

    os.makedirs(output_dir, exist_ok=True)
    instrument = config['instrument_name']

    print(f"Instrument: {instrument}")
    print(f"Range: MIDI {config['range']['lo_note']}-{config['range']['hi_note']}")
    print(f"Interval: {config['sampling_interval']} semitones")
    print()

    all_slice_maps = {}

    for artic in config['articulations']:
        midi_path = os.path.join(output_dir, f"{instrument}_{artic['name']}.mid")
        slice_map_path = os.path.join(output_dir, f"{instrument}_{artic['name']}_slicemap.json")

        root_notes, count, total_time, slice_map = generate_midi_for_articulation(
            config, artic, midi_path
        )

        all_slice_maps[artic['name']] = slice_map

        # Save slice map for the slicer
        with open(slice_map_path, 'w') as f:
            json.dump({
                "instrument": instrument,
                "articulation": artic['name'],
                "sample_rate": config['sample_rate'],
                "samples": slice_map
            }, f, indent=2)

        minutes = int(total_time // 60)
        seconds = total_time % 60
        print(f"[{artic['name']}]")
        print(f"  MIDI: {midi_path}")
        print(f"  Root notes: {len(root_notes)} ({midi_to_name(root_notes[0])}-{midi_to_name(root_notes[-1])})")
        print(f"  Velocity layers: {len(artic['velocity_layers'])}")
        print(f"  Round robins: {artic['round_robins']}")
        print(f"  Total samples: {count}")
        print(f"  Recording time: {minutes}m {seconds:.1f}s")
        print()

    print("Done! Next steps:")
    print("1. Load Kontakt instrument in Reaper")
    print("2. Import each .mid file and record the audio output")
    print("3. Run audio_slicer.py with the recorded WAV and corresponding _slicemap.json")


if __name__ == '__main__':
    main()
