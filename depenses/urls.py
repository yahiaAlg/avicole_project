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
    # ── Associés ─────────────────────────────────────────────────────────
    path("associes/", views.associe_list, name="associe_list"),
    path("associes/creer/", views.associe_create, name="associe_create"),
    path("associes/<int:pk>/modifier/", views.associe_edit, name="associe_edit"),
    path("associes/<int:pk>/", views.associe_detail, name="associe_detail"),
    path("retraits/", views.retrait_list, name="retrait_list"),
    path("retraits/creer/", views.retrait_create, name="retrait_create"),
    path("retraits/<int:pk>/modifier/", views.retrait_edit, name="retrait_edit"),
    path("retraits/<int:pk>/supprimer/", views.retrait_delete, name="retrait_delete"),
    # ── RH — Employés ────────────────────────────────────────────────────
    path("rh/", views.rh_dashboard, name="rh_dashboard"),
    path("rh/employes/", views.employe_list, name="employe_list"),
    path("rh/employes/creer/", views.employe_create, name="employe_create"),
    path("rh/employes/<int:pk>/", views.employe_detail, name="employe_detail"),
    path("rh/employes/<int:pk>/modifier/", views.employe_edit, name="employe_edit"),
    # ── RH — Pointage ────────────────────────────────────────────────────
    path("rh/pointages/", views.pointage_list, name="pointage_list"),
    path("rh/pointages/creer/", views.pointage_create, name="pointage_create"),
    path(
        "rh/pointages/<int:pk>/modifier/",
        views.pointage_edit,
        name="pointage_edit",
    ),
    path(
        "rh/pointages/generer-mois/",
        views.pointage_generer_mois,
        name="pointage_generer_mois",
    ),
    # ── RH — Congés ──────────────────────────────────────────────────────
    path("rh/conges/", views.conge_list, name="conge_list"),
    path("rh/conges/creer/", views.conge_create, name="conge_create"),
    # ── RH — Acomptes ────────────────────────────────────────────────────
    path("rh/acomptes/", views.acompte_employe_list, name="acompte_employe_list"),
    path(
        "rh/acomptes/creer/",
        views.acompte_employe_create,
        name="acompte_employe_create",
    ),
    # ── RH — Bulletins de paie ───────────────────────────────────────────
    path("rh/bulletins/", views.bulletin_paie_list, name="bulletin_paie_list"),
    path(
        "rh/bulletins/generer/",
        views.bulletin_paie_generer,
        name="bulletin_paie_generer",
    ),
    path(
        "rh/bulletins/<int:pk>/",
        views.bulletin_paie_detail,
        name="bulletin_paie_detail",
    ),
    path(
        "rh/bulletins/<int:pk>/valider/",
        views.bulletin_paie_valider,
        name="bulletin_paie_valider",
    ),
    path(
        "rh/bulletins/<int:pk>/payer/",
        views.bulletin_paie_payer,
        name="bulletin_paie_payer",
    ),
    path(
        "rh/bulletins/<int:pk>/imprimer/",
        views.bulletin_paie_print,
        name="bulletin_paie_print",
    ),
]
