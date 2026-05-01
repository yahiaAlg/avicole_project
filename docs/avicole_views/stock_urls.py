"""
stock/urls.py

URL patterns for stock management:
  StockIntrant, StockProduitFini, StockMouvement, StockAjustement.
"""

from django.urls import path
from stock import views

app_name = "stock"

urlpatterns = [
    # ── StockIntrant ─────────────────────────────────────────────────────
    path(
        "intrants/",
        views.stock_intrant_list,
        name="stock_intrant_list",
    ),
    path(
        "intrants/<int:pk>/",
        views.stock_intrant_detail,
        name="stock_intrant_detail",
    ),
    path(
        "intrants/<int:pk>/json/",
        views.stock_intrant_json,
        name="stock_intrant_json",
    ),
    # ── StockProduitFini ────────────────────────────────────────────────
    path(
        "produits-finis/",
        views.stock_produit_fini_list,
        name="stock_produit_fini_list",
    ),
    path(
        "produits-finis/<int:pk>/",
        views.stock_produit_fini_detail,
        name="stock_produit_fini_detail",
    ),
    path(
        "produits-finis/<int:pk>/json/",
        views.stock_produit_fini_balance_json,
        name="stock_produit_fini_balance_json",
    ),
    # ── StockMouvement ───────────────────────────────────────────────────
    path(
        "mouvements/",
        views.stock_mouvement_list,
        name="stock_mouvement_list",
    ),
    # ── StockAjustement ──────────────────────────────────────────────────
    path(
        "ajustements/",
        views.stock_ajustement_list,
        name="stock_ajustement_list",
    ),
    path(
        "ajustements/creer/",
        views.stock_ajustement_create,
        name="stock_ajustement_create",
    ),
]
