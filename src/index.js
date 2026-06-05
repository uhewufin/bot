export default {
  async fetch(request, env, ctx) {
    if (request.method !== 'POST') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    // --- MANUAL VERIFICATION (No external library needed) ---
    const signature = request.headers.get('x-signature-ed25519');
    const timestamp = request.headers.get('x-signature-timestamp');
    const body = await request.clone().arrayBuffer();

    // This is the hex-to-binary verification logic
    const isValid = await verifyKey(body, signature, timestamp, env.PK);
    if (!isValid) {
      return new Response('Invalid request signature', { status: 401 });
    }
    // ---------------------------------------------------------

    const message = await request.json();

    if (message.type === 1) { // Ping
      return Response.json({ type: 1 });
    }

    if (message.type === 2) { // Slash command
      return Response.json({
        type: 4,
        data: { content: 'Hello! Your bot is working.' },
      });
    }

    return new Response('Unknown', { status: 404 });
  },
};

// This helper function does what discord-interactions usually does
async function verifyKey(body, signature, timestamp, publicKey) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    hexToUint8Array(publicKey),
    'ed25519',
    false,
    ['verify']
  );
  return await crypto.subtle.verify(
    'ed25519',
    key,
    hexToUint8Array(signature),
    encoder.encode(timestamp + new TextDecoder().decode(body))
  );
}

function hexToUint8Array(hex) {
  return new Uint8Array(hex.match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
}
