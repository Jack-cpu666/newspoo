import socketio
import time
import os
import io
import threading
import logging # Using Python's logging module
from PIL import Image
import mss
import pyautogui
import platform

# --- Logging Setup ---
log_format = '%(asctime)s - %(threadName)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration ---
SERVER_URL = os.environ.get('REMOTE_SERVER_URL', 'https://your-render-app-name.onrender.com') # REPLACE!
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1') # MUST MATCH SERVER
CLIENT_TARGET_FPS = int(os.environ.get('CLIENT_TARGET_FPS', 2)) # DRASTICALLY REDUCED FPS FOR TESTING
JPEG_QUALITY = int(os.environ.get('JPEG_QUALITY', 40)) # REDUCED QUALITY FOR TESTING
SCROLL_SENSITIVITY_VERTICAL = 20
SCROLL_SENSITIVITY_HORIZONTAL = 20

# --- Global State Variables ---
sio = socketio.Client(reconnection_attempts=10, reconnection_delay=5, logger=True, engineio_logger=True)
is_registered = False
screen_capture_stop_event = threading.Event()
capture_thread_obj: threading.Thread | None = None

# --- PyAutoGUI Configuration & Key Mapping (same as before) ---
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0
PYAUTOGUI_SPECIAL_KEYS_MAP = {
    "Control": "ctrl", "Shift": "shift", "Alt": "alt",
    "Meta": "command" if platform.system() == "Darwin" else "win",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Delete": "delete",
    "Tab": "tab", " ": "space",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6",
    "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
    "PageUp": "pageup", "PageDown": "pagedown", "Home": "home", "End": "end",
    "Insert": "insert", "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
    "PrintScreen": "printscreen",
}
def map_key_to_pyautogui(key_name_from_js: str) -> str | None:
    if key_name_from_js in PYAUTOGUI_SPECIAL_KEYS_MAP:
        return PYAUTOGUI_SPECIAL_KEYS_MAP[key_name_from_js]
    if len(key_name_from_js) == 1: return key_name_from_js
    lower_key = key_name_from_js.lower()
    if lower_key in pyautogui.KEY_NAMES: return lower_key
    logger.warning(f"Unmapped key: '{key_name_from_js}'.")
    return key_name_from_js

# --- SocketIO Event Handlers ---
@sio.event
def connect():
    logger.info(f"CLIENT_SOCKET_CONNECT: Successfully connected to server. SID: {sio.sid}. Attempting registration.")
    try:
        sio.emit('register_client', {'token': ACCESS_PASSWORD})
    except Exception as e:
        logger.error(f"CLIENT_SOCKET_CONNECT: Error emitting register_client: {e}")

@sio.event
def connect_error(data):
    logger.error(f"CLIENT_SOCKET_CONNECT_ERROR: Connection failed: {data}")
    global is_registered
    is_registered = False
    screen_capture_stop_event.set()

@sio.event
def disconnect(reason=None): # reason might be provided by server or transport
    logger.warning(f"CLIENT_SOCKET_DISCONNECT: Disconnected from server. Reason: {reason if reason else 'N/A'}")
    global is_registered
    is_registered = False
    screen_capture_stop_event.set()

@sio.on('registration_success')
def on_registration_success():
    global is_registered, capture_thread_obj
    if is_registered:
        logger.info("CLIENT_REG_SUCCESS: Already registered. Ignoring.")
        return
    logger.info("CLIENT_REG_SUCCESS: Client registration successful.")
    is_registered = True
    screen_capture_stop_event.clear()
    if capture_thread_obj is None or not capture_thread_obj.is_alive():
        logger.info("CLIENT_REG_SUCCESS: Starting screen capture thread.")
        capture_thread_obj = threading.Thread(target=screen_capture_loop, name="ScreenCaptureThread", daemon=True)
        capture_thread_obj.start()
    else:
        logger.warning("CLIENT_REG_SUCCESS: Capture thread was already running.")


@sio.on('registration_fail')
def on_registration_fail(data):
    global is_registered
    logger.error(f"CLIENT_REG_FAIL: {data.get('message', 'No message')}")
    is_registered = False
    screen_capture_stop_event.set()
    sio.disconnect()

@sio.on('command')
def on_command(data: dict):
    if not is_registered:
        logger.warning("CLIENT_CMD_RECV_UNREG: Received command while not registered. Ignoring.")
        return
    action = data.get('action')
    logger.debug(f"CLIENT_CMD_RECV: {data}")
    try:
        if action == 'move': pyautogui.moveTo(data['x'], data['y'], duration=0)
        elif action == 'click': pyautogui.click(x=data['x'], y=data['y'], button=data.get('button', 'left').lower())
        elif action == 'scroll':
            dy, dx = data.get('dy', 0), data.get('dx', 0)
            if dy != 0: pyautogui.scroll(dy * SCROLL_SENSITIVITY_VERTICAL * -1)
            if dx != 0: pyautogui.hscroll(dx * SCROLL_SENSITIVITY_HORIZONTAL)
        elif action == 'keydown':
            pg_key = map_key_to_pyautogui(data['key'])
            if pg_key: pyautogui.keyDown(pg_key)
        elif action == 'keyup':
            pg_key = map_key_to_pyautogui(data['key'])
            if pg_key: pyautogui.keyUp(pg_key)
        else: logger.warning(f"CLIENT_CMD_UNKNOWN: Action: {action}")
    except Exception as e:
        logger.error(f"CLIENT_CMD_ERROR: Processing command {data}: {e}", exc_info=True)

# --- Screen Capture and Sending Function ---
def screen_capture_loop():
    logger.info(f"CAPTURE_THREAD_START: Target FPS: {CLIENT_TARGET_FPS}, JPEG Quality: {JPEG_QUALITY}")
    with mss.mss() as sct:
        try:
            monitor_definition = sct.monitors[1]
        except IndexError:
            logger.warning("Primary monitor (idx 1) not found, using all monitors (idx 0).")
            monitor_definition = sct.monitors[0]
        if not monitor_definition:
            logger.error("CAPTURE_THREAD_ERROR: No monitor definition found. Exiting capture thread.")
            return

        logger.info(f"CAPTURE_THREAD_MONITOR: Capturing: {monitor_definition}")
        
        last_frame_send_time = time.time()
        target_interval = 1.0 / CLIENT_TARGET_FPS

        while not screen_capture_stop_event.is_set() and is_registered and sio.connected:
            capture_start_time = time.time()
            
            # Ensure we don't send faster than target_interval (more precise than just sleep)
            time_since_last_send = capture_start_time - last_frame_send_time
            if time_since_last_send < target_interval:
                sleep_for = target_interval - time_since_last_send
                # logger.debug(f"CAPTURE_THREAD_FPS_SLEEP: Sleeping for {sleep_for:.3f}s to meet FPS target.")
                time.sleep(sleep_for)
                capture_start_time = time.time() # Re-evaluate start time after sleep

            try:
                sct_img = sct.grab(monitor_definition)
                capture_done_time = time.time()
                logger.debug(f"TS_C: {capture_done_time:.3f} - CAPTURE_GRAB_TIME: {(capture_done_time - capture_start_time)*1000:.2f} ms")

                img = Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb, "raw", "RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                jpeg_bytes = buffer.getvalue()
                compress_done_time = time.time()
                logger.debug(f"TS_C: {compress_done_time:.3f} - CAPTURE_COMPRESS_TIME: {(compress_done_time - capture_done_time)*1000:.2f} ms, Size: {len(jpeg_bytes)}")

                if jpeg_bytes:
                    try:
                        sio.emit('screen_data_bytes', jpeg_bytes)
                        emit_done_time = time.time()
                        last_frame_send_time = emit_done_time # Update time of last successful send
                        logger.debug(f"TS_C: {emit_done_time:.3f} - CAPTURE_EMIT_TIME: {(emit_done_time - compress_done_time)*1000:.2f} ms. Total frame time: {(emit_done_time - capture_start_time)*1000:.2f} ms")
                    except socketio.exceptions.SocketIOError as e: # More specific exception
                        logger.error(f"CAPTURE_THREAD_EMIT_ERROR: SocketIOError sending frame: {e}")
                        # Potentially break or attempt reconnect if emit fails consistently
                        time.sleep(1) # Wait before retrying emit
                    except Exception as e:
                        logger.error(f"CAPTURE_THREAD_EMIT_ERROR: Generic error sending frame: {e}", exc_info=True)
                        time.sleep(1)
                else:
                    logger.warning("CAPTURE_THREAD_EMPTY_JPEG: Compression resulted in empty bytes.")

            except mss.exception.ScreenShotError as e:
                logger.error(f"CAPTURE_THREAD_MSS_ERROR: {e}. Pausing.")
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"CAPTURE_THREAD_UNEXPECTED_ERROR: {e}", exc_info=True)
                time.sleep(0.1)
            
            # No additional sleep here, as FPS control is handled at the beginning of the loop
            # elapsed_time = time.time() - capture_start_time
            # sleep_duration = target_interval - elapsed_time
            # if sleep_duration > 0:
            #     time.sleep(sleep_duration)

    logger.info("CAPTURE_THREAD_STOP: Screen capture thread has stopped.")

# --- Main Execution Block ---
def main():
    threading.current_thread().name = "MainThread"
    logger.info(f"Starting Remote Client. Server: {SERVER_URL}, FPS: {CLIENT_TARGET_FPS}, Quality: {JPEG_QUALITY}")
    if ACCESS_PASSWORD == 'change_this_password_too_server': logger.warning("USING DEFAULT CLIENT ACCESS PASSWORD!")

    global capture_thread_obj
    try:
        sio.connect(SERVER_URL, transports=['websocket'], wait_timeout=20) # Increased wait_timeout
        sio.wait()
    except socketio.exceptions.ConnectionError as e:
        logger.critical(f"CLIENT_MAIN_CONNECTION_ERROR: Could not connect to server {SERVER_URL}: {e}")
    except KeyboardInterrupt:
        logger.info("CLIENT_MAIN_SHUTDOWN_KEYBOARD: Shutdown requested.")
    finally:
        logger.info("CLIENT_MAIN_SHUTDOWN_SEQ: Initiating shutdown...")
        global is_registered
        is_registered = False
        screen_capture_stop_event.set()
        if capture_thread_obj and capture_thread_obj.is_alive():
            logger.info("CLIENT_MAIN_SHUTDOWN_SEQ: Waiting for capture thread...")
            capture_thread_obj.join(timeout=5.0)
            if capture_thread_obj.is_alive(): logger.warning("CLIENT_MAIN_SHUTDOWN_SEQ: Capture thread did not exit cleanly.")
        if sio.connected:
            logger.info("CLIENT_MAIN_SHUTDOWN_SEQ: Disconnecting from server...")
            sio.disconnect()
        logger.info("CLIENT_MAIN_SHUTDOWN_COMPLETE: Client has shut down.")

if __name__ == '__main__':
    main()
