"""
production/urls.py

URL patterns for the production domain:
  ProduitFini      : list, create, detail, edit, toggle-active
  ProductionRecord : list, create, detail, edit, validate, delete
  Dashboard        : production overview
  AJAX             : lot effectif and produit fini stock endpoints
"""

from django.urls import path
from production import views

app_name = "production"

urlpatterns = [
    # ── Dashboard ───────────────────────────────────────────────────────
    path(
        "",
        views.production_dashboard,
        name="dashboard",
    ),
    # ── ProduitFini ─────────────────────────────────────────────────────
    path(
        "produits/",
        views.produit_fini_list,
        name="produit_fini_list",
    ),
    path(
        "produits/creer/",
        views.produit_fini_create,
        name="produit_fini_create",
    ),
    path(
        "produits/<int:pk>/",
        views.produit_fini_detail,
        name="produit_fini_detail",
    ),
    path(
        "produits/<int:pk>/modifier/",
        views.produit_fini_edit,
        name="produit_fini_edit",
    ),
    path(
        "produits/<int:pk>/toggle-actif/",
        views.produit_fini_toggle_active,
        name="produit_fini_toggle_active",
    ),
    # ── ProductionRecord ────────────────────────────────────────────────
    path(
        "enregistrements/",
        views.production_record_list,
        name="production_record_list",
    ),
    # Create from scratch (lot chosen in form)
    path(
        "enregistrements/creer/",
        views.production_record_create,
        name="production_record_create",
    ),
    # Create pre-scoped to a specific lot
    path(
        "lots/<int:lot_pk>/enregistrements/creer/",
        views.production_record_create,
        name="production_record_create_for_lot",
    ),
    path(
        "enregistrements/<int:pk>/",
        views.production_record_detail,
        name="production_record_detail",
    ),
    path(
        "enregistrements/<int:pk>/modifier/",
        views.production_record_edit,
        name="production_record_edit",
    ),
    # POST-only: BROUILLON → VALIDE transition
    path(
        "enregistrements/<int:pk>/valider/",
        views.production_record_valider,
        name="production_record_valider",
    ),
    # POST-only: delete a BROUILLON record
    path(
        "enregistrements/<int:pk>/supprimer/",
        views.production_record_delete,
        name="production_record_delete",
    ),
    # ── AJAX ────────────────────────────────────────────────────────────
    # Returns lot.effectif_vivant for the bird-count guard in the form
    path(
        "lots/<int:lot_pk>/effectif.json",
        views.lot_effectif_json,
        name="lot_effectif_json",
    ),
    # Returns current StockProduitFini balance for a single produit fini
    path(
        "produits/<int:pk>/stock.json",
        views.produit_fini_stock_json,
        name="produit_fini_stock_json",
    ),
    path(
        "produits/<int:pk>/detail.json",
        views.produit_fini_detail_json,
        name="produit_fini_detail_json",
    ),
    # Quick-create a ProduitFini from the production record form modal
    path(
        "produits/creer-ajax/",
        views.produit_fini_create_ajax,
        name="produit_fini_create_ajax",
    ),
]
