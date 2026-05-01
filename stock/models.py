"""
stock/models.py

Two distinct segments:
  - StockIntrant      : inventory of input goods (aliments, poussins, médicaments)
  - StockProduitFini  : inventory of finished products ready for client delivery

Each segment maintains a running balance updated via signals in the apps
that trigger movements (achats, elevage, production, clients).

StockMouvement provides a full audit trail of every in/out event for any
stock item, regardless of segment.

StockAjustement captures manual corrections with mandatory justification.
"""

from django.db import models
from django.conf import settings

# ---------------------------------------------------------------------------
# Intrant stock
# ---------------------------------------------------------------------------


class StockIntrant(models.Model):
    """
    Current balance for one Intrant (one-to-one relationship).
    The balance is never edited directly — it is updated exclusively
    through StockMouvement records triggered by validated BL Fournisseur,
    Consommation, and StockAjustement events.
    """

    intrant = models.OneToOneField(
        "intrants.Intrant",
        on_delete=models.CASCADE,
        related_name="stock",
        verbose_name="Intrant",
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Quantité en stock",
    )
    # Weighted average cost — updated each time a BL line is validated.
    prix_moyen_pondere = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        verbose_name="Prix moyen pondéré (DZD)",
    )
    derniere_mise_a_jour = models.DateTimeField(
        auto_now=True, verbose_name="Dernière mise à jour"
    )

    class Meta:
        verbose_name = "Stock intrant"
        verbose_name_plural = "Stocks intrants"

    def __str__(self):
        return (
            f"{self.intrant.designation} — {self.quantite} {self.intrant.unite_mesure}"
        )

    @property
    def valeur_stock(self):
        return self.quantite * self.prix_moyen_pondere

    @property
    def en_alerte(self):
        return self.quantite <= self.intrant.seuil_alerte


# ---------------------------------------------------------------------------
# Produit fini stock
# ---------------------------------------------------------------------------


class StockProduitFini(models.Model):
    """
    Current balance for one ProduitFini.
    Increases via validated ProductionRecord lines.
    Decreases via validated BL Client lines.
    """

    produit_fini = models.OneToOneField(
        "production.ProduitFini",
        on_delete=models.CASCADE,
        related_name="stock",
        verbose_name="Produit fini",
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Quantité en stock",
    )
    cout_moyen_production = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        verbose_name="Coût moyen de production (DZD)",
    )
    seuil_alerte = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        verbose_name="Seuil d'alerte",
    )
    derniere_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock produit fini"
        verbose_name_plural = "Stocks produits finis"

    def __str__(self):
        return f"{self.produit_fini.designation} — {self.quantite} {self.produit_fini.unite_mesure}"

    @property
    def valeur_stock(self):
        return self.quantite * self.cout_moyen_production

    @property
    def en_alerte(self):
        return self.quantite <= self.seuil_alerte


# ---------------------------------------------------------------------------
# Unified movement audit trail
# ---------------------------------------------------------------------------


class StockMouvement(models.Model):
    """
    Immutable audit-trail record for every quantity change in either
    stock segment.  Created automatically by signals; never edited manually.

    Only one of (intrant / produit_fini) is populated per record.
    """

    TYPE_ENTREE = "entree"
    TYPE_SORTIE = "sortie"
    TYPE_AJUSTEMENT = "ajustement"

    TYPE_CHOICES = [
        (TYPE_ENTREE, "Entrée"),
        (TYPE_SORTIE, "Sortie"),
        (TYPE_AJUSTEMENT, "Ajustement"),
    ]

    SOURCE_BL_FOURNISSEUR = "bl_fournisseur"
    SOURCE_CONSOMMATION = "consommation"
    SOURCE_PRODUCTION = "production"
    SOURCE_BL_CLIENT = "bl_client"
    SOURCE_AJUSTEMENT = "ajustement"

    SOURCE_CHOICES = [
        (SOURCE_BL_FOURNISSEUR, "BL Fournisseur"),
        (SOURCE_CONSOMMATION, "Consommation lot"),
        (SOURCE_PRODUCTION, "Production"),
        (SOURCE_BL_CLIENT, "BL Client"),
        (SOURCE_AJUSTEMENT, "Ajustement manuel"),
    ]

    # Target stock item — exactly one must be set.
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mouvements",
        verbose_name="Intrant",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mouvements",
        verbose_name="Produit fini",
    )

    type_mouvement = models.CharField(
        max_length=20, choices=TYPE_CHOICES, verbose_name="Type"
    )
    source = models.CharField(
        max_length=30, choices=SOURCE_CHOICES, verbose_name="Source"
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        verbose_name="Quantité",
        help_text="Always positive; direction is given by type_mouvement.",
    )
    quantite_avant = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="Stock avant"
    )
    quantite_apres = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="Stock après"
    )
    date_mouvement = models.DateField(verbose_name="Date du mouvement")
    # Soft references to source documents (store PK as string for flexibility)
    reference_id = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="ID document source"
    )
    reference_label = models.CharField(
        max_length=100, blank=True, verbose_name="Référence document"
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mouvement de stock"
        verbose_name_plural = "Mouvements de stock"
        ordering = ["-date_mouvement", "-created_at"]

    def __str__(self):
        item = self.intrant or self.produit_fini
        return (
            f"{self.get_type_mouvement_display()} | {item} | "
            f"{self.quantite} | {self.date_mouvement}"
        )


# ---------------------------------------------------------------------------
# Manual stock adjustments
# ---------------------------------------------------------------------------


class StockAjustement(models.Model):
    """
    Manual correction applied when a physical count reveals a discrepancy.
    Flagged in audit trail; generates a StockMouvement of type 'ajustement'.
    """

    SEGMENT_INTRANT = "intrant"
    SEGMENT_PRODUIT_FINI = "produit_fini"

    SEGMENT_CHOICES = [
        (SEGMENT_INTRANT, "Stock Intrants"),
        (SEGMENT_PRODUIT_FINI, "Stock Produits Finis"),
    ]

    segment = models.CharField(
        max_length=20, choices=SEGMENT_CHOICES, verbose_name="Segment de stock"
    )
    # Exactly one of the two FKs below is populated.
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ajustements",
        verbose_name="Intrant",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ajustements",
        verbose_name="Produit fini",
    )
    date_ajustement = models.DateField(verbose_name="Date de l'ajustement")
    quantite_avant = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="Quantité avant ajustement"
    )
    quantite_apres = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="Quantité après ajustement"
    )
    raison = models.TextField(
        verbose_name="Raison / Justification",
        help_text="Obligatoire — décrivez l'écart constaté.",
    )
    effectue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ajustements_stock",
        verbose_name="Effectué par",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ajustement de stock"
        verbose_name_plural = "Ajustements de stock"
        ordering = ["-date_ajustement"]

    def __str__(self):
        item = self.intrant or self.produit_fini
        delta = self.quantite_apres - self.quantite_avant
        sign = "+" if delta >= 0 else ""
        return f"Ajustement {item} : {sign}{delta} ({self.date_ajustement})"
