"""
achats/resources.py

Import-export resources for the supplier procurement cycle.

Import policy per model:
  BLFournisseur        — import supported (brouillon rows only; no overwrite
                          of RECU/FACTURE rows to protect stock integrity).
  BLFournisseurLigne   — import supported (bulk line entry alongside BL).
  FactureFournisseur   — EXPORT ONLY (montant_total auto-derived from BL lines;
                          manual import could bypass BR-FAF-01).
  ReglementFournisseur — EXPORT ONLY (immutable after creation; import could
                          bypass FIFO allocation engine — BR-REG-03 / BR-REG-06).
  AllocationReglement  — EXPORT ONLY (created exclusively by FIFO engine).
  AcompteFournisseur   — EXPORT ONLY (created exclusively by FIFO engine).
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from achats.models import (
    BLFournisseur,
    BLFournisseurLigne,
    FactureFournisseur,
    ReglementFournisseur,
    AllocationReglement,
    AcompteFournisseur,
)
from intrants.models import Fournisseur, Intrant

# ---------------------------------------------------------------------------
# BLFournisseur
# ---------------------------------------------------------------------------


class BLFournisseurResource(resources.ModelResource):
    """
    Import / export of supplier delivery notes.

    Import is limited to brouillon-status BLs.  Rows whose current DB status
    is RECU or FACTURE are rejected to prevent re-triggering stock signals.
    """

    fournisseur = fields.Field(
        column_name="fournisseur_nom",
        attribute="fournisseur",
        widget=ForeignKeyWidget(Fournisseur, field="nom"),
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    # Computed property — export only
    montant_total = fields.Field(
        column_name="montant_total",
        attribute="montant_total",
        readonly=True,
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        attribute="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = BLFournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["reference"]
        exclude = ["piece_jointe"]
        fields = [
            "id",
            "reference",
            "fournisseur",
            "date_bl",
            "reference_fournisseur",
            "statut",
            "notes_reception",
            "a_piece_jointe",
            "montant_total",
            "created_by",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject import of rows that would overwrite a locked BL (RECU/FACTURE).
        """
        ref = row.get("reference", "").strip()
        if ref:
            try:
                existing = BLFournisseur.objects.get(reference=ref)
                if existing.statut in (
                    BLFournisseur.STATUT_RECU,
                    BLFournisseur.STATUT_FACTURE,
                ):
                    raise ValueError(
                        f"Ligne {row_number}: le BL '{ref}' est en statut "
                        f"'{existing.statut}' et ne peut pas être modifié via import."
                    )
            except BLFournisseur.DoesNotExist:
                pass  # New row — allow creation


class BLFournisseurLigneResource(resources.ModelResource):
    """
    Import / export of BL Fournisseur lines.

    `bl` is resolved by BLFournisseur.reference for operator-friendly CSVs.
    Import is rejected if the parent BL is locked.
    """

    bl = fields.Field(
        column_name="bl_reference",
        attribute="bl",
        widget=ForeignKeyWidget(BLFournisseur, field="reference"),
    )
    intrant = fields.Field(
        column_name="intrant_designation",
        attribute="intrant",
        widget=ForeignKeyWidget(Intrant, field="designation"),
    )
    # Computed property — export only
    montant_total = fields.Field(
        column_name="montant_total",
        attribute="montant_total",
        readonly=True,
    )

    class Meta:
        model = BLFournisseurLigne
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "bl",
            "intrant",
            "quantite",
            "prix_unitaire",
            "notes",
            "montant_total",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        """
        Reject lines for locked BLs (same guard as BLFournisseurResource).
        """
        ref = row.get("bl_reference", "").strip()
        if ref:
            try:
                bl = BLFournisseur.objects.get(reference=ref)
                if bl.est_verrouille:
                    raise ValueError(
                        f"Ligne {row_number}: le BL '{ref}' est verrouillé "
                        f"(statut='{bl.statut}'). Impossible d'importer des lignes."
                    )
            except BLFournisseur.DoesNotExist:
                raise ValueError(f"Ligne {row_number}: BL '{ref}' introuvable.")


# ---------------------------------------------------------------------------
# FactureFournisseur — EXPORT ONLY
# ---------------------------------------------------------------------------


class FactureFournisseurResource(resources.ModelResource):
    """
    EXPORT ONLY — supplier invoices.

    montant_total is auto-derived from BL lines (BR-FAF-01); importing
    an invoice bypasses this rule and the BL-locking logic, so import is
    blocked unconditionally.
    """

    fournisseur = fields.Field(
        column_name="fournisseur_nom",
        attribute="fournisseur",
        widget=ForeignKeyWidget(Fournisseur, field="nom"),
        readonly=True,
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    est_en_retard = fields.Field(
        column_name="est_en_retard",
        attribute="est_en_retard",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = FactureFournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["reference"]
        fields = [
            "id",
            "reference",
            "fournisseur",
            "date_facture",
            "date_echeance",
            "type_facture",
            "montant_total",
            "montant_regle",
            "reste_a_payer",
            "statut",
            "est_en_retard",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "FactureFournisseur import est désactivé (BR-FAF-01 / BR-BLF-02). "
            "Créez les factures via l'interface web."
        )


# ---------------------------------------------------------------------------
# ReglementFournisseur — EXPORT ONLY
# ---------------------------------------------------------------------------


class ReglementFournisseurResource(resources.ModelResource):
    """
    EXPORT ONLY — supplier payments.

    Import is disabled: creating a règlement via import bypasses the FIFO
    allocation engine and violates BR-REG-03 / BR-REG-06.
    """

    fournisseur = fields.Field(
        column_name="fournisseur_nom",
        attribute="fournisseur",
        widget=ForeignKeyWidget(Fournisseur, field="nom"),
        readonly=True,
    )
    created_by = fields.Field(
        column_name="created_by_username",
        attribute="created_by",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    class Meta:
        model = ReglementFournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "fournisseur",
            "date_reglement",
            "montant",
            "mode_paiement",
            "reference_paiement",
            "notes",
            "created_by",
            "created_at",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "ReglementFournisseur import est désactivé (BR-REG-03 / BR-REG-06). "
            "Enregistrez les règlements via l'interface web."
        )


# ---------------------------------------------------------------------------
# AllocationReglement — EXPORT ONLY
# ---------------------------------------------------------------------------


class AllocationReglementResource(resources.ModelResource):
    """
    EXPORT ONLY — FIFO allocation lines.
    Audit / accounting export; import is meaningless (records are immutable).
    """

    reglement_id = fields.Field(
        column_name="reglement_id",
        attribute="reglement__id",
        readonly=True,
    )
    facture_reference = fields.Field(
        column_name="facture_reference",
        attribute="facture__reference",
        readonly=True,
    )
    fournisseur_nom = fields.Field(
        column_name="fournisseur_nom",
        attribute="reglement__fournisseur__nom",
        readonly=True,
    )

    class Meta:
        model = AllocationReglement
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "reglement_id",
            "fournisseur_nom",
            "facture_reference",
            "montant_alloue",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "AllocationReglement import est désactivé — enregistrement automatique par le moteur FIFO."
        )


# ---------------------------------------------------------------------------
# AcompteFournisseur — EXPORT ONLY
# ---------------------------------------------------------------------------


class AcompteFournisseurResource(resources.ModelResource):
    """
    EXPORT ONLY — advance payments / supplier credit notes.
    Created exclusively by the FIFO engine; import disabled.
    """

    fournisseur = fields.Field(
        column_name="fournisseur_nom",
        attribute="fournisseur",
        widget=ForeignKeyWidget(Fournisseur, field="nom"),
        readonly=True,
    )
    reglement_id = fields.Field(
        column_name="reglement_source_id",
        attribute="reglement__id",
        readonly=True,
    )
    utilise = fields.Field(
        column_name="utilise",
        attribute="utilise",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = AcompteFournisseur
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "fournisseur",
            "reglement_id",
            "montant",
            "date",
            "utilise",
            "notes",
            "created_at",
        ]
        export_order = fields

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "AcompteFournisseur import est désactivé — enregistrement automatique par le moteur FIFO."
        )
