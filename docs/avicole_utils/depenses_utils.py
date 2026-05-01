"""
depenses/utils.py

Business-logic helpers for the dépenses (operational expenses) module.

  get_cash_flow_summary      — §20.6  Period cash-flow: inflows (client
                               payments) vs outflows (supplier settlements +
                               dépenses), net position.
  get_depenses_par_categorie — Expense breakdown by category for a period
                               (used in the cost analysis report).
  get_depenses_par_lot       — Expenses attributed to a specific lot, for
                               per-lot profitability (BR-DEP-04 / §20.5).
  get_depenses_summary       — Aggregate summary of all expenses for a period
                               (total, by payment method, by category).
"""

from decimal import Decimal
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §20.6 — Cash Flow Summary
# ---------------------------------------------------------------------------

def get_cash_flow_summary(date_debut=None, date_fin=None) -> dict:
    """
    Compute a period-based cash-flow statement:

      Inflows  = client payments (PaiementClient.montant) received in period
      Outflows = supplier settlements (ReglementFournisseur.montant)
               + operational expenses (Depense.montant)
               paid in period
      Net      = inflows − outflows

    Filtering is inclusive on both bounds.  When a bound is None, no date
    restriction is applied on that side.

    Args:
        date_debut (date | None): Start of period (inclusive).
        date_fin   (date | None): End of period (inclusive).

    Returns dict with keys:
        date_debut           (date | None)
        date_fin             (date | None)
        total_encaissements  (Decimal) — client payments received
        total_reglements_fournisseurs (Decimal) — supplier settlements paid
        total_depenses       (Decimal) — operational expenses paid
        total_sorties        (Decimal) — reglements + depenses
        solde_net            (Decimal) — encaissements − sorties
        detail_paiements     (queryset) — PaiementClient rows in period
        detail_reglements    (queryset) — ReglementFournisseur rows in period
        detail_depenses      (queryset) — Depense rows in period
        par_mode_encaissement (list[dict]) — inflows grouped by payment method
        par_mode_sortie       (list[dict]) — outflows (règlements+dép) by method
    """
    from clients.models import PaiementClient
    from achats.models import ReglementFournisseur
    from depenses.models import Depense
    from django.db.models import Sum

    # ── Client payments (inflows) ─────────────────────────────────────────
    paiements_qs = PaiementClient.objects.select_related("client")
    if date_debut:
        paiements_qs = paiements_qs.filter(date_paiement__gte=date_debut)
    if date_fin:
        paiements_qs = paiements_qs.filter(date_paiement__lte=date_fin)

    total_encaissements = (
        paiements_qs.aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    # ── Supplier settlements (outflows) ──────────────────────────────────
    reglements_qs = ReglementFournisseur.objects.select_related("fournisseur")
    if date_debut:
        reglements_qs = reglements_qs.filter(date_reglement__gte=date_debut)
    if date_fin:
        reglements_qs = reglements_qs.filter(date_reglement__lte=date_fin)

    total_reglements = (
        reglements_qs.aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    # ── Operational expenses (outflows) ──────────────────────────────────
    depenses_qs = Depense.objects.select_related("categorie")
    if date_debut:
        depenses_qs = depenses_qs.filter(date__gte=date_debut)
    if date_fin:
        depenses_qs = depenses_qs.filter(date__lte=date_fin)

    total_depenses = (
        depenses_qs.aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    total_sorties = total_reglements + total_depenses
    solde_net = total_encaissements - total_sorties

    # ── Group inflows by payment method ──────────────────────────────────
    par_mode_encaissement = _grouper_par_mode(paiements_qs, "mode_paiement", "montant")

    # ── Group outflows by payment method (règlements + dépenses combined) ─
    par_mode_sortie = _grouper_par_mode_mixte(reglements_qs, depenses_qs)

    logger.debug(
        "get_cash_flow_summary [%s → %s]: encaissements=%s, sorties=%s, net=%s DZD.",
        date_debut,
        date_fin,
        total_encaissements,
        total_sorties,
        solde_net,
    )

    return {
        "date_debut": date_debut,
        "date_fin": date_fin,
        "total_encaissements": total_encaissements,
        "total_reglements_fournisseurs": total_reglements,
        "total_depenses": total_depenses,
        "total_sorties": total_sorties,
        "solde_net": solde_net,
        "detail_paiements": paiements_qs.order_by("-date_paiement"),
        "detail_reglements": reglements_qs.order_by("-date_reglement"),
        "detail_depenses": depenses_qs.order_by("-date"),
        "par_mode_encaissement": par_mode_encaissement,
        "par_mode_sortie": par_mode_sortie,
    }


# ---------------------------------------------------------------------------
# Expense breakdown by category
# ---------------------------------------------------------------------------

def get_depenses_par_categorie(date_debut=None, date_fin=None) -> list[dict]:
    """
    Aggregate expenses by CategorieDepense for a given period.

    Returns a list of dicts sorted by total descending:
        categorie    — CategorieDepense instance
        total        — Decimal total DZD for the period
        nb           — count of individual expense records
        pct          — percentage of grand total (0–100, rounded to 1 dp)

    Args:
        date_debut (date | None): Period start (inclusive).
        date_fin   (date | None): Period end (inclusive).
    """
    from depenses.models import Depense, CategorieDepense
    from django.db.models import Sum, Count

    qs = Depense.objects.all()
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    aggregated = (
        qs.values("categorie_id")
        .annotate(total=Sum("montant"), nb=Count("pk"))
        .order_by("-total")
    )

    grand_total = sum(row["total"] or Decimal("0") for row in aggregated)

    # Fetch all category objects in one query for display
    categorie_map = {
        c.pk: c
        for c in CategorieDepense.objects.filter(
            pk__in=[row["categorie_id"] for row in aggregated]
        )
    }

    result = []
    for row in aggregated:
        total = row["total"] or Decimal("0")
        pct = round(float(total / grand_total * 100), 1) if grand_total else 0.0
        result.append({
            "categorie": categorie_map.get(row["categorie_id"]),
            "total": total,
            "nb": row["nb"],
            "pct": pct,
        })

    return result


# ---------------------------------------------------------------------------
# Expenses by lot  (BR-DEP-04 / §20.5)
# ---------------------------------------------------------------------------

def get_depenses_par_lot(lot) -> dict:
    """
    Return all Depense records attributed to a specific LotElevage, together
    with the total cost.  Used in the lot profitability calculation (§20.5).

    This is a thin wrapper around the lot.depenses reverse relation; it is
    kept here so the view layer always goes through utils, and so the logic
    can be extended (e.g. filtering by category) without touching view code.

    Args:
        lot (LotElevage): The lot whose expenses should be retrieved.

    Returns dict with keys:
        lot           — the LotElevage instance
        depenses      — queryset of Depense records for this lot
        total         — Decimal total DZD
        par_categorie — list of dicts (same shape as get_depenses_par_categorie)
    """
    from django.db.models import Sum, Count

    depenses_qs = lot.depenses.select_related("categorie").order_by("-date")
    total = depenses_qs.aggregate(total=Sum("montant"))["total"] or Decimal("0")

    aggregated = (
        lot.depenses.values("categorie_id", "categorie__libelle")
        .annotate(total=Sum("montant"), nb=Count("pk"))
        .order_by("-total")
    )

    par_categorie = [
        {
            "categorie_libelle": row["categorie__libelle"],
            "total": row["total"] or Decimal("0"),
            "nb": row["nb"],
        }
        for row in aggregated
    ]

    return {
        "lot": lot,
        "depenses": depenses_qs,
        "total": total,
        "par_categorie": par_categorie,
    }


# ---------------------------------------------------------------------------
# General expense summary for a period
# ---------------------------------------------------------------------------

def get_depenses_summary(date_debut=None, date_fin=None) -> dict:
    """
    Return high-level expense statistics for a period.

    Useful for the management dashboard and for the expense list header.

    Args:
        date_debut (date | None): Period start (inclusive).
        date_fin   (date | None): Period end (inclusive).

    Returns dict with keys:
        total              — Decimal total DZD in period
        nb_depenses        — int count of records
        moyenne_par_jour   — Decimal average daily spend (None if no period)
        par_mode_paiement  — list[dict] {mode, label, total, nb}
        par_categorie      — list[dict] from get_depenses_par_categorie()
        max_depense        — Depense instance with highest montant (or None)
    """
    from depenses.models import Depense
    from django.db.models import Sum, Count, Max

    qs = Depense.objects.select_related("categorie")
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    agg = qs.aggregate(total=Sum("montant"), nb=Count("pk"), max_m=Max("montant"))
    total = agg["total"] or Decimal("0")
    nb_depenses = agg["nb"] or 0

    # Daily average (only meaningful when both bounds are given)
    moyenne_par_jour = None
    if date_debut and date_fin and date_fin >= date_debut:
        nb_jours = (date_fin - date_debut).days + 1
        if nb_jours > 0:
            moyenne_par_jour = round(total / nb_jours, 2)

    # Breakdown by payment method
    par_mode = _grouper_par_mode(qs, "mode_paiement", "montant")

    # Max single expense
    max_depense = None
    if agg["max_m"] is not None:
        max_depense = qs.filter(montant=agg["max_m"]).first()

    par_categorie = get_depenses_par_categorie(date_debut, date_fin)

    return {
        "total": total,
        "nb_depenses": nb_depenses,
        "moyenne_par_jour": moyenne_par_jour,
        "par_mode_paiement": par_mode,
        "par_categorie": par_categorie,
        "max_depense": max_depense,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _grouper_par_mode(qs, mode_field: str, montant_field: str) -> list[dict]:
    """
    Group a queryset by a mode_paiement (or mode_reglement) field and
    return aggregated totals.

    Returns a list of dicts sorted by total descending:
        mode    — raw choice value
        label   — human-readable label (resolved from model choices)
        total   — Decimal
        nb      — int count
    """
    from django.db.models import Sum, Count

    rows = (
        qs.values(mode_field)
        .annotate(total=Sum(montant_field), nb=Count("pk"))
        .order_by("-total")
    )

    # Build a label map from the model's choices if possible
    label_map = _get_mode_label_map(qs)

    return [
        {
            "mode": row[mode_field],
            "label": label_map.get(row[mode_field], row[mode_field]),
            "total": row["total"] or Decimal("0"),
            "nb": row["nb"],
        }
        for row in rows
    ]


def _grouper_par_mode_mixte(reglements_qs, depenses_qs) -> list[dict]:
    """
    Combine supplier settlements and operational expenses into a single
    outflow breakdown by payment method.

    Both models share compatible mode_paiement choice values (especes,
    cheque, virement, etc.).

    Returns list[dict] sorted by total descending:
        mode   — raw choice value
        label  — human-readable label
        total  — Decimal combined total
        nb     — total count of records
    """
    from django.db.models import Sum, Count

    combined: dict[str, dict] = {}

    def _merge(qs, mode_field, montant_field):
        rows = (
            qs.values(mode_field)
            .annotate(total=Sum(montant_field), nb=Count("pk"))
        )
        for row in rows:
            mode = row[mode_field]
            total = row["total"] or Decimal("0")
            nb = row["nb"]
            if mode not in combined:
                combined[mode] = {"total": Decimal("0"), "nb": 0}
            combined[mode]["total"] += total
            combined[mode]["nb"] += nb

    _merge(reglements_qs, "mode_paiement", "montant")
    _merge(depenses_qs, "mode_paiement", "montant")

    # Resolve labels from Depense.MODE_CHOICES (shared vocabulary)
    from depenses.models import Depense
    label_map = dict(Depense.MODE_CHOICES)

    return sorted(
        [
            {
                "mode": mode,
                "label": label_map.get(mode, mode),
                "total": data["total"],
                "nb": data["nb"],
            }
            for mode, data in combined.items()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )


def _get_mode_label_map(qs) -> dict:
    """
    Extract the mode_paiement label map from the first model instance's
    field choices, falling back to an empty dict if unavailable.
    """
    try:
        model = qs.model
        field = model._meta.get_field("mode_paiement")
        return dict(field.choices) if field.choices else {}
    except Exception:
        return {}
