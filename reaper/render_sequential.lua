-- Render individual MIDI files sequentially in ONE Reaper session.
-- Kontakt loads once, then each MIDI is imported, rendered, and removed.
--
-- Expected env:
--   RENDER_MANIFEST_PATH  : path to manifest JSON (from midi_generator --individual)
--   RENDER_MIDI_DIR       : directory containing individual .mid files
--   RENDER_OUTPUT_DIR     : directory for output .wav files
--   RENDER_TEMPLATE_PATH  : track template with Kontakt loaded

local function exists(p)
  if not p or p == "" then return false end
  local f = io.open(p, "rb")
  if f then f:close(); return true end
  return false
end

local function read_all(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local d = f:read("*a")
  f:close()
  return d
end

local function dirname(path)
  return path:match("^(.*)[/\\][^/\\]+$") or ""
end

local function basename_noext(p)
  local name = p:match("([^/\\]+)$") or p
  return name:match("^(.*)%.%w+$") or name
end

local SEP = package.config:sub(1,1)

local function join(a, b)
  if not a or a == "" then return b end
  local last = a:sub(-1)
  if last == "\\" or last == "/" then return a .. b end
  return a .. SEP .. b
end

local function resolve_template_path(tpl)
  if not tpl or tpl == "" then return nil end
  if exists(tpl) then return tpl end
  local res_base = reaper.GetResourcePath() or ""
  if res_base == "" then return tpl end
  local clean = tpl:gsub("^[/\\]+", "")
  local parts = {}
  for part in clean:gmatch("([^/\\]+)") do parts[#parts+1] = part end
  local idx_reaper = nil
  for i, p in ipairs(parts) do
    if p:lower() == "reaper" then idx_reaper = i; break end
  end
  local rel = clean
  if idx_reaper then rel = table.concat(parts, SEP, idx_reaper + 1) end
  local candidate = join(res_base, rel)
  if exists(candidate) then return candidate end
  local track_tpl = join(res_base, "TrackTemplates")
  candidate = join(track_tpl, rel)
  if exists(candidate) then return candidate end
  return tpl
end

local function mkdir_p(path)
  if not path or path == "" then return end
  reaper.RecursiveCreateDirectory(path, 0)
end

local function extract_first_track_chunk(data)
  if not data then return nil end
  local s = data:find("<TRACK")
  if not s then return nil end
  local e = data:find("\n<TRACK", s+1, true)
  return e and data:sub(s, e-1) or data:sub(s)
end

local function set_time_from_item(it)
  local p = reaper.GetMediaItemInfo_Value(it, "D_POSITION")
  local l = reaper.GetMediaItemInfo_Value(it, "D_LENGTH")
  reaper.GetSet_LoopTimeRange(true, false, p, p + l, false)
  reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 1, true)
  reaper.GetSetProjectInfo(0, "RENDER_STARTPOS", p, true)
  reaper.GetSetProjectInfo(0, "RENDER_ENDPOS", p + l, true)
end

local function set_render_out(full_path)
  local dir = dirname(full_path)
  local base = basename_noext(full_path)
  reaper.GetSetProjectInfo_String(0, "RENDER_FILE", dir, true)
  reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", base, true)
  reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, true)
end

local function get_script_dir()
  local info = debug.getinfo(1, "S")
  local src = info.source:match("@(.+)")
  if src then return src:match("^(.*)[/\\]") end
  return nil
end

local function launch_auto_ok()
  local auto_ok = os.getenv("RENDER_AUTO_OK")
  if auto_ok == "0" then return end
  local dir = get_script_dir()
  if not dir then return end
  local path = join(dir, "__start_up.lua")
  if not exists(path) then return end
  local cmd_id = reaper.AddRemoveReaScript(true, 0, path, true)
  if cmd_id and cmd_id > 0 then
    reaper.Main_OnCommand(cmd_id, 0)
  end
end

-- Simple JSON array parser for manifest
local function parse_manifest(path)
  local data = read_all(path)
  if not data then return nil end
  local samples = {}
  for midi_file, wav_file in data:gmatch('"midi_file":%s*"([^"]+)".-"wav_file":%s*"([^"]+)"') do
    samples[#samples+1] = { midi = midi_file, wav = wav_file }
  end
  return samples
end

local function main()
  launch_auto_ok()

  local manifest_path = os.getenv("RENDER_MANIFEST_PATH") or ""
  local midi_dir      = os.getenv("RENDER_MIDI_DIR") or ""
  local output_dir    = os.getenv("RENDER_OUTPUT_DIR") or ""
  local template      = resolve_template_path(os.getenv("RENDER_TEMPLATE_PATH") or "")

  if manifest_path == "" or midi_dir == "" or output_dir == "" then
    reaper.ShowConsoleMsg("[ERROR] Missing RENDER_MANIFEST_PATH, RENDER_MIDI_DIR, or RENDER_OUTPUT_DIR\n")
    return
  end

  local samples = parse_manifest(manifest_path)
  if not samples or #samples == 0 then
    reaper.ShowConsoleMsg("[ERROR] No samples in manifest\n")
    return
  end

  reaper.ShowConsoleMsg(string.format("[SEQ] %d samples to render\n", #samples))

  -- New project & sample rate 48000
  reaper.Main_OnCommand(40023, 0)
  reaper.GetSetProjectInfo(0, "PROJECT_SRATE_USE", 1, true)
  reaper.GetSetProjectInfo(0, "PROJECT_SRATE", 48000, true)

  mkdir_p(output_dir)

  -- Insert track with template (Kontakt loads ONCE)
  reaper.InsertTrackAtIndex(0, true)
  local tr = reaper.GetTrack(0, 0)
  if not tr then
    reaper.ShowConsoleMsg("[ERROR] Cannot create track\n")
    return
  end

  if template and template ~= "" then
    local tpl_data = read_all(template)
    local chunk = extract_first_track_chunk(tpl_data)
    if chunk then
      reaper.SetTrackStateChunk(tr, chunk, false)
      reaper.TrackList_AdjustWindows(false)
      reaper.UpdateArrange()
      reaper.ShowConsoleMsg("[SEQ] Template loaded, waiting for Kontakt...\n")
      -- Give Kontakt time to fully initialize
      os.execute("sleep 5")
    else
      reaper.ShowConsoleMsg("[ERROR] Invalid template\n")
    end
  end

  -- Render each MIDI sequentially
  local success = 0
  local skipped = 0

  for i, sample in ipairs(samples) do
    local midi_path = join(midi_dir, sample.midi)
    local wav_path = join(output_dir, sample.wav)

    -- Skip if already rendered
    if exists(wav_path) then
      skipped = skipped + 1
      goto continue
    end

    if not exists(midi_path) then
      reaper.ShowConsoleMsg(string.format("[%d/%d] SKIP (no MIDI): %s\n", i, #samples, sample.midi))
      goto continue
    end

    reaper.ShowConsoleMsg(string.format("[%d/%d] %s\n", i, #samples, sample.wav))

    -- Clear existing items
    reaper.Main_OnCommand(40289, 0) -- Unselect all items
    reaper.SetOnlyTrackSelected(tr)
    reaper.SetEditCurPos(0, false, false)

    -- Delete any existing items on track
    while reaper.CountTrackMediaItems(tr) > 0 do
      local item = reaper.GetTrackMediaItem(tr, 0)
      reaper.DeleteTrackMediaItem(tr, item)
    end

    -- Insert MIDI
    reaper.InsertMedia(midi_path, 0)

    local item_count = reaper.CountMediaItems(0)
    if item_count == 0 then
      reaper.ShowConsoleMsg("  [WARN] Insert failed\n")
      goto continue
    end

    local it = reaper.GetMediaItem(0, item_count - 1)
    if not it then
      reaper.ShowConsoleMsg("  [WARN] GetMediaItem failed\n")
      goto continue
    end

    set_time_from_item(it)
    set_render_out(wav_path)

    -- Render
    reaper.Main_OnCommand(42230, 0)

    if exists(wav_path) then
      success = success + 1
    end

    -- Clean up item
    reaper.DeleteTrackMediaItem(tr, it)

    ::continue::
  end

  -- Clean up
  reaper.DeleteTrack(tr)

  reaper.ShowConsoleMsg(string.format("\n[DONE] Rendered: %d, Skipped: %d, Total: %d\n", success, skipped, #samples))

  -- Quit
  reaper.Main_OnCommand(40004, 0)
end

main()
