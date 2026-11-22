import base64
import json
import os
import re
import uuid
from workers import fetch

_KV_POOL_KEY = "cookie_pool"
_PENDING_PREFIX = "pending_audio:"
_PUBLISH_URL = "https://publish.roblox.com/v1/audio"
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

_DISCORD_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "DiscordBot (https://bot.liampark79394.workers.dev/, 1.0)",
}


def _message(content: str, components: list | None = None) -> dict:
    data = {"content": content}
    if components is not None:
        data["components"] = components
    return {
        "type": 4,
        "data": data,
    }


def _deferred_update_message() -> dict:
    return {
        "type": 6,
    }


def _parse_groups(env) -> tuple[list[tuple[str, int]], str | None]:
    raw_groups = getattr(env, "groups", None)
    if not raw_groups:
        return [], "`groups` environment variable is not set."

    try:
        parsed = json.loads(raw_groups) if isinstance(raw_groups, str) else raw_groups
    except Exception:
        return [], "Failed to parse `groups` environment variable as JSON."

    if not isinstance(parsed, dict) or not parsed:
        return [], "`groups` must be a non-empty JSON object of `{\"group name\": group_id}`."

    groups: list[tuple[str, int]] = []
    for name, gid in parsed.items():
        try:
            group_id = int(gid)
        except (TypeError, ValueError):
            return [], f"Invalid group ID for `{name}`: `{gid}`."
        label = str(name).strip()
        if label:
            groups.append((label[:80], group_id))

    if not groups:
        return [], "No valid groups found in `groups`."

    if len(groups) > 25:
        return [], "Discord supports at most 25 buttons per message; trim `groups` to 25 or fewer."

    return groups, None


def _extract_options(message: dict) -> dict:
    data = message.get("data") or {}
    options = data.get("options") or []
    resolved_attachments = ((data.get("resolved") or {}).get("attachments")) or {}

    parsed = {
        "attachment": None,
        "name": None,
    }

    for opt in options:
        opt_name = opt.get("name")
        val = opt.get("value")
        if opt_name == "file" and val is not None:
            att = resolved_attachments.get(str(val))
            if isinstance(att, dict):
                parsed["attachment"] = att
        elif opt_name == "name" and val is not None:
            parsed["name"] = str(val).strip()

    return parsed


def _build_group_buttons(job_id: str, groups: list[tuple[str, int]]) -> list[dict]:
    rows: list[dict] = []
    for i in range(0, len(groups), 5):
        chunk = groups[i : i + 5]
        components = [
            {
                "type": 2,
                "style": 1,
                "label": group_name,
                "custom_id": f"uploadaudio_group:{job_id}:{group_id}",
            }
            for group_name, group_id in chunk
        ]
        rows.append({"type": 1, "components": components})
    return rows


def _normalize_cookie(raw: str) -> str:
    value = (raw or "").strip()
    if value.startswith(".ROBLOSECURITY="):
        value = value[len(".ROBLOSECURITY=") :].strip()
    return value


async def _get_first_cookie(env) -> tuple[str | None, str | None, str | None]:
    kv = getattr(env, "UhewBotCookieKV", None)
    if kv is None:
        return None, None, "`UhewBotCookieKV` is not bound."

    raw = await kv.get(_KV_POOL_KEY)
    try:
        pool = json.loads(raw) if raw else []
    except Exception:
        return None, None, "Failed to parse `cookie_pool` from KV."

    if not isinstance(pool, list) or not pool:
        return None, None, "Cookie pool is empty. Add one with `/addcookie` first."

    first = pool[0]
    if isinstance(first, dict):
        cookie_val = _normalize_cookie(str(first.get("cookie") or ""))
        label = first.get("username") or str(first.get("user_id") or "Account #1")
        if cookie_val:
            return cookie_val, label, None
        return None, None, "First entry in `cookie_pool` is missing a `cookie` value."

    if isinstance(first, str):
        cookie_val = _normalize_cookie(first)
        if cookie_val:
            return cookie_val, "Account #1", None

    return None, None, "First entry in `cookie_pool` is invalid."


def _estimate_duration_seconds(raw_bytes: bytes, filename: str) -> float:
    ext = os.path.splitext(filename.lower())[1]
    bytes_per_sec = {
        ".mp3": 16000,
        ".ogg": 16000,
        ".wav": 176400,
        ".flac": 100000,
    }.get(ext, 16000)
    return round(max(1.0, len(raw_bytes) / bytes_per_sec), 2)


async def _download_attachment(url: str) -> tuple[bytes | None, str | None]:
    res = await fetch(url, method="GET")
    if res.status != 200:
        return None, f"Failed to download Discord attachment (HTTP {res.status})."

    buf = await res.arrayBuffer()
    raw_bytes = bytes(buf.to_py())
    if not raw_bytes:
        return None, "Downloaded attachment is empty."
    if len(raw_bytes) > _MAX_BYTES:
        return None, f"Audio file exceeds the 20 MB limit ({len(raw_bytes)} bytes)."
    return raw_bytes, None


async def _upload_to_roblox_group(
    cookie_val: str,
    raw_bytes: bytes,
    audio_name: str,
    duration_seconds: float,
    group_id: int,
) -> tuple[bool, dict | str]:
    payload = {
        "name": audio_name[:50],
        "file": base64.b64encode(raw_bytes).decode("utf-8"),
        "groupId": group_id,
        "paymentSource": "Group",
        "estimatedFileSize": len(raw_bytes),
        "estimatedDuration": duration_seconds,
        "assetPrivacy": 1,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cookie": f".ROBLOSECURITY={cookie_val}",
        "User-Agent": "Roblox/WinInet",
    }

    res = await fetch(
        _PUBLISH_URL,
        method="POST",
        headers=headers,
        body=json.dumps(payload),
    )

    csrf = res.headers.get("x-csrf-token")
    if res.status == 403 and csrf:
        headers["X-CSRF-TOKEN"] = csrf
        res = await fetch(
            _PUBLISH_URL,
            method="POST",
            headers=headers,
            body=json.dumps(payload),
        )

    text_body = await res.text()
    try:
        data = json.loads(text_body) if text_body else {}
    except Exception:
        data = {"raw": text_body}

    if res.status < 200 or res.status >= 300:
        return False, f"Roblox upload failed (HTTP {res.status}): {json.dumps(data)[:500]}"

    return True, data


async def _send_new_message(app_id: str, token: str, content: str) -> None:
    if not app_id or not token:
        print("[uploadaudio] Missing app_id or token for followup")
        return
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}"
    res = await fetch(
        url,
        method="POST",
        headers=_DISCORD_HEADERS,
        body=json.dumps({"content": content[:1950]}),
    )
    body_text = await res.text()
    print(f"[uploadaudio] Followup POST status={res.status} body={body_text[:300]}")


async def run(message: dict, env, ctx=None):
    groups, group_err = _parse_groups(env)
    if group_err:
        return _message(f"Group config error: {group_err}")

    kv = getattr(env, "UhewBotCookieKV", None)
    if kv is None:
        return _message("`UhewBotCookieKV` is not bound—cannot store pending upload.")

    opts = _extract_options(message)
    attachment = opts["attachment"]
    if not attachment:
        return _message("Missing `file` attachment. Please attach an audio file (`.mp3`, `.ogg`, `.wav`, `.flac`).")

    filename = str(attachment.get("filename") or "audio.mp3")
    allowed_exts = (".mp3", ".ogg", ".wav", ".flac")
    if not filename.lower().endswith(allowed_exts):
        return _message(f"Unsupported file type for `{filename}`. Allowed: `{', '.join(allowed_exts)}`.")

    att_url = attachment.get("url")
    if not att_url:
        return _message("Attachment did not include a download URL.")

    base_title = opts["name"] or os.path.splitext(filename)[0]
    audio_name = re.sub(r"\s+", " ", base_title).strip() or "Uploaded Audio"

    job_id = uuid.uuid4().hex[:16]
    pending_data = {
        "url": att_url,
        "filename": filename,
        "name": audio_name,
        "groups": {name: gid for name, gid in groups},
    }
    await kv.put(f"{_PENDING_PREFIX}{job_id}", json.dumps(pending_data), expirationTtl=600)

    return _message(
        f"Select a group to upload **{audio_name}** (`{filename}`) to:",
        components=_build_group_buttons(job_id, groups),
    )


async def _execute_group_upload(interaction: dict, env, job_id: str, group_id: int) -> None:
    app_id = str(interaction.get("application_id") or "")
    token = str(interaction.get("token") or "")
    print(f"[uploadaudio] Executing job={job_id} group={group_id} app_id={app_id}")

    try:
        kv = getattr(env, "UhewBotCookieKV", None)
        if kv is None:
            await _send_new_message(app_id, token, "`UhewBotCookieKV` is not bound.")
            return

        pending_key = f"{_PENDING_PREFIX}{job_id}"
        raw_pending = await kv.get(pending_key)
        if not raw_pending:
            await _send_new_message(
                app_id,
                token,
                "This upload prompt expired or was already used. Run `/uploadaudio` again.",
            )
            return

        await kv.delete(pending_key)

        try:
            pending = json.loads(raw_pending)
        except Exception:
            await _send_new_message(app_id, token, "Failed to read pending upload data.")
            return

        att_url = pending.get("url")
        filename = str(pending.get("filename") or "audio.mp3")
        audio_name = str(pending.get("name") or "Uploaded Audio")
        group_map = pending.get("groups") or {}
        group_label = next(
            (g_name for g_name, g_id in group_map.items() if int(g_id) == group_id),
            f"Group {group_id}",
        )

        cookie_val, account_label, cookie_err = await _get_first_cookie(env)
        if cookie_err or not cookie_val:
            await _send_new_message(app_id, token, f"Could not get cookie from pool: {cookie_err}")
            return

        raw_bytes, dl_err = await _download_attachment(str(att_url))
        if dl_err or raw_bytes is None:
            await _send_new_message(app_id, token, dl_err or "Failed to download file.")
            return

        duration = _estimate_duration_seconds(raw_bytes, filename)
        ok, result = await _upload_to_roblox_group(cookie_val, raw_bytes, audio_name, duration, group_id)
        if not ok:
            await _send_new_message(
                app_id,
                token,
                f"Upload to **{group_label}** (`{group_id}`) using **{account_label}** failed:\n{result}",
            )
            return

        asset_id = result.get("Id") if isinstance(result, dict) else None
        if asset_id:
            await _send_new_message(
                app_id,
                token,
                f"Uploaded **{audio_name}** to **{group_label}** (`{group_id}`) via **{account_label}**!\n"
                f"- **Asset ID:** `{asset_id}`\n"
                f"- **Sound ID:** `rbxassetid://{asset_id}`",
            )
        else:
            await _send_new_message(
                app_id,
                token,
                f"Upload to **{group_label}** (`{group_id}`) completed via **{account_label}**: `{json.dumps(result)[:400]}`",
            )
    except Exception as exc:
        print(f"[uploadaudio] Exception: {exc}")
        await _send_new_message(app_id, token, f"Unexpected upload error: `{exc}`")


async def handle_component(interaction: dict, env, ctx=None) -> dict:
    custom_id = str(((interaction.get("data") or {}).get("custom_id")) or "")
    print(f"[uploadaudio] handle_component custom_id={custom_id}")
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "uploadaudio_group":
        return _message("Unknown button action.")

    job_id = parts[1]
    try:
        group_id = int(parts[2])
    except ValueError:
        return _message("Invalid group selection.")

    await _execute_group_upload(interaction, env, job_id, group_id)
    return _deferred_update_message()
    
