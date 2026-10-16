import asyncio
import base64
import json
import sys
from pathlib import Path

from .schemas import RasterPage, SafeError


async def parse(data, filename, options, settings):
    # Only non-secret limits cross the parser boundary; credentials are never inherited.
    limits = {
        name: getattr(settings, name)
        for name in ("max_upload", "max_pages", "max_pixels", "total_pixels")
    }
    payload = json.dumps(
        dict(
            data=base64.b64encode(data).decode(),
            filename=filename,
            options=options.model_dump(),
            limits=limits,
        )
    ).encode()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ocr.parse_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
    )

    async def transfer():
        async def write():
            try:
                proc.stdin.write(payload)
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

        task = asyncio.create_task(write())
        try:
            out = bytearray()
            while chunk := await proc.stdout.read(64 * 1024):
                out.extend(chunk)
                if len(out) > 128 * 1024 * 1024:
                    raise SafeError("parser_output_limit")
            await task
            await proc.wait()
            if proc.returncode:
                raise SafeError("parser_resource_limit")
            try:
                result = json.loads(out)
                if "error" in result:
                    raise SafeError(result["error"])
                pages = [
                    RasterPage(**{**p, "image": base64.b64decode(p["image"], validate=True)})
                    for p in result["pages"]
                ]
                if not 1 <= len(pages) <= min(options.max_pages, settings.max_pages):
                    raise SafeError("page_limit")
                return pages
            except SafeError:
                raise
            except Exception:
                raise SafeError("invalid_document") from None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        return await asyncio.wait_for(transfer(), 40)
    except asyncio.TimeoutError:
        raise SafeError("parser_timeout") from None
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
