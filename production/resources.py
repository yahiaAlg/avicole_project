"""
production/resources.py

Import-export resources for the production module.

Import policy:
  ProduitFini          — import supported (catalogue maintenance).
  ProductionRecord     — import limited to BROUILLON records only.
                          Importing a VALIDE record would bypass the post_save
                          signal that writes StockProduitFini entries.
  ProductionLigne      — import supported for BROUILLON parent records only.
  CollecteFertilisant  — import supported (raw manure collection log).
  TraitementFertilisant — import limited to BROUILLON batches only, same
                          rationale as ProductionRecord: a VALIDE batch has
                          already credited StockProduitFini via signal.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from production.models import (
    ProduitFini,
    ProductionRecord,
    ProductionLigne,
    CollecteFertilisant,
    TraitementFertilisant,
)
from elevage.models import LotElevage
from intrants.models import Batiment

# ---------------------------------------------------------------------------
# ProduitFini
# ---------------------------------------------------------------------------


class ProduitFiniResource(resources.ModelResource):
    """
    Import / export of the finished-product catalogue.

    quantite_en_stock is a computed property included on export for quick
    stock-level reporting; it is read-only on import.
    """

    quantite_en_stock = fields.Field(
        column_name="quantite_en_stock",
        attribute="quantite_en_stock",
        readonly=True,
    )

    class Meta:
        model = ProduitFini
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "designation",
            "type_produit",
            "unite_mesure",
            "prix_vente_defaut",
            "actif",
            "notes",
            "quantite_en_stock",
            "created_at",
            "updated_at",
        ]
        export_order = fields


# ---------------------------------------------------------------------------
# ProductionRecord
# ---------------------------------------------------------------------------


class ProductionRecordResource(resources.ModelResource):
    """
    Import / export of harvest / production event headers.

    Import is blocked for VALIDE records (stock signals have already fired;
    re-importing would double-count finished-goods stock).

    `lot` is resolved by LotElevage.designation.
    poids_moyen_kg is auto-computed by model.save() — readonly on import.
    """

    lot = fields.Field(
        column_name="lot_designation",
        attribute="lot",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    class Meta:
        model = ProductionRecord
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "lot",
            "date_production",
            "nombre_oiseaux_abattus",
            "poids_total_kg",
            "poids_moyen_kg",  # readonly — auto-computed on save
            "statut",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Block import of VALIDE records to protect stock integrity.
        """
        record_id = row.get("id", "").strip() if row.get("id") else ""
        if record_id:
            try:
                pr = ProductionRecord.objects.get(pk=int(record_id))
                if pr.statut == ProductionRecord.STATUT_VALIDE:
                    raise ValueError(
                        f"Ligne {row_number}: le ProductionRecord id={record_id} est "
                        "déjà validé. Les enregistrements validés ne peuvent pas être "
                        "modifiés via import (intégrité du stock produits finis)."
                    )
            except ProductionRecord.DoesNotExist:
                pass

        # Reject direct import of VALIDE status (must go through view workflow)
        if row.get("statut", "").strip() == ProductionRecord.STATUT_VALIDE:
            raise ValueError(
                f"Ligne {row_number}: impossible de définir statut='valide' via import. "
                "Utilisez l'action 'Valider' dans l'interface web."
            )


# ---------------------------------------------------------------------------
# ProductionLigne
# ---------------------------------------------------------------------------


class ProductionLigneResource(resources.ModelResource):
    """
    Import / export of individual production output lines.

    `production` is resolved by ProductionRecord.id.
    `produit_fini` is resolved by ProduitFini.designation.
    Import is rejected when the parent ProductionRecord is VALIDE.
    """

    production = fields.Field(
        column_name="production_id",
        attribute="production",
        widget=ForeignKeyWidget(ProductionRecord, field="id"),
    )
    produit_fini = fields.Field(
        column_name="produit_fini_designation",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )
    # Computed property — export only
    valeur_totale = fields.Field(
        column_name="valeur_totale_dzd",
        attribute="valeur_totale",
        readonly=True,
    )

    class Meta:
        model = ProductionLigne
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "production",
            "produit_fini",
            "quantite",
            "poids_unitaire_kg",
            "cout_unitaire_estime",
            "notes",
            "valeur_totale",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject lines for validated production records.
        """
        production_id = (
            row.get("production_id", "").strip() if row.get("production_id") else ""
        )
        if production_id:
            try:
                pr = ProductionRecord.objects.get(pk=int(production_id))
                if pr.statut == ProductionRecord.STATUT_VALIDE:
                    raise ValueError(
                        f"Ligne {row_number}: le ProductionRecord id={production_id} est "
                        "validé. Impossible d'importer des lignes de production."
                    )
            except ProductionRecord.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: ProductionRecord id={production_id} introuvable."
                )

        designation = row.get("produit_fini_designation", "").strip()
        if designation:
            try:
                ProduitFini.objects.get(designation=designation)
            except ProduitFini.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: produit fini '{designation}' introuvable."
                )
            except ProduitFini.MultipleObjectsReturned:
                raise ValueError(
                    f"Ligne {row_number}: plusieurs produits finis partagent la désignation "
                    f"'{designation}'. Fournissez 'id' pour lever l'ambiguïté."
                )


# ---------------------------------------------------------------------------
# CollecteFertilisant
# ---------------------------------------------------------------------------


class CollecteFertilisantResource(resources.ModelResource):
    """
    Import / export of raw manure/fertilizer collection events.

    `batiment` is resolved by name. `traitement` is resolved by id — leave
    blank for raw collections not yet assigned to a treatment batch.
    """

    batiment = fields.Field(
        column_name="batiment_nom",
        attribute="batiment",
        widget=ForeignKeyWidget(Batiment, field="nom"),
    )
    traitement = fields.Field(
        column_name="traitement_id",
        attribute="traitement",
        widget=ForeignKeyWidget(TraitementFertilisant, field="id"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    est_traitee = fields.Field(
        column_name="est_traitee",
        attribute="est_traitee",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = CollecteFertilisant
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "batiment",
            "date_collecte",
            "quantite_brute_kg",
            "traitement",
            "est_traitee",
            "notes",
            "created_by",
            "created_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject reassigning a collecte already included in a VALIDE
        traitement (mirrors CollecteFertilisant.clean()).
        """
        collecte_id = row.get("id", "").strip() if row.get("id") else ""
        traitement_id = (
            row.get("traitement_id", "").strip() if row.get("traitement_id") else ""
        )
        if collecte_id and traitement_id:
            try:
                collecte = CollecteFertilisant.objects.get(pk=int(collecte_id))
                if (
                    collecte.traitement_id
                    and str(collecte.traitement_id) != traitement_id
                    and collecte.traitement.statut
                    == TraitementFertilisant.STATUT_VALIDE
                ):
                    raise ValueError(
                        f"Ligne {row_number}: impossible de réaffecter la collecte "
                        f"id={collecte_id} — déjà incluse dans un traitement validé."
                    )
            except CollecteFertilisant.DoesNotExist:
                pass


# ---------------------------------------------------------------------------
# TraitementFertilisant
# ---------------------------------------------------------------------------


class TraitementFertilisantResource(resources.ModelResource):
    """
    Import / export of fertilizer treatment batches.

    Import is blocked for VALIDE batches (stock signal has already fired;
    re-importing would double-count finished-goods stock) — same rationale
    as ProductionRecordResource.

    `produit_fini` is resolved by designation; only products of type
    fertilisant are valid (enforced at model level via limit_choices_to).
    """

    produit_fini = fields.Field(
        column_name="produit_fini_designation",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    # Computed properties — export only
    quantite_brute_totale_kg = fields.Field(
        column_name="quantite_brute_totale_kg",
        attribute="quantite_brute_totale_kg",
        readonly=True,
    )
    rendement_pourcentage = fields.Field(
        column_name="rendement_pourcentage",
        attribute="rendement_pourcentage",
        readonly=True,
    )

    class Meta:
        model = TraitementFertilisant
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "date_traitement",
            "methode",
            "produit_fini",
            "quantite_obtenue_kg",
            "cout_unitaire_estime",
            "quantite_brute_totale_kg",
            "rendement_pourcentage",
            "statut",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Block import/modification of VALIDE batches to protect stock
        integrity (mirrors ProductionRecordResource).
        """
        record_id = row.get("id", "").strip() if row.get("id") else ""
        if record_id:
            try:
                tf = TraitementFertilisant.objects.get(pk=int(record_id))
                if tf.statut == TraitementFertilisant.STATUT_VALIDE:
                    raise ValueError(
                        f"Ligne {row_number}: le TraitementFertilisant id={record_id} est "
                        "déjà validé. Les enregistrements validés ne peuvent pas être "
                        "modifiés via import (intégrité du stock produits finis)."
                    )
            except TraitementFertilisant.DoesNotExist:
                pass

        if row.get("statut", "").strip() == TraitementFertilisant.STATUT_VALIDE:
            raise ValueError(
                f"Ligne {row_number}: impossible de définir statut='valide' via import. "
                "Utilisez l'action 'Valider' dans l'interface web."
            )
