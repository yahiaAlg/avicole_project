"""
stock/resources.py

Import-export resources for the stock module.

Import policy:
  StockIntrant      — EXPORT ONLY (balances are maintained exclusively by
                       signals; manual import would desync StockMouvement audit).
  StockProduitFini  — EXPORT ONLY (same rationale).
  StockMouvement    — EXPORT ONLY (immutable audit trail; created by signals).
  StockAjustement   — import supported — this is the intended mechanism for
                       correcting physical-count discrepancies.  Importing
                       triggers the post_save signal which updates the balance.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from stock.models import (
    StockIntrant,
    StockProduitFini,
    StockMouvement,
    StockAjustement,
)
from intrants.models import Intrant
from production.models import ProduitFini

# ---------------------------------------------------------------------------
# StockIntrant — EXPORT ONLY
# ---------------------------------------------------------------------------


class StockIntrantResource(resources.ModelResource):
    """
    EXPORT ONLY — current intrant stock levels with PMP and alert flag.
    Used for inventory reporting and stock-count sheets.
    """

    intrant_designation = fields.Field(
        column_name="intrant_designation",
        attribute="intrant__designation",
        readonly=True,
    )
    intrant_categorie = fields.Field(
        column_name="intrant_categorie",
        attribute="intrant__categorie__libelle",
        readonly=True,
    )
    unite_mesure = fields.Field(
        column_name="unite_mesure",
        attribute="intrant__unite_mesure",
        readonly=True,
    )
    seuil_alerte = fields.Field(
        column_name="seuil_alerte",
        attribute="intrant__seuil_alerte",
        readonly=True,
    )
    en_alerte = fields.Field(
        column_name="en_alerte",
        attribute="en_alerte",
        widget=BooleanWidget(),
        readonly=True,
    )
    valeur_stock_dzd = fields.Field(
        column_name="valeur_stock_dzd",
        attribute="valeur_stock",
        readonly=True,
    )

    class Meta:
        model = StockIntrant
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "intrant_designation",
            "intrant_categorie",
            "unite_mesure",
            "quantite",
            "prix_moyen_pondere",
            "valeur_stock_dzd",
            "seuil_alerte",
            "en_alerte",
            "derniere_mise_a_jour",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "StockIntrant import est désactivé. "
            "Utilisez StockAjustement pour corriger les écarts de stock."
        )


# ---------------------------------------------------------------------------
# StockProduitFini — EXPORT ONLY
# ---------------------------------------------------------------------------


class StockProduitFiniResource(resources.ModelResource):
    """
    EXPORT ONLY — current finished-goods stock levels.
    """

    produit_fini_designation = fields.Field(
        column_name="produit_fini_designation",
        attribute="produit_fini__designation",
        readonly=True,
    )
    type_produit = fields.Field(
        column_name="type_produit",
        attribute="produit_fini__type_produit",
        readonly=True,
    )
    unite_mesure = fields.Field(
        column_name="unite_mesure",
        attribute="produit_fini__unite_mesure",
        readonly=True,
    )
    en_alerte = fields.Field(
        column_name="en_alerte",
        attribute="en_alerte",
        widget=BooleanWidget(),
        readonly=True,
    )
    valeur_stock_dzd = fields.Field(
        column_name="valeur_stock_dzd",
        attribute="valeur_stock",
        readonly=True,
    )

    class Meta:
        model = StockProduitFini
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "produit_fini_designation",
            "type_produit",
            "unite_mesure",
            "quantite",
            "cout_moyen_production",
            "valeur_stock_dzd",
            "seuil_alerte",
            "en_alerte",
            "derniere_mise_a_jour",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "StockProduitFini import est désactivé. "
            "Utilisez StockAjustement pour corriger les écarts de stock."
        )


# ---------------------------------------------------------------------------
# StockMouvement — EXPORT ONLY
# ---------------------------------------------------------------------------


class StockMouvementResource(resources.ModelResource):
    """
    EXPORT ONLY — full stock movement audit trail.
    Both stock segments are covered in a single resource; only one of
    (intrant_designation / produit_fini_designation) is populated per row.
    """

    intrant_designation = fields.Field(
        column_name="intrant_designation",
        attribute="intrant__designation",
        readonly=True,
    )
    produit_fini_designation = fields.Field(
        column_name="produit_fini_designation",
        attribute="produit_fini__designation",
        readonly=True,
    )
    created_by_username = fields.Field(
        column_name="created_by_username",
        attribute="created_by__username",
        readonly=True,
    )

    class Meta:
        model = StockMouvement
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "intrant_designation",
            "produit_fini_designation",
            "type_mouvement",
            "source",
            "quantite",
            "quantite_avant",
            "quantite_apres",
            "date_mouvement",
            "reference_id",
            "reference_label",
            "notes",
            "created_by_username",
            "created_at",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "StockMouvement import est désactivé — journal d'audit immuable."
        )


# ---------------------------------------------------------------------------
# StockAjustement — import supported
# ---------------------------------------------------------------------------


class StockAjustementResource(resources.ModelResource):
    """
    Import / export of manual stock adjustments.

    Importing a StockAjustement row triggers the post_save signal which:
      1. Overwrites StockIntrant.quantite or StockProduitFini.quantite with
         quantite_apres (physical count is authoritative).
      2. Creates a StockMouvement (AJUSTEMENT) for audit.

    Exactly one of (intrant / produit_fini) must be supplied per row,
    matching the segment value.
    """

    intrant = fields.Field(
        column_name="intrant_designation",
        attribute="intrant",
        widget=ForeignKeyWidget(Intrant, field="designation"),
    )
    produit_fini = fields.Field(
        column_name="produit_fini_designation",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )
    effectue_par = fields.Field(
        column_name="effectue_par_username",
        attribute="effectue_par",
        widget=ForeignKeyWidget(User, field="username"),
    )

    class Meta:
        model = StockAjustement
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "segment",
            "intrant",
            "produit_fini",
            "date_ajustement",
            "quantite_avant",
            "quantite_apres",
            "raison",
            "effectue_par",
            "created_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Validate that segment, intrant/produit_fini, and raison are consistent.
        """
        segment = row.get("segment", "").strip()
        intrant_col = row.get("intrant_designation", "").strip()
        produit_col = row.get("produit_fini_designation", "").strip()
        raison = row.get("raison", "").strip()

        if not raison:
            raise ValueError(
                f"Ligne {row_number}: le champ 'raison' est obligatoire pour "
                "un ajustement de stock."
            )

        if segment == StockAjustement.SEGMENT_INTRANT:
            if not intrant_col:
                raise ValueError(
                    f"Ligne {row_number}: segment=intrant mais intrant_designation est vide."
                )
            if produit_col:
                raise ValueError(
                    f"Ligne {row_number}: segment=intrant — ne pas fournir produit_fini_designation."
                )
            try:
                Intrant.objects.get(designation=intrant_col)
            except Intrant.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: intrant '{intrant_col}' introuvable."
                )
            except Intrant.MultipleObjectsReturned:
                raise ValueError(
                    f"Ligne {row_number}: désignation '{intrant_col}' ambiguë. "
                    "Fournissez l'id de l'intrant."
                )

        elif segment == StockAjustement.SEGMENT_PRODUIT_FINI:
            if not produit_col:
                raise ValueError(
                    f"Ligne {row_number}: segment=produit_fini mais produit_fini_designation est vide."
                )
            if intrant_col:
                raise ValueError(
                    f"Ligne {row_number}: segment=produit_fini — ne pas fournir intrant_designation."
                )
            try:
                ProduitFini.objects.get(designation=produit_col)
            except ProduitFini.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: produit fini '{produit_col}' introuvable."
                )
            except ProduitFini.MultipleObjectsReturned:
                raise ValueError(
                    f"Ligne {row_number}: désignation '{produit_col}' ambiguë."
                )

        else:
            raise ValueError(
                f"Ligne {row_number}: segment '{segment}' invalide. "
                f"Valeurs acceptées : '{StockAjustement.SEGMENT_INTRANT}', "
                f"'{StockAjustement.SEGMENT_PRODUIT_FINI}'."
            )
