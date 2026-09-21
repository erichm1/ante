from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AccessGroup, Profile


@receiver(post_save, sender=get_user_model())
def create_profile(sender, instance, created, **kwargs):
    """Every user has a Profile (the app already assumed so via get_or_create everywhere)."""
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Profile)
def give_default_groups(sender, instance, created, **kwargs):
    """A new person starts in the default group(s), so a fresh account can actually use the app."""
    if created:
        instance.groups.add(*AccessGroup.objects.filter(is_default=True))
