import json
import re
from workers import fetch

_TOKEN_RE = re.compile(r"^_\|WARNING:[^|\s]+\|_[A-Za-z0-9._-]+$")
_KV_POOL_KEY = "cookie_pool"


def _normalize_cookie(raw: str) -> str:
    value = raw.strip()
    if value.startswith(".ROBLOSECURITY="):
        value = value[len(".ROBLOSECURITY=") :].strip()
    return value


def _extract_cookie_option(message: dict) -> str:
    options = ((message.get("data") or {}).get("options")) or []
    for opt in options:
        if opt.get("name") == "cookie":
            return str(opt.get("value") or "")
    return ""


def _ephemeral(content: str) -> dict:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": 64,
        },
    }


async def _check_cookie_alive(cookie_val: str) -> tuple[bool, dict | None, str]:
    res = await fetch(
        "https://users.roblox.com/v1/users/authenticated",
        headers={
            "Cookie": f".ROBLOSECURITY={cookie_val}",
            "Accept": "application/json",
            "User-Agent": "Roblox/WinInet",
        },
    )
    if res.status != 200:
        return (
            False,
            None,
            f"Roblox auth check failed (HTTP {res.status}; cookie may be expired, invalid, or termed).",
        )

    try:
        data = json.loads(await res.text())
    except Exception:
        return False, None, "Roblox auth check returned invalid JSON."

    user_id = data.get("id")
    username = data.get("name")
    if not user_id or not username:
        return False, None, "Cookie did not resolve to an authenticated user."

    return True, {"id": user_id, "name": username}, f"Valid cookie for **{username}** (`{user_id}`)."


async def _save_to_kv(env, cookie_val: str, user_info: dict) -> tuple[bool, str]:
    kv = getattr(env, "UhewBotCookieKV", None)
    if kv is None:
        return (
            False,
            "Cookie was verified, **but `UhewBotCookieKV` is not bound in `wrangler.toml`**—nothing was saved.",
        )

    existing_raw = await kv.get(_KV_POOL_KEY)
    try:
        pool = json.loads(existing_raw) if existing_raw else []
        if not isinstance(pool, list):
            pool = []
    except Exception:
        pool = []

    updated = False
    for idx, entry in enumerate(pool):
        if isinstance(entry, dict) and entry.get("cookie") == cookie_val:
            pool[idx] = {
                "cookie": cookie_val,
                "username": user_info["name"],
                "user_id": user_info["id"],
            }
            updated = True
            break
        elif isinstance(entry, str) and entry == cookie_val:
            pool[idx] = {
                "cookie": cookie_val,
                "username": user_info["name"],
                "user_id": user_info["id"],
            }
            updated = True
            break

    if not updated:
        pool.append(
            {
                "cookie": cookie_val,
                "username": user_info["name"],
                "user_id": user_info["id"],
            }
        )

    await kv.put(_KV_POOL_KEY, json.dumps(pool))
    status_word = "Updated existing entry in" if updated else "Saved to"
    return True, f"{status_word} KV ({len(pool)} total in pool)."


async def run(message: dict, env):
    raw_cookie = _extract_cookie_option(message)
    cookie_val = _normalize_cookie(raw_cookie)

    if not cookie_val or not _TOKEN_RE.match(cookie_val):
        return _ephemeral(
            "Invalid `.ROBLOSECURITY` format. Expected a token starting with `_|WARNING:...`."
        )

    ok, user_info, detail = await _check_cookie_alive(cookie_val)
    if not ok or not user_info:
        return _ephemeral(f"Cookie rejected: {detail}")

    saved, storage_msg = await _save_to_kv(env, cookie_val, user_info)
    if not saved:
        return _ephemeral(f"{detail}\n{storage_msg}")

    return _ephemeral(f"Cookie verified and added. {detail}\n{storage_msg}")
    
