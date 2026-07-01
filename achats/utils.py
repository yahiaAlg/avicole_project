"""
achats/utils.py

Business-logic helpers for the supplier procurement cycle.

  appliquer_reglement_fifo   — FIFO allocation engine (BR-REG-03 / BR-REG-04)
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
            "FIFO: surplus of %s DZD stored as AcompteFournisseur for %s (branche=%s).",
            montant_restant,
            fournisseur.nom,
            reglement.branche.code,
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

    acomptes_qs = AcompteFournisseur.objects.filter(
        fournisseur=fournisseur, utilise=False
    )

    reglements_qs = ReglementFournisseur.objects.filter(fournisseur=fournisseur)

    if branche is not None:
        factures_ouvertes = factures_ouvertes.filter(branche=branche)
        acomptes_qs = acomptes_qs.filter(branche=branche)
        reglements_qs = reglements_qs.filter(branche=branche)

    dette_globale = factures_ouvertes.aggregate(total=Sum("reste_a_payer"))[
        "total"
    ] or Decimal("0")

    acompte_disponible = acomptes_qs.aggregate(total=Sum("montant"))[
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
