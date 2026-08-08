from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.choices import UserRole

from .models import UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Give every user a profile; superusers default to the manager role."""
    if not created:
        return
    # `raw=True` means loaddata is replaying a fixture, which carries its own
    # UserProfile rows. Creating one here wins the race and then collides with
    # the fixture's row on the unique user_id — breaking every fixture load,
    # including dump/restore of a whole database.
    if kwargs.get("raw"):
        return
    role = UserRole.MANAGER if instance.is_superuser else UserRole.ANNOTATOR
    UserProfile.objects.get_or_create(user=instance, defaults={"role": role})
