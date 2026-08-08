from django.conf import settings
from django.db import models


class ResetConfirmation(models.Model):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token_digest = models.CharField(max_length=64, unique=True)
    deployment_fingerprint = models.CharField(max_length=32)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ApplicationResetRecord(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    deployment_fingerprint = models.CharField(max_length=32)
    backup_marker = models.CharField(max_length=1024)
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
