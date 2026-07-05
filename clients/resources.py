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
    AbonnementClient,
    VoyageLivraison,
    LivraisonPartielle,
    PrixMarche,
)
from core.models import Branche
from production.models import ProduitFini


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

    class Meta:
        model = BLClientLigne
        fields = (
            "id",
            "bl",
            "produit_fini",
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
            "statut",
            "notes",
            "created_at",
            "updated_at",
        )
        export_order = fields
        import_id_fields = ("id",)
        skip_unchanged = True
        report_skipped = True


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
