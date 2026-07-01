"""
core/signals.py

Signals for the core application.

Registered signal (v1.4 — Multi-Branch Architecture, spec §3.5):
  post_save on Branche → on creation of a new branch, bootstrap a
  StockIntrant row (quantite=0) for every existing Intrant and a
  StockProduitFini row (quantite=0) for every existing ProduitFini, scoped
  to that branch.

Why this is needed:
  StockIntrant / StockProduitFini are now keyed by (branche, item) instead
  of by item alone (BR-BRA-07). intrants/signals.py and production/signals.py
  already guarantee a stock row per (existing branch, new item) whenever a
  new Intrant / ProduitFini is created. This signal is the mirror image:
  it guarantees a stock row per (new branch, existing item) whenever a new
  Branche is created, so that property/method helpers
  (Intrant.quantite_en_stock, ProduitFini.quantite_en_stock_branche, …)
  never raise RelatedObjectDoesNotExist for a freshly-opened branch.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Branche

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Branche)
def bootstrap_stock_pour_nouvelle_branche(sender, instance, created, **kwargs):
    """
    On first save of a Branche, create a zero-balance StockIntrant for every
    Intrant in the catalogue and a zero-balance StockProduitFini for every
    ProduitFini in the catalogue, scoped to this new branch.

    Imported lazily to avoid circular imports at app-load time (core is
    loaded before intrants/production/stock).
    """
    if not created:
        return

    from intrants.models import Intrant
    from production.models import ProduitFini
    from stock.models import StockIntrant, StockProduitFini

    nb_stock_intrants = 0
    for intrant in Intrant.objects.all():
        _, was_created = StockIntrant.objects.get_or_create(
            branche=instance,
            intrant=intrant,
            defaults={"quantite": 0, "prix_moyen_pondere": 0},
        )
        if was_created:
            nb_stock_intrants += 1

    nb_stock_produits = 0
    for produit_fini in ProduitFini.objects.all():
        _, was_created = StockProduitFini.objects.get_or_create(
            branche=instance,
            produit_fini=produit_fini,
            defaults={
                "quantite": 0,
                "cout_moyen_production": 0,
                "seuil_alerte": 0,
            },
        )
        if was_created:
            nb_stock_produits += 1

    logger.info(
        "Branche pk=%s (%s) créée : %d StockIntrant + %d StockProduitFini "
        "initialisés à zéro (BR-BRA-07).",
        instance.pk,
        instance.code,
        nb_stock_intrants,
        nb_stock_produits,
    )
