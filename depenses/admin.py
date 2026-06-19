"""
depenses/admin.py

Admin registration for operational expense tracking:
  CategorieDepense, Depense
and the two special expense families:
  Associe, RetraitAssocie
  Employe, Pointage, CongeEmploye, AcompteEmploye, BulletinPaie
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from depenses.models import (
    CategorieDepense,
    Depense,
    Associe,
    RetraitAssocie,
    Employe,
    Pointage,
    CongeEmploye,
    AcompteEmploye,
    BulletinPaie,
)
from depenses.resources import (
    CategorieDepenseResource,
    DepenseResource,
    AssocieResource,
    RetraitAssocieResource,
    EmployeResource,
    PointageResource,
    AcompteEmployeResource,
    BulletinPaieResource,
)


@admin.register(CategorieDepense)
class CategorieDepenseAdmin(ImportExportModelAdmin):
    resource_class = CategorieDepenseResource

    list_display = ("libelle", "code", "ordre", "actif", "created_at")
    list_filter = ("actif",)
    search_fields = ("code", "libelle")
    list_editable = ("ordre", "actif")
    ordering = ("ordre", "libelle")

    fieldsets = (
        (None, {"fields": ("code", "libelle", "description", "ordre", "actif")}),
    )


@admin.register(Depense)
class DepenseAdmin(ImportExportModelAdmin):
    resource_class = DepenseResource

    list_display = (
        "date",
        "categorie",
        "description_courte",
        "montant_dzd",
        "mode_paiement",
        "lot",
        "facture_liee",
        "a_pj",
    )
    list_filter = ("categorie", "mode_paiement", "date", "lot")
    search_fields = ("description", "reference_document", "notes", "lot__designation")
    date_hierarchy = "date"
    readonly_fields = ("a_pj", "created_at", "updated_at")
    autocomplete_fields = ("categorie", "lot", "facture_liee")

    fieldsets = (
        (
            "Dépense",
            {
                "fields": (
                    "date",
                    "categorie",
                    "description",
                    "montant",
                    "mode_paiement",
                ),
            },
        ),
        (
            "Justificatif",
            {
                "fields": ("reference_document", "piece_jointe", "a_pj"),
            },
        ),
        (
            "Imputations optionnelles",
            {
                "fields": ("lot", "facture_liee"),
                "description": (
                    "Lot : pour le calcul de rentabilité par lot (BR-DEP-04). "
                    "Facture liée : service uniquement (BR-DEP-03)."
                ),
                "classes": ("collapse",),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("enregistre_par", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Description")
    def description_courte(self, obj):
        return obj.description[:60] if obj.description else "—"

    @admin.display(description="Montant (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    @admin.display(description="PJ", boolean=True)
    def a_pj(self, obj):
        return obj.a_piece_jointe


# ===========================================================================
# Associés
# ===========================================================================


@admin.register(Associe)
class AssocieAdmin(ImportExportModelAdmin):
    resource_class = AssocieResource
    list_display = ("nom", "telephone", "pourcentage_parts", "actif", "created_at")
    list_filter = ("actif",)
    search_fields = ("nom", "telephone")
    list_editable = ("actif",)
    ordering = ("nom",)


@admin.register(RetraitAssocie)
class RetraitAssocieAdmin(ImportExportModelAdmin):
    resource_class = RetraitAssocieResource
    list_display = ("date", "associe", "montant_dzd", "mode_paiement", "motif")
    list_filter = ("associe", "mode_paiement", "date")
    search_fields = ("motif", "reference_document", "notes", "associe__nom")
    date_hierarchy = "date"
    autocomplete_fields = ("associe",)
    readonly_fields = ("created_at",)

    @admin.display(description="المبلغ (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"


# ===========================================================================
# RH — Employees & payroll
# ===========================================================================


@admin.register(Employe)
class EmployeAdmin(ImportExportModelAdmin):
    resource_class = EmployeResource
    list_display = (
        "matricule",
        "nom_complet",
        "fonction",
        "batiment",
        "jour_repos_habituel",
        "binome",
        "salaire_base_mensuel",
        "actif",
    )
    list_filter = ("actif", "batiment", "jour_repos_habituel")
    search_fields = ("matricule", "nom_complet", "fonction", "telephone")
    list_editable = ("actif",)
    autocomplete_fields = ("batiment", "binome")
    ordering = ("nom_complet",)

    fieldsets = (
        (
            "الهوية",
            {
                "fields": (
                    "matricule",
                    "nom_complet",
                    "fonction",
                    "telephone",
                    "date_embauche",
                )
            },
        ),
        ("التنظيم", {"fields": ("batiment", "jour_repos_habituel", "binome")}),
        (
            "الراتب",
            {
                "fields": (
                    "salaire_base_mensuel",
                    "heures_normales_jour",
                    "taux_majoration_heure_sup",
                )
            },
        ),
        ("أخرى", {"fields": ("actif", "notes")}),
    )


@admin.register(Pointage)
class PointageAdmin(ImportExportModelAdmin):
    resource_class = PointageResource
    list_display = ("date", "employe", "statut", "heures_supplementaires")
    list_filter = ("statut", "date", "employe")
    search_fields = ("employe__nom_complet", "employe__matricule", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("employe",)


@admin.register(CongeEmploye)
class CongeEmployeAdmin(admin.ModelAdmin):
    list_display = ("employe", "date_debut", "date_fin", "nb_jours", "motif")
    list_filter = ("employe",)
    search_fields = ("employe__nom_complet", "motif", "notes")
    autocomplete_fields = ("employe",)
    readonly_fields = ("nb_jours", "created_at")


@admin.register(AcompteEmploye)
class AcompteEmployeAdmin(ImportExportModelAdmin):
    resource_class = AcompteEmployeResource
    list_display = ("date", "employe", "montant_dzd", "mode_paiement", "deduit_badge")
    list_filter = ("employe", "mode_paiement", "date")
    search_fields = ("employe__nom_complet", "motif", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("employe", "bulletin_paie")
    readonly_fields = ("created_at",)

    @admin.display(description="المبلغ (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    @admin.display(description="مخصوم", boolean=True)
    def deduit_badge(self, obj):
        return obj.deduit


@admin.register(BulletinPaie)
class BulletinPaieAdmin(ImportExportModelAdmin):
    resource_class = BulletinPaieResource
    list_display = (
        "employe",
        "periode_label",
        "jours_presence",
        "jours_conge",
        "montant_brut",
        "total_acomptes",
        "montant_net",
        "statut",
    )
    list_filter = ("statut", "annee", "mois", "employe")
    search_fields = ("employe__nom_complet", "employe__matricule")
    autocomplete_fields = ("employe",)
    readonly_fields = (
        "jours_presence",
        "jours_absence",
        "jours_repos",
        "jours_conge",
        "total_heures_supplementaires",
        "salaire_base_reference",
        "taux_journalier",
        "montant_heures_sup",
        "montant_brut",
        "total_acomptes",
        "montant_net",
        "created_at",
        "updated_at",
    )
