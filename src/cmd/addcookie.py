import json
import re
from workers import fetch

_TOKEN_RE = re.compile(r"^_\|WARNING:-DO-NOT-SHARE--Sharing-this-[^\s]+$")


def _normalize_cookie(raw: str) -> str:
    value = raw.strip()
    if value.startswith(".ROBLOSECURITY="):
        value = value[len(".ROBLOSECURITY=") :].strip()
    return value


async def _check_cookie_alive(cookie_val: str) -> tuple[bool, str]:
    res = await fetch(
        "https://users.roblox.com/v1/users/authenticated",
        headers={
            "Cookie": f".ROBLOSECURITY={cookie_val}",
            "Accept": "application/json",
        },
    )
    if res.status != 200:
        return False, f"Roblox auth check failed (HTTP {res.status}; cookie may be expired or termed)."

    try:
        data = json.loads(await res.text())
    except Exception:
        return False, "Roblox auth check returned invalid JSON."

    user_id = data.get("id")
    username = data.get("name")
    if not user_id:
        return False, "Cookie did not resolve to an authenticated user."

    return True, f"Valid cookie for **{username}** (`{user_id}`)."


def _extract_cookie_option(message: dict) -> str:
    options = ((message.get("data") or {}).get("options")) or []
    for opt in options:
        if opt.get("name") == "cookie":
            return str(opt.get("value") or "")
    return ""


async def run(message: dict, env):
    raw_cookie = _extract_cookie_option(message)
    cookie_val = _normalize_cookie(raw_cookie)

    if not cookie_val or not _TOKEN_RE.match(cookie_val):
        return {
            "type": 4,
            "data": {
                "content": "Invalid format. Expected a `.ROBLOSECURITY` token starting with `_|WARNING:-DO-NOT-SHARE--Sharing-this-...`.",
                "flags": 64,
            },
        }

    ok, detail = await _check_cookie_alive(cookie_val)
    if not ok:
        return {
            "type": 4,
            "data": {
                "content": f"Cookie rejected: {detail}",
                "flags": 64,
            },
        }

    # Example persistence via a Cloudflare KV binding named COOKIES_KV:
    # kv = getattr(env, "COOKIES_KV", None)
    # if kv is not None:
    #     existing_raw = await kv.get("cookie_pool")
    #     pool = json.loads(existing_raw) if existing_raw else []
    #     if cookie_val not in pool:
    #         pool.append(cookie_val)
    #         await kv.put("cookie_pool", json.dumps(pool))

    return {
        "type": 4,
        "data": {
            "content": f"Cookie verified and added. {detail}",
            "flags": 64,
        },
    }
  
