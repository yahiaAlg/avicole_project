"""
elevage/admin.py

Admin registration for the poultry raising module:
  LotElevage, Mortalite, Consommation
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from import_export.admin import ImportExportModelAdmin

from elevage.models import LotElevage, Mortalite, Consommation
from elevage.resources import (
    LotElevageResource,
    MortaliteResource,
    ConsommationResource,
)

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class MortaliteInline(admin.TabularInline):
    model = Mortalite
    extra = 1
    fields = ("date", "nombre", "cause", "notes")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.statut == LotElevage.STATUT_FERME:
            return ("date", "nombre", "cause", "notes")
        return ()


class ConsommationInline(admin.TabularInline):
    model = Consommation
    extra = 1
    fields = ("date", "intrant", "quantite", "notes")
    autocomplete_fields = ("intrant",)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.statut == LotElevage.STATUT_FERME:
            return ("date", "intrant", "quantite", "notes")
        return ()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@admin.action(description="Fermer les lots sélectionnés")
def fermer_lots(modeladmin, request, queryset):
    for lot in queryset.filter(statut=LotElevage.STATUT_OUVERT):
        lot.fermer()
    modeladmin.message_user(request, f"{queryset.count()} lot(s) fermé(s).")


# ---------------------------------------------------------------------------
# LotElevage
# ---------------------------------------------------------------------------


@admin.register(LotElevage)
class LotElevageAdmin(ImportExportModelAdmin):
    resource_class = LotElevageResource
    actions = (fermer_lots,)

    list_display = (
        "designation",
        "batiment",
        "statut_badge",
        "date_ouverture",
        "date_fermeture",
        "nombre_poussins_initial",
        "effectif_vivant_display",
        "taux_mortalite_display",
        "duree_jours",
    )
    list_filter = ("statut", "batiment", "fournisseur_poussins", "date_ouverture")
    search_fields = ("designation", "souche", "notes")
    date_hierarchy = "date_ouverture"
    readonly_fields = (
        "created_at",
        "updated_at",
        "total_mortalite",
        "effectif_vivant_display",
        "taux_mortalite_display",
        "duree_jours",
        "consommation_totale_aliment",
        "cout_total_intrants",
    )
    inlines = (MortaliteInline, ConsommationInline)
    autocomplete_fields = ("fournisseur_poussins", "batiment")

    fieldsets = (
        (
            "Lot",
            {
                "fields": (
                    "designation",
                    "statut",
                    "date_ouverture",
                    "date_fermeture",
                ),
            },
        ),
        (
            "Poussins",
            {
                "fields": (
                    "nombre_poussins_initial",
                    "fournisseur_poussins",
                    "bl_fournisseur_poussins",
                    "souche",
                    "batiment",
                ),
            },
        ),
        (
            "Indicateurs (calculés)",
            {
                "fields": (
                    "total_mortalite",
                    "effectif_vivant_display",
                    "taux_mortalite_display",
                    "duree_jours",
                    "consommation_totale_aliment",
                    "cout_total_intrants",
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
        colour = "#2e7d32" if obj.statut == LotElevage.STATUT_OUVERT else "#888"
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    @admin.display(description="Effectif vivant")
    def effectif_vivant_display(self, obj):
        return obj.effectif_vivant

    @admin.display(description="Taux mortalité (%)")
    def taux_mortalite_display(self, obj):
        val = obj.taux_mortalite
        colour = "#b71c1c" if val > 5 else "#2e7d32"
        return format_html('<span style="color:{}">{} %</span>', colour, val)

    @admin.display(description="Durée (j)")
    def duree_jours(self, obj):
        return obj.duree_elevage

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.statut == LotElevage.STATUT_FERME:
            base += [
                "designation",
                "batiment",
                "statut",
                "date_ouverture",
                "date_fermeture",
                "nombre_poussins_initial",
                "fournisseur_poussins",
                "bl_fournisseur_poussins",
                "souche",
            ]
        return base


# ---------------------------------------------------------------------------
# Mortalite
# ---------------------------------------------------------------------------


@admin.register(Mortalite)
class MortaliteAdmin(ImportExportModelAdmin):
    resource_class = MortaliteResource

    list_display = ("lot", "date", "nombre", "cause", "created_at")
    list_filter = ("lot__statut", "lot", "date")
    search_fields = ("lot__designation", "cause")
    date_hierarchy = "date"
    autocomplete_fields = ("lot",)
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {"fields": ("lot", "date", "nombre", "cause")}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        ("Horodatage", {"fields": ("created_at",), "classes": ("collapse",)}),
    )


# ---------------------------------------------------------------------------
# Consommation
# ---------------------------------------------------------------------------


@admin.register(Consommation)
class ConsommationAdmin(ImportExportModelAdmin):
    resource_class = ConsommationResource

    list_display = ("lot", "date", "intrant", "quantite", "created_at")
    list_filter = ("lot__statut", "intrant__categorie", "date")
    search_fields = ("lot__designation", "intrant__designation")
    date_hierarchy = "date"
    autocomplete_fields = ("lot", "intrant")
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {"fields": ("lot", "date", "intrant", "quantite")}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )
