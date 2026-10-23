from contextlib import asynccontextmanager
from enum import Enum
import json
import logging
from pathlib import Path as PyPath
import traceback
import yaml
import sys
import shutil
import zipfile
import httpx
import os
import tempfile
import time
import subprocess

from fastapi import (
    FastAPI,
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    BackgroundTasks,
    Path as FastApiPath,
    Query,
    Body,
    Request,
)
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from app_auth import verify_token
from app_storage import confined_path, data_directory, StorageViolation, normalize_mutable_paths
from queue_readiness import QUEUE_ERRORS, require_queue_ready, require_job_execution
from ocr.worker_api import create_app as create_ocr_app
from ocr.config import Settings as OCRSettings

from huey_config import huey
from tasks import (
    run_pipeline_task,
    set_final_status,
    resolve_job_output_dir,
    resolve_authorized_config,
)
from huey.exceptions import (
    HueyException,
    TaskException,
)
from redis_config import (
    get_progress,
    redis_client,
    set_progress,
)
import signal

from resolve_path import resolve_path

from file_operation_helpers import (
    get_safe_path,
    zip_directory,
    get_dir_structure,
    FileStructure,
    MoveItemRequest,
    handle_get_structure,
    handle_download_item,
    handle_delete_item,
    handle_move_item,
    handle_create_directory,
)

from augmentoolkit.hf_persistence import (
    HFHubManager,
    CheckpointManager,
    JobManifestManager,
    IncompatibleResumeError,
    JobManifest,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SUPER_CONFIG_PATH = PyPath("super_config.yaml")
PATH_ALIASES = {}

try:
    with open(SUPER_CONFIG_PATH, "r", encoding="utf-8") as f:
        super_config = yaml.safe_load(f)
    PATH_ALIASES = super_config.get("path_aliases", {})
except FileNotFoundError:
    print(f"ERROR: Super config file not found at {SUPER_CONFIG_PATH}.")
except yaml.YAMLError as e:
    print(f"ERROR: Error parsing super config file {SUPER_CONFIG_PATH}: {e}")

os.umask(0o077)
SOURCE_ROOT = PyPath(__file__).resolve().parent
ATK_DATA_ROOT = data_directory()
INPUTS_DIR = data_directory("inputs")
OUTPUTS_DIR = data_directory("outputs")
GENERATION_DIR = data_directory("generation")
CONFIGS_DIR = data_directory("external_configs")
LOGS_DIR = data_directory("logs")
MAX_FILE_SIZE = 1024 * 1024 * 1000


class PipelineRunRequest(BaseModel):
    node_path: str
    config_path: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class PipelineRunResponse(BaseModel):
    pipeline_id: str
    message: str


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


class PipelineStatusResponse(BaseModel):
    task_id: str
    status: PipelineStatus
    message: Optional[str] = None
    progress: Optional[float] = Field(None, ge=0.0, le=1.0)
    details: Optional[Dict[str, Any]] = None


class QueueStatusResponse(BaseModel):
    pending_tasks: List[str] = Field(..., description="List of task IDs currently pending execution.")
    scheduled_tasks: List[str] = Field(..., description="List of task IDs scheduled for future execution.")
    message: str


class CreateDirectoryRequest(BaseModel):
    relative_path: str = Field(..., description="The relative path within base directory.")


class DuplicateConfigRequest(BaseModel):
    source_alias: str = Field(..., description="Alias from super_config.yaml.")
    destination_relative_path: str = Field(..., description="Desired relative path.")


class TaskParametersResponse(BaseModel):
    task_id: str
    parameters: Dict[str, Any]


# The child lifespan must be entered explicitly: Starlette mounts do not do so.
ocr_settings = OCRSettings.from_env()
ocr_settings.worker_token = os.environ.get("WORKER_TOKEN", "")
ocr_app = create_ocr_app(ocr_settings)


@asynccontextmanager
async def combined_lifespan(parent):
    async with ocr_app.router.lifespan_context(ocr_app):
        # Recovery is opt-in and cannot execute jobs unless separately enabled.
        if os.environ.get("ATK_HF_RECOVERY_ENABLED", "false").lower() == "true":
            await startup_event()
        yield


router = APIRouter(dependencies=[Depends(verify_token)])
app = FastAPI(
    title="Augmentoolkit Resumable Dataset Factory API",
    description="API for managing and running Augmentoolkit dataset pipelines with Hugging Face Hub remote persistence.",
    version="2.0",
    lifespan=combined_lifespan,
)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def startup_event():
    """At startup, reconnects to HF_WORK_REPO, discovers incomplete jobs, reconciles state, and auto-resumes if enabled."""
    logger.info("Augmentoolkit API starting up...")
    work_repo = os.environ.get("HF_WORK_REPO")
    if work_repo:
        try:
            ckpt_mgr = CheckpointManager()
            incomplete_jobs = ckpt_mgr.discover_incomplete_jobs(work_repo=work_repo)
            logger.info(f"Discovered {len(incomplete_jobs)} incomplete job(s) in HF_WORK_REPO '{work_repo}'.")
            auto_resume = os.environ.get("HF_AUTO_RESUME", "false").lower() in ("true", "1", "yes")
            if auto_resume:
                require_job_execution()
                for manifest in incomplete_jobs:
                    logger.info(f"Auto-resuming durable job {manifest.job_id}...")
                    run_pipeline_task(
                        node_path=manifest.pipeline,
                        parameters={
                            "job_id": manifest.job_id,
                            "hf_source_repo": manifest.source_repo,
                            "hf_work_repo": manifest.work_repo,
                            "hf_output_repo": manifest.output_repo,
                            "source_revision": manifest.source_revision,
                        },
                    )
        except Exception as e:
            logger.error(f"Error during startup HF recovery discovery: {e}")


@app.get("/health", summary="Health Check")
async def health_check():
    """Basic health check endpoint for Hugging Face."""
    return {"status": "ok", "message": "Augmentoolkit API is running."}


@router.get("/readiness", summary="Readiness Check")
async def readiness_check():
    """
    Comprehensive readiness check reporting Redis/Huey connection, Hugging Face authentication,
    repository accessibility (HF_SOURCE_REPO, HF_WORK_REPO, HF_OUTPUT_REPO), and worker status.
    """
    redis_ok = False
    redis_error = None
    try:
        redis_client.ping()
        redis_ok = True
    except QUEUE_ERRORS:
        redis_error = "unavailable"

    hf_mgr = HFHubManager()
    hf_health = hf_mgr.check_all_health()

    worker_ok = False
    try:
        require_queue_ready()
        worker_ok = True
    except Exception:
        pass

    overall_ready = redis_ok and worker_ok and hf_health.get("healthy", False)

    return {
        "status": "ready" if overall_ready else "degraded",
        "redis": {"status": "ok" if redis_ok else "error", "error": redis_error},
        "huey_queue": {"status": "ok" if worker_ok else "error", "consumer_verified": False},
        "huggingface": hf_health,
    }


@app.get("/api-docs", include_in_schema=False)
async def api_docs_redirect():
    return RedirectResponse(url="/docs")


@router.post(
    "/pipelines/run",
    response_model=PipelineRunResponse,
    status_code=202,
    summary="Queue a dataset generation pipeline for execution.",
)
def queue_pipeline_run(request: PipelineRunRequest):
    request.parameters = normalize_mutable_paths(request.parameters or {})
    if request.config_path:
        resolve_authorized_config(request.config_path)
    require_job_execution()
    try:
        task = run_pipeline_task(
            node_path=request.node_path,
            config_path=request.config_path,
            parameters=request.parameters,
        )
        return PipelineRunResponse(
            pipeline_id=task.id,
            message="Pipeline run queued successfully.",
        )
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        logger.error("Pipeline queueing failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Failed to enqueue pipeline task",
        )


@router.get("/jobs", summary="List jobs from HF_WORK_REPO")
def list_jobs(work_repo: Optional[str] = Query(None)):
    target_repo = work_repo or os.environ.get("HF_WORK_REPO", "")
    if not target_repo:
        return {"work_repo": "", "jobs": []}
    hf_mgr = HFHubManager()
    hf_jobs = hf_mgr.list_jobs(work_repo=target_repo)
    return {"work_repo": target_repo, "jobs": hf_jobs}


@router.get("/jobs/{job_id}/manifest", summary="Inspect job manifest")
def inspect_job_manifest(job_id: str, work_repo: Optional[str] = Query(None)):
    target_repo = work_repo or os.environ.get("HF_WORK_REPO", "")
    hf_mgr = HFHubManager()
    manifest = hf_mgr.fetch_manifest(job_id=job_id, work_repo=target_repo)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for job '{job_id}' not found.")
    return manifest


@router.get("/jobs/{job_id}/checkpoints", summary="Inspect job checkpoints")
def inspect_job_checkpoints(job_id: str, work_repo: Optional[str] = Query(None)):
    target_repo = work_repo or os.environ.get("HF_WORK_REPO", "")
    if not target_repo:
        raise HTTPException(status_code=400, detail="HF_WORK_REPO is not configured.")
    hf_mgr = HFHubManager()
    try:
        files = hf_mgr.api.list_repo_files(repo_id=target_repo, repo_type="dataset", token=hf_mgr.token)
        ckpts = [f for f in files if f.startswith(f"jobs/{job_id}/checkpoints/")]
        return {"job_id": job_id, "checkpoints": ckpts}
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking checkpoints: {e}")


@router.get("/jobs/{job_id}/outputs", summary="Inspect job output references")
def inspect_job_outputs(job_id: str, output_repo: Optional[str] = Query(None)):
    target_repo = output_repo or os.environ.get("HF_OUTPUT_REPO", "")
    if not target_repo:
        raise HTTPException(status_code=400, detail="HF_OUTPUT_REPO is not configured.")
    hf_mgr = HFHubManager()
    try:
        files = hf_mgr.api.list_repo_files(repo_id=target_repo, repo_type="dataset", token=hf_mgr.token)
        outputs = [f for f in files if f.startswith(f"datasets/{job_id}/")]
        return {"job_id": job_id, "output_repo": target_repo, "outputs": outputs}
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking outputs: {e}")


@router.post("/jobs/{job_id}/resume", summary="Explicitly resume a job from remote state")
def resume_job(job_id: str, parameters: Optional[Dict[str, Any]] = Body(None)):
    require_job_execution()
    work_repo = os.environ.get("HF_WORK_REPO", "")
    ckpt_mgr = CheckpointManager()
    manifest = ckpt_mgr.load_remote_manifest(job_id=job_id, work_repo=work_repo)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found in {work_repo}.")

    target_output_dir = resolve_job_output_dir(
        node_path=manifest.pipeline,
        config_path=parameters.get("config_path") if parameters else None,
        parameters=parameters,
        durable_job_id=job_id,
    )

    live_prompt_hash = JobManifestManager.compute_live_prompt_hash(
        prompt_folder=parameters.get("prompt_folder") if parameters else None,
        default_prompt_folder=parameters.get("default_prompt_folder") if parameters else None,
    )

    try:
        reconciled_manifest, completed_shards, restored_data = ckpt_mgr.reconcile_and_resume_job(
            job_id=job_id,
            target_pipeline=manifest.pipeline,
            target_config_dict=parameters or {},
            target_prompt_hash=live_prompt_hash,
            target_source_repo=manifest.source_repo,
            target_source_revision=manifest.source_revision,
            target_source_path=manifest.source_path,
            target_source_split=manifest.source_split,
            target_output_schema_version=manifest.output_schema_version,
            work_repo=work_repo,
            output_dir=target_output_dir,
        )
    except IncompatibleResumeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reconcile job state: {e}")

    run_params = parameters or {}
    run_params.update({
        "job_id": job_id,
        "output_dir": target_output_dir,
        "hf_source_repo": manifest.source_repo,
        "hf_work_repo": manifest.work_repo,
        "hf_output_repo": manifest.output_repo,
        "source_revision": manifest.source_revision,
        "restored_shards": list(completed_shards),
    })

    task = run_pipeline_task(
        node_path=manifest.pipeline,
        parameters=normalize_mutable_paths(run_params),
    )
    return {
        "job_id": job_id,
        "task_id": task.id,
        "output_dir": target_output_dir,
        "message": f"Resume task for durable job '{job_id}' enqueued successfully.",
        "completed_shards": list(completed_shards),
    }


@router.post("/jobs/{job_id}/fork", summary="Fork an existing job run into a new durable job")
def fork_job(
    job_id: str,
    new_job_id: Optional[str] = Query(None),
    parameters: Optional[Dict[str, Any]] = Body(None),
):
    require_job_execution()
    work_repo = os.environ.get("HF_WORK_REPO", "")
    ckpt_mgr = CheckpointManager()
    parent_manifest = ckpt_mgr.load_remote_manifest(job_id=job_id, work_repo=work_repo)
    if not parent_manifest:
        raise HTTPException(status_code=404, detail=f"Parent job '{job_id}' not found in {work_repo}.")

    forked_id = new_job_id or f"{job_id}_fork_{int(time.time())}"

    fork_params = parameters or {}
    run_params = {
        "job_id": forked_id,
        "forked_from_job_id": job_id,
        "hf_source_repo": fork_params.get("hf_source_repo", parent_manifest.source_repo),
        "hf_work_repo": work_repo,
        "hf_output_repo": fork_params.get("hf_output_repo", parent_manifest.output_repo),
        "source_revision": fork_params.get("source_revision", parent_manifest.source_revision),
        "source_path": fork_params.get("source_path", parent_manifest.source_path),
        "source_split": fork_params.get("source_split", parent_manifest.source_split),
    }
    run_params.update(fork_params)
    run_params = normalize_mutable_paths(run_params)

    task = run_pipeline_task(
        node_path=parent_manifest.pipeline,
        parameters=normalize_mutable_paths(run_params),
    )

    return {
        "forked_job_id": forked_id,
        "parent_job_id": job_id,
        "task_id": task.id,
        "message": "Forked job queued successfully.",
    }


@router.get(
    "/pipelines/available",
    response_model=List[str],
    summary="Get available pipeline aliases from super_config.yaml.",
)
def get_available_pipelines():
    available_pipelines = []
    if not SUPER_CONFIG_PATH.exists():
        return []

    try:
        with open(SUPER_CONFIG_PATH, "r", encoding="utf-8") as f:
            super_config = yaml.safe_load(f) or {}

        aliases = super_config.get("path_aliases", {})
        if not isinstance(aliases, dict):
            return []

        for alias, path_value in aliases.items():
            if isinstance(path_value, str) and not path_value.strip().lower().endswith(".yaml"):
                available_pipelines.append(alias)

        return sorted(available_pipelines)

    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing super config: {e}")


@router.get(
    "/configs/aliases",
    response_model=List[str],
    summary="Get available config file aliases from super_config.yaml.",
)
def get_available_config_aliases():
    config_aliases = []
    if not SUPER_CONFIG_PATH.exists():
        return []

    try:
        with open(SUPER_CONFIG_PATH, "r", encoding="utf-8") as f:
            super_config = yaml.safe_load(f) or {}

        aliases = super_config.get("path_aliases", {})
        if not isinstance(aliases, dict):
            return []

        for alias, path_value in aliases.items():
            if isinstance(path_value, str) and path_value.strip().lower().endswith(".yaml"):
                config_aliases.append(alias)

        return sorted(config_aliases)

    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing super config: {e}")


@router.get(
    "/tasks/queue",
    response_model=QueueStatusResponse,
    summary="Get lists of pending and scheduled tasks.",
)
def get_queue_status():
    require_queue_ready()
    try:
        pending_tasks = [task.id for task in huey.pending()]
        scheduled_tasks = [task.id for task in huey.scheduled()]
        return QueueStatusResponse(
            pending_tasks=pending_tasks,
            scheduled_tasks=scheduled_tasks,
            message=f"Found {len(pending_tasks)} pending and {len(scheduled_tasks)} scheduled tasks.",
        )
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve queue status: {e}")


@router.get(
    "/tasks/{task_id}/status",
    response_model=PipelineStatusResponse,
    summary="Get the status of a pipeline run.",
)
def get_pipeline_status(task_id: str):
    require_queue_ready()
    redis_status_key = f"status_for_task:{task_id}"
    try:
        final_status_json = redis_client.get(redis_status_key)
        if final_status_json:
            try:
                status_data = json.loads(final_status_json)
                final_status_enum = PipelineStatus(status_data["status"])
                return PipelineStatusResponse(
                    task_id=task_id,
                    status=final_status_enum,
                    message=status_data.get("message"),
                    progress=1.0,
                    details=status_data.get("details"),
                )
            except Exception:
                pass

        pipeline_progress = get_progress(task_id)
        if pipeline_progress:
            return PipelineStatusResponse(
                task_id=task_id,
                status=PipelineStatus.RUNNING,
                message=pipeline_progress.get("message", "Task is running."),
                progress=min(pipeline_progress.get("progress", 0.0), 1.0),
                details={"source": "progress_tracker"},
            )

        pending_ids = {task.id for task in huey.pending()}
        scheduled_ids = {task.id for task in huey.scheduled()}
        if task_id in pending_ids or task_id in scheduled_ids:
            return PipelineStatusResponse(
                task_id=task_id,
                status=PipelineStatus.PENDING,
                message="Task is pending in the queue.",
                progress=0.0,
                details={"source": "huey_queue"},
            )

        if huey.is_revoked(task_id):
            return PipelineStatusResponse(
                task_id=task_id,
                status=PipelineStatus.REVOKED,
                message="Task was revoked.",
                progress=0.0,
                details={"source": "huey_revoked"},
            )

        raise HTTPException(status_code=404, detail=f"Task with ID '{task_id}' not found.")
    except HTTPException:
        raise
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving status: {e}")


@router.get(
    "/tasks/{task_id}/parameters",
    response_model=TaskParametersResponse,
    summary="Get the parameters a task was executed with.",
)
def get_task_parameters(task_id: str):
    require_queue_ready()
    redis_key = f"parameters_for_task:{task_id}"
    try:
        params_json = redis_client.get(redis_key)
        if params_json is None:
            raise HTTPException(status_code=404, detail=f"Parameters for task '{task_id}' not found.")
        parameters = json.loads(params_json)
        return TaskParametersResponse(task_id=task_id, parameters=parameters)
    except HTTPException:
        raise
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving parameters: {e}")


def _find_and_kill_run_augmentoolkit_process() -> Optional[Dict[str, Any]]:
    try:
        result = subprocess.run(["ps", "-ef"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if "run_augmentoolkit.py" in line and "grep" not in line:
                fields = line.split()
                if len(fields) >= 2:
                    try:
                        pid = int(fields[1])
                        os.kill(pid, signal.SIGINT)
                        time.sleep(2)
                        return {"pid": pid, "killed": True, "method": "SIGINT"}
                    except Exception:
                        continue
        return None
    except Exception:
        return None


@router.post(
    "/tasks/{task_id}/interrupt",
    status_code=200,
    summary="Interrupt a running pipeline task subprocess or revoke a pending task.",
)
def interrupt_or_revoke_task(task_id: str):
    require_queue_ready()
    redis_pid_key = f"worker_pid_for_task:{task_id}"
    redis_status_key = f"status_for_task:{task_id}"

    try:
        final_status_json = redis_client.get(redis_status_key)
        if final_status_json:
            try:
                status_data = json.loads(final_status_json)
                existing_status = status_data.get("status", "UNKNOWN").upper()
                if existing_status in ["COMPLETED", "FAILED", "REVOKED"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Task {task_id} has already finished with status: {existing_status}.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

        pid_bytes = redis_client.get(redis_pid_key)
        if pid_bytes:
            try:
                pid = int(pid_bytes)
                os.kill(pid, signal.SIGINT)
                time.sleep(1)
                set_final_status(task_id, "REVOKED", "Task interrupted via SIGINT.")
                return {"message": f"Task {task_id} interrupted via SIGINT."}
            except Exception:
                pass

        was_revoked = huey.revoke(task_id, revoke_once=True)
        if was_revoked:
            set_final_status(task_id, "REVOKED", "Task revoked while pending.")
            return {"message": f"Task {task_id} was pending and has been revoked."}

        raise HTTPException(status_code=404, detail=f"Task {task_id} not running or not found.")
    except HTTPException:
        raise
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interrupting task: {e}")


@router.get("/tasks/{task_id}/logs", summary="Get logs for a specific task.")
def get_task_logs(task_id: str, tail: Optional[int] = Query(None, ge=1)):
    log_file_path = get_safe_path(LOGS_DIR, f"{task_id}.log")
    if not log_file_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file for task {task_id} not found.")

    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        log_content = "".join(lines[-tail:]) if tail else "".join(lines)
        return JSONResponse(content={"task_id": task_id, "logs": log_content})
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")


@router.delete("/logs", status_code=200, summary="Clear all task log files.")
def clear_all_logs():
    deleted_count = 0
    try:
        for item in LOGS_DIR.iterdir():
            if item.is_file() and item.suffix == ".log":
                item.unlink()
                deleted_count += 1
        return {"message": f"Successfully deleted {deleted_count} log file(s)."}
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear logs: {e}")


@router.get(
    "/tasks/{task_id}/outputs/download", summary="Download output directory for a task."
)
def download_task_output(task_id: str):
    require_queue_ready()
    redis_key = f"output_dir_for_task:{task_id}"
    output_dir_str = redis_client.get(redis_key)
    if not output_dir_str:
        raise HTTPException(status_code=404, detail=f"Output directory not found for task {task_id}.")

    task_output_dir = get_safe_path(OUTPUTS_DIR, output_dir_str)
    if not task_output_dir.is_dir():
        raise HTTPException(status_code=404, detail="Output directory does not exist.")

    zip_filename = f"task_{task_id}_output.zip"
    fd, temp_name = tempfile.mkstemp(suffix=".zip", dir=data_directory("downloads"))
    os.close(fd)
    temp_zip_path = PyPath(temp_name)
    try:
        zip_directory(task_output_dir, temp_zip_path)
    except Exception:
        temp_zip_path.unlink(missing_ok=True)
        raise
    from starlette.background import BackgroundTask
    return FileResponse(temp_zip_path, media_type="application/zip", filename=zip_filename,
                        background=BackgroundTask(temp_zip_path.unlink, missing_ok=True))


@router.get(
    "/outputs/structure/{relative_path:path}",
    response_model=List[FileStructure],
    summary="Get structure of a path within the outputs directory.",
)
@router.get("/outputs/structure/", include_in_schema=False)
def get_output_structure(relative_path: str = "."):
    try:
        resolved_path = (OUTPUTS_DIR / relative_path).resolve()
        resolved_path.relative_to(OUTPUTS_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal detected")

    return handle_get_structure(OUTPUTS_DIR, relative_path)


@router.get(
    "/inputs/structure/{relative_path:path}",
    response_model=List[FileStructure],
    summary="Get structure of a path within the inputs directory.",
)
@router.get("/inputs/structure/", include_in_schema=False)
def get_input_structure(relative_path: str = "."):
    try:
        resolved_path = (INPUTS_DIR / relative_path).resolve()
        resolved_path.relative_to(INPUTS_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal detected")

    return handle_get_structure(INPUTS_DIR, relative_path)


@router.get(
    "/configs/structure/{relative_path:path}",
    response_model=List[FileStructure],
    summary="Get structure of a path within the configs directory.",
)
@router.get("/configs/structure/", include_in_schema=False)
def get_config_structure(relative_path: str = "."):
    try:
        resolved_path = (CONFIGS_DIR / relative_path).resolve()
        resolved_path.relative_to(CONFIGS_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path traversal detected")

    return handle_get_structure(CONFIGS_DIR, relative_path)


# Register protected routes before the mount and SPA catch-all.
app.include_router(router)
app.mount("/ocr", ocr_app)


@app.exception_handler(StorageViolation)
async def storage_error(request, exc):
    return JSONResponse({"detail": "Path outside authorized storage"}, status_code=400)


async def queue_error(request, exc):
    return JSONResponse({"detail": "Huey/Redis unavailable"}, status_code=503)


for error_type in QUEUE_ERRORS:
    app.add_exception_handler(error_type, queue_error)


FRONTEND_DIST_DIR = SOURCE_ROOT / "atk-interface/dist"

# React document routes only. Data routes above always win; unknown API paths
# and missing assets must never become a successful HTML response.
FRONTEND_PAGES = {"", "inputs", "outputs", "configs", "monitor", "logs", "chat"}
FRONTEND_DETAIL_PAGES = {"configs", "logs"}
FRONTEND_RESERVED_IDS = {"structure", "content", "upload", "download", "directory", "move", "duplicate"}

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    parts = full_path.rstrip("/").split("/")
    page = full_path.rstrip("/") in FRONTEND_PAGES or (
        len(parts) == 2
        and parts[0] in FRONTEND_DETAIL_PAGES
        and parts[1] not in FRONTEND_RESERVED_IDS
        and parts[1] not in {"", ".", ".."}
    )
    if page:
        index_html = FRONTEND_DIST_DIR / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)
        if not full_path:
            return {"status": "ok", "message": "Augmentoolkit API is running (frontend not built)."}
        raise HTTPException(404, "Frontend not built")

    # Only the Vite assets namespace may serve files; it has no SPA fallback.
    if not full_path.startswith("assets/"):
        raise HTTPException(404, "Not found")
    file_path = get_safe_path(FRONTEND_DIST_DIR, full_path)
    if file_path.is_file():
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Not found")
