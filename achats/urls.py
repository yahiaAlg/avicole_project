"""
achats/urls.py

URL patterns for the full supplier procurement cycle:
  BLFournisseur, FactureFournisseur, ReglementFournisseur, AcompteFournisseur.
"""

from django.urls import path
from achats import views

app_name = "achats"

urlpatterns = [
    # ── BL Fournisseur ──────────────────────────────────────────────────
    path(
        "bls/",
        views.bl_fournisseur_list,
        name="bl_fournisseur_list",
    ),
    path(
        "bls/creer/",
        views.bl_fournisseur_create,
        name="bl_fournisseur_create",
    ),
    # Create pre-scoped to a specific supplier
    path(
        "fournisseurs/<int:fournisseur_pk>/bls/creer/",
        views.bl_fournisseur_create,
        name="bl_fournisseur_create_for_fournisseur",
    ),
    path(
        "bls/<int:pk>/",
        views.bl_fournisseur_detail,
        name="bl_fournisseur_detail",
    ),
    path(
        "bls/<int:pk>/modifier/",
        views.bl_fournisseur_edit,
        name="bl_fournisseur_edit",
    ),
    path(
        "bls/<int:pk>/supprimer/",
        views.bl_fournisseur_delete,
        name="bl_fournisseur_delete",
    ),
    path(
        "bls/<int:pk>/changer-statut/",
        views.bl_fournisseur_change_statut,
        name="bl_fournisseur_change_statut",
    ),
    path(
        "bls/<int:pk>/imprimer/",
        views.bl_fournisseur_print,
        name="bl_fournisseur_print",
    ),
    # ── Facture Fournisseur ─────────────────────────────────────────────
    path(
        "factures/",
        views.facture_fournisseur_list,
        name="facture_fournisseur_list",
    ),
    path(
        "factures/creer/",
        views.facture_fournisseur_create,
        name="facture_fournisseur_create",
    ),
    path(
        "factures/<int:pk>/",
        views.facture_fournisseur_detail,
        name="facture_fournisseur_detail",
    ),
    path(
        "factures/<int:pk>/imprimer/",
        views.facture_fournisseur_print,
        name="facture_fournisseur_print",
    ),
    path(
        "factures/<int:pk>/litige/",
        views.facture_fournisseur_toggle_litige,
        name="facture_fournisseur_toggle_litige",
    ),
    # ── Règlement Fournisseur ───────────────────────────────────────────
    path(
        "reglements/",
        views.reglement_fournisseur_list,
        name="reglement_fournisseur_list",
    ),
    path(
        "reglements/creer/",
        views.reglement_fournisseur_create,
        name="reglement_fournisseur_create",
    ),
    path(
        "reglements/<int:pk>/",
        views.reglement_fournisseur_detail,
        name="reglement_fournisseur_detail",
    ),
    # ── Acompte Fournisseur ─────────────────────────────────────────────
    path(
        "acomptes/",
        views.acompte_fournisseur_list,
        name="acompte_fournisseur_list",
    ),
    path(
        "acomptes/<int:pk>/",
        views.acompte_fournisseur_detail,
        name="acompte_fournisseur_detail",
    ),
    # ── Tableau de bord fournisseur ─────────────────────────────────────
    path(
        "fournisseurs/<int:pk>/tableau-de-bord/",
        views.fournisseur_tableau_de_bord,
        name="fournisseur_tableau_de_bord",
    ),
    # ── AJAX ────────────────────────────────────────────────────────────
    path(
        "bls/totaux.json",
        views.bl_lignes_total_json,
        name="bl_lignes_total_json",
    ),
    path(
        "fournisseurs/<int:pk>/dette.json",
        views.fournisseur_dette_json,
        name="fournisseur_dette_json",
    ),
]
