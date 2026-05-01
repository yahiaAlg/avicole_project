"""
intrants/urls.py

URL patterns for master-data management:
  CategorieIntrant, TypeFournisseur, Fournisseur, Batiment, Intrant.
"""

from django.urls import path
from intrants import views

app_name = "intrants"

urlpatterns = [
    # ── CategorieIntrant ────────────────────────────────────────────────
    path(
        "categories/",
        views.categorie_intrant_list,
        name="categorie_intrant_list",
    ),
    path(
        "categories/creer/",
        views.categorie_intrant_create,
        name="categorie_intrant_create",
    ),
    path(
        "categories/<int:pk>/modifier/",
        views.categorie_intrant_edit,
        name="categorie_intrant_edit",
    ),
    path(
        "categories/<int:pk>/activer/",
        views.categorie_intrant_toggle_active,
        name="categorie_intrant_toggle_active",
    ),
    # ── TypeFournisseur ─────────────────────────────────────────────────
    path(
        "types-fournisseurs/",
        views.type_fournisseur_list,
        name="type_fournisseur_list",
    ),
    path(
        "types-fournisseurs/creer/",
        views.type_fournisseur_create,
        name="type_fournisseur_create",
    ),
    path(
        "types-fournisseurs/<int:pk>/modifier/",
        views.type_fournisseur_edit,
        name="type_fournisseur_edit",
    ),
    path(
        "types-fournisseurs/<int:pk>/activer/",
        views.type_fournisseur_toggle_active,
        name="type_fournisseur_toggle_active",
    ),
    # ── Fournisseur ─────────────────────────────────────────────────────
    path(
        "fournisseurs/",
        views.fournisseur_list,
        name="fournisseur_list",
    ),
    path(
        "fournisseurs/creer/",
        views.fournisseur_create,
        name="fournisseur_create",
    ),
    path(
        "fournisseurs/<int:pk>/",
        views.fournisseur_detail,
        name="fournisseur_detail",
    ),
    path(
        "fournisseurs/<int:pk>/modifier/",
        views.fournisseur_edit,
        name="fournisseur_edit",
    ),
    path(
        "fournisseurs/<int:pk>/activer/",
        views.fournisseur_toggle_active,
        name="fournisseur_toggle_active",
    ),
    # ── Batiment ────────────────────────────────────────────────────────
    path(
        "batiments/",
        views.batiment_list,
        name="batiment_list",
    ),
    path(
        "batiments/creer/",
        views.batiment_create,
        name="batiment_create",
    ),
    path(
        "batiments/<int:pk>/modifier/",
        views.batiment_edit,
        name="batiment_edit",
    ),
    path(
        "batiments/<int:pk>/activer/",
        views.batiment_toggle_active,
        name="batiment_toggle_active",
    ),
    # ── Intrant ─────────────────────────────────────────────────────────
    path(
        "intrants/",
        views.intrant_list,
        name="intrant_list",
    ),
    path(
        "intrants/creer/",
        views.intrant_create,
        name="intrant_create",
    ),
    path(
        "intrants/<int:pk>/",
        views.intrant_detail,
        name="intrant_detail",
    ),
    path(
        "intrants/<int:pk>/modifier/",
        views.intrant_edit,
        name="intrant_edit",
    ),
    path(
        "intrants/<int:pk>/activer/",
        views.intrant_toggle_active,
        name="intrant_toggle_active",
    ),
    # ── AJAX ────────────────────────────────────────────────────────────
    path(
        "intrants/<int:pk>/stock.json",
        views.intrant_stock_json,
        name="intrant_stock_json",
    ),
    path(
        "fournisseurs/<int:pk>/intrants.json",
        views.fournisseur_intrants_json,
        name="fournisseur_intrants_json",
    ),
]
