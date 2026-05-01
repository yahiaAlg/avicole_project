"""
production/admin.py

Admin registration for the production module:
  ProduitFini, ProductionRecord, ProductionLigne
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from production.models import ProduitFini, ProductionRecord, ProductionLigne
from production.resources import (
    ProduitFiniResource,
    ProductionRecordResource,
    ProductionLigneResource,
)

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class ProductionLigneInline(admin.TabularInline):
    model = ProductionLigne
    extra = 1
    fields = (
        "produit_fini",
        "quantite",
        "poids_unitaire_kg",
        "cout_unitaire_estime",
        "valeur_totale_display",
        "notes",
    )
    readonly_fields = ("valeur_totale_display",)
    autocomplete_fields = ("produit_fini",)

    @admin.display(description="Valeur (DZD)")
    def valeur_totale_display(self, obj):
        if obj.pk:
            return f"{obj.valeur_totale:,.2f}"
        return "—"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.statut == ProductionRecord.STATUT_VALIDE:
            return (
                "produit_fini",
                "quantite",
                "poids_unitaire_kg",
                "cout_unitaire_estime",
                "valeur_totale_display",
                "notes",
            )
        return self.readonly_fields


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@admin.action(description="Valider les enregistrements sélectionnés")
def valider_productions(modeladmin, request, queryset):
    count = 0
    for record in queryset.filter(statut=ProductionRecord.STATUT_BROUILLON):
        record.statut = ProductionRecord.STATUT_VALIDE
        record.save()
        count += 1
    modeladmin.message_user(request, f"{count} enregistrement(s) validé(s).")


# ---------------------------------------------------------------------------
# ProduitFini
# ---------------------------------------------------------------------------


@admin.register(ProduitFini)
class ProduitFiniAdmin(ImportExportModelAdmin):
    resource_class = ProduitFiniResource

    list_display = (
        "designation",
        "type_produit",
        "unite_mesure",
        "prix_vente_defaut",
        "quantite_en_stock",
        "actif",
    )
    list_filter = ("type_produit", "unite_mesure", "actif")
    search_fields = ("designation", "notes")
    readonly_fields = ("quantite_en_stock", "created_at", "updated_at")
    list_editable = ("actif",)

    fieldsets = (
        (
            "Catalogue",
            {
                "fields": (
                    "designation",
                    "type_produit",
                    "unite_mesure",
                    "prix_vente_defaut",
                    "actif",
                ),
            },
        ),
        (
            "Stock (calculé)",
            {
                "fields": ("quantite_en_stock",),
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


# ---------------------------------------------------------------------------
# ProductionRecord
# ---------------------------------------------------------------------------


@admin.register(ProductionRecord)
class ProductionRecordAdmin(ImportExportModelAdmin):
    resource_class = ProductionRecordResource
    actions = (valider_productions,)

    list_display = (
        "lot",
        "date_production",
        "nombre_oiseaux_abattus",
        "poids_total_kg",
        "poids_moyen_kg",
        "statut_badge",
    )
    list_filter = ("statut", "lot", "date_production")
    search_fields = ("lot__designation", "notes")
    date_hierarchy = "date_production"
    readonly_fields = ("poids_moyen_kg", "created_at", "updated_at")
    inlines = (ProductionLigneInline,)
    autocomplete_fields = ("lot",)

    fieldsets = (
        (
            "Production",
            {
                "fields": (
                    "lot",
                    "date_production",
                    "nombre_oiseaux_abattus",
                    "poids_total_kg",
                    "poids_moyen_kg",
                    "statut",
                ),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Statut")
    def statut_badge(self, obj):
        colour = "#2e7d32" if obj.statut == ProductionRecord.STATUT_VALIDE else "#888"
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.statut == ProductionRecord.STATUT_VALIDE:
            base += [
                "lot",
                "date_production",
                "nombre_oiseaux_abattus",
                "poids_total_kg",
                "statut",
            ]
        return base

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting validated records (stock was already updated)
        if obj and obj.statut == ProductionRecord.STATUT_VALIDE:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(ProductionLigne)
class ProductionLigneAdmin(ImportExportModelAdmin):
    resource_class = ProductionLigneResource

    list_display = (
        "production",
        "produit_fini",
        "quantite",
        "poids_unitaire_kg",
        "cout_unitaire_estime",
        "valeur_totale_display",
    )
    list_filter = ("produit_fini__type_produit", "production__statut")
    search_fields = ("production__lot__designation", "produit_fini__designation")
    readonly_fields = ("valeur_totale_display",)
    autocomplete_fields = ("production", "produit_fini")

    @admin.display(description="Valeur (DZD)")
    def valeur_totale_display(self, obj):
        return f"{obj.valeur_totale:,.2f} DZD"
