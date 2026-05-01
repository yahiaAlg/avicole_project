"""
stock/signals.py

Signals for the stock app.

Registered signal:
  post_save on StockAjustement → apply the quantity correction to the
  corresponding StockIntrant or StockProduitFini, then create a
  StockMouvement of type AJUSTEMENT for the audit trail.

Business rules enforced here:
  - Exactly one of (intrant / produit_fini) is populated per StockAjustement
    (validated in the form/admin layer; guarded defensively here).
  - StockAjustement records are immutable after creation; only `created=True`
    triggers the balance update.
  - The handler writes quantite_apres directly to the stock record rather than
    applying a delta, so the physical-count value is authoritative.
  - A StockMouvement of type AJUSTEMENT is always created for auditability,
    regardless of whether the delta is positive or negative.
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from stock.models import StockAjustement

logger = logging.getLogger(__name__)


@receiver(post_save, sender=StockAjustement)
def stock_ajustement_post_save(sender, instance, created, **kwargs):
    """
    On creation of a StockAjustement:
      1. Determine the target stock record (StockIntrant or StockProduitFini).
      2. Overwrite its quantite with instance.quantite_apres.
      3. Create a StockMouvement (AJUSTEMENT) preserving before/after values.

    Subsequent saves of the same StockAjustement are no-ops (immutability).
    """
    if not created:
        return

    from stock.models import StockIntrant, StockProduitFini, StockMouvement

    segment = instance.segment

    # ── Resolve target stock record ─────────────────────────────────────────
    if segment == StockAjustement.SEGMENT_INTRANT:
        if not instance.intrant_id:
            logger.error(
                "StockAjustement pk=%s has segment=INTRANT but intrant is null. "
                "Skipping balance update.",
                instance.pk,
            )
            return

        stock, _ = StockIntrant.objects.get_or_create(
            intrant_id=instance.intrant_id,
            defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
        )
        intrant_ref = instance.intrant
        produit_ref = None

    elif segment == StockAjustement.SEGMENT_PRODUIT_FINI:
        if not instance.produit_fini_id:
            logger.error(
                "StockAjustement pk=%s has segment=PRODUIT_FINI but produit_fini is null. "
                "Skipping balance update.",
                instance.pk,
            )
            return

        stock, _ = StockProduitFini.objects.get_or_create(
            produit_fini_id=instance.produit_fini_id,
            defaults={
                "quantite": Decimal("0"),
                "cout_moyen_production": Decimal("0"),
                "seuil_alerte": Decimal("0"),
            },
        )
        intrant_ref = None
        produit_ref = instance.produit_fini

    else:
        logger.error(
            "StockAjustement pk=%s has unknown segment '%s'. Skipping.",
            instance.pk,
            segment,
        )
        return

    # ── Apply correction (physical count is authoritative) ─────────────────
    quantite_apres = Decimal(str(instance.quantite_apres))
    stock.quantite = quantite_apres
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    logger.info(
        "StockAjustement pk=%s applied: %s → %s → %s (segment=%s, item pk=%s).",
        instance.pk,
        instance.quantite_avant,
        instance.quantite_apres,
        stock.quantite,
        segment,
        instance.intrant_id or instance.produit_fini_id,
    )

    # ── Determine mouvement type based on delta sign ────────────────────────
    delta = Decimal(str(instance.quantite_apres)) - Decimal(str(instance.quantite_avant))

    # Always use TYPE_AJUSTEMENT regardless of direction; direction is implicit
    # from quantite_avant / quantite_apres stored on the mouvement.
    type_mouvement = StockMouvement.TYPE_AJUSTEMENT

    # Quantite on StockMouvement is always positive (direction via type).
    quantite_mouvement = abs(delta) if delta != 0 else Decimal("0")

    item_label = (
        instance.intrant.designation
        if instance.intrant_id
        else instance.produit_fini.designation
    )

    StockMouvement.objects.create(
        intrant=intrant_ref,
        produit_fini=produit_ref,
        type_mouvement=type_mouvement,
        source=StockMouvement.SOURCE_AJUSTEMENT,
        quantite=quantite_mouvement,
        quantite_avant=Decimal(str(instance.quantite_avant)),
        quantite_apres=quantite_apres,
        date_mouvement=instance.date_ajustement,
        reference_id=instance.pk,
        reference_label=f"Ajustement manuel — {item_label}",
        notes=instance.raison,
        created_by=instance.effectue_par,
    )

    logger.debug(
        "StockMouvement (AJUSTEMENT) created for StockAjustement pk=%s. "
        "Delta: %s%s.",
        instance.pk,
        "+" if delta >= 0 else "",
        delta,
    )
