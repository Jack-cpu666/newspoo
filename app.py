import eventlet
eventlet.monkey_patch() # Essential for Flask-SocketIO's async performance with eventlet

import os
import base64 # Kept for legacy screen_data, but binary is preferred
import time
from flask import Flask, request, session, redirect, url_for, render_template_string, Response
from flask_socketio import SocketIO, emit # Removed: join_room, leave_room, disconnect (disconnect is a function, not from flask_socketio directly for this use)
                                         # emit already handles rooms/sids
from flask_socketio import disconnect as server_disconnect_client # Explicit import for clarity
import traceback # For detailed error logging
import sys # For print(..., file=sys.stderr) if preferred over logging module

# --- Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', 'change_this_password_too')

# --- Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
# Increased buffer size slightly, might help with larger binary frames sometimes. 10MB is generous.
socketio = SocketIO(app, async_mode='eventlet',
                    ping_timeout=20,    # How long to wait for a PONG before closing (seconds)
                    ping_interval=10,   # How often to send a PING (seconds)
                    max_http_buffer_size=10 * 1024 * 1024) # Max size for HTTP buffer (primarily for polling, but good to have)

# --- Global Variables ---
client_pc_sid = None # Stores the SocketIO Session ID of the connected remote PC client

# --- FPS Throttling Variables (Server-Side Broadcast Limit) ---
# This throttles how often the server *broadcasts* frames to web viewers.
# The Python client also has its own FPS target for *sending* frames.
TARGET_FPS = 15 # Server's target broadcast FPS. Adjust as needed.
MIN_INTERVAL = 1.0 / TARGET_FPS # Minimum time interval between frame broadcasts
last_broadcast_time = 0 # Timestamp of the last broadcast screen update

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates (as strings) ---

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

# --- MODIFIED INTERFACE_HTML (JavaScript part updated for binary) ---
# This JavaScript is crucial for performance on the viewer's side.
# Using Blob URLs (URL.createObjectURL) and revoking them (URL.revokeObjectURL)
# is much more efficient than re-encoding Base64 strings on the client side.
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
            const screenView = document.getElementById('screen-view');
            const connectionStatusDot = document.getElementById('status-dot');
            const connectionStatusText = document.getElementById('status-text');
            let remoteScreenWidth = null;
            let remoteScreenHeight = null;
            let activeModifiers = { ctrl: false, shift: false, alt: false, meta: false };
            let currentImageUrl = null; // Manages the current Blob URL to enable cleanup

            document.body.focus(); // Focus body for keyboard events
            document.addEventListener('click', (e) => { if (e.target !== screenImage) { document.body.focus(); } });

            function updateStatus(status, message) { connectionStatusText.textContent = message; connectionStatusDot.className = `status-dot ${status}`; }
            function showClickFeedback(x, y, elementRect) { const feedback = document.createElement('div'); feedback.className = 'click-feedback'; feedback.style.left = `${x}px`; feedback.style.top = `${y}px`; screenView.appendChild(feedback); setTimeout(() => { feedback.remove(); }, 400); }

            socket.on('connect', () => { console.log('Connected to server'); updateStatus('status-connecting', 'Server connected, waiting for remote PC...'); });
            socket.on('disconnect', () => { console.warn('Disconnected from server'); updateStatus('status-disconnected', 'Server disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Server+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('connect_error', (error) => { console.error('Connection Error:', error); updateStatus('status-disconnected', 'Connection Error'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=Connection+Error'; });
            socket.on('client_connected', (data) => { console.log(data.message); updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
            socket.on('client_disconnected', (data) => { console.warn(data.message); updateStatus('status-disconnected', 'Remote PC Disconnected'); if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; } screenImage.src = 'https://placehold.co/600x338/333333/CCCCCC?text=PC+Disconnected'; remoteScreenWidth = null; remoteScreenHeight = null; });
            socket.on('command_error', (data) => { console.error('Command Error:', data.message); });

            // --- Handler for Binary Screen Data (Optimized) ---
            socket.on('screen_frame_bytes', (imageDataBytes) => {
                const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
                const newImageUrl = URL.createObjectURL(blob);

                // --- Detect Resolution on First Frame ---
                if (remoteScreenWidth === null || remoteScreenHeight === null) {
                    const tempImg = new Image();
                    tempImg.onload = () => {
                        if (remoteScreenWidth === null) { // Check again inside onload to be sure
                           remoteScreenWidth = tempImg.naturalWidth;
                           remoteScreenHeight = tempImg.naturalHeight;
                           console.log(`Remote screen resolution detected: ${remoteScreenWidth}x${remoteScreenHeight}`);
                        }
                        URL.revokeObjectURL(tempImg.src); // Clean up temp image URL
                    };
                    tempImg.onerror = () => {
                        console.error("Error loading image to detect dimensions from blob URL:", tempImg.src);
                        URL.revokeObjectURL(tempImg.src); // Clean up on error too
                    };
                    tempImg.src = newImageUrl; // Use the blob URL for dimension check
                }

                // --- Update Image and Cleanup Old URL ---
                const previousObjectUrl = currentImageUrl; // Store URL of the image currently displayed
                currentImageUrl = newImageUrl;             // Update global tracker to the new URL

                screenImage.onload = () => {
                    // New image has loaded successfully.
                    // Revoke the *previous* Blob URL to free up browser memory.
                    if (previousObjectUrl) {
                        // console.log("Revoking old blob URL:", previousObjectUrl);
                        URL.revokeObjectURL(previousObjectUrl);
                    }
                };
                screenImage.onerror = () => {
                    // The new image (newImageUrl) failed to load.
                    console.error("Error loading image from blob URL:", newImageUrl);
                    if (newImageUrl) { // Attempt to revoke the URL that failed
                        URL.revokeObjectURL(newImageUrl);
                    }
                    // If currentImageUrl still points to this failed URL, nullify it.
                    if (currentImageUrl === newImageUrl) {
                        currentImageUrl = null;
                    }
                    // Optionally set to a visible error placeholder
                    screenImage.src = 'https://placehold.co/600x338/FF0000/FFFFFF?text=Image+Load+Failed';
                };
                screenImage.src = newImageUrl; // Set the image source to the new Blob URL
            });

            // --- OLD Base64 Handler (Fallback, can be removed if client ONLY sends binary) ---
            /*
            socket.on('screen_update', (data) => {
                 const imageSrc = `data:image/jpeg;base64,${data.image}`;
                 // Revoking logic for base64 is not needed as they aren't Blob URLs
                 screenImage.src = imageSrc;
                 // Original resolution detection logic here
                 console.log("Received Base64 frame (Legacy Handler)");
                 // If mixing, ensure currentImageUrl is nulled if switching from blob to base64
                 if (currentImageUrl) { URL.revokeObjectURL(currentImageUrl); currentImageUrl = null; }
            });
            */

            // --- Mouse Handling (Unchanged from your original, looks good) ---
             screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
             screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
             screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); showClickFeedback(x, y, rect); document.body.focus(); });
             screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const deltaY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); const deltaX = event.deltaX > 0 ? 1 : (event.deltaX < 0 ? -1 : 0); if (deltaY !== 0 || deltaX !== 0) { socket.emit('control_command', { action: 'scroll', dx: deltaX, dy: deltaY }); } document.body.focus(); });

            // --- Keyboard Event Handling (Unchanged from your original, looks good) ---
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
            document.body.focus(); // Initial focus

        }); // End DOMContentLoaded
    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True
            print("Login successful for web interface.")
            return redirect(url_for('interface'))
        else:
            print("Login failed for web interface.")
            return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'):
        return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'):
        print(f"Unauthorized access attempt to /interface from IP: {request.remote_addr}")
        return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    viewer_sid = session.get('_id') # Flask-Session's session ID, not SocketIO SID
    print(f"Web user (Flask Session: {viewer_sid or 'N/A'}) logging out.")
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    # This event is for any SocketIO connection (web viewers or the remote PC client)
    sid = request.sid
    # Differentiate between web viewer and potential remote PC client later during registration
    print(f"[SocketIO Connect] New connection from SID: {sid}, IP: {request.remote_addr}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    sid = request.sid
    print(f"[SocketIO Disconnect] SID: {sid}")
    if sid == client_pc_sid:
        print(f"[!!!] Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        # Notify all web viewers that the remote PC has disconnected
        emit('client_disconnected', {'message': 'Remote PC disconnected'}, broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    client_token = data.get('token')
    sid = request.sid
    print(f"[RegClient Attempt] SID: {sid}, IP: {request.remote_addr}")

    if client_token == ACCESS_PASSWORD:
        if client_pc_sid and client_pc_sid != sid:
            print(f"[RegClient] A different Remote PC (Old SID: {client_pc_sid}) was connected.")
            print(f"[RegClient] New Remote PC (SID: {sid}) is taking over. Disconnecting old one.")
            try:
                # flask_socketio.disconnect is different from socketio.disconnect (Client)
                server_disconnect_client(client_pc_sid, silent=True) # `silent=True` suppresses errors if SID no longer exists
            except Exception as e:
                # This might happen if the old client already disconnected uncleanly
                print(f"Error trying to disconnect old client {client_pc_sid}: {e}", file=sys.stderr)
        elif client_pc_sid == sid:
            print(f"[RegClient] Remote PC (SID: {sid}) re-registered successfully.")
        else: # client_pc_sid is None or matches current sid
            print(f"[RegClient] Remote PC (SID: {sid}) registered successfully.")

        client_pc_sid = sid
        # Notify web viewers that a remote PC is connected
        emit('client_connected', {'message': 'Remote PC connected successfully'}, broadcast=True, include_self=False)
        # Send confirmation only to the registering client
        emit('registration_success', room=sid)
    else:
        print(f"[RegClient FAIL] Authentication failed for SID: {sid}. Incorrect token.", file=sys.stderr)
        emit('registration_fail', {'message': 'Authentication failed. Invalid token.'}, room=sid)
        server_disconnect_client(sid)


# --- Optimized Handler for Binary Screen Data from Python Client ---
@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    global last_broadcast_time
    if request.sid != client_pc_sid:
        print(f"Ignoring screen_data_bytes from non-registered SID: {request.sid}", file=sys.stderr)
        return

    current_time = time.time()
    if current_time - last_broadcast_time < MIN_INTERVAL:
        # print(f"Skipping binary frame due to server-side FPS throttle.") # Uncomment for debug
        return # Skip frame for throttling

    try:
        if data and isinstance(data, bytes):
            # Broadcast the raw bytes directly to all connected web viewers
            # 'screen_frame_bytes' is the event the JavaScript in INTERFACE_HTML listens for
            emit('screen_frame_bytes', data, broadcast=True, include_self=False)
            last_broadcast_time = current_time
            # print(f"Broadcast binary frame ({len(data)} bytes).") # Uncomment for debug
        else:
            print(f"Warning: Received non-bytes or empty data on 'screen_data_bytes' from {request.sid}", file=sys.stderr)

    except Exception as e:
        print(f"!!! ERROR in handle_screen_data_bytes from SID {request.sid}: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


# --- Legacy Handler for Base64 Screen Data (Fallback) ---
@socketio.on('screen_data')
def handle_screen_data(data_dict): # Expecting a dictionary now
    global last_broadcast_time
    if request.sid != client_pc_sid:
        print(f"Ignoring legacy screen_data from non-registered SID: {request.sid}", file=sys.stderr)
        return

    print("[Warning] Received data on legacy 'screen_data' event. Client should ideally use 'screen_data_bytes'.", file=sys.stderr)

    current_time = time.time()
    if current_time - last_broadcast_time < MIN_INTERVAL:
        # print("Skipping legacy frame due to server-side FPS throttle.") # Uncomment for debug
        return

    try:
        image_data_base64 = data_dict.get('image') # Expects dict with 'image' key (Base64 string)
        if image_data_base64 and isinstance(image_data_base64, str):
            # Broadcast using the old event name 'screen_update'
            emit('screen_update', {'image': image_data_base64}, broadcast=True, include_self=False)
            last_broadcast_time = current_time
            # print(f"Broadcast legacy base64 frame ({len(image_data_base64)} chars).") # Uncomment for debug
        else:
            print(f"Warning: Received invalid data format on 'screen_data' (expected dict with base64 'image' string) from {request.sid}", file=sys.stderr)
    except Exception as e:
        print(f"!!! ERROR in handle_screen_data (legacy) from SID {request.sid}: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


# --- Control Command Handler (from Web Viewer to Python Client) ---
@socketio.on('control_command')
def handle_control_command(data):
    # This event comes from an authenticated web viewer
    # We need to ensure the web viewer is authenticated by checking session
    # However, Flask-SocketIO doesn't automatically bridge Flask session to SocketIO context
    # For simplicity here, we assume if they are connected to this event, they passed /interface auth.
    # A more robust check would involve token-based auth for SocketIO or custom session handling.

    if not session.get('authenticated'): # Basic check
        print(f"Unauthorized control_command from SID: {request.sid}, IP: {request.remote_addr}. No Flask session.", file=sys.stderr)
        emit('command_error', {'message': 'Not authenticated to send commands.'}, room=request.sid)
        return

    if client_pc_sid:
        # Forward the command to the registered remote PC client
        emit('command', data, room=client_pc_sid)
        # print(f"Sent command {data.get('action')} to remote PC (SID: {client_pc_sid})") # Uncomment for debug
    else:
        # Inform the web viewer that no remote PC is connected
        emit('command_error', {'message': 'Remote PC not connected. Cannot send command.'}, room=request.sid)


# --- Main Execution ---
if __name__ == '__main__':
    print("--- Starting Flask-SocketIO Server (Optimized for Binary Data) ---")
    port = int(os.environ.get('PORT', 5000)) # Default to 5000 if PORT env var not set
    host = '0.0.0.0' # Listen on all available network interfaces

    print(f"Server listening on: http://{host}:{port}")
    print(f"Web Interface: http://localhost:{port}/ (or replace localhost with server IP)")
    print(f"Target Server Broadcast FPS: {TARGET_FPS} (Interval: {MIN_INTERVAL:.3f}s)")
    print(f"Binary Screen Handler: 'screen_data_bytes' -> 'screen_frame_bytes' (ENABLED)")
    print(f"Legacy Base64 Handler: 'screen_data' -> 'screen_update' (ENABLED for fallback)")

    if ACCESS_PASSWORD == 'change_this_password_too':
        print("\n!!! WARNING: Using DEFAULT remote access password. This is INSECURE. !!!")
        print("!!! Set the REMOTE_ACCESS_PASSWORD environment variable to a strong, unique password. !!!\n")
    if SECRET_KEY == 'change_this_strong_secret_key_12345':
        print("\n!!! WARNING: Using DEFAULT Flask secret key. This is INSECURE for session management. !!!")
        print("!!! Set the FLASK_SECRET_KEY environment variable to a strong, unique random string. !!!\n")
    print("-------------------------------------------------------------")

    # Use `debug=False` for production/testing with eventlet.
    # `debug=True` with eventlet can sometimes cause issues with reloader.
    socketio.run(app, host=host, port=port, debug=False)
