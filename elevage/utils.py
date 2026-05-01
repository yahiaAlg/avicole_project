"""
elevage/utils.py

Business-logic helpers for the lot d'élevage domain.

  calculer_ic            — Feed Conversion Ratio (Indice de Consommation)
  get_lot_summary        — Full KPI snapshot for one lot (used by detail view
                           and lot-profitability report)
  verifier_mortalite_anormale — Detect abnormal daily mortality (alert trigger)
"""

from decimal import Decimal
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feed Conversion Ratio  (Indice de Consommation — IC)
# ---------------------------------------------------------------------------


def calculer_ic(
    total_aliment_kg: Decimal, poids_total_produit_kg: Decimal
) -> Decimal | None:
    """
    IC = total feed consumed (kg) / total live-weight produced (kg).

    Returns None when either figure is zero (IC is undefined / not yet
    meaningful).  A lower IC indicates better feed efficiency.

    Args:
        total_aliment_kg:       Cumulative feed consumed in kg.
        poids_total_produit_kg: Total live-weight harvested in kg.
    """
    aliment = Decimal(str(total_aliment_kg))
    poids = Decimal(str(poids_total_produit_kg))

    if aliment <= 0 or poids <= 0:
        return None

    return round(aliment / poids, 3)


# ---------------------------------------------------------------------------
# Lot KPI summary
# ---------------------------------------------------------------------------


def get_lot_summary(lot) -> dict:
    """
    Compile all computed indicators for a lot into a single dict.

    This is the canonical source of truth for the Lot Detail page (§9.2)
    and the lot-profitability report (§9.12).  Results are intentionally NOT
    cached here — the view layer is responsible for caching if needed.

    Keys returned:
        effectif_vivant          (int)
        total_mortalite          (int)
        taux_mortalite           (Decimal — %)
        duree_elevage            (int — days)
        consommation_totale_aliment_kg  (Decimal)
        poids_total_produit_kg   (Decimal)
        ic                       (Decimal | None)
        cout_total_intrants      (Decimal — DZD)
        cout_total_depenses      (Decimal — DZD)
        revenus_ventes           (Decimal — DZD)
        marge_brute              (Decimal — DZD)
        productions              (queryset)
        consommations            (queryset)
        mortalites               (queryset)
        depenses                 (queryset)

    Args:
        lot (LotElevage): The lot instance (with related managers available).
    """
    from django.db.models import Sum

    # --- Mortality & effectif -------------------------------------------
    total_mortalite = lot.total_mortalite  # property — uses DB aggregate
    effectif_vivant = lot.effectif_vivant
    taux_mortalite = lot.taux_mortalite
    duree_elevage = lot.duree_elevage

    # --- Consommation (feed) --------------------------------------------
    conso_aliment_qs = lot.consommations.filter(intrant__categorie__code="ALIMENT")
    total_aliment_kg = conso_aliment_qs.aggregate(total=Sum("quantite"))[
        "total"
    ] or Decimal("0")

    # --- Production output -----------------------------------------------
    from production.models import ProductionRecord

    productions = lot.productions.filter(
        statut=ProductionRecord.STATUT_VALIDE
    ).prefetch_related("lignes__produit_fini")
    poids_total_produit_kg = productions.aggregate(total=Sum("poids_total_kg"))[
        "total"
    ] or Decimal("0")

    ic = calculer_ic(total_aliment_kg, poids_total_produit_kg)

    # --- Input costs (Σ consommation × PMP) ------------------------------
    cout_total_intrants = Decimal(str(lot.cout_total_intrants))  # existing property

    # --- Attributed operational expenses --------------------------------
    depenses = lot.depenses.all()
    cout_total_depenses = depenses.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

    # --- Sales revenue: BL Client lines traceable to this lot's production
    #     We link via: lot → production → produits finis → BLClientLigne
    revenus_ventes = _calculer_revenus_lot(lot)

    marge_brute = revenus_ventes - cout_total_intrants - cout_total_depenses

    return {
        "effectif_vivant": effectif_vivant,
        "total_mortalite": total_mortalite,
        "taux_mortalite": taux_mortalite,
        "duree_elevage": duree_elevage,
        "consommation_totale_aliment_kg": total_aliment_kg,
        "poids_total_produit_kg": poids_total_produit_kg,
        "ic": ic,
        "cout_total_intrants": cout_total_intrants,
        "cout_total_depenses": cout_total_depenses,
        "revenus_ventes": revenus_ventes,
        "marge_brute": marge_brute,
        "productions": productions,
        "consommations": lot.consommations.select_related("intrant").order_by("-date"),
        "mortalites": lot.mortalites.order_by("-date"),
        "depenses": depenses.select_related("categorie").order_by("-date"),
    }


def _calculer_revenus_lot(lot) -> Decimal:
    """
    Estimate revenue attributable to a lot.

    Revenue is the sum of validated BLClientLigne line totals for all
    BLClient lines whose produit_fini was produced in a validated
    ProductionRecord for this lot.

    This is an approximation: the same produit_fini may be produced by
    multiple lots, so revenue is not perfectly isolated without a direct
    lot → BLClientLigne FK.  The spec notes this as "Revenus lot (ventes)"
    and accepts this level of traceability.
    """
    from production.models import ProductionRecord, ProductionLigne
    from clients.models import BLClientLigne, BLClient
    from django.db.models import Sum

    # Find all produit_fini PKs produced by this lot's validated records.
    produit_fini_ids = (
        ProductionLigne.objects.filter(
            production__lot=lot,
            production__statut=ProductionRecord.STATUT_VALIDE,
        )
        .values_list("produit_fini_id", flat=True)
        .distinct()
    )

    if not produit_fini_ids:
        return Decimal("0")

    # Sum BLClientLigne totals for those produits on validated (Livré/Facturé) BLs.
    from django.db.models import F, ExpressionWrapper, DecimalField

    total = BLClientLigne.objects.filter(
        produit_fini_id__in=produit_fini_ids,
        bl__statut__in=[BLClient.STATUT_LIVRE, BLClient.STATUT_FACTURE],
    ).aggregate(
        total=Sum(
            ExpressionWrapper(
                F("quantite") * F("prix_unitaire"),
                output_field=DecimalField(max_digits=16, decimal_places=2),
            )
        )
    )[
        "total"
    ]

    return Decimal(str(total or 0))


# ---------------------------------------------------------------------------
# Abnormal mortality detection  (alert trigger — §10.9)
# ---------------------------------------------------------------------------


def verifier_mortalite_anormale(
    lot,
    seuil_pourcentage: float = 5.0,
) -> bool:
    """
    Return True if any single-day mortality record for this lot exceeds
    *seuil_pourcentage* of the initial bird count.

    Used by the alert engine to flag lots with unusually high daily mortality.

    Args:
        lot (LotElevage): The lot to check.
        seuil_pourcentage (float): Daily mortality % threshold (default 5%).
    """
    if lot.nombre_poussins_initial == 0:
        return False

    seuil_absolu = lot.nombre_poussins_initial * seuil_pourcentage / 100.0

    return lot.mortalites.filter(nombre__gte=seuil_absolu).exists()
