import eventlet
eventlet.monkey_patch()

import os
import time
from flask import Flask, request, session, redirect, url_for, render_template_string
from flask_socketio import SocketIO, emit
from flask_socketio import disconnect as server_disconnect_client
import traceback
import sys
import logging

# --- Logging Setup (same as before) ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration (same as before) ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345_server_v2')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1') # Ensure this matches client

# --- Flask App Setup (timeouts adjusted from previous iteration) ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app,
                    async_mode='eventlet',
                    ping_timeout=90,
                    ping_interval=30,
                    max_http_buffer_size=20 * 1024 * 1024,
                    logger=False, engineio_logger=False # Set to True for very verbose SocketIO debugging
                   )

# --- Global Variables ---
client_pc_sid = None

# --- Authentication (same as before) ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
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
        html, body { height: 100%; overflow: hidden; font-family: 'Inter', sans-serif; margin: 0; padding: 0; box-sizing: border-box; }
        #screen-view img { max-width: 100%; max-height: 100%; height: auto; width: auto; display: block; cursor: crosshair; background-color: #333; object-fit: contain; }
        #screen-view { width: 100%; height: 100%; overflow: hidden; position: relative; display: flex; align-items: center; justify-content: center; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; }
        .click-feedback { position: absolute; border: 2px solid red; border-radius: 50%; width: 20px; height: 20px; transform: translate(-50%, -50%) scale(0); pointer-events: none; background-color: rgba(255, 0, 0, 0.3); animation: click-pulse 0.4s ease-out forwards; }
        @keyframes click-pulse { 0% { transform: translate(-50%, -50%) scale(0.5); opacity: 1; } 100% { transform: translate(-50%, -50%) scale(2); opacity: 0; } }
        body:focus { outline: none; }
        /* Styles for the text injection tool */
        #text-injection-container {
            background-color: #f3f4f6; /* gray-100 */
            padding: 0.75rem; /* p-3 */
            border-radius: 0.375rem; /* rounded-md */
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06); /* shadow-md */
            margin-top: 0.5rem; /* mt-2 */
        }
        #injection-text {
            width: 100%;
            min-height: 80px;
            padding: 0.5rem;
            border: 1px solid #d1d5db; /* gray-300 */
            border-radius: 0.375rem;
            font-family: monospace;
            font-size: 0.875rem;
        }
        #send-injection-text-button {
            margin-top: 0.5rem;
            padding: 0.5rem 1rem;
            background-color: #2563eb; /* blue-600 */
            color: white;
            border: none;
            border-radius: 0.375rem;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        #send-injection-text-button:hover {
            background-color: #1d4ed8; /* blue-700 */
        }
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
        <!-- Sidebar for controls, including text injection -->
        <aside class="w-64 bg-white p-2 rounded-lg shadow-md flex-shrink-0 flex flex-col overflow-y-auto">
            <h2 class="text-md font-semibold text-gray-700 mb-2">Tools</h2>
            <div id="text-injection-container">
                <h3 class="text-sm font-medium text-gray-600 mb-1">Text Injection</h3>
                <p class="text-xs text-gray-500 mb-2">Client types this text on triple-Ctrl press.</p>
                <textarea id="injection-text" placeholder="Enter text to be typed on client..."></textarea>
                <button id="send-injection-text-button">Send Text to Client</button>
                <p id="injection-status" class="text-xs text-green-600 mt-1"></p>
            </div>
            <!-- Future tools can be added here -->
        </aside>
    </main>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io(window.location.origin, { path: '/socket.io/' });
            const screenImage = document.getElementById('screen-image');
            const screenView = document.getElementById('screen-view');
            const connectionStatusDot = document.getElementById('status-dot');
            const connectionStatusText = document.getElementById('status-text');
            let remoteScreenWidth = null;
            let remoteScreenHeight = null;
            let activeModifiers = { ctrl: false, shift: false, alt: false, meta: false };
            let currentImageUrl = null;

            // Text Injection Elements
            const injectionTextarea = document.getElementById('injection-text');
            const sendInjectionTextButton = document.getElementById('send-injection-text-button');
            const injectionStatus = document.getElementById('injection-status');


            document.body.focus();
            document.addEventListener('click', (e) => { if (e.target !== screenImage) { document.body.focus(); } });

            function updateStatus(status, message) { connectionStatusText.textContent = message; connectionStatusDot.className = `status-dot ${status}`; console.log(`UI_STATUS: ${message} (${status})`); }
            function showClickFeedback(x, y, elementRect) { const feedback = document.createElement('div'); feedback.className = 'click-feedback'; feedback.style.left = `${x}px`; feedback.style.top = `${y}px`; screenView.appendChild(feedback); setTimeout(() => { feedback.remove(); }, 400); }

            socket.on('connect', () => { console.log(`TS: ${Date.now()} - IO: Connected to server`); updateStatus('status-connecting', 'Server connected, waiting for PC...'); });
            socket.on('disconnect', (reason) => { console.warn(`TS: ${Date.now()} - IO: Disconnected from server. Reason: ${reason}`); updateStatus('status-disconnected', 'Server disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Server+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('connect_error', (error) => { console.error(`TS: ${Date.now()} - IO: Connection Error:`, error); updateStatus('status-disconnected', 'Connection Error'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Connection+Error'; });
            socket.on('client_connected', (data) => { console.log(`TS: ${Date.now()} - IO: ${data.message}`); updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
            socket.on('client_disconnected', (data) => { console.warn(`TS: ${Date.now()} - IO: ${data.message}`); updateStatus('status-disconnected', 'Remote PC Disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=PC+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('command_error', (data) => { console.error(`TS: ${Date.now()} - IO: Command Error: ${data.message}`); });
            // New: Ack for text injection
            socket.on('text_injection_set_ack', (data) => {
                console.log(`TS: ${Date.now()} - IO: Text injection acknowledged by client: ${data.status}`);
                injectionStatus.textContent = data.status === 'success' ? 'Text sent to client successfully!' : 'Failed to send text.';
                setTimeout(() => { injectionStatus.textContent = ''; }, 3000);
            });


            socket.on('screen_frame_bytes', (imageDataBytes) => {
                // console.log(`TS: ${Date.now()} - FRAME_RECV: Received ${imageDataBytes.byteLength} bytes.`);
                const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
                const newImageUrl = URL.createObjectURL(blob);

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
                    // console.log(`TS: ${Date.now()} - FRAME_LOAD: Image displayed.`);
                    if (previousObjectUrl) { URL.revokeObjectURL(previousObjectUrl); }
                };
                screenImage.onerror = () => {
                     console.error(`TS: ${Date.now()} - FRAME_ERROR: Error loading image blob: ${newImageUrl}`);
                     if (newImageUrl) { URL.revokeObjectURL(newImageUrl); }
                     if (currentImageUrl === newImageUrl) { currentImageUrl = null; }
                };
                screenImage.src = newImageUrl;
            });

            // --- Mouse Handling ---
            screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
            screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
            screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
            screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const deltaY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); const deltaX = event.deltaX > 0 ? 1 : (event.deltaX < 0 ? -1 : 0); if (deltaY !== 0 || deltaX !== 0) { socket.emit('control_command', { action: 'scroll', dx: deltaX, dy: deltaY }); } document.body.focus(); });

            // --- Keyboard Event Handling ---
            // Important: To prevent text typed into the injection textarea from being sent as key events
            document.body.addEventListener('keydown', (event) => {
                if (event.target === injectionTextarea) return; // Do not process keydown if focus is on textarea

                if (event.key === 'Control') activeModifiers.ctrl = true; if (event.key === 'Shift') activeModifiers.shift = true; if (event.key === 'Alt') activeModifiers.alt = true; if (event.key === 'Meta') activeModifiers.meta = true;
                let shouldPreventDefault = false; const isModifierKey = ['Control', 'Shift', 'Alt', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock'].includes(event.key); const isFKey = event.key.startsWith('F') && event.key.length > 1 && !isNaN(parseInt(event.key.substring(1))); const keysToPrevent = [ 'Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ' ];
                if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) { shouldPreventDefault = true; } else if (keysToPrevent.includes(event.key) && !(event.altKey && event.key === 'Tab')) { shouldPreventDefault = true; }
                if (event.metaKey && event.shiftKey && event.key.toLowerCase() === 's') { shouldPreventDefault = false; } if (event.altKey && event.key === 'Tab') { shouldPreventDefault = false; } if (event.ctrlKey && ['c', 'v', 'x', 'a', 'z', 'y', 'r', 't', 'w', 'l', 'p', 'f'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (isFKey) { shouldPreventDefault = false; } if (event.ctrlKey && event.shiftKey && ['i', 'j', 'c'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (event.ctrlKey && event.key === 'Tab') { shouldPreventDefault = false; }
                if (shouldPreventDefault) { event.preventDefault(); }
                const command = { action: 'keydown', key: event.key, code: event.code, ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, altKey: event.altKey, metaKey: event.metaKey }; socket.emit('control_command', command);
            });
            document.body.addEventListener('keyup', (event) => {
                if (event.target === injectionTextarea) return; // Do not process keyup if focus is on textarea

                 if (event.key === 'Control') activeModifiers.ctrl = false; if (event.key === 'Shift') activeModifiers.shift = false; if (event.key === 'Alt') activeModifiers.alt = false; if (event.key === 'Meta') activeModifiers.meta = false;
                 const command = { action: 'keyup', key: event.key, code: event.code }; socket.emit('control_command', command);
            });
             window.addEventListener('blur', () => {
                 console.log('Window blurred - releasing tracked modifier keys');
                 if (activeModifiers.ctrl) { socket.emit('control_command', { action: 'keyup', key: 'Control', code: 'ControlLeft' }); activeModifiers.ctrl = false; } if (activeModifiers.shift) { socket.emit('control_command', { action: 'keyup', key: 'Shift', code: 'ShiftLeft' }); activeModifiers.shift = false; } if (activeModifiers.alt) { socket.emit('control_command', { action: 'keyup', key: 'Alt', code: 'AltLeft' }); activeModifiers.alt = false; } if (activeModifiers.meta) { socket.emit('control_command', { action: 'keyup', key: 'Meta', code: 'MetaLeft' }); activeModifiers.meta = false; }
             });

            // --- Text Injection Logic ---
            sendInjectionTextButton.addEventListener('click', () => {
                const text = injectionTextarea.value;
                if (text.trim() === '') {
                    injectionStatus.textContent = 'Text is empty.';
                    setTimeout(() => { injectionStatus.textContent = ''; }, 3000);
                    return;
                }
                console.log(`TS: ${Date.now()} - UI: Sending injection text to client: "${text.substring(0,30)}..."`);
                socket.emit('set_injection_text', { text_to_inject: text });
                injectionStatus.textContent = 'Sending...';
            });

            updateStatus('status-connecting', 'Initializing...');
            document.body.focus();

        }); // End DOMContentLoaded
    </script>
</body>
</html>
"""

# --- Flask Routes (same as before) ---
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
    # logger.info(f"SOCKET_DISCONNECT SID: {sid}, IP: {request.remote_addr}") # Can be noisy
    if sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        emit('client_disconnected', {'message': 'Remote PC disconnected from server.'}, broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data): # Same as before
    global client_pc_sid
    client_token = data.get('token')
    sid = request.sid
    logger.info(f"REGISTER_ATTEMPT SID: {sid}, IP: {request.remote_addr}")

    if client_token == ACCESS_PASSWORD:
        if client_pc_sid and client_pc_sid != sid:
            logger.warning(f"New Remote PC (SID: {sid}) replacing old (SID: {client_pc_sid}). Disconnecting old.")
            try: server_disconnect_client(client_pc_sid, silent=True)
            except Exception as e: logger.error(f"Error disconnecting old client {client_pc_sid}: {e}")
        client_pc_sid = sid
        logger.info(f"Remote PC (SID: {sid}) registered/re-registered successfully.")
        emit('client_connected', {'message': 'Remote PC connected to server.'}, broadcast=True, include_self=False)
        emit('registration_success', room=sid)
    else:
        logger.error(f"REGISTER_FAIL SID: {sid}. Invalid token.")
        emit('registration_fail', {'message': 'Authentication failed. Invalid token.'}, room=sid)
        server_disconnect_client(sid)


@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data): # Same as before, logging can be made less verbose
    if request.sid != client_pc_sid: return
    # logger.debug(f"TS_S: {time.time():.3f} - SCREEN_BYTES_RECV SID: {request.sid}, Size: {len(data)} bytes")
    try:
        if data and isinstance(data, bytes):
            emit('screen_frame_bytes', data, broadcast=True, include_self=False)
            # logger.debug(f"TS_S: {time.time():.3f} - SCREEN_BYTES_BCAST SID: {request.sid}.")
    except Exception as e:
        logger.error(f"Error in handle_screen_data_bytes from SID {request.sid}: {e}\n{traceback.format_exc()}")


@socketio.on('control_command')
def handle_control_command(data): # Same as before
    if not session.get('authenticated'):
        logger.warning(f"CONTROL_CMD_UNAUTH SID: {request.sid}. No Flask session.")
        emit('command_error', {'message': 'Not authenticated to send commands.'}, room=request.sid)
        return
    if client_pc_sid:
        # logger.debug(f"CONTROL_CMD_SEND Action: {data.get('action')} to SID: {client_pc_sid}")
        emit('command', data, room=client_pc_sid)
    else:
        emit('command_error', {'message': 'Remote PC not connected.'}, room=request.sid)

# --- New SocketIO Event for Text Injection ---
@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if not session.get('authenticated'):
        logger.warning(f"INJECT_TEXT_UNAUTH SID: {request.sid}. No Flask session.")
        # Optionally send an error back to the web client
        return

    text_to_inject = data.get('text_to_inject')
    if client_pc_sid:
        if text_to_inject is not None: # Allow empty string to clear
            logger.info(f"INJECT_TEXT_SEND: Sending text to SID {client_pc_sid}: '{text_to_inject[:50]}...'")
            emit('receive_injection_text', {'text': text_to_inject}, room=client_pc_sid)
            # Send ack back to the web client that initiated
            emit('text_injection_set_ack', {'status': 'success'}, room=request.sid)
        else:
            logger.warning(f"INJECT_TEXT_SEND_FAIL: No text provided by SID {request.sid}.")
            emit('text_injection_set_ack', {'status': 'error', 'message': 'No text provided.'}, room=request.sid)
    else:
        logger.warning(f"INJECT_TEXT_SEND_FAIL: Remote PC not connected. SID {request.sid} tried to send text.")
        emit('text_injection_set_ack', {'status': 'error', 'message': 'Remote PC not connected.'}, room=request.sid)


if __name__ == '__main__':
    logger.info("--- Starting Flask-SocketIO Server (with Text Injection) ---")
    port = int(os.environ.get('PORT', 5000))
    host = '0.0.0.0'
    logger.info(f"Server listening on: http://{host}:{port}")
    if ACCESS_PASSWORD == '1': logger.warning("USING DEFAULT SERVER ACCESS PASSWORD '1'!") # Updated default for example
    if SECRET_KEY.startswith('change_this_strong_secret_key'): logger.warning("USING DEFAULT SERVER FLASK SECRET KEY!")
    logger.info("-------------------------------------------------------------")
    socketio.run(app, host=host, port=port, debug=False)
