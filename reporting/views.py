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
"""

import csv
import datetime
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, F, Max, Min, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

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
        "Vous n'avez pas les permissions nécessaires pour accéder à ce rapport.",
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
    """
    role = _get_role(request.user)
    today = datetime.date.today()

    # Quick KPIs for the dashboard header
    from achats.models import FactureFournisseur
    from clients.models import FactureClient
    from stock.models import StockIntrant

    nb_factures_retard_fournisseur = FactureFournisseur.objects.filter(
        statut__in=[
            FactureFournisseur.STATUT_NON_PAYE,
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
        ],
        date_echeance__lt=today,
    ).count()

    nb_factures_retard_client = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
        date_echeance__lt=today,
    ).count()

    nb_stocks_alerte = StockIntrant.objects.filter(
        quantite__lte=F("intrant__seuil_alerte"),
    ).count()

    return render(
        request,
        "reporting/dashboard.html",
        {
            "title": "Rapports & Tableaux de Bord",
            "role": role,
            "is_financial": role in FINANCIAL_ROLES,
            "is_admin": role in ADMIN_ROLES,
            "nb_factures_retard_fournisseur": nb_factures_retard_fournisseur,
            "nb_factures_retard_client": nb_factures_retard_client,
            "nb_stocks_alerte": nb_stocks_alerte,
            "today": today,
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

    fournisseur_pk = request.GET.get("fournisseur", "").strip()
    fournisseur_obj = None
    if fournisseur_pk:
        fournisseur_obj = get_object_or_404(Fournisseur, pk=fournisseur_pk)

    buckets = get_supplier_aging_buckets(fournisseur=fournisseur_obj)

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

    return render(
        request,
        "reporting/supplier_aging.html",
        {
            "title": "Balance Fournisseur par Ancienneté",
            "buckets": buckets,
            "totaux": totaux,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "fournisseur_obj": fournisseur_obj,
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

    qs = ReglementFournisseur.objects.select_related(
        "fournisseur", "created_by"
    ).order_by("-date_reglement", "-created_at")

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

    return render(
        request,
        "reporting/historique_reglements.html",
        {
            "title": "Historique des Règlements",
            "page": page,
            "totaux": totaux,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "mode": mode,
            "mode_choices": ReglementFournisseur.MODE_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "allocations_map": allocations_map,
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

    date_debut, date_fin = _parse_dates(request)
    fournisseur_pk = request.GET.get("fournisseur", "").strip()

    # Base: allocations whose règlement falls in the period
    qs = AllocationReglement.objects.select_related(
        "reglement__fournisseur",
        "facture",
    )
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

    return render(
        request,
        "reporting/repartition_reglements.html",
        {
            "title": "Répartition des Règlements",
            "page": page,
            "supplier_totals": supplier_totals,
            "mode_totals": mode_totals_display,
            "grand_total": grand_total,
            "fournisseurs": fournisseurs,
            "fournisseur_pk": fournisseur_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
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

    fournisseurs_all = Fournisseur.objects.filter(actif=True).order_by("nom")

    fournisseur_pk = request.GET.get("fournisseur", "").strip()
    fournisseur_obj = None
    if fournisseur_pk:
        fournisseur_obj = get_object_or_404(Fournisseur, pk=fournisseur_pk)

    fournisseurs_qs = [fournisseur_obj] if fournisseur_obj else list(fournisseurs_all)

    rows = []
    for fournisseur in fournisseurs_qs:
        factures_ouvertes = FactureFournisseur.objects.filter(
            fournisseur=fournisseur,
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ],
        )
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
            ReglementFournisseur.objects.filter(fournisseur=fournisseur)
            .order_by("-date_reglement")
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

    return render(
        {
            "title": "Dettes en Cours par Fournisseur",
            "rows": rows,
            "grand_total_dette": grand_total_dette,
            "today": today,
            "fournisseurs": fournisseurs_all,
            "fournisseur_pk": fournisseur_pk,
            "fournisseur_obj": fournisseur_obj,
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

    qs = LotElevage.objects.select_related(
        "fournisseur_poussins", "batiment", "created_by"
    ).order_by("-date_ouverture")

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
        lot_obj = get_object_or_404(LotElevage, pk=lot_pk)
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

    return render(
        request,
        "reporting/rentabilite_lot.html",
        {
            "title": "Rentabilité par Lot",
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
      Outflows = ReglementFournisseur + Depenses
      Net      = Inflows − Outflows

    Filters:  ?date_debut=  ?date_fin=
    Export:   ?export=csv (three separate sections)

    Access: FINANCIAL_ROLES
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.utils import get_cash_flow_summary

    date_debut, date_fin = _parse_dates(request)

    # Default to current month if no range provided
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(day=1)
        date_fin = today

    summary = get_cash_flow_summary(date_debut=date_debut, date_fin=date_fin)

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

        return _csv_response("resume_tresorerie", headers, rows)

    return render(
        request,
        "reporting/cash_flow.html",
        {
            "title": "Résumé de Trésorerie",
            "summary": summary,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "today": today,
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
    """
    from intrants.models import CategorieIntrant
    from stock.models import StockIntrant, StockProduitFini

    segment = request.GET.get("segment", "").strip()
    categorie_pk = request.GET.get("categorie", "").strip()
    alerte_only = request.GET.get("alerte", "") == "1"
    q = request.GET.get("q", "").strip()

    # ── Intrants ──────────────────────────────────────────────────────────
    stocks_intrants = []
    if segment in ("", "intrants"):
        si_qs = StockIntrant.objects.select_related("intrant__categorie").order_by(
            "intrant__categorie__libelle", "intrant__designation"
        )
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
        spf_qs = StockProduitFini.objects.select_related("produit_fini").order_by(
            "produit_fini__type_produit", "produit_fini__designation"
        )
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

    return render(
        request,
        "reporting/etat_stocks.html",
        {
            "title": "État des Stocks",
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
    """
    from elevage.models import Consommation, LotElevage
    from intrants.models import CategorieIntrant, Intrant

    qs = Consommation.objects.select_related(
        "lot", "intrant__categorie", "created_by"
    ).order_by("-date", "lot__designation")

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
    lots = LotElevage.objects.order_by("-date_ouverture")
    intrants = Intrant.objects.filter(
        actif=True, categorie__consommable_en_lot=True
    ).select_related("categorie")
    categories = CategorieIntrant.objects.filter(actif=True)

    return render(
        request,
        "reporting/consommation_lot.html",
        {
            "title": "Consommation par Lot",
            "page": page,
            "par_intrant": par_intrant,
            "nb_total": nb_total,
            "lots": lots,
            "intrants": intrants,
            "categories": categories,
            "lot_pk": lot_pk,
            "intrant_pk": intrant_pk,
            "categorie_pk": categorie_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
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
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from clients.models import Client
    from clients.utils import get_client_aging_buckets

    client_pk = request.GET.get("client", "").strip()
    client_obj = None
    if client_pk:
        client_obj = get_object_or_404(Client, pk=client_pk)

    buckets = get_client_aging_buckets(client=client_obj)

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

    return render(
        request,
        "reporting/creances_clients.html",
        {
            "title": "Créances Clients",
            "buckets": buckets,
            "totaux": totaux,
            "clients": clients,
            "client_pk": client_pk,
            "client_obj": client_obj,
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
    """
    from clients.models import BLClient, Client

    qs = (
        BLClient.objects.select_related("client", "created_by")
        .prefetch_related("lignes__produit_fini")
        .order_by("-date_bl")
    )

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
                "Oui" if bl.statut == BLClient.STATUT_FACTURE else "Non",
            ]
            for bl in qs
        ]
        return _csv_response("historique_bl_clients", headers, rows)

    page = _paginate(qs, request.GET.get("page"))
    clients = Client.objects.filter(actif=True).order_by("nom")

    return render(
        request,
        "reporting/historique_bl_clients.html",
        {
            "title": "Historique BL Clients",
            "page": page,
            "nb_total": agg["nb"] or 0,
            "clients": clients,
            "client_pk": client_pk,
            "statut": statut,
            "statut_choices": BLClient.STATUT_CHOICES,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "q": q,
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
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from production.utils import get_production_dashboard

    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(month=1, day=1)
        date_fin = today

    rows = get_production_dashboard(date_debut=date_debut, date_fin=date_fin)

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

    return render(
        request,
        "reporting/production_dashboard.html",
        {
            "title": "Tableau de Bord Production",
            "rows": rows,
            "totaux": totaux,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "today": today,
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
    """
    if not _require_role(request, FINANCIAL_ROLES):
        return redirect("reporting:dashboard")

    from depenses.models import CategorieDepense, Depense
    from depenses.utils import get_depenses_summary
    from elevage.models import LotElevage

    date_debut, date_fin = _parse_dates(request)
    today = datetime.date.today()
    if not date_debut and not date_fin:
        date_debut = today.replace(day=1)
        date_fin = today

    categorie_pk = request.GET.get("categorie", "").strip()
    lot_pk = request.GET.get("lot", "").strip()

    summary = get_depenses_summary(date_debut=date_debut, date_fin=date_fin)

    # Filtered expense queryset for the detail table
    depenses_qs = Depense.objects.select_related(
        "categorie", "lot", "enregistre_par"
    ).order_by("-date")
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
    lots = LotElevage.objects.order_by("-date_ouverture")

    return render(
        request,
        "reporting/depenses.html",
        {
            "title": "Rapport des Dépenses",
            "summary": summary,
            "page": page,
            "categories": categories,
            "lots": lots,
            "categorie_pk": categorie_pk,
            "lot_pk": lot_pk,
            "date_debut": date_debut,
            "date_fin": date_fin,
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
    """
    from elevage.models import LotElevage
    from elevage.utils import get_lot_summary

    lot = get_object_or_404(LotElevage, pk=lot_pk)
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

    return render(
        request,
        "reporting/consommation_lot_detail.html",
        {
            "title": f"Consommation — {lot.designation}",
            "lot": lot,
            "summary": summary,
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
    """
    from core.models import CompanyInfo
    from stock.models import StockMouvement

    mouvement = get_object_or_404(StockMouvement, pk=pk)
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
# AJAX: quick-stats endpoints used by the reporting dashboard widgets
# ===========================================================================


@login_required(login_url=LOGIN_URL)
def kpi_summary_json(request):
    """
    Return top-level KPI figures for the reporting dashboard header widget.
    Called by the dashboard via fetch() on page load.

    Response keys:
      dette_fournisseurs_totale   — Decimal (DZD)
      creances_clients_totale     — Decimal (DZD)
      nb_lots_ouverts             — int
      nb_stocks_en_alerte         — int
      nb_factures_fournisseur_retard — int
      nb_factures_client_retard   — int
    """
    from achats.models import FactureFournisseur
    from clients.models import FactureClient
    from elevage.models import LotElevage
    from stock.models import StockIntrant

    today = datetime.date.today()

    dette = (
        FactureFournisseur.objects.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ]
        ).aggregate(total=Sum("reste_a_payer"))["total"]
        or 0
    )

    creances = (
        FactureClient.objects.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ]
        ).aggregate(total=Sum("reste_a_payer"))["total"]
        or 0
    )

    return JsonResponse(
        {
            "dette_fournisseurs_totale": float(dette),
            "creances_clients_totale": float(creances),
            "nb_lots_ouverts": LotElevage.objects.filter(
                statut=LotElevage.STATUT_OUVERT
            ).count(),
            "nb_stocks_en_alerte": StockIntrant.objects.filter(
                quantite__lte=F("intrant__seuil_alerte"),
                quantite__gt=0,
            ).count(),
            "nb_ruptures_stock": StockIntrant.objects.filter(quantite__lte=0).count(),
            "nb_factures_fournisseur_retard": FactureFournisseur.objects.filter(
                statut__in=[
                    FactureFournisseur.STATUT_NON_PAYE,
                    FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
                ],
                date_echeance__lt=today,
            ).count(),
            "nb_factures_client_retard": FactureClient.objects.filter(
                statut__in=[
                    FactureClient.STATUT_NON_PAYEE,
                    FactureClient.STATUT_PARTIELLEMENT_PAYEE,
                ],
                date_echeance__lt=today,
            ).count(),
        }
    )
