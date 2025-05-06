import socketio
import time
import os
import io
import threading
import logging
from PIL import Image # type: ignore
import mss # type: ignore
import pyautogui # type: ignore
import platform # For OS-specific key mapping (e.g., Meta key)

# --- Configuration ---
SERVER_URL = os.environ.get('REMOTE_SERVER_URL', 'https://newspoogunicorn-worker-class-eventlet-w.onrender.com')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', 'change_this_password_too')
CLIENT_TARGET_FPS = int(os.environ.get('CLIENT_TARGET_FPS', 15))
JPEG_QUALITY = int(os.environ.get('JPEG_QUALITY', 75)) # 0-100 (higher quality = larger size)
SCROLL_SENSITIVITY_VERTICAL = 20 # Adjust as needed for vertical scroll speed
SCROLL_SENSITIVITY_HORIZONTAL = 20 # Adjust as needed for horizontal scroll speed

# --- Logging Setup ---
# Using a more descriptive format, good for multi-threaded apps
log_format = '%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format)
logger = logging.getLogger(__name__)

# --- Global State Variables ---
sio = socketio.Client(reconnection_attempts=10, reconnection_delay=3, logger=False, engineio_logger=False)
is_registered = False
screen_capture_stop_event = threading.Event() # Event to signal the capture thread to stop
capture_thread_obj: threading.Thread | None = None # To hold the screen capture thread object

# --- PyAutoGUI Configuration ---
pyautogui.FAILSAFE = False  # IMPORTANT: Set to False to allow control when mouse is at screen edge.
                           # Be cautious, as this means an errant script can't be stopped by moving mouse to corner.
pyautogui.PAUSE = 0.0      # No artificial pause between PyAutoGUI actions by default.

# --- Key Mapping: JavaScript event.key to PyAutoGUI ---
PYAUTOGUI_SPECIAL_KEYS_MAP = {
    "Control": "ctrl", "Shift": "shift", "Alt": "alt",
    "Meta": "command" if platform.system() == "Darwin" else "win", # 'command' on macOS, 'win' on Windows/Linux
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Delete": "delete",
    "Tab": "tab", " ": "space", # Note: ' ' is for event.key == " "
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6",
    "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
    "PageUp": "pageup", "PageDown": "pagedown", "Home": "home", "End": "end",
    "Insert": "insert", "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
    "PrintScreen": "printscreen",
    # Add other mappings as identified from JS event.key values
}

def map_key_to_pyautogui(key_name_from_js: str) -> str | None:
    """Maps a JavaScript event.key string to a PyAutoGUI compatible key string."""
    if key_name_from_js in PYAUTOGUI_SPECIAL_KEYS_MAP:
        return PYAUTOGUI_SPECIAL_KEYS_MAP[key_name_from_js]

    if len(key_name_from_js) == 1: # Single character keys (e.g., 'a', 'A', '1', '$')
        return key_name_from_js # PyAutoGUI handles these directly, case matters for typewrite, not for keyDown/Up

    # For other unmapped keys, try lowercase version (pyautogui often uses lowercase for its named keys)
    lower_key = key_name_from_js.lower()
    if lower_key in pyautogui.KEY_NAMES: # pyautogui.KEY_NAMES is a list of valid key strings
        return lower_key
    
    logger.warning(f"Unmapped key: '{key_name_from_js}'. Returning original. May not work as expected.")
    return key_name_from_js # Fallback, might work for some keys pyautogui recognizes by other names


# --- SocketIO Event Handlers ---
@sio.event
def connect():
    logger.info(f"Successfully connected to server. SID: {sio.sid}")
    logger.info("Attempting to register client...")
    sio.emit('register_client', {'token': ACCESS_PASSWORD})

@sio.event
def connect_error(data):
    logger.error(f"Connection failed: {data}")
    global is_registered
    is_registered = False
    screen_capture_stop_event.set() # Signal capture thread to stop if it was running

@sio.event
def disconnect():
    logger.info("Disconnected from server.")
    global is_registered
    is_registered = False
    screen_capture_stop_event.set() # Ensure capture thread stops

@sio.on('registration_success')
def on_registration_success():
    global is_registered, capture_thread_obj
    if is_registered: # Avoid issues if multiple success messages are received
        logger.info("Already registered. Ignoring redundant registration_success event.")
        return

    logger.info("Client registration successful with server.")
    is_registered = True
    screen_capture_stop_event.clear() # Clear the stop event for a new capture session

    # Start screen capture thread if not already running
    if capture_thread_obj is None or not capture_thread_obj.is_alive():
        logger.info("Starting screen capture thread.")
        capture_thread_obj = threading.Thread(target=screen_capture_loop, name="ScreenCaptureThread", daemon=True)
        capture_thread_obj.start()
    else:
        # This case should ideally not be hit if logic is correct, but good to log
        logger.warning("Screen capture thread was already running upon registration success.")


@sio.on('registration_fail')
def on_registration_fail(data):
    global is_registered
    logger.error(f"Client registration failed: {data.get('message', 'No message received from server.')}")
    is_registered = False
    screen_capture_stop_event.set()
    sio.disconnect() # Disconnect if registration fails

@sio.on('command')
def on_command(data: dict):
    if not is_registered:
        logger.warning("Received command while not registered with server. Ignoring.")
        return

    action = data.get('action')
    # logger.debug(f"Received command: {data}") # Uncomment for verbose command logging

    try:
        if action == 'move':
            pyautogui.moveTo(data['x'], data['y'], duration=0) # Instant move
        elif action == 'click':
            button = data.get('button', 'left').lower() # Default to left click
            pyautogui.click(x=data['x'], y=data['y'], button=button)
        elif action == 'scroll':
            dx = data.get('dx', 0) # Horizontal scroll delta
            dy = data.get('dy', 0) # Vertical scroll delta

            # Server: dy > 0 is scroll down. PyAutoGUI: positive scrolls UP.
            if dy != 0:
                pyautogui.scroll(dy * SCROLL_SENSITIVITY_VERTICAL * -1)
            # Server: dx > 0 is scroll right. PyAutoGUI: positive hscrolls RIGHT.
            if dx != 0:
                pyautogui.hscroll(dx * SCROLL_SENSITIVITY_HORIZONTAL)
        elif action == 'keydown':
            key_name_js = data['key']
            pg_key = map_key_to_pyautogui(key_name_js)
            if pg_key:
                # logger.info(f"KeyDown: JS='{key_name_js}', PyAutoGUI='{pg_key}'")
                pyautogui.keyDown(pg_key)
            else:
                logger.warning(f"KeyDown: No PyAutoGUI mapping for JS key '{key_name_js}'")
        elif action == 'keyup':
            key_name_js = data['key']
            pg_key = map_key_to_pyautogui(key_name_js)
            if pg_key:
                # logger.info(f"KeyUp: JS='{key_name_js}', PyAutoGUI='{pg_key}'")
                pyautogui.keyUp(pg_key)
            else:
                logger.warning(f"KeyUp: No PyAutoGUI mapping for JS key '{key_name_js}'")
        else:
            logger.warning(f"Received unknown command action: {action}")

    except Exception as e:
        logger.error(f"Error processing command {data}: {e}", exc_info=True)


# --- Screen Capture and Sending Function (runs in a separate thread) ---
def screen_capture_loop():
    logger.info(f"Screen capture thread initiated. Target FPS: {CLIENT_TARGET_FPS}, JPEG Quality: {JPEG_QUALITY}")
    
    with mss.mss() as sct:
        try:
            # Attempt to use the primary monitor (index 1 in mss.monitors list)
            monitor_definition = sct.monitors[1]
            logger.info(f"Capturing primary monitor: {monitor_definition}")
        except IndexError:
            logger.warning("Primary monitor (index 1) not found. Falling back to all monitors (index 0).")
            try:
                monitor_definition = sct.monitors[0] # This is the combined virtual screen
                logger.info(f"Capturing all monitors (virtual screen): {monitor_definition}")
            except IndexError:
                logger.error("No monitors detected by MSS. Cannot capture screen. Exiting capture thread.")
                return # Critical error, cannot proceed

        while not screen_capture_stop_event.is_set() and is_registered and sio.connected:
            loop_start_time = time.time()
            try:
                sct_img = sct.grab(monitor_definition) # Capture the screen

                # Convert MSS BGRA/RGB data to PIL Image object
                # sct_img.rgb provides pixels in RGB order, sct_img.bgra in BGRA.
                # Image.frombytes expects RGB.
                img = Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb, "raw", "RGB")

                # Compress to JPEG in memory
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                jpeg_bytes = buffer.getvalue()

                if jpeg_bytes:
                    sio.emit('screen_data_bytes', jpeg_bytes)
                    # logger.debug(f"Sent frame: {len(jpeg_bytes)} bytes") # Very verbose
                else:
                    logger.warning("JPEG compression resulted in empty byte string.")

            except mss.exception.ScreenShotError as e:
                # This can happen if the screen is locked, fast user switching, etc.
                logger.error(f"MSS ScreenShotError: {e}. Pausing capture momentarily.")
                time.sleep(1.0) # Wait a bit before retrying
            except Exception as e:
                logger.error(f"Unexpected error in screen capture loop: {e}", exc_info=True)
                time.sleep(0.1) # Brief pause before retrying general errors

            # Calculate time to sleep to maintain target FPS
            elapsed_time = time.time() - loop_start_time
            sleep_duration = (1.0 / CLIENT_TARGET_FPS) - elapsed_time
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            # else:
                # logger.debug(f"Frame processing took longer ({elapsed_time:.3f}s) than target interval ({1.0/CLIENT_TARGET_FPS:.3f}s). Running at max speed.")


    logger.info("Screen capture thread has stopped.")


# --- Main Execution Block ---
def main():
    # Set current thread's name for better logging
    threading.current_thread().name = "MainThread"
    logger.info(f"Starting Remote Control Client. Attempting to connect to: {SERVER_URL}")

    if ACCESS_PASSWORD == 'change_this_password_too':
        logger.warning("USING DEFAULT ACCESS PASSWORD. This is insecure and should be changed via REMOTE_ACCESS_PASSWORD environment variable.")

    global capture_thread_obj # Make sure we can access it in finally block

    try:
        sio.connect(SERVER_URL, transports=['websocket'], wait_timeout=10)
        sio.wait() # Keep the main thread alive, processing SocketIO events, until disconnect
    except socketio.exceptions.ConnectionError as e:
        logger.critical(f"Could not connect to server {SERVER_URL} after multiple attempts: {e}")
    except KeyboardInterrupt:
        logger.info("Client shutdown requested (KeyboardInterrupt).")
    finally:
        logger.info("Initiating client shutdown sequence...")
        global is_registered
        is_registered = False # Ensure loops dependent on this flag will terminate
        screen_capture_stop_event.set() # Signal the screen capture thread to stop

        if capture_thread_obj and capture_thread_obj.is_alive():
            logger.info("Waiting for screen capture thread to cleanly exit...")
            capture_thread_obj.join(timeout=3.0) # Wait for up to 3 seconds
            if capture_thread_obj.is_alive():
                logger.warning("Screen capture thread did not exit in time.")

        if sio.connected:
            logger.info("Disconnecting from server...")
            sio.disconnect()
        
        logger.info("Client has been shut down.")

if __name__ == '__main__':
    main()
