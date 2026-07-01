"""
achats/signals.py

Signals for the supplier procurement cycle (achats app).

Registered signals:
  1. pre_save  on BLFournisseur   → cache old statut on instance before save.
  2. post_save on BLFournisseur   → when statut transitions to RECU, create
                                     stock entries (StockIntrant ↑, PMP update,
                                     StockMouvement created) for every BL line,
                                     scoped to bl.branche.
  3. pre_save  on FactureFournisseur → mark BL lines as FACTURE and lock them
                                       (BR-BLF-02 / BR-FAF-03) when the invoice
                                       transitions to a saved state.
  4. post_save on ReglementFournisseur → run FIFO allocation engine
                                         (achats.utils.appliquer_reglement_fifo),
                                         which only allocates across factures
                                         in the same branche (BR-BRA-01).

Business rules enforced here:
  BR-BLF-02 : A BL whose statut is FACTURE is locked (est_verrouille = True).
  BR-FAF-01 : montant_total is computed from BL lines — never entered manually.
  BR-REG-03 : FIFO payment allocation across open invoices.
  BR-REG-04 : Surplus beyond total debt → AcompteFournisseur.
  BR-REG-06 : Règlements and their allocations are immutable after creation.
  BR-BRA-01 (v1.4) : every BLFournisseur/FactureFournisseur/ReglementFournisseur
             belongs to exactly one Branche, and the StockIntrant row credited
             on RECU is the one for THAT branche (BR-BRA-07: stock is keyed
             by (branche, intrant), not by intrant alone).
"""

import logging
import datetime
from decimal import Decimal

from django.db.models.signals import post_save, pre_save, m2m_changed
from django.dispatch import receiver

from achats.models import BLFournisseur, FactureFournisseur, ReglementFournisseur

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: stock entry for one BL line
# ---------------------------------------------------------------------------


def _enregistrer_entree_stock_ligne(
    ligne, date_bl, created_by, reference_label, reference_id, branche
):
    """
    Increase StockIntrant balance and recompute PMP for a single BL line,
    scoped to `branche` (BR-BRA-07 — stock is keyed by (branche, intrant)).
    Create a StockMouvement (entree) for audit, also scoped to `branche`.

    Called exclusively when a BL transitions to STATUT_RECU.
    """
    from stock.models import StockIntrant, StockMouvement
    from achats.utils import calculer_pmp

    intrant = ligne.intrant

    # Ensure a stock record exists for this branche (should always exist via
    # the intrants/core bootstrap signals, but guard defensively).
    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={"quantite": Decimal("0"), "prix_moyen_pondere": Decimal("0")},
    )

    quantite_avant = stock.quantite

    # Recalculate weighted-average cost before updating quantity.
    nouveau_pmp = calculer_pmp(
        quantite_ancienne=stock.quantite,
        pmp_ancien=stock.prix_moyen_pondere,
        quantite_entree=ligne.quantite,
        prix_unitaire=ligne.prix_unitaire,
    )

    stock.quantite = stock.quantite + ligne.quantite
    stock.prix_moyen_pondere = nouveau_pmp
    stock.save(update_fields=["quantite", "prix_moyen_pondere", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_BL_FOURNISSEUR,
        quantite=ligne.quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date_bl,
        reference_id=reference_id,
        reference_label=reference_label,
        created_by=created_by,
    )

    logger.debug(
        "Stock entry: intrant pk=%s +%s → %s (PMP: %s DZD, branche=%s). BL %s.",
        intrant.pk,
        ligne.quantite,
        stock.quantite,
        nouveau_pmp,
        branche.code,
        reference_label,
    )


# ---------------------------------------------------------------------------
# Signal 1 & 2 — BLFournisseur: cache old status, trigger stock on RECU
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=BLFournisseur)
def bl_fournisseur_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save statut on the instance so the post_save handler can
    detect a statut transition without an extra DB query.
    """
    if instance.pk:
        try:
            instance._old_statut = (
                BLFournisseur.objects.filter(pk=instance.pk)
                .values_list("statut", flat=True)
                .get()
            )
        except BLFournisseur.DoesNotExist:
            instance._old_statut = None
    else:
        instance._old_statut = None


def traiter_entrees_stock_bl(instance):
    """
    Process stock entries for all lines of a BLFournisseur that is in STATUT_RECU.

    This is the shared implementation called both by the post_save signal
    (statut-change path) and directly from the create/edit views (where lines
    are saved *after* the BL header, so the signal fires too early).

    It is idempotent-safe when called from the view immediately after formset
    save, because the signal guard (``_stock_already_processed``) prevents the
    signal handler from running a second time for the same save cycle.

    BR-BLF-05 (secondary guard): if an autorisation_acces BL somehow reaches
    this point with an expired date, stock entry is blocked and an error is
    logged.  The form's clean() is the primary enforcement layer.
    """
    # BR-BLF-05: defensive guard — form validation should catch this first.
    if (
        instance.type_document == BLFournisseur.TYPE_AUTORISATION_ACCES
        and instance.date_expiration_autorisation
        and instance.date_expiration_autorisation < datetime.date.today()
    ):
        logger.error(
            "BR-BLF-05 BLOCKED: BLFournisseur pk=%s (%s) is an expired "
            "autorisation_acces (expired %s). Stock entry skipped.",
            instance.pk,
            instance.reference,
            instance.date_expiration_autorisation,
        )
        return

    # Log the AUTORISE → RECU pickup confirmation for audit trail.
    old_statut = getattr(instance, "_old_statut", None)
    if (
        instance.type_document == BLFournisseur.TYPE_AUTORISATION_ACCES
        and old_statut == BLFournisseur.STATUT_AUTORISE
    ):
        logger.info(
            "AutorisationAcces pk=%s (%s): goods confirmed picked up "
            "(AUTORISE → RECU). Processing stock entry.",
            instance.pk,
            instance.reference,
        )

    lignes = instance.lignes.select_related("intrant").all()

    if not lignes.exists():
        logger.warning(
            "BLFournisseur pk=%s transitioned to RECU but has no lignes. "
            "No stock entries created.",
            instance.pk,
        )
        return

    logger.info(
        "BLFournisseur pk=%s (%s) processing %d ligne(s) for stock entry.",
        instance.pk,
        instance.reference,
        lignes.count(),
    )

    for ligne in lignes:
        _enregistrer_entree_stock_ligne(
            ligne=ligne,
            date_bl=instance.date_bl,
            created_by=instance.created_by,
            reference_label=instance.reference,
            reference_id=instance.pk,
            branche=instance.branche,
        )

    # Mark so the post_save signal skips duplicate processing in the same cycle.
    instance._stock_already_processed = True


@receiver(post_save, sender=BLFournisseur)
def bl_fournisseur_post_save(sender, instance, created, **kwargs):
    """
    When a BLFournisseur transitions to STATUT_RECU, process every ligne:
      - Increase StockIntrant.quantite
      - Recompute StockIntrant.prix_moyen_pondere (PMP / weighted average cost)
      - Create a StockMouvement of type ENTREE

    NOTE: When a BL is *created* with statut=RECU via the create view, lines
    are saved by the formset *after* this signal fires, so ``instance.lignes``
    is empty here.  The view calls ``traiter_entrees_stock_bl`` explicitly
    after formset.save() for that case.  The ``_stock_already_processed`` flag
    prevents double-counting when both paths are active.

    A BL already in RECU (or FACTURE) that is saved again without a status
    change is ignored — this prevents double-counting on incidental saves.
    """
    # Skip if the view already processed stock entries after formset save.
    if getattr(instance, "_stock_already_processed", False):
        return

    old_statut = getattr(instance, "_old_statut", None)
    is_transitioning_to_recu = (
        instance.statut == BLFournisseur.STATUT_RECU
        and old_statut not in (BLFournisseur.STATUT_RECU, BLFournisseur.STATUT_FACTURE)
    )

    if not is_transitioning_to_recu:
        return

    traiter_entrees_stock_bl(instance)


# ---------------------------------------------------------------------------
# Signal 3 — FactureFournisseur: compute montant_total, lock BLs (BR-FAF-01,
#            BR-FAF-03, BR-BLF-02)
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=FactureFournisseur)
def facture_fournisseur_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save state for transition detection.
    """
    if instance.pk:
        try:
            db_instance = FactureFournisseur.objects.get(pk=instance.pk)
            instance._old_statut = db_instance.statut
            instance._is_new = False
        except FactureFournisseur.DoesNotExist:
            instance._old_statut = None
            instance._is_new = True
    else:
        instance._old_statut = None
        instance._is_new = True


@receiver(post_save, sender=FactureFournisseur)
def facture_fournisseur_post_save(sender, instance, created, **kwargs):
    """
    On creation, mark the instance so the m2m_changed handler knows to act.
    NOTE: Do NOT compute montant_total here — Django saves M2M relations
    *after* post_save, so instance.bls.all() would be empty at this point.
    All BL-dependent work is handled in facture_fournisseur_bls_changed.
    """
    if not created:
        return
    instance._just_created = True
    logger.info(
        "FactureFournisseur pk=%s created. Awaiting M2M BL linkage.",
        instance.pk,
    )


@receiver(m2m_changed, sender=FactureFournisseur.bls.through)
def facture_fournisseur_bls_changed(sender, instance, action, pk_set, **kwargs):
    """
    Fires after BLs are attached to a FactureFournisseur via M2M.

    BR-FAF-01 : compute montant_total from BL lines (now they are linked).
    BR-FAF-03 / BR-BLF-02 : lock all included BLs to STATUT_FACTURE.
    BR-REG initialise reste_a_payer = montant_total.

    Guard: only act when the facture's montant_total is still 0 (i.e. this
    is the first M2M population after creation — not a later admin edit).
    """
    if action != "post_add" or not pk_set:
        return
    if not isinstance(instance, FactureFournisseur):
        return

    # Only process freshly-created factures (montant_total == 0 in DB).
    db_row = (
        FactureFournisseur.objects.filter(pk=instance.pk)
        .values("montant_total")
        .first()
    )
    if not db_row or db_row["montant_total"] != Decimal("0"):
        return  # Already computed — skip.

    # BR-BRA-01 (defensive): every linked BL must belong to the same branche
    # as the facture. Primary enforcement is at the view/form layer (the BL
    # queryset offered to the user is filtered to the facture's branche);
    # this is a last-resort audit log, not a block, since the M2M rows are
    # already written by the time this signal fires.
    bls_branche_differente = instance.bls.exclude(branche_id=instance.branche_id)
    if bls_branche_differente.exists():
        logger.error(
            "BR-BRA-01 VIOLATION: FactureFournisseur pk=%s (branche=%s) a été "
            "liée à %d BL(s) d'une autre branche : %s.",
            instance.pk,
            instance.branche_id,
            bls_branche_differente.count(),
            list(bls_branche_differente.values_list("reference", flat=True)),
        )

    # BR-FAF-01: derive montant_total from BL lines.
    montant_total = Decimal("0")
    for bl in instance.bls.prefetch_related("lignes").all():
        for ligne in bl.lignes.all():
            montant_total += ligne.montant_total

    # Persist derived totals via UPDATE (avoids re-triggering post_save).
    FactureFournisseur.objects.filter(pk=instance.pk).update(
        montant_total=montant_total,
        reste_a_payer=montant_total,
    )
    logger.info(
        "FactureFournisseur pk=%s: BLs linked via M2M. "
        "montant_total computed: %s DZD.",
        instance.pk,
        montant_total,
    )

    # BR-FAF-03 / BR-BLF-02: lock all included BLs.
    locked_count = instance.bls.exclude(statut=BLFournisseur.STATUT_FACTURE).update(
        statut=BLFournisseur.STATUT_FACTURE
    )

    if locked_count:
        logger.info(
            "FactureFournisseur pk=%s: locked %d BL(s) to STATUT_FACTURE.",
            instance.pk,
            locked_count,
        )


# ---------------------------------------------------------------------------
# Signal 4 — ReglementFournisseur: FIFO allocation (BR-REG-03 / BR-REG-04)
# ---------------------------------------------------------------------------


@receiver(post_save, sender=ReglementFournisseur)
def reglement_fournisseur_post_save(sender, instance, created, **kwargs):
    """
    On creation of a new ReglementFournisseur, run the FIFO allocation engine.

    BR-REG-06: règlements are immutable — the signal only fires on `created`.
    The engine (achats.utils.appliquer_reglement_fifo):
      - Allocates the amount across open invoices (oldest first).
      - Creates AllocationReglement records.
      - Updates each facture's montant_regle, reste_a_payer, and statut.
      - Creates an AcompteFournisseur if any surplus remains (BR-REG-04).
    """
    if not created:
        return

    logger.info(
        "ReglementFournisseur pk=%s created (%s DZD for %s). "
        "Running FIFO allocation.",
        instance.pk,
        instance.montant,
        instance.fournisseur.nom,
    )

    from achats.utils import appliquer_reglement_fifo

    appliquer_reglement_fifo(instance)
