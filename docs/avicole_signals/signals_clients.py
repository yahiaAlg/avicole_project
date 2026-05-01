"""
clients/signals.py

Signals for the client AR (accounts-receivable) cycle.

Registered signals:
  1. pre_save  on BLClient        → cache old statut for transition detection.
  2. post_save on BLClient        → when statut transitions to LIVRE, decrease
                                     StockProduitFini for every BL line and
                                     create a StockMouvement (sortie / bl_client).
  3. pre_save  on FactureClient   → cache old statut / is_new flag.
  4. post_save on FactureClient   → on creation:
                                       a. (BR-FAC-01) compute montant_ht from
                                          BL line totals, derive montant_tva and
                                          montant_ttc, initialise reste_a_payer.
                                       b. (BR-FAC-02 / BR-BLC-03) lock all
                                          included BLs to STATUT_FACTURE.

Business rules enforced here:
  BR-BLC-01  Stock produits finis decreases ONLY on BL BROUILLON → LIVRE
             transition — never on re-saves of an already-Livré BL.
  BR-BLC-03  BLs are locked (STATUT_FACTURE) as soon as they are included in
             a FactureClient — the lock is set here in the post_save signal,
             not by the user.
  BR-FAC-01  montant_ht is computed from BL lines; never entered manually.
             montant_tva = montant_ht × taux_tva / 100 (rounded to 2 d.p.).
             montant_ttc = montant_ht + montant_tva.
             reste_a_payer is initialised to montant_ttc at creation.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from clients.models import BLClient, FactureClient

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
        instance.statut == BLClient.STATUT_LIVRE
        and old_statut != BLClient.STATUT_LIVRE
    )

    if not is_transitioning_to_livre:
        return

    lignes = instance.lignes.select_related("produit_fini").all()

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
        _appliquer_sortie_stock_produit_fini(
            ligne=ligne,
            date_bl=instance.date_bl,
            created_by=instance.created_by,
            reference_label=instance.reference,
            reference_id=instance.pk,
        )


def _appliquer_sortie_stock_produit_fini(
    ligne, date_bl, created_by, reference_label, reference_id
):
    """
    Decrease StockProduitFini.quantite for one BLClientLigne and record a
    StockMouvement (SORTIE / BL_CLIENT).

    A negative balance is allowed at the model level (physical discrepancy);
    a warning is logged so operators can reconcile via StockAjustement.
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
    stock.quantite = stock.quantite - ligne.quantite
    stock.save(update_fields=["quantite", "derniere_mise_a_jour"])

    if stock.quantite < 0:
        logger.warning(
            "Stock négatif après BL Client: produit_fini pk=%s quantite=%s. "
            "Vérifiez les entrées ou créez un ajustement.",
            produit_fini.pk,
            stock.quantite,
        )

    StockMouvement.objects.create(
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
        "Stock sortie (BL Client): produit_fini pk=%s -%s → %s. BL %s.",
        produit_fini.pk,
        ligne.quantite,
        stock.quantite,
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
# Signal 4 — FactureClient: compute totals, lock BLs on creation
# ---------------------------------------------------------------------------

@receiver(post_save, sender=FactureClient)
def facture_client_post_save(sender, instance, created, **kwargs):
    """
    On first creation of a FactureClient:

      1. (BR-FAC-01) Compute montant_ht as the sum of all BLClientLigne
         totals for every BL included in this invoice.
         Derive:
           montant_tva = montant_ht × taux_tva / 100  (rounded to 2 d.p.)
           montant_ttc = montant_ht + montant_tva
         Initialise reste_a_payer = montant_ttc.
         Persist via a direct UPDATE (update_fields) to avoid re-triggering
         this signal.

      2. (BR-FAC-02 / BR-BLC-03) Transition all linked BLs to STATUT_FACTURE
         so they are locked against further modification or re-invoicing.

    On subsequent saves this handler is a no-op; montant_ht is immutable
    (also enforced in FactureClient.clean()).
    """
    if not created:
        return

    # ── BR-FAC-01: derive financial totals from BL lines ─────────────────
    montant_ht = Decimal("0")
    for bl in instance.bls.prefetch_related("lignes").all():
        for ligne in bl.lignes.all():
            montant_ht += ligne.montant_total

    taux_tva = Decimal(str(instance.taux_tva or "0"))
    montant_tva = (montant_ht * taux_tva / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    montant_ttc = montant_ht + montant_tva

    # Persist derived totals; update_fields prevents re-triggering this signal.
    FactureClient.objects.filter(pk=instance.pk).update(
        montant_ht=montant_ht,
        montant_tva=montant_tva,
        montant_ttc=montant_ttc,
        reste_a_payer=montant_ttc,
    )

    logger.info(
        "FactureClient pk=%s created. "
        "montant_ht=%s DZD, TVA=%s%% → TTC=%s DZD.",
        instance.pk,
        montant_ht,
        taux_tva,
        montant_ttc,
    )

    # ── BR-FAC-02 / BR-BLC-03: lock all included BLs ─────────────────────
    locked_count = instance.bls.exclude(
        statut=BLClient.STATUT_FACTURE
    ).update(statut=BLClient.STATUT_FACTURE)

    if locked_count:
        logger.info(
            "FactureClient pk=%s: locked %d BL(s) to STATUT_FACTURE.",
            instance.pk,
            locked_count,
        )
