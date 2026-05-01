"""
elevage/resources.py

Import-export resources for the poultry raising module.

Import policy:
  LotElevage   — import supported for OUVERT lots only; FERME lots are
                  locked (closing a lot has stock/financial implications
                  that cannot be safely replayed via CSV).
  Mortalite    — import supported (bulk historical entry); open lots only.
  Consommation — import supported (bulk historical entry); open lots only.
                  Warning: importing Consommation rows triggers the post_save
                  signal which will deduct from StockIntrant — ensure stock
                  records are correct before bulk importing.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from elevage.models import LotElevage, Mortalite, Consommation
from intrants.models import Fournisseur, Batiment, Intrant
from achats.models import BLFournisseur

# ---------------------------------------------------------------------------
# LotElevage
# ---------------------------------------------------------------------------


class LotElevageResource(resources.ModelResource):
    """
    Import / export of poultry batches (lots).

    FK columns use human-readable names / references rather than integer IDs.
    Computed KPIs (effectif_vivant, taux_mortalite, etc.) are included on
    export for reporting dashboards.
    """

    fournisseur_poussins = fields.Field(
        column_name="fournisseur_poussins_nom",
        attribute="fournisseur_poussins",
        widget=ForeignKeyWidget(Fournisseur, field="nom"),
    )
    batiment = fields.Field(
        column_name="batiment_nom",
        attribute="batiment",
        widget=ForeignKeyWidget(Batiment, field="nom"),
    )
    bl_fournisseur_poussins = fields.Field(
        column_name="bl_poussins_reference",
        attribute="bl_fournisseur_poussins",
        widget=ForeignKeyWidget(BLFournisseur, field="reference"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    # Computed KPIs — export only
    total_mortalite = fields.Field(
        column_name="total_mortalite",
        attribute="total_mortalite",
        readonly=True,
    )
    effectif_vivant = fields.Field(
        column_name="effectif_vivant",
        attribute="effectif_vivant",
        readonly=True,
    )
    taux_mortalite_pct = fields.Field(
        column_name="taux_mortalite_pct",
        attribute="taux_mortalite",
        readonly=True,
    )
    duree_elevage_jours = fields.Field(
        column_name="duree_elevage_jours",
        attribute="duree_elevage",
        readonly=True,
    )
    consommation_totale_aliment = fields.Field(
        column_name="consommation_totale_aliment",
        attribute="consommation_totale_aliment",
        readonly=True,
    )
    cout_total_intrants = fields.Field(
        column_name="cout_total_intrants_dzd",
        attribute="cout_total_intrants",
        readonly=True,
    )

    class Meta:
        model = LotElevage
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "designation",
            "date_ouverture",
            "date_fermeture",
            "statut",
            "nombre_poussins_initial",
            "fournisseur_poussins",
            "bl_fournisseur_poussins",
            "batiment",
            "souche",
            "notes",
            "total_mortalite",
            "effectif_vivant",
            "taux_mortalite_pct",
            "duree_elevage_jours",
            "consommation_totale_aliment",
            "cout_total_intrants",
            "created_by",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject rows that attempt to import/overwrite a FERME lot.
        """
        lot_id = row.get("id", "").strip() if row.get("id") else ""
        if lot_id:
            try:
                lot = LotElevage.objects.get(pk=int(lot_id))
                if lot.statut == LotElevage.STATUT_FERME:
                    raise ValueError(
                        f"Ligne {row_number}: le lot id={lot_id} est fermé "
                        "et ne peut pas être modifié via import."
                    )
            except LotElevage.DoesNotExist:
                pass  # New lot — allow

        # Reject if importing a 'ferme' status directly (would skip the
        # lot.fermer() workflow which captures closure date properly)
        if row.get("statut", "").strip() == LotElevage.STATUT_FERME:
            raise ValueError(
                f"Ligne {row_number}: impossible de définir statut='ferme' via import. "
                "Utilisez l'action 'Fermer le lot' dans l'interface."
            )


# ---------------------------------------------------------------------------
# Mortalite
# ---------------------------------------------------------------------------


class MortaliteResource(resources.ModelResource):
    """
    Import / export of daily mortality records.

    `lot` is resolved by LotElevage designation for operator convenience.
    Bulk import is useful for entering historical records.
    """

    lot = fields.Field(
        column_name="lot_designation",
        attribute="lot",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
    )

    class Meta:
        model = Mortalite
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "lot",
            "date",
            "nombre",
            "cause",
            "notes",
            "created_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject mortality records for closed lots.
        """
        designation = row.get("lot_designation", "").strip()
        if designation:
            try:
                lot = LotElevage.objects.get(designation=designation)
                if lot.statut == LotElevage.STATUT_FERME:
                    raise ValueError(
                        f"Ligne {row_number}: impossible d'importer une mortalité "
                        f"sur le lot fermé '{designation}'."
                    )
            except LotElevage.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: lot '{designation}' introuvable."
                )


# ---------------------------------------------------------------------------
# Consommation
# ---------------------------------------------------------------------------


class ConsommationResource(resources.ModelResource):
    """
    Import / export of input consumption events.

    IMPORTANT: importing Consommation rows fires the post_save signal which
    immediately deducts from StockIntrant.  Only import after confirming that
    the stock records reflect the correct pre-import balance.

    `intrant` is resolved by Intrant.designation; if multiple intrants share
    the same designation, use IntrantResource to obtain and supply the `id`
    column instead.
    """

    lot = fields.Field(
        column_name="lot_designation",
        attribute="lot",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
    )
    intrant = fields.Field(
        column_name="intrant_designation",
        attribute="intrant",
        widget=ForeignKeyWidget(Intrant, field="designation"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    class Meta:
        model = Consommation
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "lot",
            "date",
            "intrant",
            "quantite",
            "notes",
            "created_by",
            "created_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Guard: reject consommation on closed lots.
        Guard: reject intrants whose category is not consommable_en_lot.
        """
        designation_lot = row.get("lot_designation", "").strip()
        if designation_lot:
            try:
                lot = LotElevage.objects.get(designation=designation_lot)
                if lot.statut == LotElevage.STATUT_FERME:
                    raise ValueError(
                        f"Ligne {row_number}: impossible d'importer une consommation "
                        f"sur le lot fermé '{designation_lot}'."
                    )
            except LotElevage.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: lot '{designation_lot}' introuvable."
                )

        designation_intrant = row.get("intrant_designation", "").strip()
        if designation_intrant:
            try:
                intrant = Intrant.objects.select_related("categorie").get(
                    designation=designation_intrant
                )
                if not intrant.categorie.consommable_en_lot:
                    raise ValueError(
                        f"Ligne {row_number}: l'intrant '{designation_intrant}' "
                        f"appartient à la catégorie '{intrant.categorie.libelle}' "
                        "qui n'est pas consommable en lot."
                    )
            except Intrant.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: intrant '{designation_intrant}' introuvable."
                )
            except Intrant.MultipleObjectsReturned:
                raise ValueError(
                    f"Ligne {row_number}: plusieurs intrants partagent la désignation "
                    f"'{designation_intrant}'. Fournissez la colonne 'id' pour lever l'ambiguïté."
                )
