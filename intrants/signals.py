"""
intrants/signals.py

Signals for the intrants app.

Registered signal:
  - post_save on Intrant → auto-create a StockIntrant for every existing
    Branche whenever a new Intrant catalogue entry is saved for the first
    time.  This guarantees that every (branche, intrant) pair always has an
    associated stock record so that property/method helpers
    (Intrant.quantite_en_stock, Intrant.en_alerte) never raise
    RelatedObjectDoesNotExist (v1.4 — BR-BRA-07: stock is keyed by
    (branche, intrant), not by intrant alone).

    The mirror-image guarantee — bootstrapping stock rows for every existing
    Intrant whenever a *new Branche* is created — lives in core/signals.py
    (bootstrap_stock_pour_nouvelle_branche).
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from intrants.models import Intrant

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Intrant)
def creer_stock_intrant(sender, instance, created, **kwargs):
    """
    On first save of an Intrant, create a matching StockIntrant
    (quantite=0, prix_moyen_pondere=0) for every Branche currently in the
    system (BR-BRA-07).

    Imported lazily to avoid circular imports at app-load time.
    """
    if not created:
        return

    # Lazy import — stock/core apps depend on intrants, not the other way around.
    from core.models import Branche
    from stock.models import StockIntrant

    branches = list(Branche.objects.all())
    if not branches:
        logger.warning(
            "creer_stock_intrant: aucune Branche n'existe encore — "
            "Intrant pk=%s (%s) créé sans StockIntrant. Une ligne sera "
            "créée pour chaque branche dès qu'elle existera (BR-BRA-07).",
            instance.pk,
            instance.designation,
        )
        return

    nb_crees = 0
    for branche in branches:
        stock, was_created = StockIntrant.objects.get_or_create(
            branche=branche,
            intrant=instance,
            defaults={
                "quantite": 0,
                "prix_moyen_pondere": 0,
            },
        )
        if was_created:
            nb_crees += 1

    logger.debug(
        "StockIntrant créé pour le nouvel Intrant pk=%s (%s) sur %d/%d branche(s).",
        instance.pk,
        instance.designation,
        nb_crees,
        len(branches),
    )
