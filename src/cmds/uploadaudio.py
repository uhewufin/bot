
import base64
import json
import os
import re
import uuid

from workers import fetch

_KV_POOL_KEY = "cookie_pool"
_PENDING_PREFIX = "pending_audio:"
_PUBLISH_URL = "https://publish.roblox.com/v1/audio"
_GROUP_JOIN_URL = "https://groups.roblox.com/v1/groups/{}/users"
_GROUPS_URL = "https://groups.roblox.com/v2/users/{}/groups/roles"
_AUTH_URL = "https://users.roblox.com/v1/users/authenticated"

_MAX_BYTES = 20 * 1024 * 1024

_DISCORD_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "DiscordBot (https://bot.liampark79394.workers.dev/, 1.0)",
}

_ROBLOX_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Roblox/WinInet",
}


def _schedule_background(ctx, coro):
    # Cloudflare's Python Workers SDK exposes ExecutionContext.waitUntil.
    wait_until = getattr(ctx, "waitUntil", None) if ctx is not None else None

    if not callable(wait_until):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "Cloudflare ExecutionContext.waitUntil is unavailable; "
            "background upload was not started."
        )

    wait_until(coro)


async def _send_message(app_id: str, token: str, content: str):
    """Edit the deferred Discord response instead of leaving it on Thinking."""

    if not app_id or not token:
        print("[uploadaudio] Missing Discord application ID or token.")
        return

    url = (
        f"https://discord.com/api/v10/webhooks/"
        f"{app_id}/{token}/messages/@original"
    )

    try:
        res = await fetch(
            url,
            method="PATCH",
            headers=_DISCORD_HEADERS,
            body=json.dumps({
                "content": content[:1950],
            }),
        )

        if res.status >= 300:
            body = await res.text()
            print(
                f"[uploadaudio] Discord response edit failed: "
                f"{res.status} {body[:500]}"
            )

    except Exception as exc:
        print(f"[uploadaudio] Discord response exception: {exc}")


def _message(content: str, components: list | None = None) -> dict:
    data = {"content": content[:1950]}

    if components is not None:
        data["components"] = components

    return {
        "type": 4,
        "data": data,
    }


def _parse_groups(env):
    raw_groups = getattr(env, "groups", None)

    if not raw_groups:
        return [], "`groups` environment variable is not set."

    try:
        parsed = (
            json.loads(raw_groups)
            if isinstance(raw_groups, str)
            else raw_groups
        )
    except Exception:
        return [], "Failed to parse `groups` environment variable as JSON."

    if not isinstance(parsed, dict) or not parsed:
        return [], "`groups` must be a non-empty JSON object."

    groups = []

    for name, gid in parsed.items():
        try:
            group_id = int(gid)
        except (TypeError, ValueError):
            return [], f"Invalid group ID for `{name}`: `{gid}`."

        label = str(name).strip()

        if label:
            groups.append((label[:80], group_id))

    if not groups:
        return [], "No valid groups found."

    if len(groups) > 25:
        return [], "Discord supports at most 25 buttons per message."

    return groups, None


def _extract_options(message: dict) -> dict:
    data = message.get("data") or {}
    options = data.get("options") or []

    resolved_attachments = (
        (data.get("resolved") or {}).get("attachments")
    ) or {}

    parsed = {
        "attachment": None,
        "name": None,
    }

    for opt in options:
        opt_name = opt.get("name")
        val = opt.get("value")

        if opt_name == "file" and val is not None:
            attachment = resolved_attachments.get(str(val))

            if isinstance(attachment, dict):
                parsed["attachment"] = attachment

        elif opt_name == "name" and val is not None:
            parsed["name"] = str(val).strip()

    return parsed


def _build_group_buttons(job_id, groups):
    rows = []

    for i in range(0, len(groups), 5):
        chunk = groups[i:i + 5]

        components = [
            {
                "type": 2,
                "style": 1,
                "label": group_name,
                "custom_id": (
                    f"uploadaudio_group:{job_id}:{group_id}"
                ),
            }
            for group_name, group_id in chunk
        ]

        rows.append({
            "type": 1,
            "components": components,
        })

    return rows


def _normalize_cookie(raw: str) -> str:
    value = (raw or "").strip()

    if value.startswith(".ROBLOSECURITY="):
        value = value[len(".ROBLOSECURITY="):].strip()

    return value


async def _get_first_cookie(env):
    kv = getattr(env, "UhewBotCookieKV", None)

    if kv is None:
        return None, None, "`UhewBotCookieKV` is not bound."

    raw = await kv.get(_KV_POOL_KEY)

    try:
        pool = json.loads(raw) if raw else []
    except Exception:
        return None, None, "Failed to parse `cookie_pool` from KV."

    if not isinstance(pool, list) or not pool:
        return None, None, (
            "Cookie pool is empty. Add an account with `/addcookie` first."
        )

    first = pool[0]

    if isinstance(first, dict):
        cookie_val = _normalize_cookie(
            str(first.get("cookie") or "")
        )

        label = (
            first.get("username")
            or str(first.get("user_id") or "Account #1")
        )

        if cookie_val:
            return cookie_val, label, None

        return None, None, "First cookie pool entry has no cookie value."

    if isinstance(first, str):
        cookie_val = _normalize_cookie(first)

        if cookie_val:
            return cookie_val, "Account #1", None

    return None, None, "First cookie pool entry is invalid."


async def _roblox_request(
    url: str,
    cookie_val: str,
    method: str = "GET",
    body=None,
):
    """Make a Roblox request and retry once if Roblox returns a CSRF token."""

    headers = {
        **_ROBLOX_HEADERS,
        "Cookie": f".ROBLOSECURITY={cookie_val}",
    }

    request_body = None

    if body is not None:
        headers["Content-Type"] = "application/json"
        request_body = json.dumps(body)

    res = await fetch(
        url,
        method=method,
        headers=headers,
        body=request_body,
    )

    if res.status == 403:
        csrf = res.headers.get("x-csrf-token")

        if csrf:
            headers["x-csrf-token"] = csrf

            res = await fetch(
                url,
                method=method,
                headers=headers,
                body=request_body,
            )

    text_body = await res.text()

    try:
        data = json.loads(text_body) if text_body else {}
    except Exception:
        data = {"raw": text_body[:500]}

    return res.status, data


async def _ensure_group_membership(cookie_val: str, group_id: int):
    """Check membership and attempt to join if necessary."""

    try:
        status, account = await _roblox_request(
            _AUTH_URL,
            cookie_val,
        )
    except Exception as exc:
        return False, f"Could not verify Roblox account: {exc}"

    if status != 200:
        return False, (
            f"Roblox authentication failed (HTTP {status}). "
            "The account cookie may be invalid or expired."
        )

    user_id = account.get("id") if isinstance(account, dict) else None
    username = account.get("name") if isinstance(account, dict) else None

    if not user_id:
        return False, "Roblox did not return an authenticated account ID."

    try:
        status, memberships = await _roblox_request(
            _GROUPS_URL.format(user_id),
            cookie_val,
        )
    except Exception as exc:
        return False, f"Could not check group membership: {exc}"

    if status != 200:
        return False, (
            f"Could not check group membership (HTTP {status}): "
            f"{json.dumps(memberships)[:400]}"
        )

    group_entries = (
        memberships.get("data", [])
        if isinstance(memberships, dict)
        else []
    )

    for entry in group_entries:
        group = entry.get("group") or {}

        try:
            existing_group_id = int(group.get("id"))
        except (TypeError, ValueError):
            continue

        if existing_group_id == group_id:
            return True, (
                f"**{username or 'Account'}** is already a member "
                f"of group `{group_id}`."
            )

    try:
        status, join_result = await _roblox_request(
            _GROUP_JOIN_URL.format(group_id),
            cookie_val,
            method="POST",
            body={},
        )
    except Exception as exc:
        return False, f"Could not submit group join request: {exc}"

    if status < 200 or status >= 300:
        detail = json.dumps(join_result)[:500]

        if status == 403:
            return False, (
                "Roblox denied the group join request (HTTP 403). "
                "The group may require approval, a CAPTCHA, or other "
                f"conditions. Roblox response: `{detail}`"
            )

        if status == 429:
            return False, (
                "Roblox rate-limited the group join request. "
                "Wait before trying again."
            )

        return False, (
            f"Could not join group `{group_id}` (HTTP {status}): "
            f"`{detail}`"
        )

    try:
        verify_status, verify_data = await _roblox_request(
            _GROUPS_URL.format(user_id),
            cookie_val,
        )
    except Exception as exc:
        return False, (
            f"Roblox accepted the join request, but membership "
            f"could not be verified: {exc}"
        )

    if verify_status == 200:
        entries = verify_data.get("data", [])

        for entry in entries:
            group = entry.get("group") or {}

            try:
                if int(group.get("id")) == group_id:
                    return True, (
                        f"**{username or 'Account'}** successfully "
                        f"joined group `{group_id}`."
                    )
            except (TypeError, ValueError):
                continue

    return False, (
        "Roblox accepted the join request, but the account is not "
        "confirmed as a member yet. The group may require approval. "
        "The audio upload was not started."
    )


def _estimate_duration_seconds(raw_bytes: bytes, filename: str) -> float:
    ext = os.path.splitext(filename.lower())[1]

    bytes_per_sec = {
        ".mp3": 16000,
        ".ogg": 16000,
        ".wav": 176400,
        ".flac": 100000,
    }.get(ext, 16000)

    return round(max(1.0, len(raw_bytes) / bytes_per_sec), 2)


async def _response_to_bytes(res) -> bytes:
    if hasattr(res, "bytes"):
        b = await res.bytes()
        return bytes(b)

    buf = await res.arrayBuffer()
    py_buf = buf.to_py()

    if isinstance(py_buf, (bytes, bytearray, memoryview)):
        return bytes(py_buf)

    try:
        from js import Uint8Array
        return bytes(Uint8Array.new(buf).to_py())
    except Exception:
        return bytes(py_buf)


async def _download_attachment(url: str):
    res = await fetch(url, method="GET")

    if res.status != 200:
        return None, (
            f"Failed to download Discord attachment "
            f"(HTTP {res.status})."
        )

    raw_bytes = await _response_to_bytes(res)

    if not raw_bytes:
        return None, "Downloaded attachment is empty."

    if len(raw_bytes) > _MAX_BYTES:
        return None, (
            f"Audio file exceeds the 20 MB limit "
            f"({len(raw_bytes)} bytes)."
        )

    return raw_bytes, None


async def _upload_to_roblox_group(
    cookie_val: str,
    raw_bytes: bytes,
    audio_name: str,
    duration_seconds: float,
    group_id: int,
):
    payload = {
        "name": audio_name[:50],
        "file": base64.b64encode(raw_bytes).decode("utf-8"),
        "groupId": group_id,
        "paymentSource": "Group",
        "estimatedFileSize": len(raw_bytes),
        "estimatedDuration": duration_seconds,
        "assetPrivacy": 1,
    }

    status, data = await _roblox_request(
        _PUBLISH_URL,
        cookie_val,
        method="POST",
        body=payload,
    )

    if status < 200 or status >= 300:
        return False, (
            f"Roblox upload failed (HTTP {status}): "
            f"{json.dumps(data)[:500]}"
        )

    return True, data


async def run(message: dict, env, ctx=None):
    groups, group_err = _parse_groups(env)

    if group_err:
        return _message(f"Group config error: {group_err}")

    kv = getattr(env, "UhewBotCookieKV", None)

    if kv is None:
        return _message("`UhewBotCookieKV` is not bound.")

    opts = _extract_options(message)
    attachment = opts["attachment"]

    if not attachment:
        return _message(
            "Missing `file` attachment. Attach an audio file "
            "(`.mp3`, `.ogg`, `.wav`, `.flac`)."
        )

    filename = str(attachment.get("filename") or "audio.mp3")
    allowed_exts = (".mp3", ".ogg", ".wav", ".flac")

    if not filename.lower().endswith(allowed_exts):
        return _message(
            f"Unsupported file type for `{filename}`. "
            f"Allowed: `{', '.join(allowed_exts)}`."
        )

    att_url = attachment.get("url")

    if not att_url:
        return _message("Attachment did not include a download URL.")

    base_title = opts["name"] or os.path.splitext(filename)[0]

    audio_name = (
        re.sub(r"\s+", " ", base_title).strip()
        or "Uploaded Audio"
    )

    job_id = uuid.uuid4().hex[:16]

    pending_data = {
        "url": att_url,
        "filename": filename,
        "name": audio_name,
        "groups": {
            name: gid
            for name, gid in groups
        },
    }

    await kv.put(
        f"{_PENDING_PREFIX}{job_id}",
        json.dumps(pending_data),
        expirationTtl=600,
    )

    return _message(
        f"Select a group to upload **{audio_name}** (`{filename}`) to:",
        components=_build_group_buttons(job_id, groups),
    )


async def _on_button_click(
    interaction: dict,
    env,
    job_id: str,
    group_id: int,
):
    app_id = str(interaction.get("application_id") or "")
    token = str(interaction.get("token") or "")

    try:
        kv = getattr(env, "UhewBotCookieKV", None)

        if kv is None:
            await _send_message(
                app_id, token,
                "`UhewBotCookieKV` is not bound.",
            )
            return

        pending_key = f"{_PENDING_PREFIX}{job_id}"
        raw_pending = await kv.get(pending_key)

        if not raw_pending:
            await _send_message(
                app_id, token,
                "This upload prompt expired or was already used. "
                "Run `/uploadaudio` again.",
            )
            return

        await kv.delete(pending_key)

        try:
            pending = json.loads(raw_pending)
        except Exception:
            await _send_message(
                app_id, token,
                "Failed to read pending upload data.",
            )
            return

        att_url = pending.get("url")
        filename = str(pending.get("filename") or "audio.mp3")
        audio_name = str(pending.get("name") or "Uploaded Audio")
        group_map = pending.get("groups") or {}

        group_label = next(
            (
                name
                for name, gid in group_map.items()
                if int(gid) == group_id
            ),
            f"Group {group_id}",
        )

        if not any(
            int(gid) == group_id
            for gid in group_map.values()
        ):
            await _send_message(
                app_id, token,
                "That group wasn't part of this upload prompt.",
            )
            return

        await _send_message(
            app_id, token,
            f"Checking account and group membership for "
            f"**{group_label}** (`{group_id}`)...",
        )

        cookie_val, account_label, cookie_err = (
            await _get_first_cookie(env)
        )

        if cookie_err or not cookie_val:
            await _send_message(
                app_id, token,
                f"Could not get cookie from pool: {cookie_err}",
            )
            return

        joined, membership_msg = await _ensure_group_membership(
            cookie_val,
            group_id,
        )

        if not joined:
            await _send_message(
                app_id, token,
                f"Group membership check failed for "
                f"**{account_label}**:\n{membership_msg}",
            )
            return

        await _send_message(
            app_id, token,
            f"{membership_msg}\n"
            f"Downloading **{audio_name}**...",
        )

        raw_bytes, dl_err = await _download_attachment(
            str(att_url)
        )

        if dl_err or raw_bytes is None:
            await _send_message(
                app_id, token,
                dl_err or "Failed to download file.",
            )
            return

        duration = _estimate_duration_seconds(
            raw_bytes,
            filename,
        )

        await _send_message(
            app_id, token,
            f"Uploading **{audio_name}** to **{group_label}** "
            f"using **{account_label}**...",
        )

        ok, result = await _upload_to_roblox_group(
            cookie_val,
            raw_bytes,
            audio_name,
            duration,
            group_id,
        )

        if not ok:
            await _send_message(
                app_id, token,
                f"Upload to **{group_label}** (`{group_id}`) "
                f"using **{account_label}** failed:\n{result}",
            )
            return

        asset_id = (
            result.get("Id")
            if isinstance(result, dict)
            else None
        )

        if asset_id:
            await _send_message(
                app_id, token,
                f"Uploaded **{audio_name}** to **{group_label}** "
                f"(`{group_id}`) via **{account_label}**!\n"
                f"- **Asset ID:** `{asset_id}`\n"
                f"- **Sound ID:** `rbxassetid://{asset_id}`",
            )
        else:
            await _send_message(
                app_id, token,
                f"Upload request completed, but Roblox did not return "
                f"an asset ID:\n`{json.dumps(result)[:500]}`",
            )

    except Exception as exc:
        print(f"[uploadaudio] Exception: {exc}")

        await _send_message(
            app_id, token,
            f"Unexpected upload error: `{str(exc)[:500]}`",
        )


async def handle_component(
    interaction: dict,
    env,
    ctx=None,
) -> dict:

    custom_id = str(
        ((interaction.get("data") or {}).get("custom_id")) or ""
    )

    parts = custom_id.split(":")

    if len(parts) != 3 or parts[0] != "uploadaudio_group":
        return {
            "type": 4,
            "data": {
                "content": "Invalid upload button.",
                "flags": 64,
            },
        }

    job_id = parts[1]

    try:
        group_id = int(parts[2])
    except (TypeError, ValueError):
        return {
            "type": 4,
            "data": {
                "content": "Invalid group ID.",
                "flags": 64,
            },
        }

    try:
        _schedule_background(
            ctx,
            _on_button_click(
                interaction,
                env,
                job_id,
                group_id,
            ),
        )

    except Exception as exc:
        print(f"[uploadaudio] Failed to schedule upload: {exc}")

        return {
            "type": 4,
            "data": {
                "content": f"Could not start upload: `{exc}`",
                "flags": 64,
            },
        }

    return {
        "type": 5,
        "data": {
            "flags": 64,
        },
    }
    
