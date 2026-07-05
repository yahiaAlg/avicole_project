"""
clients/urls.py

URL patterns for the full client AR (accounts-receivable) cycle:
  Client, BLClient, FactureClient, PaiementClient.
"""

from django.urls import path
from clients import views

app_name = "clients"

urlpatterns = [
    # ── Dashboard ───────────────────────────────────────────────────────
    path(
        "",
        views.clients_dashboard,
        name="dashboard",
    ),
    # ── Client ──────────────────────────────────────────────────────────
    path(
        "clients/",
        views.client_list,
        name="client_list",
    ),
    path(
        "clients/creer/",
        views.client_create,
        name="client_create",
    ),
    path(
        "clients/<int:pk>/",
        views.client_detail,
        name="client_detail",
    ),
    path(
        "clients/<int:pk>/modifier/",
        views.client_edit,
        name="client_edit",
    ),
    path(
        "clients/<int:pk>/toggle-actif/",
        views.client_toggle_active,
        name="client_toggle_active",
    ),
    # ── BL Client ────────────────────────────────────────────────────────
    path(
        "bls/",
        views.bl_client_list,
        name="bl_client_list",
    ),
    # Create from scratch (client chosen in form)
    path(
        "bls/creer/",
        views.bl_client_create,
        name="bl_client_create",
    ),
    # Create pre-scoped to a specific client
    path(
        "clients/<int:client_pk>/bls/creer/",
        views.bl_client_create,
        name="bl_client_create_for_client",
    ),
    path(
        "bls/<int:pk>/",
        views.bl_client_detail,
        name="bl_client_detail",
    ),
    path(
        "bls/<int:pk>/modifier/",
        views.bl_client_edit,
        name="bl_client_edit",
    ),
    # POST-only: BROUILLON → LIVRE transition (with stock check)
    path(
        "bls/<int:pk>/valider/",
        views.bl_client_valider,
        name="bl_client_valider",
    ),
    # POST-only: manual statut change (brouillon↔litige, livre→litige)
    path(
        "bls/<int:pk>/changer-statut/",
        views.bl_client_change_statut,
        name="bl_client_change_statut",
    ),
    # POST-only: delete a BROUILLON BL
    path(
        "bls/<int:pk>/supprimer/",
        views.bl_client_delete,
        name="bl_client_delete",
    ),
    path(
        "bls/<int:pk>/imprimer/",
        views.bl_client_print,
        name="bl_client_print",
    ),
    # ── Facture Client ───────────────────────────────────────────────────
    path(
        "factures/",
        views.facture_client_list,
        name="facture_client_list",
    ),
    # Create from scratch (client chosen in form)
    path(
        "factures/creer/",
        views.facture_client_create,
        name="facture_client_create",
    ),
    # Create pre-scoped to a specific client
    path(
        "clients/<int:client_pk>/factures/creer/",
        views.facture_client_create,
        name="facture_client_create_for_client",
    ),
    path(
        "factures/<int:pk>/",
        views.facture_client_detail,
        name="facture_client_detail",
    ),
    path(
        "factures/<int:pk>/imprimer/",
        views.facture_client_print,
        name="facture_client_print",
    ),
    path(
        "factures/<int:pk>/supprimer/",
        views.facture_client_delete,
        name="facture_client_delete",
    ),
    # ── Paiement Client ──────────────────────────────────────────────────
    path(
        "paiements/",
        views.paiement_client_list,
        name="paiement_client_list",
    ),
    # Create from scratch (client chosen in form)
    path(
        "paiements/creer/",
        views.paiement_client_create,
        name="paiement_client_create",
    ),
    # Create pre-scoped to a specific client
    path(
        "clients/<int:client_pk>/paiements/creer/",
        views.paiement_client_create,
        name="paiement_client_create_for_client",
    ),
    path(
        "paiements/<int:pk>/",
        views.paiement_client_detail,
        name="paiement_client_detail",
    ),
    path(
        "paiements/<int:pk>/imprimer/",
        views.paiement_client_print,
        name="paiement_client_print",
    ),
    # ── Fiche des dettes client ──────────────────────────────────────────
    path(
        "clients/<int:pk>/fiche-dettes/",
        views.fiche_dettes_client,
        name="fiche_dettes_client",
    ),
    # ── Prix Marché ──────────────────────────────────────────────────────
    path(
        "prix-marche/",
        views.prix_marche_list,
        name="prix_marche_list",
    ),
    path(
        "prix-marche/creer/",
        views.prix_marche_create,
        name="prix_marche_create",
    ),
    path(
        "prix-marche/<int:pk>/modifier/",
        views.prix_marche_edit,
        name="prix_marche_edit",
    ),
    path(
        "prix-marche/<int:pk>/supprimer/",
        views.prix_marche_delete,
        name="prix_marche_delete",
    ),
    # ── AJAX ─────────────────────────────────────────────────────────────
    path(
        "clients/<int:pk>/solde.json",
        views.client_solde_json,
        name="client_solde_json",
    ),
    # ── AbonnementClient ─────────────────────────────────────────────────
    path(
        "abonnements/",
        views.abonnement_list,
        name="abonnement_list",
    ),
    path(
        "abonnements/creer/",
        views.abonnement_create,
        name="abonnement_create",
    ),
    path(
        "clients/<int:client_pk>/abonnements/creer/",
        views.abonnement_create,
        name="abonnement_create_for_client",
    ),
    path(
        "abonnements/<int:pk>/",
        views.abonnement_detail,
        name="abonnement_detail",
    ),
    path(
        "abonnements/<int:pk>/modifier/",
        views.abonnement_edit,
        name="abonnement_edit",
    ),
    path(
        "abonnements/<int:pk>/toggle-statut/",
        views.abonnement_toggle_statut,
        name="abonnement_toggle_statut",
    ),
    # ── LivraisonPartielle ───────────────────────────────────────────────
    path(
        "abonnements/<int:abonnement_pk>/livraisons/creer/",
        views.livraison_partielle_create,
        name="livraison_partielle_create",
    ),
    path(
        "livraisons/<int:pk>/supprimer/",
        views.livraison_partielle_delete,
        name="livraison_partielle_delete",
    ),
    # ── VoyageLivraison ──────────────────────────────────────────────────
    path(
        "voyages/",
        views.voyage_list,
        name="voyage_list",
    ),
    path(
        "voyages/creer/",
        views.voyage_create,
        name="voyage_create",
    ),
    path(
        "voyages/<int:pk>/",
        views.voyage_detail,
        name="voyage_detail",
    ),
    path(
        "voyages/<int:pk>/modifier/",
        views.voyage_edit,
        name="voyage_edit",
    ),
]
