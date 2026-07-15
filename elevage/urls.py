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
        "lots/<int:lot_pk>/consommations-medicaments/creer/",
        views.consommation_medicament_create,
        name="consommation_medicament_create",
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
    # ── Consommation (médicament) — liste + dépense groupée ─────────────
    path(
        "consommations-medicaments/",
        views.consommation_medicament_list,
        name="consommation_medicament_list",
    ),
    path(
        "consommations-medicaments/paiement/creer/",
        views.consommation_medicament_paiement_create,
        name="consommation_medicament_paiement_create",
    ),
    # ── TransfertLot ─────────────────────────────────────────────────────
    path(
        "lots/<int:lot_pk>/transferer/",
        views.transfert_create,
        name="transfert_create",
    ),
    # ── PeseeEchantillon ─────────────────────────────────────────────────
    path(
        "lots/<int:lot_pk>/pesees/creer/",
        views.pesee_create,
        name="pesee_create",
    ),
    path(
        "pesees/<int:pk>/supprimer/",
        views.pesee_delete,
        name="pesee_delete",
    ),
    # ── RecolteOeufs ─────────────────────────────────────────────────────
    path(
        "recoltes-oeufs/",
        views.recolte_oeufs_list,
        name="recolte_oeufs_list",
    ),
    path(
        "lots/<int:lot_pk>/recoltes-oeufs/creer/",
        views.recolte_oeufs_create,
        name="recolte_oeufs_create",
    ),
    path(
        "recoltes-oeufs/<int:pk>/modifier/",
        views.recolte_oeufs_edit,
        name="recolte_oeufs_edit",
    ),
    path(
        "recoltes-oeufs/<int:pk>/supprimer/",
        views.recolte_oeufs_delete,
        name="recolte_oeufs_delete",
    ),
    # ── Suivi journalier (tableau d'accumulation) ───────────────────────
    path(
        "lots/<int:pk>/suivi/",
        views.lot_suivi_journalier,
        name="lot_suivi_journalier",
    ),
    path(
        "lots/<int:pk>/suivi/exporter.csv",
        views.lot_suivi_journalier_export,
        name="lot_suivi_journalier_export",
    ),
    # ── FormuleAliment (recettes d'aliment) ─────────────────────────────
    path(
        "formules-aliment/",
        views.formule_aliment_list,
        name="formule_aliment_list",
    ),
    path(
        "formules-aliment/creer/",
        views.formule_aliment_create,
        name="formule_aliment_create",
    ),
    path(
        "formules-aliment/<int:pk>/modifier/",
        views.formule_aliment_edit,
        name="formule_aliment_edit",
    ),
    # ── ProductionAliment (réapprovisionnement) ─────────────────────────
    path(
        "aliments/produire/",
        views.production_aliment_create,
        name="production_aliment_create",
    ),
    path(
        "aliments/",
        views.production_aliment_list,
        name="production_aliment_list",
    ),
    path(
        "aliments/<int:pk>/",
        views.production_aliment_detail,
        name="production_aliment_detail",
    ),
    path(
        "aliments/paiement/creer/",
        views.production_aliment_paiement_create,
        name="production_aliment_paiement_create",
    ),
    # ── RetraitOeufs ─────────────────────────────────────────────────────
    path(
        "recoltes-oeufs/retraits/creer/",
        views.retrait_oeufs_create,
        name="retrait_oeufs_create",
    ),
    path(
        "lots/<int:lot_pk>/recoltes-oeufs/retraits/creer/",
        views.retrait_oeufs_create,
        name="retrait_oeufs_create_lot",
    ),
    # ── AJAX ────────────────────────────────────────────────────────────
    path(
        "lots/<int:pk>/kpi.json",
        views.lot_kpi_json,
        name="lot_kpi_json",
    ),
    path(
        "poussins/bl-fournisseur.json",
        views.bl_fournisseur_poussins_json,
        name="bl_fournisseur_poussins_json",
    ),
    path(
        "oeufs/verifier-retrait.json",
        views.retrait_oeufs_verifier_json,
        name="retrait_oeufs_verifier_json",
    ),
    path(
        "oeufs/prix-marche/creer.json",
        views.prix_marche_quick_create_json,
        name="prix_marche_quick_create_json",
    ),
    path(
        "formules-aliment/prix-estime.json",
        views.formule_aliment_prix_estime_json,
        name="formule_aliment_prix_estime_json",
    ),
]
