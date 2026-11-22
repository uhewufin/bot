from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
from typing import Optional, Tuple

from workers import Response, WorkerEntrypoint

# Ed25519 parameters from RFC 8032. Keeping verification in the standard
# library avoids native-extension dependencies in Cloudflare's Pyodide runtime.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
_IDENTITY = (0, 1)
_Point = Tuple[int, int]
_CMD_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def _decode_point(encoded: bytes) -> Optional[_Point]:
    if len(encoded) != 32:
        return None

    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        return None

    y_squared = (y * y) % _Q
    denominator = (_D * y_squared + 1) % _Q
    if denominator == 0:
        return None
    x_squared = ((y_squared - 1) * pow(denominator, _Q - 2, _Q)) % _Q

    x = pow(x_squared, (_Q + 3) // 8, _Q)
    if (x * x - x_squared) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - x_squared) % _Q != 0:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = _Q - x

    return (x, y)


def _add_points(left: _Point, right: _Point) -> _Point:
    x1, y1 = left
    x2, y2 = right
    product = (_D * x1 * x2 * y1 * y2) % _Q
    x_denominator = (1 + product) % _Q
    y_denominator = (1 - product) % _Q
    x = ((x1 * y2 + y1 * x2) * pow(x_denominator, _Q - 2, _Q)) % _Q
    y = ((y1 * y2 + x1 * x2) * pow(y_denominator, _Q - 2, _Q)) % _Q
    return (x, y)


def _multiply_point(scalar: int, point: _Point) -> _Point:
    result = _IDENTITY
    addend = point
    while scalar:
        if scalar & 1:
            result = _add_points(result, addend)
        addend = _add_points(addend, addend)
        scalar >>= 1
    return result


def _verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    try:
        public_key = bytes.fromhex(public_key_hex)
        signature = bytes.fromhex(signature_hex)
    except (TypeError, ValueError):
        return False

    if len(public_key) != 32 or len(signature) != 64:
        return False

    public_point = _decode_point(public_key)
    r_point = _decode_point(signature[:32])
    if public_point is None or r_point is None:
        return False

    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= _L:
        return False

    # Reject points outside the prime-order subgroup.
    if _multiply_point(_L, public_point) != _IDENTITY:
        return False
    if _multiply_point(_L, r_point) != _IDENTITY:
        return False

    base_point = _decode_point(bytes.fromhex("58" + "66" * 31))
    if base_point is None:  # Defensive; the constant is defined by RFC 8032.
        return False

    challenge = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
    ) % _L
    return _multiply_point(scalar, base_point) == _add_points(
        r_point, _multiply_point(challenge, public_point)
    )


async def _run_command_module(cmd_name: str, message: dict, env) -> Response:
    if not _CMD_NAME_RE.match(cmd_name):
        return Response.json(
            {"type": 4, "data": {"content": f"Invalid command name: `{cmd_name}`"}}
        )

    try:
        module = importlib.import_module(f"cmd.{cmd_name}")
    except ModuleNotFoundError as err:
        here = os.path.dirname(__file__)
        cmd_dir = os.path.join(here, "cmd")
        here_items = os.listdir(here) if os.path.exists(here) else ["<missing>"]
        cmd_items = os.listdir(cmd_dir) if os.path.exists(cmd_dir) else ["<no cmd dir>"]
        debug_text = (
            f"Error: {err}\n"
            f"here={here}\n"
            f"here_items={here_items}\n"
            f"cmd_items={cmd_items}\n"
            f"sys.path={sys.path}"
        )
        return Response.json({"type": 4, "data": {"content": debug_text[:1900]}})

    handler = getattr(module, "run", None)
    if not callable(handler):
        return Response.json(
            {"type": 4, "data": {"content": f"`cmd/{cmd_name}.py` is missing `async def run(message, env)`."}}
        )

    result = await handler(message, env)
    if isinstance(result, Response):
        return result
    return Response.json(result)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method == "GET":
            return Response(
                "Hey, dude. I dont know how you got here but this is where all of "
                "the bot stuff happens, so bye now.",
                status=405,
            )

        signature = request.headers.get("x-signature-ed25519")
        timestamp = request.headers.get("x-signature-timestamp")
        if not signature or not timestamp:
            return Response("Invalid request signature", status=401)

        try:
            body = (await request.text()).encode("utf-8")
        except Exception:
            return Response("Invalid request body", status=400)

        public_key = self.env.PK
        if not _verify_ed25519(public_key, signature, timestamp.encode("utf-8") + body):
            return Response("Invalid request signature", status=401)

        try:
            message = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response("Invalid JSON", status=400)

        if not isinstance(message, dict):
            return Response("Invalid interaction", status=400)

        if message.get("type") == 1:  # Discord PING
            return Response.json({"type": 1})

        if message.get("type") == 2:  # Discord application command
            data = message.get("data") or {}
            cmd_name = data.get("name")
            if not isinstance(cmd_name, str):
                return Response.json(
                    {"type": 4, "data": {"content": "Missing command name."}}
                )
            return await _run_command_module(cmd_name, message, self.env)

        return Response("Unknown", status=404)
        
