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
        cout_aliments            (Decimal — DZD)
        cout_medicaments         (Decimal — DZD, material/stock cost only)
        cout_main_oeuvre_medicament (Decimal — DZD, vet/team labor fee Depense)
        cout_traitement_total    (Decimal — DZD, cout_medicaments + cout_main_oeuvre_medicament)
        cout_mortalite_poussins  (Decimal — DZD)
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
    cout_aliments = Decimal(str(lot.cout_aliments))
    cout_medicaments = Decimal(str(lot.cout_medicaments))
    cout_mortalite_poussins = Decimal(str(lot.cout_mortalite_poussins))

    # --- Attributed operational expenses --------------------------------
    depenses = lot.depenses.all()
    cout_total_depenses = depenses.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

    # --- Treatment cost (médicaments/vaccins) — full picture -------------
    # cout_medicaments (above) is only the MATERIAL cost (Σ quantite × PMP,
    # already drawn from stock at consumption time). The vet/team's labor
    # fee is a separate Depense (catégorie MAIN_OEUVRE_MEDICAMENT, created
    # via views.consommation_medicament_paiement_create) and is only
    # attributed to THIS lot when every consommation in that payment batch
    # belonged to it (see that view's lot_unique logic) — a batch spanning
    # several lots stays unattributed, same limitation as
    # cout_total_depenses above. cout_traitement_total is the sum shown to
    # the user as "the real cost of treating this lot" (BR-request:
    # clarity between stock-cost médicaments and vet/team labor).
    cout_main_oeuvre_medicament = depenses.filter(
        categorie__code="MAIN_OEUVRE_MEDICAMENT"
    ).aggregate(total=Sum("montant"))["total"] or Decimal("0")
    cout_traitement_total = cout_medicaments + cout_main_oeuvre_medicament

    # --- Sales revenue: BL Client lines traceable to this lot's production
    #     We link via: lot → production → produits finis → BLClientLigne
    revenus_ventes = _calculer_revenus_lot(lot)

    marge_brute = revenus_ventes - cout_total_intrants - cout_total_depenses

    # --- Eggs (RecolteOeufs / RetraitOeufs) ------------------------------
    # `stock_oeufs_lot` is informational only (see RetraitOeufs docstring):
    # it's the running balance of *this lot's* collections minus its own
    # withdrawals, not a physical sub-stock — the real egg stock lives in
    # StockProduitFini, scoped to the branche as a whole.
    from elevage.models import RecolteOeufs, RetraitOeufs

    total_oeufs_collectes = (
        lot.recoltes_oeufs.aggregate(total=Sum("nombre_oeufs"))["total"] or 0
    )
    total_oeufs_retires = (
        RetraitOeufs.objects.filter(lot=lot).aggregate(total=Sum("quantite_oeufs"))[
            "total"
        ]
        or 0
    )
    stock_oeufs_lot = get_oeufs_stock_lot(lot)

    return {
        "effectif_vivant": effectif_vivant,
        "total_mortalite": total_mortalite,
        "taux_mortalite": taux_mortalite,
        "duree_elevage": duree_elevage,
        "consommation_totale_aliment_kg": total_aliment_kg,
        "poids_total_produit_kg": poids_total_produit_kg,
        "ic": ic,
        "cout_total_intrants": cout_total_intrants,
        "cout_aliments": cout_aliments,
        "cout_medicaments": cout_medicaments,
        "cout_main_oeuvre_medicament": cout_main_oeuvre_medicament,
        "cout_traitement_total": cout_traitement_total,
        "cout_mortalite_poussins": cout_mortalite_poussins,
        "cout_total_depenses": cout_total_depenses,
        "revenus_ventes": revenus_ventes,
        "marge_brute": marge_brute,
        "productions": productions,
        "consommations": lot.consommations.select_related("intrant").order_by("-date"),
        "mortalites": lot.mortalites.order_by("-date"),
        "depenses": depenses.select_related("categorie").order_by("-date"),
        "total_oeufs_collectes": total_oeufs_collectes,
        "total_oeufs_retires": total_oeufs_retires,
        "stock_oeufs_lot": stock_oeufs_lot,
        "stock_oeufs_lot_plateaux": stock_oeufs_lot // RecolteOeufs.PLATEAU_SIZE,
        "stock_oeufs_lot_hors_plateau": stock_oeufs_lot % RecolteOeufs.PLATEAU_SIZE,
        "retraits_oeufs": RetraitOeufs.objects.filter(lot=lot)
        .select_related("client")
        .order_by("-date"),
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
# Egg withdrawal helpers — informational per-lot stock + FIFO suggestion
# ---------------------------------------------------------------------------
#
# The real, physical egg stock is StockProduitFini, pooled at branche level
# (see RetraitOeufs model docstring) and — like Consommation/Mortalite — is
# never blocked from going negative. `lot` on RetraitOeufs/RecolteOeufs is
# optional attribution/bookkeeping only. Everything below is therefore
# advisory: it helps a user avoid accidentally emptying a lot's own ledger
# below zero, and suggests a FIFO (oldest-lot-first) attribution split when
# a single lot's own running balance can't cover the withdrawal — it never
# blocks a save.


def get_oeufs_stock_lot(lot, as_of=None) -> int:
    """
    Informational running egg balance for one lot: its own cumulative
    RecolteOeufs minus its own cumulative RetraitOeufs, optionally capped to
    `as_of` (inclusive). This is the single source of truth reused by
    get_lot_summary, get_lot_suivi_journalier, and the withdrawal-form
    soft-warning/FIFO endpoints — never a physical sub-stock (see module
    docstring above).
    """
    from django.db.models import Sum
    from elevage.models import RetraitOeufs

    recoltes_qs = lot.recoltes_oeufs.all()
    retraits_qs = RetraitOeufs.objects.filter(lot=lot)
    if as_of is not None:
        recoltes_qs = recoltes_qs.filter(date__lte=as_of)
        retraits_qs = retraits_qs.filter(date__lte=as_of)

    total_collectes = recoltes_qs.aggregate(total=Sum("nombre_oeufs"))["total"] or 0
    total_retires = retraits_qs.aggregate(total=Sum("quantite_oeufs"))["total"] or 0
    return total_collectes - total_retires


def get_oeufs_fifo_allocation(
    branche, quantite_demandee: int, date, lot_prioritaire=None
) -> dict:
    """
    Advisory FIFO split suggestion for withdrawing `quantite_demandee` eggs
    on `date`, scoped to `branche`.

    Walks open lots oldest-first (date_ouverture ascending — FIFO), starting
    with `lot_prioritaire` if given (so the lot the user actually selected is
    drained first, exactly like the paper ledger would), taking from each
    lot's own informational balance (get_oeufs_stock_lot, as_of=date) until
    the requested quantity is covered or lots run out.

    Purely a suggestion for how to *attribute* the withdrawal across lots'
    ledgers — it never touches the real (branche-level, always-lenient)
    stock, and never blocks anything.

    Returns:
        {
          "allocations": [
              {"lot_id": int, "designation": str, "quantite": int,
               "stock_disponible": int}, ...
          ],
          "quantite_allouee": int,
          "shortfall": int,  # > 0 if even every open lot combined can't
                              # cover the requested quantity
        }
    """
    from elevage.models import LotElevage

    lots = list(
        LotElevage.objects.filter(
            branche=branche, statut=LotElevage.STATUT_OUVERT
        ).order_by("date_ouverture")
    )
    if lot_prioritaire is not None:
        lots = [lot_prioritaire] + [l for l in lots if l.pk != lot_prioritaire.pk]

    restant = quantite_demandee
    allocations = []
    for lot in lots:
        if restant <= 0:
            break
        disponible = get_oeufs_stock_lot(lot, as_of=date)
        if disponible <= 0:
            continue
        pris = min(disponible, restant)
        allocations.append(
            {
                "lot_id": lot.pk,
                "designation": lot.designation,
                "quantite": pris,
                "stock_disponible": disponible,
            }
        )
        restant -= pris

    return {
        "allocations": allocations,
        "quantite_allouee": quantite_demandee - restant,
        "shortfall": max(restant, 0),
    }


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
    lot.date_ouverture through lot.date_fermeture — or, for a still-open
    lot, through the date of its most recent recorded action (mortalité,
    consommation/médicament, récolte or retrait d'œufs), not today. No
    trailing empty rows are generated past that last event.

    Returns a list of dicts (one per calendar day, chronological order):
        date, jour_numero, semaine, mortalite_jour, effectif_vivant_fin_jour,
        aliment_jour_kg, aliment_cumul_kg,
        medicament_jour (list of {libelle, total} per unité, non-zero only),
        medicament_cumul (list of {libelle, total} per unité, all units seen),
        oeufs_jour, oeufs_cumul, oeufs_retraits_jour, oeufs_stock
    """
    from django.db.models import Sum
    from elevage.models import RetraitOeufs, RecolteOeufs

    date_debut = lot.date_ouverture

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
    # Médicaments span several units (ML, DOSE, FLACON…) unlike feed
    # (always KG), so they're grouped per (date, unité) rather than summed
    # into one figure. `unites_ordre` fixes a stable column order (by the
    # unité's own display order) that both medicament_jour and
    # medicament_cumul stick to, so every row lines up under the same
    # unit headings.
    medicament_rows = (
        lot.consommations.exclude(intrant__categorie__code="ALIMENT")
        .values(
            "date",
            "intrant__unite_mesure__libelle",
            "intrant__unite_mesure__ordre",
        )
        .annotate(total=Sum("quantite"))
        .order_by("intrant__unite_mesure__ordre")
    )
    unites_ordre = []
    medicament_par_jour = {}
    for row in medicament_rows:
        libelle = row["intrant__unite_mesure__libelle"]
        if libelle not in unites_ordre:
            unites_ordre.append(libelle)
        medicament_par_jour.setdefault(row["date"], {})[libelle] = row["total"]
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

    if lot.date_fermeture:
        date_fin = lot.date_fermeture
    else:
        derniers = [
            max(mortalite_par_jour) if mortalite_par_jour else None,
            max(aliment_par_jour) if aliment_par_jour else None,
            max(medicament_par_jour) if medicament_par_jour else None,
            max(oeufs_par_jour) if oeufs_par_jour else None,
            max(retraits_par_jour) if retraits_par_jour else None,
        ]
        derniers = [d for d in derniers if d is not None]
        date_fin = max(derniers) if derniers else date_debut
    nb_jours = (date_fin - date_debut).days + 1
    if nb_jours <= 0:
        return []

    effectif = lot.nombre_poussins_initial
    aliment_cumul = Decimal("0")
    medicament_cumul_par_unite = {libelle: Decimal("0") for libelle in unites_ordre}
    oeufs_cumul = 0
    oeufs_retraits_cumul = 0
    rows = []

    for i in range(nb_jours):
        jour = date_debut + datetime.timedelta(days=i)

        m = mortalite_par_jour.get(jour, 0)
        a = aliment_par_jour.get(jour) or Decimal("0")
        meds_jour_par_unite = medicament_par_jour.get(jour, {})
        o = oeufs_par_jour.get(jour, 0)
        r = retraits_par_jour.get(jour, 0)

        effectif -= m
        aliment_cumul += Decimal(str(a))
        for libelle, total in meds_jour_par_unite.items():
            medicament_cumul_par_unite[libelle] += Decimal(str(total))
        oeufs_cumul += o
        oeufs_retraits_cumul += r
        oeufs_stock = oeufs_cumul - oeufs_retraits_cumul

        rows.append(
            {
                "date": jour,
                "jour_numero": i + 1,
                "semaine": i // 7 + 1,
                "mortalite_jour": m,
                "effectif_vivant_fin_jour": effectif,
                "aliment_jour_kg": Decimal(str(a)),
                "aliment_cumul_kg": aliment_cumul,
                "medicament_jour": [
                    {"libelle": libelle, "total": meds_jour_par_unite[libelle]}
                    for libelle in unites_ordre
                    if libelle in meds_jour_par_unite
                ],
                "medicament_cumul": [
                    {"libelle": libelle, "total": medicament_cumul_par_unite[libelle]}
                    for libelle in unites_ordre
                ],
                "oeufs_jour": o,
                "oeufs_cumul": oeufs_cumul,
                "oeufs_retraits_jour": r,
                "oeufs_retraits_jour_plateaux": r // RecolteOeufs.PLATEAU_SIZE,
                "oeufs_retraits_jour_hors_plateau": r % RecolteOeufs.PLATEAU_SIZE,
                "oeufs_stock": oeufs_stock,
                "oeufs_stock_plateaux": oeufs_stock // RecolteOeufs.PLATEAU_SIZE,
                "oeufs_stock_hors_plateau": oeufs_stock % RecolteOeufs.PLATEAU_SIZE,
            }
        )

    return rows
