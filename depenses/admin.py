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

from core.admin import BrancheScopedAdminMixin, PieceJointeInline
from depenses.models import (
    CategorieDepense,
    Depense,
    Associe,
    RetraitAssocie,
    Employe,
    Pointage,
    JourFerie,
    CongeEmploye,
    AcompteEmploye,
    DetteEmploye,
    RemboursementDette,
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
class DepenseAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = DepenseResource

    list_display = (
        "date",
        "branche",
        "categorie",
        "description_courte",
        "montant_dzd",
        "mode_paiement",
        "lot",
        "facture_liee",
        "a_pj",
    )
    list_filter = ("categorie", "mode_paiement", "branche", "date", "lot", "voyage")
    search_fields = ("description", "reference_document", "notes", "lot__designation")
    date_hierarchy = "date"
    readonly_fields = ("a_pj", "created_at", "updated_at")
    autocomplete_fields = ("branche", "categorie", "lot", "voyage", "facture_liee")
    inlines = (PieceJointeInline,)

    fieldsets = (
        (
            "Dépense",
            {
                "fields": (
                    "date",
                    "branche",
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
                "fields": ("reference_document", "a_pj"),
            },
        ),
        (
            "Imputations optionnelles",
            {
                "fields": ("lot", "voyage", "facture_liee"),
                "description": (
                    "Lot : pour le calcul de rentabilité par lot (BR-DEP-04). "
                    "Voyage : coût de transport d'une tournée de livraison "
                    "(clients.VoyageLivraison). "
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
    list_display = ("date", "associe", "montant_dzd", "mode_paiement", "motif", "a_pj")
    list_filter = ("associe", "mode_paiement", "date")
    search_fields = ("motif", "reference_document", "notes", "associe__nom")
    date_hierarchy = "date"
    autocomplete_fields = ("associe",)
    readonly_fields = ("created_at",)
    inlines = (PieceJointeInline,)

    @admin.display(description="المبلغ (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    @admin.display(description="PJ", boolean=True)
    def a_pj(self, obj):
        return obj.a_piece_jointe


# ===========================================================================
# RH — Employees & payroll
# ===========================================================================


@admin.register(Employe)
class EmployeAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = EmployeResource
    branche_lookup = "batiment__branche"

    list_display = (
        "matricule",
        "nom_complet",
        "fonction",
        "branche_display",
        "batiment",
        "jour_repos_habituel",
        "binome",
        "salaire_base_mensuel",
        "actif",
    )
    list_filter = ("actif", "batiment__branche", "batiment", "jour_repos_habituel")
    search_fields = ("matricule", "nom_complet", "fonction", "telephone")
    list_editable = ("actif",)
    autocomplete_fields = ("batiment", "binome")
    ordering = ("nom_complet",)

    @admin.display(description="الفرع")
    def branche_display(self, obj):
        return obj.branche

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
class PointageAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = PointageResource
    branche_lookup = "employe__batiment__branche"

    list_display = ("date", "employe", "statut", "heures_supplementaires")
    list_filter = ("statut", "date", "employe__batiment__branche", "employe")
    search_fields = ("employe__nom_complet", "employe__matricule", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("employe",)


@admin.register(CongeEmploye)
class CongeEmployeAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "employe__batiment__branche"

    list_display = ("employe", "date_debut", "date_fin", "nb_jours", "motif")
    list_filter = ("employe__batiment__branche", "employe")
    search_fields = ("employe__nom_complet", "motif", "notes")
    autocomplete_fields = ("employe",)
    readonly_fields = ("nb_jours", "created_at")


@admin.register(AcompteEmploye)
class AcompteEmployeAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = AcompteEmployeResource
    branche_lookup = "employe__batiment__branche"

    list_display = (
        "date",
        "employe",
        "montant_dzd",
        "mode_paiement",
        "deduit_badge",
        "a_pj",
    )
    list_filter = ("employe__batiment__branche", "employe", "mode_paiement", "date")
    search_fields = ("employe__nom_complet", "motif", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("employe", "bulletin_paie")
    readonly_fields = ("created_at",)
    inlines = (PieceJointeInline,)

    @admin.display(description="المبلغ (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    @admin.display(description="مخصوم", boolean=True)
    def deduit_badge(self, obj):
        return obj.deduit

    @admin.display(description="PJ", boolean=True)
    def a_pj(self, obj):
        return obj.a_piece_jointe


@admin.register(JourFerie)
class JourFerieAdmin(admin.ModelAdmin):
    list_display = ("nom", "date", "actif")
    list_filter = ("actif",)
    search_fields = ("nom",)
    date_hierarchy = "date"
    list_editable = ("actif",)
    ordering = ("-date",)


class RemboursementDetteInline(admin.TabularInline):
    model = RemboursementDette
    extra = 0
    fields = ("bulletin_paie", "montant", "notes", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("bulletin_paie",)


@admin.register(DetteEmploye)
class DetteEmployeAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    branche_lookup = "employe__batiment__branche"

    list_display = (
        "date",
        "employe",
        "montant",
        "montant_rembourse",
        "montant_restant",
        "soldee_badge",
        "a_pj",
    )
    list_filter = ("employe__batiment__branche", "employe", "date")
    search_fields = ("employe__nom_complet", "motif", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("employe",)
    readonly_fields = (
        "created_at",
        "montant_rembourse",
        "montant_restant",
    )
    inlines = (RemboursementDetteInline, PieceJointeInline)

    fieldsets = (
        (None, {"fields": ("employe", "date", "montant", "motif")}),
        (
            "Suivi (calculé)",
            {"fields": ("montant_rembourse", "montant_restant")},
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("enregistre_par", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="مسددّ", boolean=True)
    def soldee_badge(self, obj):
        return obj.soldee

    @admin.display(description="PJ", boolean=True)
    def a_pj(self, obj):
        return obj.a_piece_jointe


@admin.register(RemboursementDette)
class RemboursementDetteAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "dette__employe__batiment__branche"

    list_display = ("dette", "bulletin_paie", "montant", "created_at")
    list_filter = ("dette__employe__batiment__branche", "dette__employe")
    search_fields = ("dette__employe__nom_complet", "notes")
    autocomplete_fields = ("dette", "bulletin_paie")
    readonly_fields = ("created_at",)


@admin.register(BulletinPaie)
class BulletinPaieAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = BulletinPaieResource
    branche_lookup = "employe__batiment__branche"

    list_display = (
        "employe",
        "periode_label",
        "jours_presence",
        "jours_conge",
        "jours_feries",
        "montant_brut",
        "total_acomptes",
        "total_dettes",
        "montant_net",
        "statut",
        "a_pj",
    )
    list_filter = ("statut", "annee", "mois", "employe__batiment__branche", "employe")
    search_fields = ("employe__nom_complet", "employe__matricule")
    autocomplete_fields = ("employe",)
    inlines = (PieceJointeInline,)
    readonly_fields = (
        "jours_presence",
        "jours_absence",
        "jours_repos",
        "jours_conge",
        "jours_feries",
        "montant_jours_feries",
        "total_heures_supplementaires",
        "salaire_base_reference",
        "taux_journalier",
        "montant_heures_sup",
        "montant_brut",
        "total_acomptes",
        "total_dettes",
        "montant_net",
        "created_at",
        "updated_at",
    )

    @admin.display(description="PJ", boolean=True)
    def a_pj(self, obj):
        return obj.a_piece_jointe
