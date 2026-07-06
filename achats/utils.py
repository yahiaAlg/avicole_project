"""
achats/utils.py

Business-logic helpers for the supplier procurement cycle.

  appliquer_reglement_fifo   — FIFO allocation engine (BR-REG-03 / BR-REG-04)
  consommer_acomptes_fifo    — Prepayment consumption engine (BR-REG-07)
  supprimer_facture_fournisseur_cascade  — Admin hard delete of a facture
  supprimer_reglement_fournisseur_cascade — Admin hard delete of a règlement
                                            (BR-REG-06 override)
  calculer_pmp               — Weighted-average cost (Prix Moyen Pondéré)
  generer_reference_bl_fournisseur    — Sequential BLF reference
  generer_reference_facture_fournisseur — Sequential FRN reference
  get_supplier_aging_buckets — Aged debt analysis for reporting
  get_fournisseur_solde      — Full financial summary for one supplier

v1.4 — Multi-Branch Architecture (§3.5): Fournisseur stays global (BR-BRA-06),
but BLFournisseur / FactureFournisseur / ReglementFournisseur / AcompteFournisseur
are branch-scoped (BR-BRA-01). The FIFO engine now only allocates a règlement
across factures in its OWN branche — a payment recorded in one branch can
never silently settle another branch's debt, even for the same supplier
(BR-BRA-01, mirrored by AllocationReglement.clean()). Reporting helpers below
take an optional `branche` (Vue par Branche when given, Vue Globale — summed
across all branches — when omitted, per §3.5.5).
"""

from decimal import Decimal
import datetime
import logging
from django.db import models as django_models

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FIFO Payment Allocation  (BR-REG-03 / BR-REG-04)
# ---------------------------------------------------------------------------


def appliquer_reglement_fifo(reglement):
    """
    Distribute *reglement.montant* across open factures fournisseur for the
    same supplier **within the same branche** (BR-BRA-01) using FIFO
    (oldest invoice date first).

    Steps:
      1. Fetch all Non-Payé / Partiellement-Payé invoices for this supplier
         AND this règlement's branche, ordered by date_facture ASC, then pk
         ASC (tie-break — BR-REG-02). A supplier owed by two branches has
         two fully independent FIFO queues, even though it is the same
         Fournisseur record (§3.5.3 ¶4) — a règlement recorded in one
         branch must never reach another branch's open invoices.
      2. Allocate the payment amount invoice by invoice until exhausted.
      3. For each allocation, create an AllocationReglement record and call
         facture.recalculer_solde() to update the invoice balance + status.
      4. If surplus remains after all of THIS branche's invoices are
         cleared, create an AcompteFournisseur (BR-REG-04) — it auto-syncs
         its own `branche` from `reglement.branche` in its save() override,
         so it never needs to be passed explicitly here.

    Called inside a post_save signal; assumes the surrounding DB transaction.

    Args:
        reglement (ReglementFournisseur): The freshly-created payment record.
    """
    from achats.models import (
        FactureFournisseur,
        AllocationReglement,
        AcompteFournisseur,
    )

    fournisseur = reglement.fournisseur
    montant_restant = Decimal(str(reglement.montant))

    if montant_restant <= 0:
        logger.warning(
            "appliquer_reglement_fifo: règlement pk=%s has non-positive amount (%s). "
            "Skipping allocation.",
            reglement.pk,
            montant_restant,
        )
        return

    # 1. Open invoices for this supplier, scoped to the règlement's OWN
    #    branche (BR-BRA-01) — oldest first (FIFO); exclude En Litige (BR-FAF-05)
    factures_ouvertes = FactureFournisseur.objects.filter(
        fournisseur=fournisseur,
        branche=reglement.branche,
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ],
    ).order_by("date_facture", "pk")

    # 2. Allocate
    for facture in factures_ouvertes:
        if montant_restant <= 0:
            break

        montant_a_allouer = min(montant_restant, facture.reste_a_payer)

        if montant_a_allouer <= 0:
            continue

        # 3. Create immutable allocation record
        AllocationReglement.objects.create(
            reglement=reglement,
            facture=facture,
            montant_alloue=montant_a_allouer,
        )

        facture.montant_regle = facture.montant_regle + montant_a_allouer
        facture.recalculer_solde()

        montant_restant -= montant_a_allouer
        logger.debug(
            "FIFO: allocated %s DZD from règlement pk=%s to facture %s. Remaining: %s DZD.",
            montant_a_allouer,
            reglement.pk,
            facture.reference,
            montant_restant,
        )

    # 4. Surplus → acompte (BR-REG-04). When there were no open invoices at
    #    all, this stores the *entire* règlement as a paiement anticipé
    #    (prepayment) — e.g. a cheque handed to a supplier before any
    #    facture exists. It sits here as an unused advance until
    #    consommer_acomptes_fifo() below draws it down against future
    #    factures (BR-REG-07).
    if montant_restant > 0:
        AcompteFournisseur.objects.create(
            fournisseur=fournisseur,
            reglement=reglement,
            montant=montant_restant,
            montant_restant=montant_restant,
            date=reglement.date_reglement,
            notes=(
                f"Surplus automatique depuis le règlement du "
                f"{reglement.date_reglement} — {reglement.montant} DZD total."
            ),
        )
        logger.info(
            "FIFO: surplus of %s DZD stored as AcompteFournisseur for %s (branche=%s).",
            montant_restant,
            fournisseur.nom,
            reglement.branche.code,
        )


# ---------------------------------------------------------------------------
# Prepayment consumption engine  (BR-REG-07)
# ---------------------------------------------------------------------------


def consommer_acomptes_fifo(facture):
    """
    Draw down unused AcompteFournisseur advances (prepayments / overpayment
    surplus) against a freshly-created facture, oldest advance first (FIFO),
    scoped to the SAME fournisseur + branche (BR-BRA-01) — mirrors
    appliquer_reglement_fifo but in the opposite direction (money already in
    hand, waiting for an invoice, instead of an invoice waiting for money).

    Typical flow this enables: a supplier (e.g. ONAB) is paid by cheque
    up front while it has no open invoices — the full amount becomes one
    AcompteFournisseur (see step 4 of appliquer_reglement_fifo). Each time a
    new facture for that supplier is created afterwards, this function is
    called (from the m2m_changed signal, right after montant_total is
    computed) and consumes the advance(s) automatically, one invoice at a
    time, until the advance is exhausted — at which point normal règlements
    take over again for any remaining balance.

    For every euro/dinar consumed:
      - An immutable AllocationAcompte record is created (audit trail).
      - The acompte's montant_restant is decremented; `utilise` flips to
        True once it reaches 0.
      - facture.montant_regle is increased and recalculer_solde() is called.

    Called inside the same DB transaction as facture creation.

    Args:
        facture (FactureFournisseur): The freshly-created invoice (its
            montant_total/reste_a_payer must already be set).
    """
    from achats.models import AcompteFournisseur, AllocationAcompte

    if facture.reste_a_payer <= 0:
        return

    acomptes = AcompteFournisseur.objects.filter(
        fournisseur=facture.fournisseur,
        branche=facture.branche,
        montant_restant__gt=0,
    ).order_by("date", "pk")

    montant_a_couvrir = facture.reste_a_payer

    for acompte in acomptes:
        if montant_a_couvrir <= 0:
            break

        montant_a_consommer = min(acompte.montant_restant, montant_a_couvrir)
        if montant_a_consommer <= 0:
            continue

        AllocationAcompte.objects.create(
            acompte=acompte,
            facture=facture,
            montant_alloue=montant_a_consommer,
        )

        acompte.montant_restant = acompte.montant_restant - montant_a_consommer
        acompte.utilise = acompte.montant_restant <= 0
        acompte.save(update_fields=["montant_restant", "utilise"])

        facture.montant_regle = facture.montant_regle + montant_a_consommer
        montant_a_couvrir -= montant_a_consommer

        logger.info(
            "BR-REG-07: consumed %s DZD from acompte pk=%s for %s to pay "
            "facture %s. Acompte remaining: %s DZD.",
            montant_a_consommer,
            acompte.pk,
            facture.fournisseur.nom,
            facture.reference,
            acompte.montant_restant,
        )

    if facture.montant_regle > 0:
        facture.recalculer_solde()


# ---------------------------------------------------------------------------
# Admin-only cascade delete — Facture + BLs + Règlements (destructive)
# ---------------------------------------------------------------------------


def supprimer_facture_fournisseur_cascade(facture):
    """
    ADMIN-ONLY hard delete: remove a FactureFournisseur together with every
    BL it includes and every ReglementFournisseur that allocated money to
    it — a full undo of an invoice cycle, for correcting mistakes.

    This intentionally bypasses two rules enforced everywhere else in the
    app, so the caller (the view) MUST verify the requesting user is an
    admin before calling this:
      - BR-BLF-02 / BR-FAF-03 : a Facturé BL is normally locked — here it
        is deleted outright.
      - BR-REG-06 : règlements are normally immutable after creation — here
        any règlement that touched this facture is deleted outright.

    Side effects (everything stays scoped to its own branche — BR-BRA-01):
      1. For every ReglementFournisseur that has at least one
         AllocationReglement pointing at `facture`:
           - Any OTHER allocations of that same règlement (i.e. money it
             also paid toward a DIFFERENT facture) are removed too, and
             that other facture's montant_regle / reste_a_payer / statut
             are recalculated — since the cash behind that allocation no
             longer exists in the system once the règlement is gone. This
             is logged as a BR-REG-06 override so it can be audited.
           - Its AcompteFournisseur (surplus), if any, is deleted.
           - The règlement itself is deleted.
      2. For every BLFournisseur included in `facture`:
           - Its RECU stock entry is reversed (StockIntrant decreased,
             corrective StockMouvement SORTIE logged — see
             achats.signals.annuler_entrees_stock_bl).
           - The BL is deleted (cascades to its lignes).
      3. `facture` itself is deleted last.

    Wrapped in a single DB transaction — either the whole cascade succeeds
    or none of it is applied.

    Args:
        facture (FactureFournisseur): The invoice to delete, with everything
            it created.

    Returns:
        dict: {
            "facture_reference": str,
            "bls_references": list[str],
            "reglements_references": list[str],
            "factures_tierces_impactees": list[str],
        }
    """
    from django.db import transaction
    from achats.models import (
        AllocationReglement,
        ReglementFournisseur,
        AcompteFournisseur,
    )
    from achats.signals import annuler_entrees_stock_bl

    summary = {
        "facture_reference": facture.reference,
        "bls_references": [],
        "reglements_references": [],
        "factures_tierces_impactees": [],
    }

    with transaction.atomic():
        # 1. Règlements that paid (any part of) this facture.
        reglement_ids = list(
            AllocationReglement.objects.filter(facture=facture)
            .values_list("reglement_id", flat=True)
            .distinct()
        )
        reglements = list(
            ReglementFournisseur.objects.filter(pk__in=reglement_ids)
        )

        for reglement in reglements:
            # 1a. Allocations of this règlement to OTHER factures also
            #     vanish with it — recompute those factures' soldes.
            autres_allocations = list(
                reglement.allocations.exclude(facture=facture).select_related(
                    "facture"
                )
            )
            for alloc in autres_allocations:
                autre_facture = alloc.facture
                autre_facture.montant_regle = max(
                    Decimal("0"), autre_facture.montant_regle - alloc.montant_alloue
                )
                alloc.delete()
                autre_facture.recalculer_solde()
                summary["factures_tierces_impactees"].append(autre_facture.reference)
                logger.warning(
                    "BR-REG-06 override: suppression cascade de la facture %s a "
                    "retiré %s DZD alloués par le règlement pk=%s à la facture "
                    "tierce %s (solde recalculé).",
                    facture.reference,
                    alloc.montant_alloue,
                    reglement.pk,
                    autre_facture.reference,
                )

            # 1b. Overpayment credit created by this règlement, if any. Any
            #     OTHER facture that has since consumed part of this acompte
            #     (BR-REG-07) must have that consumption reversed first —
            #     otherwise deleting the acompte would silently erase money
            #     another invoice was counting as paid.
            try:
                acompte = reglement.acompte
            except AcompteFournisseur.DoesNotExist:
                acompte = None
            if acompte is not None:
                for alloc in list(
                    acompte.allocations.exclude(facture=facture).select_related(
                        "facture"
                    )
                ):
                    autre_facture = alloc.facture
                    autre_facture.montant_regle = max(
                        Decimal("0"), autre_facture.montant_regle - alloc.montant_alloue
                    )
                    alloc.delete()
                    autre_facture.recalculer_solde()
                    summary["factures_tierces_impactees"].append(
                        autre_facture.reference
                    )
                    logger.warning(
                        "BR-REG-06 override: suppression cascade de la facture %s a "
                        "retiré %s DZD consommés depuis l'acompte du règlement "
                        "pk=%s sur la facture tierce %s (solde recalculé).",
                        facture.reference,
                        alloc.montant_alloue,
                        reglement.pk,
                        autre_facture.reference,
                    )
                acompte.allocations.filter(facture=facture).delete()
                acompte.delete()

            # 1c. Remaining allocations (the ones pointing at `facture`).
            reglement.allocations.all().delete()

            summary["reglements_references"].append(
                f"{reglement.montant} DZD ({reglement.date_reglement})"
            )
            reglement.delete()

        # 1d. This facture may itself have been paid (in whole or in part)
        #     from an acompte belonging to a DIFFERENT règlement (one that
        #     did not touch it via AllocationReglement at all — BR-REG-07).
        #     Give that money back to the advance.
        for alloc in list(facture.allocations_acompte.select_related("acompte")):
            source_acompte = alloc.acompte
            source_acompte.montant_restant = source_acompte.montant_restant + alloc.montant_alloue
            source_acompte.utilise = source_acompte.montant_restant <= 0
            source_acompte.save(update_fields=["montant_restant", "utilise"])
            alloc.delete()
            logger.info(
                "Suppression de la facture %s : %s DZD restitués à l'acompte pk=%s.",
                facture.reference,
                alloc.montant_alloue,
                source_acompte.pk,
            )

        # 2. BLs included in the facture: reverse stock, then delete.
        bls = list(
            facture.bls.select_related("branche").prefetch_related(
                "lignes__intrant"
            )
        )
        for bl in bls:
            annuler_entrees_stock_bl(bl)
            summary["bls_references"].append(bl.reference)
            bl.delete()

        # 3. The facture itself.
        facture.delete()

    logger.info(
        "ADMIN CASCADE DELETE: facture %s supprimée avec %d BL(s) et %d "
        "règlement(s). %d facture(s) tierce(s) impactée(s) : %s.",
        summary["facture_reference"],
        len(summary["bls_references"]),
        len(summary["reglements_references"]),
        len(summary["factures_tierces_impactees"]),
        summary["factures_tierces_impactees"],
    )
    return summary


# ---------------------------------------------------------------------------
# Admin-only cascade delete — Règlement (destructive, overrides BR-REG-06)
# ---------------------------------------------------------------------------


def supprimer_reglement_fournisseur_cascade(reglement):
    """
    ADMIN-ONLY hard delete: remove a ReglementFournisseur and reverse every
    side effect the FIFO engine created for it, for correcting mistakes
    (e.g. a payment recorded against the wrong supplier or amount).

    This intentionally bypasses BR-REG-06 (règlements are normally immutable
    after creation) — the caller (the view / admin) MUST verify the
    requesting user is an admin before calling this.

    Side effects, all within one DB transaction:
      1. Every AllocationReglement this règlement made to a facture is
         removed; that facture's montant_regle / reste_a_payer / statut are
         recalculated.
      2. If this règlement produced an AcompteFournisseur (BR-REG-04 surplus,
         or a full paiement anticipé), everything THAT acompte went on to
         fund via consommer_acomptes_fifo (BR-REG-07) — possibly invoices
         created well after this règlement — is reversed the same way, then
         the acompte itself is deleted.
      3. The règlement itself is deleted.

    Note: unlike supprimer_facture_fournisseur_cascade, this never touches
    BLs or stock — a règlement never created any stock movement.

    Args:
        reglement (ReglementFournisseur): The payment to delete.

    Returns:
        dict: {
            "reglement_montant": Decimal,
            "fournisseur_nom": str,
            "factures_impactees": list[str],
            "acompte_supprime": bool,
        }
    """
    from django.db import transaction
    from achats.models import AcompteFournisseur

    summary = {
        "reglement_montant": reglement.montant,
        "fournisseur_nom": reglement.fournisseur.nom,
        "factures_impactees": [],
        "acompte_supprime": False,
    }

    with transaction.atomic():
        # 1. Direct FIFO allocations to invoices.
        for alloc in list(reglement.allocations.select_related("facture")):
            facture = alloc.facture
            facture.montant_regle = max(
                Decimal("0"), facture.montant_regle - alloc.montant_alloue
            )
            alloc.delete()
            facture.recalculer_solde()
            summary["factures_impactees"].append(facture.reference)

        # 2. Surplus / prepayment acompte this règlement produced, and
        #    everything it went on to fund (BR-REG-07).
        try:
            acompte = reglement.acompte
        except AcompteFournisseur.DoesNotExist:
            acompte = None

        if acompte is not None:
            for alloc in list(acompte.allocations.select_related("facture")):
                facture = alloc.facture
                facture.montant_regle = max(
                    Decimal("0"), facture.montant_regle - alloc.montant_alloue
                )
                alloc.delete()
                facture.recalculer_solde()
                if facture.reference not in summary["factures_impactees"]:
                    summary["factures_impactees"].append(facture.reference)
                logger.warning(
                    "BR-REG-06 override: suppression du règlement pk=%s a retiré "
                    "%s DZD consommés depuis son acompte sur la facture %s "
                    "(solde recalculé).",
                    reglement.pk,
                    alloc.montant_alloue,
                    facture.reference,
                )
            acompte.delete()
            summary["acompte_supprime"] = True

        # 3. The règlement itself.
        reglement.delete()

    logger.info(
        "ADMIN CASCADE DELETE: règlement (%s DZD, %s) supprimé. %d facture(s) "
        "impactée(s) : %s. Acompte supprimé : %s.",
        summary["reglement_montant"],
        summary["fournisseur_nom"],
        len(summary["factures_impactees"]),
        summary["factures_impactees"],
        summary["acompte_supprime"],
    )
    return summary


# ---------------------------------------------------------------------------
# Weighted-average cost  (Prix Moyen Pondéré)
# ---------------------------------------------------------------------------


def calculer_pmp(
    quantite_ancienne,
    pmp_ancien,
    quantite_entree,
    prix_unitaire,
) -> Decimal:
    """
    Compute the new weighted-average cost (PMP) after a stock entry.

    Formula:
        PMP_new = (Q_old × PMP_old + Q_in × P_unit) / (Q_old + Q_in)

    Returns Decimal rounded to 4 decimal places.
    If total quantity is zero, returns Decimal("0.0000").

    Args:
        quantite_ancienne: Current stock quantity before the entry.
        pmp_ancien:        Current weighted-average cost.
        quantite_entree:   Quantity being received.
        prix_unitaire:     Unit cost of the incoming goods.
    """
    quantite_ancienne = Decimal(str(quantite_ancienne))
    pmp_ancien = Decimal(str(pmp_ancien))
    quantite_entree = Decimal(str(quantite_entree))
    prix_unitaire = Decimal(str(prix_unitaire))

    total_qty = quantite_ancienne + quantite_entree
    if total_qty == 0:
        return Decimal("0.0000")

    valeur_ancienne = quantite_ancienne * pmp_ancien
    valeur_entree = quantite_entree * prix_unitaire
    return round((valeur_ancienne + valeur_entree) / total_qty, 4)


# ---------------------------------------------------------------------------
# Sequential reference generators
# ---------------------------------------------------------------------------


def generer_reference_bl_fournisseur(branche) -> str:
    """
    Generate the next BL Fournisseur reference, scoped to *branche*.
    Format: <prefixe_bl_fournisseur>-<code_branche>-<YYYY>-<NNNN>
            e.g. BLF-EST-2026-0001 (BR-BRA-05).

    Args:
        branche (Branche): The branch this BL belongs to (BLFournisseur.branche
            is a required FK — BR-BRA-01).
    """
    from achats.models import BLFournisseur
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_bl_fournisseur")
    return generer_reference(BLFournisseur, prefix, branche=branche)


def generer_reference_facture_fournisseur(branche) -> str:
    """
    Generate the next Facture Fournisseur reference, scoped to *branche*.
    Format: <prefixe_facture_fournisseur>-<code_branche>-<YYYY>-<NNNN>
            e.g. FRN-EST-2026-0001 (BR-BRA-05).

    Args:
        branche (Branche): The branch this invoice belongs to
            (FactureFournisseur.branche is a required FK — BR-BRA-01).
    """
    from achats.models import FactureFournisseur
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_facture_fournisseur")
    return generer_reference(FactureFournisseur, prefix, branche=branche)


# ---------------------------------------------------------------------------
# Supplier financial summary
# ---------------------------------------------------------------------------


def get_fournisseur_solde(fournisseur, branche=None) -> dict:
    """
    Return a complete financial snapshot for one supplier.

    v1.4 (§3.5.3 ¶4): BLFournisseur/FactureFournisseur/ReglementFournisseur/
    AcompteFournisseur are branch-scoped, while Fournisseur itself stays
    global. Pass `branche` to get the figures exactly as that branch's chef
    de branche sees them; omit it for the Vue Globale figures, summed across
    every branch the supplier has ever transacted with.

    Keys returned:
        dette_globale       — sum of reste_a_payer on open invoices
        acompte_disponible  — unused overpayment credit
        factures_ouvertes   — queryset of non/partially-paid invoices (oldest first)
        total_reglements    — sum of all payments ever recorded
        nb_factures_retard  — count of overdue invoices (past due_date, not paid)

    Args:
        fournisseur (Fournisseur): The supplier instance.
        branche (Branche | None): Scope to one branch; omit for Vue Globale.
    """
    from achats.models import (
        FactureFournisseur,
        ReglementFournisseur,
        AcompteFournisseur,
    )
    from django.db.models import Sum

    factures_ouvertes = FactureFournisseur.objects.filter(
        fournisseur=fournisseur,
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ],
    ).order_by("date_facture", "pk")

    # BR-REG-07: acomptes can now be *partially* consumed, so availability is
    # `montant_restant` (not `montant`, and not just `utilise=False`) summed
    # over every advance that still has something left.
    acomptes_qs = AcompteFournisseur.objects.filter(
        fournisseur=fournisseur, montant_restant__gt=0
    )

    reglements_qs = ReglementFournisseur.objects.filter(fournisseur=fournisseur)

    if branche is not None:
        factures_ouvertes = factures_ouvertes.filter(branche=branche)
        acomptes_qs = acomptes_qs.filter(branche=branche)
        reglements_qs = reglements_qs.filter(branche=branche)

    dette_globale = factures_ouvertes.aggregate(total=Sum("reste_a_payer"))[
        "total"
    ] or Decimal("0")

    acompte_disponible = acomptes_qs.aggregate(total=Sum("montant_restant"))[
        "total"
    ] or Decimal("0")

    total_reglements = reglements_qs.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0")

    today = datetime.date.today()
    nb_factures_retard = (
        factures_ouvertes.filter(
            date_echeance__lt=today,
        )
        .exclude(statut=FactureFournisseur.STATUT_PAYE)
        .count()
    )

    return {
        "dette_globale": dette_globale,
        "acompte_disponible": acompte_disponible,
        "factures_ouvertes": factures_ouvertes,
        "total_reglements": total_reglements,
        "nb_factures_retard": nb_factures_retard,
    }


# ---------------------------------------------------------------------------
# Autorisation d'accès helpers
# ---------------------------------------------------------------------------


def get_autorisations_expirees(branche=None) -> list:
    """
    Return all autorisation_acces BLs that are past their expiry date and
    still in STATUT_AUTORISE (goods not yet picked up).

    Useful for dashboard alerts and nightly monitoring tasks.

    Args:
        branche (Branche | None): Scope to one branch (Vue par Branche);
            omit for Vue Globale — every branch's overdue authorizations,
            with the originating branch readable via `.branche` on each
            result (§3.5.5).

    Returns:
        list[BLFournisseur]: Ordered by expiry date ascending (oldest first).
    """
    from achats.models import BLFournisseur

    today = datetime.date.today()
    qs = BLFournisseur.objects.filter(
        type_document=BLFournisseur.TYPE_AUTORISATION_ACCES,
        statut=BLFournisseur.STATUT_AUTORISE,
        date_expiration_autorisation__lt=today,
    ).select_related("fournisseur", "branche")

    if branche is not None:
        qs = qs.filter(branche=branche)

    return list(qs.order_by("date_expiration_autorisation"))


def get_autorisations_en_attente(fournisseur=None, branche=None) -> list:
    """
    Return all autorisation_acces BLs in STATUT_AUTORISE that have not
    yet expired — i.e. active authorizations awaiting truck dispatch.

    Args:
        fournisseur (Fournisseur | None): Filter to one supplier; None = all.
        branche (Branche | None): Scope to one branch; omit for Vue Globale.

    Returns:
        list[BLFournisseur]: Ordered by expiry date ascending (soonest first).
    """
    from achats.models import BLFournisseur

    today = datetime.date.today()
    qs = (
        BLFournisseur.objects.filter(
            type_document=BLFournisseur.TYPE_AUTORISATION_ACCES,
            statut=BLFournisseur.STATUT_AUTORISE,
        )
        .filter(
            django_models.Q(date_expiration_autorisation__gte=today)
            | django_models.Q(date_expiration_autorisation__isnull=True)
        )
        .select_related("fournisseur", "branche")
    )

    if fournisseur:
        qs = qs.filter(fournisseur=fournisseur)
    if branche is not None:
        qs = qs.filter(branche=branche)

    return list(qs.order_by("date_expiration_autorisation"))


# ---------------------------------------------------------------------------
# Supplier aged-debt analysis  (for reporting — §9.12)
# ---------------------------------------------------------------------------


def get_supplier_aging_buckets(fournisseur=None, branche=None) -> list[dict]:
    """
    Compute an aged-debt breakdown for one supplier or all suppliers.

    Aging buckets (days past due date):
        current       — not yet due
        1_30          — 1–30 days overdue
        31_60         — 31–60 days overdue
        61_90         — 61–90 days overdue
        over_90       — > 90 days overdue

    v1.4 (§3.5.5): pass `branche` for the Vue par Branche figures (exactly
    what that branch's chef de branche sees); omit for Vue Globale, which
    sums a supplier's debt across every branch it has invoices in.

    Args:
        fournisseur (Fournisseur | None): Filter to one supplier; None = all.
        branche (Branche | None): Scope to one branch; omit for Vue Globale.

    Returns:
        list[dict]: One dict per supplier with bucket totals.
    """
    from achats.models import FactureFournisseur
    from intrants.models import Fournisseur

    today = datetime.date.today()

    qs = FactureFournisseur.objects.filter(
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ]
    ).select_related("fournisseur")

    if fournisseur:
        qs = qs.filter(fournisseur=fournisseur)
    if branche is not None:
        qs = qs.filter(branche=branche)

    # Group by supplier
    buckets_by_supplier: dict[int, dict] = {}

    for facture in qs:
        sup = facture.fournisseur
        if sup.pk not in buckets_by_supplier:
            buckets_by_supplier[sup.pk] = {
                "fournisseur": sup,
                "current": Decimal("0"),
                "1_30": Decimal("0"),
                "31_60": Decimal("0"),
                "61_90": Decimal("0"),
                "over_90": Decimal("0"),
                "total": Decimal("0"),
            }

        entry = buckets_by_supplier[sup.pk]
        rap = facture.reste_a_payer
        entry["total"] += rap

        if not facture.date_echeance or facture.date_echeance >= today:
            entry["current"] += rap
        else:
            days_late = (today - facture.date_echeance).days
            if days_late <= 30:
                entry["1_30"] += rap
            elif days_late <= 60:
                entry["31_60"] += rap
            elif days_late <= 90:
                entry["61_90"] += rap
            else:
                entry["over_90"] += rap

    return sorted(buckets_by_supplier.values(), key=lambda x: x["fournisseur"].nom)
