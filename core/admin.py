"""
core/admin.py

Admin registration for the core app:
  - CompanyInfo  (singleton — add button hidden, only edit pk=1)
  - Branche      (v1.4 multi-branch architecture, §3.5)
  - UserProfile  (inline on User + standalone)

Also defines BrancheScopedAdminMixin, reused by every other app's admin
to enforce BR-BRA-02/03/04: a chef_branche/opérateur (and a comptable
bound to a branche) only ever sees/creates records in their own branche;
admin and an unbound comptable keep full Vue Globale visibility.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from core.models import CompanyInfo, Branche, UserProfile, PieceJointe
from core.resources import CompanyInfoResource, UserProfileResource


# ---------------------------------------------------------------------------
# PieceJointeInline — shared generic inline (v1.5), reused by every admin
# that used to carry an ad-hoc `piece_jointe` FileField (BLFournisseur,
# FactureFournisseur, ReglementFournisseur, AcompteFournisseur, BLClient,
# FactureClient, PaiementClient, Depense, RetraitAssocie, AcompteEmploye,
# BulletinPaie). Import and drop into `inlines` on the target ModelAdmin.
# ---------------------------------------------------------------------------


class PieceJointeInline(GenericTabularInline):
    model = PieceJointe
    extra = 1
    fields = ("fichier", "type_document", "description", "uploaded_by")
    readonly_fields = ("uploaded_by",)
    verbose_name = "Pièce jointe"
    verbose_name_plural = "Pièces jointes"

    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        if not obj.uploaded_by_id and getattr(self, "_request", None):
            obj.uploaded_by = self._request.user
        if commit:
            obj.save()
        return obj

    def get_formset(self, request, obj=None, **kwargs):
        self._request = request
        return super().get_formset(request, obj, **kwargs)

# ---------------------------------------------------------------------------
# BrancheScopedAdminMixin — shared scoping logic (BR-BRA-01..04)
# ---------------------------------------------------------------------------


class BrancheScopedAdminMixin:
    """
    Mixin for ModelAdmins of branch-scoped models.

    - `branche_lookup`: ORM lookup used to filter the queryset to the
      logged-in user's branche (e.g. "branche" for models with their own
      FK, or "bl__branche" for a line/child model reached through a
      parent). Defaults to "branche".
    - When the lookup IS the model's own "branche" FK, the field's choices
      are also restricted to that single branche on the add/edit form, and
      it is auto-filled on creation, so a locked user never has to (and
      cannot) pick a different one.
    - Admin and an unbound comptable (a_vue_globale=True) are unaffected.
    """

    branche_lookup = "branche"

    def _profile(self, request):
        return getattr(request.user, "profile", None)

    def _is_locked_to_branche(self, request):
        profile = self._profile(request)
        return bool(profile) and not profile.a_vue_globale

    def _user_branche(self, request):
        profile = self._profile(request)
        return profile.branche if profile else None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self._is_locked_to_branche(request):
            qs = qs.filter(**{self.branche_lookup: self._user_branche(request)})
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if (
            self.branche_lookup == "branche"
            and db_field.name == "branche"
            and self._is_locked_to_branche(request)
        ):
            branche = self._user_branche(request)
            kwargs["queryset"] = Branche.objects.filter(
                pk=branche.pk if branche else None
            )
            kwargs["initial"] = branche
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if (
            self.branche_lookup == "branche"
            and not change
            and self._is_locked_to_branche(request)
            and not getattr(obj, "branche_id", None)
        ):
            obj.branche = self._user_branche(request)
        super().save_model(request, obj, form, change)


# ---------------------------------------------------------------------------
# Branche
# ---------------------------------------------------------------------------


@admin.register(Branche)
class BrancheAdmin(admin.ModelAdmin):
    list_display = ("nom", "code", "wilaya", "chef_de_branche", "actif", "created_at")
    list_filter = ("actif", "wilaya")
    search_fields = ("nom", "code", "wilaya")
    list_editable = ("actif",)
    autocomplete_fields = ("chef_de_branche",)
    readonly_fields = ("created_at",)

    fieldsets = (
        (
            "Identification",
            {"fields": ("nom", "code", "actif")},
        ),
        (
            "Coordonnées",
            {"fields": ("wilaya", "adresse", "telephone")},
        ),
        (
            "Responsable",
            {
                "fields": ("chef_de_branche",),
                "description": (
                    "L'utilisateur choisi doit porter le rôle « رئيس فرع » "
                    "(BR-BRA-02)."
                ),
            },
        ),
        ("Horodatage", {"fields": ("created_at",), "classes": ("collapse",)}),
    )


# ---------------------------------------------------------------------------
# UserProfile — inline on User
# ---------------------------------------------------------------------------


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profil"
    fields = ("role", "branche", "telephone", "notes")
    autocomplete_fields = ("branche",)
    extra = 1


class UserAdminWithProfile(BaseUserAdmin):
    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, UserAdminWithProfile)


# ---------------------------------------------------------------------------
# UserProfile — standalone (read-only audit view)
# ---------------------------------------------------------------------------


@admin.register(UserProfile)
class UserProfileAdmin(ImportExportModelAdmin):
    resource_class = UserProfileResource

    list_display = ("user", "get_full_name", "role", "branche", "telephone", "created_at")
    list_filter = ("role", "branche")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "telephone",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("branche",)

    fieldsets = (
        (
            None,
            {
                "fields": ("user", "role", "branche", "telephone"),
                "description": (
                    "Branche : obligatoire pour رئيس فرع/مشغّل (BR-BRA-02), "
                    "optionnelle pour محاسب, toujours vide pour مدير (BR-BRA-03)."
                ),
            },
        ),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
        (
            "Horodatage",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description="Nom complet")
    def get_full_name(self, obj):
        return obj.user.get_full_name() or "—"


# ---------------------------------------------------------------------------
# CompanyInfo — singleton
# ---------------------------------------------------------------------------


@admin.register(CompanyInfo)
class CompanyInfoAdmin(ImportExportModelAdmin):
    resource_class = CompanyInfoResource

    fieldsets = (
        (
            "Identité de l'entreprise",
            {
                "fields": (
                    "nom",
                    "logo",
                    "adresse",
                    "wilaya",
                    "telephone",
                    "telephone_2",
                    "email",
                ),
            },
        ),
        (
            "Identifiants fiscaux & légaux",
            {
                "fields": ("nif", "rc", "ai", "nis", "tap", "rib", "banque"),
                "classes": ("collapse",),
            },
        ),
        (
            "Paramètres fiscaux",
            {
                "fields": ("regime_fiscal", "assujetti_tva", "taux_tva"),
            },
        ),
        (
            "Paramètres de l'application",
            {
                "fields": (
                    "devise",
                    "format_date",
                    "prefixe_bl_client",
                    "prefixe_bl_fournisseur",
                    "prefixe_facture_client",
                    "prefixe_facture_fournisseur",
                ),
            },
        ),
        (
            "Documents imprimés",
            {
                "fields": ("pied_de_page",),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        # Singleton — block creating a second record
        return not CompanyInfo.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Logo")
    def logo_preview(self, obj):
        if obj.logo:
            return format_html('<img src="{}" height="40" />', obj.logo.url)
        return "—"
