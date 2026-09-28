"""
Celery Application Instance for Synchrono AI Reasoning.
Supports Redis and PostgreSQL as event-driven message brokers and result backends.
"""

from celery import Celery
from reasoning.config import (
    CELERY_BROKER_URL,
    CELERY_CONCURRENCY,
    CELERY_RESULT_BACKEND,
)

celery_app = Celery(
    "synchrono_reasoning",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["reasoning.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    task_track_started=True,
    # Worker prefetch multiplier 1 ensures fair queue distribution among concurrent workers
    worker_prefetch_multiplier=1,
    # Acks late ensures tasks are only acknowledged after successful execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    worker_concurrency=CELERY_CONCURRENCY,
)
