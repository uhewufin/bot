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

**Important:** Cloudflare is still configured to run `npx wrangler deploy` if
your build log shows that command. Change the Worker build's deploy command in
Cloudflare to:

```sh
pip install uv && rm -f requirements.txt && uvx --from workers-py pywrangler deploy
```

The `rm` removes the legacy requirements file before PyWrangler runs. PyWrangler
uses `pyproject.toml` and exits if `requirements.txt` is present. The plain
`npx wrangler deploy` command does not run PyWrangler's SDK sync step, so the
runtime reports `No module named 'workers'`.

This endpoint does not use `discord.py`: `discord.py` is a gateway client that
expects a long-running process and a persistent Discord Gateway connection,
whereas this Cloudflare Worker handles individual HTTP interaction requests.
