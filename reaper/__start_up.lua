-- __startup.lua
-- MIDI File Import 창이 뜨면 자동으로 OK(Enter) 누르기
-- ※ js_ReaScriptAPI 필수 (JS_Window_* 함수)

local TITLE = "MIDI File Import"      -- 다이얼로그 제목 그대로

local VK_RETURN = 0x0D                -- Enter 키 코드

local function auto_ok_midi_import()
  -- JS_ReaScriptAPI 없으면 그냥 종료
  if not reaper.APIExists("JS_Window_Find") then
    return
  end

  -- 제목으로 창 찾기 (case-sensitive, exact match)
  local hwnd = reaper.JS_Window_Find(TITLE, true)
  if hwnd then
    -- 포그라운드로 가져오고 Enter 키 보내기
    reaper.JS_Window_SetForeground(hwnd)
    -- WM_KEYDOWN / WM_KEYUP : VK_RETURN
    reaper.JS_WindowMessage_Post(hwnd, "WM_KEYDOWN", VK_RETURN, 0, 0, 0)
    reaper.JS_WindowMessage_Post(hwnd, "WM_KEYUP",   VK_RETURN, 0, 0, 0)
  end

  -- 계속 반복
  reaper.defer(auto_ok_midi_import)
end

auto_ok_midi_import()