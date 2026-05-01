"""
intrants/resources.py

Import-export resources for master-data tables: categories, supplier types,
suppliers, buildings, and the intrant catalogue.

Import notes:
  - CategorieIntrant / TypeFournisseur: `code` is the natural import key —
    prevents duplicate seed records on re-import.
  - Fournisseur / Intrant: `nom` / `designation` are NOT unique in the DB, so
    import uses `id` as the primary key; operators must supply it for updates.
  - Batiment: uses `nom` as the import key (unique enough for a small farm).
  - IntrantResource resolves `categorie` via CategorieIntrant.code for
    human-friendly CSV headers.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget, BooleanWidget

from intrants.models import (
    CategorieIntrant,
    TypeFournisseur,
    Fournisseur,
    Batiment,
    Intrant,
)

# ---------------------------------------------------------------------------
# CategorieIntrant
# ---------------------------------------------------------------------------


class CategorieIntrantResource(resources.ModelResource):
    """
    Import / export of intrant categories.
    `code` is the natural import key — seeded rows are identified by code,
    not by auto-increment id, so that fixture re-imports are idempotent.
    """

    class Meta:
        model = CategorieIntrant
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["code"]
        fields = [
            "id",
            "code",
            "libelle",
            "consommable_en_lot",
            "ordre",
            "actif",
        ]
        export_order = fields


# ---------------------------------------------------------------------------
# TypeFournisseur
# ---------------------------------------------------------------------------


class TypeFournisseurResource(resources.ModelResource):
    """
    Import / export of supplier types.
    `code` is the natural import key — same rationale as CategorieIntrant.
    """

    class Meta:
        model = TypeFournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["code"]
        fields = [
            "id",
            "code",
            "libelle",
            "ordre",
            "actif",
        ]
        export_order = fields


# ---------------------------------------------------------------------------
# Batiment
# ---------------------------------------------------------------------------


class BatimentResource(resources.ModelResource):
    """
    Import / export of physical farm buildings.
    """

    class Meta:
        model = Batiment
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "nom",
            "capacite",
            "description",
            "actif",
        ]
        export_order = fields


# ---------------------------------------------------------------------------
# Fournisseur
# ---------------------------------------------------------------------------


class FournisseurResource(resources.ModelResource):
    """
    Import / export of supplier master records.

    `type_principal` is resolved by TypeFournisseur.code so the CSV column
    can hold "ALIMENT" / "POUSSINS" etc. rather than an opaque integer id.
    """

    type_principal = fields.Field(
        column_name="type_principal_code",
        attribute="type_principal",
        widget=ForeignKeyWidget(TypeFournisseur, field="code"),
    )

    class Meta:
        model = Fournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "nom",
            "adresse",
            "wilaya",
            "telephone",
            "telephone_2",
            "email",
            "nif",
            "rc",
            "contact_nom",
            "type_principal",
            "actif",
            "notes",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Allow blank type_principal — ForeignKeyWidget returns None for
        empty strings without raising.
        """
        if not row.get("type_principal_code"):
            row["type_principal_code"] = ""


# ---------------------------------------------------------------------------
# Intrant
# ---------------------------------------------------------------------------


class IntrantResource(resources.ModelResource):
    """
    Import / export of the intrant catalogue.

    Foreign key columns use human-readable codes / names for operator
    convenience:
      - categorie   → CategorieIntrant.code  (e.g. "ALIMENT")
      - fournisseurs → comma-separated Fournisseur.nom values (M2M, export only)
    """

    categorie = fields.Field(
        column_name="categorie_code",
        attribute="categorie",
        widget=ForeignKeyWidget(CategorieIntrant, field="code"),
    )

    # M2M — export only; use the admin UI to manage supplier associations
    fournisseurs = fields.Field(
        column_name="fournisseurs_noms",
        attribute="fournisseurs",
        widget=ManyToManyWidget(Fournisseur, separator="|", field="nom"),
        readonly=True,
    )

    # Computed properties — export only for reporting dashboards
    quantite_en_stock = fields.Field(
        column_name="quantite_en_stock",
        attribute="quantite_en_stock",
        readonly=True,
    )
    en_alerte = fields.Field(
        column_name="en_alerte",
        attribute="en_alerte",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = Intrant
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        exclude = []
        fields = [
            "id",
            "designation",
            "categorie",
            "unite_mesure",
            "seuil_alerte",
            "actif",
            "notes",
            "fournisseurs",
            "quantite_en_stock",
            "en_alerte",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject import rows whose categorie_code is not a known seed code.
        This prevents accidentally creating intrants under a wrong category.
        """
        code = row.get("categorie_code", "").strip()
        if code and not CategorieIntrant.objects.filter(code=code).exists():
            raise ValueError(
                f"Ligne {row_number}: categorie_code '{code}' introuvable. "
                f"Codes valides : {list(CategorieIntrant.objects.values_list('code', flat=True))}."
            )
