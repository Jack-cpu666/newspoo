import eventlet
eventlet.monkey_patch()

import os
import base64
import time
from flask import Flask, request, session, redirect, url_for, render_template_string
from flask_socketio import SocketIO, emit
from flask_socketio import disconnect as server_disconnect_client
import traceback
import sys
import logging # Using Python's logging module

# --- Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout) # Log to stdout for Render
logger = logging.getLogger(__name__)


# --- Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345_server')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', 'change_this_password_too_server')

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY

# INCREASED TIMEOUTS SIGNIFICANTLY & ASYNC TIMEOUT FOR EVENTLET
# ping_timeout: Server closes connection if PONG not received in this time after PING.
# ping_interval: Server sends PING every this interval.
# Ensure ping_timeout > ping_interval
socketio = SocketIO(app,
                    async_mode='eventlet',
                    ping_timeout=90,  # Increased from 20 to 90 seconds
                    ping_interval=30, # Increased from 10 to 30 seconds
                    max_http_buffer_size=20 * 1024 * 1024, # 20MB
                    logger=True, engineio_logger=True # Enable detailed SocketIO logging
                   )

# --- Global Variables ---
client_pc_sid = None

# --- FPS Throttling Variables (SERVER-SIDE BROADCAST) ---
# We will disable this for now to see if it's a major source of delay
# TARGET_SERVER_BROADCAST_FPS = 5 # Example: Lower server broadcast rate
# MIN_SERVER_BROADCAST_INTERVAL = 1.0 / TARGET_SERVER_BROADCAST_FPS
# last_server_broadcast_time = 0

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates (LOGIN_HTML is the same, INTERFACE_HTML needs JS logging) ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control - Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style> body { font-family: 'Inter', sans-serif; } </style>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm">
        <h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Remote Access Login</h1>
        {% if error %}
            <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-4" role="alert">
                <span class="block sm:inline">{{ error }}</span>
            </div>
        {% endif %}
        <form method="POST" action="{{ url_for('index') }}">
            <div class="mb-4">
                <label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label>
                <input type="password" id="password" name="password" required
                       class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                       placeholder="Enter access password">
            </div>
            <button type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md transition duration-200 ease-in-out">
                Login
            </button>
        </form>
    </div>
</body>
</html>
"""

INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control Interface</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        /* CSS is the same as before, ensure it's included */
        html, body { height: 100%; overflow: hidden; font-family: 'Inter', sans-serif; margin: 0; padding: 0; box-sizing: border-box; }
        #screen-view img { max-width: 100%; max-height: 100%; height: auto; width: auto; display: block; cursor: crosshair; background-color: #333; object-fit: contain; }
        #screen-view { width: 100%; height: 100%; overflow: hidden; position: relative; display: flex; align-items: center; justify-content: center; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; }
        .click-feedback { position: absolute; border: 2px solid red; border-radius: 50%; width: 20px; height: 20px; transform: translate(-50%, -50%) scale(0); pointer-events: none; background-color: rgba(255, 0, 0, 0.3); animation: click-pulse 0.4s ease-out forwards; }
        @keyframes click-pulse { 0% { transform: translate(-50%, -50%) scale(0.5); opacity: 1; } 100% { transform: translate(-50%, -50%) scale(2); opacity: 0; } }
        body:focus { outline: none; }
    </style>
</head>
<body class="bg-gray-200 flex flex-col h-screen" tabindex="0">
    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md flex-shrink-0">
        <h1 class="text-lg font-semibold">Remote Desktop Control</h1>
        <div class="flex items-center space-x-3">
            <div id="connection-status" class="flex items-center text-xs">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
            </div>
             <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md transition duration-150 ease-in-out">Logout</a>
        </div>
    </header>
    <main class="flex-grow flex p-2 gap-2 overflow-hidden">
        <div class="flex-grow bg-black rounded-lg shadow-inner flex items-center justify-center overflow-hidden" id="screen-view-container">
            <div id="screen-view">
                 <img id="screen-image" src="https://placehold.co/1920x1080/333333/CCCCCC?text=Waiting+for+Remote+Screen..." alt="Remote Screen"
                       onerror="this.onerror=null; this.src='https://placehold.co/600x338/333333/CCCCCC?text=Error+Loading+Screen'; console.error('Image load error (placeholder):', this.src);">
            </div>
        </div>
    </main>
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io(window.location.origin, { path: '/socket.io/' });
            const screenImage = document.getElementById('screen-image');
            // ... (other variable declarations from your original JS)
            let remoteScreenWidth = null;
            let remoteScreenHeight = null;
            let activeModifiers = { ctrl: false, shift: false, alt: false, meta: false };
            let currentImageUrl = null;
            const connectionStatusDot = document.getElementById('status-dot');
            const connectionStatusText = document.getElementById('status-text');
            const screenView = document.getElementById('screen-view');


            function updateStatus(status, message) { connectionStatusText.textContent = message; connectionStatusDot.className = `status-dot ${status}`; console.log(`UI_STATUS: ${message} (${status})`); }
            function showClickFeedback(x, y, elementRect) { const feedback = document.createElement('div'); feedback.className = 'click-feedback'; feedback.style.left = `${x}px`; feedback.style.top = `${y}px`; screenView.appendChild(feedback); setTimeout(() => { feedback.remove(); }, 400); }

            socket.on('connect', () => { console.log(`TS: ${Date.now()} - IO: Connected to server`); updateStatus('status-connecting', 'Server connected, waiting for PC...'); });
            socket.on('disconnect', (reason) => { console.warn(`TS: ${Date.now()} - IO: Disconnected from server. Reason: ${reason}`); updateStatus('status-disconnected', 'Server disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Server+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('connect_error', (error) => { console.error(`TS: ${Date.now()} - IO: Connection Error:`, error); updateStatus('status-disconnected', 'Connection Error'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Connection+Error'; });
            socket.on('client_connected', (data) => { console.log(`TS: ${Date.now()} - IO: ${data.message}`); updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
            socket.on('client_disconnected', (data) => { console.warn(`TS: ${Date.now()} - IO: ${data.message}`); updateStatus('status-disconnected', 'Remote PC Disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=PC+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('command_error', (data) => { console.error(`TS: ${Date.now()} - IO: Command Error: ${data.message}`); });

            socket.on('screen_frame_bytes', (imageDataBytes) => {
                console.log(`TS: ${Date.now()} - FRAME_RECV: Received ${imageDataBytes.byteLength} bytes.`);
                const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
                const newImageUrl = URL.createObjectURL(blob);
                console.log(`TS: ${Date.now()} - FRAME_BLOB: Blob URL created: ${newImageUrl.substring(0,50)}...`);

                if (remoteScreenWidth === null || remoteScreenHeight === null) {
                    const tempImg = new Image();
                    tempImg.onload = () => {
                        if (remoteScreenWidth === null) {
                           remoteScreenWidth = tempImg.naturalWidth;
                           remoteScreenHeight = tempImg.naturalHeight;
                           console.log(`TS: ${Date.now()} - FRAME_DIM: Remote screen resolution detected: ${remoteScreenWidth}x${remoteScreenHeight}`);
                        }
                        URL.revokeObjectURL(tempImg.src);
                    };
                    tempImg.onerror = () => { console.error("Error loading image for dimension detection."); URL.revokeObjectURL(tempImg.src);};
                    tempImg.src = newImageUrl;
                }

                const previousObjectUrl = currentImageUrl;
                currentImageUrl = newImageUrl;

                screenImage.onload = () => {
                    console.log(`TS: ${Date.now()} - FRAME_LOAD: Image displayed from ${newImageUrl.substring(0,50)}...`);
                    if (previousObjectUrl) { URL.revokeObjectURL(previousObjectUrl); /* console.log(`TS: ${Date.now()} - FRAME_REVOKE: Old blob URL revoked.`); */ }
                };
                screenImage.onerror = () => {
                     console.error(`TS: ${Date.now()} - FRAME_ERROR: Error loading image blob: ${newImageUrl}`);
                     if (newImageUrl) { URL.revokeObjectURL(newImageUrl); }
                     if (currentImageUrl === newImageUrl) { currentImageUrl = null; }
                };
                screenImage.src = newImageUrl;
                console.log(`TS: ${Date.now()} - FRAME_SRC_SET: Image src set to new blob.`);
            });

            // Mouse and Keyboard handlers are the same, ensure they are included
            // ... (copy your existing mouse/keyboard JS handlers here) ...
            screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
            screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
            screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
            screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const deltaY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); const deltaX = event.deltaX > 0 ? 1 : (event.deltaX < 0 ? -1 : 0); if (deltaY !== 0 || deltaX !== 0) { socket.emit('control_command', { action: 'scroll', dx: deltaX, dy: deltaY }); } document.body.focus(); });
            document.body.addEventListener('keydown', (event) => {
                if (event.key === 'Control') activeModifiers.ctrl = true; if (event.key === 'Shift') activeModifiers.shift = true; if (event.key === 'Alt') activeModifiers.alt = true; if (event.key === 'Meta') activeModifiers.meta = true;
                let shouldPreventDefault = false; const isModifierKey = ['Control', 'Shift', 'Alt', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock'].includes(event.key); const isFKey = event.key.startsWith('F') && event.key.length > 1 && !isNaN(parseInt(event.key.substring(1))); const keysToPrevent = [ 'Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ' ];
                if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) { shouldPreventDefault = true; } else if (keysToPrevent.includes(event.key) && !(event.altKey && event.key === 'Tab')) { shouldPreventDefault = true; }
                if (event.metaKey && event.shiftKey && event.key.toLowerCase() === 's') { shouldPreventDefault = false; } if (event.altKey && event.key === 'Tab') { shouldPreventDefault = false; } if (event.ctrlKey && ['c', 'v', 'x', 'a', 'z', 'y', 'r', 't', 'w', 'l', 'p', 'f'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (isFKey) { shouldPreventDefault = false; } if (event.ctrlKey && event.shiftKey && ['i', 'j', 'c'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (event.ctrlKey && event.key === 'Tab') { shouldPreventDefault = false; }
                if (shouldPreventDefault) { event.preventDefault(); }
                const command = { action: 'keydown', key: event.key, code: event.code, ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, altKey: event.altKey, metaKey: event.metaKey }; socket.emit('control_command', command);
            });
            document.body.addEventListener('keyup', (event) => {
                 if (event.key === 'Control') activeModifiers.ctrl = false; if (event.key === 'Shift') activeModifiers.shift = false; if (event.key === 'Alt') activeModifiers.alt = false; if (event.key === 'Meta') activeModifiers.meta = false;
                 const command = { action: 'keyup', key: event.key, code: event.code }; socket.emit('control_command', command);
            });
             window.addEventListener('blur', () => {
                 console.log('Window blurred - releasing tracked modifier keys');
                 if (activeModifiers.ctrl) { socket.emit('control_command', { action: 'keyup', key: 'Control', code: 'ControlLeft' }); activeModifiers.ctrl = false; } if (activeModifiers.shift) { socket.emit('control_command', { action: 'keyup', key: 'Shift', code: 'ShiftLeft' }); activeModifiers.shift = false; } if (activeModifiers.alt) { socket.emit('control_command', { action: 'keyup', key: 'Alt', code: 'AltLeft' }); activeModifiers.alt = false; } if (activeModifiers.meta) { socket.emit('control_command', { action: 'keyup', key: 'Meta', code: 'MetaLeft' }); activeModifiers.meta = false; }
             });


            updateStatus('status-connecting', 'Initializing...');
            document.body.focus();
        }); // End DOMContentLoaded
    </script>
</body>
</html>
"""

# --- Flask Routes (mostly same) ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True
            logger.info(f"Web login successful for IP: {request.remote_addr}")
            return redirect(url_for('interface'))
        else:
            logger.warning(f"Web login failed for IP: {request.remote_addr}")
            return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'):
        return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'):
        logger.warning(f"Unauthorized access to /interface from IP: {request.remote_addr}")
        return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    logger.info(f"Web user logging out (Flask Session: {session.get('_id', 'N/A')}, IP: {request.remote_addr})")
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    logger.info(f"SOCKET_CONNECT SID: {request.sid}, IP: {request.remote_addr}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    sid = request.sid
    logger.info(f"SOCKET_DISCONNECT SID: {sid}, IP: {request.remote_addr}")
    if sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        emit('client_disconnected', {'message': 'Remote PC disconnected from server.'}, broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    client_token = data.get('token')
    sid = request.sid
    logger.info(f"REGISTER_ATTEMPT SID: {sid}, IP: {request.remote_addr}")

    if client_token == ACCESS_PASSWORD:
        if client_pc_sid and client_pc_sid != sid:
            logger.warning(f"New Remote PC (SID: {sid}) replacing old (SID: {client_pc_sid}). Disconnecting old.")
            try:
                server_disconnect_client(client_pc_sid, silent=True)
            except Exception as e:
                logger.error(f"Error disconnecting old client {client_pc_sid}: {e}")
        elif client_pc_sid == sid:
            logger.info(f"Remote PC (SID: {sid}) re-registered.")
        else:
            logger.info(f"Remote PC (SID: {sid}) registered successfully.")

        client_pc_sid = sid
        emit('client_connected', {'message': 'Remote PC connected to server.'}, broadcast=True, include_self=False)
        emit('registration_success', room=sid)
    else:
        logger.error(f"REGISTER_FAIL SID: {sid}. Invalid token.")
        emit('registration_fail', {'message': 'Authentication failed. Invalid token.'}, room=sid)
        server_disconnect_client(sid)


@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    # global last_server_broadcast_time # Server-side throttling disabled for now
    if request.sid != client_pc_sid:
        logger.warning(f"SCREEN_BYTES_RECV_UNAUTH SID: {request.sid}. Ignoring.")
        return

    receive_time = time.time()
    logger.debug(f"TS_S: {receive_time:.3f} - SCREEN_BYTES_RECV SID: {request.sid}, Size: {len(data)} bytes")

    # --- SERVER-SIDE THROTTLING (DISABLED FOR NOW) ---
    # current_time = time.time()
    # if current_time - last_server_broadcast_time < MIN_SERVER_BROADCAST_INTERVAL:
    #     logger.debug(f"TS_S: {current_time:.3f} - SERVER_THROTTLE_SKIP SID: {request.sid}")
    #     return # Skip frame for server-side throttling

    try:
        if data and isinstance(data, bytes):
            emit('screen_frame_bytes', data, broadcast=True, include_self=False)
            # last_server_broadcast_time = current_time # Part of server-side throttling
            broadcast_time = time.time()
            logger.debug(f"TS_S: {broadcast_time:.3f} - SCREEN_BYTES_BCAST SID: {request.sid}. Latency S_Recv->S_Bcast: {(broadcast_time - receive_time)*1000:.2f} ms")
        else:
            logger.warning(f"SCREEN_BYTES_INVALID SID: {request.sid}. Data not bytes or empty.")
    except Exception as e:
        logger.error(f"Error in handle_screen_data_bytes from SID {request.sid}: {e}\n{traceback.format_exc()}")


# Legacy screen_data handler can remain for compatibility if needed, or be removed.
@socketio.on('screen_data')
def handle_screen_data(data_dict):
    if request.sid != client_pc_sid: return
    logger.warning(f"LEGACY_SCREEN_DATA_RECV SID: {request.sid}. Client should use 'screen_data_bytes'.")
    # ... (rest of legacy handler)


@socketio.on('control_command')
def handle_control_command(data):
    if not session.get('authenticated'):
        logger.warning(f"CONTROL_CMD_UNAUTH SID: {request.sid}. No Flask session.")
        emit('command_error', {'message': 'Not authenticated to send commands.'}, room=request.sid)
        return

    if client_pc_sid:
        logger.debug(f"CONTROL_CMD_SEND Action: {data.get('action')} to SID: {client_pc_sid}")
        emit('command', data, room=client_pc_sid)
    else:
        logger.warning(f"CONTROL_CMD_NO_CLIENT SID: {request.sid}. Remote PC not connected.")
        emit('command_error', {'message': 'Remote PC not connected. Cannot send command.'}, room=request.sid)

if __name__ == '__main__':
    logger.info("--- Starting Flask-SocketIO Server (Optimized Phase 1) ---")
    port = int(os.environ.get('PORT', 5000))
    host = '0.0.0.0'
    logger.info(f"Server listening on: http://{host}:{port}")
    logger.info(f"SocketIO Ping Timeout: {socketio.ping_timeout}s, Ping Interval: {socketio.ping_interval}s")
    if ACCESS_PASSWORD == 'change_this_password_too_server': logger.warning("USING DEFAULT SERVER ACCESS PASSWORD!")
    if SECRET_KEY == 'change_this_strong_secret_key_12345_server': logger.warning("USING DEFAULT SERVER FLASK SECRET KEY!")
    logger.info("-------------------------------------------------------------")
    socketio.run(app, host=host, port=port, debug=False) # debug=False for production with eventlet
