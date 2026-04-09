-- Render a single job using environment variables provided by the Python worker.
-- Expected env:
--   RENDER_MIDI_PATH        : absolute path to MIDI file
--   RENDER_WAV_PATH         : absolute path to output WAV
--   RENDER_TEMPLATE_PATH    : absolute path to REAPER TrackTemplate
--   RENDER_JOB_FILE         : optional; path to job metadata (for debugging)
-- Sample rate is fixed to 48000.

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

local function split_parts(path)
  local t = {}
  for part in path:gmatch("([^/\\]+)") do
    t[#t+1] = part
  end
  return t
end

local function join(a, b)
  if not a or a == "" then return b end
  local last = a:sub(-1)
  if last == "\\" or last == "/" then
    return a .. b
  else
    return a .. "\\" .. b
  end
end

local function resolve_template_path(tpl)
  if not tpl or tpl == "" then return nil end
  if exists(tpl) then return tpl end

  local res_base = reaper.GetResourcePath() or ""
  if res_base == "" then return tpl end

  local clean = tpl:gsub("^[/\\]+", "")
  local parts = split_parts(clean)
  local idx_reaper = nil
  for i, p in ipairs(parts) do
    if p:lower() == "reaper" then
      idx_reaper = i
      break
    end
  end

  local rel = clean
  if idx_reaper then
    rel = table.concat(parts, "\\", idx_reaper + 1)
  end

  -- If path starts with AppData\Roaming\REAPER\..., drop everything up to REAPER
  local candidate = join(res_base, rel)
  if exists(candidate) then
    return candidate
  end

  -- Last fallback: treat as relative to TrackTemplates under resource path
  local track_tpl = join(res_base, "TrackTemplates")
  candidate = join(track_tpl, rel)
  if exists(candidate) then
    return candidate
  end

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

local function load_template_chunk(path)
  if not exists(path) then return nil, ("template not found: " .. tostring(path)) end
  local chunk = extract_first_track_chunk(read_all(path))
  if not chunk then return nil, ("invalid track template: " .. tostring(path)) end
  return chunk
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

local function do_render()
  -- Render using most recent settings
  reaper.Main_OnCommand(42230, 0)
end

local function get_script_dir()
  local info = debug.getinfo(1, "S")
  local src = info.source:match("@(.+)")
  if src then return src:match("^(.*)[/\\]") end
  return nil
end

local function launch_auto_ok()
  -- Skip if explicitly disabled (default: enabled)
  local auto_ok = os.getenv("RENDER_AUTO_OK")
  if auto_ok == "0" then return end

  local dir = get_script_dir()
  if not dir then return end
  local path = dir .. "\\" .. "__start_up.lua"
  if not exists(path) then
    path = dir .. "/" .. "__start_up.lua"
    if not exists(path) then return end
  end
  -- AddRemoveReaScript is idempotent for the same path (returns existing cmd ID)
  local cmd_id = reaper.AddRemoveReaScript(true, 0, path, true)
  if cmd_id and cmd_id > 0 then
    reaper.Main_OnCommand(cmd_id, 0)
  end
end

local function main()
  launch_auto_ok()

  local midi_path   = os.getenv("RENDER_MIDI_PATH") or ""
  local wav_path    = os.getenv("RENDER_WAV_PATH") or ""
  local template    = resolve_template_path(os.getenv("RENDER_TEMPLATE_PATH") or "")

  if midi_path == "" or wav_path == "" then
    reaper.ShowConsoleMsg("[ERROR] Missing RENDER_MIDI_PATH or RENDER_WAV_PATH\n")
    return
  end
  if not exists(midi_path) then
    reaper.ShowConsoleMsg("[ERROR] MIDI not found: " .. midi_path .. "\n")
    return
  end
  if template == "" then
    reaper.ShowConsoleMsg("[WARN] No template path provided; track will be empty\n")
  end

  reaper.ShowConsoleMsg(string.format("[JOB] midi=%s\n", midi_path))
  reaper.ShowConsoleMsg(string.format("[JOB] wav=%s\n", wav_path))
  if template ~= "" then
    reaper.ShowConsoleMsg(string.format("[JOB] template=%s\n", template))
  end

  -- New project & sample rate 48000
  reaper.Main_OnCommand(40023, 0)
  reaper.GetSetProjectInfo(0, "PROJECT_SRATE_USE", 1, true)
  reaper.GetSetProjectInfo(0, "PROJECT_SRATE", 48000, true)

  mkdir_p(dirname(wav_path))

  reaper.InsertTrackAtIndex(0, true)
  local tr = reaper.GetTrack(0, 0)
  if not tr then
    reaper.ShowConsoleMsg("[ERROR] cannot create track\n")
    return
  end

  if template ~= "" then
    local chunk, err = load_template_chunk(template)
    if not chunk then
      reaper.ShowConsoleMsg("[ERROR] template load failed: " .. (err or "") .. "\n")
      reaper.DeleteTrack(tr)
      return
    end
    reaper.SetTrackStateChunk(tr, chunk, false)
    reaper.TrackList_AdjustWindows(false)
    reaper.UpdateArrange()
  end

  reaper.Main_OnCommand(40289, 0) -- Unselect all items
  reaper.SetOnlyTrackSelected(tr)
  reaper.SetEditCurPos(0, false, false)

  reaper.InsertMedia(midi_path, 0)

  local item_count = reaper.CountMediaItems(0)
  if item_count == 0 then
    reaper.ShowConsoleMsg("[ERROR] insert failed\n")
    reaper.DeleteTrack(tr)
    return
  end

  local it = reaper.GetMediaItem(0, item_count - 1)
  if not it then
    reaper.ShowConsoleMsg("[ERROR] GetMediaItem failed\n")
    reaper.DeleteTrack(tr)
    return
  end

  set_time_from_item(it)
  set_render_out(wav_path)

  do_render()

  reaper.DeleteTrackMediaItem(tr, it)
  reaper.DeleteTrack(tr)

  reaper.ShowConsoleMsg("[DONE] Render complete\n")

  -- Ensure REAPER exits after script finishes
  reaper.Main_OnCommand(40004, 0) -- File: Quit REAPER
end

main()
