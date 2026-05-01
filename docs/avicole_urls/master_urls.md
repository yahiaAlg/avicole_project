# achats_urls.py

```python
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

```

---

# clients_urls.py

```python
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
    # POST-only: BROUILLON → LIVRE transition
    path(
        "bls/<int:pk>/valider/",
        views.bl_client_valider,
        name="bl_client_valider",
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

    # ── AJAX ─────────────────────────────────────────────────────────────
    path(
        "clients/<int:pk>/solde.json",
        views.client_solde_json,
        name="client_solde_json",
    ),
]

```

---

# core_urls.py

```python
"""
core/urls.py

URL patterns for:
  - Authentication   (login, logout)
  - Dashboard        (/)
  - Company Info     (/parametres/entreprise/)
  - User Management  (/parametres/utilisateurs/)
  - My Profile       (/profil/)
"""

from django.urls import path
from core import views

app_name = "core"

urlpatterns = [
    # ── Authentication ──────────────────────────────────────────────────
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # ── Dashboard ───────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),

    # ── My Profile ──────────────────────────────────────────────────────
    path("profil/", views.profile_view, name="profile"),
    path(
        "profil/mot-de-passe/",
        views.own_password_change,
        name="own_password_change",
    ),

    # ── Company Information ─────────────────────────────────────────────
    path(
        "parametres/entreprise/",
        views.company_info_view,
        name="company_info",
    ),

    # ── User Management (admin only) ────────────────────────────────────
    path(
        "parametres/utilisateurs/",
        views.user_list,
        name="user_list",
    ),
    path(
        "parametres/utilisateurs/creer/",
        views.user_create,
        name="user_create",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/modifier/",
        views.user_edit,
        name="user_edit",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/activer/",
        views.user_toggle_active,
        name="user_toggle_active",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/mot-de-passe/",
        views.user_password_change,
        name="user_password_change",
    ),
]

```

---

# depenses_urls.py

```python
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

```

---

# elevage_urls.py

```python
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

```

---

# intrants_urls.py

```python
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

```

---

# production_urls.py

```python
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
]

```

---

# reporting_urls.py

```python
"""
reporting/urls.py

URL patterns for all reports and the reporting dashboard.

Report access levels (enforced in views, not at URL level):
  Financial roles (admin, manager, comptable):
    supplier aging, settlement history, distribution, debt dashboard,
    lot profitability, cash flow, depenses, production dashboard,
    client receivables aging.
  All roles (including operateur):
    stock status, consumption by lot, BL client history,
    stock movement print.
"""

from django.urls import path
from reporting import views

app_name = "reporting"

urlpatterns = [

    # ── Reporting Dashboard ──────────────────────────────────────────────
    path(
        "",
        views.reporting_dashboard,
        name="dashboard",
    ),

    # ── 20.1 Balance Fournisseur par Ancienneté ──────────────────────────
    path(
        "fournisseurs/anciennete/",
        views.rapport_supplier_aging,
        name="supplier_aging",
    ),

    # ── 20.2 Historique des Règlements ───────────────────────────────────
    path(
        "fournisseurs/reglements/",
        views.rapport_historique_reglements,
        name="historique_reglements",
    ),

    # ── 20.3 Répartition des Règlements ──────────────────────────────────
    path(
        "fournisseurs/repartition-reglements/",
        views.rapport_repartition_reglements,
        name="repartition_reglements",
    ),

    # ── 20.4 Dettes en Cours par Fournisseur ─────────────────────────────
    path(
        "fournisseurs/dettes/",
        views.rapport_dettes_fournisseurs,
        name="dettes_fournisseurs",
    ),

    # ── 20.5 Rentabilité par Lot ─────────────────────────────────────────
    path(
        "lots/rentabilite/",
        views.rapport_rentabilite_lot,
        name="rentabilite_lot",
    ),

    # ── 20.6 Résumé de Trésorerie ────────────────────────────────────────
    path(
        "tresorerie/",
        views.rapport_cash_flow,
        name="cash_flow",
    ),

    # ── 20.7 État des Stocks ─────────────────────────────────────────────
    path(
        "stocks/etat/",
        views.rapport_etat_stocks,
        name="etat_stocks",
    ),

    # ── Consommation par Lot (cross-lot) ─────────────────────────────────
    path(
        "lots/consommation/",
        views.rapport_consommation_lot,
        name="consommation_lot",
    ),

    # ── Consommation Detail (single lot drill-down) ───────────────────────
    path(
        "lots/<int:lot_pk>/consommation/",
        views.rapport_consommation_lot_detail,
        name="consommation_lot_detail",
    ),

    # ── Créances Clients (receivables aging) ─────────────────────────────
    path(
        "clients/creances/",
        views.rapport_creances_clients,
        name="creances_clients",
    ),

    # ── Historique BL Clients ─────────────────────────────────────────────
    path(
        "clients/bls/",
        views.rapport_historique_bl_clients,
        name="historique_bl_clients",
    ),

    # ── Tableau de Bord Production ────────────────────────────────────────
    path(
        "production/",
        views.rapport_production_dashboard,
        name="production_dashboard",
    ),

    # ── Rapport Dépenses ─────────────────────────────────────────────────
    path(
        "depenses/",
        views.rapport_depenses,
        name="depenses",
    ),

    # ── Print: Bon de Mouvement de Stock (spec §9.11) ─────────────────────
    path(
        "stocks/mouvements/<int:pk>/imprimer/",
        views.stock_mouvement_print,
        name="stock_mouvement_print",
    ),

    # ── AJAX: KPI summary widget ─────────────────────────────────────────
    path(
        "kpi.json",
        views.kpi_summary_json,
        name="kpi_summary_json",
    ),
]

```

---

# stock_urls.py

```python
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

```

---
