import argparse
import asyncio
import json
from pathlib import Path

from .config import Settings
from .jobs import Jobs
from .routing import Dispatcher
from .schemas import OCROptions


async def main(args):
    settings = Settings.from_env()
    if args.command == "probe-alias":
        from .adapters.freellm_alias import FreeLLMAlias

        backend = FreeLLMAlias(settings.freellm_url, settings.freellm_key)
        try:
            print(json.dumps(await backend.probe(), indent=2))
        finally:
            await backend.close()
        return
    from .adapters.rapidocr_cpu import RapidCPU

    cpu = RapidCPU(settings.models_dir)
    await cpu.initialize()
    dispatcher = Dispatcher(cpu, None, settings)
    dispatcher.start()
    jobs = Jobs(dispatcher, settings)
    options = OCROptions(mode="cpu_only", sensitivity=args.sensitivity)
    try:
        path = Path(args.file)
        job = jobs.submit(path.read_bytes(), path.name, options, "cli")
        await job.task
        if not job.result:
            raise RuntimeError("job_failed")
        if args.format == "markdown":
            print(job.result.markdown)
        else:
            print(job.result.model_dump_json(indent=2))
        if job.status != "completed":
            raise SystemExit(1)
    finally:
        await jobs.close()
        await dispatcher.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("file")
    run.add_argument("--sensitivity", choices=["D0", "D1", "D2"], default="D2")
    run.add_argument("--format", choices=["json", "markdown"], default="json")
    sub.add_parser("probe-alias")
    asyncio.run(main(parser.parse_args()))
