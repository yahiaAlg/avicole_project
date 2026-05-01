"""
achats/admin.py

Admin registration for the supplier procurement cycle:
  BLFournisseur, BLFournisseurLigne, FactureFournisseur,
  ReglementFournisseur, AllocationReglement, AcompteFournisseur
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from import_export.admin import ImportExportModelAdmin

from achats.models import (
    BLFournisseur,
    BLFournisseurLigne,
    FactureFournisseur,
    ReglementFournisseur,
    AllocationReglement,
    AcompteFournisseur,
)
from achats.resources import (
    BLFournisseurResource,
    BLFournisseurLigneResource,
    FactureFournisseurResource,
    ReglementFournisseurResource,
    AllocationReglementResource,
    AcompteFournisseurResource,
)

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class BLFournisseurLigneInline(admin.TabularInline):
    model = BLFournisseurLigne
    extra = 1
    fields = ("intrant", "quantite", "prix_unitaire", "montant_total_display", "notes")
    readonly_fields = ("montant_total_display",)
    autocomplete_fields = ("intrant",)

    @admin.display(description="Total (DZD)")
    def montant_total_display(self, obj):
        if obj.pk:
            return f"{obj.montant_total:,.2f}"
        return "—"

    def get_readonly_fields(self, request, obj=None):
        # Lock lines when BL is already invoiced
        if obj and obj.est_verrouille:
            return (
                "intrant",
                "quantite",
                "prix_unitaire",
                "montant_total_display",
                "notes",
            )
        return self.readonly_fields


class AllocationReglementInline(admin.TabularInline):
    model = AllocationReglement
    extra = 0
    fields = ("facture", "montant_alloue")
    readonly_fields = ("facture", "montant_alloue")
    can_delete = False
    verbose_name_plural = "Allocations FIFO (auto-générées)"

    def has_add_permission(self, request, obj=None):
        return False


class FactureAllocationInline(admin.TabularInline):
    """Show allocations on a FactureFournisseur."""

    model = AllocationReglement
    extra = 0
    fields = ("reglement", "montant_alloue")
    readonly_fields = ("reglement", "montant_alloue")
    can_delete = False
    verbose_name_plural = "Règlements imputés (auto)"

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# BLFournisseur
# ---------------------------------------------------------------------------


@admin.register(BLFournisseur)
class BLFournisseurAdmin(ImportExportModelAdmin):
    resource_class = BLFournisseurResource

    list_display = (
        "reference",
        "fournisseur",
        "date_bl",
        "statut_badge",
        "montant_total_dzd",
        "a_piece_jointe",
        "created_at",
    )
    list_filter = ("statut", "fournisseur", "date_bl")
    search_fields = ("reference", "fournisseur__nom", "reference_fournisseur")
    date_hierarchy = "date_bl"
    readonly_fields = (
        "created_at",
        "updated_at",
        "montant_total_dzd",
        "est_verrouille",
    )
    inlines = (BLFournisseurLigneInline,)
    autocomplete_fields = ("fournisseur",)

    fieldsets = (
        (
            "Entête",
            {
                "fields": (
                    "reference",
                    "fournisseur",
                    "date_bl",
                    "reference_fournisseur",
                    "statut",
                    "est_verrouille",
                ),
            },
        ),
        (
            "Pièce jointe & notes",
            {
                "fields": ("piece_jointe", "notes_reception"),
                "classes": ("collapse",),
            },
        ),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Montant total (DZD)")
    def montant_total_dzd(self, obj):
        return f"{obj.montant_total:,.2f} DZD"

    @admin.display(description="Statut")
    def statut_badge(self, obj):
        colours = {
            BLFournisseur.STATUT_BROUILLON: "#888",
            BLFournisseur.STATUT_RECU: "#2e7d32",
            BLFournisseur.STATUT_FACTURE: "#1565c0",
            BLFournisseur.STATUT_LITIGE: "#b71c1c",
        }
        colour = colours.get(obj.statut, "#333")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    @admin.display(description="PJ", boolean=True)
    def a_piece_jointe(self, obj):
        return obj.a_piece_jointe

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.est_verrouille:
            # Lock the whole header when BL is invoiced
            base += [
                "reference",
                "fournisseur",
                "date_bl",
                "reference_fournisseur",
                "statut",
            ]
        return base


@admin.register(BLFournisseurLigne)
class BLFournisseurLigneAdmin(ImportExportModelAdmin):
    resource_class = BLFournisseurLigneResource

    list_display = ("bl", "intrant", "quantite", "prix_unitaire", "montant_total_dzd")
    list_filter = ("bl__statut", "intrant__categorie")
    search_fields = ("bl__reference", "intrant__designation")
    readonly_fields = ("montant_total_dzd",)
    autocomplete_fields = ("bl", "intrant")

    @admin.display(description="Total (DZD)")
    def montant_total_dzd(self, obj):
        return f"{obj.montant_total:,.2f} DZD"


# ---------------------------------------------------------------------------
# FactureFournisseur — export-only import; header locked after creation
# ---------------------------------------------------------------------------


@admin.register(FactureFournisseur)
class FactureFournisseurAdmin(ImportExportModelAdmin):
    resource_class = FactureFournisseurResource

    list_display = (
        "reference",
        "fournisseur",
        "date_facture",
        "type_facture",
        "montant_total_dzd",
        "montant_regle_dzd",
        "reste_a_payer_dzd",
        "statut_badge",
        "en_retard",
    )
    list_filter = ("statut", "type_facture", "fournisseur", "date_facture")
    search_fields = ("reference", "fournisseur__nom")
    date_hierarchy = "date_facture"
    filter_horizontal = ("bls",)
    readonly_fields = (
        "montant_total",
        "montant_regle",
        "reste_a_payer",
        "statut",
        "created_at",
        "updated_at",
    )
    inlines = (FactureAllocationInline,)
    autocomplete_fields = ("fournisseur",)

    fieldsets = (
        (
            "Facture",
            {
                "fields": (
                    "reference",
                    "fournisseur",
                    "date_facture",
                    "date_echeance",
                    "type_facture",
                ),
            },
        ),
        (
            "BL inclus",
            {
                "fields": ("bls",),
                "description": "Sélectionnez les BL reçus à regrouper dans cette facture.",
            },
        ),
        (
            "Finances (calculé automatiquement)",
            {
                "fields": ("montant_total", "montant_regle", "reste_a_payer", "statut"),
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

    @admin.display(description="Total (DZD)")
    def montant_total_dzd(self, obj):
        return f"{obj.montant_total:,.2f}"

    @admin.display(description="Réglé (DZD)")
    def montant_regle_dzd(self, obj):
        return f"{obj.montant_regle:,.2f}"

    @admin.display(description="Reste (DZD)")
    def reste_a_payer_dzd(self, obj):
        val = obj.reste_a_payer
        if val > 0:
            return format_html('<span style="color:red">{}</span>', f"{val:,.2f}")
        return f"{val:,.2f}"

    @admin.display(description="Statut")
    def statut_badge(self, obj):
        colours = {
            FactureFournisseur.STATUT_NON_PAYE: "#b71c1c",
            FactureFournisseur.STATUT_PARTIELLEMENT_PAYE: "#e65100",
            FactureFournisseur.STATUT_PAYE: "#2e7d32",
            FactureFournisseur.STATUT_EN_LITIGE: "#7b1fa2",
        }
        colour = colours.get(obj.statut, "#333")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    @admin.display(description="En retard", boolean=True)
    def en_retard(self, obj):
        return obj.est_en_retard

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        # After creation the BL set and computed fields are fully locked
        if obj:
            base += ["reference", "fournisseur", "bls", "type_facture"]
        return base


# ---------------------------------------------------------------------------
# ReglementFournisseur — export-only; immutable after creation
# ---------------------------------------------------------------------------


@admin.register(ReglementFournisseur)
class ReglementFournisseurAdmin(ImportExportModelAdmin):
    resource_class = ReglementFournisseurResource

    list_display = (
        "fournisseur",
        "date_reglement",
        "montant_dzd",
        "mode_paiement",
        "reference_paiement",
        "created_at",
    )
    list_filter = ("mode_paiement", "fournisseur", "date_reglement")
    search_fields = ("fournisseur__nom", "reference_paiement", "notes")
    date_hierarchy = "date_reglement"
    readonly_fields = ("created_at",)
    inlines = (AllocationReglementInline,)
    autocomplete_fields = ("fournisseur",)

    fieldsets = (
        (
            "Règlement",
            {
                "fields": (
                    "fournisseur",
                    "date_reglement",
                    "montant",
                    "mode_paiement",
                    "reference_paiement",
                ),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Montant (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    def get_readonly_fields(self, request, obj=None):
        # BR-REG-06: immutable after creation
        if obj:
            return (
                "fournisseur",
                "date_reglement",
                "montant",
                "mode_paiement",
                "reference_paiement",
                "notes",
                "created_by",
                "created_at",
            )
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AllocationReglement)
class AllocationReglementAdmin(ImportExportModelAdmin):
    resource_class = AllocationReglementResource

    list_display = ("reglement", "facture", "montant_alloue_dzd")
    list_filter = ("reglement__fournisseur",)
    search_fields = (
        "reglement__fournisseur__nom",
        "facture__reference",
    )
    readonly_fields = ("reglement", "facture", "montant_alloue")

    @admin.display(description="Montant alloué (DZD)")
    def montant_alloue_dzd(self, obj):
        return f"{obj.montant_alloue:,.2f} DZD"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AcompteFournisseur)
class AcompteFournisseurAdmin(ImportExportModelAdmin):
    resource_class = AcompteFournisseurResource

    list_display = (
        "fournisseur",
        "montant_dzd",
        "date",
        "utilise",
        "created_at",
    )
    list_filter = ("utilise", "fournisseur")
    search_fields = ("fournisseur__nom", "notes")
    date_hierarchy = "date"
    readonly_fields = ("fournisseur", "reglement", "montant", "date", "created_at")

    fieldsets = (
        (
            None,
            {
                "fields": ("fournisseur", "reglement", "montant", "date", "utilise"),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        ("Horodatage", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    @admin.display(description="Montant (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f} DZD"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
