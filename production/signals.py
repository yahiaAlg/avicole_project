"""
production/signals.py

Signals for the production app.

Registered signals:
  1. post_save on ProduitFini      → auto-create a StockProduitFini for every
                                     existing Branche, mirroring the same
                                     guarantee provided for Intrant →
                                     StockIntrant in intrants/signals.py.
  2. pre_save  on ProductionRecord → cache old statut for transition detection.
  3. post_save on ProductionRecord → when statut transitions to VALIDE, for
                                     every ProductionLigne:
                                       a. Increase StockProduitFini.quantite
                                          for production.branche.
                                       b. Recalculate StockProduitFini.cout_moyen_production.
                                       c. Create a StockMouvement (entree / production),
                                          scoped to production.branche.
  4. pre_save  on TraitementFertilisant → cache old statut for transition detection.
  5. post_save on TraitementFertilisant → when statut transitions to VALIDE,
                                     increase StockProduitFini for the
                                     treatment's produit_fini (scoped to
                                     instance.branche) and create a
                                     StockMouvement (entree / production).

Business rules enforced here:
  - Only a BROUILLON → VALIDE transition triggers stock entries; re-saving a
    validated record does not double-count.
  - poids_moyen_kg is auto-computed by ProductionRecord.save() (model level).
  - cout_moyen_production uses a weighted average analogous to the PMP for
    intrants: (old_qty × old_cost + new_qty × new_cost) / total_qty. The same
    formula is reused for TraitementFertilisant.
  - v1.4 (BR-BRA-07): StockProduitFini is keyed by (branche, produit_fini),
    not by produit_fini alone. ProductionRecord.branche is denormalized from
    lot.branche (kept in sync in ProductionRecord.save()); TraitementFertilisant.branche
    is set explicitly at creation. Every stock lookup below is scoped to the
    relevant branche accordingly.
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from production.models import (
    ProduitFini,
    ProductionRecord,
    TraitementFertilisant,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal 1 — ProduitFini: auto-create StockProduitFini
# ---------------------------------------------------------------------------


@receiver(post_save, sender=ProduitFini)
def creer_stock_produit_fini(sender, instance, created, **kwargs):
    """
    On first save of a ProduitFini, create a matching StockProduitFini
    (quantite=0) for every Branche currently in the system, so that
    quantite_en_stock / quantite_en_stock_branche never raise
    RelatedObjectDoesNotExist (BR-BRA-07).
    """
    if not created:
        return

    from core.models import Branche
    from stock.models import StockProduitFini

    branches = list(Branche.objects.all())
    if not branches:
        logger.warning(
            "creer_stock_produit_fini: aucune Branche n'existe encore — "
            "ProduitFini pk=%s (%s) créé sans StockProduitFini. Une ligne "
            "sera créée pour chaque branche dès qu'elle existera (BR-BRA-07).",
            instance.pk,
            instance.designation,
        )
        return

    nb_crees = 0
    for branche in branches:
        stock, was_created = StockProduitFini.objects.get_or_create(
            branche=branche,
            produit_fini=instance,
            defaults={
                "quantite": Decimal("0"),
                "cout_moyen_production": Decimal("0"),
                "seuil_alerte": Decimal("0"),
            },
        )
        if was_created:
            nb_crees += 1

    logger.debug(
        "StockProduitFini créé pour le nouveau ProduitFini pk=%s (%s) sur %d/%d branche(s).",
        instance.pk,
        instance.designation,
        nb_crees,
        len(branches),
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
    StockMouvement (ENTREE / PRODUCTION), scoped to production.branche
    (BR-BRA-07 — stock is keyed by (branche, produit_fini)).

    Weighted-average cost formula (mirrors PMP for intrants):
        CMP_new = (Q_old × CMP_old + Q_in × cost_in) / (Q_old + Q_in)
    """
    from stock.models import StockProduitFini, StockMouvement

    produit_fini = ligne.produit_fini
    branche = production.branche

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=branche,
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
    stock.save(
        update_fields=["quantite", "cout_moyen_production", "derniere_mise_a_jour"]
    )

    StockMouvement.objects.create(
        branche=branche,
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
        "Stock entry (production): produit_fini pk=%s +%s → %s (CMP: %s DZD, branche=%s).",
        produit_fini.pk,
        ligne.quantite,
        stock.quantite,
        nouveau_cmp,
        branche.code,
    )


# ---------------------------------------------------------------------------
# Signal 4 — TraitementFertilisant: cache old statut
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=TraitementFertilisant)
def traitement_fertilisant_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save statut so the post_save handler can detect the
    BROUILLON → VALIDE transition without an extra DB query (mirrors
    production_record_pre_save).
    """
    if instance.pk:
        try:
            instance._old_statut = (
                TraitementFertilisant.objects.filter(pk=instance.pk)
                .values_list("statut", flat=True)
                .get()
            )
        except TraitementFertilisant.DoesNotExist:
            instance._old_statut = None
    else:
        instance._old_statut = None


# ---------------------------------------------------------------------------
# Signal 5 — TraitementFertilisant: credit stock on VALIDE transition
# ---------------------------------------------------------------------------


@receiver(post_save, sender=TraitementFertilisant)
def traitement_fertilisant_post_save(sender, instance, created, **kwargs):
    """
    On BROUILLON → VALIDE transition, increase StockProduitFini for the
    treatment's finished fertilizer and create a StockMouvement (ENTREE,
    source=FERTILISANT). Weighted-average cost logic mirrors
    _enregistrer_entree_stock_production.

    A re-save of an already-validated treatment is a no-op.
    """
    old_statut = getattr(instance, "_old_statut", None)
    is_validating = (
        instance.statut == TraitementFertilisant.STATUT_VALIDE
        and old_statut != TraitementFertilisant.STATUT_VALIDE
    )
    if not is_validating:
        return

    if not instance.quantite_obtenue_kg or instance.quantite_obtenue_kg <= 0:
        logger.warning(
            "TraitementFertilisant pk=%s validé avec quantite_obtenue_kg<=0. "
            "Aucune entrée stock créée.",
            instance.pk,
        )
        return

    from stock.models import StockProduitFini, StockMouvement

    produit_fini = instance.produit_fini
    branche = instance.branche

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=branche,
        produit_fini=produit_fini,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )

    quantite_avant = stock.quantite
    cout_unitaire = Decimal(str(instance.cout_unitaire_estime or "0"))
    quantite_entree = Decimal(str(instance.quantite_obtenue_kg))

    total_qty = stock.quantite + quantite_entree
    if total_qty > 0:
        valeur_ancienne = stock.quantite * stock.cout_moyen_production
        valeur_entree = quantite_entree * cout_unitaire
        nouveau_cmp = round((valeur_ancienne + valeur_entree) / total_qty, 4)
    else:
        nouveau_cmp = stock.cout_moyen_production

    stock.quantite = total_qty
    stock.cout_moyen_production = nouveau_cmp
    stock.save(
        update_fields=["quantite", "cout_moyen_production", "derniere_mise_a_jour"]
    )

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit_fini,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_FERTILISANT,
        quantite=quantite_entree,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date_traitement,
        reference_id=instance.pk,
        reference_label=f"Traitement fertilisant — {instance.date_traitement}",
        created_by=instance.created_by,
    )

    logger.info(
        "TraitementFertilisant pk=%s validé: produit_fini pk=%s +%s kg → %s "
        "(CMP: %s DZD, branche=%s).",
        instance.pk,
        produit_fini.pk,
        quantite_entree,
        stock.quantite,
        nouveau_cmp,
        branche.code,
    )
