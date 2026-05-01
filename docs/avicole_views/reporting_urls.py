"""
reporting/urls.py

URL patterns for all reports and the reporting dashboard.

Report access levels (enforced in views, not at URL level):
  Financial roles (admin, manager, comptable):
    supplier aging, settlement history, distribution, debt dashboard,
    lot profitability, cash flow, depenses, production dashboard,
    client receivables aging.
  All roles (including operateur):
    stock status, consumption by lot, BL client history,
    stock movement print.
"""

from django.urls import path
from reporting import views

app_name = "reporting"

urlpatterns = [

    # ── Reporting Dashboard ──────────────────────────────────────────────
    path(
        "",
        views.reporting_dashboard,
        name="dashboard",
    ),

    # ── 20.1 Balance Fournisseur par Ancienneté ──────────────────────────
    path(
        "fournisseurs/anciennete/",
        views.rapport_supplier_aging,
        name="supplier_aging",
    ),

    # ── 20.2 Historique des Règlements ───────────────────────────────────
    path(
        "fournisseurs/reglements/",
        views.rapport_historique_reglements,
        name="historique_reglements",
    ),

    # ── 20.3 Répartition des Règlements ──────────────────────────────────
    path(
        "fournisseurs/repartition-reglements/",
        views.rapport_repartition_reglements,
        name="repartition_reglements",
    ),

    # ── 20.4 Dettes en Cours par Fournisseur ─────────────────────────────
    path(
        "fournisseurs/dettes/",
        views.rapport_dettes_fournisseurs,
        name="dettes_fournisseurs",
    ),

    # ── 20.5 Rentabilité par Lot ─────────────────────────────────────────
    path(
        "lots/rentabilite/",
        views.rapport_rentabilite_lot,
        name="rentabilite_lot",
    ),

    # ── 20.6 Résumé de Trésorerie ────────────────────────────────────────
    path(
        "tresorerie/",
        views.rapport_cash_flow,
        name="cash_flow",
    ),

    # ── 20.7 État des Stocks ─────────────────────────────────────────────
    path(
        "stocks/etat/",
        views.rapport_etat_stocks,
        name="etat_stocks",
    ),

    # ── Consommation par Lot (cross-lot) ─────────────────────────────────
    path(
        "lots/consommation/",
        views.rapport_consommation_lot,
        name="consommation_lot",
    ),

    # ── Consommation Detail (single lot drill-down) ───────────────────────
    path(
        "lots/<int:lot_pk>/consommation/",
        views.rapport_consommation_lot_detail,
        name="consommation_lot_detail",
    ),

    # ── Créances Clients (receivables aging) ─────────────────────────────
    path(
        "clients/creances/",
        views.rapport_creances_clients,
        name="creances_clients",
    ),

    # ── Historique BL Clients ─────────────────────────────────────────────
    path(
        "clients/bls/",
        views.rapport_historique_bl_clients,
        name="historique_bl_clients",
    ),

    # ── Tableau de Bord Production ────────────────────────────────────────
    path(
        "production/",
        views.rapport_production_dashboard,
        name="production_dashboard",
    ),

    # ── Rapport Dépenses ─────────────────────────────────────────────────
    path(
        "depenses/",
        views.rapport_depenses,
        name="depenses",
    ),

    # ── Print: Bon de Mouvement de Stock (spec §9.11) ─────────────────────
    path(
        "stocks/mouvements/<int:pk>/imprimer/",
        views.stock_mouvement_print,
        name="stock_mouvement_print",
    ),

    # ── AJAX: KPI summary widget ─────────────────────────────────────────
    path(
        "kpi.json",
        views.kpi_summary_json,
        name="kpi_summary_json",
    ),
]
