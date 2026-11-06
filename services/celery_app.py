import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL is required for the distributed worker.")

celery_app = Celery(
    "6ws_trip_encoder",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["services.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=60 * 60,
    task_soft_time_limit=55 * 60,
    result_expires=60 * 60 * 24 * 7,
)
