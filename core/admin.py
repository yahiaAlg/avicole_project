"""
core/admin.py

Admin registration for the core app:
  - CompanyInfo  (singleton — add button hidden, only edit pk=1)
  - UserProfile  (inline on User + standalone)
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from import_export.admin import ImportExportModelAdmin

from core.models import CompanyInfo, UserProfile
from core.resources import CompanyInfoResource, UserProfileResource

# ---------------------------------------------------------------------------
# UserProfile — inline on User
# ---------------------------------------------------------------------------


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profil"
    fields = ("role", "telephone", "notes")
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

    list_display = ("user", "get_full_name", "role", "telephone", "created_at")
    list_filter = ("role",)
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "telephone",
    )
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("user", "role", "telephone")}),
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
