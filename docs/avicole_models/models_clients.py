"""
clients/models.py

Full client-side AR cycle:
    Client → BLClient (delivery note) → FactureClient (invoice) → PaiementClient

Key business rules enforced at model level:
  BR-BLC-01  Stock produits finis decreases ONLY on BL validation (via signal).
  BR-BLC-02  BL cannot be validated if requested qty > available stock (view/form layer).
  BR-BLC-03  A BL in Facturé status is locked — cannot be edited or re-invoiced.
  BR-FAC-01  Invoice total = auto-sum of selected BL line totals (no manual override).
  BR-FAC-02  Only Livré (non-invoiced) BLs from the selected client may be included.
  BR-FAC-03  Client can manually select which invoice(s) a payment applies to.
"""

import datetime
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings

# ---------------------------------------------------------------------------
# Client master record
# ---------------------------------------------------------------------------


class Client(models.Model):
    """
    Customer master record.  Referenced by BL Client, Facture Client,
    and Paiement Client.

    Clients are soft-deleted via `actif = False`; never hard-deleted.
    """

    nom = models.CharField(max_length=255, verbose_name="Nom du client")
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
    TYPE_CHOICES = [
        ("grossiste", "Grossiste"),
        ("detaillant", "Détaillant"),
        ("restauration", "Restauration / Hôtellerie"),
        ("particulier", "Particulier"),
        ("autre", "Autre"),
    ]
    type_client = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="grossiste",
        verbose_name="Type de client",
    )
    plafond_credit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Plafond de crédit (DZD)",
        help_text="0 = pas de limite configurée.",
    )
    actif = models.BooleanField(default=True, verbose_name="Actif")
    notes = models.TextField(verbose_name="Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    # ------------------------------------------------------------------
    # Financial helpers
    # ------------------------------------------------------------------

    @property
    def creance_globale(self):
        """
        Sum of *reste_a_payer* across all non_payee and partiellement_payee
        client invoices.  Computed on-demand; cache at view layer.
        """
        qs = self.factures_client.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ]
        )
        total = qs.aggregate(total=models.Sum("reste_a_payer"))["total"]
        return total or 0

    @property
    def depasse_plafond(self):
        """True when a credit ceiling is configured and is exceeded."""
        if self.plafond_credit and self.plafond_credit > 0:
            return self.creance_globale > self.plafond_credit
        return False


# ---------------------------------------------------------------------------
# BL Client  (delivery note — client side)
# ---------------------------------------------------------------------------


class BLClient(models.Model):
    """
    Client delivery note.  Each validated BL deducts quantities from
    StockProduitFini via the post_save signal on BLClientLigne.

    Statuses:
      brouillon  — being entered, no stock impact yet
      livre      — validated; stock deducted; eligible for invoicing
      facture    — included in a FactureClient; locked (BR-BLC-03)
      litige     — flagged disputed; excluded from invoice creation
    """

    STATUT_BROUILLON = "brouillon"
    STATUT_LIVRE = "livre"
    STATUT_FACTURE = "facture"
    STATUT_LITIGE = "litige"

    STATUT_CHOICES = [
        (STATUT_BROUILLON, "Brouillon"),
        (STATUT_LIVRE, "Livré"),
        (STATUT_FACTURE, "Facturé"),
        (STATUT_LITIGE, "En litige"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="Référence BL"
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="bls_client",
        verbose_name="Client",
    )
    date_bl = models.DateField(verbose_name="Date du BL")
    adresse_livraison = models.TextField(
        blank=True, verbose_name="Adresse de livraison"
    )
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="Statut",
    )
    signe_par = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Signé par (réceptionnaire)",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bls_client_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "BL Client"
        verbose_name_plural = "BL Clients"
        ordering = ["-date_bl", "-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.client.nom} ({self.date_bl})"

    @property
    def montant_total(self):
        return sum(ligne.montant_total for ligne in self.lignes.all())

    @property
    def est_verrouille(self):
        """Locked BLs cannot be edited or re-invoiced (BR-BLC-03)."""
        return self.statut == self.STATUT_FACTURE


class BLClientLigne(models.Model):
    """
    One line on a BL Client — one product, quantity, and unit price.
    The line_total is computed as a property; no stored field to avoid drift.

    When the parent BL is validated (statut → livre), a post_save signal
    on BLClientLigne triggers the StockProduitFini decrease and logs a
    StockMouvement (sortie, source = bl_client).
    """

    bl = models.ForeignKey(
        BLClient,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="BL Client",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        related_name="lignes_bl_client",
        verbose_name="Produit fini",
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
        help_text="Pré-rempli depuis prix_vente_defaut du produit fini.",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")

    class Meta:
        verbose_name = "Ligne BL Client"
        verbose_name_plural = "Lignes BL Client"

    def __str__(self):
        return (
            f"{self.bl.reference} — "
            f"{self.produit_fini.designation} × {self.quantite}"
        )

    @property
    def montant_total(self):
        return self.quantite * self.prix_unitaire


# ---------------------------------------------------------------------------
# Facture Client  (AR invoice)
# ---------------------------------------------------------------------------


class FactureClient(models.Model):
    """
    Client invoice aggregating one or more validated (Livré) BL Clients.

    BR-FAC-01: total HT = auto-sum of BLClientLigne totals for included BLs.
    BR-FAC-02: Only Livré BLs from the selected client may be included.
    Upon creation, included BLs are marked Facturé (locked).

    TVA is stored separately to support future rate changes; may be 0.
    """

    STATUT_NON_PAYEE = "non_payee"
    STATUT_PARTIELLEMENT_PAYEE = "partiellement_payee"
    STATUT_PAYEE = "payee"
    STATUT_EN_LITIGE = "en_litige"

    STATUT_CHOICES = [
        (STATUT_NON_PAYEE, "Non payée"),
        (STATUT_PARTIELLEMENT_PAYEE, "Partiellement payée"),
        (STATUT_PAYEE, "Payée"),
        (STATUT_EN_LITIGE, "En litige"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="Référence facture"
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="factures_client",
        verbose_name="Client",
    )
    # BLs linked at creation; locked afterwards (mirrors BR-FAF-03 logic)
    bls = models.ManyToManyField(
        BLClient,
        blank=True,
        related_name="factures",
        verbose_name="BL inclus",
    )
    date_facture = models.DateField(verbose_name="Date de la facture")
    date_echeance = models.DateField(
        null=True, blank=True, verbose_name="Date d'échéance"
    )
    # Auto-computed from BL lines at invoice creation; stored for performance.
    montant_ht = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Montant HT (DZD)",
    )
    taux_tva = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Taux TVA (%)",
        help_text="0 si exonéré.",
    )
    montant_tva = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Montant TVA (DZD)",
    )
    montant_ttc = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="Montant TTC (DZD)",
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
        default=STATUT_NON_PAYEE,
        verbose_name="Statut",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factures_client_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Facture Client"
        verbose_name_plural = "Factures Clients"
        ordering = ["-date_facture"]

    def __str__(self):
        return f"{self.reference} — {self.client.nom} — " f"{self.montant_ttc} DZD"

    def compute_montant_ht(self):
        """
        BR-FAC-01: Invoice HT total = sum of all BLClientLigne totals for
        every BL included in this invoice.  Call this at invoice creation
        inside a transaction and store the result in montant_ht.
        """
        total = sum(
            ligne.montant_total for bl in self.bls.all() for ligne in bl.lignes.all()
        )
        return total

    def clean(self):
        """
        BR-FAC-01: guard montant_ht against manual post-creation changes.
        """
        from django.core.exceptions import ValidationError

        if self.pk:
            try:
                original = FactureClient.objects.get(pk=self.pk)
                if original.montant_ht != self.montant_ht:
                    raise ValidationError(
                        {
                            "montant_ht": (
                                "BR-FAC-01 : le montant HT est calculé "
                                "automatiquement depuis les lignes BL et ne "
                                "peut pas être modifié manuellement."
                            )
                        }
                    )
            except FactureClient.DoesNotExist:
                pass

    def recalculer_solde(self):
        """
        Recompute reste_a_payer and update statut.
        Called after each PaiementClientAllocation is recorded.
        """
        self.reste_a_payer = max(0, self.montant_ttc - self.montant_regle)
        if self.statut == self.STATUT_EN_LITIGE:
            pass  # preserve litige status
        elif self.montant_regle <= 0:
            self.statut = self.STATUT_NON_PAYEE
        elif self.reste_a_payer <= 0:
            self.statut = self.STATUT_PAYEE
        else:
            self.statut = self.STATUT_PARTIELLEMENT_PAYEE
        self.save(
            update_fields=[
                "montant_regle",
                "reste_a_payer",
                "statut",
                "updated_at",
            ]
        )

    @property
    def est_en_retard(self):
        if self.date_echeance and self.statut not in (self.STATUT_PAYEE,):
            return datetime.date.today() > self.date_echeance
        return False


# ---------------------------------------------------------------------------
# Paiement Client
# ---------------------------------------------------------------------------


class PaiementClient(models.Model):
    """
    A payment amount recorded against a client.

    Unlike supplier settlement (FIFO-automatic), the user manually selects
    which invoice(s) this payment applies to via PaiementClientAllocation
    (BR-FAC-03).

    Records are immutable after creation.
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

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="paiements",
        verbose_name="Client",
    )
    date_paiement = models.DateField(verbose_name="Date du paiement")
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
        related_name="paiements_client_enregistres",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Paiement Client"
        verbose_name_plural = "Paiements Clients"
        ordering = ["-date_paiement", "-created_at"]

    def __str__(self):
        return (
            f"Paiement {self.client.nom} — "
            f"{self.montant} DZD ({self.date_paiement})"
        )

    @property
    def montant_alloue(self):
        """Sum of amounts already allocated to invoices."""
        result = self.allocations.aggregate(total=models.Sum("montant_alloue"))["total"]
        return result or 0

    @property
    def solde_non_alloue(self):
        """Portion of this payment not yet attributed to any invoice."""
        return self.montant - self.montant_alloue


class PaiementClientAllocation(models.Model):
    """
    Immutable line: portion of one paiement applied to one facture client.
    Created by the view when the user selects invoices to pay (BR-FAC-03).
    Never edited after creation.
    """

    paiement = models.ForeignKey(
        PaiementClient,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="Paiement",
    )
    facture = models.ForeignKey(
        FactureClient,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="Facture",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="Montant alloué (DZD)",
        validators=[MinValueValidator(0.01)],
    )

    class Meta:
        verbose_name = "Allocation Paiement Client"
        verbose_name_plural = "Allocations Paiements Clients"

    def __str__(self):
        return (
            f"{self.paiement} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )
