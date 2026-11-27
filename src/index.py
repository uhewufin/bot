import hashlib
import hmac
import importlib
import inspect
import json
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint


# --- Ed25519 signature verification ---

_P = (1 << 255) - 19
_L = (1 << 252) + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, -1, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(
        hashlib.sha512(data).digest(),
        "little",
    )


def _xrecover(y: int) -> int:
    xx = (
        (y * y - 1)
        * pow(_D * y * y + 1, -1, _P)
    ) % _P

    x = pow(xx, (_P + 3) // 8, _P)

    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P

    if x % 2 != 0:
        x = _P - x

    return x


def _decode_point(s: bytes):
    if len(s) != 32:
        return None

    y = int.from_bytes(s, "little") & ((1 << 255) - 1)

    if y >= _P:
        return None

    x = _xrecover(y)

    if (x & 1) != ((s[31] >> 7) & 1):
        x = _P - x

    if (
        -x * x
        + y * y
        - 1
        - _D * x * x * y * y
    ) % _P != 0:
        return None

    return (
        x % _P,
        y % _P,
        1,
        (x * y) % _P,
    )


def _edwards_add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2

    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P

    e = (b - a) % _P
    f = (d - c) % _P
    g = (d + c) % _P
    h = (b + a) % _P

    return (
        e * f % _P,
        g * h % _P,
        f * g % _P,
        e * h % _P,
    )


def _edwards_mul(p, n: int):
    q = (0, 1, 1, 0)

    while n > 0:
        if n & 1:
            q = _edwards_add(q, p)

        p = _edwards_add(p, p)
        n >>= 1

    return q


def _encode_point(p) -> bytes:
    x, y, z, _ = p

    inv_z = pow(z, -1, _P)

    x = x * inv_z % _P
    y = y * inv_z % _P

    bits = bytearray(y.to_bytes(32, "little"))
    bits[31] |= (x & 1) << 7

    return bytes(bits)


_B_Y = 4 * pow(5, -1, _P) % _P
_B_X = _xrecover(_B_Y)

_B = (
    _B_X,
    _B_Y,
    1,
    (_B_X * _B_Y) % _P,
)


def _verify_ed25519(
    public_key: bytes,
    message: bytes,
    signature: bytes,
) -> bool:

    if len(public_key) != 32 or len(signature) != 64:
        return False

    a_pt = _decode_point(public_key)

    if a_pt is None:
        return False

    r_bytes = signature[:32]
    s = int.from_bytes(signature[32:], "little")

    if s >= _L:
        return False

    r_pt = _decode_point(r_bytes)

    if r_pt is None:
        return False

    h = _sha512_int(
        r_bytes + public_key + message
    ) % _L

    lhs = _edwards_mul(_B, s)

    rhs = _edwards_add(
        r_pt,
        _edwards_mul(a_pt, h),
    )

    return hmac.compare_digest(
        _encode_point(lhs),
        _encode_point(rhs),
    )


def _verify_discord_signature(
    raw_body: bytes,
    signature_hex: str,
    timestamp: str,
    public_key_hex: str,
) -> bool:

    if not signature_hex or not timestamp or not public_key_hex:
        return False

    try:
        public_key = bytes.fromhex(public_key_hex.strip())
        signature = bytes.fromhex(signature_hex.strip())

        return _verify_ed25519(
            public_key,
            timestamp.encode("utf-8") + raw_body,
            signature,
        )

    except ValueError:
        return False


# --- Response helpers ---

def _json_response(
    payload: dict,
    status: int = 200,
) -> Response:

    return Response(
        json.dumps(payload),
        status=status,
        headers={
            "Content-Type": "application/json",
        },
    )


def _ephemeral(content: str) -> dict:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": 64,
        },
    }


# --- Handler invocation ---

async def _invoke_handler(
    handler,
    interaction: dict,
    env,
    ctx,
) -> dict:

    sig = inspect.signature(handler)

    params = [
        p
        for p in sig.parameters.values()
        if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]

    if len(params) >= 3:
        return await handler(interaction, env, ctx)

    return await handler(interaction, env)


# --- Slash command dispatcher ---

async def _dispatch_command(
    cmd_name: str,
    interaction: dict,
    env,
    ctx=None,
) -> dict:

    if not cmd_name or not cmd_name.isidentifier():
        return _ephemeral(
            f"Invalid command name: `/{cmd_name}`"
        )

    try:
        module = importlib.import_module(
            f"cmds.{cmd_name}"
        )

    except ModuleNotFoundError:
        return _ephemeral(
            f"No command handler found for `/{cmd_name}`."
        )

    except Exception as exc:
        return _ephemeral(
            f"Failed to load `/{cmd_name}`: `{exc}`"
        )

    handler = getattr(module, "run", None)

    if not callable(handler):
        return _ephemeral(
            f"Command `/{cmd_name}` is missing a `run(message, env)` function."
        )

    try:
        return await _invoke_handler(
            handler,
            interaction,
            env,
            ctx,
        )

    except Exception as exc:
        print(f"[dispatcher] Command error: {exc}")

        return _ephemeral(
            f"Command failed: `{exc}`"
        )


# --- Component dispatcher ---

async def _dispatch_component(
    interaction: dict,
    env,
    ctx=None,
) -> dict:

    data = interaction.get("data") or {}

    custom_id = str(
        data.get("custom_id") or ""
    )

    if custom_id.startswith("uploadaudio_group:"):

        try:
            module = importlib.import_module(
                "cmds.uploadaudio"
            )

        except Exception as exc:
            print(f"[dispatcher] Failed to load uploadaudio: {exc}")

            return _ephemeral(
                f"Failed to load `cmds.uploadaudio`: `{exc}`"
            )

        handler = getattr(
            module,
            "handle_component",
            None,
        )

        if not callable(handler):
            return _ephemeral(
                "`cmds.uploadaudio` is missing `handle_component`."
            )

        try:
            return await _invoke_handler(
                handler,
                interaction,
                env,
                ctx,
            )

        except Exception as exc:
            print(f"[dispatcher] Component error: {exc}")

            return _ephemeral(
                f"Button handler failed: `{exc}`"
            )

    return _ephemeral(
        f"Unhandled component interaction: `{custom_id}`"
    )


# --- Cloudflare Worker entry point ---

class Default(WorkerEntrypoint):

    async def on_fetch(
        self,
        request,
        env=None,
        ctx=None,
    ):

        env = env or self.env
        ctx = ctx or getattr(self, "ctx", None)

        # GET health check
        if request.method == "GET":

            path = urlparse(request.url).path

            if path in ("/", "/health"):

                kv_bound = (
                    getattr(env, "UhewBotCookieKV", None)
                    is not None
                )

                pid_set = bool(
                    getattr(env, "PID", "") or ""
                )

                groups_set = bool(
                    getattr(env, "groups", "") or ""
                )

                return _json_response({
                    "ok": True,
                    "bot": "uhewbot",
                    "kv_bound": kv_bound,
                    "pid_set": pid_set,
                    "groups_set": groups_set,
                })

            return _json_response(
                {"error": "Not found"},
                status=404,
            )

        if request.method != "POST":
            return _json_response(
                {"error": "Method not allowed"},
                status=405,
            )

        # Read the original request body
        headers = request.headers

        signature = (
            headers.get("x-signature-ed25519") or ""
        )

        timestamp = (
            headers.get("x-signature-timestamp") or ""
        )

        public_key = (
            getattr(env, "PID", "") or ""
        )

        raw_text = await request.text()
        raw_bytes = raw_text.encode("utf-8")

        # Verify Discord signature
        if not _verify_discord_signature(
            raw_bytes,
            signature,
            timestamp,
            public_key,
        ):

            return Response(
                "Invalid request signature",
                status=401,
            )

        # Parse interaction JSON
        try:
            interaction = json.loads(raw_text)

        except Exception:
            return _json_response(
                {"error": "Invalid JSON payload"},
                status=400,
            )

        interaction_type = interaction.get("type")

        # Discord PING
        if interaction_type == 1:
            return _json_response({
                "type": 1,
            })

        # Slash command
        if interaction_type == 2:

            data = interaction.get("data") or {}

            cmd_name = str(
                data.get("name") or ""
            )

            result = await _dispatch_command(
                cmd_name,
                interaction,
                env,
                ctx,
            )

            return _json_response(result)

        # Button/component interaction
        if interaction_type == 3:

            result = await _dispatch_component(
                interaction,
                env,
                ctx,
            )

            return _json_response(result)

        return _json_response(
            {
                "error": "Unsupported interaction type",
                "type": interaction_type,
            },
            status=400,
        )
        
