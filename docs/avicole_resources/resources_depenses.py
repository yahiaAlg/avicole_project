"""
depenses/resources.py

Import-export resources for operational expense tracking.

Import policy:
  CategorieDepense — import supported (admin maintenance of category list).
  Depense          — import supported for bulk historical entry.
                      The facture_liee FK is constrained on import to
                      service-type invoices only (BR-DEP-03 / BR-DEP-01).
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from depenses.models import CategorieDepense, Depense
from elevage.models import LotElevage
from achats.models import FactureFournisseur

# ---------------------------------------------------------------------------
# CategorieDepense
# ---------------------------------------------------------------------------


class CategorieDepenseResource(resources.ModelResource):
    """
    Import / export of expense categories.
    `code` is the natural import key — prevents duplicates on re-import.
    """

    class Meta:
        model = CategorieDepense
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["code"]
        fields = [
            "id",
            "code",
            "libelle",
            "description",
            "actif",
            "ordre",
            "created_at",
        ]
        export_order = fields


# ---------------------------------------------------------------------------
# Depense
# ---------------------------------------------------------------------------


class DepenseResource(resources.ModelResource):
    """
    Import / export of operational expense records.

    FK columns:
      - categorie    resolved by CategorieDepense.code
      - lot          resolved by LotElevage.designation (optional)
      - facture_liee resolved by FactureFournisseur.reference (optional;
                     must be a Service-type invoice — BR-DEP-03)
      - enregistre_par resolved by User.username (readonly on import)

    File attachments (piece_jointe) are excluded — managed via admin.
    """

    categorie = fields.Field(
        column_name="categorie_code",
        attribute="categorie",
        widget=ForeignKeyWidget(CategorieDepense, field="code"),
    )
    lot = fields.Field(
        column_name="lot_designation",
        attribute="lot",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
    )
    facture_liee = fields.Field(
        column_name="facture_liee_reference",
        attribute="facture_liee",
        widget=ForeignKeyWidget(FactureFournisseur, field="reference"),
    )
    enregistre_par = fields.Field(
        column_name="enregistre_par_username",
        attribute="enregistre_par",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        attribute="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = Depense
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        exclude = ["piece_jointe"]
        fields = [
            "id",
            "date",
            "categorie",
            "description",
            "montant",
            "mode_paiement",
            "reference_document",
            "lot",
            "facture_liee",
            "notes",
            "a_piece_jointe",
            "enregistre_par",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        BR-DEP-01 / BR-DEP-03: reject rows where facture_liee is a
        Marchandises-type invoice.
        """
        ref = row.get("facture_liee_reference", "").strip()
        if ref:
            try:
                facture = FactureFournisseur.objects.get(reference=ref)
                if facture.type_facture != FactureFournisseur.TYPE_SERVICE:
                    raise ValueError(
                        f"Ligne {row_number}: la facture '{ref}' est de type "
                        f"'{facture.get_type_facture_display()}'. "
                        "Seules les factures de type 'Service' peuvent être liées "
                        "à une dépense (BR-DEP-01 / BR-DEP-03)."
                    )
            except FactureFournisseur.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: facture fournisseur '{ref}' introuvable."
                )

        # Validate categorie code
        code = row.get("categorie_code", "").strip()
        if code and not CategorieDepense.objects.filter(code=code, actif=True).exists():
            raise ValueError(
                f"Ligne {row_number}: catégorie de dépense code='{code}' introuvable ou inactive."
            )
