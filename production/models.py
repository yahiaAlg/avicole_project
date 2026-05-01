"""
production/models.py

Captures the transformation of live birds from a lot into finished products
(produits finis) and their entry into finished-goods stock.
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings


class ProduitFini(models.Model):
    """
    Catalogue of all finished product types the farm can produce.
    Stock balance is maintained in stock.StockProduitFini (one-to-one).
    """

    TYPE_VOLAILLE_VIVANTE = "volaille_vivante"
    TYPE_CARCASSE = "carcasse"
    TYPE_DECOUPE = "decoupe"
    TYPE_ABATS = "abats"
    TYPE_OEUFS = "oeufs"
    TYPE_AUTRE = "autre"

    TYPE_CHOICES = [
        (TYPE_VOLAILLE_VIVANTE, "Volaille vivante"),
        (TYPE_CARCASSE, "Carcasse entière"),
        (TYPE_DECOUPE, "Découpe"),
        (TYPE_ABATS, "Abats"),
        (TYPE_OEUFS, "Œufs"),
        (TYPE_AUTRE, "Autre"),
    ]

    UNITE_CHOICES = [
        ("unite", "Unité / Tête"),
        ("kg", "Kilogramme (kg)"),
        ("plateau", "Plateau"),
        ("caisse", "Caisse"),
        ("paquet", "Paquet"),
    ]

    designation = models.CharField(max_length=255, verbose_name="Désignation")
    type_produit = models.CharField(
        max_length=30,
        choices=TYPE_CHOICES,
        default=TYPE_VOLAILLE_VIVANTE,
        verbose_name="Type de produit",
    )
    unite_mesure = models.CharField(
        max_length=20,
        choices=UNITE_CHOICES,
        default="unite",
        verbose_name="Unité de mesure",
    )
    prix_vente_defaut = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name="Prix de vente par défaut (DZD)",
        help_text="Pré-rempli sur les lignes BL client — modifiable.",
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Produit fini"
        verbose_name_plural = "Produits finis"
        ordering = ["type_produit", "designation"]

    def __str__(self):
        return f"{self.get_type_produit_display()} — {self.designation}"

    @property
    def quantite_en_stock(self):
        try:
            return self.stock.quantite
        except Exception:
            return 0


class ProductionRecord(models.Model):
    """
    Header record for a harvest / production event.
    Linked to one LotElevage; may have multiple output lines (ProductionLigne).

    On validation, all ProductionLigne quantities are added to
    StockProduitFini and a StockMouvement (entree) is created for each.
    """

    STATUT_BROUILLON = "brouillon"
    STATUT_VALIDE = "valide"
    STATUT_CHOICES = [
        (STATUT_BROUILLON, "Brouillon"),
        (STATUT_VALIDE, "Validé"),
    ]

    lot = models.ForeignKey(
        "elevage.LotElevage",
        on_delete=models.PROTECT,
        related_name="productions",
        verbose_name="Lot d'élevage",
    )
    date_production = models.DateField(verbose_name="Date de production / abattage")
    nombre_oiseaux_abattus = models.PositiveIntegerField(
        verbose_name="Nombre d'oiseaux abattus / récoltés",
        validators=[MinValueValidator(1)],
    )
    poids_total_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Poids total (kg)",
        default=0,
    )
    poids_moyen_kg = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        verbose_name="Poids moyen par oiseau (kg)",
        default=0,
        help_text="Auto-calculé si poids_total fourni.",
    )
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="Statut",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productions_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Enregistrement de production"
        verbose_name_plural = "Enregistrements de production"
        ordering = ["-date_production"]

    def __str__(self):
        return f"Production {self.lot.designation} — {self.date_production}"

    def save(self, *args, **kwargs):
        # Auto-compute average weight when total weight is provided.
        if self.poids_total_kg and self.nombre_oiseaux_abattus:
            self.poids_moyen_kg = round(
                self.poids_total_kg / self.nombre_oiseaux_abattus, 3
            )
        super().save(*args, **kwargs)


class ProductionLigne(models.Model):
    """
    One output line within a ProductionRecord.
    Each line creates stock for one ProduitFini type.
    """

    production = models.ForeignKey(
        ProductionRecord,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="Enregistrement de production",
    )
    produit_fini = models.ForeignKey(
        ProduitFini,
        on_delete=models.PROTECT,
        related_name="lignes_production",
        verbose_name="Produit fini",
    )
    quantite = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Quantité produite",
        validators=[MinValueValidator(0.001)],
    )
    poids_unitaire_kg = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        default=0,
        verbose_name="Poids unitaire (kg)",
    )
    cout_unitaire_estime = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        verbose_name="Coût unitaire estimé (DZD)",
        help_text="Alloué depuis le coût total du lot.",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")

    class Meta:
        verbose_name = "Ligne de production"
        verbose_name_plural = "Lignes de production"

    def __str__(self):
        return (
            f"{self.production} — {self.produit_fini.designation} "
            f"× {self.quantite} {self.produit_fini.unite_mesure}"
        )

    @property
    def valeur_totale(self):
        return self.quantite * self.cout_unitaire_estime
