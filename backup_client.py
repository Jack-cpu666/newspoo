import socketio
import time
import os
import io
import threading
import logging
from PIL import Image # Still needed for image processing
import mss          # Still needed for screen capture
import platform
import sys
import keyboard     # Still needed for local F2 press detection
import random       # Still needed for typing simulation

# --- Ctypes for Windows Low-Level Input ---
if platform.system() == "Windows":
    import ctypes
    import ctypes.wintypes as wintypes

    # Define necessary Windows structures and constants
    INPUT_MOUSE = 0; INPUT_KEYBOARD = 1; INPUT_HARDWARE = 2;
    KEYEVENTF_KEYDOWN = 0x0000; KEYEVENTF_EXTENDEDKEY = 0x0001; KEYEVENTF_KEYUP = 0x0002;
    KEYEVENTF_UNICODE = 0x0004; KEYEVENTF_SCANCODE = 0x0008; MAPVK_VK_TO_VSC = 0;
    MOUSEEVENTF_MOVE = 0x0001; MOUSEEVENTF_ABSOLUTE = 0x8000; MOUSEEVENTF_LEFTDOWN = 0x0002;
    MOUSEEVENTF_LEFTUP = 0x0004; MOUSEEVENTF_RIGHTDOWN = 0x0008; MOUSEEVENTF_RIGHTUP = 0x0010;
    MOUSEEVENTF_MIDDLEDOWN = 0x0020; MOUSEEVENTF_MIDDLEUP = 0x0040; MOUSEEVENTF_WHEEL = 0x0800;
    WHEEL_DELTA = 120;

    ULONG_PTR = ctypes.POINTER(wintypes.ULONG)
    class MOUSEINPUT(ctypes.Structure): _fields_ = (("dx", wintypes.LONG),("dy", wintypes.LONG),("mouseData", wintypes.DWORD),("dwFlags", wintypes.DWORD),("time", wintypes.DWORD),("dwExtraInfo", ULONG_PTR))
    class KEYBDINPUT(ctypes.Structure): _fields_ = (("wVk", wintypes.WORD),("wScan", wintypes.WORD),("dwFlags", wintypes.DWORD),("time", wintypes.DWORD),("dwExtraInfo", ULONG_PTR))
    class HARDWAREINPUT(ctypes.Structure): _fields_ = (("uMsg", wintypes.DWORD),("wParamL", wintypes.WORD),("wParamH", wintypes.WORD))
    class _INPUT_UNION(ctypes.Union): _fields_ = (("mi", MOUSEINPUT),("ki", KEYBDINPUT),("hi", HARDWAREINPUT))
    class INPUT(ctypes.Structure): _fields_ = (("type", wintypes.DWORD), ("union", _INPUT_UNION))

    SendInput = ctypes.windll.user32.SendInput
    SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int); SendInput.restype = wintypes.UINT
    GetSystemMetrics = ctypes.windll.user32.GetSystemMetrics; SM_CXSCREEN = 0; SM_CYSCREEN = 1;
    MapVirtualKeyA = ctypes.windll.user32.MapVirtualKeyA
    MapVirtualKeyA.argtypes = (wintypes.UINT, wintypes.UINT); MapVirtualKeyA.restype = wintypes.UINT

    def _create_input(input_type, input_union):
        inp = INPUT(); inp.type = wintypes.DWORD(input_type); inp.union = input_union
        if input_type == INPUT_MOUSE: inp.union.mi.dwExtraInfo = ULONG_PTR(wintypes.ULONG(0))
        elif input_type == INPUT_KEYBOARD: inp.union.ki.dwExtraInfo = ULONG_PTR(wintypes.ULONG(0))
        return inp
    def _send_inputs(inputs):
        nInputs = len(inputs); LPINPUT = INPUT * nInputs; pInputs = LPINPUT(*inputs)
        cbSize = ctypes.c_int(ctypes.sizeof(INPUT)); return SendInput(nInputs, pInputs, cbSize)

    def press_key_ctypes(vk_code):
        ki = KEYBDINPUT(wVk=wintypes.WORD(vk_code), wScan=0, dwFlags=KEYEVENTF_KEYDOWN, time=0)
        inp = _create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=ki)); _send_inputs([inp])
    def release_key_ctypes(vk_code):
        ki = KEYBDINPUT(wVk=wintypes.WORD(vk_code), wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0)
        inp = _create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=ki)); _send_inputs([inp])
    def type_char_ctypes(char):
        char_code = ord(char)
        ki_down = KEYBDINPUT(wVk=0, wScan=wintypes.WORD(char_code), dwFlags=KEYEVENTF_UNICODE, time=0)
        inp_down = _create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=ki_down))
        ki_up = KEYBDINPUT(wVk=0, wScan=wintypes.WORD(char_code), dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0)
        inp_up = _create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=ki_up)); _send_inputs([inp_down, inp_up])

    def move_mouse_ctypes(x, y, absolute=True):
        flags = MOUSEEVENTF_MOVE; dx_val, dy_val = x, y
        if absolute:
            screen_width = GetSystemMetrics(SM_CXSCREEN); screen_height = GetSystemMetrics(SM_CYSCREEN)
            if screen_width == 0 or screen_height == 0: logger.error("CTYPES_MOUSE_MOVE: Failed screen metrics."); return
            dx_val = int(x * 65535 / screen_width); dy_val = int(y * 65535 / screen_height); flags |= MOUSEEVENTF_ABSOLUTE
        mi = MOUSEINPUT(dx=wintypes.LONG(dx_val), dy=wintypes.LONG(dy_val), mouseData=0, dwFlags=wintypes.DWORD(flags), time=0)
        inp = _create_input(INPUT_MOUSE, _INPUT_UNION(mi=mi)); _send_inputs([inp])
    def click_mouse_ctypes(button='left'):
        down_flag, up_flag = 0, 0
        if button == 'left': down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        elif button == 'right': down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        elif button == 'middle': down_flag, up_flag = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
        else: logger.warning(f"CTYPES_CLICK: Unknown button '{button}'"); return
        mi_down = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=wintypes.DWORD(down_flag), time=0); inp_down = _create_input(INPUT_MOUSE, _INPUT_UNION(mi=mi_down))
        mi_up = MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=wintypes.DWORD(up_flag), time=0); inp_up = _create_input(INPUT_MOUSE, _INPUT_UNION(mi=mi_up))
        _send_inputs([inp_down, inp_up])
    def scroll_mouse_ctypes(amount):
        send_input_amount = int(amount * -WHEEL_DELTA)
        mi = MOUSEINPUT(dx=0, dy=0, mouseData=wintypes.DWORD(send_input_amount), dwFlags=MOUSEEVENTF_WHEEL, time=0)
        inp = _create_input(INPUT_MOUSE, _INPUT_UNION(mi=mi)); _send_inputs([inp])

    VK_LBUTTON = 0x01; VK_RBUTTON = 0x02; VK_MBUTTON = 0x04; VK_BACK = 0x08; VK_TAB = 0x09;
    VK_RETURN = 0x0D; VK_SHIFT = 0x10; VK_CONTROL = 0x11; VK_MENU = 0x12; VK_PAUSE = 0x13;
    VK_CAPITAL = 0x14; VK_ESCAPE = 0x1B; VK_SPACE = 0x20; VK_PRIOR = 0x21; VK_NEXT = 0x22;
    VK_END = 0x23; VK_HOME = 0x24; VK_LEFT = 0x25; VK_UP = 0x26; VK_RIGHT = 0x27; VK_DOWN = 0x28;
    VK_SELECT = 0x29; VK_PRINT = 0x2A; VK_EXECUTE = 0x2B; VK_SNAPSHOT = 0x2C; VK_INSERT = 0x2D;
    VK_DELETE = 0x2E; VK_HELP = 0x2F; VK_LWIN = 0x5B; VK_RWIN = 0x5C; VK_APPS = 0x5D; VK_SLEEP = 0x5F;
    VK_F1 = 0x70; VK_F2 = 0x71; VK_F3 = 0x72; VK_F4 = 0x73; VK_F5 = 0x74; VK_F6 = 0x75; VK_F7 = 0x76;
    VK_F8 = 0x77; VK_F9 = 0x78; VK_F10 = 0x79; VK_F11 = 0x7A; VK_F12 = 0x7B; VK_NUMLOCK = 0x90;
    VK_SCROLL = 0x91; VK_LSHIFT = 0xA0; VK_RSHIFT = 0xA1; VK_LCONTROL = 0xA2; VK_RCONTROL = 0xA3;
    VK_LMENU = 0xA4; VK_RMENU = 0xA5;

    CTYPES_VK_MAP = { # Map from common lowercase names to VK codes
        'backspace': VK_BACK, 'tab': VK_TAB, 'enter': VK_RETURN, 'return': VK_RETURN,
        'shift': VK_SHIFT, 'ctrl': VK_CONTROL, 'alt': VK_MENU, 'menu': VK_MENU,
        'pause': VK_PAUSE, 'capslock': VK_CAPITAL, 'esc': VK_ESCAPE, 'escape': VK_ESCAPE,
        'space': VK_SPACE, 'pageup': VK_PRIOR, 'pagedown': VK_NEXT,
        'end': VK_END, 'home': VK_HOME, 'left': VK_LEFT, 'up': VK_UP,
        'right': VK_RIGHT, 'down': VK_DOWN, 'select': VK_SELECT, 'print': VK_PRINT,
        'execute': VK_EXECUTE, 'printscreen': VK_SNAPSHOT, 'insert': VK_INSERT,
        'delete': VK_DELETE, 'del': VK_DELETE, 'help': VK_HELP,
        'win': VK_LWIN, 'lwin': VK_LWIN, 'rwin': VK_RWIN, 'apps': VK_APPS, 'sleep': VK_SLEEP,
        'f1': VK_F1, 'f2': VK_F2, 'f3': VK_F3, 'f4': VK_F4, 'f5': VK_F5, 'f6': VK_F6,
        'f7': VK_F7, 'f8': VK_F8, 'f9': VK_F9, 'f10': VK_F10, 'f11': VK_F11, 'f12': VK_F12,
        'numlock': VK_NUMLOCK, 'scrolllock': VK_SCROLL,
        'lshift': VK_LSHIFT, 'rshift': VK_RSHIFT,
        'lctrl': VK_LCONTROL, 'rctrl': VK_RCONTROL,
        'lalt': VK_LMENU, 'ralt': VK_RMENU,
        'command': VK_LWIN # Map mac command to windows key
    }
else: # Non-Windows systems stub functions
    logger.warning("Non-Windows system detected. Input control functionality will be disabled.")
    def press_key_ctypes(vk_code_name): pass # No-op
    def release_key_ctypes(vk_code_name): pass # No-op
    def type_char_ctypes(char): pass # No-op
    def move_mouse_ctypes(x,y,absolute=True): pass # No-op
    def click_mouse_ctypes(button='left'): pass # No-op
    def scroll_mouse_ctypes(amount): pass # No-op
    CTYPES_VK_MAP = {} # Empty map

# --- Logging Setup ---
log_format = '%(asctime)s - %(threadName)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration ---
SERVER_URL = os.environ.get('REMOTE_SERVER_URL', 'https://newspoogunicorn-worker-class-eventlet-w.onrender.com')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')
CLIENT_TARGET_FPS = int(os.environ.get('CLIENT_TARGET_FPS', 7))
JPEG_QUALITY = int(os.environ.get('JPEG_QUALITY', 65))
CAPTURE_MONITOR_INDEX = int(os.environ.get('CAPTURE_MONITOR_INDEX', 1))
LIST_MONITORS_ONLY = os.environ.get('LIST_MONITORS_ONLY', 'false').lower() == 'true'

# --- Text Injection Globals & Configuration ---
text_to_inject_globally = ""
remaining_text_to_type = ""
is_typing_active = False
is_typing_paused = False
typing_thread_obj: threading.Thread | None = None
typing_stop_event = threading.Event()
BASE_TYPING_INTERVAL = 0.12
TYPING_INTERVAL_VARIATION = 0.06
MISTAKE_PROBABILITY = 0.025
BACKSPACE_PAUSE_MIN = 0.15; BACKSPACE_PAUSE_MAX = 0.4
CORRECTION_PAUSE_MIN = 0.1; CORRECTION_PAUSE_MAX = 0.3
local_key_listener_stop_event = threading.Event()

# --- Global State Variables ---
sio = socketio.Client(reconnection_attempts=10, reconnection_delay=5, logger=False, engineio_logger=False)
is_registered = False
screen_capture_stop_event = threading.Event()
capture_thread_obj: threading.Thread | None = None
local_key_listener_thread_obj: threading.Thread | None = None
selected_monitor_details: dict | None = None

# --- JavaScript Key Name to Common Name Mapping ---
# Map from JS Event Key Name -> common lowercase name (used in CTYPES_VK_MAP)
JS_TO_COMMON_KEY_MAP = {
    "Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "win", # Map Meta to 'win' for VK map lookup
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Delete": "delete",
    "Tab": "tab", " ": "space",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6",
    "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
    "PageUp": "pageup", "PageDown": "pagedown", "Home": "home", "End": "end",
    "Insert": "insert", "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
    "PrintScreen": "printscreen",
}
def map_js_key_to_common_name(key_name_from_js: str) -> str | None:
    """Maps JS event.key to common lowercase name used for CTYPES_VK_MAP."""
    if key_name_from_js in JS_TO_COMMON_KEY_MAP:
        return JS_TO_COMMON_KEY_MAP[key_name_from_js]
    if len(key_name_from_js) == 1:
        return key_name_from_js.lower() # Use lowercase single chars for VK map lookup if needed later
    # If not in map and not single char, return lowercase version as a guess
    return key_name_from_js.lower()

def list_available_monitors(): # (Same as before)
    logger.info("---- Available Monitors ----")
    with mss.mss() as sct:
        monitors = sct.monitors
        if not monitors: logger.info("No monitors found by mss."); return
        for i, monitor in enumerate(monitors):
            logger.info(f"Monitor Index: {i}, Dim: {monitor['width']}x{monitor['height']}, Pos: left={monitor['left']}, top={monitor['top']}")
            if i == 0: logger.info("  (Usually combined virtual screen)")
            elif i == 1: logger.info("  (Often primary monitor)")
    logger.info(f"--------------------------\nSet CAPTURE_MONITOR_INDEX (current: {CAPTURE_MONITOR_INDEX})")

# --- Enhanced Typing Function (Using ctypes) ---
# (Identical to the function provided in the previous step's corrected code)
def execute_typing_task():
    global remaining_text_to_type, is_typing_active, is_typing_paused, typing_stop_event
    if platform.system() != "Windows":
        logger.error("TYPING_TASK_ERROR: ctypes-based typing is only supported on Windows."); is_typing_active=False; return

    logger.info(f"TYPING_TASK_START (ctypes): Typing {len(remaining_text_to_type)} chars.")
    text_buffer = list(remaining_text_to_type); remaining_text_to_type = ""

    def get_nearby_char(char): # Simple nearby char logic
        if not char.isalnum(): return char
        rows = ["qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890"]
        char_lower = char.lower()
        for row in rows:
            if char_lower in row:
                idx = row.find(char_lower); possible = []
                if idx > 0: possible.append(row[idx-1])
                if idx < len(row) - 1: possible.append(row[idx+1])
                if possible:
                    chosen = random.choice(possible)
                    return chosen.upper() if char.isupper() else chosen
        return random.choice("aeiou") # Fallback

    try:
        idx = 0
        while idx < len(text_buffer):
            if typing_stop_event.is_set():
                logger.info("TYPING_TASK_STOP_EVENT (ctypes)"); remaining_text_to_type="".join(text_buffer[idx:]); break
            if is_typing_paused: time.sleep(0.1); continue

            char_to_type = text_buffer[idx]
            made_mistake_and_corrected = False

            if random.random() < MISTAKE_PROBABILITY and char_to_type.isalnum() and char_to_type != ' ':
                mistake_type = random.choice(['wrong_char_corrected', 'extra_char_corrected'])
                if mistake_type == 'wrong_char_corrected':
                    wrong_char = get_nearby_char(char_to_type)
                    if wrong_char != char_to_type:
                        logger.debug(f"TYPING_MISTAKE (WRONG_CHAR_CTYPES): Intended '{char_to_type}', typed '{wrong_char}'")
                        type_char_ctypes(wrong_char) # Type mistake
                        time.sleep(random.uniform(BACKSPACE_PAUSE_MIN, BACKSPACE_PAUSE_MAX))
                        press_key_ctypes(VK_BACK); release_key_ctypes(VK_BACK) # Backspace
                        time.sleep(random.uniform(CORRECTION_PAUSE_MIN, CORRECTION_PAUSE_MAX))
                        type_char_ctypes(char_to_type) # Type correct char immediately
                        made_mistake_and_corrected = True
                elif mistake_type == 'extra_char_corrected':
                    extra_wrong_char = get_nearby_char(char_to_type)
                    if extra_wrong_char == char_to_type and char_to_type.isalpha(): extra_wrong_char = random.choice('estnriola')
                    logger.debug(f"TYPING_MISTAKE (EXTRA_CHAR_CTYPES): Intended '{char_to_type}', typed '{char_to_type}{extra_wrong_char}'")
                    type_char_ctypes(char_to_type) # Type correct
                    time.sleep(random.uniform(0.01, 0.05))
                    type_char_ctypes(extra_wrong_char) # Type extra wrong char
                    time.sleep(random.uniform(BACKSPACE_PAUSE_MIN, BACKSPACE_PAUSE_MAX))
                    press_key_ctypes(VK_BACK); release_key_ctypes(VK_BACK) # Backspace extra
                    time.sleep(random.uniform(CORRECTION_PAUSE_MIN, CORRECTION_PAUSE_MAX))
                    made_mistake_and_corrected = True

            if not made_mistake_and_corrected:
                # Type normally if no mistake or correction occurred
                if char_to_type == '\n': press_key_ctypes(VK_RETURN); release_key_ctypes(VK_RETURN)
                elif char_to_type == '\t': press_key_ctypes(VK_TAB); release_key_ctypes(VK_TAB)
                else: type_char_ctypes(char_to_type)

            current_interval = BASE_TYPING_INTERVAL + random.uniform(-TYPING_INTERVAL_VARIATION, TYPING_INTERVAL_VARIATION)
            time.sleep(max(0.02, current_interval))
            idx += 1

        if idx == len(text_buffer): logger.info("TYPING_TASK_COMPLETE (ctypes).")
    except Exception as e:
        logger.error(f"TYPING_TASK_ERROR (ctypes): {e}"); remaining_text_to_type="".join(text_buffer[idx:])
    finally:
        is_typing_active = False


# --- Local F2 Key Press Handler (Manages Typing Task) ---
# (This function on_local_f2_press remains THE SAME as the previous version)
def on_local_f2_press():
    global is_typing_active, is_typing_paused, remaining_text_to_type, text_to_inject_globally
    global typing_thread_obj, typing_stop_event
    logger.info(f"LOCAL_F2_PRESS: F2 pressed. Active: {is_typing_active}, Paused: {is_typing_paused}")
    if not is_typing_active:
        if not text_to_inject_globally: logger.info("LOCAL_F2_PRESS: No text set."); return
        is_typing_active = True; is_typing_paused = False; typing_stop_event.clear()
        remaining_text_to_type = text_to_inject_globally
        if typing_thread_obj and typing_thread_obj.is_alive():
            logger.warning("LOCAL_F2_PRESS: Previous typing thread alive? Stopping."); typing_stop_event.set(); typing_thread_obj.join(timeout=0.5)
            if typing_thread_obj.is_alive(): logger.error("LOCAL_F2_PRESS: Could not stop previous thread."); is_typing_active=False; return
        typing_thread_obj = threading.Thread(target=execute_typing_task, name="TypingTaskThread", daemon=True); typing_thread_obj.start()
        logger.info("LOCAL_F2_PRESS: Started new typing task.")
    else:
        is_typing_paused = not is_typing_paused
        logger.info(f"LOCAL_F2_PRESS: Typing {'PAUSED' if is_typing_paused else 'RESUMED'}.")
        if not is_typing_paused and not (typing_thread_obj and typing_thread_obj.is_alive()):
            logger.warning("LOCAL_F2_PRESS_RESUME_WARN: Typing thread not alive.")
            if remaining_text_to_type:
                logger.info("LOCAL_F2_PRESS_RESUME_RESTART: Restarting task with remaining text.")
                is_typing_active = True; is_typing_paused = False; typing_stop_event.clear()
                typing_thread_obj = threading.Thread(target=execute_typing_task, name="TypingTaskThread", daemon=True); typing_thread_obj.start()
            else: is_typing_active = False

# --- Local Keyboard Listener Thread Function (same as before) ---
def local_key_listener_loop():
    logger.info("LOCAL_KEY_LISTENER_THREAD_START: Listening for local F2 presses.")
    try:
        keyboard.add_hotkey('f2', on_local_f2_press, suppress=False)
        local_key_listener_stop_event.wait() # Block until stop event is set
    except ImportError: logger.error("LOCAL_KEY_LISTENER_THREAD_ERROR: 'keyboard' library missing or permissions issue.")
    except Exception as e: logger.error(f"LOCAL_KEY_LISTENER_THREAD_ERROR: {e}", exc_info=False)
    finally:
        try: keyboard.remove_hotkey('f2')
        except: pass
        try: keyboard.unhook_all()
        except: pass
        logger.info("LOCAL_KEY_LISTENER_THREAD_STOP: Stopped.")

# --- SocketIO Event Handlers ---
@sio.event
def connect(): sio.emit('register_client', {'token': ACCESS_PASSWORD})
@sio.event
def connect_error(data): logger.error(f"CLIENT_SOCKET_CONNECT_ERROR: {data}"); global is_registered; is_registered=False; screen_capture_stop_event.set();
@sio.event
def disconnect(reason=None): logger.warning(f"CLIENT_SOCKET_DISCONNECT: {reason or 'N/A'}"); global is_registered; is_registered=False; screen_capture_stop_event.set();

@sio.on('registration_success')
def on_registration_success():
    global is_registered, capture_thread_obj, local_key_listener_thread_obj, local_key_listener_stop_event
    if is_registered: return
    is_registered = True; screen_capture_stop_event.clear(); local_key_listener_stop_event.clear()
    logger.info("CLIENT_REG_SUCCESS.")
    if not (capture_thread_obj and capture_thread_obj.is_alive()):
        capture_thread_obj = threading.Thread(target=screen_capture_loop, name="ScreenCaptureThread", daemon=True); capture_thread_obj.start()
    if not (local_key_listener_thread_obj and local_key_listener_thread_obj.is_alive()):
        local_key_listener_thread_obj = threading.Thread(target=local_key_listener_loop, name="LocalKeyListenerThread", daemon=True); local_key_listener_thread_obj.start()

@sio.on('registration_fail')
def on_registration_fail(data):
    logger.error(f"CLIENT_REG_FAIL: {data.get('message')}"); global is_registered; is_registered=False; screen_capture_stop_event.set(); local_key_listener_stop_event.set(); sio.disconnect();

@sio.on('receive_injection_text')
def on_receive_injection_text(data):
    global text_to_inject_globally, remaining_text_to_type, is_typing_active, typing_stop_event
    new_text = data.get('text', "")
    if is_typing_active:
        logger.warning("CLIENT_INJECT_TEXT_SET: New text received while typing active. Stopping current task."); typing_stop_event.set();
        if typing_thread_obj and typing_thread_obj.is_alive(): typing_thread_obj.join(timeout=0.5)
        is_typing_active = False; is_typing_paused = False; remaining_text_to_type = ""
    text_to_inject_globally = new_text
    logger.info(f"CLIENT_INJECT_TEXT_SET: Text for local F2 updated: '{text_to_inject_globally[:30]}...'")

@sio.on('command')
def on_command(data: dict): # USES CTYPES FOR INPUT ON WINDOWS
    global selected_monitor_details
    if not is_registered: return

    if platform.system() != "Windows": return # Only process commands on Windows

    action = data.get('action')
    abs_x, abs_y = data.get('x'), data.get('y')
    if selected_monitor_details and abs_x is not None and abs_y is not None:
        abs_x += selected_monitor_details.get('left', 0); abs_y += selected_monitor_details.get('top', 0)

    try:
        if action == 'keydown':
            key_name_js = data['key']
            if key_name_js == "F2": return # F2 from server does nothing for typing
            common_key_name = map_js_key_to_common_name(key_name_js)
            if common_key_name:
                vk_code = CTYPES_VK_MAP.get(common_key_name) # Lookup using common name
                if vk_code: press_key_ctypes(vk_code)
                # else: logger.debug(f"CTYPES_KEYDOWN: No VK_MAP for '{common_key_name}'.")
        elif action == 'keyup':
            common_key_name = map_js_key_to_common_name(data['key'])
            if common_key_name:
                vk_code = CTYPES_VK_MAP.get(common_key_name)
                if vk_code: release_key_ctypes(vk_code)
                # else: logger.debug(f"CTYPES_KEYUP: No VK_MAP for '{common_key_name}'.")
        elif action == 'move' and abs_x is not None and abs_y is not None:
            move_mouse_ctypes(abs_x, abs_y, absolute=True)
        elif action == 'click' and abs_x is not None and abs_y is not None:
            move_mouse_ctypes(abs_x, abs_y, absolute=True)
            time.sleep(0.02) # Small delay between move and click
            click_mouse_ctypes(button=data.get('button','left').lower())
        elif action == 'scroll':
            scroll_amount = data.get('dy', 0) # Server dy>0 is scroll down
            if scroll_amount != 0: scroll_mouse_ctypes(scroll_amount)
    except Exception as e: logger.error(f"CLIENT_CMD_ERROR (ctypes): Processing {data}: {e}", exc_info=True)


# --- Screen Capture Loop ---
# (Identical to previous version)
def screen_capture_loop():
    global selected_monitor_details
    logger.info(f"CAPTURE_THREAD_START: FPS: {CLIENT_TARGET_FPS}, Quality: {JPEG_QUALITY}, Monitor: {CAPTURE_MONITOR_INDEX}")
    with mss.mss() as sct:
        monitors = sct.monitors
        if not monitors: logger.error("CAPTURE_THREAD_ERROR: No mss monitors. Exiting."); return
        actual_monitor_idx = CAPTURE_MONITOR_INDEX if CAPTURE_MONITOR_INDEX < len(monitors) else 0
        if CAPTURE_MONITOR_INDEX >= len(monitors): logger.warning(f"Monitor index {CAPTURE_MONITOR_INDEX} out of range, using {actual_monitor_idx}.")
        monitor_definition = monitors[actual_monitor_idx]; selected_monitor_details = monitor_definition
        logger.info(f"CAPTURE_THREAD_MONITOR: Capturing Index {actual_monitor_idx}: {monitor_definition}")
        last_frame_send_time = time.time(); target_interval = 1.0 / CLIENT_TARGET_FPS
        while not screen_capture_stop_event.is_set() and is_registered and sio.connected:
            capture_start_time = time.time()
            if (capture_start_time - last_frame_send_time) < target_interval: time.sleep(target_interval - (capture_start_time - last_frame_send_time))
            try:
                sct_img = sct.grab(monitor_definition)
                img = Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb, "raw", "RGB")
                buffer = io.BytesIO(); img.save(buffer, format="JPEG", quality=JPEG_QUALITY); jpeg_bytes = buffer.getvalue()
                if jpeg_bytes:
                    try: sio.emit('screen_data_bytes', jpeg_bytes); last_frame_send_time = time.time()
                    except Exception as e: logger.error(f"CAPTURE_THREAD_EMIT_ERROR: {e}"); time.sleep(1)
            except Exception as e: logger.error(f"CAPTURE_THREAD_UNEXPECTED_ERROR: {e}"); time.sleep(0.1)
    logger.info("CAPTURE_THREAD_STOP"); selected_monitor_details = None


# --- Main Execution Block ---
# (Identical to previous version)
def main():
    threading.current_thread().name = "MainThread"
    if LIST_MONITORS_ONLY: list_available_monitors(); return
    logger.info(f"Starting Remote Client (ctypes-only). Server: {SERVER_URL}, FPS: {CLIENT_TARGET_FPS}, Quality: {JPEG_QUALITY}, Monitor: {CAPTURE_MONITOR_INDEX}")
    if ACCESS_PASSWORD == '1': logger.warning("USING DEFAULT CLIENT ACCESS PASSWORD '1'!")
    global capture_thread_obj, local_key_listener_thread_obj, typing_thread_obj
    try:
        sio.connect(SERVER_URL, transports=['websocket'], wait_timeout=20)
        sio.wait()
    except socketio.exceptions.ConnectionError as e: logger.critical(f"CLIENT_MAIN_CONNECTION_ERROR: {SERVER_URL}: {e}")
    except KeyboardInterrupt: logger.info("CLIENT_MAIN_SHUTDOWN_KEYBOARD.")
    finally:
        logger.info("CLIENT_MAIN_SHUTDOWN_SEQ: Initiating...")
        global is_registered; is_registered = False
        screen_capture_stop_event.set(); local_key_listener_stop_event.set(); typing_stop_event.set()
        threads_to_join = []
        if capture_thread_obj and capture_thread_obj.is_alive(): threads_to_join.append(("Capture", capture_thread_obj))
        if local_key_listener_thread_obj and local_key_listener_thread_obj.is_alive(): threads_to_join.append(("Key Listener", local_key_listener_thread_obj))
        if typing_thread_obj and typing_thread_obj.is_alive(): threads_to_join.append(("Typing Task", typing_thread_obj))
        for name, thread in threads_to_join:
            logger.info(f"CLIENT_MAIN_SHUTDOWN_SEQ: Waiting for {name} thread...")
            thread.join(timeout=2.0)
            if thread.is_alive(): logger.warning(f"CLIENT_MAIN_SHUTDOWN_WARN: {name} thread did not exit cleanly.")
        if sio.connected: logger.info("CLIENT_MAIN_SHUTDOWN_SEQ: Disconnecting..."); sio.disconnect()
        logger.info("CLIENT_MAIN_SHUTDOWN_COMPLETE.")


if __name__ == '__main__':
    main()
