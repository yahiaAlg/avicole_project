"""
depenses/utils.py

Business-logic helpers for the dépenses (operational expenses) module,
including the two special expense families: associés withdrawals and RH
(payroll/attendance).

  get_cash_flow_summary      — §20.6  Period cash-flow: inflows (client
                               payments) vs outflows (supplier settlements +
                               dépenses + retraits associés + paie), net
                               position.
  get_depenses_par_categorie — Expense breakdown by category for a period
                               (used in the cost analysis report).
  get_depenses_par_lot       — Expenses attributed to a specific lot, for
                               per-lot profitability (BR-DEP-04 / §20.5).
  get_depenses_summary       — Aggregate summary of all expenses for a period
                               (total, by payment method, by category).

  get_retraits_associes_summary — Withdrawals summary for a period, with
                               per-stakeholder breakdown (BR-ASSOC-01).

  get_solde_conge            — Paid-leave balance for an employee (BR-RH-03).
  appliquer_conge_aux_pointages — Materialize a CongeEmploye block into
                               Pointage rows (single source of truth, BR-RH-05).
  calculer_donnees_paie      — Compute (without saving) a payslip's figures
                               from an employee's Pointage rows for a month
                               (BR-RH-02 / BR-RH-05).
  get_rh_summary              — Aggregate payroll + advances figures for a
                               period (used in the RH dashboard).

v1.4 — Multi-Branch Architecture (§3.5): `Depense.branche` is now a required
FK (BR-BRA-01), so every Depense-based reporting helper below gains an
optional `branche` (Vue par Branche when given, Vue Globale — summed across
all branches — when omitted, per §3.5.5). `Employe.branche` is instead a
*derived* property read from `employe.batiment.branche` (BR-BRA-09) — it has
no column of its own — so every Employe-linked queryset below (Pointage,
CongeEmploye, AcompteEmploye, BulletinPaie) is filtered via the join
`employe__batiment__branche` rather than a stored field. `Associe` and
`RetraitAssocie` are the one family left untouched: per BR-BRA-08 they are
intentionally NOT branch-scoped — equity withdrawals belong to the company
as a whole and are always shown at their full, company-wide total regardless
of which branch is selected (§3.5.6) — so `get_retraits_associes_summary`
takes no `branche` parameter, and `total_retraits_associes` in
`get_cash_flow_summary` is never filtered by it either.
"""

from decimal import Decimal, ROUND_HALF_UP
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §20.6 — Cash Flow Summary
# ---------------------------------------------------------------------------


def get_cash_flow_summary(date_debut=None, date_fin=None, branche=None) -> dict:
    """
    Compute a period-based cash-flow statement:

      Inflows  = client payments (PaiementClient.montant) received in period
      Outflows = supplier settlements (ReglementFournisseur.montant)
               + operational expenses (Depense.montant)
               + stakeholder withdrawals (RetraitAssocie.montant) — BR-ASSOC-01
               + payroll cash paid: AcompteEmploye.montant (on date given)
                 + BulletinPaie.montant_net where statut=paye (on date_paiement)
               paid in period
      Net      = inflows − outflows

    Filtering is inclusive on both bounds.  When a bound is None, no date
    restriction is applied on that side.

    Acomptes and payslip net amounts are NOT double-counted: an acompte is
    an outflow on the day it is handed out, and the payslip's montant_net
    already excludes whatever acomptes were deducted from it (BR-RH-04).

    v1.4 (§3.5.5): pass `branche` for the Vue par Branche cash-flow exactly
    as that branch's chef de branche sees it — client payments, supplier
    settlements, and dépenses are filtered on their own `branche` FK
    (BR-BRA-01); payroll cash (acomptes/bulletins) is filtered via the
    derived `employe__batiment__branche` join (BR-BRA-09). Omit `branche`
    for the Vue Globale figures, summed across every branch.

    `RetraitAssocie` is the one exception (BR-BRA-08): stakeholder
    withdrawals are never branch-scoped, so `total_retraits_associes` /
    `detail_retraits` always reflect the full company-wide total regardless
    of `branche` — a chef de branche's cash-flow report still shows the
    real equity draw rather than a fictional per-branch share of it.

    Args:
        date_debut (date | None): Start of period (inclusive).
        date_fin   (date | None): End of period (inclusive).
        branche (Branche | None): Scope to one branch; omit for Vue Globale.
            Has no effect on stakeholder withdrawals (BR-BRA-08).

    Returns dict with keys:
        date_debut           (date | None)
        date_fin             (date | None)
        total_encaissements  (Decimal) — client payments received
        total_reglements_fournisseurs (Decimal) — supplier settlements paid
        total_depenses       (Decimal) — operational expenses paid
        total_retraits_associes (Decimal) — stakeholder withdrawals (BR-ASSOC-01)
        total_acomptes_employes (Decimal) — salary advances paid
        total_salaires_payes (Decimal) — payslip net amounts actually paid
        total_paie            (Decimal) — total_acomptes_employes + total_salaires_payes
        total_sorties        (Decimal) — reglements + depenses + retraits + paie
        solde_net            (Decimal) — encaissements − sorties
        detail_paiements     (queryset) — PaiementClient rows in period
        detail_reglements    (queryset) — ReglementFournisseur rows in period
        detail_depenses      (queryset) — Depense rows in period
        detail_retraits      (queryset) — RetraitAssocie rows in period
        detail_acomptes      (queryset) — AcompteEmploye rows in period
        detail_bulletins_payes (queryset) — BulletinPaie (statut=paye) in period
        par_mode_encaissement (list[dict]) — inflows grouped by payment method
        par_mode_sortie       (list[dict]) — all outflows grouped by method
    """
    from clients.models import PaiementClient
    from achats.models import ReglementFournisseur
    from depenses.models import Depense, RetraitAssocie, AcompteEmploye, BulletinPaie
    from django.db.models import Sum

    # ── Client payments (inflows) ─────────────────────────────────────────
    paiements_qs = PaiementClient.objects.select_related("client")
    if date_debut:
        paiements_qs = paiements_qs.filter(date_paiement__gte=date_debut)
    if date_fin:
        paiements_qs = paiements_qs.filter(date_paiement__lte=date_fin)
    if branche is not None:
        paiements_qs = paiements_qs.filter(branche=branche)

    total_encaissements = paiements_qs.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0")

    # ── Supplier settlements (outflows) ──────────────────────────────────
    reglements_qs = ReglementFournisseur.objects.select_related("fournisseur")
    if date_debut:
        reglements_qs = reglements_qs.filter(date_reglement__gte=date_debut)
    if date_fin:
        reglements_qs = reglements_qs.filter(date_reglement__lte=date_fin)
    if branche is not None:
        reglements_qs = reglements_qs.filter(branche=branche)

    total_reglements = reglements_qs.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0")

    # ── Operational expenses (outflows) ──────────────────────────────────
    depenses_qs = Depense.objects.select_related("categorie")
    if date_debut:
        depenses_qs = depenses_qs.filter(date__gte=date_debut)
    if date_fin:
        depenses_qs = depenses_qs.filter(date__lte=date_fin)
    if branche is not None:
        depenses_qs = depenses_qs.filter(branche=branche)

    total_depenses = depenses_qs.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

    # ── Stakeholder withdrawals (outflows) — BR-ASSOC-01 ──────────────────
    # v1.4 / BR-BRA-08: never filtered by `branche` — withdrawals are
    # company-wide equity draws, always shown at their full total regardless
    # of which branch's cash-flow report is being viewed.
    retraits_qs = RetraitAssocie.objects.select_related("associe")
    if date_debut:
        retraits_qs = retraits_qs.filter(date__gte=date_debut)
    if date_fin:
        retraits_qs = retraits_qs.filter(date__lte=date_fin)

    total_retraits = retraits_qs.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )

    # ── Payroll cash out (outflows) — BR-RH-04 ────────────────────────────
    # v1.4 / BR-BRA-09: Employe.branche is derived from employe.batiment, so
    # scoping goes through the employe__batiment__branche join rather than a
    # stored field on AcompteEmploye/BulletinPaie themselves.
    acomptes_qs = AcompteEmploye.objects.select_related("employe")
    if date_debut:
        acomptes_qs = acomptes_qs.filter(date__gte=date_debut)
    if date_fin:
        acomptes_qs = acomptes_qs.filter(date__lte=date_fin)
    if branche is not None:
        acomptes_qs = acomptes_qs.filter(employe__batiment__branche=branche)

    total_acomptes_employes = acomptes_qs.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0")

    bulletins_payes_qs = BulletinPaie.objects.select_related("employe").filter(
        statut=BulletinPaie.STATUT_PAYE
    )
    if date_debut:
        bulletins_payes_qs = bulletins_payes_qs.filter(date_paiement__gte=date_debut)
    if date_fin:
        bulletins_payes_qs = bulletins_payes_qs.filter(date_paiement__lte=date_fin)
    if branche is not None:
        bulletins_payes_qs = bulletins_payes_qs.filter(
            employe__batiment__branche=branche
        )

    total_salaires_payes = bulletins_payes_qs.aggregate(total=Sum("montant_net"))[
        "total"
    ] or Decimal("0")

    total_paie = total_acomptes_employes + total_salaires_payes

    total_sorties = total_reglements + total_depenses + total_retraits + total_paie
    solde_net = total_encaissements - total_sorties

    # ── Group inflows by payment method ──────────────────────────────────
    par_mode_encaissement = _grouper_par_mode(paiements_qs, "mode_paiement", "montant")

    # ── Group all outflows by payment method ───────────────────────────────
    par_mode_sortie = _grouper_par_mode_mixte(
        [
            (reglements_qs, "mode_paiement", "montant"),
            (depenses_qs, "mode_paiement", "montant"),
            (retraits_qs, "mode_paiement", "montant"),
            (acomptes_qs, "mode_paiement", "montant"),
            (bulletins_payes_qs, "mode_paiement", "montant_net"),
        ]
    )

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
        "total_retraits_associes": total_retraits,
        "total_acomptes_employes": total_acomptes_employes,
        "total_salaires_payes": total_salaires_payes,
        "total_paie": total_paie,
        "total_sorties": total_sorties,
        "solde_net": solde_net,
        "detail_paiements": paiements_qs.order_by("-date_paiement"),
        "detail_reglements": reglements_qs.order_by("-date_reglement"),
        "detail_depenses": depenses_qs.order_by("-date"),
        "detail_retraits": retraits_qs.order_by("-date"),
        "detail_acomptes": acomptes_qs.order_by("-date"),
        "detail_bulletins_payes": bulletins_payes_qs.order_by("-date_paiement"),
        "par_mode_encaissement": par_mode_encaissement,
        "par_mode_sortie": par_mode_sortie,
    }


# ---------------------------------------------------------------------------
# Expense breakdown by category
# ---------------------------------------------------------------------------


def get_depenses_par_categorie(
    date_debut=None, date_fin=None, branche=None
) -> list[dict]:
    """
    Aggregate expenses by CategorieDepense for a given period.

    Returns a list of dicts sorted by total descending:
        categorie    — CategorieDepense instance
        total        — Decimal total DZD for the period
        nb           — count of individual expense records
        pct          — percentage of grand total (0–100, rounded to 1 dp)

    v1.4 (§3.5.5): pass `branche` for the Vue par Branche breakdown — a chef
    de branche's own expenses only (BR-BRA-01, `Depense.branche` is a
    required FK); omit for Vue Globale, summed across every branch.

    Args:
        date_debut (date | None): Period start (inclusive).
        date_fin   (date | None): Period end (inclusive).
        branche (Branche | None): Scope to one branch; omit for Vue Globale.
    """
    from depenses.models import Depense, CategorieDepense
    from django.db.models import Sum, Count

    qs = Depense.objects.all()
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)
    if branche is not None:
        qs = qs.filter(branche=branche)

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
        result.append(
            {
                "categorie": categorie_map.get(row["categorie_id"]),
                "total": total,
                "nb": row["nb"],
                "pct": pct,
            }
        )

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

    v1.4 (BR-BRA-01): no `branche` parameter is needed here — a lot belongs
    to exactly one branche, and `Depense.clean()` already guards that any
    dépense attributed to it shares that same branche, so `lot.depenses` is
    inherently single-branch.

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


def get_depenses_summary(date_debut=None, date_fin=None, branche=None) -> dict:
    """
    Return high-level expense statistics for a period.

    Useful for the management dashboard and for the expense list header.

    v1.4 (§3.5.5): pass `branche` for the Vue par Branche figures (BR-BRA-01);
    omit for Vue Globale, summed across every branch.

    Args:
        date_debut (date | None): Period start (inclusive).
        date_fin   (date | None): Period end (inclusive).
        branche (Branche | None): Scope to one branch; omit for Vue Globale.

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
    if branche is not None:
        qs = qs.filter(branche=branche)

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

    par_categorie = get_depenses_par_categorie(date_debut, date_fin, branche)

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


def _grouper_par_mode_mixte(sources) -> list[dict]:
    """
    Combine several querysets (each a different outflow source) into a
    single breakdown by payment method.

    Args:
        sources: iterable of (queryset, mode_field, montant_field) tuples.
                 All sources are assumed to share a compatible mode_paiement
                 vocabulary (especes, cheque, virement, etc.) — true for
                 Depense, ReglementFournisseur, RetraitAssocie, AcompteEmploye
                 and BulletinPaie in this codebase.

    Returns list[dict] sorted by total descending:
        mode   — raw choice value
        label  — human-readable label
        total  — Decimal combined total
        nb     — total count of records
    """
    from django.db.models import Sum, Count

    combined: dict[str, dict] = {}

    def _merge(qs, mode_field, montant_field):
        rows = qs.values(mode_field).annotate(total=Sum(montant_field), nb=Count("pk"))
        for row in rows:
            mode = row[mode_field]
            total = row["total"] or Decimal("0")
            nb = row["nb"]
            if mode not in combined:
                combined[mode] = {"total": Decimal("0"), "nb": 0}
            combined[mode]["total"] += total
            combined[mode]["nb"] += nb

    for qs, mode_field, montant_field in sources:
        _merge(qs, mode_field, montant_field)

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


# ---------------------------------------------------------------------------
# Associés — withdrawals summary  (BR-ASSOC-01)
# ---------------------------------------------------------------------------


def get_retraits_associes_summary(date_debut=None, date_fin=None) -> dict:
    """
    Aggregate stakeholder withdrawals for a period, with a per-stakeholder
    breakdown (history of withdrawals, per BR-ASSOC-01).

    v1.4 / BR-BRA-08: unlike every other reporting helper in this module,
    this one takes NO `branche` parameter — Associé/RetraitAssocie are
    intentionally not branch-scoped (equity belongs to the company as a
    whole, §3.5.6), so this summary is always company-wide and is shown
    identically regardless of which branch is currently selected.

    Returns dict with keys:
        total          — Decimal total DZD withdrawn in period
        nb             — int count of withdrawal records
        par_associe    — list[dict] {associe, total, nb, pct} sorted desc
        par_mode       — list[dict] breakdown by payment method
    """
    from depenses.models import RetraitAssocie, Associe
    from django.db.models import Sum, Count

    qs = RetraitAssocie.objects.all()
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    agg = qs.aggregate(total=Sum("montant"), nb=Count("pk"))
    total = agg["total"] or Decimal("0")
    nb = agg["nb"] or 0

    aggregated = (
        qs.values("associe_id")
        .annotate(total=Sum("montant"), nb=Count("pk"))
        .order_by("-total")
    )
    associe_map = {
        a.pk: a
        for a in Associe.objects.filter(
            pk__in=[row["associe_id"] for row in aggregated]
        )
    }

    par_associe = []
    for row in aggregated:
        row_total = row["total"] or Decimal("0")
        pct = round(float(row_total / total * 100), 1) if total else 0.0
        par_associe.append(
            {
                "associe": associe_map.get(row["associe_id"]),
                "total": row_total,
                "nb": row["nb"],
                "pct": pct,
            }
        )

    par_mode = _grouper_par_mode(qs, "mode_paiement", "montant")

    return {
        "total": total,
        "nb": nb,
        "par_associe": par_associe,
        "par_mode": par_mode,
    }


# ---------------------------------------------------------------------------
# RH — Leave balance  (BR-RH-03)
# ---------------------------------------------------------------------------


def get_solde_conge(employe, as_of=None) -> Decimal:
    """
    Compute an employee's paid-leave balance as of a given date.

      accrued = anciennete_mois × 2.5  (CONGE_JOURS_PAR_MOIS)
      taken   = sum(CongeEmploye.nb_jours) for blocks starting on/before as_of
      balance = accrued − taken   (never negative)

    v1.4 (BR-BRA-09): no `branche` parameter is needed — this operates on a
    single `employe`, whose branch is already pinned via `employe.branche`
    (derived from their assigned bâtiment).

    Args:
        employe (Employe)
        as_of (date | None): defaults to today.

    Returns:
        Decimal: leave days available, rounded to 1 decimal place.
    """
    from depenses.models import CongeEmploye, CONGE_JOURS_PAR_MOIS
    from django.db.models import Sum

    as_of = as_of or datetime.date.today()

    accrued = Decimal(employe.anciennete_mois(as_of)) * CONGE_JOURS_PAR_MOIS

    taken = (
        CongeEmploye.objects.filter(employe=employe, date_debut__lte=as_of).aggregate(
            total=Sum("nb_jours")
        )["total"]
        or 0
    )

    balance = accrued - Decimal(taken)
    return balance if balance > 0 else Decimal("0.0")


def appliquer_conge_aux_pointages(conge) -> int:
    """
    Materialize a CongeEmploye block into Pointage rows (BR-RH-05): for
    every day in [date_debut, date_fin] that is NOT the employee's
    scheduled rest day, create or update the Pointage row to statut=conge.

    The employee's rest day is left untouched (it is not a leave day —
    it was never going to be worked anyway).

    Returns the number of Pointage rows created/updated.
    """
    from depenses.models import Pointage

    employe = conge.employe
    jour = conge.date_debut
    touched = 0
    while jour <= conge.date_fin:
        if jour.weekday() != employe.jour_repos_habituel:
            Pointage.objects.update_or_create(
                employe=employe,
                date=jour,
                defaults={"statut": Pointage.STATUT_CONGE, "heures_supplementaires": 0},
            )
            touched += 1
        jour += datetime.timedelta(days=1)

    logger.info(
        "appliquer_conge_aux_pointages: conge pk=%s (employe=%s) → %s jours marqués 'congé'.",
        conge.pk,
        employe.matricule,
        touched,
    )
    return touched


# ---------------------------------------------------------------------------
# RH — Payroll calculation  (BR-RH-02 / BR-RH-05)
# ---------------------------------------------------------------------------


def calculer_donnees_paie(employe, annee: int, mois: int) -> dict:
    """
    Compute (without persisting) the figures for an employee's payslip for
    a given calendar month, from their Pointage rows.

      taux_journalier      = salaire_base_mensuel / 25          (BR-RH-02)
      montant_brut         = taux_journalier × (jours_presence + jours_conge)
                            + montant_heures_sup
      montant_heures_sup    = total_heures_sup × taux_horaire × taux_majoration
      total_acomptes        = sum of this employee's AcompteEmploye not yet
                              linked to a payslip (bulletin_paie is null),
                              dated within the month
      montant_net           = montant_brut − total_acomptes

    STATUT_ABSENT days simply contribute nothing (no line item needed);
    STATUT_REPOS days are excluded from both pay and deductions.

    Returns a dict matching BulletinPaie's snapshot fields, plus
    'acomptes_a_deduire' (queryset) for the caller to link once saved.
    """
    from calendar import monthrange
    from depenses.models import Pointage, AcompteEmploye
    from django.db.models import Sum, Count

    premier_jour = datetime.date(annee, mois, 1)
    dernier_jour = datetime.date(annee, mois, monthrange(annee, mois)[1])

    pointages_qs = Pointage.objects.filter(
        employe=employe, date__gte=premier_jour, date__lte=dernier_jour
    )

    par_statut = dict(
        pointages_qs.values("statut")
        .annotate(nb=Count("pk"))
        .values_list("statut", "nb")
    )
    jours_presence = par_statut.get(Pointage.STATUT_PRESENT, 0)
    jours_absence = par_statut.get(Pointage.STATUT_ABSENT, 0)
    jours_repos = par_statut.get(Pointage.STATUT_REPOS, 0)
    jours_conge = par_statut.get(Pointage.STATUT_CONGE, 0)

    total_heures_sup = pointages_qs.aggregate(total=Sum("heures_supplementaires"))[
        "total"
    ] or Decimal("0.00")

    taux_journalier = employe.taux_journalier
    taux_horaire = employe.taux_horaire

    montant_heures_sup = (
        total_heures_sup * taux_horaire * employe.taux_majoration_heure_sup
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    montant_brut = (
        taux_journalier * Decimal(jours_presence + jours_conge) + montant_heures_sup
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Unlinked acomptes for this employee within the month (BR-RH-04).
    acomptes_a_deduire = AcompteEmploye.objects.filter(
        employe=employe,
        bulletin_paie__isnull=True,
        date__gte=premier_jour,
        date__lte=dernier_jour,
    )
    total_acomptes = acomptes_a_deduire.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0.00")

    montant_net = montant_brut - total_acomptes

    return {
        "jours_presence": jours_presence,
        "jours_absence": jours_absence,
        "jours_repos": jours_repos,
        "jours_conge": jours_conge,
        "total_heures_supplementaires": total_heures_sup,
        "salaire_base_reference": employe.salaire_base_mensuel,
        "taux_journalier": taux_journalier,
        "montant_heures_sup": montant_heures_sup,
        "montant_brut": montant_brut,
        "total_acomptes": total_acomptes,
        "montant_net": montant_net,
        "acomptes_a_deduire": acomptes_a_deduire,
    }


# ---------------------------------------------------------------------------
# RH — Period summary (dashboard)
# ---------------------------------------------------------------------------


def get_rh_summary(date_debut=None, date_fin=None, branche=None) -> dict:
    """
    High-level payroll statistics for a period — RH dashboard.

    v1.4 (§3.5.5 / BR-BRA-09): an employee's branch is derived from their
    assigned bâtiment, not stored on Employe itself, so every queryset below
    is scoped via the `employe__batiment__branche` join (or `batiment__branche`
    directly on Employe) rather than a stored `branche` field. Pass `branche`
    for the Vue par Branche payroll summary — what a chef de branche sees for
    their own employees only; omit for Vue Globale, the company-wide summary
    available to admin/comptable per BR-BRA-09.

    Args:
        date_debut (date | None): Period start (inclusive).
        date_fin   (date | None): Period end (inclusive).
        branche (Branche | None): Scope to one branch; omit for Vue Globale.

    Returns dict with keys:
        total_salaires_payes   — Decimal, sum of BulletinPaie.montant_net (statut=paye)
        nb_bulletins_payes     — int
        total_acomptes         — Decimal, sum of AcompteEmploye in period
        nb_acomptes            — int
        nb_employes_actifs     — int
        bulletins_en_attente   — int, brouillon/valide not yet paid
    """
    from depenses.models import BulletinPaie, AcompteEmploye, Employe
    from django.db.models import Sum, Count

    bulletins_qs = BulletinPaie.objects.filter(statut=BulletinPaie.STATUT_PAYE)
    if date_debut:
        bulletins_qs = bulletins_qs.filter(date_paiement__gte=date_debut)
    if date_fin:
        bulletins_qs = bulletins_qs.filter(date_paiement__lte=date_fin)
    if branche is not None:
        bulletins_qs = bulletins_qs.filter(employe__batiment__branche=branche)

    agg_bulletins = bulletins_qs.aggregate(total=Sum("montant_net"), nb=Count("pk"))

    acomptes_qs = AcompteEmploye.objects.all()
    if date_debut:
        acomptes_qs = acomptes_qs.filter(date__gte=date_debut)
    if date_fin:
        acomptes_qs = acomptes_qs.filter(date__lte=date_fin)
    if branche is not None:
        acomptes_qs = acomptes_qs.filter(employe__batiment__branche=branche)

    agg_acomptes = acomptes_qs.aggregate(total=Sum("montant"), nb=Count("pk"))

    employes_actifs_qs = Employe.objects.filter(actif=True)
    bulletins_en_attente_qs = BulletinPaie.objects.exclude(
        statut=BulletinPaie.STATUT_PAYE
    )
    if branche is not None:
        employes_actifs_qs = employes_actifs_qs.filter(batiment__branche=branche)
        bulletins_en_attente_qs = bulletins_en_attente_qs.filter(
            employe__batiment__branche=branche
        )

    return {
        "total_salaires_payes": agg_bulletins["total"] or Decimal("0"),
        "nb_bulletins_payes": agg_bulletins["nb"] or 0,
        "total_acomptes": agg_acomptes["total"] or Decimal("0"),
        "nb_acomptes": agg_acomptes["nb"] or 0,
        "nb_employes_actifs": employes_actifs_qs.count(),
        "bulletins_en_attente": bulletins_en_attente_qs.count(),
    }
