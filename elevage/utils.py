"""
elevage/utils.py

Business-logic helpers for the lot d'élevage domain.

  calculer_ic            — Feed Conversion Ratio (Indice de Consommation)
  get_lot_summary        — Full KPI snapshot for one lot (used by detail view
                           and lot-profitability report)
  verifier_mortalite_anormale — Detect abnormal daily mortality (alert trigger)
  lots_a_transferer      — Lots in Poussinière past the transfer-age threshold
                           (alert trigger, same spirit as verifier_mortalite_anormale)

v1.4 — Multi-Branch Architecture (§3.5): a LotElevage's `branche` is
denormalized from its bâtiment (BR-BRA-01) and every function below that
takes a `lot` is therefore already correctly scoped — no extra filtering
needed. The one exception was `_calculer_revenus_lot`, which crossed back
out to the global BLClientLigne table by `produit_fini` alone; since
StockProduitFini (and therefore sales) is now keyed by (branche, produit
fini) — BR-BRA-07 — that lookup is tightened to the lot's own branche below
so revenue from another branch selling the same catalogue product is never
misattributed to this lot.
"""

from decimal import Decimal
from typing import Optional
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feed Conversion Ratio  (Indice de Consommation — IC)
# ---------------------------------------------------------------------------


def calculer_ic(
    total_aliment_kg: Decimal, poids_total_produit_kg: Decimal
) -> Optional[Decimal]:
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

    v1.4 (BR-BRA-01 / BR-BRA-07): the produit_fini catalogue stays global,
    but its stock — and therefore every BL Client sale of it — is now keyed
    by (branche, produit_fini). A unit this lot produced only ever entered
    its OWN branche's StockProduitFini, so sales are restricted to BLs from
    that same branche; otherwise a sale of the same catalogue product by an
    entirely different branche/lot would be wrongly counted here.
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

    # Sum BLClientLigne totals for those produits on validated (Livré/Facturé)
    # BLs FROM THIS LOT'S OWN BRANCHE only (BR-BRA-07).
    from django.db.models import F, ExpressionWrapper, DecimalField

    total = BLClientLigne.objects.filter(
        produit_fini_id__in=produit_fini_ids,
        bl__branche=lot.branche,
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
    # Use nombre_poussins_reference (true initial cohort size) rather than
    # nombre_poussins_initial, which shrinks after transfers and would produce
    # a falsely low absolute threshold — triggering spurious alerts on
    # post-transfer lots where even a single death crosses the threshold.
    ref = lot.nombre_poussins_reference
    if ref == 0:
        return False

    seuil_absolu = ref * seuil_pourcentage / 100.0

    return lot.mortalites.filter(nombre__gte=seuil_absolu).exists()


# ---------------------------------------------------------------------------
# Transfer-due detection  (alert trigger — companion to verifier_mortalite_anormale)
# ---------------------------------------------------------------------------


def lots_a_transferer(branche=None) -> list:
    """
    Return open lots currently housed in a Poussinière that have reached
    (or passed) the configured transfer-age threshold
    (ParametrageElevage.age_transfert_poussiniere_jours).

    Used by the alert engine to prompt operators to create a TransfertLot
    for each lot returned — this function only detects the condition, it
    never moves a lot itself (that stays an explicit, auditable action via
    TransfertLot — see elevage.signals.transfert_lot_post_save).

    v1.4 (§3.5.5): every alert is computed per branch and surfaced to that
    branch's chef de branche. Pass `branche` to scope to one branch (what a
    chef de branche sees); omit for Vue Globale — every branch's due lots,
    with the originating branch readable via `lot.branche` on each result.

    The DB-level filter narrows to open lots in a Poussinière (and,
    optionally, one branche); the actual age/threshold comparison is
    delegated to LotElevage.doit_etre_transfere (single source of truth)
    since age_jours is a Python property, not a queryable field.

    Args:
        branche (Branche | None): Scope to one branch; omit for Vue Globale.
    """
    from elevage.models import LotElevage
    from intrants.models import Batiment

    candidats = LotElevage.objects.filter(
        statut=LotElevage.STATUT_OUVERT,
        batiment__type_batiment=Batiment.TYPE_POUSSINIERE,
    ).select_related("batiment", "branche")

    if branche is not None:
        candidats = candidats.filter(branche=branche)

    return [lot for lot in candidats if lot.doit_etre_transfere]


# ---------------------------------------------------------------------------
# Daily accumulation table (paper-ledger style) — new feature
# ---------------------------------------------------------------------------
#
# Reproduces the handwritten daily sheet (DATE / M / ALIMENT / OEFS / CUM /
# SEM / STOK …) as one row per calendar day of the lot's life, with running
# cumulative columns computed here rather than stored. Ambiguous columns
# from the paper form (OBL, KL) aren't reproduced — everything below maps
# to a concrete, already-modeled quantity:
#   M       -> mortalité du jour
#   ALIMENT -> aliment consommé ce jour-là (kg, catégorie ALIMENT)
#   CUM (aliment) -> cumul aliment depuis l'ouverture du lot
#   OEFS    -> œufs récoltés ce jour-là
#   CUM (œufs) -> cumul œufs récoltés depuis l'ouverture
#   RETRAIT -> œufs sortis ce jour-là (vente directe/don/perte — RetraitOeufs)
#   STOCK   -> solde d'œufs = cumul récolté − cumul retiré (peut dépasser ce
#              lot si des œufs d'autres lots partagent le même StockProduitFini
#              — affiché à titre indicatif pour ce lot)
#   SEM     -> numéro de semaine d'élevage (1 = jours 1-7, etc.)


def get_lot_suivi_journalier(lot) -> list:
    """
    Build the day-by-day accumulation table for one lot, from
    lot.date_ouverture through lot.date_fermeture (or today if still open).

    Returns a list of dicts (one per calendar day, chronological order):
        date, jour_numero, semaine, mortalite_jour, effectif_vivant_fin_jour,
        aliment_jour_kg, aliment_cumul_kg,
        oeufs_jour, oeufs_cumul, oeufs_retraits_jour, oeufs_stock
    """
    from django.db.models import Sum
    from elevage.models import RetraitOeufs

    date_fin = lot.date_fermeture or datetime.date.today()
    date_debut = lot.date_ouverture
    nb_jours = (date_fin - date_debut).days + 1
    if nb_jours <= 0:
        return []

    # --- Pre-aggregate every source by date, once, to avoid N+1 queries ---
    mortalite_par_jour = {
        row["date"]: row["total"]
        for row in lot.mortalites.values("date").annotate(total=Sum("nombre"))
    }
    aliment_par_jour = {
        row["date"]: row["total"]
        for row in lot.consommations.filter(intrant__categorie__code="ALIMENT")
        .values("date")
        .annotate(total=Sum("quantite"))
    }
    oeufs_par_jour = {
        row["date"]: row["total"]
        for row in lot.recoltes_oeufs.values("date").annotate(total=Sum("nombre_oeufs"))
    }
    retraits_par_jour = {
        row["date"]: row["total"]
        for row in RetraitOeufs.objects.filter(lot=lot)
        .values("date")
        .annotate(total=Sum("quantite_oeufs"))
    }

    effectif = lot.nombre_poussins_initial
    aliment_cumul = Decimal("0")
    oeufs_cumul = 0
    oeufs_retraits_cumul = 0
    rows = []

    for i in range(nb_jours):
        jour = date_debut + datetime.timedelta(days=i)

        m = mortalite_par_jour.get(jour, 0)
        a = aliment_par_jour.get(jour) or Decimal("0")
        o = oeufs_par_jour.get(jour, 0)
        r = retraits_par_jour.get(jour, 0)

        effectif -= m
        aliment_cumul += Decimal(str(a))
        oeufs_cumul += o
        oeufs_retraits_cumul += r

        rows.append(
            {
                "date": jour,
                "jour_numero": i + 1,
                "semaine": i // 7 + 1,
                "mortalite_jour": m,
                "effectif_vivant_fin_jour": effectif,
                "aliment_jour_kg": Decimal(str(a)),
                "aliment_cumul_kg": aliment_cumul,
                "oeufs_jour": o,
                "oeufs_cumul": oeufs_cumul,
                "oeufs_retraits_jour": r,
                "oeufs_stock": oeufs_cumul - oeufs_retraits_cumul,
            }
        )

    return rows
