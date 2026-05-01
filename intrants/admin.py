"""
intrants/admin.py

Admin registration for master-data tables:
  CategorieIntrant, TypeFournisseur, Fournisseur, Batiment, Intrant
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from intrants.models import (
    CategorieIntrant,
    TypeFournisseur,
    Fournisseur,
    Batiment,
    Intrant,
)
from intrants.resources import (
    CategorieIntrantResource,
    TypeFournisseurResource,
    FournisseurResource,
    BatimentResource,
    IntrantResource,
)


@admin.register(CategorieIntrant)
class CategorieIntrantAdmin(ImportExportModelAdmin):
    resource_class = CategorieIntrantResource

    list_display = ("libelle", "code", "consommable_en_lot", "ordre", "actif")
    list_filter = ("consommable_en_lot", "actif")
    search_fields = ("code", "libelle")
    list_editable = ("ordre", "actif")
    ordering = ("ordre", "libelle")

    fieldsets = (
        (None, {"fields": ("code", "libelle", "consommable_en_lot", "ordre", "actif")}),
    )

    def get_readonly_fields(self, request, obj=None):
        # Seed codes must not be renamed
        if obj and obj.code in ("ALIMENT", "POUSSIN", "MEDICAMENT", "AUTRE"):
            return ("code",)
        return ()


@admin.register(TypeFournisseur)
class TypeFournisseurAdmin(ImportExportModelAdmin):
    resource_class = TypeFournisseurResource

    list_display = ("libelle", "code", "ordre", "actif")
    list_filter = ("actif",)
    search_fields = ("code", "libelle")
    list_editable = ("ordre", "actif")
    ordering = ("ordre", "libelle")


@admin.register(Batiment)
class BatimentAdmin(ImportExportModelAdmin):
    resource_class = BatimentResource

    list_display = ("nom", "capacite", "actif", "description_courte")
    list_filter = ("actif",)
    search_fields = ("nom",)
    list_editable = ("actif",)

    fieldsets = (
        (None, {"fields": ("nom", "capacite", "actif")}),
        ("Description", {"fields": ("description",), "classes": ("collapse",)}),
    )

    @admin.display(description="Description")
    def description_courte(self, obj):
        return obj.description[:80] if obj.description else "—"


@admin.register(Fournisseur)
class FournisseurAdmin(ImportExportModelAdmin):
    resource_class = FournisseurResource

    list_display = (
        "nom",
        "type_principal",
        "wilaya",
        "telephone",
        "actif",
        "dette_globale_dzd",
        "acompte_disponible_dzd",
    )
    list_filter = ("actif", "type_principal", "wilaya")
    search_fields = ("nom", "nif", "rc", "telephone", "email")
    readonly_fields = (
        "created_at",
        "updated_at",
        "dette_globale_dzd",
        "acompte_disponible_dzd",
    )

    fieldsets = (
        (
            "Identification",
            {
                "fields": ("nom", "type_principal", "actif"),
            },
        ),
        (
            "Coordonnées",
            {
                "fields": (
                    "adresse",
                    "wilaya",
                    "telephone",
                    "telephone_2",
                    "email",
                    "contact_nom",
                ),
            },
        ),
        (
            "Identifiants légaux",
            {
                "fields": ("nif", "rc"),
                "classes": ("collapse",),
            },
        ),
        (
            "Finances (calculé)",
            {
                "fields": ("dette_globale_dzd", "acompte_disponible_dzd"),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Dette globale (DZD)")
    def dette_globale_dzd(self, obj):
        val = obj.dette_globale
        if val > 0:
            return format_html(
                '<span style="color:red;font-weight:bold">{} DZD</span>', f"{val:,.2f}"
            )
        return "0,00 DZD"

    @admin.display(description="Acompte disponible (DZD)")
    def acompte_disponible_dzd(self, obj):
        val = obj.acompte_disponible
        if val > 0:
            return format_html('<span style="color:green">{} DZD</span>', f"{val:,.2f}")
        return "0,00 DZD"


@admin.register(Intrant)
class IntrantAdmin(ImportExportModelAdmin):
    resource_class = IntrantResource

    list_display = (
        "designation",
        "categorie",
        "unite_mesure",
        "quantite_en_stock",
        "seuil_alerte",
        "statut_alerte",
        "actif",
    )
    list_filter = ("categorie", "unite_mesure", "actif")
    search_fields = ("designation", "notes")
    filter_horizontal = ("fournisseurs",)
    readonly_fields = ("quantite_en_stock", "statut_alerte", "created_at", "updated_at")

    fieldsets = (
        (
            "Catalogue",
            {
                "fields": ("designation", "categorie", "unite_mesure", "actif"),
            },
        ),
        (
            "Stock",
            {
                "fields": ("seuil_alerte", "quantite_en_stock", "statut_alerte"),
            },
        ),
        (
            "Fournisseurs associés",
            {
                "fields": ("fournisseurs",),
                "classes": ("collapse",),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Alerte stock", boolean=True)
    def statut_alerte(self, obj):
        return obj.en_alerte
