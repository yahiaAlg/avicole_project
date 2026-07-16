# clients/resources.py
# django-import-export ModelResource definitions for the clients application.
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, ManyToManyWidget, BooleanWidget

from clients.models import (
    TypeClient,
    Client,
    BLClient,
    BLClientLigne,
    FactureClient,
    PaiementClient,
    PaiementClientAllocation,
    AcompteClient,
    AllocationAcompteClient,
    AbonnementClient,
    VoyageLivraison,
    LivraisonPartielle,
    PrixMarche,
)
from core.models import Branche
from production.models import ProduitFini
from intrants.models import Intrant


class TypeClientResource(resources.ModelResource):
    class Meta:
        model = TypeClient
        fields = ("id", "code", "libelle", "ordre", "actif")
        export_order = fields
        import_id_fields = ("code",)
        skip_unchanged = True
        report_skipped = True


class ClientResource(resources.ModelResource):
    type_client = fields.Field(
        column_name="type_client_code",
        attribute="type_client",
        widget=ForeignKeyWidget(TypeClient, field="code"),
    )

    class Meta:
        model = Client
        fields = (
            "id",
            "nom",
            "type_client",
            "adresse",
            "wilaya",
            "telephone",
            "telephone_2",
            "email",
            "nif",
            "rc",
            "contact_nom",
            "plafond_credit",
            "actif",
            "notes",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class BLClientResource(resources.ModelResource):
    branche = fields.Field(
        column_name="branche",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )
    client = fields.Field(
        column_name="client",
        attribute="client",
        widget=ForeignKeyWidget(Client, field="nom"),
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        attribute="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = BLClient
        fields = (
            "id",
            "reference",
            "branche",
            "client",
            "date_bl",
            "adresse_livraison",
            "statut",
            "signe_par",
            "notes",
            "a_piece_jointe",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("reference",)
        skip_unchanged = True
        report_skipped = True


class BLClientLigneResource(resources.ModelResource):
    bl = fields.Field(
        column_name="bl_reference",
        attribute="bl",
        widget=ForeignKeyWidget(BLClient, field="reference"),
    )
    produit_fini = fields.Field(
        column_name="produit_fini",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )
    # v1.6 — BR-BLC-06: a line sells EITHER produit_fini OR a surplus
    # intrant, never both (enforced by BLClientLigne.clean()).
    intrant = fields.Field(
        column_name="intrant",
        attribute="intrant",
        widget=ForeignKeyWidget(Intrant, field="designation"),
    )

    class Meta:
        model = BLClientLigne
        fields = (
            "id",
            "bl",
            "produit_fini",
            "intrant",
            "quantite",
            "prix_unitaire",
            "notes",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class FactureClientResource(resources.ModelResource):
    branche = fields.Field(
        column_name="branche",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )
    client = fields.Field(
        column_name="client",
        attribute="client",
        widget=ForeignKeyWidget(Client, field="nom"),
    )
    bls = fields.Field(
        column_name="bls",
        attribute="bls",
        widget=ManyToManyWidget(BLClient, field="reference", separator=","),
    )
    # v1.6 — BR-ABO-03: alternate invoice source for a forfait
    # AbonnementClient due, bypassing the BL-driven montant_ht computation.
    # A facture has EITHER bls OR abonnement — never both. Export only;
    # populated exclusively by clients.utils.generer_facture_abonnement.
    abonnement = fields.Field(
        column_name="abonnement_id",
        attribute="abonnement",
        widget=ForeignKeyWidget(AbonnementClient, field="id"),
        readonly=True,
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = FactureClient
        fields = (
            "id",
            "reference",
            "branche",
            "client",
            "bls",
            "abonnement",
            "periode_debut",
            "periode_fin",
            "date_facture",
            "date_echeance",
            "montant_ht",
            "taux_tva",
            "montant_tva",
            "montant_ttc",
            "montant_regle",
            "reste_a_payer",
            "statut",
            "notes",
            "a_piece_jointe",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("reference",)
        skip_unchanged = True
        report_skipped = True

    def dehydrate_a_piece_jointe(self, obj):
        return obj.pieces_jointes.exists()


class PaiementClientResource(resources.ModelResource):
    client = fields.Field(
        column_name="client",
        attribute="client",
        widget=ForeignKeyWidget(Client, field="nom"),
    )
    branche = fields.Field(
        column_name="branche",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = PaiementClient
        fields = (
            "id",
            "client",
            "branche",
            "date_paiement",
            "montant",
            "mode_paiement",
            "reference_paiement",
            "notes",
            "a_piece_jointe",
            "created_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True

    def dehydrate_a_piece_jointe(self, obj):
        return obj.pieces_jointes.exists()


class PaiementClientAllocationResource(resources.ModelResource):
    paiement = fields.Field(
        column_name="paiement_id",
        attribute="paiement",
        widget=ForeignKeyWidget(PaiementClient, field="id"),
    )
    facture = fields.Field(
        column_name="facture_reference",
        attribute="facture",
        widget=ForeignKeyWidget(FactureClient, field="reference"),
    )

    class Meta:
        model = PaiementClientAllocation
        fields = ("id", "paiement", "facture", "montant_alloue")
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class AbonnementClientResource(resources.ModelResource):
    client = fields.Field(
        column_name="client",
        attribute="client",
        widget=ForeignKeyWidget(Client, field="nom"),
    )
    branche = fields.Field(
        column_name="branche",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
    )
    produit_fini = fields.Field(
        column_name="produit_fini",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )

    class Meta:
        model = AbonnementClient
        fields = (
            "id",
            "client",
            "branche",
            "produit_fini",
            "date_debut",
            "date_fin",
            "frequence",
            "quantite_totale_prevue",
            "prix_unitaire",
            "mode_facturation",
            "montant_forfait",
            "mode_paiement",
            "statut",
            "notes",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class AcompteClientResource(resources.ModelResource):
    """
    EXPORT ONLY — client prepayments / overpayment surplus, mirroring
    AcompteFournisseurResource on the supplier side. Created automatically
    right after a PaiementClient's allocations are applied; import is
    disabled since these rows are never created directly by a user.
    """

    client = fields.Field(
        column_name="client",
        attribute="client",
        widget=ForeignKeyWidget(Client, field="nom"),
        readonly=True,
    )
    branche = fields.Field(
        column_name="branche_code",
        attribute="branche",
        widget=ForeignKeyWidget(Branche, field="code"),
        readonly=True,
    )
    paiement_id = fields.Field(
        column_name="paiement_source_id",
        attribute="paiement__id",
        readonly=True,
    )
    utilise = fields.Field(
        column_name="utilise",
        attribute="utilise",
        widget=BooleanWidget(),
        readonly=True,
    )
    a_piece_jointe = fields.Field(
        column_name="a_piece_jointe",
        widget=BooleanWidget(),
        readonly=True,
    )

    class Meta:
        model = AcompteClient
        fields = (
            "id",
            "branche",
            "client",
            "paiement_id",
            "montant",
            "montant_restant",
            "date",
            "utilise",
            "a_piece_jointe",
            "notes",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True

    def dehydrate_a_piece_jointe(self, obj):
        return obj.pieces_jointes.exists()

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "AcompteClient import est désactivé — enregistrement automatique "
            "après imputation d'un PaiementClient (BR-FAC-03)."
        )


class AllocationAcompteClientResource(resources.ModelResource):
    """
    EXPORT ONLY — portion of an AcompteClient consumed against a
    FactureClient. Created exclusively by
    clients.utils.consommer_acomptes_client_fifo; immutable afterwards.
    """

    acompte_id = fields.Field(
        column_name="acompte_id",
        attribute="acompte__id",
        readonly=True,
    )
    facture_reference = fields.Field(
        column_name="facture_reference",
        attribute="facture__reference",
        readonly=True,
    )
    client_nom = fields.Field(
        column_name="client_nom",
        attribute="acompte__client__nom",
        readonly=True,
    )
    branche_code = fields.Field(
        column_name="branche_code",
        attribute="acompte__branche__code",
        readonly=True,
    )

    class Meta:
        model = AllocationAcompteClient
        fields = (
            "id",
            "acompte_id",
            "branche_code",
            "client_nom",
            "facture_reference",
            "montant_alloue",
            "created_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True

    def before_import(self, dataset, **kwargs):
        raise NotImplementedError(
            "AllocationAcompteClient import est désactivé — enregistrement "
            "automatique par le moteur de consommation des avances client."
        )


class VoyageLivraisonResource(resources.ModelResource):
    class Meta:
        model = VoyageLivraison
        fields = (
            "id",
            "date_voyage",
            "chauffeur",
            "vehicule",
            "notes",
            "created_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class LivraisonPartielleResource(resources.ModelResource):
    abonnement = fields.Field(
        column_name="abonnement_id",
        attribute="abonnement",
        widget=ForeignKeyWidget(AbonnementClient, field="id"),
    )
    voyage = fields.Field(
        column_name="voyage_id",
        attribute="voyage",
        widget=ForeignKeyWidget(VoyageLivraison, field="id"),
    )

    class Meta:
        model = LivraisonPartielle
        fields = (
            "id",
            "abonnement",
            "voyage",
            "date",
            "quantite_livree",
            "notes",
            "created_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


class PrixMarcheResource(resources.ModelResource):
    produit_fini = fields.Field(
        column_name="produit_fini",
        attribute="produit_fini",
        widget=ForeignKeyWidget(ProduitFini, field="designation"),
    )

    class Meta:
        model = PrixMarche
        fields = (
            "id",
            "produit_fini",
            "date",
            "prix_marche",
            "source",
            "notes",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("produit_fini", "date")
        skip_unchanged = True
        report_skipped = True
