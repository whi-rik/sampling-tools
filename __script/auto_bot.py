# gui_popup_bot.py
# - REAPER Query  → '아니요(&n)'
# - Kontakt 8     → OK/확인
# - MIDI Import   → OK/확인 or Enter
# - 클릭 발생할 때 txt 로그 파일로 기록

import win32gui
import win32con
import win32process
import win32api
import time
from datetime import datetime
import os

# ================================
# 설정
# ================================
LOG_PATH = r"C:\projects\RenderForge\logs\popup_bot.log"

# 로그 디렉토리 없으면 생성
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def log(msg):
    """파일 + 콘솔 동시에 출력"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ================================
# 공통 유틸
# ================================
def safe_get_text(hwnd):
    try:
        return win32gui.GetWindowText(hwnd)
    except:
        return ""


def safe_get_class(hwnd):
    try:
        return win32gui.GetClassName(hwnd)
    except:
        return ""


def enum_children(hwnd):
    children = []
    try:
        def callback(ch, param):
            children.append(ch)
            return True

        win32gui.EnumChildWindows(hwnd, callback, None)
    except:
        pass
    return children


def enum_windows():
    windows = []
    try:
        def callback(hwnd, param):
            windows.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
    except:
        pass
    return windows


def get_window_start_time(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            create_time, _, _, _ = win32process.GetProcessTimes(handle)
            return create_time.timestamp()
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return 0


def activate_window(hwnd):
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg_hwnd = win32gui.GetForegroundWindow()
        cur_thread = win32api.GetCurrentThreadId()
        try:
            fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
            tgt_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            fg_thread = tgt_thread = 0

        if fg_thread and tgt_thread and fg_thread != tgt_thread:
            win32process.AttachThreadInput(fg_thread, cur_thread, True)
            win32process.AttachThreadInput(tgt_thread, cur_thread, True)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetFocus(hwnd)
        finally:
            if fg_thread and tgt_thread and fg_thread != tgt_thread:
                win32process.AttachThreadInput(fg_thread, cur_thread, False)
                win32process.AttachThreadInput(tgt_thread, cur_thread, False)
        log(f"ACTIVATE hwnd={hwnd}")
    except Exception as e:
        log(f"ERROR ACTIVATE hwnd={hwnd}: {e}")


def click_bm(button_hwnd, label=""):
    try:
        win32gui.SendMessage(button_hwnd, win32con.BM_CLICK, 0, 0)
        log(f"CLICK({label}) via BM_CLICK → button_hwnd={button_hwnd}")
    except Exception as e:
        log(f"ERROR BM_CLICK({label}): {e}")


def click_wmcommand(dialog_hwnd, button_hwnd, label=""):
    try:
        ctrl_id = win32gui.GetDlgCtrlID(button_hwnd)
        win32gui.SendMessage(dialog_hwnd, win32con.WM_COMMAND, ctrl_id, button_hwnd)
        log(f"CLICK({label}) via WM_COMMAND → button_hwnd={button_hwnd}, id={ctrl_id}")
    except Exception as e:
        log(f"ERROR WM_COMMAND({label}): {e}")


def send_enter(hwnd, label="ENTER"):
    try:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        log(f"SEND {label} → hwnd={hwnd}")
    except Exception as e:
        log(f"ERROR sending {label}: {e}")


# ================================
# 핸들러들
# ================================
def handle_reaper_query(hwnd):
    log(f"DETECTED: REAPER Query hwnd={hwnd}")
    activate_window(hwnd)

    for ch in enum_children(hwnd):
        if safe_get_class(ch) != "Button":
            continue

        txt = safe_get_text(ch).strip().lower()
        log(f"   [RQ] Button: '{txt}'")

        if "아니요" in txt or txt.startswith("no"):
            click_bm(ch, label="REAPER_NO")
            return


def handle_kontakt(hwnd):
    log(f"DETECTED: Kontakt 8 hwnd={hwnd}")
    activate_window(hwnd)

    for ch in enum_children(hwnd):
        if safe_get_class(ch) != "Button":
            continue

        txt = safe_get_text(ch).strip().lower()
        log(f"   [KT] Button: '{txt}'")

        if txt == "ok" or "확인" in txt or "ok" in txt:
            click_wmcommand(hwnd, ch, label="KONTAKT_OK")
            return


def handle_midi_import(hwnd):
    log(f"DETECTED: MIDI File Import hwnd={hwnd}")
    activate_window(hwnd)

    ok_clicked = False

    for ch in enum_children(hwnd):
        if safe_get_class(ch) != "Button":
            continue

        txt = safe_get_text(ch).strip().lower()
        log(f"   [MI] Button: '{txt}'")

        if txt == "ok" or "확인" in txt or "ok" in txt:
            click_wmcommand(hwnd, ch, label="MIDI_IMPORT_OK")
            ok_clicked = True
            break

    if not ok_clicked:
        send_enter(hwnd, label="MIDI_IMPORT_ENTER")


# ================================
# 메인 루프
# ================================
def main():
    log("GUI Popup Bot started.")

    TARGET_MIDI_IMPORT_TITLE = "MIDI File Import"

    while True:
        handled = False
        windows = enum_windows()
        windows.sort(key=get_window_start_time, reverse=True)
        for hwnd in windows:
            if not win32gui.IsWindowVisible(hwnd):
                continue

            cls = safe_get_class(hwnd)
            title = safe_get_text(hwnd)

            if cls == "#32770" and title == "REAPER Query":
                handle_reaper_query(hwnd)
                handled = True

            elif cls == "#32770" and title == "Kontakt 8":
                handle_kontakt(hwnd)
                handled = True

            elif title == TARGET_MIDI_IMPORT_TITLE:
                handle_midi_import(hwnd)
                handled = True

        if handled:
            time.sleep(0.3)

        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user.")
