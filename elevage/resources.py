"""
elevage/resources.py

Import-export resources for the poultry raising module.

Import policy:
  LotElevage      — import supported for OUVERT lots only; FERME lots are
                     locked (closing a lot has stock/financial implications
                     that cannot be safely replayed via CSV). v1.4: `branche`
                     is denormalized read-only (always mirrors
                     batiment.branche — BR-BRA-01); never set on import.
  Mortalite       — import supported (bulk historical entry); open lots only.
  Consommation    — import supported (bulk historical entry); open lots only.
                     Warning: importing Consommation rows triggers the post_save
                     signal which will deduct from StockIntrant — ensure stock
                     records are correct before bulk importing.
                     `prix_unitaire` (médicament/vaccin costing, optional) is
                     importable; `depense_paiement` (BR-request batched
                     team/vet payment link) is export-only — it is only ever
                     set via the "دفع أجرة" batching workflow.
  ParametrageElevage — no resource: singleton row managed via the admin only.

v1.4 — TransfertLot, PeseeEchantillon, and RecolteOeufs no longer have an
import-export resource here. Admin registration for these three models
dropped ImportExportModelAdmin in favor of the standard web workflow:
  - TransfertLot.clean() now also enforces that origin/destination
    bâtiments share the same branche as the lot (BR-BRA-01), a guard that
    is awkward to replay safely from a flat CSV row-by-row import.
  - PeseeEchantillon / RecolteOeufs derive `branche` from their parent lot
    (read-only, not stored) — purely informational here, so bulk import of
    these stays a manual/admin-only workflow going forward.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from elevage.models import (
    LotElevage,
    Mortalite,
    Consommation,
)
from intrants.models import Fournisseur, Batiment, Intrant
from achats.models import BLFournisseur
from depenses.models import Depense
from core.models import Branche

# ---------------------------------------------------------------------------
# LotElevage
# ---------------------------------------------------------------------------


class LotElevageResource(resources.ModelResource):
    """
    Import / export of poultry batches (lots).

    FK columns use human-readable names / references rather than integer IDs.
    Computed KPIs (effectif_vivant, taux_mortalite, etc.) are included on
    export for reporting dashboards.

    `branche` (v1.4) always mirrors batiment.branche (BR-BRA-01) — readonly,
    auto-synced by model.save(), never set on import.
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
    branche = fields.Field(
        column_name="branche_code",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
        readonly=True,
    )
    bl_fournisseur_poussins = fields.Field(
        column_name="bl_poussins_reference",
        attribute="bl_fournisseur_poussins",
        widget=ForeignKeyWidget(BLFournisseur, field="reference"),
    )
    # Lineage — set automatically when this lot is created by a SPLIT_NEW
    # TransfertLot; export only (self-referential, resolved by designation).
    lot_parent = fields.Field(
        column_name="lot_parent_designation",
        attribute="lot_parent",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
        readonly=True,
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
            "branche",
            "souche",
            "lot_parent",
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
    `branche_code` (v1.4) is derived from lot.branche (BR-BRA-01), export only.
    """

    lot = fields.Field(
        column_name="lot_designation",
        attribute="lot",
        widget=ForeignKeyWidget(LotElevage, field="designation"),
    )
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )

    class Meta:
        model = Mortalite
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "lot",
            "branche_code",
            "date",
            "nombre",
            "cause",
            "notes",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""

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
    `branche_code` (v1.4) is derived from lot.branche (BR-BRA-01), export only.
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
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )
    # Costing/payment tracking (médicament/vaccin only — BR-request).
    depense_paiement = fields.Field(
        column_name="depense_paiement_id",
        attribute="depense_paiement",
        widget=ForeignKeyWidget(Depense, field="id"),
        readonly=True,
    )
    est_paye = fields.Field(
        column_name="est_paye",
        attribute="est_paye",
        widget=BooleanWidget(),
        readonly=True,
    )
    montant_total = fields.Field(
        column_name="montant_total",
        attribute="montant_total",
        readonly=True,
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
            "branche_code",
            "date",
            "intrant",
            "quantite",
            "prix_unitaire",
            "montant_total",
            "depense_paiement",
            "est_paye",
            "notes",
            "created_by",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""

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
