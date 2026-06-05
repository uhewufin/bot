import { verifyKey } from 'https://esm.sh/discord-interactions@3.4.0';

export default {
  async fetch(request, env, ctx) {
    // 1. Verify the request is a POST
    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    // 2. Validate the signature using your Public Key (PK)
    const signature = request.headers.get('x-signature-ed25519');
    const timestamp = request.headers.get('x-signature-timestamp');
    const body = await request.clone().arrayBuffer();

    const isValidRequest = verifyKey(body, signature, timestamp, env.PK);
    if (!isValidRequest) {
      return new Response('Invalid request signature', { status: 401 });
    }

    // 3. Parse the message
    const message = await request.json();

    // 4. Handle "Ping" (required for Discord to save your URL)
    if (message.type === 1) {
      return Response.json({ type: 1 });
    }

    // 5. Handle Slash Commands
    if (message.type === 2) {
      if (message.data.name === 'hello') {
        return Response.json({
          type: 4,
          data: { content: 'Hello! Your bot is online and working.' },
        });
      }
    }

    return new Response('Unknown Command', { status: 404 });
  },
};
