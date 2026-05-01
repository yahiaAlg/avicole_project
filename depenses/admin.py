"""
depenses/admin.py

Admin registration for operational expense tracking:
  CategorieDepense, Depense
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from depenses.models import CategorieDepense, Depense
from depenses.resources import CategorieDepenseResource, DepenseResource


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
