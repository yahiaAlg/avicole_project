"""
elevage/admin.py

Admin registration for the poultry raising module:
  LotElevage, Mortalite, Consommation
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from import_export.admin import ImportExportModelAdmin

from core.admin import BrancheScopedAdminMixin
from elevage.models import (
    ParametrageElevage,
    LotElevage,
    Mortalite,
    Consommation,
    ConsommationAlimentAllocation,
    TransfertLot,
    PeseeEchantillon,
    RecolteOeufs,
    FormuleAliment,
    FormuleAlimentLigne,
    ProductionAliment,
    RetraitOeufs,
)
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
    fields = ("date", "intrant", "quantite", "prix_unitaire", "notes")
    autocomplete_fields = ("intrant",)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.statut == LotElevage.STATUT_FERME:
            return ("date", "intrant", "quantite", "prix_unitaire", "notes")
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
class LotElevageAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = LotElevageResource
    actions = (fermer_lots,)

    list_display = (
        "designation",
        "branche",
        "batiment",
        "statut_badge",
        "date_ouverture",
        "date_fermeture",
        "nombre_poussins_initial",
        "effectif_vivant_display",
        "taux_mortalite_display",
        "duree_jours",
    )
    list_filter = (
        "statut",
        "branche",
        "batiment",
        "fournisseur_poussins",
        "date_ouverture",
    )
    search_fields = ("designation", "souche", "notes")
    date_hierarchy = "date_ouverture"
    readonly_fields = (
        "branche",
        "lot_parent",
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
    autocomplete_fields = ("fournisseur_poussins", "batiment", "lot_parent")

    fieldsets = (
        (
            "Lot",
            {
                "fields": (
                    "designation",
                    "branche",
                    "statut",
                    "date_ouverture",
                    "date_fermeture",
                    "lot_parent",
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
class MortaliteAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = MortaliteResource
    branche_lookup = "lot__branche"

    list_display = ("lot", "date", "nombre", "cause", "created_at")
    list_filter = ("lot__statut", "lot__branche", "lot", "date")
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
class ConsommationAdmin(BrancheScopedAdminMixin, ImportExportModelAdmin):
    resource_class = ConsommationResource
    branche_lookup = "lot__branche"

    list_display = (
        "lot",
        "date",
        "intrant",
        "quantite",
        "prix_unitaire",
        "est_paye_badge",
        "created_at",
    )
    list_filter = ("lot__statut", "lot__branche", "intrant__categorie", "date")
    search_fields = ("lot__designation", "intrant__designation")
    date_hierarchy = "date"
    autocomplete_fields = ("lot", "intrant", "depense_paiement")
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {"fields": ("lot", "date", "intrant", "quantite")}),
        (
            "Tarification / paiement (médicament — BR-request)",
            {
                "fields": ("prix_unitaire", "depense_paiement"),
                "classes": ("collapse",),
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

    @admin.display(description="مدفوعة", boolean=True)
    def est_paye_badge(self, obj):
        return obj.est_paye


# ---------------------------------------------------------------------------
# ParametrageElevage — singleton config row
# ---------------------------------------------------------------------------


@admin.register(ParametrageElevage)
class ParametrageElevageAdmin(admin.ModelAdmin):
    list_display = (
        "age_transfert_poussiniere_jours",
        "age_maturite_vente_jours",
    )

    def has_add_permission(self, request):
        # Singleton row (pk=1) — created on first access via get_solo().
        return not ParametrageElevage.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# TransfertLot
# ---------------------------------------------------------------------------


@admin.register(TransfertLot)
class TransfertLotAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "lot__branche"

    list_display = (
        "lot",
        "batiment_origine",
        "batiment_destination",
        "date_transfert",
        "age_jours_transfert",
        "effectif_transfere",
        "motif",
    )
    list_filter = (
        "lot__branche",
        "batiment_origine",
        "batiment_destination",
        "date_transfert",
    )
    search_fields = ("lot__designation", "motif")
    date_hierarchy = "date_transfert"
    autocomplete_fields = ("lot", "batiment_origine", "batiment_destination")
    readonly_fields = ("created_at",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "lot",
                    "batiment_origine",
                    "batiment_destination",
                    "date_transfert",
                    "age_jours_transfert",
                    "effectif_transfere",
                    "motif",
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


# ---------------------------------------------------------------------------
# PeseeEchantillon
# ---------------------------------------------------------------------------


@admin.register(PeseeEchantillon)
class PeseeEchantillonAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "lot__branche"

    list_display = (
        "lot",
        "date",
        "type_pesee",
        "nombre_sujets",
        "poids_total_g",
        "poids_moyen_g_display",
        "qualite_display",
    )
    list_filter = ("type_pesee", "lot__branche", "lot", "date")
    search_fields = ("lot__designation",)
    date_hierarchy = "date"
    autocomplete_fields = ("lot",)
    readonly_fields = ("created_at", "poids_moyen_g_display", "qualite_display")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "lot",
                    "date",
                    "type_pesee",
                    "nombre_sujets",
                    "poids_total_g",
                    "poids_moyen_g_display",
                    "qualite_display",
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

    @admin.display(description="الوزن المتوسط (غ)")
    def poids_moyen_g_display(self, obj):
        return obj.poids_moyen_g

    @admin.display(description="الجودة")
    def qualite_display(self, obj):
        qualite = obj.qualite
        return qualite.libelle if qualite else "—"


# ---------------------------------------------------------------------------
# RecolteOeufs
# ---------------------------------------------------------------------------


@admin.register(RecolteOeufs)
class RecolteOeufsAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "lot__branche"

    list_display = (
        "lot",
        "date",
        "nombre_oeufs",
        "nombre_plateaux_display",
        "oeufs_hors_plateau_display",
        "pesee",
        "qualite_display",
    )
    list_filter = ("lot__branche", "lot", "date")
    search_fields = ("lot__designation",)
    date_hierarchy = "date"
    autocomplete_fields = ("lot", "pesee")
    readonly_fields = (
        "created_at",
        "nombre_plateaux_display",
        "oeufs_hors_plateau_display",
        "qualite_display",
    )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "lot",
                    "date",
                    "nombre_oeufs",
                    "nombre_plateaux_display",
                    "oeufs_hors_plateau_display",
                    "pesee",
                    "qualite_display",
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

    @admin.display(description="عدد الصواني")
    def nombre_plateaux_display(self, obj):
        return obj.nombre_plateaux

    @admin.display(description="بيض خارج الصواني")
    def oeufs_hors_plateau_display(self, obj):
        return obj.oeufs_hors_plateau

    @admin.display(description="الجودة")
    def qualite_display(self, obj):
        qualite = obj.qualite
        return qualite.libelle if qualite else "—"


# ---------------------------------------------------------------------------
# FormuleAliment / FormuleAlimentLigne
# ---------------------------------------------------------------------------


class FormuleAlimentLigneInline(admin.TabularInline):
    model = FormuleAlimentLigne
    extra = 1
    fields = ("intrant", "proportion_kg")
    autocomplete_fields = ("intrant",)


@admin.register(FormuleAliment)
class FormuleAlimentAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "intrant_produit",
        "actif",
        "total_proportion_kg_display",
        "created_at",
    )
    list_filter = ("actif", "intrant_produit")
    search_fields = ("nom", "intrant_produit__designation")
    autocomplete_fields = ("intrant_produit",)
    readonly_fields = ("created_at",)
    inlines = (FormuleAlimentLigneInline,)

    fieldsets = (
        (None, {"fields": ("nom", "intrant_produit", "actif")}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        ("Horodatage", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    @admin.display(description="إجمالي النسب (كغ/100كغ)")
    def total_proportion_kg_display(self, obj):
        return obj.total_proportion_kg


# ---------------------------------------------------------------------------
# ProductionAliment
# ---------------------------------------------------------------------------


@admin.register(ProductionAliment)
class ProductionAlimentAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "intrant_produit",
        "branche",
        "date",
        "formule",
        "quantite_produite_kg",
        "quantite_restante_kg",
        "prix_unitaire",
        "montant_total_display",
        "est_paye_badge",
    )
    list_filter = ("branche", "intrant_produit", "formule", "date")
    search_fields = ("intrant_produit__designation", "formule__nom")
    date_hierarchy = "date"
    autocomplete_fields = ("intrant_produit", "formule", "depense_paiement")
    readonly_fields = (
        "created_at",
        "montant_total_display",
        "quantite_restante_kg",
        "prix_facon_unitaire",
        "cout_facon_impute",
    )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "branche",
                    "date",
                    "intrant_produit",
                    "formule",
                    "quantite_produite_kg",
                    "prix_unitaire",
                    "montant_total_display",
                ),
            },
        ),
        (
            "تكلفة التصنيع بالدفعة (BR-request)",
            {
                "fields": (
                    "quantite_restante_kg",
                    "prix_facon_unitaire",
                    "cout_facon_impute",
                    "depense_paiement",
                ),
                "classes": ("collapse",),
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

    @admin.display(description="المبلغ الإجمالي (د.ج)")
    def montant_total_display(self, obj):
        return obj.montant_total

    @admin.display(description="مدفوعة", boolean=True)
    def est_paye_badge(self, obj):
        return obj.est_paye


# ---------------------------------------------------------------------------
# ConsommationAlimentAllocation — auto-generated batch-costing ledger
# ---------------------------------------------------------------------------


@admin.register(ConsommationAlimentAllocation)
class ConsommationAlimentAllocationAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    branche_lookup = "consommation__lot__branche"

    list_display = (
        "consommation",
        "production",
        "quantite_kg",
        "cout_facon_alloue",
        "created_at",
    )
    list_filter = ("consommation__lot__branche", "consommation__lot")
    search_fields = ("consommation__lot__designation", "production__intrant_produit__designation")
    readonly_fields = ("consommation", "production", "quantite_kg", "cout_facon_alloue", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# RetraitOeufs
# ---------------------------------------------------------------------------


@admin.register(RetraitOeufs)
class RetraitOeufsAdmin(BrancheScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "date",
        "branche",
        "lot",
        "quantite_oeufs",
        "motif",
        "client",
        "destinataire",
        "bl_genere",
    )
    list_filter = ("branche", "motif", "lot", "date")
    search_fields = ("lot__designation", "client__nom", "destinataire")
    date_hierarchy = "date"
    autocomplete_fields = ("lot", "client")
    readonly_fields = ("bl_genere", "created_at")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "branche",
                    "lot",
                    "date",
                    "quantite_oeufs",
                    "motif",
                    "client",
                    "destinataire",
                    "bl_genere",
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
