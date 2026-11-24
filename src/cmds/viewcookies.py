
import json

_KV_POOL_KEY = "cookie_pool"


def _reply(content: str) -> dict:
    """Return a public Discord interaction response."""
    return {
        "type": 4,
        "data": {
            "content": content,
        },
    }


async def run(message: dict, env):
    kv = getattr(env, "UhewBotCookieKV", None)

    if kv is None:
        return _reply("`UhewBotCookieKV` is not bound.")

    try:
        raw_pool = await kv.get(_KV_POOL_KEY)
    except Exception as exc:
        return _reply(
            f"Failed to read the cookie pool: `{str(exc)[:300]}`"
        )

    try:
        pool = json.loads(raw_pool) if raw_pool else []
    except Exception:
        return _reply("Failed to parse `cookie_pool` from KV.")

    if not isinstance(pool, list):
        return _reply("`cookie_pool` has an invalid format.")

    if not pool:
        return _reply(
            "No accounts are currently stored.\n"
            "Use `/addcookie` to add one."
        )

    lines = []

    for index, entry in enumerate(pool, start=1):
        if isinstance(entry, dict):
            username = (
                entry.get("username")
                or entry.get("user_id")
                or f"Account #{index}"
            )
            cookie = str(entry.get("cookie") or "").strip()

        elif isinstance(entry, str):
            username = f"Account #{index}"
            cookie = entry.strip()

        else:
            username = f"Account #{index}"
            cookie = ""

        if cookie:
            lines.append(f"`{index}.` **{username}**")
        else:
            lines.append(
                f"`{index}.` **{username}** — Missing cookie"
            )

    content = (
        "**Stored Roblox Accounts**\n"
        + "\n".join(lines)
        + f"\n\n**Total:** {len(pool)} account(s)"
    )

    return _reply(content)
    
