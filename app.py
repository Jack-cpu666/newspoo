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

# --- Logging Setup (same) ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration (same) ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345_server_v3')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')

# --- Flask App Setup (same) ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', ping_timeout=90, ping_interval=30,
                    max_http_buffer_size=20 * 1024 * 1024, logger=False, engineio_logger=False)

# --- Global Variables (same) ---
client_pc_sid = None

# --- Authentication (same) ---
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
        
        /* Main layout containers */
        #main-content { display: flex; flex-direction: column; height: calc(100% - 3.5rem); /* Adjust based on header height */ }
        #screen-view-area { flex-grow: 1; display: flex; align-items: center; justify-content: center; background-color: #000; overflow: hidden; position: relative; transition: height 0.3s ease-in-out; }
        #text-input-area { height: 0; overflow: hidden; background-color: #f9fafb; padding:0; transition: height 0.3s ease-in-out, padding 0.3s ease-in-out; display: flex; flex-direction: column; }
        
        /* Text Input Mode Active */
        body.text-input-mode #screen-view-area { height: 50%; /* Or your desired height */ }
        body.text-input-mode #text-input-area { height: 50%; padding: 1rem; /* Or your desired height */ }

        #screen-view-area img { max-width: 100%; max-height: 100%; height: auto; width: auto; display: block; cursor: crosshair; object-fit: contain; }
        
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; }
        .click-feedback { position: absolute; border: 2px solid red; border-radius: 50%; width: 20px; height: 20px; transform: translate(-50%, -50%) scale(0); pointer-events: none; background-color: rgba(255, 0, 0, 0.3); animation: click-pulse 0.4s ease-out forwards; }
        @keyframes click-pulse { 0% { transform: translate(-50%, -50%) scale(0.5); opacity: 1; } 100% { transform: translate(-50%, -50%) scale(2); opacity: 0; } }
        body:focus { outline: none; }

        #injection-text {
            width: 100%;
            flex-grow: 1; /* Take remaining space in text-input-area */
            padding: 0.75rem;
            border: 1px solid #d1d5db; 
            border-radius: 0.375rem;
            font-family: monospace;
            font-size: 0.95rem;
            resize: none; /* Prevent manual resize */
        }
        .control-button {
            padding: 0.5rem 1rem;
            background-color: #2563eb; color: white; border: none;
            border-radius: 0.375rem; cursor: pointer; transition: background-color 0.2s;
            margin-right: 0.5rem;
        }
        .control-button:hover { background-color: #1d4ed8; }
        .control-button.active { background-color: #16a34a; } /* Green when active */
        .control-button.active:hover { background-color: #15803d; }
    </style>
</head>
<body class="bg-gray-200 flex flex-col h-screen" tabindex="0">

    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md flex-shrink-0 h-14">
        <h1 class="text-lg font-semibold">Remote Desktop Control</h1>
        <div class="flex items-center space-x-3">
            <button id="toggle-text-mode-button" class="control-button text-xs">Text Input Mode</button>
            <div id="connection-status" class="flex items-center text-xs">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
            </div>
            <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md transition duration-150 ease-in-out">Logout</a>
        </div>
    </header>

    <main id="main-content" class="p-2">
        <div id="screen-view-area">
            <img id="screen-image" src="https://placehold.co/1920x1080/333333/CCCCCC?text=Waiting+for+Remote+Screen..." alt="Remote Screen"
                 onerror="this.onerror=null; this.src='https://placehold.co/600x338/333333/CCCCCC?text=Error+Loading+Screen'; console.error('Image load error (placeholder):', this.src);">
        </div>
        <div id="text-input-area">
            <textarea id="injection-text" placeholder="Text entered here will be saved. Client types it on F1 press..."></textarea>
            <div class="mt-2 flex justify-end">
                <p id="injection-status" class="text-xs text-green-600 mr-auto self-center"></p>
                <button id="send-injection-text-button" class="control-button text-sm">Save Text for Client</button>
            </div>
        </div>
    </main>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const socket = io(window.location.origin, { path: '/socket.io/' });
            const screenImage = document.getElementById('screen-image');
            const connectionStatusDot = document.getElementById('status-dot');
            const connectionStatusText = document.getElementById('status-text');
            let remoteScreenWidth = null;
            let remoteScreenHeight = null;
            let activeModifiers = { ctrl: false, shift: false, alt: false, meta: false };
            let currentImageUrl = null;

            // UI Elements for layout and text injection
            const bodyElement = document.body;
            const toggleTextModeButton = document.getElementById('toggle-text-mode-button');
            const injectionTextarea = document.getElementById('injection-text');
            const sendInjectionTextButton = document.getElementById('send-injection-text-button');
            const injectionStatus = document.getElementById('injection-status');


            document.body.focus(); // For keyboard events
            // Avoid re-focusing if clicking inside textarea
            document.addEventListener('click', (e) => {
                if (e.target !== screenImage && e.target !== injectionTextarea && !injectionTextarea.contains(e.target)) {
                    document.body.focus();
                }
            });

            function updateStatus(status, message) { connectionStatusText.textContent = message; connectionStatusDot.className = `status-dot ${status}`; }
            function showClickFeedback(x, y) { /* ... same as before ... */ }


            socket.on('connect', () => { updateStatus('status-connecting', 'Server connected, waiting for PC...'); });
            socket.on('disconnect', (reason) => { updateStatus('status-disconnected', 'Server disconnected'); /* ... cleanup ... */ });
            socket.on('connect_error', (error) => { updateStatus('status-disconnected', 'Connection Error'); /* ... cleanup ... */ });
            socket.on('client_connected', (data) => { updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
            socket.on('client_disconnected', (data) => { updateStatus('status-disconnected', 'Remote PC Disconnected'); /* ... cleanup ... */ });
            socket.on('command_error', (data) => { console.error(`IO: Command Error: ${data.message}`); });
            socket.on('text_injection_set_ack', (data) => {
                injectionStatus.textContent = data.status === 'success' ? 'Text saved for client!' : `Error: ${data.message || 'Failed to save.'}`;
                setTimeout(() => { injectionStatus.textContent = ''; }, 3000);
            });

            socket.on('screen_frame_bytes', (imageDataBytes) => { /* ... same as before ... */
                const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
                const newImageUrl = URL.createObjectURL(blob);
                if (remoteScreenWidth === null) { /* ... dimension detection ... */
                    const tempImg = new Image();
                    tempImg.onload = () => {
                       remoteScreenWidth = tempImg.naturalWidth; remoteScreenHeight = tempImg.naturalHeight;
                       URL.revokeObjectURL(tempImg.src);
                    };
                    tempImg.src = newImageUrl;
                }
                const oldUrl = currentImageUrl; currentImageUrl = newImageUrl;
                screenImage.onload = () => { if (oldUrl) URL.revokeObjectURL(oldUrl); };
                screenImage.src = newImageUrl;
            });

            // --- Mouse Handling (same) ---
            screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
            screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); /*showClickFeedback*/ document.body.focus(); });
            screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); /*showClickFeedback*/ document.body.focus(); });
            screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const dY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); const dX = event.deltaX > 0 ? 1 : (event.deltaX < 0 ? -1 : 0); if (dY || dX) socket.emit('control_command', { action: 'scroll', dx: dX, dy: dY }); document.body.focus(); });

            // --- Keyboard Event Handling ---
            document.body.addEventListener('keydown', (event) => {
                if (document.activeElement === injectionTextarea || injectionTextarea.contains(document.activeElement)) {
                     // Allow typing in textarea, but capture F1 if it's for text injection trigger (handled by client)
                    if (event.key === "F1") {
                        // Optionally, prevent default F1 behavior if it does anything in browser
                        // event.preventDefault(); 
                        // We still send F1 to the client to trigger typing.
                    } else {
                        return; // Don't send other keys as control commands
                    }
                }

                // ... (rest of existing keydown logic for modifiers and sending commands)
                if (event.key === 'Control') activeModifiers.ctrl = true; if (event.key === 'Shift') activeModifiers.shift = true; if (event.key === 'Alt') activeModifiers.alt = true; if (event.key === 'Meta') activeModifiers.meta = true;
                let shouldPreventDefault = false; const isModifierKey = ['Control', 'Shift', 'Alt', 'Meta', 'CapsLock', 'NumLock', 'ScrollLock'].includes(event.key); const isFKey = event.key.startsWith('F') && event.key.length > 1 && !isNaN(parseInt(event.key.substring(1))); const keysToPrevent = [ 'Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ' ];
                if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) { shouldPreventDefault = true; } else if (keysToPrevent.includes(event.key) && !(event.altKey && event.key === 'Tab')) { shouldPreventDefault = true; }
                if (event.metaKey && event.shiftKey && event.key.toLowerCase() === 's') { shouldPreventDefault = false; } if (event.altKey && event.key === 'Tab') { shouldPreventDefault = false; } if (event.ctrlKey && ['c', 'v', 'x', 'a', 'z', 'y', 'r', 't', 'w', 'l', 'p', 'f'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (isFKey && event.key !== "F1") { shouldPreventDefault = false; } /* Allow F1 to pass through */ if (event.ctrlKey && event.shiftKey && ['i', 'j', 'c'].includes(event.key.toLowerCase())) { shouldPreventDefault = false; } if (event.ctrlKey && event.key === 'Tab') { shouldPreventDefault = false; }
                
                // For F1, we always want to send it, but might not prevent default if textarea is focused
                // For other keys that should be prevented, do so.
                if (shouldPreventDefault && event.key !== "F1") { event.preventDefault(); }


                const command = { action: 'keydown', key: event.key, code: event.code, ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, altKey: event.altKey, metaKey: event.metaKey };
                socket.emit('control_command', command);
            });
            document.body.addEventListener('keyup', (event) => {
                if (document.activeElement === injectionTextarea || injectionTextarea.contains(document.activeElement)) return;
                // ... (rest of existing keyup logic)
                 if (event.key === 'Control') activeModifiers.ctrl = false; if (event.key === 'Shift') activeModifiers.shift = false; if (event.key === 'Alt') activeModifiers.alt = false; if (event.key === 'Meta') activeModifiers.meta = false;
                 const command = { action: 'keyup', key: event.key, code: event.code }; socket.emit('control_command', command);
            });
            window.addEventListener('blur', () => { /* ... same as before ... */ });

            // --- UI Mode Toggle & Text Injection ---
            toggleTextModeButton.addEventListener('click', () => {
                bodyElement.classList.toggle('text-input-mode');
                if (bodyElement.classList.contains('text-input-mode')) {
                    toggleTextModeButton.textContent = 'Screen View Mode';
                    toggleTextModeButton.classList.add('active');
                    injectionTextarea.focus(); // Focus textarea when mode is active
                } else {
                    toggleTextModeButton.textContent = 'Text Input Mode';
                    toggleTextModeButton.classList.remove('active');
                    document.body.focus(); // Focus body for general controls
                }
            });

            sendInjectionTextButton.addEventListener('click', () => {
                const text = injectionTextarea.value;
                // No need to check for empty here, client can receive empty string to clear
                socket.emit('set_injection_text', { text_to_inject: text });
                injectionStatus.textContent = 'Saving...';
            });

            updateStatus('status-connecting', 'Initializing...');
            document.body.focus();
        });
    </script>
</body>
</html>
"""

# --- Flask Routes (same) ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True; return redirect(url_for('interface'))
        else:
            return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'): return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'): return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    session.pop('authenticated', None); return redirect(url_for('index'))

# --- SocketIO Events (mostly same, set_injection_text updated) ---
@socketio.on('connect')
def handle_connect(): logger.info(f"SOCKET_CONNECT SID: {request.sid}, IP: {request.remote_addr}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    if request.sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        emit('client_disconnected', {'message': 'Remote PC disconnected.'}, broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    client_token = data.get('token'); sid = request.sid
    if client_token == ACCESS_PASSWORD:
        if client_pc_sid and client_pc_sid != sid:
            try: server_disconnect_client(client_pc_sid, silent=True)
            except Exception as e: logger.error(f"Error disconnecting old client {client_pc_sid}: {e}")
        client_pc_sid = sid
        logger.info(f"Remote PC (SID: {sid}) registered.")
        emit('client_connected', {'message': 'Remote PC connected.'}, broadcast=True, include_self=False)
        emit('registration_success', room=sid)
    else:
        emit('registration_fail', {'message': 'Auth failed.'}, room=sid); server_disconnect_client(sid)

@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    if request.sid == client_pc_sid and data and isinstance(data, bytes):
        emit('screen_frame_bytes', data, broadcast=True, include_self=False)

@socketio.on('control_command')
def handle_control_command(data):
    if session.get('authenticated') and client_pc_sid:
        emit('command', data, room=client_pc_sid)
    elif not client_pc_sid:
        emit('command_error', {'message': 'Remote PC not connected.'}, room=request.sid)

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if not session.get('authenticated'): return
    text_to_inject = data.get('text_to_inject') # Can be empty string
    if client_pc_sid:
        if text_to_inject is not None:
            logger.info(f"SERVER_INJECT_TEXT_SEND: To SID {client_pc_sid}, Text: '{text_to_inject[:30]}...'")
            emit('receive_injection_text', {'text': text_to_inject}, room=client_pc_sid)
            emit('text_injection_set_ack', {'status': 'success'}, room=request.sid)
        else:
            emit('text_injection_set_ack', {'status': 'error', 'message': 'No text data received.'}, room=request.sid)
    else:
        emit('text_injection_set_ack', {'status': 'error', 'message': 'Remote PC not connected.'}, room=request.sid)

if __name__ == '__main__':
    logger.info("--- Server with Toggleable Text Input Mode ---")
    port = int(os.environ.get('PORT', 5000)); host = '0.0.0.0'
    logger.info(f"Listening on http://{host}:{port}")
    if ACCESS_PASSWORD == '1': logger.warning("USING DEFAULT SERVER ACCESS PASSWORD '1'!")
    socketio.run(app, host=host, port=port, debug=False)
