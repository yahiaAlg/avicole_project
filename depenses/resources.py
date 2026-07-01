"""
depenses/resources.py

Import-export resources for operational expense tracking and the two
special expense families (associés, RH).

Import policy:
  CategorieDepense — import supported (admin maintenance of category list).
  Depense          — import supported for bulk historical entry.
                      The facture_liee FK is constrained on import to
                      service-type invoices only (BR-DEP-03 / BR-DEP-01).
                      v1.4: `branche` is required (BR-BRA-01).
  Associe / RetraitAssocie — import supported for historical withdrawals.
                      v1.4: intentionally WITHOUT branche — equity
                      withdrawals stay company-wide (BR-BRA-08).
  Employe / Pointage / AcompteEmploye — import supported for bulk HR data
                      entry (e.g. migrating an existing attendance sheet).
                      v1.4: each employee's branche is DERIVED from their
                      assigned bâtiment (BR-BRA-09) — exposed read-only on
                      export, never set directly on import.
  CongeEmploye     — no resource: managed via the admin only (paid-leave
                      blocks interact with Pointage via
                      depenses.utils.appliquer_conge_aux_pointages(), which
                      a CSV import would bypass).
  BulletinPaie     — EXPORT ONLY. Payslips must be generated via
                      depenses.utils.calculer_donnees_paie() so the snapshot
                      figures stay consistent with Pointage (BR-RH-05);
                      importing arbitrary payslip rows is disabled.
"""

from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, BooleanWidget

from django.contrib.auth.models import User

from depenses.models import (
    CategorieDepense,
    Depense,
    Associe,
    RetraitAssocie,
    Employe,
    Pointage,
    AcompteEmploye,
    BulletinPaie,
)
from elevage.models import LotElevage
from achats.models import FactureFournisseur
from intrants.models import Batiment
from core.models import Branche

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
      - branche      resolved by core.Branche.code (BR-BRA-01, required)
      - categorie    resolved by CategorieDepense.code
      - lot          resolved by LotElevage.designation (optional; must
                     share the dépense's branche — BR-BRA-01)
      - facture_liee resolved by FactureFournisseur.reference (optional;
                     must be a Service-type invoice — BR-DEP-03; must
                     share the dépense's branche — BR-BRA-01)
      - enregistre_par resolved by User.username (readonly on import)

    File attachments (piece_jointe) are excluded — managed via admin.
    """

    branche = fields.Field(
        column_name="branche_code",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )
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
            "branche",
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
        BR-BRA-01: branche is mandatory, and any linked lot / facture_liee
        must belong to the same branche.
        BR-DEP-01 / BR-DEP-03: reject rows where facture_liee is a
        Marchandises-type invoice.
        """
        branche_code = row.get("branche_code", "").strip()
        if not branche_code:
            raise ValueError(
                f"Ligne {row_number}: le champ 'branche_code' est obligatoire "
                "(BR-BRA-01)."
            )

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
                if facture.branche.code != branche_code:
                    raise ValueError(
                        f"Ligne {row_number}: la facture '{ref}' appartient à une "
                        "autre branche que celle de la dépense (BR-BRA-01)."
                    )
            except FactureFournisseur.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: facture fournisseur '{ref}' introuvable."
                )

        lot_designation = row.get("lot_designation", "").strip()
        if lot_designation:
            try:
                lot = LotElevage.objects.get(designation=lot_designation)
                if lot.branche.code != branche_code:
                    raise ValueError(
                        f"Ligne {row_number}: le lot '{lot_designation}' appartient "
                        "à une autre branche que celle de la dépense (BR-BRA-01)."
                    )
            except LotElevage.DoesNotExist:
                raise ValueError(
                    f"Ligne {row_number}: lot '{lot_designation}' introuvable."
                )

        # Validate categorie code
        code = row.get("categorie_code", "").strip()
        if code and not CategorieDepense.objects.filter(code=code, actif=True).exists():
            raise ValueError(
                f"Ligne {row_number}: catégorie de dépense code='{code}' introuvable ou inactive."
            )


# ---------------------------------------------------------------------------
# Associés
# ---------------------------------------------------------------------------


class AssocieResource(resources.ModelResource):
    class Meta:
        model = Associe
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["nom"]
        fields = [
            "id",
            "nom",
            "telephone",
            "pourcentage_parts",
            "actif",
            "notes",
            "created_at",
        ]
        export_order = fields


class RetraitAssocieResource(resources.ModelResource):
    """
    BR-ASSOC-01: each row is a withdrawal against the stakeholder's
    history; `associe` is resolved by name, never created on the fly.
    """

    associe = fields.Field(
        column_name="associe_nom",
        attribute="associe",
        widget=ForeignKeyWidget(Associe, field="nom"),
    )
    enregistre_par = fields.Field(
        column_name="enregistre_par_username",
        attribute="enregistre_par",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )

    class Meta:
        model = RetraitAssocie
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        exclude = ["piece_jointe"]
        fields = [
            "id",
            "date",
            "associe",
            "montant",
            "mode_paiement",
            "motif",
            "reference_document",
            "notes",
            "enregistre_par",
            "created_at",
        ]
        export_order = fields

    def before_import_row(self, row, row_number=None, **kwargs):
        nom = row.get("associe_nom", "").strip()
        if nom and not Associe.objects.filter(nom=nom, actif=True).exists():
            raise ValueError(
                f"Ligne {row_number}: شريك '{nom}' introuvable ou inactif."
            )


# ---------------------------------------------------------------------------
# RH — Employees
# ---------------------------------------------------------------------------


class EmployeResource(resources.ModelResource):
    batiment = fields.Field(
        column_name="batiment_nom",
        attribute="batiment",
        widget=ForeignKeyWidget(Batiment, field="nom"),
    )
    binome = fields.Field(
        column_name="binome_matricule",
        attribute="binome",
        widget=ForeignKeyWidget(Employe, field="matricule"),
    )
    # v1.4 — derived from batiment.branche (BR-BRA-09), export only.
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )

    class Meta:
        model = Employe
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["matricule"]
        fields = [
            "id",
            "matricule",
            "nom_complet",
            "fonction",
            "telephone",
            "date_embauche",
            "batiment",
            "branche_code",
            "jour_repos_habituel",
            "binome",
            "salaire_base_mensuel",
            "heures_normales_jour",
            "taux_majoration_heure_sup",
            "actif",
            "notes",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""


class PointageResource(resources.ModelResource):
    """
    Bulk import of attendance — typically used once, to migrate an existing
    paper/Excel attendance sheet. `employe` resolved by matricule.
    """

    employe = fields.Field(
        column_name="employe_matricule",
        attribute="employe",
        widget=ForeignKeyWidget(Employe, field="matricule"),
    )
    # v1.4 — derived from employe.branche (BR-BRA-09), export only.
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )

    class Meta:
        model = Pointage
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "employe",
            "branche_code",
            "date",
            "statut",
            "heures_supplementaires",
            "notes",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""

    def before_import_row(self, row, row_number=None, **kwargs):
        statut = row.get("statut", "").strip()
        heures_sup = row.get("heures_supplementaires") or 0
        if (
            statut in (Pointage.STATUT_REPOS, Pointage.STATUT_ABSENT)
            and float(heures_sup) > 0
        ):
            raise ValueError(
                f"Ligne {row_number}: ساعات إضافية غير ممكنة في يوم راحة/غياب."
            )


class AcompteEmployeResource(resources.ModelResource):
    employe = fields.Field(
        column_name="employe_matricule",
        attribute="employe",
        widget=ForeignKeyWidget(Employe, field="matricule"),
    )
    enregistre_par = fields.Field(
        column_name="enregistre_par_username",
        attribute="enregistre_par",
        widget=ForeignKeyWidget(User, field="username"),
        readonly=True,
    )
    # v1.4 — derived from employe.branche (BR-BRA-09), export only.
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )

    class Meta:
        model = AcompteEmploye
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "date",
            "employe",
            "branche_code",
            "montant",
            "mode_paiement",
            "motif",
            "notes",
            "enregistre_par",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""


class BulletinPaieResource(resources.ModelResource):
    """
    Export only (see module docstring) — payslips must be generated through
    depenses.utils.calculer_donnees_paie() to stay consistent with Pointage.
    """

    employe = fields.Field(
        column_name="employe_matricule",
        attribute="employe",
        widget=ForeignKeyWidget(Employe, field="matricule"),
        readonly=True,
    )
    # v1.4 — derived from employe.branche (BR-BRA-09), export only.
    branche_code = fields.Field(
        column_name="branche_code",
        readonly=True,
    )

    class Meta:
        model = BulletinPaie
        skip_unchanged = True
        report_skipped = False
        import_id_fields = ["id"]
        fields = [
            "id",
            "employe",
            "branche_code",
            "annee",
            "mois",
            "jours_presence",
            "jours_absence",
            "jours_repos",
            "jours_conge",
            "total_heures_supplementaires",
            "salaire_base_reference",
            "taux_journalier",
            "montant_heures_sup",
            "montant_brut",
            "total_acomptes",
            "montant_net",
            "statut",
            "date_paiement",
            "mode_paiement",
            "created_at",
        ]
        export_order = fields

    def dehydrate_branche_code(self, obj):
        branche = obj.branche
        return branche.code if branche else ""

    def before_import_row(self, row, row_number=None, **kwargs):
        raise ValueError(
            f"Ligne {row_number}: l'import de BulletinPaie est désactivé — "
            "les bulletins sont générés via le module RH (BR-RH-05)."
        )
