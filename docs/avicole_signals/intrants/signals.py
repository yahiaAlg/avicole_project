"""
intrants/signals.py

Signals for the intrants app.

Registered signal:
  - post_save on Intrant → auto-create a StockIntrant (one-to-one) whenever a
    new Intrant catalogue entry is saved for the first time.  This guarantees
    that every intrant always has an associated stock record so that property
    helpers (quantite_en_stock, en_alerte) never raise RelatedObjectDoesNotExist.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from intrants.models import Intrant

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Intrant)
def creer_stock_intrant(sender, instance, created, **kwargs):
    """
    On first save of an Intrant, create a matching StockIntrant with
    quantite=0 and prix_moyen_pondere=0.

    Imported lazily to avoid circular imports at app-load time.
    """
    if not created:
        return

    # Lazy import — stock app depends on intrants, not the other way around.
    from stock.models import StockIntrant

    stock, was_created = StockIntrant.objects.get_or_create(
        intrant=instance,
        defaults={
            "quantite": 0,
            "prix_moyen_pondere": 0,
        },
    )

    if was_created:
        logger.debug(
            "StockIntrant created for new Intrant pk=%s (%s).",
            instance.pk,
            instance.designation,
        )
    else:
        # Edge-case: record already existed (e.g. created by a fixture).
        logger.warning(
            "StockIntrant already existed for Intrant pk=%s on creation signal.",
            instance.pk,
        )
