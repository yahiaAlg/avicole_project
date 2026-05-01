"""
achats/utils.py

Business-logic helpers for the supplier procurement cycle.

FIFO Payment Allocation (BR-REG-03 / BR-REG-04):
  When a ReglementFournisseur is created, the full amount is distributed
  across the supplier's unpaid invoices ordered by date_facture ascending
  (oldest first).  Any surplus beyond the total debt is stored as an
  AcompteFournisseur (BR-REG-04).
"""

from decimal import Decimal
import datetime
import logging

logger = logging.getLogger(__name__)


def appliquer_reglement_fifo(reglement):
    """
    Distribute *reglement.montant* across open factures for the same
    fournisseur using FIFO (oldest invoice date first).

    Steps:
      1. Fetch all Non-Payé / Partiellement-Payé invoices ordered by date ASC.
      2. Allocate the payment amount invoice by invoice until exhausted.
      3. For each allocation, create an AllocationReglement record and call
         facture.recalculer_solde() to update the invoice balance + status.
      4. If surplus remains after all invoices are cleared, create an
         AcompteFournisseur (BR-REG-04).

    This function is called inside a post_save signal and assumes it runs
    within an atomic block (signals are already wrapped by Django's save
    transaction for SQLite/PostgreSQL).

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

    # 1. Open invoices — oldest first (FIFO)
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

        # Update invoice running total and status
        facture.montant_regle = facture.montant_regle + montant_a_allouer
        facture.recalculer_solde()

        montant_restant -= montant_a_allouer
        logger.debug(
            "FIFO: allocated %s DZD from règlement pk=%s to facture %s. "
            "Remaining: %s DZD.",
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


def calculer_pmp(quantite_ancienne, pmp_ancien, quantite_entree, prix_unitaire):
    """
    Weighted-average cost (Prix Moyen Pondéré) after a stock entry.

    Formula:
        PMP_new = (Q_old × PMP_old + Q_in × P_unit) / (Q_old + Q_in)

    Returns a Decimal rounded to 4 decimal places.
    If the existing stock is zero or the unit price is zero, the existing
    PMP is returned unchanged (unless there was no previous PMP either, in
    which case the entry price is used directly).

    Args:
        quantite_ancienne (Decimal): Current stock quantity before entry.
        pmp_ancien (Decimal):        Current weighted-average cost.
        quantite_entree (Decimal):   Quantity being received.
        prix_unitaire (Decimal):     Unit cost of the incoming goods.

    Returns:
        Decimal: New PMP rounded to 4 decimal places.
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
    nouveau_pmp = (valeur_ancienne + valeur_entree) / total_qty
    return round(nouveau_pmp, 4)


def generer_reference_bl_fournisseur():
    """
    Generate the next BL Fournisseur reference using the company prefix
    and an auto-incrementing sequence based on existing records.

    Format: <prefix>-<YYYY>-<NNNN>  e.g. BLF-2025-0001

    Returns:
        str: The next available reference string.
    """
    from achats.models import BLFournisseur
    from core.models import CompanyInfo

    prefix = CompanyInfo.get_instance().prefixe_bl_fournisseur
    year = datetime.date.today().year
    pattern = f"{prefix}-{year}-"

    last = (
        BLFournisseur.objects.filter(reference__startswith=pattern)
        .order_by("reference")
        .last()
    )

    if last:
        try:
            last_seq = int(last.reference.split("-")[-1])
        except (ValueError, IndexError):
            last_seq = 0
    else:
        last_seq = 0

    return f"{pattern}{last_seq + 1:04d}"


def generer_reference_facture_fournisseur():
    """
    Generate the next Facture Fournisseur reference.

    Format: <prefix>-<YYYY>-<NNNN>  e.g. FRN-2025-0001
    """
    from achats.models import FactureFournisseur
    from core.models import CompanyInfo

    prefix = CompanyInfo.get_instance().prefixe_facture_fournisseur
    year = datetime.date.today().year
    pattern = f"{prefix}-{year}-"

    last = (
        FactureFournisseur.objects.filter(reference__startswith=pattern)
        .order_by("reference")
        .last()
    )

    if last:
        try:
            last_seq = int(last.reference.split("-")[-1])
        except (ValueError, IndexError):
            last_seq = 0
    else:
        last_seq = 0

    return f"{pattern}{last_seq + 1:04d}"
