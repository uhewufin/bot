import json

_KV_POOL_KEY = "cookie_pool"


def _ephemeral(content: str) -> dict:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": 64,
        },
    }


async def run(message: dict, env):
    kv = getattr(env, "UhewBotCookieKV", None)
    if kv is None:
        return _ephemeral("`UhewBotCookieKV` is not bound—cannot read cookie pool.")

    raw = await kv.get(_KV_POOL_KEY)
    try:
        pool = json.loads(raw) if raw else []
        if not isinstance(pool, list):
            pool = []
    except Exception:
        return _ephemeral("Failed to parse `cookie_pool` from KV.")

    if not pool:
        return _ephemeral("Cookie pool is currently empty. Use `/addcookie` to add one.")

    lines = [f"**Cookie Pool ({len(pool)} account{'s' if len(pool) != 1 else ''}):**"]
    for i, entry in enumerate(pool, start=1):
        if isinstance(entry, dict):
            username = entry.get("username") or "Unknown"
            user_id = entry.get("user_id")
            if user_id:
                lines.append(f"{i}. **{username}** (`{user_id}`)")
            else:
                lines.append(f"{i}. **{username}**")
        else:
            lines.append(f"{i}. *Legacy entry (no username)*")

    return _ephemeral("\n".join(lines))
  
