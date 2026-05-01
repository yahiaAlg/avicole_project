"""
intrants/models.py

Master-data for the farm:
  - CategorieIntrant   : dynamic intrant categories (seeded via data migration)
  - TypeFournisseur    : dynamic supplier types    (seeded via data migration)
  - Fournisseur        : supplier records
  - Batiment           : physical poultry buildings
  - Intrant            : input-goods catalogue
"""

from django.db import models

# ---------------------------------------------------------------------------
# Dynamic category tables (replace hardcoded choice tuples)
# ---------------------------------------------------------------------------


class CategorieIntrant(models.Model):
    """
    User-manageable categories for input goods.
    Seeded: Aliment, Poussin, Médicament/Vétérinaire, Autre.

    The `code` field is the stable programmatic key used in business-logic
    guards (e.g. Consommation is restricted to aliment/medicament categories).
    Administrators may add categories but should NOT rename the four seed codes.
    """

    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Code",
        help_text="Clé stable : ALIMENT, POUSSIN, MEDICAMENT, AUTRE — ne pas renommer.",
    )
    libelle = models.CharField(max_length=150, verbose_name="Libellé")
    # Whether items in this category are consumable inside a lot (feed/medicine)
    consommable_en_lot = models.BooleanField(
        default=False,
        verbose_name="Consommable en lot",
        help_text="Cocher pour les catégories pouvant être saisies en Consommation.",
    )
    ordre = models.PositiveSmallIntegerField(
        default=0, verbose_name="Ordre d'affichage"
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")

    class Meta:
        verbose_name = "Catégorie d'intrant"
        verbose_name_plural = "Catégories d'intrants"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class TypeFournisseur(models.Model):
    """
    User-manageable supplier types.
    Seeded: Aliments, Poussins, Médicaments/Vétérinaires, Services, Autre.
    """

    code = models.CharField(max_length=50, unique=True, verbose_name="Code")
    libelle = models.CharField(max_length=150, verbose_name="Libellé")
    ordre = models.PositiveSmallIntegerField(
        default=0, verbose_name="Ordre d'affichage"
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")

    class Meta:
        verbose_name = "Type de fournisseur"
        verbose_name_plural = "Types de fournisseurs"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


# ---------------------------------------------------------------------------
# Fournisseur
# ---------------------------------------------------------------------------


class Fournisseur(models.Model):
    """
    Supplier master record.  Referenced by BL Fournisseur, Facture Fournisseur,
    Règlement Fournisseur, and the Intrant catalogue.
    """

    nom = models.CharField(max_length=255, verbose_name="Nom du fournisseur")
    adresse = models.TextField(verbose_name="Adresse", blank=True)
    wilaya = models.CharField(max_length=100, verbose_name="Wilaya", blank=True)
    telephone = models.CharField(max_length=30, verbose_name="Téléphone", blank=True)
    telephone_2 = models.CharField(
        max_length=30, verbose_name="Téléphone 2", blank=True
    )
    email = models.EmailField(verbose_name="Email", blank=True)
    nif = models.CharField(max_length=50, verbose_name="NIF", blank=True)
    rc = models.CharField(max_length=50, verbose_name="RC", blank=True)
    contact_nom = models.CharField(
        max_length=150, verbose_name="Nom du contact", blank=True
    )
    type_principal = models.ForeignKey(
        TypeFournisseur,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fournisseurs",
        verbose_name="Type principal",
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")
    notes = models.TextField(verbose_name="Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Fournisseur"
        verbose_name_plural = "Fournisseurs"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    # ------------------------------------------------------------------
    # Financial helpers (delegated to achats app via reverse relations)
    # ------------------------------------------------------------------
    @property
    def dette_globale(self):
        """
        Sum of *reste_a_payer* across all Non Payé and Partiellement Payé
        factures fournisseurs.  Computed on-demand; cache at view layer.
        """
        from achats.models import FactureFournisseur

        qs = self.factures_fournisseur.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ]
        )
        total = qs.aggregate(total=models.Sum("reste_a_payer"))["total"]
        return total or 0

    @property
    def acompte_disponible(self):
        from achats.models import AcompteFournisseur

        result = self.acomptes.filter(utilise=False).aggregate(
            total=models.Sum("montant")
        )["total"]
        return result or 0


# ---------------------------------------------------------------------------
# Batiment
# ---------------------------------------------------------------------------


class Batiment(models.Model):
    """
    Physical poultry house / building on the farm.
    Each *lot d'élevage* is assigned to one building.
    """

    nom = models.CharField(max_length=100, verbose_name="Nom / Numéro")
    capacite = models.PositiveIntegerField(
        verbose_name="Capacité (têtes)", null=True, blank=True
    )
    description = models.TextField(verbose_name="Description", blank=True)
    actif = models.BooleanField(default=True, verbose_name="Actif")

    class Meta:
        verbose_name = "Bâtiment"
        verbose_name_plural = "Bâtiments"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


# ---------------------------------------------------------------------------
# Intrant
# ---------------------------------------------------------------------------


class Intrant(models.Model):
    """
    Catalogue of all input goods used by the farm.
    Stock balance is maintained in stock.StockIntrant (one-to-one).

    `categorie` is a FK to CategorieIntrant.  Business-logic guards that
    previously compared categorie == "aliment" / "medicament" must now
    compare categorie.code == "ALIMENT" / "MEDICAMENT" (stable seed codes).
    """

    UNITE_CHOICES = [
        ("kg", "Kilogramme (kg)"),
        ("sac", "Sac (25 kg)"),
        ("unite", "Unité / Tête"),
        ("litre", "Litre"),
        ("flacon", "Flacon"),
        ("dose", "Dose"),
        ("ml", "Millilitre (ml)"),
        ("g", "Gramme (g)"),
    ]

    designation = models.CharField(max_length=255, verbose_name="Désignation")
    categorie = models.ForeignKey(
        CategorieIntrant,
        on_delete=models.PROTECT,
        related_name="intrants",
        verbose_name="Catégorie",
    )
    unite_mesure = models.CharField(
        max_length=20,
        choices=UNITE_CHOICES,
        verbose_name="Unité de mesure",
        default="kg",
    )
    # Suppliers that provide this intrant (informational M2M)
    fournisseurs = models.ManyToManyField(
        Fournisseur,
        blank=True,
        related_name="intrants",
        verbose_name="Fournisseurs associés",
    )
    seuil_alerte = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Seuil d'alerte stock",
        default=0,
        help_text="Alerte si le stock descend sous ce seuil.",
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")
    notes = models.TextField(verbose_name="Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Intrant"
        verbose_name_plural = "Intrants"
        ordering = ["categorie__libelle", "designation"]

    def __str__(self):
        return f"{self.categorie.libelle} — {self.designation}"

    @property
    def quantite_en_stock(self):
        """Shortcut to current stock balance."""
        try:
            return self.stock.quantite
        except Exception:
            return 0

    @property
    def en_alerte(self):
        return self.quantite_en_stock <= self.seuil_alerte

    @property
    def est_consommable_en_lot(self):
        """True when the intrant's category is flagged as lot-consumable."""
        return self.categorie.consommable_en_lot
