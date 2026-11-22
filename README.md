# Discord interactions bot (Cloudflare Python Worker)

The bot is an HTTP endpoint for Discord interactions. It verifies Discord's
Ed25519 request signature, answers PING requests, and replies to application
commands with a simple greeting. The implementation is in `src/index.py` and
uses only Python's standard library.

Configure the Discord application to send interactions to the deployed
Worker's URL. Keep the application's public key in the `PK` Wrangler variable.

Python Workers need PyWrangler to sync the runtime SDK into `python_modules`
before Wrangler deploys the Worker. Run locally with:

```sh
uvx --from workers-py pywrangler dev
uvx --from workers-py pywrangler deploy
```

For the Cloudflare build's deploy command, use:

```sh
rm -f requirements.txt && uvx --from workers-py pywrangler deploy
```

The `rm` removes the legacy requirements file previously used to install
`workers-py`; current PyWrangler requires dependencies to be declared in
`pyproject.toml`. The Cloudflare deploy command must use PyWrangler rather than
calling `npx wrangler deploy` directly, so the Python runtime SDK is synced
before upload.

This endpoint does not use `discord.py`: `discord.py` is a gateway client that
expects a long-running process and a persistent Discord Gateway connection,
whereas this Cloudflare Worker handles individual HTTP interaction requests.
