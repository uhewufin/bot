import importlib
import json
from urllib.parse import urlparse
from workers import Response, WorkerEntrypoint

try:
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
except ImportError:
    VerifyKey = None
    BadSignatureError = Exception


def _verify_discord_signature(raw_body: bytes, signature_hex: str, timestamp: str, public_key_hex: str) -> bool:
    if not signature_hex or not timestamp or not public_key_hex:
        return False
    if VerifyKey is None:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex.strip()))
        verify_key.verify(timestamp.encode("utf-8") + raw_body, bytes.fromhex(signature_hex.strip()))
        return True
    except (ValueError, BadSignatureError, Exception):
        return False


def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload),
        status=status,
        headers={"Content-Type": "application/json"},
    )


def _ephemeral(content: str) -> dict:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": 64,
        },
    }


async def _dispatch_command(cmd_name: str, interaction: dict, env) -> dict:
    if not cmd_name or not cmd_name.isidentifier():
        return _ephemeral(f"Invalid command name: `/{cmd_name}`")

    try:
        module = importlib.import_module(f"cmds.{cmd_name}")
    except ModuleNotFoundError:
        return _ephemeral(f"No command handler found for `/{cmd_name}`.")
    except Exception as exc:
        return _ephemeral(f"Failed to load `/{cmd_name}`: `{exc}`")

    handler = getattr(module, "run", None)
    if not callable(handler):
        return _ephemeral(f"Command `/{cmd_name}` is missing a `run(message, env)` function.")

    return await handler(interaction, env)


class Default(WorkerEntrypoint):
    async def on_fetch(self, request):
        env = self.env

        if request.method == "GET":
            path = urlparse(request.url).path
            if path in ("/", "/health"):
                kv_bound = getattr(env, "UhewBotCookieKV", None) is not None
                pid_set = bool(getattr(env, "PID", "") or "")
                return _json_response({
                    "ok": True,
                    "bot": "uhewbot",
                    "kv_bound": kv_bound,
                    "pid_set": pid_set,
                })
            return _json_response({"error": "Not found"}, status=404)

        if request.method != "POST":
            return _json_response({"error": "Method not allowed"}, status=405)

        headers = request.headers
        signature = headers.get("x-signature-ed25519") or ""
        timestamp = headers.get("x-signature-timestamp") or ""
        public_key = getattr(env, "PID", "") or ""

        raw_text = await request.text()
        raw_bytes = raw_text.encode("utf-8")

        if not _verify_discord_signature(raw_bytes, signature, timestamp, public_key):
            return Response("Invalid request signature", status=401)

        try:
            interaction = json.loads(raw_text)
        except Exception:
            return _json_response({"error": "Invalid JSON payload"}, status=400)

        interaction_type = interaction.get("type")

        if interaction_type == 1:
            return _json_response({"type": 1})

        if interaction_type == 2:
            data = interaction.get("data") or {}
            cmd_name = data.get("name") or ""
            result = await _dispatch_command(cmd_name, interaction, env)
            return _json_response(result)

        return _json_response({"error": "Unsupported interaction type"}, status=400)
        
