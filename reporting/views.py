"""
reporting/views.py

Comprehensive reporting views for the Élevage Avicole management system.

Reports implemented (spec §21 / §9.12):
  20.1  Balance Fournisseur par Ancienneté   (supplier aged-debt breakdown)
  20.2  Historique des Règlements            (settlement history per supplier)
  20.3  Répartition des Règlements           (payment distribution summary)
  20.4  Dettes en Cours par Fournisseur      (live supplier debt dashboard)
  20.5  Rentabilité par Lot                  (lot profitability)
  20.6  Résumé de Trésorerie                 (cash flow summary)
  20.7  État des Stocks                      (stock status snapshot)
        Consommation par Lot                 (feed/medicine consumption detail)
        Créances Clients                     (client receivables aging)
        Historique BL Clients               (client delivery note log)

All views:
  - GET only (read-only reports)
  - Decorated with @login_required; admin-only reports additionally check role
  - Support CSV export via ?export=csv query parameter
  - Use utility functions from achats.utils, clients.utils, elevage.utils,
    depenses.utils, and stock.utils rather than duplicating aggregation logic

Access levels (spec §9.12):
  Admin        → all reports
  Manager      → all reports
  Comptable    → financial reports (aging, cash flow, créances)
  Opérateur    → stock status, consommation, BL client history

v1.4 — Multi-Branch Architecture (§3.5.5): every report gains an implicit
Vue par Branche / Vue Globale toggle. `core.views.get_active_branche`
resolves the request's active branche exactly as every other app does
(`None` == Vue Globale). Vue par Branche shows the same figures a chef de
branche already sees for their own branche; Vue Globale aggregates across
every branche, with `active_branche`/`vue_globale` passed to every template
so the report header can render the toggle/breadcrumb. Reporting utility
functions that already accept an optional `branche` kwarg (achats.utils,
clients.utils, depenses.utils, production.utils) are called with it
directly; manual querysets are filtered with `.filter(branche=branche)` for
models carrying a real `branche` FK, or via the appropriate join
(`lot__branche`, `employe__batiment__branche`, …) for the handful of models
where `branche` is a derived property rather than a stored column
(BR-BRA-09 for RH, the elevage event models). Per BR-BRA-08, the Retraits
Associés report (`rapport_retraits_associes`) is the one deliberate
exception — it stays company-wide regardless of the active branche, since
Associés/RetraitAssocie are never branch-scoped.
"""

import csv
import datetime
import json
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, F, Max, Min, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.views import branche_object_or_404, get_active_branche

logger = logging.getLogger(__name__)

LOGIN_URL = "core:login"
PER_PAGE = 50  # reports show more rows per page than operational views


# ---------------------------------------------------------------------------
# Role-access helpers
# ---------------------------------------------------------------------------

ADMIN_ROLES = {"admin", "manager"}
FINANCIAL_ROLES = {"admin", "manager", "comptable"}
ALL_ROLES = {"admin", "manager", "comptable", "operateur"}


def _get_role(user):
    """Return the user's role string, or 'operateur' if profile is absent."""
    try:
        return user.profile.role
    except Exception:
        return "operateur"


def _require_role(request, allowed_roles: set):
    """
    Return True if the current user's role is in *allowed_roles*.
    Adds an error message and returns False otherwise — the caller should
    redirect to the reporting dashboard.
    """
    if _get_role(request.user) in allowed_roles:
        return True
    messages.error(
        request,
        "ليس لديك الصلاحيات اللازمة للوصول إلى هذا التقرير.",
    )
    return False


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------


def _paginate(qs, page_number, per_page=PER_PAGE):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


# ---------------------------------------------------------------------------
# Date-range parse helper
# ---------------------------------------------------------------------------


def _parse_dates(request):
    """
    Parse ?date_debut=YYYY-MM-DD and ?date_fin=YYYY-MM-DD from GET params.
    Returns (date_debut, date_fin) — each is a datetime.date or None.
    """
    fmt = "%Y-%m-%d"
    date_debut = date_fin = None
    try:
        raw = request.GET.get("date_debut", "").strip()
        if raw:
            date_debut = datetime.datetime.strptime(raw, fmt).date()
    except ValueError:
        pass
    try:
        raw = request.GET.get("date_fin", "").strip()
        if raw:
            date_fin = datetime.datetime.strptime(raw, fmt).date()
    except ValueError:
        pass
    return date_debut, date_fin


# ---------------------------------------------------------------------------
# CSV response helper
# ---------------------------------------------------------------------------


def _csv_response(filename: str, headers: list, rows):
    """
    Build and return an HttpResponse that downloads a CSV file.

    Args:
        filename: Download filename (without .csv extension).
        headers:  Column header labels.
        rows:     Iterable of row iterables (strings / numbers).
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return response


# ===========================================================================
# Reporting Dashboard
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def reporting_dashboard(request):
    """
    Central hub linking all available reports.
    Filters the visible report cards by the user's role.

    v1.4 (§3.5.5): the header KPIs are scoped to the request's active
    branche (Vue par Branche); admin/comptable in Vue Globale
    (`branche is None`) see every branche's figures combined.
    """
    role = _get_role(request.user)
    today = datetime.date.today()
    branche = get_active_branche(request)
    vue_globale = branche is None

    # Quick KPIs for the dashboard header
    from achats.models import FactureFournisseur
    from clients.models import FactureClient
    from stock.models import StockIntrant

    factures_fournisseur_qs = FactureFournisseur.objects.filter(
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ],
    )
    factures_client_qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    )
    stock_intrant_qs = StockIntrant.objects.filter(
        quantite__lte=F("intrant__seuil_alerte"),
        quantite__gt=0,
    )
    if branche is not None:
        factures_fournisseur_qs = factures_fournisseur_qs.filter(branche=branche)
        factures_client_qs = factures_client_qs.filter(branche=branche)
        stock_intrant_qs = stock_intrant_qs.filter(branche=branche)

    nb_factures_retard_fournisseur = factures_fournisseur_qs.filter(
        date_echeance__lt=today
    ).count()
    nb_factures_retard_client = factures_client_qs.filter(
        date_echeance__lt=today
    ).count()
    nb_stocks_alerte = stock_intrant_qs.count()

    from elevage.models import LotElevage
    from django.db.models import Sum as _Sum

    lots_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
    if branche is not None:
        lots_qs = lots_qs.filter(branche=branche)
    nb_lots_ouverts = lots_qs.count()

    dette_totale = (
        factures_fournisseur_qs.aggregate(total=_Sum("reste_a_payer"))["total"] or 0
    )

    creances_totale = (
        factures_client_qs.aggregate(total=_Sum("reste_a_payer"))["total"] or 0
    )

    # Respect GET params so the period picker persists after Actualiser
    _dd, _df = _parse_dates(request)
    date_debut_default = _dd if _dd else today - datetime.timedelta(days=365)
    date_fin_default = _df if _df else today

    return render(
        request,
        "reporting/dashboard.html",
        {
            "title": "التقارير ولوحات التحكم",
            "role": role,
            "is_financial": role in FINANCIAL_ROLES,
            "is_admin": role in ADMIN_ROLES,
            "nb_factures_retard_fournisseur": nb_factures_retard_fournisseur,
            "nb_factures_retard_client": nb_factures_retard_client,
            "nb_stocks_alerte": nb_stocks_alerte,
            "nb_lots_ouverts": nb_lots_ouverts,
            "dette_totale": dette_totale,
            "creances_totale": creances_totale,
            "date_debut_default": date_debut_default,
            "date_fin_default": date_fin_default,
            "today": today,
            "active_branche": branche,
            "vue_globale": vue_globale,
        },
    )


# ===========================================================================
# 20.1 — Balance Fournisseur par Ancienneté
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_supplier_aging(request):
    """
    Aged-debt breakdown by supplier.

    Buckets:  current (not yet due) | 1–30 | 31–60 | 61–90 | 90+ days overdue.
    Filters:  ?fournisseur=<pk>
    Export:   ?export=csv

    Access: FINANCIAL_ROLES
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from achats.utils import get_supplier_aging_buckets
    from intrants.models import Fournisseur

    branche = get_active_branche(request)
    fournisseur_pk = request.GET.get("fournisseur", "").strip()
    fournisseur_obj = None
    if fournisseur_pk:
        fournisseur_obj = get_object_or_404(Fournisseur, pk=fournisseur_pk)

    buckets = get_supplier_aging_buckets(fournisseur=fournisseur_obj, branche=branche)

    # Column totals
    totaux = {
        "current": sum(b["current"] for b in buckets),
        "1_30": sum(b["1_30"] for b in buckets),
        "31_60": sum(b["31_60"] for b in buckets),
        "61_90": sum(b["61_90"] for b in buckets),
        "over_90": sum(b["over_90"] for b in buckets),
        "total": sum(b["total"] for b in buckets),
    }

    if request.GET.get("export") == "csv":
        headers = [
            "Fournisseur",
            "Courant (non échu)",
            "1–30 jours",
            "31–60 jours",
            "61–90 jours",
            "> 90 jours",
            "Total",
        ]
        rows = [
            [
                b["fournisseur"].nom,
                b["current"],
                b["1_30"],
                b["31_60"],
                b["61_90"],
                b["over_90"],
                b["total"],
            ]
            for b in buckets
        ]
        rows.append(
            [
                "TOTAL",
                totaux["current"],
                totaux["1_30"],
                totaux["31_60"],
                totaux["61_90"],
                totaux["over_90"],
                totaux["total"],
            ]
        )
        return _csv_response("balance_fournisseur_anciennete", headers, rows)

    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    chart_json = json.dumps(
        {
            "labels": [b["fournisseur"].nom for b in buckets],
            "current": [float(b["current"]) for b in buckets],
            "d30": [float(b["1_30"]) for b in buckets],
            "d60": [float(b["31_60"]) for b in buckets],
            "d90": [float(b["61_90"]) for b in buckets],
            "over90": [float(b["over_90"]) for b in buckets],
            "totaux": {
                "current": float(totaux["current"]),
                "d30": float(totaux["1_30"]),
                "d60": float(totaux["31_60"]),
                "d90": float(totaux["61_90"]),
                "over90": float(totaux["over_90"]),
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/supplier_aging.html",
        {
            "title": "رصيد المورد حسب الأقدمية",
            "buckets": buckets,
            "totaux": totaux,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "fournisseur_obj": fournisseur_obj,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.2 — Historique des Règlements
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_historique_reglements(request):
    """
    Chronological settlement history with per-reglement allocation detail.

    Filters:  ?fournisseur=<pk>  ?date_debut=  ?date_fin=  ?mode=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from achats.models import AllocationReglement, ReglementFournisseur
    from intrants.models import Fournisseur

    branche = get_active_branche(request)
    qs = ReglementFournisseur.objects.select_related(
        "fournisseur", "created_by"
    ).order_by("-date_reglement", "-created_at")
    if branche is not None:
        qs = qs.filter(branche=branche)

    fournisseur_pk = request.GET.get("fournisseur", "").strip()
    if fournisseur_pk:
        qs = qs.filter(fournisseur_id=fournisseur_pk)

    mode = request.GET.get("mode", "").strip()
    if mode:
        qs = qs.filter(mode_paiement=mode)

    date_debut, date_fin = _parse_dates(request)
    if date_debut:
        qs = qs.filter(date_reglement__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_reglement__lte=date_fin)

    # Aggregate totals for the filtered set
    totaux = qs.aggregate(total=Sum("montant"), nb=Count("pk"))

    if request.GET.get("export") == "csv":
        # Flatten: one row per allocation
        allocations = (
            AllocationReglement.objects.filter(reglement__in=qs)
            .select_related("reglement__fournisseur", "facture")
            .order_by("-reglement__date_reglement", "reglement__pk")
        )
        headers = [
            "Date règlement",
            "Fournisseur",
            "Montant règlement (DZD)",
            "Mode",
            "Référence",
            "Facture imputée",
            "Montant alloué (DZD)",
        ]
        rows = [
            [
                a.reglement.date_reglement,
                a.reglement.fournisseur.nom,
                a.reglement.montant,
                a.reglement.get_mode_paiement_display(),
                a.reglement.reference_paiement,
                a.facture.reference,
                a.montant_alloue,
            ]
            for a in allocations
        ]
        return _csv_response("historique_reglements", headers, rows)

    page = _paginate(qs, request.GET.get("page"))
    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")

    # Pre-fetch allocations for the current page to avoid N+1
    reglement_ids = [r.pk for r in page.object_list]
    allocations_map: dict = {}
    for alloc in AllocationReglement.objects.filter(
        reglement_id__in=reglement_ids
    ).select_related("facture"):
        allocations_map.setdefault(alloc.reglement_id, []).append(alloc)

    alloc_json = json.dumps(
        {
            str(reg_id): [
                {"facture": a.facture.reference, "montant": float(a.montant_alloue)}
                for a in allocs
            ]
            for reg_id, allocs in allocations_map.items()
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/historique_reglements.html",
        {
            "title": "سجل التسويات",
            "page": page,
            "totaux": totaux,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "mode": mode,
            "mode_choices": ReglementFournisseur.MODE_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "alloc_json": alloc_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.3 — Répartition des Règlements
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_repartition_reglements(request):
    """
    Distribution summary: how settlements were spread across invoices in a
    given period — grouped by supplier, invoice, and payment method.

    Filters:  ?fournisseur=<pk>  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from achats.models import AllocationReglement, ReglementFournisseur
    from intrants.models import Fournisseur

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    fournisseur_pk = request.GET.get("fournisseur", "").strip()

    # Base: allocations whose règlement falls in the period
    qs = AllocationReglement.objects.select_related(
        "reglement__fournisseur",
        "facture",
    )
    if branche is not None:
        qs = qs.filter(reglement__branche=branche)
    if date_debut:
        qs = qs.filter(reglement__date_reglement__gte=date_debut)
    if date_fin:
        qs = qs.filter(reglement__date_reglement__lte=date_fin)
    if fournisseur_pk:
        qs = qs.filter(reglement__fournisseur_id=fournisseur_pk)

    qs = qs.order_by(
        "reglement__fournisseur__nom",
        "reglement__date_reglement",
        "facture__reference",
    )

    # Aggregated totals per supplier
    supplier_totals = (
        qs.values("reglement__fournisseur__nom")
        .annotate(total_alloue=Sum("montant_alloue"), nb=Count("pk"))
        .order_by("reglement__fournisseur__nom")
    )

    # Totals per mode
    mode_totals = (
        qs.values("reglement__mode_paiement")
        .annotate(total=Sum("montant_alloue"), nb=Count("pk"))
        .order_by("-total")
    )
    mode_label_map = dict(ReglementFournisseur.MODE_CHOICES)
    mode_totals_display = [
        {
            "mode": row["reglement__mode_paiement"],
            "label": mode_label_map.get(
                row["reglement__mode_paiement"], row["reglement__mode_paiement"]
            ),
            "total": row["total"],
            "nb": row["nb"],
        }
        for row in mode_totals
    ]

    grand_total = qs.aggregate(total=Sum("montant_alloue"))["total"] or Decimal("0")

    if request.GET.get("export") == "csv":
        headers = [
            "Fournisseur",
            "Date règlement",
            "Mode",
            "Référence règlement",
            "Facture",
            "Montant alloué (DZD)",
        ]
        rows = [
            [
                a.reglement.fournisseur.nom,
                a.reglement.date_reglement,
                a.reglement.get_mode_paiement_display(),
                a.reglement.reference_paiement,
                a.facture.reference,
                a.montant_alloue,
            ]
            for a in qs
        ]
        return _csv_response("repartition_reglements", headers, rows)

    fournisseurs = Fournisseur.objects.filter(actif=True).order_by("nom")
    page = _paginate(qs, request.GET.get("page"))

    supplier_totals_list = list(supplier_totals)
    chart_json = json.dumps(
        {
            "modes": {
                "labels": [m["label"] for m in mode_totals_display],
                "values": [float(m["total"]) for m in mode_totals_display],
            },
            "suppliers": {
                "labels": [
                    s["reglement__fournisseur__nom"] for s in supplier_totals_list
                ],
                "values": [float(s["total_alloue"]) for s in supplier_totals_list],
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/repartition_reglements.html",
        {
            "title": "توزيع التسويات",
            "page": page,
            "supplier_totals": supplier_totals_list,
            "mode_totals": mode_totals_display,
            "grand_total": grand_total,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.4 — Dettes en Cours par Fournisseur
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_dettes_fournisseurs(request):
    """
    Live dashboard: total debt, open invoice count, oldest unpaid invoice date,
    days since last settlement, and next due date — per active supplier.

    No date filter (always reflects live state).
    Filter: ?actif_seulement=1  (default: active suppliers only)
    Export: ?export=csv

    Access: FINANCIAL_ROLES
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from achats.models import FactureFournisseur, ReglementFournisseur
    from intrants.models import Fournisseur

    today = datetime.date.today()
    branche = get_active_branche(request)

    fournisseurs_qs = Fournisseur.objects.order_by("nom")
    if request.GET.get("actif_seulement", "1") != "0":
        fournisseurs_qs = fournisseurs_qs.filter(actif=True)

    # Search
    q = request.GET.get("q", "").strip()
    if q:
        fournisseurs_qs = fournisseurs_qs.filter(nom__icontains=q)

    rows = []
    for fournisseur in fournisseurs_qs:
        factures_ouvertes = FactureFournisseur.objects.filter(
            fournisseur=fournisseur,
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ],
        )
        last_reg_qs = ReglementFournisseur.objects.filter(fournisseur=fournisseur)
        if branche is not None:
            factures_ouvertes = factures_ouvertes.filter(branche=branche)
            last_reg_qs = last_reg_qs.filter(branche=branche)
        agg = factures_ouvertes.aggregate(
            dette=Sum("reste_a_payer"),
            nb=Count("pk"),
            date_plus_ancienne=Min("date_facture"),
            prochaine_echeance=Min("date_echeance"),
        )
        dette = agg["dette"] or Decimal("0")
        if dette <= 0:
            continue  # skip suppliers with no current debt

        # Last settlement date
        last_reg = (
            last_reg_qs.order_by("-date_reglement")
            .values_list("date_reglement", flat=True)
            .first()
        )
        jours_sans_reglement = (today - last_reg).days if last_reg else None

        # Overdue invoices
        nb_retard = factures_ouvertes.filter(date_echeance__lt=today).count()

        rows.append(
            {
                "fournisseur": fournisseur,
                "dette_globale": dette,
                "nb_factures_ouvertes": agg["nb"] or 0,
                "date_facture_plus_ancienne": agg["date_plus_ancienne"],
                "prochaine_echeance": agg["prochaine_echeance"],
                "nb_retard": nb_retard,
                "last_reglement": last_reg,
                "jours_sans_reglement": jours_sans_reglement,
            }
        )

    # Sort by debt descending
    rows.sort(key=lambda r: r["dette_globale"], reverse=True)

    grand_total_dette = sum(r["dette_globale"] for r in rows)

    if request.GET.get("export") == "csv":
        headers = [
            "Fournisseur",
            "Dette globale (DZD)",
            "Factures ouvertes",
            "Plus ancienne facture",
            "Prochaine échéance",
            "Factures en retard",
            "Dernier règlement",
            "Jours sans règlement",
        ]
        csv_rows = [
            [
                r["fournisseur"].nom,
                r["dette_globale"],
                r["nb_factures_ouvertes"],
                r["date_facture_plus_ancienne"] or "",
                r["prochaine_echeance"] or "",
                r["nb_retard"],
                r["last_reglement"] or "",
                (
                    r["jours_sans_reglement"]
                    if r["jours_sans_reglement"] is not None
                    else ""
                ),
            ]
            for r in rows
        ]
        return _csv_response("dettes_fournisseurs", headers, csv_rows)

    top10 = rows[:10]
    chart_json = json.dumps(
        {
            "labels": [r["fournisseur"].nom for r in top10],
            "values": [float(r["dette_globale"]) for r in top10],
            "retard": [r["nb_retard"] for r in top10],
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/dettes_fournisseurs.html",
        {
            "title": "الديون الجارية حسب المورد",
            "rows": rows,
            "grand_total_dette": grand_total_dette,
            "today": today,
            "q": q,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.5 — Rentabilité par Lot
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_rentabilite_lot(request):
    """
    Lot profitability report.

    Shows per-lot KPIs: chick count, survival, mortality rate, feed consumed,
    IC, input cost, attributed dépenses, sales revenue, gross margin.

    Filters:  ?lot=<pk>  ?statut=ouvert|ferme  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES (detailed margin data)
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from elevage.models import LotElevage
    from elevage.utils import get_lot_summary

    branche = get_active_branche(request)
    qs = LotElevage.objects.select_related(
        "fournisseur_poussins", "batiment", "created_by"
    ).order_by("-date_ouverture")
    if branche is not None:
        qs = qs.filter(branche=branche)

    # Statut filter
    statut = request.GET.get("statut", "").strip()
    if statut:
        qs = qs.filter(statut=statut)

    # Date range on lot opening date
    date_debut, date_fin = _parse_dates(request)
    if date_debut:
        qs = qs.filter(date_ouverture__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_ouverture__lte=date_fin)

    # Single-lot drill-down
    lot_pk = request.GET.get("lot", "").strip()
    lot_obj = None
    lot_summary = None
    if lot_pk:
        lot_obj = branche_object_or_404(request, LotElevage, pk=lot_pk)
        lot_summary = get_lot_summary(lot_obj)

    # Cross-lot summary table
    lot_rows = []
    for lot in qs:
        summary = get_lot_summary(lot)
        lot_rows.append(
            {
                "lot": lot,
                "effectif_vivant": summary["effectif_vivant"],
                "total_mortalite": summary["total_mortalite"],
                "taux_mortalite": summary["taux_mortalite"],
                "duree_elevage": summary["duree_elevage"],
                "consommation_totale_aliment_kg": summary[
                    "consommation_totale_aliment_kg"
                ],
                "poids_total_produit_kg": summary["poids_total_produit_kg"],
                "ic": summary["ic"],
                "cout_total_intrants": summary["cout_total_intrants"],
                "cout_total_depenses": summary["cout_total_depenses"],
                "revenus_ventes": summary["revenus_ventes"],
                "marge_brute": summary["marge_brute"],
            }
        )

    # Aggregated totals across the filtered set
    totaux = {
        "cout_total_intrants": sum(r["cout_total_intrants"] for r in lot_rows),
        "cout_total_depenses": sum(r["cout_total_depenses"] for r in lot_rows),
        "revenus_ventes": sum(r["revenus_ventes"] for r in lot_rows),
        "marge_brute": sum(r["marge_brute"] for r in lot_rows),
    }

    if request.GET.get("export") == "csv":
        headers = [
            "Lot",
            "Bâtiment",
            "Date ouverture",
            "Date fermeture",
            "Statut",
            "Poussins initial",
            "Effectif vivant",
            "Mortalité",
            "Taux mortalité (%)",
            "Durée (j)",
            "Aliment consommé (kg)",
            "Poids produit (kg)",
            "IC",
            "Coût intrants (DZD)",
            "Dépenses attribuées (DZD)",
            "Revenus ventes (DZD)",
            "Marge brute (DZD)",
        ]
        rows = [
            [
                r["lot"].designation,
                r["lot"].batiment.nom,
                r["lot"].date_ouverture,
                r["lot"].date_fermeture or "",
                r["lot"].get_statut_display(),
                r["lot"].nombre_poussins_initial,
                r["effectif_vivant"],
                r["total_mortalite"],
                r["taux_mortalite"],
                r["duree_elevage"],
                r["consommation_totale_aliment_kg"],
                r["poids_total_produit_kg"],
                r["ic"] or "",
                r["cout_total_intrants"],
                r["cout_total_depenses"],
                r["revenus_ventes"],
                r["marge_brute"],
            ]
            for r in lot_rows
        ]
        return _csv_response("rentabilite_lots", headers, rows)

    lots_all = LotElevage.objects.order_by("-date_ouverture")
    if branche is not None:
        lots_all = lots_all.filter(branche=branche)

    chart_json = json.dumps(
        {
            "labels": [r["lot"].designation[:20] for r in lot_rows],
            "revenus": [float(r["revenus_ventes"]) for r in lot_rows],
            "couts_intrants": [float(r["cout_total_intrants"]) for r in lot_rows],
            "couts_depenses": [float(r["cout_total_depenses"]) for r in lot_rows],
            "marges": [float(r["marge_brute"]) for r in lot_rows],
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/rentabilite_lot.html",
        {
            "title": "ربحية الدفعة",
            "lot_rows": lot_rows,
            "totaux": totaux,
            "lot_obj": lot_obj,
            "lot_summary": lot_summary,
            "lots_all": lots_all,
            "lot_pk": lot_pk,
            "statut": statut,
            "statut_choices": LotElevage.STATUT_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.6 — Résumé de Trésorerie (Cash Flow Summary)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_cash_flow(request):
    """
    Period-based cash-flow statement (spec §20.6):
      Inflows  = PaiementClient received
      Outflows = ReglementFournisseur + Depenses + RetraitAssocie (BR-ASSOC-01)
               + payroll cash paid: AcompteEmploye + BulletinPaie statut=paye (BR-RH-04)
      Net      = Inflows − Outflows

    Filters:  ?date_debut=  ?date_fin=
    Export:   ?export=csv (six separate sections)

    Access: FINANCIAL_ROLES

    v1.4 (§3.5.5): Vue par Branche shows this branche's own cash flow
    (PaiementClient/ReglementFournisseur/Depense filtered on `branche`,
    payroll cash filtered via `employe__batiment__branche` — BR-BRA-09);
    Vue Globale sums every branche. Per BR-BRA-08, stakeholder withdrawals
    (RetraitAssocie) are never branch-scoped — `total_retraits_associes`
    always reflects the full company-wide figure regardless of the active
    branche.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.utils import get_cash_flow_summary

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)

    # Default to last 12 months if no range provided
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today - datetime.timedelta(days=365)
        date_fin = today

    summary = get_cash_flow_summary(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )

    if request.GET.get("export") == "csv":
        headers = ["Catégorie", "Détail", "Date", "Montant (DZD)", "Mode de paiement"]
        rows = []

        # Inflows
        for p in summary["detail_paiements"]:
            rows.append(
                [
                    "Encaissement",
                    f"Paiement {p.client.nom}",
                    p.date_paiement,
                    p.montant,
                    p.get_mode_paiement_display(),
                ]
            )

        # Outflows — supplier settlements
        for r in summary["detail_reglements"]:
            rows.append(
                [
                    "Règlement fournisseur",
                    r.fournisseur.nom,
                    r.date_reglement,
                    r.montant,
                    r.get_mode_paiement_display(),
                ]
            )

        # Outflows — operational expenses
        for d in summary["detail_depenses"]:
            rows.append(
                [
                    "Dépense opérationnelle",
                    d.description[:80],
                    d.date,
                    d.montant,
                    d.get_mode_paiement_display(),
                ]
            )

        # Outflows — stakeholder withdrawals (BR-ASSOC-01)
        for ret in summary["detail_retraits"]:
            rows.append(
                [
                    "Retrait associé",
                    f"{ret.associe.nom}" + (f" — {ret.motif}" if ret.motif else ""),
                    ret.date,
                    ret.montant,
                    ret.get_mode_paiement_display(),
                ]
            )

        # Outflows — salary advances (BR-RH-04)
        for ac in summary["detail_acomptes"]:
            rows.append(
                [
                    "Acompte sur salaire",
                    ac.employe.nom_complet,
                    ac.date,
                    ac.montant,
                    ac.get_mode_paiement_display(),
                ]
            )

        # Outflows — payslips actually paid (BR-RH-04)
        for b in summary["detail_bulletins_payes"]:
            rows.append(
                [
                    "Salaire payé",
                    f"{b.employe.nom_complet} ({b.periode_label})",
                    b.date_paiement,
                    b.montant_net,
                    b.get_mode_paiement_display(),
                ]
            )

        return _csv_response("resume_tresorerie", headers, rows)

    chart_json = json.dumps(
        {
            "encaissements": float(summary["total_encaissements"]),
            "reglements": float(summary["total_reglements_fournisseurs"]),
            "depenses": float(summary["total_depenses"]),
            "retraits_associes": float(summary["total_retraits_associes"]),
            "acomptes_employes": float(summary["total_acomptes_employes"]),
            "salaires_payes": float(summary["total_salaires_payes"]),
            "paie": float(summary["total_paie"]),
            "sorties": float(summary["total_sorties"]),
            "solde": float(summary["solde_net"]),
        }
    )

    return render(
        request,
        "reporting/cash_flow.html",
        {
            "title": "ملخص الخزينة",
            "summary": summary,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "today": today,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 20.7 — État des Stocks
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_etat_stocks(request):
    """
    Snapshot of all stock levels (intrants + produits finis).

    Filters:
      ?segment=intrants|produits_finis   (default: both)
      ?categorie=<pk>                    (intrant category filter)
      ?alerte=1                          (only items at/below threshold)
      ?q=<search>                        (designation search)
    Export: ?export=csv

    Access: ALL_ROLES

    v1.4 (§3.5.3 ¶3, BR-BRA-07): StockIntrant/StockProduitFini are now one
    row per (branche, item). Vue par Branche shows only the active branche's
    balances — exactly what a chef de branche sees and depletes; Vue Globale
    shows every branche's rows side by side (the branche is annotated on
    each row so the same catalogue item's several branch balances are
    distinguishable), with valuation totals summed across all of them.
    """
    from intrants.models import CategorieIntrant
    from stock.models import StockIntrant, StockProduitFini

    branche = get_active_branche(request)
    segment = request.GET.get("segment", "").strip()
    categorie_pk = request.GET.get("categorie", "").strip()
    alerte_only = request.GET.get("alerte", "") == "1"
    q = request.GET.get("q", "").strip()

    # ── Intrants ──────────────────────────────────────────────────────────
    stocks_intrants = []
    if segment in ("", "intrants"):
        si_qs = StockIntrant.objects.select_related(
            "intrant__categorie", "branche"
        ).order_by("intrant__categorie__libelle", "intrant__designation")
        if branche is not None:
            si_qs = si_qs.filter(branche=branche)
        if categorie_pk:
            si_qs = si_qs.filter(intrant__categorie_id=categorie_pk)
        if alerte_only:
            si_qs = si_qs.filter(quantite__lte=F("intrant__seuil_alerte"))
        if q:
            si_qs = si_qs.filter(
                Q(intrant__designation__icontains=q)
                | Q(intrant__categorie__libelle__icontains=q)
            )
        stocks_intrants = list(si_qs)

    # ── Produits finis ────────────────────────────────────────────────────
    stocks_produits = []
    if segment in ("", "produits_finis"):
        spf_qs = StockProduitFini.objects.select_related(
            "produit_fini", "branche"
        ).order_by("produit_fini__type_produit", "produit_fini__designation")
        if branche is not None:
            spf_qs = spf_qs.filter(branche=branche)
        if alerte_only:
            spf_qs = spf_qs.filter(quantite__lte=F("seuil_alerte"))
        if q:
            spf_qs = spf_qs.filter(produit_fini__designation__icontains=q)
        stocks_produits = list(spf_qs)

    # Valuation totals
    valeur_intrants = sum((s.valeur_stock for s in stocks_intrants), Decimal("0"))
    valeur_produits = sum((s.valeur_stock for s in stocks_produits), Decimal("0"))

    if request.GET.get("export") == "csv":
        headers = [
            "Branche",
            "Segment",
            "Catégorie",
            "Désignation",
            "Quantité",
            "Unité",
            "Seuil alerte",
            "Statut",
            "PMP / Coût moyen (DZD)",
            "Valeur stock (DZD)",
            "Dernière MAJ",
        ]
        rows = []
        for s in stocks_intrants:
            statut = (
                "RUPTURE" if s.quantite <= 0 else ("ALERTE" if s.en_alerte else "OK")
            )
            rows.append(
                [
                    s.branche.nom,
                    "Intrant",
                    s.intrant.categorie.libelle,
                    s.intrant.designation,
                    s.quantite,
                    s.intrant.unite_mesure,
                    s.intrant.seuil_alerte,
                    statut,
                    s.prix_moyen_pondere,
                    s.valeur_stock,
                    s.derniere_mise_a_jour.date() if s.derniere_mise_a_jour else "",
                ]
            )
        for s in stocks_produits:
            statut = (
                "RUPTURE" if s.quantite <= 0 else ("ALERTE" if s.en_alerte else "OK")
            )
            rows.append(
                [
                    s.branche.nom,
                    "Produit fini",
                    s.produit_fini.get_type_produit_display(),
                    s.produit_fini.designation,
                    s.quantite,
                    s.produit_fini.unite_mesure,
                    s.seuil_alerte,
                    statut,
                    s.cout_moyen_production,
                    s.valeur_stock,
                    s.derniere_mise_a_jour.date() if s.derniere_mise_a_jour else "",
                ]
            )
        return _csv_response("etat_stocks", headers, rows)

    categories = CategorieIntrant.objects.filter(actif=True).order_by("libelle")

    # Build chart data using json.dumps to guarantee valid JSON regardless of
    # Django locale settings (which format Decimals as "0,00" in fr locale).
    top10 = stocks_intrants[:10]
    chart_json = json.dumps(
        {
            "intrants_labels": [s.intrant.designation[:20] for s in top10],
            "intrants_values": [float(s.valeur_stock) for s in top10],
            "donut_labels": ["Stock المدخلات", "Stock المنتجات النهائية"],
            "donut_values": [float(valeur_intrants), float(valeur_produits)],
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/etat_stocks.html",
        {
            "title": "حالة المخزون",
            "stocks_intrants": stocks_intrants,
            "stocks_produits": stocks_produits,
            "valeur_intrants": valeur_intrants,
            "valeur_produits": valeur_produits,
            "valeur_totale": valeur_intrants + valeur_produits,
            "categories": categories,
            "segment": segment,
            "categorie_pk": categorie_pk,
            "alerte_only": alerte_only,
            "q": q,
            "nb_alerte_intrants": sum(
                1 for s in stocks_intrants if s.en_alerte and s.quantite > 0
            ),
            "nb_rupture_intrants": sum(1 for s in stocks_intrants if s.quantite <= 0),
            "nb_alerte_produits": sum(
                1 for s in stocks_produits if s.en_alerte and s.quantite > 0
            ),
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Consommation par Lot
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_consommation_lot(request):
    """
    Feed and medicine consumption detail, filterable by lot, intrant, and
    date range.  Used to analyse per-lot input usage.

    Filters:  ?lot=<pk>  ?intrant=<pk>  ?categorie=<pk>  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: ALL_ROLES (operateurs can view consumption data)

    v1.4 (§3.5.5, BR-BRA-01): Consommation has no stored `branche` column —
    it is derived from `lot.branche`. Vue par Branche filters on the
    `lot__branche` join (and scopes the lot picker the same way); Vue
    Globale shows every branche's consumption combined.
    """
    from elevage.models import Consommation, LotElevage
    from intrants.models import CategorieIntrant, Intrant

    branche = get_active_branche(request)

    qs = Consommation.objects.select_related(
        "lot", "intrant__categorie", "created_by"
    ).order_by("-date", "lot__designation")
    if branche is not None:
        qs = qs.filter(lot__branche=branche)

    lot_pk = request.GET.get("lot", "").strip()
    if lot_pk:
        qs = qs.filter(lot_id=lot_pk)

    intrant_pk = request.GET.get("intrant", "").strip()
    if intrant_pk:
        qs = qs.filter(intrant_id=intrant_pk)

    categorie_pk = request.GET.get("categorie", "").strip()
    if categorie_pk:
        qs = qs.filter(intrant__categorie_id=categorie_pk)

    date_debut, date_fin = _parse_dates(request)
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    # Aggregated totals
    agg = qs.aggregate(nb=Count("pk"))
    nb_total = agg["nb"] or 0

    # Per-intrant aggregation for summary table
    par_intrant = (
        qs.values(
            "intrant__designation",
            "intrant__unite_mesure",
            "intrant__categorie__libelle",
        )
        .annotate(total_quantite=Sum("quantite"), nb=Count("pk"))
        .order_by("intrant__categorie__libelle", "intrant__designation")
    )

    if request.GET.get("export") == "csv":
        headers = [
            "Date",
            "Lot",
            "Intrant",
            "Catégorie",
            "Quantité",
            "Unité",
        ]
        rows = [
            [
                c.date,
                c.lot.designation,
                c.intrant.designation,
                c.intrant.categorie.libelle,
                c.quantite,
                c.intrant.unite_mesure,
            ]
            for c in qs
        ]
        return _csv_response("consommation_lot", headers, rows)

    page = _paginate(qs, request.GET.get("page"))
    lots_qs = LotElevage.objects.order_by("-date_ouverture")
    if branche is not None:
        lots_qs = lots_qs.filter(branche=branche)
    lots = lots_qs
    intrants = Intrant.objects.filter(
        actif=True, categorie__consommable_en_lot=True
    ).select_related("categorie")
    categories = CategorieIntrant.objects.filter(actif=True)

    par_intrant_list = list(par_intrant)

    chart_json = json.dumps(
        {
            "labels": [r["intrant__designation"][:18] for r in par_intrant_list],
            "values": [float(r["total_quantite"]) for r in par_intrant_list],
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/consommation_lot.html",
        {
            "title": "الاستهلاك حسب الدفعة",
            "page": page,
            "par_intrant": par_intrant_list,
            "nb_total": nb_total,
            "lots": lots,
            "intrants": intrants,
            "categories": categories,
            "lot_pk": lot_pk,
            "intrant_pk": intrant_pk,
            "categorie_pk": categorie_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Créances Clients (Receivables Aging)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_creances_clients(request):
    """
    Client receivables aging report — mirrors the supplier aging report but
    for accounts receivable.

    Buckets:  current | 1–30 | 31–60 | 61–90 | 90+ days overdue.
    Filters:  ?client=<pk>  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES

    v1.4 (§3.5.5): Vue par Branche shows this branche's own receivables
    (BLClient/FactureClient are branch-scoped, Client stays global); Vue
    Globale sums every branche the client has been served by.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from clients.models import Client
    from clients.utils import get_client_aging_buckets

    branche = get_active_branche(request)
    client_pk = request.GET.get("client", "").strip()
    client_obj = None
    if client_pk:
        client_obj = get_object_or_404(Client, pk=client_pk)

    buckets = get_client_aging_buckets(client=client_obj, branche=branche)

    totaux = {
        "current": sum(b["current"] for b in buckets),
        "1_30": sum(b["1_30"] for b in buckets),
        "31_60": sum(b["31_60"] for b in buckets),
        "61_90": sum(b["61_90"] for b in buckets),
        "over_90": sum(b["over_90"] for b in buckets),
        "total": sum(b["total"] for b in buckets),
    }

    if request.GET.get("export") == "csv":
        headers = [
            "Client",
            "Courant (non échu)",
            "1–30 jours",
            "31–60 jours",
            "61–90 jours",
            "> 90 jours",
            "Total créance",
        ]
        rows = [
            [
                b["client"].nom,
                b["current"],
                b["1_30"],
                b["31_60"],
                b["61_90"],
                b["over_90"],
                b["total"],
            ]
            for b in buckets
        ]
        rows.append(
            [
                "TOTAL",
                totaux["current"],
                totaux["1_30"],
                totaux["31_60"],
                totaux["61_90"],
                totaux["over_90"],
                totaux["total"],
            ]
        )
        return _csv_response("creances_clients", headers, rows)

    clients = Client.objects.filter(actif=True).order_by("nom")

    chart_json = json.dumps(
        {
            "labels": [b["client"].nom for b in buckets],
            "current": [float(b["current"]) for b in buckets],
            "d30": [float(b["1_30"]) for b in buckets],
            "d60": [float(b["31_60"]) for b in buckets],
            "d90": [float(b["61_90"]) for b in buckets],
            "over90": [float(b["over_90"]) for b in buckets],
            "totaux": {
                "current": float(totaux["current"]),
                "d30": float(totaux["1_30"]),
                "d60": float(totaux["31_60"]),
                "d90": float(totaux["61_90"]),
                "over90": float(totaux["over_90"]),
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/creances_clients.html",
        {
            "title": "مستحقات العملاء",
            "buckets": buckets,
            "totaux": totaux,
            "clients": clients,
            "client_pk": client_pk,
            "client_obj": client_obj,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Historique BL Clients
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_historique_bl_clients(request):
    """
    Delivery note (BL Client) history log with invoice-link status.

    Filters:  ?client=<pk>  ?statut=  ?date_debut=  ?date_fin=  ?q=
    Export:   ?export=csv

    Access: ALL_ROLES

    v1.4 (§3.5.5, BR-BRA-01): BLClient carries a required `branche` FK. Vue
    par Branche shows only the active branche's delivery notes; Vue Globale
    shows every branche combined.
    """
    from clients.models import BLClient, Client

    branche = get_active_branche(request)

    qs = (
        BLClient.objects.select_related("client", "branche", "created_by")
        .prefetch_related("lignes__produit_fini")
        .order_by("-date_bl")
    )
    if branche is not None:
        qs = qs.filter(branche=branche)

    client_pk = request.GET.get("client", "").strip()
    if client_pk:
        qs = qs.filter(client_id=client_pk)

    statut = request.GET.get("statut", "").strip()
    if statut:
        qs = qs.filter(statut=statut)

    date_debut, date_fin = _parse_dates(request)
    if date_debut:
        qs = qs.filter(date_bl__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_bl__lte=date_fin)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(client__nom__icontains=q))

    # Totals
    agg = qs.aggregate(nb=Count("pk"))

    if request.GET.get("export") == "csv":
        headers = [
            "Référence BL",
            "Client",
            "Date",
            "Statut",
            "Montant total (DZD)",
            "Facturé",
        ]
        rows = [
            [
                bl.reference,
                bl.client.nom,
                bl.date_bl,
                bl.get_statut_display(),
                bl.montant_total,
                "نعم" if bl.statut == BLClient.STATUT_FACTURE else "لا",
            ]
            for bl in qs
        ]
        return _csv_response("historique_bl_clients", headers, rows)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    chart_json = json.dumps(
        {
            "rows": [
                {
                    "statut": bl.statut,
                    "client": bl.client.nom,
                    "montant": float(bl.montant_total),
                }
                for bl in page.object_list
            ]
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/historique_bl_clients.html",
        {
            "title": "سجل وصولات تسليم العملاء",
            "page": page,
            "nb_total": agg["nb"] or 0,
            "clients": clients,
            "client_pk": client_pk,
            "statut": statut,
            "statut_choices": BLClient.STATUT_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "q": q,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Production Dashboard Report  (cross-lot KPI table — spec §20.5 supplement)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_production_dashboard(request):
    """
    Cross-lot production summary: birds harvested, total weight, avg weight,
    estimated production cost — per lot, per period.

    Filters:  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES

    v1.4 (§3.5.5): ProductionRecord.branche is denormalized from the lot.
    Vue par Branche shows only the active branche's production; Vue Globale
    sums every branche.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from production.utils import get_production_dashboard

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(month=1, day=1)
        date_fin = today

    rows = get_production_dashboard(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )

    totaux = {
        "nb_oiseaux_abattus": sum(r["nb_oiseaux_abattus"] for r in rows),
        "poids_total_kg": sum(r["poids_total_kg"] for r in rows),
        "cout_total_dzd": sum(r["cout_total_dzd"] for r in rows),
    }

    if request.GET.get("export") == "csv":
        headers = [
            "Lot",
            "Date dernière production",
            "Oiseaux abattus",
            "Poids total (kg)",
            "Poids moyen (kg)",
            "Coût total estimé (DZD)",
        ]
        csv_rows = [
            [
                r["lot"].designation,
                r["date_production"] or "",
                r["nb_oiseaux_abattus"],
                r["poids_total_kg"],
                r["poids_moyen_kg"],
                r["cout_total_dzd"],
            ]
            for r in rows
        ]
        return _csv_response("tableau_bord_production", headers, csv_rows)

    chart_json = json.dumps(
        {
            "lots": [r["lot"].designation for r in rows],
            "abattus": [r["nb_oiseaux_abattus"] for r in rows],
            "poids_total": [float(r["poids_total_kg"]) for r in rows],
            "poids_moyen": [float(r["poids_moyen_kg"]) for r in rows],
            "cout": [float(r["cout_total_dzd"]) for r in rows],
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/production_dashboard.html",
        {
            "title": "لوحة تحكم الإنتاج",
            "rows": rows,
            "totaux": totaux,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "today": today,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Dépenses Summary Report
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_depenses(request):
    """
    Operational expense summary: total, average daily spend, breakdown by
    category and payment method.

    Filters:  ?date_debut=  ?date_fin=  ?categorie=<pk>  ?lot=<pk>
    Export:   ?export=csv

    Access: FINANCIAL_ROLES

    v1.4 (§3.5.5, BR-BRA-01): Depense carries a required `branche` FK. Vue
    par Branche shows only the active branche's expenses (and scopes the
    lot picker the same way); Vue Globale sums every branche.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.models import CategorieDepense, Depense
    from depenses.utils import get_depenses_summary
    from elevage.models import LotElevage

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today - datetime.timedelta(days=365)
        date_fin = today

    categorie_pk = request.GET.get("categorie", "").strip()
    lot_pk = request.GET.get("lot", "").strip()

    summary = get_depenses_summary(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )

    # Filtered expense queryset for the detail table
    depenses_qs = Depense.objects.select_related(
        "categorie", "lot", "branche", "enregistre_par"
    ).order_by("-date")
    if branche is not None:
        depenses_qs = depenses_qs.filter(branche=branche)
    if date_debut:
        depenses_qs = depenses_qs.filter(date__gte=date_debut)
    if date_fin:
        depenses_qs = depenses_qs.filter(date__lte=date_fin)
    if categorie_pk:
        depenses_qs = depenses_qs.filter(categorie_id=categorie_pk)
    if lot_pk:
        depenses_qs = depenses_qs.filter(lot_id=lot_pk)

    if request.GET.get("export") == "csv":
        headers = [
            "Date",
            "Catégorie",
            "Description",
            "Montant (DZD)",
            "Mode",
            "Référence document",
            "Lot attribué",
        ]
        rows = [
            [
                d.date,
                d.categorie.libelle,
                d.description[:100],
                d.montant,
                d.get_mode_paiement_display(),
                d.reference_document,
                d.lot.designation if d.lot else "",
            ]
            for d in depenses_qs
        ]
        return _csv_response("depenses", headers, rows)

    page = _paginate(depenses_qs, request.GET.get("page"))
    categories = CategorieDepense.objects.filter(actif=True).order_by("libelle")
    lots_qs = LotElevage.objects.order_by("-date_ouverture")
    if branche is not None:
        lots_qs = lots_qs.filter(branche=branche)
    lots = lots_qs

    chart_json = json.dumps(
        {
            "categories": {
                "labels": [c["categorie"].libelle for c in summary["par_categorie"]],
                "values": [float(c["total"]) for c in summary["par_categorie"]],
                "pcts": [float(c["pct"]) for c in summary["par_categorie"]],
            },
            "modes": {
                "labels": [m["label"] for m in summary["par_mode_paiement"]],
                "values": [float(m["total"]) for m in summary["par_mode_paiement"]],
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/depenses.html",
        {
            "title": "تقرير المصروفات",
            "summary": summary,
            "page": page,
            "categories": categories,
            "lots": lots,
            "categorie_pk": categorie_pk,
            "lot_pk": lot_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# Lot Consommation Detail (drill-down for a single lot — used from lot page)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_consommation_lot_detail(request, lot_pk):
    """
    Detailed consumption report for a single lot.
    Wraps elevage.utils.get_lot_summary but focuses on the consumption slice.

    Access: ALL_ROLES

    BR-BRA-02: the lot must belong to the request's active branche.
    """
    from elevage.models import LotElevage
    from elevage.utils import get_lot_summary

    lot = branche_object_or_404(request, LotElevage, pk=lot_pk)
    summary = get_lot_summary(lot)

    if request.GET.get("export") == "csv":
        headers = ["Date", "Intrant", "Catégorie", "Quantité", "Unité"]
        rows = [
            [
                c.date,
                c.intrant.designation,
                c.intrant.categorie.libelle,
                c.quantite,
                c.intrant.unite_mesure,
            ]
            for c in summary["consommations"]
        ]
        return _csv_response(
            f"consommation_{lot.designation.replace(' ', '_')}", headers, rows
        )

    conso_json = json.dumps(
        [
            {
                "date": c.date.strftime("%d/%m/%Y"),
                "intrant": c.intrant.designation,
                "categorie": c.intrant.categorie.libelle,
                "categorie_code": c.intrant.categorie.code,
                "quantite": float(c.quantite),
                "unite": c.intrant.unite_mesure,
            }
            for c in summary["consommations"]
        ],
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/consommation_lot_detail.html",
        {
            "title": f"الاستهلاك — {lot.designation}",
            "lot": lot,
            "summary": summary,
            "conso_json": conso_json,
            "active_branche": get_active_branche(request),
        },
    )


# ===========================================================================
# Mouvement de Stock print view
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def stock_mouvement_print(request, pk):
    """
    Printable bon de mouvement de stock (spec §9.11).
    Accessible to all roles.

    BR-BRA-02: the movement must belong to the request's active branche.
    """
    from core.models import CompanyInfo
    from stock.models import StockMouvement

    mouvement = branche_object_or_404(
        request, StockMouvement.objects.select_related("branche"), pk=pk
    )
    company = CompanyInfo.get_instance()

    return render(
        request,
        "reporting/print/stock_mouvement.html",
        {
            "mouvement": mouvement,
            "company": company,
            "print_mode": True,
        },
    )


# ===========================================================================
# 23.7 — Retraits Associés
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_retraits_associes(request):
    """
    Stakeholder withdrawal report (spec §23.7).

    Shows per-associé totals and a detail table, filterable by period.
    Filters:  ?associe=<pk>  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES (equity draw data)

    v1.4 / BR-BRA-08: deliberate exception to the Vue par Branche / Vue
    Globale toggle — Associé/RetraitAssocie are never branch-scoped, so
    this report always shows the full company-wide figures regardless of
    the request's active branche. `active_branche` is still passed to the
    template for the header/breadcrumb, but no queryset below is filtered
    by it.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.models import Associe, RetraitAssocie

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(month=1, day=1)
        date_fin = today

    associe_pk = request.GET.get("associe", "").strip()

    qs = RetraitAssocie.objects.select_related("associe", "enregistre_par").order_by(
        "-date"
    )

    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)
    if associe_pk:
        qs = qs.filter(associe_id=associe_pk)

    grand_total = qs.aggregate(total=Sum("montant"), nb=Count("pk"))
    total_dzd = grand_total["total"] or Decimal("0")

    # Per-associé breakdown
    par_associe = (
        qs.values("associe__nom", "associe__pk")
        .annotate(total=Sum("montant"), nb=Count("pk"))
        .order_by("-total")
    )

    # Per-mode breakdown
    from depenses.models import Depense

    mode_label_map = dict(Depense.MODE_CHOICES)
    par_mode = (
        qs.values("mode_paiement")
        .annotate(total=Sum("montant"), nb=Count("pk"))
        .order_by("-total")
    )
    par_mode_display = [
        {
            "mode": r["mode_paiement"],
            "label": mode_label_map.get(r["mode_paiement"], r["mode_paiement"]),
            "total": r["total"],
            "nb": r["nb"],
        }
        for r in par_mode
    ]

    if request.GET.get("export") == "csv":
        headers = ["التاريخ", "الشريك", "المبلغ (دج)", "طريقة الدفع", "السبب", "مرجع"]
        rows = [
            [
                r.date,
                r.associe.nom,
                r.montant,
                mode_label_map.get(r.mode_paiement, r.mode_paiement),
                r.motif,
                r.reference_document,
            ]
            for r in qs
        ]
        return _csv_response("retraits_associes", headers, rows)

    associes = Associe.objects.filter(actif=True).order_by("nom")
    page = _paginate(qs, request.GET.get("page"))
    par_associe_list = list(par_associe)

    chart_json = json.dumps(
        {
            "labels": [r["associe__nom"] for r in par_associe_list],
            "values": [float(r["total"]) for r in par_associe_list],
            "modes": {
                "labels": [m["label"] for m in par_mode_display],
                "values": [float(m["total"]) for m in par_mode_display],
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/retraits_associes.html",
        {
            "title": "سحوبات الشركاء",
            "page": page,
            "total_dzd": total_dzd,
            "nb_total": grand_total["nb"] or 0,
            "par_associe": par_associe_list,
            "par_mode": par_mode_display,
            "associes": associes,
            "associe_pk": associe_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 23.8 — Synthèse RH / Payroll Summary
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_synthese_rh(request):
    """
    HR / payroll summary (spec §23.8).

    Aggregate payroll figures for a period: total gross, total advances,
    total net paid, attendance breakdown.  Per-employee breakdown table.

    Filters:  ?date_debut=  ?date_fin=  ?employe=<pk>  ?statut=
    Export:   ?export=csv

    Access: FINANCIAL_ROLES (payroll data is sensitive)

    v1.4 (§3.5.5, BR-BRA-09): Employe.branche is derived from the assigned
    batiment, not a stored column. Vue par Branche filters every queryset
    below via the `employe__batiment__branche` (or `batiment__branche`)
    join; Vue Globale shows the company-wide payroll summary, exactly as
    spec'd for admin/comptable.
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.models import AcompteEmploye, BulletinPaie, Employe

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(month=1, day=1)
        date_fin = today

    employe_pk = request.GET.get("employe", "").strip()
    statut = request.GET.get("statut", "").strip()

    # Bulletins in period (filter by date_paiement when paid, else annee/mois)
    bulletins_qs = BulletinPaie.objects.select_related(
        "employe", "genere_par"
    ).order_by("-annee", "-mois")
    if branche is not None:
        bulletins_qs = bulletins_qs.filter(employe__batiment__branche=branche)

    if date_debut:
        bulletins_qs = bulletins_qs.filter(annee__gte=date_debut.year).filter(
            annee__lte=(date_fin.year if date_fin else today.year)
        )
    if date_fin:
        bulletins_qs = bulletins_qs.filter(annee__lte=date_fin.year)
    if employe_pk:
        bulletins_qs = bulletins_qs.filter(employe_id=employe_pk)
    if statut:
        bulletins_qs = bulletins_qs.filter(statut=statut)

    # Aggregate totals
    agg = bulletins_qs.aggregate(
        total_brut=Sum("montant_brut"),
        total_acomptes=Sum("total_acomptes"),
        total_net=Sum("montant_net"),
        total_presence=Sum("jours_presence"),
        total_absence=Sum("jours_absence"),
        total_repos=Sum("jours_repos"),
        total_conge=Sum("jours_conge"),
        total_heures_sup=Sum("total_heures_supplementaires"),
        nb=Count("pk"),
    )

    # Advances in period (independently)
    acomptes_qs = AcompteEmploye.objects.select_related("employe").filter(
        date__gte=date_debut or today.replace(year=today.year - 1),
        date__lte=date_fin or today,
    )
    if branche is not None:
        acomptes_qs = acomptes_qs.filter(employe__batiment__branche=branche)
    if employe_pk:
        acomptes_qs = acomptes_qs.filter(employe_id=employe_pk)
    total_acomptes_payes = acomptes_qs.aggregate(total=Sum("montant"))[
        "total"
    ] or Decimal("0")

    # Leave balances for active employees
    from depenses.models import CongeEmploye

    employes_actifs_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
    if branche is not None:
        employes_actifs_qs = employes_actifs_qs.filter(batiment__branche=branche)
    employes_actifs = employes_actifs_qs
    soldes_conge = {}
    for emp in employes_actifs:
        accrued = Decimal(str(emp.anciennete_mois())) * Decimal("2.5")
        used = sum(c.nb_jours or 0 for c in CongeEmploye.objects.filter(employe=emp))
        soldes_conge[emp.pk] = max(Decimal("0"), accrued - Decimal(str(used)))

    if request.GET.get("export") == "csv":
        headers = [
            "العامل",
            "الشهر/السنة",
            "الحالة",
            "أيام الحضور",
            "أيام الغياب",
            "أيام الراحة",
            "أيام العطلة",
            "ساعات إضافية",
            "مبلغ إجمالي (دج)",
            "تسبيقات (دج)",
            "صافي (دج)",
        ]
        rows = [
            [
                b.employe.nom_complet,
                b.periode_label,
                b.get_statut_display(),
                b.jours_presence,
                b.jours_absence,
                b.jours_repos,
                b.jours_conge,
                b.total_heures_supplementaires,
                b.montant_brut,
                b.total_acomptes,
                b.montant_net,
            ]
            for b in bulletins_qs
        ]
        return _csv_response("synthese_rh", headers, rows)

    page = _paginate(bulletins_qs, request.GET.get("page"))
    employes_qs = Employe.objects.filter(actif=True).order_by("nom_complet")
    if branche is not None:
        employes_qs = employes_qs.filter(batiment__branche=branche)
    employes = employes_qs

    chart_json = json.dumps(
        {
            "brut": float(agg["total_brut"] or 0),
            "acomptes": float(agg["total_acomptes"] or 0),
            "net": float(agg["total_net"] or 0),
            "presence": agg["total_presence"] or 0,
            "absence": agg["total_absence"] or 0,
            "conge": agg["total_conge"] or 0,
            "repos": agg["total_repos"] or 0,
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/synthese_rh.html",
        {
            "title": "ملخص الموارد البشرية والرواتب",
            "page": page,
            "agg": agg,
            "total_acomptes_payes": total_acomptes_payes,
            "employes": employes,
            "employes_actifs": employes_actifs,
            "soldes_conge": soldes_conge,
            "employe_pk": employe_pk,
            "statut": statut,
            "statut_choices": BulletinPaie.STATUT_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# 23.10 — Rapport Œufs & Fertilisant (by-product report)
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def rapport_oeufs_fertilisant(request):
    """
    Egg collection and fertilizer by-product report (spec §23.10).

    For laying lots: cumulative eggs collected, plateaux derived, quality
    distribution from PeseeEchantillon.

    For fertilizer: raw collected per building, treated, finished yield.

    Filters:  ?lot=<pk>  ?batiment=<pk>  ?date_debut=  ?date_fin=
    Export:   ?export=csv

    Access: ALL_ROLES

    v1.4 (§3.5.5, BR-BRA-01): RecolteOeufs has no stored `branche` — it is
    derived from `lot.branche`, so it's filtered via the `lot__branche`
    join. CollecteFertilisant/TraitementFertilisant carry their own
    `branche` FK (denormalized from batiment, resp. explicit). Vue par
    Branche scopes all three to the active branche (and the lot/bâtiment
    pickers the same way); Vue Globale shows every branche combined.
    """
    from elevage.models import LotElevage, RecolteOeufs
    from intrants.models import Batiment
    from production.models import CollecteFertilisant, TraitementFertilisant

    branche = get_active_branche(request)
    date_debut, date_fin = _parse_dates(request)
    lot_pk = request.GET.get("lot", "").strip()
    batiment_pk = request.GET.get("batiment", "").strip()

    # ── Egg collections ──────────────────────────────────────────────────
    oeufs_qs = RecolteOeufs.objects.select_related(
        "lot", "lot__batiment", "pesee"
    ).order_by("-date")
    if branche is not None:
        oeufs_qs = oeufs_qs.filter(lot__branche=branche)
    if date_debut:
        oeufs_qs = oeufs_qs.filter(date__gte=date_debut)
    if date_fin:
        oeufs_qs = oeufs_qs.filter(date__lte=date_fin)
    if lot_pk:
        oeufs_qs = oeufs_qs.filter(lot_id=lot_pk)

    oeufs_agg = oeufs_qs.aggregate(
        total_oeufs=Sum("nombre_oeufs"),
        nb=Count("pk"),
    )
    total_oeufs = oeufs_agg["total_oeufs"] or 0
    total_plateaux = total_oeufs // 30
    total_oeufs_hors = total_oeufs % 30

    # Per-lot egg breakdown
    par_lot_oeufs = (
        oeufs_qs.values("lot__designation", "lot__pk")
        .annotate(oeufs=Sum("nombre_oeufs"), nb=Count("pk"))
        .order_by("-oeufs")
    )

    # ── Fertilizer ───────────────────────────────────────────────────────
    collectes_qs = CollecteFertilisant.objects.select_related(
        "batiment", "traitement"
    ).order_by("-date_collecte")
    if branche is not None:
        collectes_qs = collectes_qs.filter(branche=branche)
    if date_debut:
        collectes_qs = collectes_qs.filter(date_collecte__gte=date_debut)
    if date_fin:
        collectes_qs = collectes_qs.filter(date_collecte__lte=date_fin)
    if batiment_pk:
        collectes_qs = collectes_qs.filter(batiment_id=batiment_pk)

    collectes_agg = collectes_qs.aggregate(
        total_brut=Sum("quantite_brute_kg"), nb=Count("pk")
    )

    traitements_qs = TraitementFertilisant.objects.select_related(
        "produit_fini"
    ).filter(statut=TraitementFertilisant.STATUT_VALIDE)
    if branche is not None:
        traitements_qs = traitements_qs.filter(branche=branche)
    if date_debut:
        traitements_qs = traitements_qs.filter(date_traitement__gte=date_debut)
    if date_fin:
        traitements_qs = traitements_qs.filter(date_traitement__lte=date_fin)

    traitements_agg = traitements_qs.aggregate(
        total_obtenu=Sum("quantite_obtenue_kg"), nb=Count("pk")
    )
    total_brut = collectes_agg["total_brut"] or Decimal("0")
    total_obtenu = traitements_agg["total_obtenu"] or Decimal("0")
    rendement_global = (
        round(float(total_obtenu) / float(total_brut) * 100, 1) if total_brut else None
    )

    if request.GET.get("export") == "csv":
        headers = [
            "القسم",
            "التاريخ",
            "الدفعة / المبنى",
            "البيانات",
            "الكمية",
            "وحدة",
        ]
        rows = []
        for r in oeufs_qs:
            rows.append(
                [
                    "بيض",
                    r.date,
                    r.lot.designation,
                    f"جمع بيض — {r.nombre_oeufs} بيضة ({r.nombre_plateaux} صينية)",
                    r.nombre_oeufs,
                    "بيضة",
                ]
            )
        for c in collectes_qs:
            rows.append(
                [
                    "سماد — جمع خام",
                    c.date_collecte,
                    c.batiment.nom,
                    "",
                    c.quantite_brute_kg,
                    "كغ",
                ]
            )
        for t in traitements_qs:
            rows.append(
                [
                    "سماد — معالجة",
                    t.date_traitement,
                    t.produit_fini.designation,
                    t.methode,
                    t.quantite_obtenue_kg,
                    "كغ",
                ]
            )
        return _csv_response("oeufs_fertilisant", headers, rows)

    lots_qs = LotElevage.objects.order_by("-date_ouverture")
    batiments_qs = Batiment.objects.filter(actif=True).order_by("nom")
    if branche is not None:
        lots_qs = lots_qs.filter(branche=branche)
        batiments_qs = batiments_qs.filter(branche=branche)
    lots = lots_qs
    batiments = batiments_qs
    oeufs_page = _paginate(oeufs_qs, request.GET.get("page_oeufs", 1))
    collectes_page = _paginate(collectes_qs, request.GET.get("page_collectes", 1))

    par_lot_list = list(par_lot_oeufs)
    chart_json = json.dumps(
        {
            "oeufs_labels": [r["lot__designation"][:18] for r in par_lot_list],
            "oeufs_values": [r["oeufs"] for r in par_lot_list],
            "fertilisant": {
                "brut": float(total_brut),
                "obtenu": float(total_obtenu),
                "rendement": rendement_global,
            },
        },
        ensure_ascii=False,
    )

    return render(
        request,
        "reporting/oeufs_fertilisant.html",
        {
            "title": "تقرير البيض والسماد",
            "total_oeufs": total_oeufs,
            "total_plateaux": total_plateaux,
            "total_oeufs_hors": total_oeufs_hors,
            "oeufs_page": oeufs_page,
            "par_lot_oeufs": par_lot_list,
            "collectes_page": collectes_page,
            "traitements": traitements_qs,
            "total_brut": total_brut,
            "total_obtenu": total_obtenu,
            "rendement_global": rendement_global,
            "lots": lots,
            "batiments": batiments,
            "lot_pk": lot_pk,
            "batiment_pk": batiment_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "chart_json": chart_json,
            "active_branche": branche,
            "vue_globale": branche is None,
        },
    )


# ===========================================================================
# AJAX: quick-stats endpoints used by the reporting dashboard widgets
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def kpi_summary_json(request):
    """
    Return full KPI + chart data for the reporting dashboard.
    Called by the dashboard via fetch() on page load.

    Accepts optional ?date_debut=YYYY-MM-DD and ?date_fin=YYYY-MM-DD query
    params to filter period-based metrics (cash flow, production).
    Defaults to the last 12 months when not supplied.

    Response keys:
      dette_fournisseurs_totale         — float (DZD)
      creances_clients_totale           — float (DZD)
      nb_lots_ouverts                   — int
      nb_stocks_en_alerte               — int
      nb_ruptures_stock                 — int
      nb_factures_fournisseur_retard    — int
      nb_factures_client_retard         — int
      supplier_aging                    — {current, 1_30, 31_60, 61_90, over_90} | null
      client_aging                      — {current, 1_30, 31_60, 61_90, over_90} | null
      lots                              — [{designation, batiment, effectif_vivant, taux_mortalite, duree}]
      production_recent                 — [{lot, nb_oiseaux, poids_kg}]
      stock_status                      — {normal, alerte, valeur_totale}
      bl_clients                        — {brouillon, livre, facture, litige, total}
      cash_flow                         — {encaissements, reglements, depenses,
                                            retraits_associes, paie, sorties, solde_net}
      date_debut                        — str ISO
      date_fin                          — str ISO

    v1.4 (§3.5.5): every KPI below is scoped to the request's active
    branche (Vue par Branche) — supplier/client invoices, lots, stock,
    production, BL clients, and cash flow are all filtered consistently
    with the rest of the reporting suite; Vue Globale (`branche is None`)
    combines every branche, exactly as `reporting_dashboard` does.
    """
    from achats.models import FactureFournisseur
    from clients.models import BLClient, FactureClient
    from depenses.utils import get_cash_flow_summary
    from elevage.models import LotElevage
    from production.models import ProductionRecord
    from stock.models import StockIntrant

    today = datetime.date.today()
    branche = get_active_branche(request)

    # ── Date range (default: last 12 months) ──────────────────────────────
    date_debut, date_fin = _parse_dates(request)
    if not date_debut:
        date_debut = today - datetime.timedelta(days=365)
    if not date_fin:
        date_fin = today

    # ── Basic scalar KPIs ─────────────────────────────────────────────────
    dette_qs = FactureFournisseur.objects.filter(
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ]
    )
    creances_qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ]
    )
    if branche is not None:
        dette_qs = dette_qs.filter(branche=branche)
        creances_qs = creances_qs.filter(branche=branche)

    dette = dette_qs.aggregate(total=Sum("reste_a_payer"))["total"] or 0
    creances = creances_qs.aggregate(total=Sum("reste_a_payer"))["total"] or 0

    lots_ouverts_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
    stock_intrant_qs = StockIntrant.objects.all()
    if branche is not None:
        lots_ouverts_qs = lots_ouverts_qs.filter(branche=branche)
        stock_intrant_qs = stock_intrant_qs.filter(branche=branche)
    nb_lots_ouverts = lots_ouverts_qs.count()

    nb_stocks_alerte = stock_intrant_qs.filter(
        quantite__lte=F("intrant__seuil_alerte"),
        quantite__gt=0,
    ).count()

    nb_ruptures = stock_intrant_qs.filter(quantite__lte=0).count()

    nb_ff_retard = dette_qs.filter(date_echeance__lt=today).count()

    nb_fc_retard = creances_qs.filter(date_echeance__lt=today).count()

    # ── Supplier aging buckets ────────────────────────────────────────────
    open_ff = dette_qs.values("date_echeance", "reste_a_payer")

    sup_aging = {
        "current": 0.0,
        "1_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "over_90": 0.0,
    }
    for ff in open_ff:
        amt = float(ff["reste_a_payer"] or 0)
        if not ff["date_echeance"] or ff["date_echeance"] >= today:
            sup_aging["current"] += amt
        else:
            days = (today - ff["date_echeance"]).days
            if days <= 30:
                sup_aging["1_30"] += amt
            elif days <= 60:
                sup_aging["31_60"] += amt
            elif days <= 90:
                sup_aging["61_90"] += amt
            else:
                sup_aging["over_90"] += amt
    supplier_aging_out = sup_aging if sum(sup_aging.values()) > 0 else None

    # ── Client aging buckets ──────────────────────────────────────────────
    open_fc = creances_qs.values("date_echeance", "reste_a_payer")

    cli_aging = {
        "current": 0.0,
        "1_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "over_90": 0.0,
    }
    for fc in open_fc:
        amt = float(fc["reste_a_payer"] or 0)
        if not fc["date_echeance"] or fc["date_echeance"] >= today:
            cli_aging["current"] += amt
        else:
            days = (today - fc["date_echeance"]).days
            if days <= 30:
                cli_aging["1_30"] += amt
            elif days <= 60:
                cli_aging["31_60"] += amt
            elif days <= 90:
                cli_aging["61_90"] += amt
            else:
                cli_aging["over_90"] += amt
    client_aging_out = cli_aging if sum(cli_aging.values()) > 0 else None

    # ── Open lots summary ─────────────────────────────────────────────────
    lots_qs = lots_ouverts_qs.select_related("batiment").order_by("-date_ouverture")[
        :10
    ]
    lots_data = []
    for lot in lots_qs:
        lots_data.append(
            {
                "designation": lot.designation,
                "batiment": lot.batiment.nom,
                "effectif_vivant": lot.effectif_vivant,
                "taux_mortalite": float(lot.taux_mortalite),
                "duree": lot.duree_elevage,
            }
        )

    # ── Recent production (within period) ────────────────────────────────
    prod_qs = ProductionRecord.objects.filter(
        statut=ProductionRecord.STATUT_VALIDE,
        date_production__gte=date_debut,
        date_production__lte=date_fin,
    )
    if branche is not None:
        prod_qs = prod_qs.filter(branche=branche)
    prod_qs = prod_qs.select_related("lot").order_by("-date_production")[:6]
    prod_data = [
        {
            "lot": r.lot.designation[:22],
            "nb_oiseaux": r.nombre_oiseaux_abattus,
            "poids_kg": float(r.poids_total_kg),
        }
        for r in prod_qs
    ]

    # ── Stock status ──────────────────────────────────────────────────────
    all_si = stock_intrant_qs.select_related("intrant")
    nb_normal_s = nb_alerte_s = 0
    valeur_totale = 0.0
    for si in all_si:
        valeur_totale += float(si.valeur_stock)
        if si.en_alerte or si.quantite <= 0:
            nb_alerte_s += 1
        else:
            nb_normal_s += 1
    stock_status = {
        "normal": nb_normal_s,
        "alerte": nb_alerte_s,
        "valeur_totale": round(valeur_totale, 2),
    }

    # ── BL Clients status distribution ───────────────────────────────────
    bl_clients_qs = BLClient.objects.all()
    if branche is not None:
        bl_clients_qs = bl_clients_qs.filter(branche=branche)
    bl_counts = bl_clients_qs.values("statut").annotate(nb=Count("pk"))
    bl_map = {r["statut"]: r["nb"] for r in bl_counts}
    bl_clients = {
        "brouillon": bl_map.get("brouillon", 0),
        "livre": bl_map.get("livre", 0),
        "facture": bl_map.get("facture", 0),
        "litige": bl_map.get("litige", 0),
        "total": sum(bl_map.values()),
    }

    # ── Cash flow summary (for period) ────────────────────────────────────
    cf = get_cash_flow_summary(
        date_debut=date_debut, date_fin=date_fin, branche=branche
    )
    cash_flow = {
        "encaissements": round(float(cf["total_encaissements"]), 2),
        "reglements": round(float(cf["total_reglements_fournisseurs"]), 2),
        "depenses": round(float(cf["total_depenses"]), 2),
        "retraits_associes": round(float(cf["total_retraits_associes"]), 2),
        "paie": round(float(cf["total_paie"]), 2),
        "sorties": round(float(cf["total_sorties"]), 2),
        "solde_net": round(float(cf["solde_net"]), 2),
    }

    return JsonResponse(
        {
            "dette_fournisseurs_totale": float(dette),
            "creances_clients_totale": float(creances),
            "nb_lots_ouverts": nb_lots_ouverts,
            "nb_stocks_en_alerte": nb_stocks_alerte,
            "nb_ruptures_stock": nb_ruptures,
            "nb_factures_fournisseur_retard": nb_ff_retard,
            "nb_factures_client_retard": nb_fc_retard,
            "supplier_aging": supplier_aging_out,
            "client_aging": client_aging_out,
            "lots": lots_data,
            "production_recent": prod_data,
            "stock_status": stock_status,
            "bl_clients": bl_clients,
            "cash_flow": cash_flow,
            "date_debut": str(date_debut),
            "date_fin": str(date_fin),
            "vue_globale": branche is None,
        }
    )
