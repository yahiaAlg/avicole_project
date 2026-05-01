"""
core/resources.py

Import-export resources for the core app.

CompanyInfo is a singleton — import is restricted to update-only (skip create).
UserProfile export is provided for HR/audit purposes; import is intentionally
disabled (user creation goes through Django's auth system).
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User
from core.models import CompanyInfo, UserProfile


class CompanyInfoResource(resources.ModelResource):
    """
    Singleton export/import for company identity and settings.
    Import always targets pk=1 (enforced by model.save()).
    File-based fields (logo) are excluded — managed via admin upload.
    """

    class Meta:
        model = CompanyInfo
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        exclude = ["logo"]
        export_order = [
            "id",
            "nom",
            "adresse",
            "wilaya",
            "telephone",
            "telephone_2",
            "email",
            "nif",
            "rc",
            "ai",
            "nis",
            "regime_fiscal",
            "assujetti_tva",
            "taux_tva",
            "tap",
            "rib",
            "banque",
            "devise",
            "format_date",
            "prefixe_bl_client",
            "prefixe_bl_fournisseur",
            "prefixe_facture_client",
            "prefixe_facture_fournisseur",
            "pied_de_page",
        ]

    def before_import_row(self, row, row_number=None, **kwargs):
        # Force singleton: always import into pk=1
        row["id"] = 1


class UserProfileResource(resources.ModelResource):
    """
    Export-only resource for user profiles.
    Exposes username, full name, email, role, and phone for HR audits.
    Import is blocked — users must be created through Django admin / auth views.
    """

    username = fields.Field(
        column_name="username",
        attribute="user",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    first_name = fields.Field(
        column_name="first_name",
        attribute="user__first_name",
        readonly=True,
    )
    last_name = fields.Field(
        column_name="last_name",
        attribute="user__last_name",
        readonly=True,
    )
    email = fields.Field(
        column_name="email",
        attribute="user__email",
        readonly=True,
    )
    is_active = fields.Field(
        column_name="is_active",
        attribute="user__is_active",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = UserProfile
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "role",
            "telephone",
            "notes",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "UserProfile import is disabled. "
            "Create users through Django admin → Authentication → Users."
        )
