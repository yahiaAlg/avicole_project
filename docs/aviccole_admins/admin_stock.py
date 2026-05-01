"""
stock/admin.py

Admin registration for the stock module:
  StockIntrant, StockProduitFini  — read-only dashboards
  StockMouvement                  — read-only audit trail
  StockAjustement                 — writable (the approved way to correct balances)
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from stock.models import StockIntrant, StockProduitFini, StockMouvement, StockAjustement
from stock.resources import (
    StockIntrantResource,
    StockProduitFiniResource,
    StockMouvementResource,
    StockAjustementResource,
)


# ---------------------------------------------------------------------------
# StockIntrant — read-only balance view
# ---------------------------------------------------------------------------

@admin.register(StockIntrant)
class StockIntrantAdmin(ImportExportModelAdmin):
    resource_class = StockIntrantResource

    list_display = (
        "intrant", "categorie", "quantite_display",
        "prix_moyen_pondere", "valeur_stock_dzd",
        "seuil_alerte_display", "alerte_badge", "derniere_mise_a_jour",
    )
    list_filter = ("intrant__categorie", "intrant__actif")
    search_fields = ("intrant__designation",)
    readonly_fields = (
        "intrant", "quantite", "prix_moyen_pondere",
        "valeur_stock_dzd", "alerte_badge", "derniere_mise_a_jour",
    )

    @admin.display(description="Catégorie")
    def categorie(self, obj):
        return obj.intrant.categorie.libelle

    @admin.display(description="Quantité")
    def quantite_display(self, obj):
        val = obj.quantite
        colour = "#b71c1c" if obj.en_alerte else "inherit"
        return format_html('<span style="color:{}">{}</span>', colour, val)

    @admin.display(description="Seuil")
    def seuil_alerte_display(self, obj):
        return obj.intrant.seuil_alerte

    @admin.display(description="Alerte", boolean=True)
    def alerte_badge(self, obj):
        return obj.en_alerte

    @admin.display(description="Valeur stock (DZD)")
    def valeur_stock_dzd(self, obj):
        return f"{obj.valeur_stock:,.2f} DZD"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# StockProduitFini — read-only balance view
# ---------------------------------------------------------------------------

@admin.register(StockProduitFini)
class StockProduitFiniAdmin(ImportExportModelAdmin):
    resource_class = StockProduitFiniResource

    list_display = (
        "produit_fini", "type_produit", "quantite",
        "cout_moyen_production", "valeur_stock_dzd",
        "seuil_alerte", "alerte_badge", "derniere_mise_a_jour",
    )
    list_filter = ("produit_fini__type_produit",)
    search_fields = ("produit_fini__designation",)
    readonly_fields = (
        "produit_fini", "quantite", "cout_moyen_production",
        "valeur_stock_dzd", "seuil_alerte", "derniere_mise_a_jour",
    )

    @admin.display(description="Type")
    def type_produit(self, obj):
        return obj.produit_fini.get_type_produit_display()

    @admin.display(description="Alerte", boolean=True)
    def alerte_badge(self, obj):
        return obj.en_alerte

    @admin.display(description="Valeur stock (DZD)")
    def valeur_stock_dzd(self, obj):
        return f"{obj.valeur_stock:,.2f} DZD"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# StockMouvement — immutable audit trail
# ---------------------------------------------------------------------------

@admin.register(StockMouvement)
class StockMouvementAdmin(ImportExportModelAdmin):
    resource_class = StockMouvementResource

    list_display = (
        "date_mouvement", "item", "type_mouvement_badge",
        "source", "quantite", "quantite_avant", "quantite_apres",
        "reference_label", "created_at",
    )
    list_filter = ("type_mouvement", "source", "date_mouvement")
    search_fields = (
        "intrant__designation", "produit_fini__designation",
        "reference_label", "notes",
    )
    date_hierarchy = "date_mouvement"
    readonly_fields = (
        "intrant", "produit_fini", "type_mouvement", "source",
        "quantite", "quantite_avant", "quantite_apres",
        "date_mouvement", "reference_id", "reference_label",
        "notes", "created_by", "created_at",
    )

    @admin.display(description="Article")
    def item(self, obj):
        return str(obj.intrant or obj.produit_fini or "—")

    @admin.display(description="Type")
    def type_mouvement_badge(self, obj):
        colours = {
            StockMouvement.TYPE_ENTREE: "#2e7d32",
            StockMouvement.TYPE_SORTIE: "#b71c1c",
            StockMouvement.TYPE_AJUSTEMENT: "#1565c0",
        }
        colour = colours.get(obj.type_mouvement, "#333")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour, obj.get_type_mouvement_display(),
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# StockAjustement — writable; triggers balance correction via signal
# ---------------------------------------------------------------------------

@admin.register(StockAjustement)
class StockAjustementAdmin(ImportExportModelAdmin):
    resource_class = StockAjustementResource

    list_display = (
        "date_ajustement", "segment", "item",
        "quantite_avant", "quantite_apres", "delta_display",
        "effectue_par", "raison_courte",
    )
    list_filter = ("segment", "date_ajustement")
    search_fields = (
        "intrant__designation", "produit_fini__designation", "raison",
    )
    date_hierarchy = "date_ajustement"
    readonly_fields = ("created_at",)
    autocomplete_fields = ("intrant", "produit_fini")

    fieldsets = (
        ("Ajustement", {
            "fields": (
                "segment", "intrant", "produit_fini",
                "date_ajustement",
                "quantite_avant", "quantite_apres",
            ),
            "description": (
                "Renseignez exactement un des deux champs intrant / produit_fini "
                "selon le segment sélectionné."
            ),
        }),
        ("Justification (obligatoire)", {
            "fields": ("raison", "effectue_par"),
        }),
        ("Horodatage", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    @admin.display(description="Article")
    def item(self, obj):
        return str(obj.intrant or obj.produit_fini or "—")

    @admin.display(description="Δ")
    def delta_display(self, obj):
        delta = obj.quantite_apres - obj.quantite_avant
        sign = "+" if delta >= 0 else ""
        colour = "#2e7d32" if delta >= 0 else "#b71c1c"
        return format_html(
            '<span style="color:{};font-weight:bold">{}{}</span>',
            colour, sign, delta,
        )

    @admin.display(description="Raison")
    def raison_courte(self, obj):
        return obj.raison[:80] if obj.raison else "—"

    def get_readonly_fields(self, request, obj=None):
        # Immutable after creation
        if obj:
            return (
                "segment", "intrant", "produit_fini",
                "date_ajustement", "quantite_avant", "quantite_apres",
                "raison", "effectue_par", "created_at",
            )
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False
