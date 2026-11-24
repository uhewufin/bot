import asyncio
import io
import wave

import httpx
from workers import WorkerEntrypoint, Response

CLOUDCONVERT_BASE = "https://api.cloudconvert.com/v2"


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method != "POST":
            return Response('POST a JSON body: {"url": "<direct audio url>"}', status=405)

        body = await request.json()
        source_url = body.get("url")
        if not source_url:
            return Response("Missing 'url'", status=400)

        api_key = self.env.CLOUDCONVERT_API_KEY
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient() as client:
            # 1. Reject anything that isn't actually a direct audio file
            head = await client.head(source_url, follow_redirects=True)
            content_type = head.headers.get("content-type", "")
            if not head.is_success or not content_type.startswith("audio/"):
                return Response(
                    f'That\'s not a direct audio file link (got "{content_type}")',
                    status=400,
                )

            # 2. Create the CloudConvert job: import -> convert to WAV -> export
            job_res = await client.post(
                f"{CLOUDCONVERT_BASE}/jobs",
                headers=headers,
                json={
                    "tasks": {
                        "import-file": {"operation": "import/url", "url": source_url},
                        "convert-file": {
                            "operation": "convert",
                            "input": "import-file",
                            "output_format": "wav",
                        },
                        "export-file": {"operation": "export/url", "input": "convert-file"},
                    }
                },
            )
            job_id = job_res.json()["data"]["id"]

            # 3. Poll until the export task finishes
            export_url = None
            while not export_url:
                await asyncio.sleep(1)
                status_res = await client.get(f"{CLOUDCONVERT_BASE}/jobs/{job_id}", headers=headers)
                status_job = status_res.json()["data"]

                if status_job["status"] == "error":
                    return Response("CloudConvert job failed", status=500)

                for task in status_job["tasks"]:
                    if task["name"] == "export-file" and task["status"] == "finished":
                        export_url = task["result"]["files"][0]["url"]
                        break

            # 4. Download the resulting WAV
            wav_bytes = (await client.get(export_url)).content

        # 5. Parse WAV -> raw PCM with the stdlib (no ffmpeg needed)
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            pcm = w.readframes(w.getnframes())
            sample_rate = w.getframerate()
            channels = w.getnchannels()
            sample_width = w.getsampwidth()

        return Response(
            pcm,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Sample-Rate": str(sample_rate),
                "X-Channels": str(channels),
                "X-Sample-Width": str(sample_width),
            },
        )
