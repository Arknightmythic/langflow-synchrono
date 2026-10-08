from celery import Celery

from engine import settings as cfg

app = Celery("synchrono", broker=cfg.BROKER_URL, include=["worker.tasks"])
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=20,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": cfg.VISIBILITY_TIMEOUT},
    task_routes={
        "grading.run": {"queue": "grading"},
        "matching.run": {"queue": "matching"},
        "master.load": {"queue": "matching"},
        "kl.release": {"queue": "matching"},
    },
)
