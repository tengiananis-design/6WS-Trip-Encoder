import os
from dotenv import load_dotenv

load_dotenv()

from services.celery_app import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "--loglevel=" + os.getenv("CELERY_LOG_LEVEL", "INFO"),
        "--concurrency=" + os.getenv("CELERY_CONCURRENCY", "1"),
        "--prefetch-multiplier=1",
    ])
