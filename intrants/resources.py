"""
intrants/resources.py

Import-export resources for master-data tables: categories, supplier types,
suppliers, buildings, and the intrant catalogue.

Import notes:
  - CategorieIntrant / TypeFournisseur: `code` is the natural import key —
    prevents duplicate seed records on re-import.
  - CategorieQualite: `code` + `type_pesee` together are the natural import
    key (code alone is not unique — see model unique_together).
  - Fournisseur / Intrant: `nom` / `designation` are NOT unique in the DB, so
    import uses `id` as the primary key; operators must supply it for updates.
  - Batiment: uses `id` as the import key; `categorie_stockage` is only
    meaningful when `type_batiment` = entrepot (enforced by model.clean()).
    v1.4: `branche` is required (BR-BRA-01) and resolved via Branche.code.
  - IntrantResource resolves `categorie` via CategorieIntrant.code and
    `unite_mesure` via UniteMesure.code for human-friendly CSV headers.
    v1.4: StockIntrant is now one row per
    (branche, intrant) pair (BR-BRA-07), so `quantite_en_stock`/`en_alerte`
    became methods on the model (optional `branche` arg) instead of
    properties; exported here via dehydrate as the Vue Globale total
    (no branche argument = summed across all branches).
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget, BooleanWidget

from intrants.models import (
    CategorieIntrant,
    TypeFournisseur,
    UniteMesure,
    CategorieQualite,
    Fournisseur,
    Batiment,
    Intrant,
)
from core.models import Branche

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
# UniteMesure
# ---------------------------------------------------------------------------


class UniteMesureResource(resources.ModelResource):
    """
    Import / export of the shared unit-of-measure master table.
    `code` is the natural import key — same rationale as CategorieIntrant.
    """

    class Meta:
        model = UniteMesure
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
# CategorieQualite
# ---------------------------------------------------------------------------


class CategorieQualiteResource(resources.ModelResource):
    """
    Import / export of quality-grading brackets (oiseaux / oeufs scales).
    `code` is unique only per `type_pesee` (see Meta.unique_together on the
    model), so both columns together form the natural import key.
    """

    class Meta:
        model = CategorieQualite
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["code", "type_pesee"]
        fields = [
            "id",
            "code",
            "libelle",
            "type_pesee",
            "poids_min",
            "poids_max",
            "ordre",
            "actif",
        ]
        export_order = fields


class BatimentResource(resources.ModelResource):
    """
    Import / export of physical farm buildings.

    `branche` (v1.4) is required (BR-BRA-01) and resolved by Branche.code.
    """

    branche = fields.Field(
        column_name="branche_code",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )

    class Meta:
        model = Batiment
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "nom",
            "branche",
            "type_batiment",
            "categorie_stockage",
            "capacite",
            "description",
            "actif",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        BR-BRA-01: branche is mandatory — every bâtiment belongs to
        exactly one branche.
        """
        if not row.get("branche_code", "").strip():
            raise ValueError(
                f"Ligne {row_number}: le champ 'branche_code' est obligatoire "
                "(BR-BRA-01)."
            )


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
    unite_mesure = fields.Field(
        column_name="unite_mesure_code",
        attribute="unite_mesure",
        widget=ForeignKeyWidget(UniteMesure, field="code"),
    )

    # M2M — export only; use the admin UI to manage supplier associations
    fournisseurs = fields.Field(
        column_name="fournisseurs_noms",
        attribute="fournisseurs",
        widget=ManyToManyWidget(Fournisseur, separator="|", field="nom"),
        readonly=True,
    )

    # v1.4 — StockIntrant is now one row per (branche, intrant) pair
    # (BR-BRA-07); quantite_en_stock/en_alerte became METHODS on the model
    # (optional `branche` arg), not properties, so they're dehydrated below
    # rather than read via `attribute=`. Calling them with no argument
    # returns the Vue Globale total across every branche.
    quantite_en_stock = fields.Field(
        column_name="quantite_en_stock",
        readonly=True,
    )
    en_alerte = fields.Field(
        column_name="en_alerte",
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
            "stade",
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

    def dehydrate_quantite_en_stock(self, obj):
        return obj.quantite_en_stock()

    def dehydrate_en_alerte(self, obj):
        return obj.en_alerte()

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
