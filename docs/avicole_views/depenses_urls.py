"""
depenses/urls.py

URL patterns for the operational expense (dépenses) module:
  CategorieDepense, Depense.
"""

from django.urls import path
from depenses import views

app_name = "depenses"

urlpatterns = [

    # ── Dashboard ────────────────────────────────────────────────────────
    path(
        "",
        views.depenses_dashboard,
        name="dashboard",
    ),

    # ── CategorieDepense ─────────────────────────────────────────────────
    path(
        "categories/",
        views.categorie_depense_list,
        name="categorie_depense_list",
    ),
    path(
        "categories/creer/",
        views.categorie_depense_create,
        name="categorie_depense_create",
    ),
    path(
        "categories/<int:pk>/modifier/",
        views.categorie_depense_edit,
        name="categorie_depense_edit",
    ),
    # POST-only: toggle actif/inactif
    path(
        "categories/<int:pk>/toggle-actif/",
        views.categorie_depense_toggle_active,
        name="categorie_depense_toggle_active",
    ),

    # ── Depense ──────────────────────────────────────────────────────────
    path(
        "depenses/",
        views.depense_list,
        name="depense_list",
    ),
    path(
        "depenses/creer/",
        views.depense_create,
        name="depense_create",
    ),
    path(
        "depenses/<int:pk>/",
        views.depense_detail,
        name="depense_detail",
    ),
    path(
        "depenses/<int:pk>/modifier/",
        views.depense_edit,
        name="depense_edit",
    ),
    # POST-only: hard delete
    path(
        "depenses/<int:pk>/supprimer/",
        views.depense_delete,
        name="depense_delete",
    ),
    # Print: Pièce Justificative de Dépense (spec §16.8)
    path(
        "depenses/<int:pk>/imprimer/",
        views.depense_print,
        name="depense_print",
    ),
]
