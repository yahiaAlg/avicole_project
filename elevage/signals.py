"""
elevage/signals.py

Signals for the elevage (poultry raising) app.

Registered signals:
  1. pre_save  on Consommation → cache the pre-save state (quantite, intrant)
                                 to allow diff computation on updates.
  2. post_save on Consommation → decrease StockIntrant and create a
                                  StockMouvement (sortie) when a consumption
                                  event is created or its quantity changes.
  3. pre_delete on Consommation → reverse the stock decrease when a
                                   consumption record is deleted (restore
                                   stock balance and log a corrective mouvement).

Business rules enforced here:
  - Lot must be OUVERT to accept Consommation (enforced in model.clean()).
  - Stock is decreased immediately on save — no deferred validation.
  - If a Consommation is deleted, the stock is restored to maintain
    consistency between StockIntrant.quantite and StockMouvement history.
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from elevage.models import Consommation, Mortalite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mortalite signals: decrease / restore poussin StockIntrant
# ---------------------------------------------------------------------------
# When a bird dies, its unit should leave the poussin intrant stock.
# We identify the intrant via the BL linked to the lot (bl_fournisseur_poussins).
# If no BL is linked, we fall back to the first poussin-category intrant line
# on any received BL from the lot's poussin supplier — or skip gracefully.
# ---------------------------------------------------------------------------


def _get_poussin_intrant(lot):
    """
    Return the Intrant (poussin) associated with the lot's opening BL.
    Falls back to any poussin-category intrant from the lot's supplier.
    Returns None if no poussin intrant can be determined.
    """
    bl = lot.bl_fournisseur_poussins
    if bl:
        ligne = (
            bl.lignes.filter(intrant__categorie__code="POUSSIN")
            .select_related("intrant")
            .first()
        )
        if ligne:
            return ligne.intrant

    # Fallback: any received BL from the poussin supplier with a poussin line
    from achats.models import BLFournisseur

    ligne = (
        BLFournisseur.objects.filter(
            fournisseur=lot.fournisseur_poussins,
            statut__in=[BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE],
        )
        .prefetch_related("lignes__intrant__categorie")
        .values_list("lignes__intrant", flat=True)
        .filter(lignes__intrant__categorie__code="POUSSIN")
        .first()
    )
    if ligne:
        from intrants.models import Intrant

        return Intrant.objects.get(pk=ligne)

    logger.warning(
        "_get_poussin_intrant: could not identify poussin intrant for lot pk=%s.",
        lot.pk,
    )
    return None


@receiver(pre_save, sender=Mortalite)
def mortalite_pre_save(sender, instance, **kwargs):
    """Cache old nombre + intrant before save so post_save can compute delta."""
    if instance.pk:
        try:
            old = Mortalite.objects.get(pk=instance.pk)
            instance._old_nombre = old.nombre
            instance._old_lot_id = old.lot_id
        except Mortalite.DoesNotExist:
            instance._old_nombre = None
            instance._old_lot_id = None
    else:
        instance._old_nombre = None
        instance._old_lot_id = None


@receiver(post_save, sender=Mortalite)
def mortalite_post_save(sender, instance, created, **kwargs):
    """
    Decrease poussin StockIntrant by the number of dead birds.

    Create: decrease by instance.nombre.
    Update (same lot): apply net delta (new - old).
    Update (lot changed): restore old lot's stock, decrease new lot's stock.
    """
    from stock.models import StockIntrant, StockMouvement

    lot = instance.lot
    intrant = _get_poussin_intrant(lot)
    if not intrant:
        return

    old_nombre = getattr(instance, "_old_nombre", None)
    old_lot_id = getattr(instance, "_old_lot_id", None)

    if created:
        _appliquer_sortie_mortalite(
            intrant=intrant,
            nombre=instance.nombre,
            date=instance.date,
            lot=lot,
            reference_id=instance.pk,
        )

    else:
        lot_changed = old_lot_id and old_lot_id != lot.pk

        if lot_changed:
            # Restore old lot's stock
            from elevage.models import LotElevage

            try:
                old_lot = LotElevage.objects.get(pk=old_lot_id)
                old_intrant = _get_poussin_intrant(old_lot)
                if old_intrant and old_nombre:
                    _appliquer_entree_mortalite_correction(
                        intrant=old_intrant,
                        nombre=old_nombre,
                        date=instance.date,
                        reference_id=instance.pk,
                        notes=f"Correction: lot modifié sur mortalite pk={instance.pk}.",
                    )
            except LotElevage.DoesNotExist:
                pass
            # Apply full amount to new lot
            _appliquer_sortie_mortalite(
                intrant=intrant,
                nombre=instance.nombre,
                date=instance.date,
                lot=lot,
                reference_id=instance.pk,
            )

        elif old_nombre is not None and old_nombre != instance.nombre:
            delta = instance.nombre - old_nombre
            if delta > 0:
                _appliquer_sortie_mortalite(
                    intrant=intrant,
                    nombre=delta,
                    date=instance.date,
                    lot=lot,
                    reference_id=instance.pk,
                    notes=f"Delta update (+{delta}) sur mortalite pk={instance.pk}.",
                )
            else:
                _appliquer_entree_mortalite_correction(
                    intrant=intrant,
                    nombre=abs(delta),
                    date=instance.date,
                    reference_id=instance.pk,
                    notes=f"Delta update ({delta}) sur mortalite pk={instance.pk}.",
                )


@receiver(pre_delete, sender=Mortalite)
def mortalite_pre_delete(sender, instance, **kwargs):
    """Restore poussin stock when a mortality record is deleted."""
    intrant = _get_poussin_intrant(instance.lot)
    if not intrant:
        return
    logger.info(
        "Mortalite pk=%s being deleted. Restoring %s of intrant pk=%s to stock.",
        instance.pk,
        instance.nombre,
        intrant.pk,
    )
    _appliquer_entree_mortalite_correction(
        intrant=intrant,
        nombre=instance.nombre,
        date=instance.date,
        reference_id=instance.pk,
        notes=f"Annulation de la mortalite pk={instance.pk} (suppression).",
    )


def _appliquer_sortie_mortalite(intrant, nombre, date, lot, reference_id, notes=""):
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - Decimal(str(nombre))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock poussin négatif après mortalite: intrant pk=%s quantite=%s.",
            intrant.pk,
            stock.quantite,
        )

    StockMouvement.objects.create(
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_CONSOMMATION,  # closest available source
        quantite=Decimal(str(nombre)),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date,
        reference_id=reference_id,
        reference_label=f"Mortalite lot {lot.designation}",
        notes=notes,
        created_by=None,
    )
    logger.debug(
        "Sortie mortalite: intrant pk=%s -%s → %s (lot: %s).",
        intrant.pk,
        nombre,
        stock.quantite,
        lot.pk,
    )


def _appliquer_entree_mortalite_correction(intrant, nombre, date, reference_id, notes):
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + Decimal(str(nombre))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_CONSOMMATION,
        quantite=Decimal(str(nombre)),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date,
        reference_id=reference_id,
        reference_label=f"Correction mortalite pk={reference_id}",
        notes=notes,
        created_by=None,
    )
    logger.debug(
        "Correction entrée mortalite: intrant pk=%s +%s → %s.",
        intrant.pk,
        nombre,
        stock.quantite,
    )


# ---------------------------------------------------------------------------
# pre_save: cache old state for update-diff detection (Consommation)


@receiver(pre_save, sender=Consommation)
def consommation_pre_save(sender, instance, **kwargs):
    """
    Before saving, retrieve the current DB state so the post_save handler
    can compute the net quantity delta on updates.
    """
    if instance.pk:
        try:
            old = Consommation.objects.select_related("intrant").get(pk=instance.pk)
            instance._old_quantite = old.quantite
            instance._old_intrant_id = old.intrant_id
        except Consommation.DoesNotExist:
            instance._old_quantite = None
            instance._old_intrant_id = None
    else:
        instance._old_quantite = None
        instance._old_intrant_id = None


# ---------------------------------------------------------------------------
# post_save: apply stock decrease
# ---------------------------------------------------------------------------


@receiver(post_save, sender=Consommation)
def consommation_post_save(sender, instance, created, **kwargs):
    """
    Decrease StockIntrant.quantite by the consumed quantity and log a
    StockMouvement of type SORTIE / source CONSOMMATION.

    Create scenario (created=True):
      delta = instance.quantite  (full consumption)

    Update scenario (created=False):
      If the intrant changed, reverse the old intrant's stock and apply
      the full new quantity to the new intrant.
      If only the quantity changed, apply the delta (new - old) to the
      same intrant.

    A negative balance is allowed at model level (physical discrepancy);
    a warning is logged so operators can reconcile via StockAjustement.
    """
    from stock.models import StockIntrant, StockMouvement

    intrant = instance.intrant
    old_quantite = getattr(instance, "_old_quantite", None)
    old_intrant_id = getattr(instance, "_old_intrant_id", None)

    if created:
        # ── New record: decrease stock by full quantity ──────────────────
        _appliquer_sortie_stock(
            intrant=intrant,
            quantite=instance.quantite,
            date=instance.date,
            lot=instance.lot,
            reference_id=instance.pk,
            created_by=instance.created_by,
        )

    else:
        intrant_changed = old_intrant_id and old_intrant_id != intrant.pk

        if intrant_changed:
            # ── Intrant changed: restore old, apply full new ──────────────
            from intrants.models import Intrant as IntrantModel

            try:
                old_intrant = IntrantModel.objects.get(pk=old_intrant_id)
                _appliquer_entree_stock_correction(
                    intrant=old_intrant,
                    quantite=old_quantite,
                    date=instance.date,
                    reference_id=instance.pk,
                    notes=(
                        f"Correction: intrant modifié sur consommation pk={instance.pk}. "
                        "Annulation de la sortie précédente."
                    ),
                    created_by=instance.created_by,
                )
            except IntrantModel.DoesNotExist:
                logger.error(
                    "consommation_post_save: old intrant pk=%s not found during intrant-change "
                    "correction for Consommation pk=%s.",
                    old_intrant_id,
                    instance.pk,
                )

            _appliquer_sortie_stock(
                intrant=intrant,
                quantite=instance.quantite,
                date=instance.date,
                lot=instance.lot,
                reference_id=instance.pk,
                created_by=instance.created_by,
            )

        elif old_quantite is not None and old_quantite != instance.quantite:
            # ── Same intrant, quantity changed: apply net delta ───────────
            delta = instance.quantite - old_quantite

            if delta > 0:
                # Quantity increased → additional sortie
                _appliquer_sortie_stock(
                    intrant=intrant,
                    quantite=delta,
                    date=instance.date,
                    lot=instance.lot,
                    reference_id=instance.pk,
                    created_by=instance.created_by,
                    notes=f"Delta update (+{delta}) sur consommation pk={instance.pk}.",
                )
            else:
                # Quantity decreased → partial reversal (entree corrective)
                _appliquer_entree_stock_correction(
                    intrant=intrant,
                    quantite=abs(delta),
                    date=instance.date,
                    reference_id=instance.pk,
                    notes=f"Delta update ({delta}) sur consommation pk={instance.pk}.",
                    created_by=instance.created_by,
                )


# ---------------------------------------------------------------------------
# pre_delete: reverse stock decrease when a Consommation is deleted
# ---------------------------------------------------------------------------


@receiver(pre_delete, sender=Consommation)
def consommation_pre_delete(sender, instance, **kwargs):
    """
    Before deleting a Consommation, restore the stock balance by creating
    a corrective ENTREE movement.  This keeps StockIntrant.quantite
    consistent with the sum of all StockMouvement records.
    """
    logger.info(
        "Consommation pk=%s being deleted. Restoring %s of intrant pk=%s to stock.",
        instance.pk,
        instance.quantite,
        instance.intrant_id,
    )

    _appliquer_entree_stock_correction(
        intrant=instance.intrant,
        quantite=instance.quantite,
        date=instance.date,
        reference_id=instance.pk,
        notes=f"Annulation de la consommation pk={instance.pk} (suppression).",
        created_by=None,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _appliquer_sortie_stock(
    intrant, quantite, date, lot, reference_id, created_by, notes=""
):
    """
    Decrease StockIntrant.quantite by *quantite* and record a StockMouvement
    (SORTIE / CONSOMMATION).
    """
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après consommation: intrant pk=%s quantite=%s. "
            "Vérifiez les entrées ou créez un ajustement.",
            intrant.pk,
            stock.quantite,
        )

    ref_label = (
        f"Conso lot {lot.designation} — {intrant.designation}"
        if lot
        else f"Conso {intrant.designation}"
    )

    StockMouvement.objects.create(
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_CONSOMMATION,
        quantite=quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date,
        reference_id=reference_id,
        reference_label=ref_label,
        notes=notes,
        created_by=created_by,
    )

    logger.debug(
        "Sortie stock: intrant pk=%s -%s → %s (lot: %s).",
        intrant.pk,
        quantite,
        stock.quantite,
        lot.pk if lot else "—",
    )


def _appliquer_entree_stock_correction(
    intrant, quantite, date, reference_id, notes, created_by
):
    """
    Increase StockIntrant.quantite by *quantite* as a corrective ENTREE
    (used for update-diff reversals and deletions).
    PMP is not recalculated on corrections — the existing PMP is preserved.
    """
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_CONSOMMATION,
        quantite=quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date,
        reference_id=reference_id,
        reference_label=f"Correction consommation pk={reference_id}",
        notes=notes,
        created_by=created_by,
    )

    logger.debug(
        "Correction entrée stock: intrant pk=%s +%s → %s.",
        intrant.pk,
        quantite,
        stock.quantite,
    )
