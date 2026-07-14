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

    ws = websocket.create_connection(f"ws://{host}:{port}", timeout=3)
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
        return ws
    except Exception:
        ws.close()
        raise


def _request(ws, request_type):
    ws.send(json.dumps({"op": _OP_REQUEST, "d": {"requestType": request_type, "requestId": request_type}}))
    resp = json.loads(ws.recv())["d"]
    if not resp.get("requestStatus", {}).get("result"):
        raise RuntimeError(f"{request_type} failed: {resp.get('requestStatus', {}).get('comment')}")
    return resp.get("responseData", {})


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
