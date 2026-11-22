from workers import Response


async def run(message, env):
    return Response.json(
        {
            "type": 4,
            "data": {"content": "pong!"},
        }
    )
  
