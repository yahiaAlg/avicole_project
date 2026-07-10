"""
clients/signals.py

Signals for the client AR (accounts-receivable) cycle.

Registered signals:
  1. pre_save   on BLClient        → cache old statut for transition detection.
  2. post_save  on BLClient        → when statut transitions to LIVRE, decrease
                                      StockProduitFini OR StockIntrant (scoped
                                      to bl.branche — v1.6/BR-BLC-06: a line
                                      may sell a surplus intrant instead of a
                                      finished product) for every BL line and
                                      create a StockMouvement (sortie /
                                      bl_client).
  3. pre_save   on FactureClient   → cache old statut / is_new flag.
  4. post_save  on FactureClient   → on creation: log only (BLs not linked yet).
  5. m2m_changed on FactureClient.bls.through →
       after BLs are linked (post_add) on a fresh invoice:
         a. (BR-FAC-01) compute montant_ht from BL line totals, derive
            montant_tva and montant_ttc, initialise reste_a_payer.
         b. (BR-FAC-02 / BR-BLC-03) lock all included BLs to STATUT_FACTURE.
  6. post_save  on LivraisonPartielle → on creation, decrease StockProduitFini
                                      (scoped to the parent abonnement's
                                      branche) for the parent abonnement's
                                      product and create a StockMouvement
                                      (sortie).
  7. pre_delete on LivraisonPartielle → reverse the stock decrease.

Root-cause fix (créances clients = 0 on dashboard):
  The view calls facture.save() then form.save_m2m().  The post_save signal
  on FactureClient fires BEFORE save_m2m() has linked the BLs, so iterating
  instance.bls.all() yields nothing and montant_ht is computed as 0.
  Moving the computation to m2m_changed / post_add guarantees the BLs are
  already in the join table when totals are derived.

Business rules enforced here:
  BR-BLC-01  Stock produits finis decreases ONLY on BL BROUILLON → LIVRE
             transition — never on re-saves of an already-Livré BL.
  BR-BLC-03  BLs are locked (STATUT_FACTURE) as soon as they are included in
             a FactureClient — the lock is set here in the m2m_changed signal.
  BR-FAC-01  montant_ht is computed from BL lines; never entered manually.
             montant_tva = montant_ht × taux_tva / 100 (rounded to 2 d.p.).
             montant_ttc = montant_ht + montant_tva.
             reste_a_payer is initialised to montant_ttc at creation.
  BR-BRA-01/07 (v1.4) : every BLClient/FactureClient/AbonnementClient belongs
             to exactly one Branche, and the StockProduitFini row debited is
             the one for THAT branche (stock is keyed by (branche, produit
             fini), not by produit fini alone). LivraisonPartielle has no
             stored branche — it inherits it from its parent abonnement.

Also exposes ``annuler_sortie_stock_bl_client`` (not a signal — a helper
called directly by clients.utils.supprimer_facture_client_cascade) which
reverses the stock decrease made for a BL Client when an admin hard-deletes
a FactureClient along with its BLs and paiements.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
import datetime

from django.db.models.signals import post_save, pre_save, m2m_changed, pre_delete
from django.dispatch import receiver

from clients.models import BLClient, FactureClient, LivraisonPartielle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal 1 — BLClient: cache old statut before save
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=BLClient)
def bl_client_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save statut on the instance so the post_save handler can
    detect a BROUILLON → LIVRE transition without an extra DB query.
    """
    if instance.pk:
        try:
            instance._old_statut = (
                BLClient.objects.filter(pk=instance.pk)
                .values_list("statut", flat=True)
                .get()
            )
        except BLClient.DoesNotExist:
            instance._old_statut = None
    else:
        instance._old_statut = None


# ---------------------------------------------------------------------------
# Signal 2 — BLClient: stock sortie on BROUILLON → LIVRE transition
# ---------------------------------------------------------------------------


@receiver(post_save, sender=BLClient)
def bl_client_post_save(sender, instance, created, **kwargs):
    """
    When a BLClient transitions to STATUT_LIVRE, process every ligne:
      - Decrease StockProduitFini.quantite
      - Create a StockMouvement of type SORTIE / source BL_CLIENT

    A BL already in LIVRE (or FACTURE) that is re-saved without a status
    change is a no-op — this prevents double-counting on incidental saves.

    BR-BLC-01: stock is reduced once and only once, on the Livré transition.
    """
    old_statut = getattr(instance, "_old_statut", None)
    is_transitioning_to_livre = (
        instance.statut == BLClient.STATUT_LIVRE and old_statut != BLClient.STATUT_LIVRE
    )

    if not is_transitioning_to_livre:
        return

    lignes = instance.lignes.select_related("produit_fini", "intrant").all()

    if not lignes.exists():
        logger.warning(
            "BLClient pk=%s transitioned to LIVRE but has no lignes. "
            "No stock entries created.",
            instance.pk,
        )
        return

    logger.info(
        "BLClient pk=%s (%s) transitioned to LIVRE. "
        "Processing %d ligne(s) for stock sortie.",
        instance.pk,
        instance.reference,
        lignes.count(),
    )

    for ligne in lignes:
        # v1.6 (BR-BLC-06) — a line sells either a produit_fini or a
        # surplus intrant; dispatch to the matching stock segment.
        if ligne.intrant_id:
            _appliquer_sortie_stock_intrant(
                ligne=ligne,
                date_bl=instance.date_bl,
                created_by=instance.created_by,
                reference_label=instance.reference,
                reference_id=instance.pk,
                branche=instance.branche,
            )
        else:
            _appliquer_sortie_stock_produit_fini(
                ligne=ligne,
                date_bl=instance.date_bl,
                created_by=instance.created_by,
                reference_label=instance.reference,
                reference_id=instance.pk,
                branche=instance.branche,
            )


def annuler_sortie_stock_bl_client(instance):
    """
    ADMIN-ONLY reversal counterpart to ``_appliquer_sortie_stock_produit_fini``
    / ``_appliquer_sortie_stock_intrant``.

    Increases the matching stock balance (StockProduitFini or StockIntrant)
    back up and logs a corrective StockMouvement (ENTREE) for every ligne of
    a BL Client that previously triggered a stock decrease (i.e. was
    LIVRE/FACTURE). Called exclusively by
    ``clients.utils.supprimer_facture_client_cascade`` right before the BL
    itself is deleted, as part of an admin-triggered hard delete of a
    FactureClient (and everything it created).

    Mirrors the existing ``livraison_partielle_pre_delete`` reversal pattern
    (same direction of correction, same source category kept for audit
    continuity).
    """
    from stock.models import StockProduitFini, StockIntrant, StockMouvement

    lignes = instance.lignes.select_related("produit_fini", "intrant").all()

    for ligne in lignes:
        if ligne.intrant_id:
            intrant = ligne.intrant

            stock, _ = StockIntrant.objects.get_or_create(
                branche=instance.branche,
                intrant=intrant,
                defaults={
                    "quantite": Decimal("0"),
                    "prix_moyen_pondere": Decimal("0"),
                },
            )

            quantite_avant = stock.quantite
            stock.quantite = stock.quantite + ligne.quantite
            stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

            StockMouvement.objects.create(
                branche=instance.branche,
                intrant=intrant,
                type_mouvement=StockMouvement.TYPE_ENTREE,
                source=StockMouvement.SOURCE_BL_CLIENT,
                quantite=ligne.quantite,
                quantite_avant=quantite_avant,
                quantite_apres=stock.quantite,
                date_mouvement=datetime.date.today(),
                reference_id=instance.pk,
                reference_label=f"Annulation {instance.reference} (suppression admin)",
                created_by=None,
            )

            logger.info(
                "Stock reversal (admin delete): intrant pk=%s +%s → %s "
                "(branche=%s). BL Client %s.",
                intrant.pk,
                ligne.quantite,
                stock.quantite,
                instance.branche.code,
                instance.reference,
            )
            continue

        produit_fini = ligne.produit_fini

        stock, _ = StockProduitFini.objects.get_or_create(
            branche=instance.branche,
            produit_fini=produit_fini,
            defaults={
                "quantite": Decimal("0"),
                "cout_moyen_production": Decimal("0"),
                "seuil_alerte": Decimal("0"),
            },
        )

        quantite_avant = stock.quantite
        stock.quantite = stock.quantite + ligne.quantite
        stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

        StockMouvement.objects.create(
            branche=instance.branche,
            produit_fini=produit_fini,
            type_mouvement=StockMouvement.TYPE_ENTREE,
            source=StockMouvement.SOURCE_BL_CLIENT,
            quantite=ligne.quantite,
            quantite_avant=quantite_avant,
            quantite_apres=stock.quantite,
            date_mouvement=datetime.date.today(),
            reference_id=instance.pk,
            reference_label=f"Annulation {instance.reference} (suppression admin)",
            created_by=None,
        )

        logger.info(
            "Stock reversal (admin delete): produit_fini pk=%s +%s → %s "
            "(branche=%s). BL Client %s.",
            produit_fini.pk,
            ligne.quantite,
            stock.quantite,
            instance.branche.code,
            instance.reference,
        )


def _appliquer_sortie_stock_produit_fini(
    ligne, date_bl, created_by, reference_label, reference_id, branche
):
    """
    Decrease StockProduitFini.quantite for one BLClientLigne, scoped to
    `branche` (BR-BRA-07 — stock is keyed by (branche, produit_fini)), and
    record a StockMouvement (SORTIE / BL_CLIENT) in the same branche.

    A negative balance is allowed at the model level (physical discrepancy);
    a warning is logged so operators can reconcile via StockAjustement.
    """
    from stock.models import StockProduitFini, StockMouvement

    produit_fini = ligne.produit_fini

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
    stock.quantite = stock.quantite - ligne.quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après BL Client: produit_fini pk=%s quantite=%s (branche=%s). "
            "Vérifiez les entrées ou créez un ajustement.",
            produit_fini.pk,
            stock.quantite,
            branche.code,
        )

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit_fini,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_BL_CLIENT,
        quantite=ligne.quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date_bl,
        reference_id=reference_id,
        reference_label=reference_label,
        created_by=created_by,
    )

    logger.debug(
        "Stock sortie (BL Client): produit_fini pk=%s -%s → %s (branche=%s). BL %s.",
        produit_fini.pk,
        ligne.quantite,
        stock.quantite,
        branche.code,
        reference_label,
    )


def _appliquer_sortie_stock_intrant(
    ligne, date_bl, created_by, reference_label, reference_id, branche
):
    """
    Decrease StockIntrant.quantite for one BLClientLigne that sells a
    surplus intrant (BR-BLC-06), scoped to `branche`, and record a
    StockMouvement (SORTIE / BL_CLIENT) in the same branche.

    Mirrors ``_appliquer_sortie_stock_produit_fini`` exactly, but on the
    StockIntrant segment. A negative balance is allowed at the model level
    (physical discrepancy); a warning is logged so operators can reconcile
    via StockAjustement.
    """
    from stock.models import StockIntrant, StockMouvement

    intrant = ligne.intrant

    stock, _ = StockIntrant.objects.get_or_create(
        branche=branche,
        intrant=intrant,
        defaults={
            "quantite": Decimal("0"),
            "prix_moyen_pondere": Decimal("0"),
        },
    )

    quantite_avant = stock.quantite
    stock.quantite = stock.quantite - ligne.quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après BL Client (intrant vendu): intrant pk=%s quantite=%s "
            "(branche=%s). Vérifiez les entrées ou créez un ajustement.",
            intrant.pk,
            stock.quantite,
            branche.code,
        )

    StockMouvement.objects.create(
        branche=branche,
        intrant=intrant,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_BL_CLIENT,
        quantite=ligne.quantite,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=date_bl,
        reference_id=reference_id,
        reference_label=reference_label,
        created_by=created_by,
    )

    logger.debug(
        "Stock sortie (BL Client, intrant vendu): intrant pk=%s -%s → %s (branche=%s). BL %s.",
        intrant.pk,
        ligne.quantite,
        stock.quantite,
        branche.code,
        reference_label,
    )


# ---------------------------------------------------------------------------
# Signal 3 — FactureClient: cache old statut / is_new
# ---------------------------------------------------------------------------


@receiver(pre_save, sender=FactureClient)
def facture_client_pre_save(sender, instance, **kwargs):
    """
    Cache the pre-save state for transition detection in the post_save handler.
    """
    if instance.pk:
        try:
            db_instance = FactureClient.objects.get(pk=instance.pk)
            instance._old_statut = db_instance.statut
            instance._is_new = False
        except FactureClient.DoesNotExist:
            instance._old_statut = None
            instance._is_new = True
    else:
        instance._old_statut = None
        instance._is_new = True


# ---------------------------------------------------------------------------
# Signal 4 — FactureClient: log on creation (totals deferred to m2m_changed)
# ---------------------------------------------------------------------------


@receiver(post_save, sender=FactureClient)
def facture_client_post_save(sender, instance, created, **kwargs):
    """
    On FactureClient creation, log that the record exists.

    NOTE: Do NOT compute montant_ht here.  The view calls facture.save()
    first, then form.save_m2m() — so at this point the BLs M2M join table
    is still empty.  Summing BL lines here always yields 0 (root cause of
    the "créances clients = 0" dashboard bug).

    Financial totals and BL locking are handled in facture_client_bls_changed
    (m2m_changed / post_add), which fires AFTER save_m2m() has written the
    join table rows.
    """
    if not created:
        return

    logger.info(
        "FactureClient pk=%s created (awaiting BL M2M link — "
        "totals will be computed in m2m_changed).",
        instance.pk,
    )


# ---------------------------------------------------------------------------
# Signal 5 — FactureClient.bls M2M: compute totals and lock BLs after link
# ---------------------------------------------------------------------------


@receiver(m2m_changed, sender=FactureClient.bls.through)
def facture_client_bls_changed(sender, instance, action, **kwargs):
    """
    Fires after Django writes the BL join-table rows (action == 'post_add').

    For a freshly-created invoice (montant_ttc == 0 in the DB), this handler:

      1. (BR-FAC-01) Computes montant_ht as the sum of all BLClientLigne
         totals for every BL now linked to this invoice.
         Derives:
           montant_tva = montant_ht × taux_tva / 100  (rounded 2 d.p.)
           montant_ttc = montant_ht + montant_tva
         Initialises reste_a_payer = montant_ttc.
         Persists via a direct UPDATE (update_fields) to avoid re-triggering
         this signal.

      2. (BR-FAC-02 / BR-BLC-03) Transitions all linked BLs to STATUT_FACTURE
         so they are locked against further modification or re-invoicing.

    On subsequent M2M changes (e.g. admin edits) the check
    `db_instance.montant_ttc != 0` makes this a no-op, preserving immutability
    of the computed totals.
    """
    if action != "post_add":
        return

    # Re-fetch from DB: instance may carry stale in-memory state.
    try:
        db_instance = FactureClient.objects.get(pk=instance.pk)
    except FactureClient.DoesNotExist:
        return

    # Only run for fresh invoices where totals have not yet been computed.
    if db_instance.montant_ttc != 0:
        logger.debug(
            "facture_client_bls_changed: pk=%s montant_ttc=%s — skipping (already computed).",
            db_instance.pk,
            db_instance.montant_ttc,
        )
        return

    # BR-BRA-01 (defensive): every linked BL must belong to the same branche
    # as the facture. Primary enforcement is at the view/form layer (the BL
    # queryset offered to the user is filtered to the facture's branche);
    # this is a last-resort audit log, not a block, since the M2M rows are
    # already written by the time this signal fires.
    bls_branche_differente = db_instance.bls.exclude(branche_id=db_instance.branche_id)
    if bls_branche_differente.exists():
        logger.error(
            "BR-BRA-01 VIOLATION: FactureClient pk=%s (branche=%s) a été liée "
            "à %d BL(s) d'une autre branche : %s.",
            db_instance.pk,
            db_instance.branche_id,
            bls_branche_differente.count(),
            list(bls_branche_differente.values_list("reference", flat=True)),
        )

    # ── BR-FAC-01: derive financial totals from BL lines ─────────────────
    montant_ht = Decimal("0")
    for bl in db_instance.bls.prefetch_related("lignes").all():
        for ligne in bl.lignes.all():
            montant_ht += ligne.montant_total

    taux_tva = Decimal(str(db_instance.taux_tva or "0"))
    montant_tva = (montant_ht * taux_tva / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    montant_ttc = montant_ht + montant_tva

    # Persist derived totals; update_fields prevents re-triggering this signal.
    FactureClient.objects.filter(pk=db_instance.pk).update(
        montant_ht=montant_ht,
        montant_tva=montant_tva,
        montant_ttc=montant_ttc,
        reste_a_payer=montant_ttc,
    )
    # Keep the in-memory instance in sync with the UPDATE above so the
    # prepayment consumption step below (and recalculer_solde inside it)
    # operates on the current, not stale, figures.
    db_instance.montant_ht = montant_ht
    db_instance.montant_tva = montant_tva
    db_instance.montant_ttc = montant_ttc
    db_instance.reste_a_payer = montant_ttc
    db_instance.montant_regle = Decimal("0")

    logger.info(
        "FactureClient pk=%s (BLS linked): "
        "montant_ht=%s DZD, TVA=%s%% → TTC=%s DZD.",
        db_instance.pk,
        montant_ht,
        taux_tva,
        montant_ttc,
    )

    # ── BR-FAC-02 / BR-BLC-03: lock all included BLs ─────────────────────
    locked_count = db_instance.bls.exclude(statut=BLClient.STATUT_FACTURE).update(
        statut=BLClient.STATUT_FACTURE
    )

    if locked_count:
        logger.info(
            "FactureClient pk=%s: locked %d BL(s) to STATUT_FACTURE.",
            db_instance.pk,
            locked_count,
        )

    # Prepayment consumption: if this client is holding unused advances
    # (AcompteClient, created from a prior payment's unallocated surplus)
    # in this same branche, consume them against this brand-new invoice
    # automatically, oldest advance first — mirrors achats' BR-REG-07 so a
    # client who pre-pays doesn't need a fresh manual allocation for every
    # subsequent facture.
    from clients.utils import consommer_acomptes_client_fifo

    consommer_acomptes_client_fifo(db_instance)


# ---------------------------------------------------------------------------
# Signal 6/7 — LivraisonPartielle: stock sortie on creation, reversal on delete
# ---------------------------------------------------------------------------


@receiver(post_save, sender=LivraisonPartielle)
def livraison_partielle_post_save(sender, instance, created, **kwargs):
    """
    Decrease StockProduitFini and log a StockMouvement (SORTIE, source=
    LIVRAISON_ABONNEMENT) when a LivraisonPartielle is created. Records are
    immutable after creation (mirrors PaiementClientAllocation) — only the
    create path touches stock.

    v1.4 (BR-BRA-07): LivraisonPartielle has no stored `branche` — it
    inherits the abonnement's branche (instance.branche property), and that
    is the branche whose StockProduitFini row is debited.
    """
    if not created:
        return

    # v1.7 — quantite_livree is optional under mode_facturation=forfait
    # (purely informational). No quantity means no stock movement to record.
    if instance.quantite_livree is None:
        return

    from stock.models import StockProduitFini, StockMouvement

    produit_fini = instance.abonnement.produit_fini
    branche = instance.abonnement.branche

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
    stock.quantite = stock.quantite - instance.quantite_livree
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après LivraisonPartielle: produit_fini pk=%s quantite=%s "
            "(branche=%s). Vérifiez les entrées ou créez un ajustement.",
            produit_fini.pk,
            stock.quantite,
            branche.code,
        )

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit_fini,
        type_mouvement=StockMouvement.TYPE_SORTIE,
        source=StockMouvement.SOURCE_LIVRAISON_ABONNEMENT,
        quantite=instance.quantite_livree,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=(
            f"Livraison abonnement — {instance.abonnement.client.nom} ({instance.date})"
        ),
        created_by=instance.created_by,
    )

    logger.info(
        "LivraisonPartielle pk=%s: -%s %s → %s (abonnement pk=%s, branche=%s).",
        instance.pk,
        instance.quantite_livree,
        produit_fini.unite_mesure,
        stock.quantite,
        instance.abonnement_id,
        branche.code,
    )


@receiver(pre_delete, sender=LivraisonPartielle)
def livraison_partielle_pre_delete(sender, instance, **kwargs):
    """Reverse the stock decrease when a LivraisonPartielle is deleted."""
    if instance.quantite_livree is None:
        return

    from stock.models import StockProduitFini, StockMouvement

    produit_fini = instance.abonnement.produit_fini
    branche = instance.abonnement.branche

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
    stock.quantite = stock.quantite + instance.quantite_livree
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    StockMouvement.objects.create(
        branche=branche,
        produit_fini=produit_fini,
        type_mouvement=StockMouvement.TYPE_ENTREE,
        source=StockMouvement.SOURCE_LIVRAISON_ABONNEMENT,
        quantite=instance.quantite_livree,
        quantite_avant=quantite_avant,
        quantite_apres=stock.quantite,
        date_mouvement=instance.date,
        reference_id=instance.pk,
        reference_label=f"Annulation livraison abonnement pk={instance.pk} (suppression)",
        created_by=instance.created_by,
    )

    logger.debug(
        "Stock entrée (annulation livraison abonnement): produit_fini pk=%s +%s → %s (branche=%s).",
        produit_fini.pk,
        instance.quantite_livree,
        stock.quantite,
        branche.code,
    )
