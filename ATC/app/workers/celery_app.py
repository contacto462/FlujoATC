from celery import Celery

celery_app = Celery(
    "helpdesk",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
)

celery_app.conf.beat_schedule = {
    "import-emails-every-minute": {
        "task": "app.workers.tasks_email.import_emails_task",
        "schedule": 60.0,
    },
}