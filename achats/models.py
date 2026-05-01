"""
achats/models.py

Supplier procurement cycle:
  BLFournisseur → FactureFournisseur → ReglementFournisseur (FIFO)
  AcompteFournisseur captures overpayment surplus.
"""

import datetime
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings


class BLFournisseur(models.Model):
    STATUT_BROUILLON = "brouillon"
    STATUT_RECU = "recu"
    STATUT_FACTURE = "facture"
    STATUT_LITIGE = "litige"

    STATUT_CHOICES = [
        (STATUT_BROUILLON, "Brouillon"),
        (STATUT_RECU, "Reçu"),
        (STATUT_FACTURE, "Facturé"),
        (STATUT_LITIGE, "En litige"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="Référence BL"
    )
    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="bls_fournisseur",
        verbose_name="Fournisseur",
    )
    date_bl = models.DateField(verbose_name="Date du BL")
    reference_fournisseur = models.CharField(
        max_length=100, blank=True, verbose_name="Référence fournisseur"
    )
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="Statut",
    )
    notes_reception = models.TextField(blank=True, verbose_name="Notes de réception")
    piece_jointe = models.FileField(
        upload_to="bl_fournisseur/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="Pièce jointe (PDF/JPG/PNG)",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bls_fournisseur_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BL Fournisseur"
        verbose_name_plural = "BL Fournisseurs"
        ordering = ["-date_bl", "-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.fournisseur.nom} ({self.date_bl})"

    @property
    def montant_total(self):
        return sum(ligne.montant_total for ligne in self.lignes.all())

    @property
    def a_piece_jointe(self):
        return bool(self.piece_jointe)

    @property
    def est_verrouille(self):
        """Locked BLs cannot be edited or re-invoiced (BR-BLF-02)."""
        return self.statut == self.STATUT_FACTURE


class BLFournisseurLigne(models.Model):
    bl = models.ForeignKey(
        BLFournisseur,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="BL Fournisseur",
    )
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="lignes_bl_fournisseur",
        verbose_name="Intrant",
    )
    quantite = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="Quantité",
        validators=[MinValueValidator(0.001)],
    )
    prix_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        verbose_name="Prix unitaire (DZD)",
        validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True, verbose_name="Notes")

    class Meta:
        verbose_name = "Ligne BL Fournisseur"
        verbose_name_plural = "Lignes BL Fournisseur"

    def __str__(self):
        return f"{self.bl.reference} — {self.intrant.designation} × {self.quantite}"

    @property
    def montant_total(self):
        return self.quantite * self.prix_unitaire


class FactureFournisseur(models.Model):
    STATUT_NON_PAYE = "non_paye"
    STATUT_PARTIELLEMENT_PAYE = "partiellement_paye"
    STATUT_PAYE = "paye"
    STATUT_EN_LITIGE = "en_litige"

    STATUT_CHOICES = [
        (STATUT_NON_PAYE, "Non payée"),
        (STATUT_PARTIELLEMENT_PAYE, "Partiellement payée"),
        (STATUT_PAYE, "Payée"),
        (STATUT_EN_LITIGE, "En litige"),
    ]

    TYPE_MARCHANDISES = "marchandises"
    TYPE_SERVICE = "service"

    TYPE_CHOICES = [
        (TYPE_MARCHANDISES, "Marchandises"),
        (TYPE_SERVICE, "Service"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="Référence facture"
    )
    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="factures_fournisseur",
        verbose_name="Fournisseur",
    )
    # BLs included in this invoice — set at creation; locked afterwards (BR-FAF-03)
    bls = models.ManyToManyField(
        BLFournisseur,
        blank=True,
        related_name="factures",
        verbose_name="BL inclus",
    )
    date_facture = models.DateField(verbose_name="Date de la facture")
    date_echeance = models.DateField(
        null=True, blank=True, verbose_name="Date d'échéance"
    )
    type_facture = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_MARCHANDISES,
        verbose_name="Type de facture",
    )
    # Auto-computed from BL lines at invoice creation (BR-FAF-01); stored for performance.
    montant_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Montant total (DZD)",
    )
    montant_regle = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Montant réglé (DZD)",
    )
    reste_a_payer = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Reste à payer (DZD)",
    )
    statut = models.CharField(
        max_length=25,
        choices=STATUT_CHOICES,
        default=STATUT_NON_PAYE,
        verbose_name="Statut",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factures_fournisseur_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facture Fournisseur"
        verbose_name_plural = "Factures Fournisseurs"
        ordering = ["-date_facture"]

    def __str__(self):
        return f"{self.reference} — {self.fournisseur.nom} — {self.montant_total} DZD"

    def clean(self):
        """
        BR-FAF-01: montant_total must be derived exclusively from selected BL
        line totals at invoice creation — no manual entry.  This guard
        prevents accidental zeroing of the stored amount.
        """
        from django.core.exceptions import ValidationError

        # On update, block manual changes to montant_total.
        if self.pk:
            try:
                original = FactureFournisseur.objects.get(pk=self.pk)
                if original.montant_total != self.montant_total:
                    raise ValidationError(
                        {
                            "montant_total": (
                                "BR-FAF-01 : le montant total est calculé "
                                "automatiquement depuis les lignes BL et ne "
                                "peut pas être modifié manuellement."
                            )
                        }
                    )
            except FactureFournisseur.DoesNotExist:
                pass

    def recalculer_solde(self):
        """Recompute reste_a_payer and update statut. Called after each allocation."""
        self.reste_a_payer = max(0, self.montant_total - self.montant_regle)
        if self.statut == self.STATUT_EN_LITIGE:
            pass  # preserve litige status
        elif self.montant_regle <= 0:
            self.statut = self.STATUT_NON_PAYE
        elif self.reste_a_payer <= 0:
            self.statut = self.STATUT_PAYE
        else:
            self.statut = self.STATUT_PARTIELLEMENT_PAYE
        self.save(
            update_fields=["montant_regle", "reste_a_payer", "statut", "updated_at"]
        )

    @property
    def est_en_retard(self):
        if self.date_echeance and self.statut not in (self.STATUT_PAYE,):
            return datetime.date.today() > self.date_echeance
        return False


class ReglementFournisseur(models.Model):
    """
    A payment sum recorded against a supplier.
    On creation, the FIFO engine (achats.utils.appliquer_reglement_fifo)
    automatically allocates it across open invoices oldest-first.
    Records are immutable after creation (BR-REG-06).
    """

    MODE_ESPECES = "especes"
    MODE_CHEQUE = "cheque"
    MODE_VIREMENT = "virement"
    MODE_AUTRE = "autre"

    MODE_CHOICES = [
        (MODE_ESPECES, "Espèces"),
        (MODE_CHEQUE, "Chèque"),
        (MODE_VIREMENT, "Virement bancaire"),
        (MODE_AUTRE, "Autre"),
    ]

    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="reglements",
        verbose_name="Fournisseur",
    )
    date_reglement = models.DateField(verbose_name="Date du règlement")
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Montant (DZD)",
        validators=[MinValueValidator(0.01)],
    )
    mode_paiement = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_ESPECES,
        verbose_name="Mode de paiement",
    )
    reference_paiement = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Référence (n° chèque / virement)",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reglements_fournisseur_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Règlement Fournisseur"
        verbose_name_plural = "Règlements Fournisseurs"
        ordering = ["-date_reglement", "-created_at"]

    def __str__(self):
        return (
            f"Règlement {self.fournisseur.nom} — "
            f"{self.montant} DZD ({self.date_reglement})"
        )


class AllocationReglement(models.Model):
    """
    Immutable line: portion of one règlement applied to one facture.
    Created exclusively by the FIFO engine; never edited by users (BR-REG-06).
    """

    reglement = models.ForeignKey(
        ReglementFournisseur,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="Règlement",
    )
    facture = models.ForeignKey(
        FactureFournisseur,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="Facture",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Montant alloué (DZD)",
    )

    class Meta:
        verbose_name = "Allocation Règlement"
        verbose_name_plural = "Allocations Règlements"

    def __str__(self):
        return (
            f"{self.reglement} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )


class AcompteFournisseur(models.Model):
    """
    Overpayment surplus credited to the supplier for future invoices (BR-REG-04).
    Created automatically when règlement.montant > dette_globale.
    """

    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="acomptes",
        verbose_name="Fournisseur",
    )
    reglement = models.OneToOneField(
        ReglementFournisseur,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acompte",
        verbose_name="Règlement source",
    )
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Montant (DZD)",
        validators=[MinValueValidator(0.01)],
    )
    date = models.DateField(verbose_name="Date")
    utilise = models.BooleanField(default=False, verbose_name="Utilisé")
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Acompte Fournisseur"
        verbose_name_plural = "Acomptes Fournisseurs"
        ordering = ["-date"]

    def __str__(self):
        status = "Utilisé" if self.utilise else "En attente"
        return f"Acompte {self.fournisseur.nom} — {self.montant} DZD [{status}]"
