"""Stateless, signed, read-only full-task sharing."""

from django.core import signing

from .models import AnnotationTask

SALT = "annotation.public-task-share.v1"


def create_token(task: AnnotationTask) -> str:
    return signing.dumps({"task_id": task.pk}, salt=SALT, compress=True)


def resolve_token(token: str) -> AnnotationTask | None:
    if not token:
        return None
    try:
        payload = signing.loads(token, salt=SALT)
        task_id = int(payload["task_id"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    return (
        AnnotationTask.objects.select_related("volume", "project")
        .filter(pk=task_id)
        .first()
    )
