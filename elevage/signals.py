"""
elevage/signals.py

Signals for the elevage (poultry raising) app.

Registered signals:
  1. pre_save  on Consommation → cache the pre-save state (quantite, intrant)
                                 to allow diff computation on updates.
  2. post_save on Consommation → decrease StockIntrant (scoped to the lot's
                                  branche) and create a StockMouvement
                                  (sortie) when a consumption event is
                                  created or its quantity changes.
  3. pre_delete on Consommation → reverse the stock decrease when a
                                   consumption record is deleted (restore
                                   stock balance and log a corrective mouvement).

Business rules enforced here:
  - Lot must be OUVERT to accept Consommation (enforced in model.clean()).
  - Stock is decreased immediately on save — no deferred validation.
  - If a Consommation is deleted, the stock is restored to maintain
    consistency between StockIntrant.quantite and StockMouvement history.
  - v1.4 (BR-BRA-01/07): every Mortalite/Consommation/RecolteOeufs inherits
    its branche from its lot (lot.branche, via the model's `branche`
    property). StockIntrant/StockProduitFini are keyed by (branche, item),
    so every stock lookup below is scoped to that branche.
"""

import logging
from decimal import Decimal

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from elevage.models import (
    Consommation,
    Mortalite,
    TransfertLot,
    RecolteOeufs,
    LotElevage,
    ProductionAliment,
    RetraitOeufs,
)

logger = logging.getLogger(__name__)


def _get_produit_oeufs():
    """
    Resolve the ProduitFini that egg harvests should credit.

    Picks the first active ProduitFini of type OEUFS. If the farm ever needs
    more than one egg SKU (e.g. by calibre), this should be replaced with an
    explicit FK on RecolteOeufs — kept simple for now since eggs aren't
    differentiated at the catalogue level yet.
    """
    from production.models import ProduitFini

    qs = ProduitFini.objects.filter(type_produit__code="OEUFS", actif=True)
    produit = qs.first()
    if qs.count() > 1:
        logger.warning(
            "_get_produit_oeufs: %d ProduitFini actifs de type OEUFS trouvés — "
            "utilisation du premier (pk=%s). Envisager une distinction explicite.",
            qs.count(),
            produit.pk,
        )
    if not produit:
        logger.error(
            "_get_produit_oeufs: aucun ProduitFini actif de type OEUFS trouvé. "
            "Créez-en un pour que la récolte d'œufs alimente le stock."
        )
    return produit


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
    Decrease poussin StockIntrant by the number of dead birds, scoped to the
    lot's branche (BR-BRA-07).

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
            branche=lot.branche,
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
                        branche=old_lot.branche,
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
                branche=lot.branche,
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
                    branche=lot.branche,
                    reference_id=instance.pk,
                    notes=f"Delta update (+{delta}) sur mortalite pk={instance.pk}.",
                )
            else:
                _appliquer_entree_mortalite_correction(
                    intrant=intrant,
                    nombre=abs(delta),
                    date=instance.date,
                    branche=lot.branche,
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
        branche=instance.lot.branche,
        reference_id=instance.pk,
        notes=f"Annulation de la mortalite pk={instance.pk} (suppression).",
    )


def _appliquer_sortie_mortalite(
    intrant, nombre, date, lot, branche, reference_id, notes=""
):
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - Decimal(str(nombre))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock poussin négatif après mortalite: intrant pk=%s quantite=%s (branche=%s).",
            intrant.pk,
            stock.quantite,
            branche.code,
        )

    StockMouvement.objects.create(
        branche=branche,
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_MORTALITE,
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
        "Sortie mortalite: intrant pk=%s -%s → %s (lot: %s, branche=%s).",
        intrant.pk,
        nombre,
        stock.quantite,
        lot.pk,
        branche.code,
    )


def _appliquer_entree_mortalite_correction(
    intrant, nombre, date, branche, reference_id, notes
):
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + Decimal(str(nombre))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_MORTALITE,
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
        "Correction entrée mortalite: intrant pk=%s +%s → %s (branche=%s).",
        intrant.pk,
        nombre,
        stock.quantite,
        branche.code,
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
    StockMouvement of type SORTIE / source CONSOMMATION, scoped to the
    lot's branche (BR-BRA-07).

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
    branche = instance.lot.branche
    old_quantite = getattr(instance, "_old_quantite", None)
    old_intrant_id = getattr(instance, "_old_intrant_id", None)

    est_aliment = bool(
        getattr(intrant, "categorie_id", None)
        and intrant.categorie.code == "ALIMENT"
    )

    if created:
        # ── New record: decrease stock by full quantity ──────────────────
        _appliquer_sortie_stock(
            intrant=intrant,
            quantite=instance.quantite,
            date=instance.date,
            lot=instance.lot,
            branche=branche,
            reference_id=instance.pk,
            created_by=instance.created_by,
        )
        if est_aliment:
            _allouer_consommation_aliment(instance, instance.quantite)

    else:
        intrant_changed = old_intrant_id and old_intrant_id != intrant.pk

        if intrant_changed:
            # Batch-costing ledger (BR-request): reverse any allocation made
            # against the OLD intrant's batches before touching stock, since
            # a fresh allocation against the NEW intrant follows below.
            _reverser_allocations_aliment(instance)

            # ── Intrant changed: restore old, apply full new ──────────────
            from intrants.models import Intrant as IntrantModel

            try:
                old_intrant = IntrantModel.objects.get(pk=old_intrant_id)
                _appliquer_entree_stock_correction(
                    intrant=old_intrant,
                    quantite=old_quantite,
                    date=instance.date,
                    branche=branche,
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
                branche=branche,
                reference_id=instance.pk,
                created_by=instance.created_by,
            )
            if est_aliment:
                _allouer_consommation_aliment(instance, instance.quantite)

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
                    branche=branche,
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
                    branche=branche,
                    reference_id=instance.pk,
                    notes=f"Delta update ({delta}) sur consommation pk={instance.pk}.",
                    created_by=instance.created_by,
                )

            # Batch-costing ledger (BR-request): re-derive the FIFO
            # allocation from scratch against the new quantity rather than
            # patching the delta — simpler, and always consistent since the
            # set of open batches may itself have changed since the
            # original allocation.
            if est_aliment:
                _reverser_allocations_aliment(instance)
                _allouer_consommation_aliment(instance, instance.quantite)


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

    # Batch-costing ledger (BR-request): give back the kg + façon cost to
    # whichever ProductionAliment batch(es) this consumption had drawn from,
    # before restoring the aggregate StockIntrant balance below.
    if (
        getattr(instance.intrant, "categorie_id", None)
        and instance.intrant.categorie.code == "ALIMENT"
    ):
        _reverser_allocations_aliment(instance)

    _appliquer_entree_stock_correction(
        intrant=instance.intrant,
        quantite=instance.quantite,
        date=instance.date,
        branche=instance.lot.branche,
        reference_id=instance.pk,
        notes=f"Annulation de la consommation pk={instance.pk} (suppression).",
        created_by=None,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _appliquer_sortie_stock(
    intrant, quantite, date, lot, branche, reference_id, created_by, notes=""
):
    """
    Decrease StockIntrant.quantite by *quantite* and record a StockMouvement
    (SORTIE / CONSOMMATION), scoped to `branche` (BR-BRA-07).
    """
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après consommation: intrant pk=%s quantite=%s (branche=%s). "
            "Vérifiez les entrées ou créez un ajustement.",
            intrant.pk,
            stock.quantite,
            branche.code,
        )

    ref_label = (
        f"Conso lot {lot.designation} — {intrant.designation}"
        if lot
        else f"Conso {intrant.designation}"
    )

    StockMouvement.objects.create(
        branche=branche,
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
        "Sortie stock: intrant pk=%s -%s → %s (lot: %s, branche=%s).",
        intrant.pk,
        quantite,
        stock.quantite,
        lot.pk if lot else "—",
        branche.code,
    )


def _appliquer_entree_stock_correction(
    intrant, quantite, date, branche, reference_id, notes, created_by
):
    """
    Increase StockIntrant.quantite by *quantite* as a corrective ENTREE
    (used for update-diff reversals and deletions), scoped to `branche`
    (BR-BRA-07).
    PMP is not recalculated on corrections — the existing PMP is preserved.
    """
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
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
        "Correction entrée stock: intrant pk=%s +%s → %s (branche=%s).",
        intrant.pk,
        quantite,
        stock.quantite,
        branche.code,
    )


# ---------------------------------------------------------------------------
# Batch costing (BR-request): FIFO-allocate a feed (ALIMENT) Consommation
# across open ProductionAliment batches, and keep each batch's
# quantite_restante_kg / cout_facon_impute in sync as consumptions come in,
# get edited, or get deleted.
#
# This runs ALONGSIDE the aggregate StockIntrant decrease above (unchanged) —
# it's a separate, finer-grained ledger (ConsommationAlimentAllocation) that
# answers "which specific milling batch did this consumption actually come
# from, and how much of that batch's façon (mill labor) fee should this
# consumption's lot be charged". See ProductionAliment.quantite_restante_kg /
# prix_facon_unitaire / cout_facon_impute and Consommation.cout_facon_alloue_total.
# ---------------------------------------------------------------------------


def _allouer_consommation_aliment(consommation, quantite):
    """
    FIFO-allocate *quantite* kg of a feed Consommation across open
    ProductionAliment batches — same branche, same intrant_produit,
    quantite_restante_kg > 0 — oldest production date first.

    For every batch touched: decrement its quantite_restante_kg, recognize
    its façon cost slice (quantite_kg × prix_facon_unitaire — 0 if the
    batch's façon fee isn't known/paid yet) into cout_facon_impute, and
    record a ConsommationAlimentAllocation row so the slice can later be
    reversed (edit/delete) or retroactively re-priced once the façon fee
    IS known (see views._recognize_facon_cost_for_batch).

    Leniently stops once no open batch remains, even if *quantite* isn't
    fully allocated — pre-existing stock (predating this feature) or a
    manual StockAjustement correction has no batch to draw from. Never
    blocks the Consommation save; the gap is just logged.
    """
    from elevage.models import ConsommationAlimentAllocation, ProductionAliment

    restante = Decimal(str(quantite))
    if restante <= 0:
        return

    batches = ProductionAliment.objects.select_for_update().filter(
        branche=consommation.lot.branche,
        intrant_produit_id=consommation.intrant_id,
        quantite_restante_kg__gt=0,
    ).order_by("date", "created_at")

    for batch in batches:
        if restante <= 0:
            break

        pris = min(batch.quantite_restante_kg, restante)
        if pris <= 0:
            continue

        cout_slice = Decimal("0")
        if batch.prix_facon_unitaire and batch.prix_facon_unitaire > 0:
            cout_slice = (pris * batch.prix_facon_unitaire).quantize(Decimal("0.01"))

        batch.quantite_restante_kg = batch.quantite_restante_kg - pris
        batch.cout_facon_impute = batch.cout_facon_impute + cout_slice
        batch.save(update_fields=["quantite_restante_kg", "cout_facon_impute"])

        ConsommationAlimentAllocation.objects.create(
            consommation=consommation,
            production=batch,
            quantite_kg=pris,
            cout_facon_alloue=cout_slice,
        )

        restante -= pris

    if restante > 0:
        logger.info(
            "Consommation pk=%s: %s kg de « %s » non rattachés à une "
            "ProductionAliment ouverte (stock antérieur à la fonctionnalité "
            "de traçabilité par lot, ou correction manuelle) — coût façon "
            "ignoré pour cette part.",
            consommation.pk,
            restante,
            consommation.intrant.designation,
        )


def _reverser_allocations_aliment(consommation):
    """
    Undo every ConsommationAlimentAllocation tied to *consommation*:
    restore each batch's quantite_restante_kg and cout_facon_impute, then
    delete the allocation rows. Called before an update (quantity or
    intrant change) or a delete, so the batch FIFO ledger stays consistent
    — mirrors the existing entrée-corrective pattern used for StockIntrant
    elsewhere in this module. No-op (queryset simply empty) for a
    non-ALIMENT consommation or one with no allocations yet.
    """
    for alloc in consommation.allocations_batch.select_related("production").all():
        batch = alloc.production
        batch.quantite_restante_kg = batch.quantite_restante_kg + alloc.quantite_kg
        batch.cout_facon_impute = batch.cout_facon_impute - alloc.cout_facon_alloue
        batch.save(update_fields=["quantite_restante_kg", "cout_facon_impute"])
    consommation.allocations_batch.all().delete()


# ---------------------------------------------------------------------------
# TransfertLot: apply building move on creation
# ---------------------------------------------------------------------------


@receiver(post_save, sender=TransfertLot)
def transfert_lot_post_save(sender, instance, created, **kwargs):
    """
    On creation of a TransfertLot, execute the chosen mode:

    MODE_FULL
        Move the source lot's batiment to batiment_destination (existing behaviour).
        Baseline unchanged — all birds travel together as one cohort.

    MODE_SPLIT_NEW
        Partial move — source lot stays in its current building:
          • source.nombre_poussins_initial -= effectif_transfere
          • A new child LotElevage is created at batiment_destination with
            nombre_poussins_initial = effectif_transfere, inheriting
            fournisseur_poussins / souche / date_ouverture from the parent.
          • TransfertLot.lot_enfant is back-patched to the new child lot.

    MODE_SPLIT_MERGE
        Partial move — source stays, destination absorbs the birds:
          • source.nombre_poussins_initial -= effectif_transfere
          • lot_destination.nombre_poussins_initial += effectif_transfere

    Re-saves (edits to notes/motif) never re-trigger — only initial creation.
    """
    if not created:
        return

    lot = instance.lot
    mode = instance.mode

    if mode == instance.MODE_FULL:
        # ── Full transfer: source loses all transferred birds, a child lot
        #    is created at the destination building, then source is closed.
        n = instance.effectif_transfere

        # 1. Decrease source baseline
        lot.nombre_poussins_initial = lot.nombre_poussins_initial - n
        lot.save(update_fields=["nombre_poussins_initial", "updated_at"])

        # 2. Build child lot designation
        designation = (
            instance.designation_lot_enfant.strip()
            if instance.designation_lot_enfant
            else f"{lot.designation} — {instance.batiment_destination.nom}"
        )

        # 3. Create child lot at destination
        child_lot = LotElevage.objects.create(
            designation=designation,
            date_ouverture=lot.date_ouverture,
            nombre_poussins_initial=n,
            fournisseur_poussins=lot.fournisseur_poussins,
            bl_fournisseur_poussins=None,
            batiment=instance.batiment_destination,
            souche=lot.souche,
            notes=(
                f"دفعة منقولة كاملةً من «{lot.designation}» "
                f"بتاريخ {instance.date_transfert.strftime('%d/%m/%Y')} "
                f"({n} طير)."
            ),
            lot_parent=lot,
            created_by=instance.created_by,
        )

        # 4. Back-patch lot_enfant
        TransfertLot.objects.filter(pk=instance.pk).update(lot_enfant=child_lot)

        # 5. Close the source lot — it is now empty
        lot.fermer(date_fermeture=instance.date_transfert)

        logger.info(
            "TransfertLot pk=%s (FULL): lot pk=%s closed; "
            "child lot pk=%s created at batiment pk=%s (age %s j).",
            instance.pk,
            lot.pk,
            child_lot.pk,
            instance.batiment_destination_id,
            instance.age_jours_transfert,
        )

    elif mode == instance.MODE_SPLIT_NEW:
        # ── Partial: create child lot ────────────────────────────────────
        n = instance.effectif_transfere

        # Decrease source baseline
        lot.nombre_poussins_initial = lot.nombre_poussins_initial - n
        lot.save(update_fields=["nombre_poussins_initial", "updated_at"])

        # Build child lot designation
        designation = (
            instance.designation_lot_enfant.strip()
            or f"{lot.designation} — شق {instance.date_transfert.strftime('%d/%m/%Y')}"
        )

        child_lot = LotElevage.objects.create(
            designation=designation,
            date_ouverture=lot.date_ouverture,
            nombre_poussins_initial=n,
            fournisseur_poussins=lot.fournisseur_poussins,
            bl_fournisseur_poussins=None,  # not a new delivery — no BL
            batiment=instance.batiment_destination,
            souche=lot.souche,
            notes=(
                f"دفعة فرعية مُنشأة بالتقسيم من «{lot.designation}» "
                f"بتاريخ {instance.date_transfert.strftime('%d/%m/%Y')} "
                f"({n} طير)."
            ),
            lot_parent=lot,
            created_by=instance.created_by,
        )

        # Back-patch lot_enfant without re-triggering this signal
        TransfertLot.objects.filter(pk=instance.pk).update(lot_enfant=child_lot)

        logger.info(
            "TransfertLot pk=%s (SPLIT_NEW): lot pk=%s baseline -%s; "
            "child lot pk=%s created at batiment pk=%s.",
            instance.pk,
            lot.pk,
            n,
            child_lot.pk,
            instance.batiment_destination_id,
        )

    elif mode == instance.MODE_SPLIT_MERGE:
        # ── Partial: merge into existing destination lot ─────────────────
        n = instance.effectif_transfere
        dest_lot = instance.lot_destination

        # Decrease source baseline
        lot.nombre_poussins_initial = lot.nombre_poussins_initial - n
        lot.save(update_fields=["nombre_poussins_initial", "updated_at"])

        # Increase destination baseline
        dest_lot.nombre_poussins_initial = dest_lot.nombre_poussins_initial + n
        dest_lot.save(update_fields=["nombre_poussins_initial", "updated_at"])

        logger.info(
            "TransfertLot pk=%s (SPLIT_MERGE): lot pk=%s baseline -%s; "
            "dest lot pk=%s baseline +%s.",
            instance.pk,
            lot.pk,
            n,
            dest_lot.pk,
            n,
        )


# ---------------------------------------------------------------------------
# RecolteOeufs: credit StockProduitFini (œufs) on save / reverse on delete
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=RecolteOeufs)
def recolte_oeufs_pre_save(sender, instance, **kwargs):
    """Cache old nombre_oeufs before save so post_save can compute the delta."""
    if instance.pk:
        try:
            instance._old_nombre_oeufs = RecolteOeufs.objects.values_list(
                "nombre_oeufs", flat=True
            ).get(pk=instance.pk)
        except RecolteOeufs.DoesNotExist:
            instance._old_nombre_oeufs = None
    else:
        instance._old_nombre_oeufs = None


@receiver(post_save, sender=RecolteOeufs)
def recolte_oeufs_post_save(sender, instance, created, **kwargs):
    """
    Increase StockProduitFini (œufs) and log a StockMouvement (ENTREE,
    source=PONTE), scoped to the lot's branche (BR-BRA-07).
    """
    from stock.models import StockProduitFini, StockMouvement

    produit = _get_produit_oeufs()
    if not produit:
        return

    branche = instance.lot.branche
    old_nombre = getattr(instance, "_old_nombre_oeufs", None)
    delta = (
        instance.nombre_oeufs
        if created
        else (instance.nombre_oeufs - old_nombre if old_nombre is not None else 0)
    )
    if delta == 0:
        return

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=branche,
        produit_fini=produit,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + Decimal(str(delta))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_PONTE,
        quantite=abs(Decimal(str(delta))),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=f"Récolte œufs — {instance.lot.designation} ({instance.date})",
        created_by=instance.created_by,
    )

    logger.debug(
        "Récolte œufs: produit_fini pk=%s %+d → %s (lot pk=%s, branche=%s).",
        produit.pk,
        delta,
        stock.quantite,
        instance.lot_id,
        branche.code,
    )


@receiver(pre_delete, sender=RecolteOeufs)
def recolte_oeufs_pre_delete(sender, instance, **kwargs):
    """Reverse the stock entry when a RecolteOeufs record is deleted."""
    from stock.models import StockProduitFini, StockMouvement

    produit = _get_produit_oeufs()
    if not produit:
        return

    branche = instance.lot.branche

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=branche,
        produit_fini=produit,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - Decimal(str(instance.nombre_oeufs))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_PONTE,
        quantite=Decimal(str(instance.nombre_oeufs)),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=f"Annulation récolte œufs pk={instance.pk} (suppression)",
        created_by=instance.created_by,
    )


# ---------------------------------------------------------------------------
# ProductionAliment: credit the finished feed's StockIntrant, optionally
# debit ingredient StockIntrants per FormuleAlimentLigne
# ---------------------------------------------------------------------------
#
# NOTE: uses the source strings "production_aliment" / "retrait_oeufs" below.
# stock.models.StockMouvement wasn't available in this workspace to confirm
# its SOURCE_CHOICES — if that field enforces a strict choices list, add
# these two source codes there (mirroring SOURCE_PONTE / SOURCE_CONSOMMATION).


@receiver(pre_save, sender=ProductionAliment)
def production_aliment_pre_save(sender, instance, **kwargs):
    """Cache old quantite_produite_kg before save so post_save can diff it."""
    if instance.pk:
        try:
            instance._old_quantite = ProductionAliment.objects.values_list(
                "quantite_produite_kg", flat=True
            ).get(pk=instance.pk)
        except ProductionAliment.DoesNotExist:
            instance._old_quantite = None
    else:
        instance._old_quantite = None


@receiver(post_save, sender=ProductionAliment)
def production_aliment_post_save(sender, instance, created, **kwargs):
    """
    Credit intrant_produit's StockIntrant by the (signed) delta of
    quantite_produite_kg, scoped to instance.branche. If a formule is set,
    also debit each ingredient's StockIntrant proportionally.

    Ingredient sufficiency (BR-INT-03) is enforced upstream in
    ProductionAlimentForm.clean() before this signal ever runs, so this
    debit should never push a StockIntrant negative in the normal form
    flow. This save is still not blocked here at the model layer (mirrors
    Consommation's tolerance) so a manual/scripted ProductionAliment.save()
    outside the form is never silently rejected — only warned about via
    the resulting negative StockIntrant.quantite.

    Costing (BR-request — direct entry is the primary flow): when this
    replenishment adds stock (delta > 0), the feed's
    StockIntrant.prix_moyen_pondere is refreshed by the standard weighted-
    average formula, using:
      - instance.prix_unitaire directly, for a bare/direct entry (no
        formule) — the common case: "we just bought/received X kg at Y
        DZD/kg";
      - otherwise, when a formule is used, the cost implied by the current
        PMP of each ingredient actually debited below (Σ qty×PMP / delta) —
        so the operator can leave prix_unitaire at 0 for that path.
    A cost of 0 (direct entry, no price known) or an ingredient with no PMP
    yet simply skips the PMP update — the previous PMP is preserved, same
    tolerance already used elsewhere for stock corrections. Decreases
    (delta < 0, e.g. a downward edit) never touch PMP either, since
    "un-mixing" a weighted average retroactively isn't meaningful.
    """
    from stock.models import StockIntrant, StockMouvement

    old_qty = getattr(instance, "_old_quantite", None)
    delta = (
        instance.quantite_produite_kg
        if created
        else (
            instance.quantite_produite_kg - old_qty
            if old_qty is not None
            else Decimal("0")
        )
    )
    if delta == 0:
        return

    delta = Decimal(str(delta))

    # --- Optional: debit ingredients per formule, proportional to delta,
    #     and tally their cost (at their current PMP) to imply a unit cost
    #     for the finished feed below. ---------------------------------
    cout_ingredients_total = Decimal("0")
    if instance.formule_id:
        for ligne in instance.formule.lignes.select_related("intrant").all():
            qte_ingredient = (delta / Decimal("100")) * ligne.proportion_kg
            if qte_ingredient == 0:
                continue
            ing_stock, _ = StockIntrant.objects.get_or_create(
                branche=instance.branche,
                intrant=ligne.intrant,
                defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
            )
            avant = ing_stock.quantite
            if delta > 0:
                # Cost of the ingredient actually consumed by this
                # replenishment, valued at its own current PMP.
                cout_ingredients_total += qte_ingredient * ing_stock.prix_moyen_pondere
            ing_stock.quantite = ing_stock.quantite - qte_ingredient
            ing_stock.save(update_fields=["quantite"])

            StockMouvement.objects.create(
                branche=instance.branche,
                intrant=ligne.intrant,
                type_mouvement=StockMouvement.TYPE_SORTIE,
                source="production_aliment",
                quantite=abs(qte_ingredient),
                quantite_avant=avant,
                quantite_apres=ing_stock.quantite,
                date_mouvement=instance.date,
                reference_id=instance.pk,
                reference_label=(
                    f"Mélange {instance.formule.nom} pour "
                    f"{instance.intrant_produit.designation} ({instance.date})"
                )[:100],
                created_by=instance.created_by,
            )

    # --- Effective unit cost for this replenishment, if any --------------
    prix_unitaire_effectif = None
    if delta > 0:
        if instance.formule_id:
            if cout_ingredients_total > 0:
                prix_unitaire_effectif = cout_ingredients_total / delta
        elif instance.prix_unitaire and instance.prix_unitaire > 0:
            prix_unitaire_effectif = instance.prix_unitaire

    # --- Credit the finished feed itself, refreshing PMP when priced -----
    stock, _ = StockIntrant.objects.get_or_create(
        branche=instance.branche,
        intrant=instance.intrant_produit,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + delta

    update_fields = ["quantite"]
    if prix_unitaire_effectif is not None:
        quantite_totale = quantite_avant + delta
        if quantite_totale > 0:
            stock.prix_moyen_pondere = round(
                (
                    (quantite_avant * stock.prix_moyen_pondere)
                    + (delta * prix_unitaire_effectif)
                )
                / quantite_totale,
                4,
            )
        else:
            stock.prix_moyen_pondere = round(prix_unitaire_effectif, 4)
        update_fields.append("prix_moyen_pondere")

    stock.save(update_fields=update_fields)

    StockMouvement.objects.create(
        branche=instance.branche,
        intrant=instance.intrant_produit,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source="production_aliment",
        quantite=abs(delta),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=(
            f"Production aliment — {instance.intrant_produit.designation} ({instance.date})"
        )[:100],
        created_by=instance.created_by,
    )

    # --- Batch costing (BR-request): this ProductionAliment row IS a batch;
    #     keep its own quantite_restante_kg in sync with quantite_produite_kg
    #     edits. Uses a bare .update() (no re-save()) to avoid re-entering
    #     this same post_save signal. ------------------------------------
    if created:
        ProductionAliment.objects.filter(pk=instance.pk).update(
            quantite_restante_kg=instance.quantite_produite_kg
        )
        instance.quantite_restante_kg = instance.quantite_produite_kg
    else:
        # An edit changed quantite_produite_kg by `delta` — shift the
        # still-untouched remainder by the same amount, clamped to
        # [0, quantite_produite_kg] (never let a downward edit push it
        # negative, never let an upward edit push it above the new total).
        nouvelle_restante = instance.quantite_restante_kg + delta
        if nouvelle_restante < 0:
            nouvelle_restante = Decimal("0")
        elif nouvelle_restante > instance.quantite_produite_kg:
            nouvelle_restante = instance.quantite_produite_kg
        ProductionAliment.objects.filter(pk=instance.pk).update(
            quantite_restante_kg=nouvelle_restante
        )
        instance.quantite_restante_kg = nouvelle_restante


@receiver(pre_delete, sender=ProductionAliment)
def production_aliment_pre_delete(sender, instance, **kwargs):
    """Reverse both the finished-feed credit and any ingredient debits.

    PMP is intentionally left untouched here — same tolerance as the rest
    of the module: un-mixing a weighted average retroactively isn't
    meaningful once later movements may already have used it.
    """
    from stock.models import StockIntrant, StockMouvement

    stock, _ = StockIntrant.objects.get_or_create(
        branche=instance.branche,
        intrant=instance.intrant_produit,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )
    avant = stock.quantite
    stock.quantite = stock.quantite - instance.quantite_produite_kg
    stock.save(update_fields=["quantite"])

    StockMouvement.objects.create(
        branche=instance.branche,
        intrant=instance.intrant_produit,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source="production_aliment",
        quantite=instance.quantite_produite_kg,
        quantite_avant=avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=(
            f"Annulation production aliment pk={instance.pk} (suppression)"
        )[:100],
        created_by=instance.created_by,
    )

    if instance.formule_id:
        for ligne in instance.formule.lignes.select_related("intrant").all():
            qte_ingredient = (
                instance.quantite_produite_kg / Decimal("100")
            ) * ligne.proportion_kg
            if qte_ingredient == 0:
                continue
            ing_stock, _ = StockIntrant.objects.get_or_create(
                branche=instance.branche,
                intrant=ligne.intrant,
                defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
            )
            avant_i = ing_stock.quantite
            ing_stock.quantite = ing_stock.quantite + qte_ingredient
            ing_stock.save(update_fields=["quantite"])

            StockMouvement.objects.create(
                branche=instance.branche,
                intrant=ligne.intrant,
                type_mouvement=StockMouvement.TYPE_ENTREE,
                source="production_aliment",
                quantite=qte_ingredient,
                quantite_avant=avant_i,
                quantite_apres=ing_stock.quantite,
                date_mouvement=instance.date,
                reference_id=instance.pk,
                reference_label=f"Annulation mélange pk={instance.pk} (suppression)",
                created_by=instance.created_by,
            )


# ---------------------------------------------------------------------------
# RetraitOeufs: debit the egg StockProduitFini on save / restore on delete
# (mirror image of recolte_oeufs_post_save above)
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=RetraitOeufs)
def retrait_oeufs_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            instance._old_quantite = RetraitOeufs.objects.values_list(
                "quantite_oeufs", flat=True
            ).get(pk=instance.pk)
        except RetraitOeufs.DoesNotExist:
            instance._old_quantite = None
    else:
        instance._old_quantite = None


@receiver(post_save, sender=RetraitOeufs)
def retrait_oeufs_post_save(sender, instance, created, **kwargs):
    """Decrease StockProduitFini (œufs) by the (signed) delta, scoped to branche.

    Skipped entirely when `bl_genere_id` is set: the withdrawal was turned
    into a formal BLClient sale (see views.retrait_oeufs_create), and that
    BL's own BLClientLigne signal already debits the same stock — debiting
    here too would deduct the eggs twice.
    """
    from stock.models import StockProduitFini, StockMouvement

    if instance.bl_genere_id:
        return

    produit = _get_produit_oeufs()
    if not produit:
        return

    old_qty = getattr(instance, "_old_quantite", None)
    delta = (
        instance.quantite_oeufs
        if created
        else (instance.quantite_oeufs - old_qty if old_qty is not None else 0)
    )
    if delta == 0:
        return

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=instance.branche,
        produit_fini=produit,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - Decimal(str(delta))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=instance.branche,
        produit_fini=produit,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source="retrait_oeufs",
        quantite=abs(Decimal(str(delta))),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=f"Retrait œufs ({instance.get_motif_display()}) — {instance.date}",
        created_by=instance.created_by,
    )


@receiver(pre_delete, sender=RetraitOeufs)
def retrait_oeufs_pre_delete(sender, instance, **kwargs):
    """Restore StockProduitFini (œufs) when a RetraitOeufs is deleted.

    Skipped when `bl_genere_id` is set — see retrait_oeufs_post_save.
    """
    from stock.models import StockProduitFini, StockMouvement

    if instance.bl_genere_id:
        return

    produit = _get_produit_oeufs()
    if not produit:
        return

    stock, _ = StockProduitFini.objects.get_or_create(
        branche=instance.branche,
        produit_fini=produit,
        defaults={
            "quantite": Decimal("0"),
            "cout_moyen_production": Decimal("0"),
            "seuil_alerte": Decimal("0"),
        },
    )
    quantite_avant = stock.quantite
    stock.quantite = stock.quantite + Decimal(str(instance.quantite_oeufs))
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=instance.branche,
        produit_fini=produit,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source="retrait_oeufs",
        quantite=Decimal(str(instance.quantite_oeufs)),
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=f"Annulation retrait œufs pk={instance.pk} (suppression)",
        created_by=instance.created_by,
    )
