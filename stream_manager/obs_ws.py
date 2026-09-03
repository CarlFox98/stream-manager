"""
Minimal synchronous OBS WebSocket v5 client, used for status polling only.

Talks to OBS's built-in WebSocket server (OBS 28+, protocol v5) to report
real streaming/recording state and the current scene name — replacing the
old tasklist/PowerShell process-sniffing where it's available. Read-only:
never issues a request that changes OBS's state. Opens a short-lived
connection per poll rather than holding one open, so it survives OBS
restarts and network hiccups without any reconnect bookkeeping.

Configure via .env: OBS_WEBSOCKET_PASSWORD (required if OBS's WebSocket
server has "Enable Authentication" checked, which is the default), and
optionally OBS_WEBSOCKET_HOST / OBS_WEBSOCKET_PORT (default localhost:4455).
"""
import base64, hashlib, json, os

try:
    import websocket  # the 'websocket-client' package; optional dependency
except ImportError:
    websocket = None

_OP_IDENTIFY = 1
_OP_IDENTIFIED = 2
_OP_REQUEST = 6
_OP_REQUEST_RESPONSE = 7

# Last connection outcome, so the dashboard can say *why* OBS is unreachable
# instead of a bare "not reachable". Updated on every connect attempt.
status = {"ok": False, "error": "not connected yet", "target": ""}


def _friendly(exc, host, port):
    """Turn a socket/websocket exception into something a streamer can act on."""
    import errno, socket
    name = type(exc).__name__
    text = str(exc) or name
    if isinstance(exc, socket.timeout) or "timed out" in text.lower():
        return (f"no response from {host}:{port} — if OBS is on this PC set "
                f"OBS_WEBSOCKET_HOST=127.0.0.1 in .env (a stale LAN IP times out like this)")
    if isinstance(exc, ConnectionRefusedError) or getattr(exc, "errno", None) == errno.ECONNREFUSED:
        return (f"connection refused at {host}:{port} — OBS is not running, or its WebSocket "
                f"server is off (OBS → Tools → WebSocket Server Settings → Enable)")
    if isinstance(exc, socket.gaierror):
        return f"cannot resolve host '{host}' — check OBS_WEBSOCKET_HOST in .env"
    low = text.lower()
    if "authentication" in low or "auth" in low:
        return "OBS rejected the password — copy it from OBS → Tools → WebSocket Server Settings → Show Connect Info"
    if "unreachable" in low or "no route" in low:
        return f"{host}:{port} is unreachable — check OBS_WEBSOCKET_HOST in .env"
    return f"{name}: {text}"

if websocket is None and os.environ.get("OBS_WEBSOCKET_PASSWORD"):
    print("[obs] OBS_WEBSOCKET_PASSWORD is set but the 'websocket-client' package "
          "isn't installed (pip install websocket-client) — falling back to process detection.")


def _build_auth_response(password, challenge, salt):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


def _connect():
    """Open + authenticate a fresh connection. Returns a websocket.WebSocket, or raises."""
    host = os.environ.get("OBS_WEBSOCKET_HOST", "localhost")
    port = int(os.environ.get("OBS_WEBSOCKET_PORT") or 4455)
    password = os.environ.get("OBS_WEBSOCKET_PASSWORD", "")

    status["target"] = f"{host}:{port}"
    try:
        ws = websocket.create_connection(f"ws://{host}:{port}", timeout=3)
    except Exception as e:
        status.update(ok=False, error=_friendly(e, host, port))
        raise
    try:
        hello = json.loads(ws.recv())["d"]
        identify = {"rpcVersion": hello["rpcVersion"], "eventSubscriptions": 0}
        if "authentication" in hello:
            if not password:
                raise RuntimeError("OBS requires a WebSocket password but OBS_WEBSOCKET_PASSWORD isn't set")
            auth = hello["authentication"]
            identify["authentication"] = _build_auth_response(password, auth["challenge"], auth["salt"])
        ws.send(json.dumps({"op": _OP_IDENTIFY, "d": identify}))
        resp = json.loads(ws.recv())
        if resp.get("op") != _OP_IDENTIFIED:
            raise RuntimeError(f"OBS did not identify us: {resp}")
        status.update(ok=True, error="")
        return ws
    except Exception as e:
        status.update(ok=False, error=_friendly(e, host, port))
        ws.close()
        raise


def _request(ws, request_type, data=None):
    payload = {"requestType": request_type, "requestId": request_type}
    if data:
        payload["requestData"] = data
    ws.send(json.dumps({"op": _OP_REQUEST, "d": payload}))
    resp = json.loads(ws.recv())["d"]
    if not resp.get("requestStatus", {}).get("result"):
        raise RuntimeError(f"{request_type} failed: {resp.get('requestStatus', {}).get('comment')}")
    return resp.get("responseData", {})


def fetch_stats(mic_input="Mic/Aux"):
    """One connection, both stats calls — the numbers the health monitor needs.

    Returns {"ok": bool, "stats": {...}, "stream": {...}} where:
      stats  (GetStats)        cpuUsage, memoryUsage, availableDiskSpace, activeFps,
                               averageFrameRenderTime, renderSkippedFrames/renderTotalFrames
                               (rendering lag), outputSkippedFrames/outputTotalFrames
                               (encoding lag)
      stream (GetStreamStatus) outputActive, outputReconnecting, outputCongestion,
                               outputBytes, outputDuration, and
                               outputSkippedFrames/outputTotalFrames — the frames the
                               stream output dropped, i.e. the network drops that show
                               up in OBS as "dropped frames (network)".
    """
    if websocket is None:
        status.update(ok=False, error="the 'websocket-client' package isn't installed "
                                      "(pip install websocket-client)")
        return {"ok": False, "error": status["error"], "stats": {}, "stream": {}, "audio": {}}
    try:
        ws = _connect()
    except Exception:
        return {"ok": False, "error": status["error"], "stats": {}, "stream": {}, "audio": {}}
    out = {"ok": True, "error": "", "stats": {}, "stream": {}, "audio": {}}
    try:
        try:
            out["stats"] = _request(ws, "GetStats") or {}
        except Exception:
            pass
        try:
            out["stream"] = _request(ws, "GetStreamStatus") or {}
        except Exception:
            pass
        try:
            out["audio"] = _audio_probe(ws, mic_input)
        except Exception:
            out["audio"] = {}
        return out
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _audio_probe(ws, mic_input):
    """Mute state + bound device for the mic input, and every audio input's mute
    state. Cheap: three requests on a connection we already have open.

    Returns {"mic": {"name","muted","device"}, "muted_inputs": [...], "inputs": [...]}
    """
    out = {"mic": {}, "muted_inputs": [], "inputs": []}
    try:
        kinds = ("wasapi_input_capture", "wasapi_output_capture",
                 "wasapi_process_output_capture", "coreaudio_input_capture",
                 "pulse_input_capture", "pulse_output_capture")
        inputs = (_request(ws, "GetInputList") or {}).get("inputs") or []
        audio = [i for i in inputs if i.get("inputKind") in kinds]
        out["inputs"] = [i.get("inputName") for i in audio]
    except Exception:
        audio = []
    for i in audio:
        name = i.get("inputName")
        if not name:
            continue
        try:
            muted = bool((_request(ws, "GetInputMute", {"inputName": name}) or {}).get("inputMuted"))
        except Exception:
            continue
        if muted:
            out["muted_inputs"].append(name)
        if name == mic_input:
            device = ""
            try:
                st = (_request(ws, "GetInputSettings", {"inputName": name}) or {}).get("inputSettings") or {}
                device = str(st.get("device_id") or "")
            except Exception:
                pass
            out["mic"] = {"name": name, "muted": muted, "device": device}
    return out


def get_obs_ws_status(state):
    """
    Poll OBS over its WebSocket API for real stream/record state and the
    current scene. Returns True if the socket answered (state["obs"] is
    populated from OBS itself), or False if it's not usable right now
    (OBS not running, plugin not enabled, wrong password, library missing,
    etc.) so the caller can fall back to process detection instead.
    """
    if websocket is None:
        return False
    try:
        ws = _connect()
    except Exception:
        return False
    try:
        stream = _request(ws, "GetStreamStatus")
        record = _request(ws, "GetRecordStatus")
        scene = _request(ws, "GetCurrentProgramScene")
        state["obs"]["running"] = True
        state["obs"]["streaming"] = bool(stream.get("outputActive"))
        state["obs"]["recording"] = bool(record.get("outputActive"))
        state["obs"]["scene"] = scene.get("currentProgramSceneName", "")
        return True
    except Exception:
        return False
    finally:
        try:
            ws.close()
        except Exception:
            pass
