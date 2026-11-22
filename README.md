# Discord interactions bot (Cloudflare Python Worker)

The bot is an HTTP endpoint for Discord interactions. It verifies Discord's
Ed25519 request signature, answers PING requests, and replies to application
commands with a simple greeting. The implementation is in `src/index.py` and
uses only Python's standard library.

Configure the Discord application to send interactions to the deployed
Worker's URL. Keep the application's public key in the `PK` Wrangler variable.

Run or deploy with Wrangler's Python Worker support enabled:

```sh
npx wrangler dev
npx wrangler deploy
```

This endpoint does not use `discord.py`: `discord.py` is a gateway client that
expects a long-running process and a persistent Discord Gateway connection,
whereas this Cloudflare Worker handles individual HTTP interaction requests.
