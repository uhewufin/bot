
import json

_KV_POOL_KEY = "cookie_pool"


def _reply(content: str, ephemeral: bool = False) -> dict:
    data = {"content": content}

    if ephemeral:
        data["flags"] = 64

    return {"type": 4, "data": data}


def _extract_index(message: dict) -> str:
    options = ((message.get("data") or {}).get("options")) or []

    for option in options:
        if option.get("name") == "index":
            return str(option.get("value") or "").strip()

    return ""


def _requester_id(message: dict) -> str:
    user = message.get("user") or {}

    if not user:
        user = ((message.get("member") or {}).get("user")) or {}

    return str(user.get("id") or "")


async def run(message: dict, env):
    owner_id = str(
        getattr(env, "OWNER_ID", "") or ""
    ).strip()

    requester_id = _requester_id(message)

    if not owner_id:
        return _reply(
            "`OWNER_ID` is not configured.",
            ephemeral=True,
        )

    if not requester_id or requester_id != owner_id:
        return _reply(
            "You are not authorized to use `/removecookie`.",
            ephemeral=True,
        )

    raw_index = _extract_index(message)

    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return _reply(
            "Provide a valid index, e.g. `/removecookie index:2`."
        )

    if index < 1:
        return _reply("Cookie index must be 1 or greater.")

    kv = getattr(env, "UhewBotCookieKV", None)

    if kv is None:
        return _reply("`UhewBotCookieKV` is not bound.")

    raw_pool = await kv.get(_KV_POOL_KEY)

    try:
        pool = json.loads(raw_pool) if raw_pool else []
    except Exception:
        return _reply("Failed to parse `cookie_pool`.")

    if not isinstance(pool, list):
        return _reply("`cookie_pool` has an invalid format.")

    if index > len(pool):
        return _reply(
            f"Cookie #{index} does not exist. "
            f"There are {len(pool)} account(s) in the pool."
        )

    removed = pool.pop(index - 1)

    await kv.put(
        _KV_POOL_KEY,
        json.dumps(pool),
    )

    if isinstance(removed, dict):
        username = str(removed.get("username") or "Unknown")
        user_id = str(removed.get("user_id") or "")

        account_label = (
            f"**{username}** (`{user_id}`)"
            if user_id else f"**{username}**"
        )
    else:
        account_label = "legacy cookie entry"

    return _reply(
        f"Removed cookie #{index}: {account_label}.\n"
        f"**Remaining accounts:** {len(pool)}"
    )
  
