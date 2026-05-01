"""
production/signals.py

Signals for the production app.

Registered signals:
  1. post_save on ProduitFini      → auto-create StockProduitFini (one-to-one),
                                     mirroring the same guarantee provided for
                                     Intrant → StockIntrant in intrants/signals.py.
  2. pre_save  on ProductionRecord → cache old statut for transition detection.
  3. post_save on ProductionRecord → when statut transitions to VALIDE, for
                                     every ProductionLigne:
                                       a. Increase StockProduitFini.quantite.
                                       b. Recalculate StockProduitFini.cout_moyen_production.
                                       c. Create a StockMouvement (entree / production).

Business rules enforced here:
  - Only a BROUILLON → VALIDE transition triggers stock entries; re-saving a
    validated record does not double-count.
  - poids_moyen_kg is auto-computed by ProductionRecord.save() (model level).
  - cout_moyen_production uses a weighted average analogous to the PMP for
    intrants: (old_qty × old_cost + new_qty × new_cost) / total_qty.
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from production.models import ProduitFini, ProductionRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal 1 — ProduitFini: auto-create StockProduitFini
# ---------------------------------------------------------------------------

@receiver(post_save, sender=ProduitFini)
def creer_stock_produit_fini(sender, instance, created, **kwargs):
    """
    On first save of a ProduitFini, create a matching StockProduitFini with
    quantite=0 so that quantite_en_stock never raises RelatedObjectDoesNotExist.
    """
    if not created:
        return

    from stock.models import StockProduitFini

    stock, was_created = StockProduitFini.objects.get_or_create(
        produit_fini=instance,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )

    if was_created:
        logger.debug(
            "StockProduitFini created for new ProduitFini pk=%s (%s).",
            instance.pk,
            instance.designation,
        )
    else:
        logger.warning(
            "StockProduitFini already existed for ProduitFini pk=%s on creation signal.",
            instance.pk,
        )


# ---------------------------------------------------------------------------
# Signal 2 — ProductionRecord: cache old statut
# ---------------------------------------------------------------------------

@receiver(pre_save, sender=ProductionRecord)
def production_record_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save statut so the post_save handler can detect the
    BROUILLON → VALIDE transition without an extra DB query.
    """
    if instance.pk:
        try:
            instance._old_statut = (
                ProductionRecord.objects.filter(pk=instance.pk)
                .values_list("statut", flat=True)
                .get()
            )
        except ProductionRecord.DoesNotExist:
            instance._old_statut = None
    else:
        instance._old_statut = None


# ---------------------------------------------------------------------------
# Signal 3 — ProductionRecord: apply stock entries on VALIDE transition
# ---------------------------------------------------------------------------

@receiver(post_save, sender=ProductionRecord)
def production_record_post_save(sender, instance, created, **kwargs):
    """
    When a ProductionRecord transitions from BROUILLON to VALIDE:
      - For each ProductionLigne, increase StockProduitFini and create a
        StockMouvement of type ENTREE / source PRODUCTION.
      - Recalculate cout_moyen_production using weighted average.

    A re-save of an already-validated record is a no-op (old_statut == VALIDE).
    """
    old_statut = getattr(instance, "_old_statut", None)
    is_validating = (
        instance.statut == ProductionRecord.STATUT_VALIDE
        and old_statut != ProductionRecord.STATUT_VALIDE
    )

    if not is_validating:
        return

    lignes = instance.lignes.select_related("produit_fini").all()

    if not lignes.exists():
        logger.warning(
            "ProductionRecord pk=%s validated but has no lignes. "
            "No stock entries created.",
            instance.pk,
        )
        return

    logger.info(
        "ProductionRecord pk=%s validated (lot: %s). "
        "Processing %d ligne(s) for stock entry.",
        instance.pk,
        instance.lot.designation,
        lignes.count(),
    )

    for ligne in lignes:
        _enregistrer_entree_stock_production(ligne, instance)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _enregistrer_entree_stock_production(ligne, production):
    """
    Increase StockProduitFini for one ProductionLigne and create a
    StockMouvement (ENTREE / PRODUCTION).

    Weighted-average cost formula (mirrors PMP for intrants):
        CMP_new = (Q_old × CMP_old + Q_in × cost_in) / (Q_old + Q_in)
    """
    from stock.models import StockProduitFini, StockMouvement

    produit_fini = ligne.produit_fini

    stock, _ = StockProduitFini.objects.get_or_create(
        produit_fini=produit_fini,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )

    quantite_avant = stock.quantite
    cout_unitaire = Decimal(str(ligne.cout_unitaire_estime or "0"))

    # Weighted-average cost recalculation
    total_qty = stock.quantite + ligne.quantite
    if total_qty > 0:
        valeur_ancienne = stock.quantite * stock.cout_moyen_production
        valeur_entree = ligne.quantite * cout_unitaire
        nouveau_cmp = round((valeur_ancienne + valeur_entree) / total_qty, 4)
    else:
        nouveau_cmp = stock.cout_moyen_production  # fallback (shouldn't happen)

    stock.quantite = stock.quantite + ligne.quantite
    stock.cout_moyen_production = nouveau_cmp
    stock.save(update_fields=["quantite", "cout_moyen_production", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        produit_fini=produit_fini,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_PRODUCTION,
        quantite=ligne.quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=production.date_production,
        reference_id=production.pk,
        reference_label=(
            f"Production {production.lot.designation} — {production.date_production}"
        ),
        created_by=production.created_by,
    )

    logger.debug(
        "Stock entry (production): produit_fini pk=%s +%s → %s (CMP: %s DZD).",
        produit_fini.pk,
        ligne.quantite,
        stock.quantite,
        nouveau_cmp,
    )
