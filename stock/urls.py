"""
stock/urls.py

URL patterns for the stock domain:
  StockIntrant      : list, detail
  StockProduitFini  : list, detail
  StockMouvement    : list (unified audit trail)
  StockAjustement   : list, create
  Dashboard         : stock overview
  AJAX              : balance endpoints for intrant and produit fini
"""

from django.urls import path
from stock import views

app_name = "stock"

urlpatterns = [
    # ── Dashboard ───────────────────────────────────────────────────────
    path(
        "",
        views.stock_dashboard,
        name="dashboard",
    ),
    # ── StockIntrant ────────────────────────────────────────────────────
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
    # ── StockMouvement (unified read-only audit trail) ──────────────────
    path(
        "mouvements/",
        views.stock_mouvement_list,
        name="stock_mouvement_list",
    ),
    # ── StockAjustement ─────────────────────────────────────────────────
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
    # ── AJAX ────────────────────────────────────────────────────────────
    # Returns current StockIntrant balance + PMP for a single intrant
    path(
        "intrants/<int:pk>/balance.json",
        views.stock_intrant_balance_json,
        name="stock_intrant_balance_json",
    ),
    # Returns current StockProduitFini balance for a single produit fini
    path(
        "produits-finis/<int:pk>/balance.json",
        views.stock_produit_fini_balance_json,
        name="stock_produit_fini_balance_json",
    ),
]
