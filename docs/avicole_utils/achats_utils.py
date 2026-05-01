"""
achats/utils.py

Business-logic helpers for the supplier procurement cycle.

  appliquer_reglement_fifo   — FIFO allocation engine (BR-REG-03 / BR-REG-04)
  calculer_pmp               — Weighted-average cost (Prix Moyen Pondéré)
  generer_reference_bl_fournisseur    — Sequential BLF reference
  generer_reference_facture_fournisseur — Sequential FRN reference
  get_supplier_aging_buckets — Aged debt analysis for reporting
  get_fournisseur_solde      — Full financial summary for one supplier
"""

from decimal import Decimal
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FIFO Payment Allocation  (BR-REG-03 / BR-REG-04)
# ---------------------------------------------------------------------------

def appliquer_reglement_fifo(reglement):
    """
    Distribute *reglement.montant* across open factures fournisseur for the
    same supplier using FIFO (oldest invoice date first).

    Steps:
      1. Fetch all Non-Payé / Partiellement-Payé invoices ordered by
         date_facture ASC, then pk ASC (tie-break — BR-REG-02).
      2. Allocate the payment amount invoice by invoice until exhausted.
      3. For each allocation, create an AllocationReglement record and call
         facture.recalculer_solde() to update the invoice balance + status.
      4. If surplus remains after all invoices are cleared, create an
         AcompteFournisseur (BR-REG-04).

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

    # 1. Open invoices — oldest first (FIFO); exclude En Litige (BR-FAF-05)
    factures_ouvertes = FactureFournisseur.objects.filter(
        fournisseur=fournisseur,
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

    # 4. Surplus → acompte (BR-REG-04)
    if montant_restant > 0:
        AcompteFournisseur.objects.create(
            fournisseur=fournisseur,
            reglement=reglement,
            montant=montant_restant,
            date=reglement.date_reglement,
            notes=(
                f"Surplus automatique depuis le règlement du "
                f"{reglement.date_reglement} — {reglement.montant} DZD total."
            ),
        )
        logger.info(
            "FIFO: surplus of %s DZD stored as AcompteFournisseur for %s.",
            montant_restant,
            fournisseur.nom,
        )


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
    pmp_ancien        = Decimal(str(pmp_ancien))
    quantite_entree   = Decimal(str(quantite_entree))
    prix_unitaire     = Decimal(str(prix_unitaire))

    total_qty = quantite_ancienne + quantite_entree
    if total_qty == 0:
        return Decimal("0.0000")

    valeur_ancienne = quantite_ancienne * pmp_ancien
    valeur_entree   = quantite_entree   * prix_unitaire
    return round((valeur_ancienne + valeur_entree) / total_qty, 4)


# ---------------------------------------------------------------------------
# Sequential reference generators
# ---------------------------------------------------------------------------

def generer_reference_bl_fournisseur() -> str:
    """
    Generate the next BL Fournisseur reference.
    Format: <prefixe_bl_fournisseur>-<YYYY>-<NNNN>   e.g. BLF-2025-0001
    """
    from achats.models import BLFournisseur
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_bl_fournisseur")
    return generer_reference(BLFournisseur, prefix)


def generer_reference_facture_fournisseur() -> str:
    """
    Generate the next Facture Fournisseur reference.
    Format: <prefixe_facture_fournisseur>-<YYYY>-<NNNN>   e.g. FRN-2025-0001
    """
    from achats.models import FactureFournisseur
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_facture_fournisseur")
    return generer_reference(FactureFournisseur, prefix)


# ---------------------------------------------------------------------------
# Supplier financial summary
# ---------------------------------------------------------------------------

def get_fournisseur_solde(fournisseur) -> dict:
    """
    Return a complete financial snapshot for one supplier.

    Keys returned:
        dette_globale       — sum of reste_a_payer on open invoices
        acompte_disponible  — unused overpayment credit
        factures_ouvertes   — queryset of non/partially-paid invoices (oldest first)
        total_reglements    — sum of all payments ever recorded
        nb_factures_retard  — count of overdue invoices (past due_date, not paid)

    Args:
        fournisseur (Fournisseur): The supplier instance.
    """
    from achats.models import FactureFournisseur, ReglementFournisseur, AcompteFournisseur
    from django.db.models import Sum

    factures_ouvertes = FactureFournisseur.objects.filter(
        fournisseur=fournisseur,
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ],
    ).order_by("date_facture", "pk")

    dette_globale = (
        factures_ouvertes.aggregate(total=Sum("reste_a_payer"))["total"] or Decimal("0")
    )

    acompte_disponible = (
        AcompteFournisseur.objects.filter(fournisseur=fournisseur, utilise=False)
        .aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    total_reglements = (
        ReglementFournisseur.objects.filter(fournisseur=fournisseur)
        .aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    today = datetime.date.today()
    nb_factures_retard = factures_ouvertes.filter(
        date_echeance__lt=today,
    ).exclude(statut=FactureFournisseur.STATUT_PAYE).count()

    return {
        "dette_globale": dette_globale,
        "acompte_disponible": acompte_disponible,
        "factures_ouvertes": factures_ouvertes,
        "total_reglements": total_reglements,
        "nb_factures_retard": nb_factures_retard,
    }


# ---------------------------------------------------------------------------
# Supplier aged-debt analysis  (for reporting — §9.12)
# ---------------------------------------------------------------------------

def get_supplier_aging_buckets(fournisseur=None) -> list[dict]:
    """
    Compute an aged-debt breakdown for one supplier or all suppliers.

    Aging buckets (days past due date):
        current       — not yet due
        1_30          — 1–30 days overdue
        31_60         — 31–60 days overdue
        61_90         — 61–90 days overdue
        over_90       — > 90 days overdue

    Args:
        fournisseur (Fournisseur | None): Filter to one supplier; None = all.

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
