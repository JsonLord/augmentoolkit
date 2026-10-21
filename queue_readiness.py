"""Typed queue failures and an explicit operator-controlled execution gate."""

import os

from fastapi import HTTPException
from huey.exceptions import HueyException
from redis.exceptions import RedisError

from huey_config import huey
from redis_config import redis_client

QUEUE_ERRORS = (RedisError, HueyException)


def require_queue_ready():
    try:
        redis_client.ping()
        # Inspect the actual Huey storage too: it can use a different pool.
        huey.pending_count()
    except QUEUE_ERRORS:
        raise HTTPException(503, "Huey/Redis unavailable") from None


def require_job_execution():
    require_queue_ready()
    if os.environ.get("ATK_JOB_EXECUTION_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "Job execution is disabled by the operator")
