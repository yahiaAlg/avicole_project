"""
clients/admin.py

Admin registration for the client AR cycle:
  Client, BLClient, BLClientLigne, FactureClient,
  PaiementClient, PaiementClientAllocation
"""

from django.contrib import admin
from django.utils.html import format_html

from clients.models import (
    Client,
    BLClient,
    BLClientLigne,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
)

# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class BLClientLigneInline(admin.TabularInline):
    model = BLClientLigne
    extra = 1
    fields = (
        "produit_fini",
        "quantite",
        "prix_unitaire",
        "montant_total_display",
        "notes",
    )
    readonly_fields = ("montant_total_display",)
    autocomplete_fields = ("produit_fini",)

    @admin.display(description="Total (DZD)")
    def montant_total_display(self, obj):
        if obj.pk:
            return f"{obj.montant_total:,.2f}"
        return "—"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.est_verrouille:
            return (
                "produit_fini",
                "quantite",
                "prix_unitaire",
                "montant_total_display",
                "notes",
            )
        return self.readonly_fields


class PaiementAllocationInline(admin.TabularInline):
    """Show allocations on a FactureClient (read-only)."""

    model = PaiementClientAllocation
    extra = 0
    fields = ("paiement", "montant_alloue")
    readonly_fields = ("paiement", "montant_alloue")
    can_delete = False
    verbose_name_plural = "Paiements imputés"

    def has_add_permission(self, request, obj=None):
        return False


class FactureAllocationInline(admin.TabularInline):
    """Show allocations on a PaiementClient."""

    model = PaiementClientAllocation
    extra = 1
    fields = ("facture", "montant_alloue")
    autocomplete_fields = ("facture",)

    def get_readonly_fields(self, request, obj=None):
        # Immutable after parent paiement is saved
        if obj and obj.pk:
            return ("facture", "montant_alloue")
        return ()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "type_client",
        "wilaya",
        "telephone",
        "actif",
        "creance_globale_dzd",
        "plafond_depasse",
    )
    list_filter = ("actif", "type_client", "wilaya")
    search_fields = ("nom", "nif", "rc", "telephone", "email")
    readonly_fields = (
        "created_at",
        "updated_at",
        "creance_globale_dzd",
        "plafond_depasse",
    )

    fieldsets = (
        (
            "Identification",
            {
                "fields": ("nom", "type_client", "plafond_credit", "actif"),
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
                "fields": ("creance_globale_dzd", "plafond_depasse"),
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

    @admin.display(description="Créance globale (DZD)")
    def creance_globale_dzd(self, obj):
        val = obj.creance_globale
        if val > 0:
            return format_html(
                '<span style="color:red;font-weight:bold">{} DZD</span>', f"{val:,.2f}"
            )
        return "0,00 DZD"

    @admin.display(description="Plafond dépassé", boolean=True)
    def plafond_depasse(self, obj):
        return obj.depasse_plafond


# ---------------------------------------------------------------------------
# BLClient
# ---------------------------------------------------------------------------


@admin.register(BLClient)
class BLClientAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "client",
        "date_bl",
        "statut_badge",
        "montant_total_dzd",
        "created_at",
    )
    list_filter = ("statut", "client", "date_bl")
    search_fields = ("reference", "client__nom", "signe_par")
    date_hierarchy = "date_bl"
    readonly_fields = (
        "created_at",
        "updated_at",
        "montant_total_dzd",
        "est_verrouille",
    )
    inlines = (BLClientLigneInline,)
    autocomplete_fields = ("client",)

    fieldsets = (
        (
            "Entête",
            {
                "fields": (
                    "reference",
                    "client",
                    "date_bl",
                    "adresse_livraison",
                    "signe_par",
                    "statut",
                    "est_verrouille",
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

    @admin.display(description="Montant total (DZD)")
    def montant_total_dzd(self, obj):
        return f"{obj.montant_total:,.2f} DZD"

    @admin.display(description="Statut")
    def statut_badge(self, obj):
        colours = {
            BLClient.STATUT_BROUILLON: "#888",
            BLClient.STATUT_LIVRE: "#2e7d32",
            BLClient.STATUT_FACTURE: "#1565c0",
            BLClient.STATUT_LITIGE: "#b71c1c",
        }
        colour = colours.get(obj.statut, "#333")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.est_verrouille:
            base += ["reference", "client", "date_bl", "adresse_livraison", "statut"]
        return base

    def has_delete_permission(self, request, obj=None):
        if obj and obj.statut in (BLClient.STATUT_LIVRE, BLClient.STATUT_FACTURE):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(BLClientLigne)
class BLClientLigneAdmin(admin.ModelAdmin):
    list_display = (
        "bl",
        "produit_fini",
        "quantite",
        "prix_unitaire",
        "montant_total_dzd",
    )
    list_filter = ("bl__statut", "produit_fini__type_produit")
    search_fields = ("bl__reference", "produit_fini__designation")
    readonly_fields = ("montant_total_dzd",)
    autocomplete_fields = ("bl", "produit_fini")

    @admin.display(description="Total (DZD)")
    def montant_total_dzd(self, obj):
        return f"{obj.montant_total:,.2f} DZD"


# ---------------------------------------------------------------------------
# FactureClient
# ---------------------------------------------------------------------------


@admin.register(FactureClient)
class FactureClientAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "client",
        "date_facture",
        "montant_ttc_dzd",
        "montant_regle_dzd",
        "reste_a_payer_dzd",
        "statut_badge",
        "en_retard",
    )
    list_filter = ("statut", "client", "date_facture")
    search_fields = ("reference", "client__nom")
    date_hierarchy = "date_facture"
    filter_horizontal = ("bls",)
    readonly_fields = (
        "montant_ht",
        "montant_tva",
        "montant_ttc",
        "montant_regle",
        "reste_a_payer",
        "statut",
        "created_at",
        "updated_at",
    )
    inlines = (PaiementAllocationInline,)
    autocomplete_fields = ("client",)

    fieldsets = (
        (
            "Facture",
            {
                "fields": (
                    "reference",
                    "client",
                    "date_facture",
                    "date_echeance",
                    "taux_tva",
                ),
            },
        ),
        ("BL inclus", {"fields": ("bls",)}),
        (
            "Finances (calculé automatiquement)",
            {
                "fields": (
                    "montant_ht",
                    "montant_tva",
                    "montant_ttc",
                    "montant_regle",
                    "reste_a_payer",
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

    @admin.display(description="TTC (DZD)")
    def montant_ttc_dzd(self, obj):
        return f"{obj.montant_ttc:,.2f}"

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
            FactureClient.STATUT_NON_PAYEE: "#b71c1c",
            FactureClient.STATUT_PARTIELLEMENT_PAYEE: "#e65100",
            FactureClient.STATUT_PAYEE: "#2e7d32",
            FactureClient.STATUT_EN_LITIGE: "#7b1fa2",
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
        if obj:
            base += ["reference", "client", "bls", "taux_tva"]
        return base


# ---------------------------------------------------------------------------
# PaiementClient
# ---------------------------------------------------------------------------


@admin.register(PaiementClient)
class PaiementClientAdmin(admin.ModelAdmin):
    list_display = (
        "client",
        "date_paiement",
        "montant_dzd",
        "montant_alloue_dzd",
        "solde_non_alloue_dzd",
        "mode_paiement",
        "reference_paiement",
    )
    list_filter = ("mode_paiement", "client", "date_paiement")
    search_fields = ("client__nom", "reference_paiement", "notes")
    date_hierarchy = "date_paiement"
    readonly_fields = ("created_at", "montant_alloue_dzd", "solde_non_alloue_dzd")
    inlines = (FactureAllocationInline,)
    autocomplete_fields = ("client",)

    fieldsets = (
        (
            "Paiement",
            {
                "fields": (
                    "client",
                    "date_paiement",
                    "montant",
                    "mode_paiement",
                    "reference_paiement",
                ),
            },
        ),
        (
            "Ventilation (calculé)",
            {
                "fields": ("montant_alloue_dzd", "solde_non_alloue_dzd"),
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

    @admin.display(description="Alloué (DZD)")
    def montant_alloue_dzd(self, obj):
        return f"{obj.montant_alloue:,.2f} DZD"

    @admin.display(description="Non alloué (DZD)")
    def solde_non_alloue_dzd(self, obj):
        val = obj.solde_non_alloue
        if val > 0:
            return format_html(
                '<span style="color:orange">{} DZD</span>', f"{val:,.2f}"
            )
        return "0,00 DZD"

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "client",
                "date_paiement",
                "montant",
                "mode_paiement",
                "reference_paiement",
                "notes",
                "created_by",
                "created_at",
                "montant_alloue_dzd",
                "solde_non_alloue_dzd",
            )
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaiementClientAllocation)
class PaiementClientAllocationAdmin(admin.ModelAdmin):
    list_display = ("paiement", "facture", "montant_alloue")
    list_filter = ("paiement__client",)
    search_fields = ("paiement__client__nom", "facture__reference")
    readonly_fields = ("paiement", "facture", "montant_alloue")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
