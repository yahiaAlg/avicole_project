"""
elevage/urls.py

URL patterns for the lot d'élevage domain:
  LotElevage   : list, create, detail, edit, close
  Mortalite    : create, edit, delete, list (cross-lot)
  Consommation : create, edit, delete, list (cross-lot)
  Dashboard    : elevage overview
  AJAX         : lot KPI endpoint
"""

from django.urls import path
from elevage import views

app_name = "elevage"

urlpatterns = [

    # ── Dashboard ───────────────────────────────────────────────────────
    path(
        "",
        views.elevage_dashboard,
        name="dashboard",
    ),

    # ── LotElevage ──────────────────────────────────────────────────────
    path(
        "lots/",
        views.lot_list,
        name="lot_list",
    ),
    path(
        "lots/creer/",
        views.lot_create,
        name="lot_create",
    ),
    path(
        "lots/<int:pk>/",
        views.lot_detail,
        name="lot_detail",
    ),
    path(
        "lots/<int:pk>/modifier/",
        views.lot_edit,
        name="lot_edit",
    ),
    path(
        "lots/<int:pk>/fermer/",
        views.lot_fermer,
        name="lot_fermer",
    ),

    # ── Mortalite ────────────────────────────────────────────────────────
    path(
        "mortalites/",
        views.mortalite_list,
        name="mortalite_list",
    ),
    path(
        "lots/<int:lot_pk>/mortalites/creer/",
        views.mortalite_create,
        name="mortalite_create",
    ),
    path(
        "mortalites/<int:pk>/modifier/",
        views.mortalite_edit,
        name="mortalite_edit",
    ),
    path(
        "mortalites/<int:pk>/supprimer/",
        views.mortalite_delete,
        name="mortalite_delete",
    ),

    # ── Consommation ─────────────────────────────────────────────────────
    path(
        "consommations/",
        views.consommation_list,
        name="consommation_list",
    ),
    path(
        "lots/<int:lot_pk>/consommations/creer/",
        views.consommation_create,
        name="consommation_create",
    ),
    path(
        "consommations/<int:pk>/modifier/",
        views.consommation_edit,
        name="consommation_edit",
    ),
    path(
        "consommations/<int:pk>/supprimer/",
        views.consommation_delete,
        name="consommation_delete",
    ),

    # ── AJAX ────────────────────────────────────────────────────────────
    path(
        "lots/<int:pk>/kpi.json",
        views.lot_kpi_json,
        name="lot_kpi_json",
    ),
]
