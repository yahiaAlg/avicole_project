"""
clients/admin.py

Admin registration for the client AR cycle:
  TypeClient, Client, BLClient, BLClientLigne, FactureClient,
  PaiementClient, PaiementClientAllocation
"""

from django.contrib import admin
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from core.admin import BrancheScopedAdminMixin, PieceJointeInline
from clients.models import (
    TypeClient,
    Client,
    BLClient,
    BLClientLigne,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
    AcompteClient,
    AllocationAcompteClient,
    AbonnementClient,
    VoyageLivraison,
    LivraisonPartielle,
    PrixMarche,
)
from clients.resources import (
    TypeClientResource,
    ClientResource,
    BLClientResource,
    BLClientLigneResource,
    FactureClientResource,
    PaiementClientResource,
    PaiementClientAllocationResource,
    AbonnementClientResource,
    VoyageLivraisonResource,
    LivraisonPartielleResource,
    PrixMarcheResource,
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
# TypeClient
# ---------------------------------------------------------------------------


@admin.register(TypeClient)
class TypeClientAdmin(ImportExportModelAdmin):
    resource_classes = [TypeClientResource]

    list_display = ("libelle", "code", "ordre", "actif")
    list_filter = ("actif",)
    search_fields = ("code", "libelle")
    list_editable = ("ordre", "actif")
    ordering = ("ordre", "libelle")

    def get_readonly_fields(self, request, obj=None):
        # Seed codes must not be renamed
        if obj and obj.code in (
            "GROSSISTE", "DETAILLANT", "RESTAURATION", "PARTICULIER", "AUTRE",
        ):
            return ("code",)
        return ()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@admin.register(Client)
class ClientAdmin(ImportExportModelAdmin):
    resource_classes = [ClientResource]
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
    autocomplete_fields = ("type_client",)
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
        val = obj.creance_globale()
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
class BLClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [BLClientResource]
    list_display = (
        "reference",
        "branche",
        "client",
        "date_bl",
        "statut_badge",
        "montant_total_dzd",
        "a_piece_jointe",
        "created_at",
    )
    list_filter = ("statut", "branche", "client", "date_bl")
    search_fields = ("reference", "client__nom", "signe_par")
    date_hierarchy = "date_bl"
    readonly_fields = (
        "created_at",
        "updated_at",
        "montant_total_dzd",
        "est_verrouille",
    )
    inlines = (BLClientLigneInline, PieceJointeInline)
    autocomplete_fields = ("branche", "client")

    fieldsets = (
        (
            "Entête",
            {
                "fields": (
                    "reference",
                    "branche",
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

    @admin.display(description="PJ", boolean=True)
    def a_piece_jointe(self, obj):
        return obj.a_piece_jointe

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
            base += [
                "reference",
                "branche",
                "client",
                "date_bl",
                "adresse_livraison",
                "statut",
            ]
        return base

    def has_delete_permission(self, request, obj=None):
        if obj and obj.statut in (BLClient.STATUT_LIVRE, BLClient.STATUT_FACTURE):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(BLClientLigne)
class BLClientLigneAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [BLClientLigneResource]
    branche_lookup = "bl__branche"

    list_display = (
        "bl",
        "produit_fini",
        "quantite",
        "prix_unitaire",
        "montant_total_dzd",
    )
    list_filter = ("bl__statut", "bl__branche", "produit_fini__type_produit")
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
class FactureClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [FactureClientResource]
    list_display = (
        "reference",
        "branche",
        "client",
        "date_facture",
        "montant_ttc_dzd",
        "montant_regle_dzd",
        "reste_a_payer_dzd",
        "statut_badge",
        "en_retard",
    )
    list_filter = ("statut", "branche", "client", "date_facture")
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
    inlines = (PaiementAllocationInline, PieceJointeInline)
    autocomplete_fields = ("branche", "client")

    fieldsets = (
        (
            "Facture",
            {
                "fields": (
                    "reference",
                    "branche",
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
            base += ["reference", "branche", "client", "bls", "taux_tva"]
        return base

    # -----------------------------------------------------------------
    # Cascade delete — a plain .delete() would hit ProtectedError because
    # PaiementClientAllocation.facture/.paiement are both on_delete=PROTECT.
    # Route Django admin's delete actions through the same admin-only
    # cascade used by the app's own "Supprimer" button
    # (clients.utils.supprimer_facture_client_cascade), which also deletes
    # the invoice's BLs, reverses their stock effect, and deletes any
    # paiement that paid it.
    # -----------------------------------------------------------------

    def delete_model(self, request, obj):
        from clients.utils import supprimer_facture_client_cascade

        supprimer_facture_client_cascade(obj)

    def delete_queryset(self, request, queryset):
        from clients.utils import supprimer_facture_client_cascade

        for facture in queryset:
            supprimer_facture_client_cascade(facture)


# ---------------------------------------------------------------------------
# PaiementClient
# ---------------------------------------------------------------------------


@admin.register(PaiementClient)
class PaiementClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [PaiementClientResource]
    list_display = (
        "branche",
        "client",
        "date_paiement",
        "montant_dzd",
        "montant_alloue_dzd",
        "solde_non_alloue_dzd",
        "mode_paiement",
        "reference_paiement",
    )
    list_filter = ("mode_paiement", "branche", "client", "date_paiement")
    search_fields = ("client__nom", "reference_paiement", "notes")
    date_hierarchy = "date_paiement"
    readonly_fields = ("created_at", "montant_alloue_dzd", "solde_non_alloue_dzd")
    inlines = (FactureAllocationInline, PieceJointeInline)
    autocomplete_fields = ("branche", "client")

    fieldsets = (
        (
            "Paiement",
            {
                "fields": (
                    "branche",
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
                "branche",
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

    # -----------------------------------------------------------------
    # Cascade delete — admins may remove a paiement to correct a mistake
    # (wrong client/amount/mode). A plain .delete() would hit
    # ProtectedError because PaiementClientAllocation.paiement/.facture
    # are on_delete=PROTECT, and would also silently leave a linked
    # AcompteClient (and anything it funded) inconsistent. Route through
    # the same admin-only cascade used by the app's own "Supprimer" button
    # (clients.utils.supprimer_paiement_client_cascade), which reverses
    # every allocation (direct or via its acompte) and recalculates the
    # affected factures first.
    # -----------------------------------------------------------------

    def delete_model(self, request, obj):
        from clients.utils import supprimer_paiement_client_cascade

        supprimer_paiement_client_cascade(obj)

    def delete_queryset(self, request, queryset):
        from clients.utils import supprimer_paiement_client_cascade

        for paiement in queryset:
            supprimer_paiement_client_cascade(paiement)


@admin.register(PaiementClientAllocation)
class PaiementClientAllocationAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    # Export-only — has_add_permission below blocks the import button too,
    # since these allocations are only ever created via clean() on the model
    # (BR-FAC-03) and are immutable afterwards.
    resource_classes = [PaiementClientAllocationResource]
    branche_lookup = "paiement__branche"

    list_display = ("paiement", "facture", "montant_alloue")
    list_filter = ("paiement__client", "paiement__branche")
    search_fields = ("paiement__client__nom", "facture__reference")
    readonly_fields = ("paiement", "facture", "montant_alloue")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Acompte Client (prepayments) — created automatically, never via admin
# ---------------------------------------------------------------------------


class AllocationAcompteClientInline(admin.TabularInline):
    """Show which factures an AcompteClient has funded (read-only)."""

    model = AllocationAcompteClient
    extra = 0
    fields = ("facture", "montant_alloue", "created_at")
    readonly_fields = ("facture", "montant_alloue", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AcompteClient)
class AcompteClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    list_display = (
        "branche",
        "client",
        "date",
        "montant_dzd",
        "montant_restant_dzd",
        "utilise",
        "paiement",
    )
    list_filter = ("branche", "client", "utilise", "date")
    search_fields = ("client__nom", "notes")
    date_hierarchy = "date"
    readonly_fields = (
        "client",
        "branche",
        "paiement",
        "montant",
        "montant_restant",
        "date",
        "utilise",
        "created_at",
        "updated_at",
    )
    inlines = (AllocationAcompteClientInline, PieceJointeInline)
    autocomplete_fields = ("branche", "client", "paiement")

    @admin.display(description="Montant (DZD)")
    def montant_dzd(self, obj):
        return f"{obj.montant:,.2f}"

    @admin.display(description="Restant (DZD)")
    def montant_restant_dzd(self, obj):
        val = obj.montant_restant
        colour = "#2e7d32" if val <= 0 else "#b45309"
        return format_html(
            '<span style="color:{}">{}</span>', colour, f"{val:,.2f}"
        )

    def has_add_permission(self, request):
        # Only ever created automatically (payment surplus).
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AllocationAcompteClient)
class AllocationAcompteClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    branche_lookup = "acompte__branche"
    list_display = ("acompte", "facture", "montant_alloue", "created_at")
    list_filter = ("acompte__branche", "acompte__client")
    search_fields = ("acompte__client__nom", "facture__reference")
    readonly_fields = ("acompte", "facture", "montant_alloue", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Abonnement / livraisons partielles (recurring deliveries)
# ---------------------------------------------------------------------------


class LivraisonPartielleInline(admin.TabularInline):
    """Show deliveries on an AbonnementClient (read-only — created via own admin)."""

    model = LivraisonPartielle
    extra = 0
    fields = ("date", "voyage", "quantite_livree", "notes")
    readonly_fields = ("date", "voyage", "quantite_livree", "notes")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AbonnementClient)
class AbonnementClientAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [AbonnementClientResource]
    list_display = (
        "branche",
        "client",
        "produit_fini",
        "frequence",
        "date_debut",
        "date_fin",
        "quantite_totale_prevue",
        "quantite_livree_cumulee_dzd",
        "solde_restant_dzd",
        "statut_badge",
    )
    list_filter = ("statut", "branche", "frequence", "produit_fini")
    search_fields = ("client__nom", "produit_fini__designation")
    readonly_fields = (
        "created_at",
        "updated_at",
        "quantite_livree_cumulee_dzd",
        "solde_restant_dzd",
    )
    inlines = (LivraisonPartielleInline,)
    autocomplete_fields = ("branche", "client", "produit_fini")

    fieldsets = (
        (
            "Abonnement",
            {
                "fields": (
                    "branche",
                    "client",
                    "produit_fini",
                    "date_debut",
                    "date_fin",
                    "frequence",
                    "quantite_totale_prevue",
                    "prix_unitaire",
                    "statut",
                ),
            },
        ),
        (
            "Suivi (calculé)",
            {
                "fields": ("quantite_livree_cumulee_dzd", "solde_restant_dzd"),
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
        colours = {
            AbonnementClient.STATUT_ACTIF: "#2e7d32",
            AbonnementClient.STATUT_TERMINE: "#888",
            AbonnementClient.STATUT_SUSPENDU: "#e65100",
        }
        colour = colours.get(obj.statut, "#333")
        return format_html(
            '<span style="color:{};font-weight:bold">{}</span>',
            colour,
            obj.get_statut_display(),
        )

    @admin.display(description="Livré cumulé")
    def quantite_livree_cumulee_dzd(self, obj):
        return obj.quantite_livree_cumulee

    @admin.display(description="Solde restant")
    def solde_restant_dzd(self, obj):
        val = obj.solde_restant
        return "بدون سقف" if val is None else val


@admin.register(VoyageLivraison)
class VoyageLivraisonAdmin(ImportExportModelAdmin):
    resource_classes = [VoyageLivraisonResource]
    list_display = (
        "date_voyage",
        "chauffeur",
        "vehicule",
        "quantite_totale_livree_dzd",
    )
    list_filter = ("date_voyage",)
    search_fields = ("chauffeur", "vehicule")
    date_hierarchy = "date_voyage"
    readonly_fields = ("created_at", "quantite_totale_livree_dzd")

    fieldsets = (
        (None, {"fields": ("date_voyage", "chauffeur", "vehicule")}),
        ("Indicateur (calculé)", {"fields": ("quantite_totale_livree_dzd",)}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Quantité totale livrée")
    def quantite_totale_livree_dzd(self, obj):
        return obj.quantite_totale_livree


@admin.register(LivraisonPartielle)
class LivraisonPartielleAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_classes = [LivraisonPartielleResource]
    branche_lookup = "abonnement__branche"

    list_display = ("abonnement", "voyage", "date", "quantite_livree")
    list_filter = ("voyage", "abonnement__branche", "date")
    search_fields = ("abonnement__client__nom", "abonnement__produit_fini__designation")
    date_hierarchy = "date"
    autocomplete_fields = ("abonnement", "voyage")
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {"fields": ("abonnement", "voyage", "date", "quantite_livree")}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {
                "fields": ("created_by", "created_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        # Immutable after creation — the documented way to reverse a
        # delivery is to delete the record (signal reverses the stock
        # effect), not to edit it (see clients/models.py LivraisonPartielle).
        if obj:
            return (
                "abonnement",
                "voyage",
                "date",
                "quantite_livree",
                "notes",
                "created_by",
                "created_at",
            )
        return self.readonly_fields


# ---------------------------------------------------------------------------
# PrixMarche — daily egg market price history
# ---------------------------------------------------------------------------


@admin.register(PrixMarche)
class PrixMarcheAdmin(ImportExportModelAdmin):
    resource_classes = [PrixMarcheResource]
    list_display = (
        "date",
        "produit_fini",
        "prix_marche",
        "source",
        "created_by",
        "created_at",
    )
    list_filter = ("produit_fini", "date")
    search_fields = ("produit_fini__designation", "source", "notes")
    date_hierarchy = "date"
    readonly_fields = ("created_by", "created_at", "updated_at")
    autocomplete_fields = ()

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "produit_fini",
                    "date",
                    "prix_marche",
                    "source",
                )
            },
        ),
        (
            "Notes",
            {
                "fields": ("notes",),
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
