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
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from core.models import PieceJointe

# ---------------------------------------------------------------------------
# Dynamic category table (replaces hardcoded choice tuple)
# ---------------------------------------------------------------------------


class TypeClient(models.Model):
    """
    User-manageable client types.
    Seeded: GROSSISTE, DETAILLANT, RESTAURATION, PARTICULIER, AUTRE.

    The `code` field is the stable programmatic key. Administrators may add
    types but should NOT rename the five seed codes.
    """

    code = models.CharField(
        max_length=30,
        unique=True,
        verbose_name="الرمز",
        help_text="مفتاح ثابت: GROSSISTE, DETAILLANT, RESTAURATION, "
        "PARTICULIER, AUTRE — لا تعيد تسميته.",
    )
    libelle = models.CharField(max_length=150, verbose_name="التسمية")
    ordre = models.PositiveSmallIntegerField(default=0, verbose_name="ترتيب العرض")
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "نوع العميل"
        verbose_name_plural = "أنواع العملاء"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


# ---------------------------------------------------------------------------
# Client master record
# ---------------------------------------------------------------------------


class Client(models.Model):
    """
    Customer master record.  Referenced by BL Client, Facture Client,
    and Paiement Client.

    Clients are soft-deleted via `actif = False`; never hard-deleted.

    `type_client` is a FK to TypeClient.  Business-logic guards that
    previously compared type_client == "grossiste" must now compare
    type_client.code == "GROSSISTE" (stable seed code).
    """

    nom = models.CharField(max_length=255, verbose_name="اسم العميل")
    adresse = models.TextField(verbose_name="العنوان", blank=True)
    wilaya = models.CharField(max_length=100, verbose_name="الولاية", blank=True)
    telephone = models.CharField(max_length=30, verbose_name="الهاتف", blank=True)
    telephone_2 = models.CharField(max_length=30, verbose_name="الهاتف 2", blank=True)
    email = models.EmailField(verbose_name="البريد الإلكتروني", blank=True)
    nif = models.CharField(max_length=50, verbose_name="NIF", blank=True)
    rc = models.CharField(max_length=50, verbose_name="RC", blank=True)
    contact_nom = models.CharField(
        max_length=150, verbose_name="اسم جهة الاتصال", blank=True
    )
    type_client = models.ForeignKey(
        TypeClient,
        on_delete=models.PROTECT,
        related_name="clients",
        verbose_name="نوع العميل",
    )
    plafond_credit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="سقف الائتمان (د.ج)",
        help_text="0 = لا يوجد حد محدد.",
    )
    actif = models.BooleanField(default=True, verbose_name="نشط")
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "عميل"
        verbose_name_plural = "العملاء"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    # ------------------------------------------------------------------
    # Financial helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Financial helpers
    #
    # v1.4 — Client stays global (§3.5.3: a client can transact with
    # several branches), but FactureClient is branch-scoped. So this is
    # the Vue Globale figure (sum across all branches); pass `branche` to
    # get the figure for one branch only, exactly as a chef de branche
    # sees it (§3.5.3 ¶4).
    # ------------------------------------------------------------------

    def creance_globale(self, branche=None):
        """
        Sum of *reste_a_payer* across all non_payee and partiellement_payee
        client invoices.  Computed on-demand; cache at view layer.
        Pass `branche` to scope to one branch; omit for Vue Globale.
        """
        qs = self.factures_client.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ]
        )
        if branche is not None:
            qs = qs.filter(branche=branche)
        total = qs.aggregate(total=models.Sum("reste_a_payer"))["total"]
        return total or 0

    @property
    def creance_globale_toutes_branches(self):
        return self.creance_globale()

    @property
    def depasse_plafond(self):
        """True when a credit ceiling is configured and is exceeded."""
        if self.plafond_credit and self.plafond_credit > 0:
            return self.creance_globale() > self.plafond_credit
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
        (STATUT_BROUILLON, "مسودة"),
        (STATUT_LIVRE, "تم التسليم"),
        (STATUT_FACTURE, "مفوتر"),
        (STATUT_LITIGE, "في نزاع"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="مرجع وصل التسليم"
    )
    # v1.4 — Client stays global (§3.5.3), but the delivery itself comes
    # out of one branche's StockProduitFini (BR-BRA-01). Set explicitly
    # at creation — the chef de branche's own branche, or chosen by an admin.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="bls_client",
        verbose_name="الفرع",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="bls_client",
        verbose_name="العميل",
    )
    date_bl = models.DateField(verbose_name="تاريخ وصل التسليم")
    adresse_livraison = models.TextField(blank=True, verbose_name="عنوان التسليم")
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="الحالة",
    )
    signe_par = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="موقّع من قبل (المستلم)",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bls_client_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # v1.5 — proof documents (signed delivery slip photo, etc.).
    pieces_jointes = GenericRelation(PieceJointe, related_query_name="bl_client")

    class Meta:
        verbose_name = "وصل تسليم العميل"
        verbose_name_plural = "وصولات تسليم العملاء"
        ordering = ["-date_bl", "-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.client.nom} ({self.date_bl})"

    @property
    def montant_total(self):
        return sum(ligne.montant_total for ligne in self.lignes.all())

    @property
    def a_piece_jointe(self):
        return self.pieces_jointes.exists()

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
        verbose_name="وصل تسليم العميل",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        related_name="lignes_bl_client",
        verbose_name="المنتج النهائي",
    )
    quantite = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="الكمية",
        validators=[MinValueValidator(0.001)],
    )
    prix_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        verbose_name="سعر الوحدة (د.ج)",
        validators=[MinValueValidator(0)],
        help_text="يُملأ مسبقاً من سعر البيع الافتراضي للمنتج النهائي.",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")

    class Meta:
        verbose_name = "سطر وصل تسليم العميل"
        verbose_name_plural = "أسطر وصل تسليم العملاء"

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
        (STATUT_NON_PAYEE, "غير مدفوعة"),
        (STATUT_PARTIELLEMENT_PAYEE, "مدفوعة جزئياً"),
        (STATUT_PAYEE, "مدفوعة"),
        (STATUT_EN_LITIGE, "في نزاع"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="مرجع الفاتورة"
    )
    # v1.4 — must match the branche of every BL included in `bls` below
    # (enforced at the view/M2M-assignment layer — mirrors
    # FactureFournisseur.branche, BR-BRA-01).
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="factures_client",
        verbose_name="الفرع",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="factures_client",
        verbose_name="العميل",
    )
    # BLs linked at creation; locked afterwards (mirrors BR-FAF-03 logic)
    bls = models.ManyToManyField(
        BLClient,
        blank=True,
        related_name="factures",
        verbose_name="وصولات التسليم المضمنة",
    )
    date_facture = models.DateField(verbose_name="تاريخ الفاتورة")
    date_echeance = models.DateField(
        null=True, blank=True, verbose_name="تاريخ الاستحقاق"
    )
    # Auto-computed from BL lines at invoice creation; stored for performance.
    montant_ht = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ قبل الضريبة (د.ج)",
    )
    taux_tva = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="نسبة الضريبة على القيمة المضافة (%)",
        help_text="0 إذا كان معفى.",
    )
    montant_tva = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="مبلغ الضريبة على القيمة المضافة (د.ج)",
    )
    montant_ttc = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ شامل الضريبة (د.ج)",
    )
    montant_regle = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ المسدد (د.ج)",
    )
    reste_a_payer = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ المتبقي (د.ج)",
    )
    statut = models.CharField(
        max_length=25,
        choices=STATUT_CHOICES,
        default=STATUT_NON_PAYEE,
        verbose_name="الحالة",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factures_client_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # v1.5 — proof documents (signed invoice copy, etc.).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="facture_client"
    )

    class Meta:
        verbose_name = "فاتورة العميل"
        verbose_name_plural = "فواتير العملاء"
        ordering = ["-date_facture"]

    def __str__(self):
        return f"{self.reference} — {self.client.nom} — " f"{self.montant_ttc} DZD"

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
        (MODE_ESPECES, "نقداً"),
        (MODE_CHEQUE, "شيك"),
        (MODE_VIREMENT, "تحويل بنكي"),
        (MODE_AUTRE, "أخرى"),
    ]

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="paiements",
        verbose_name="العميل",
    )
    # v1.4 — the user manually selects which invoice(s) this payment
    # applies to (BR-FAC-03); those invoices must belong to this same
    # branche (BR-BRA-01), mirroring ReglementFournisseur.branche.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="paiements_client",
        verbose_name="الفرع",
    )
    date_paiement = models.DateField(verbose_name="تاريخ الدفع")
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    mode_paiement = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_ESPECES,
        verbose_name="طريقة الدفع",
    )
    reference_paiement = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="المرجع (رقم الشيك / التحويل)",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paiements_client_enregistres",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.5 — proof of payment (cheque scan, transfer confirmation, ...).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="paiement_client"
    )

    class Meta:
        verbose_name = "دفع العميل"
        verbose_name_plural = "مدفوعات العملاء"
        ordering = ["-date_paiement", "-created_at"]

    def __str__(self):
        return f"دفع {self.client.nom} — " f"{self.montant} DZD ({self.date_paiement})"

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
        verbose_name="الدفع",
    )
    facture = models.ForeignKey(
        FactureClient,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="الفاتورة",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ المخصص (د.ج)",
        validators=[MinValueValidator(0.01)],
    )

    class Meta:
        verbose_name = "تخصيص دفع العميل"
        verbose_name_plural = "تخصيصات مدفوعات العملاء"

    def __str__(self):
        return (
            f"{self.paiement} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )

    def clean(self):
        from django.core.exceptions import ValidationError

        # v1.4 — a payment can only be allocated to invoices in its own
        # branche (BR-BRA-01); cross-branch allocation would silently move
        # AR balance from one branch's books into another's.
        if (
            self.paiement_id
            and self.facture_id
            and self.paiement.branche_id != self.facture.branche_id
        ):
            raise ValidationError(
                "BR-BRA-01 : le paiement et la facture doivent appartenir à "
                "la même branche."
            )


# ---------------------------------------------------------------------------
# Acompte Client — prepayment / overpayment surplus
# ---------------------------------------------------------------------------


class AcompteClient(models.Model):
    """
    A client-side prepayment / overpayment surplus, mirroring
    AcompteFournisseur on the supplier side.

    Created automatically right after a PaiementClient is recorded and its
    allocations (manual or FIFO fallback) are applied, whenever some amount
    remains unattributed to any invoice (paiement.solde_non_alloue > 0) —
    e.g. a client who pays in advance before any facture exists, or pays
    more than their current debt.

    Unlike ReglementFournisseur (fully FIFO-automatic), a PaiementClient's
    allocation is manual (BR-FAC-03); the leftover is captured here in one
    shot right after that manual step, rather than inside a FIFO loop.

    Consumed automatically, oldest-first, against every new FactureClient
    created for the same client + branche thereafter (see
    clients.utils.consommer_acomptes_client_fifo, called from the
    m2m_changed signal on FactureClient.bls — mirrors BR-REG-07).
    """

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="acomptes",
        verbose_name="العميل",
    )
    # v1.4 — branch-scoped like every other AR document (BR-BRA-01); synced
    # from `paiement.branche` in save() so callers never need to pass it.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="acomptes_client",
        verbose_name="الفرع",
    )
    paiement = models.OneToOneField(
        PaiementClient,
        on_delete=models.CASCADE,
        related_name="acompte",
        verbose_name="الدفع",
    )
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ الأصلي (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    montant_restant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ المتبقي (د.ج)",
        help_text="يُنقص تلقائياً كلما استُهلك من أجل فاتورة جديدة.",
    )
    date = models.DateField(verbose_name="التاريخ")
    utilise = models.BooleanField(
        default=False,
        verbose_name="مستهلكة بالكامل",
        help_text="يصبح True تلقائياً عندما montant_restant يصل إلى 0.",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # v1.5 — proof documents, attached after the fact (no create/edit view —
    # these rows are only ever created automatically).
    pieces_jointes = GenericRelation(PieceJointe, related_query_name="acompte_client")

    class Meta:
        verbose_name = "دفعة مسبقة (عميل)"
        verbose_name_plural = "الدفعات المسبقة (العملاء)"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return (
            f"دفعة مسبقة — {self.client.nom} : "
            f"{self.montant_restant}/{self.montant} DZD"
        )

    def save(self, *args, **kwargs):
        # Keep client/branche in sync with the originating paiement so
        # callers never need to pass them explicitly.
        if self.paiement_id:
            if not self.client_id:
                self.client_id = self.paiement.client_id
            if not self.branche_id:
                self.branche_id = self.paiement.branche_id
        super().save(*args, **kwargs)


class AllocationAcompteClient(models.Model):
    """
    Immutable line: portion of one AcompteClient consumed by one
    FactureClient. Created exclusively by
    clients.utils.consommer_acomptes_client_fifo. Never edited after
    creation.
    """

    acompte = models.ForeignKey(
        AcompteClient,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="الدفعة المسبقة",
    )
    facture = models.ForeignKey(
        FactureClient,
        on_delete=models.PROTECT,
        related_name="allocations_acompte",
        verbose_name="الفاتورة",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ المخصص (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تخصيص دفعة مسبقة (عميل)"
        verbose_name_plural = "تخصيصات الدفعات المسبقة (العملاء)"

    def __str__(self):
        return (
            f"{self.acompte} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )


# ---------------------------------------------------------------------------
# Abonnement Client — recurring/metered deliveries (mainly fertilizer, but
# usable for any ProduitFini sold on a recurring/quota basis)
# ---------------------------------------------------------------------------


class AbonnementClient(models.Model):
    """
    A recurring delivery agreement for one client/product pair — e.g. a
    monthly fertilizer quota. Distinct from BLClient (a one-shot delivery
    note): an AbonnementClient is fulfilled over time via many
    LivraisonPartielle records rather than a single document.
    """

    FREQUENCE_MENSUEL = "mensuel"
    FREQUENCE_PERSONNALISE = "personnalise"
    FREQUENCE_CHOICES = [
        (FREQUENCE_MENSUEL, "شهري"),
        (FREQUENCE_PERSONNALISE, "مخصص"),
    ]

    STATUT_ACTIF = "actif"
    STATUT_TERMINE = "termine"
    STATUT_SUSPENDU = "suspendu"
    STATUT_CHOICES = [
        (STATUT_ACTIF, "نشط"),
        (STATUT_TERMINE, "منتهٍ"),
        (STATUT_SUSPENDU, "معلّق"),
    ]

    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="abonnements",
        verbose_name="العميل",
    )
    # v1.4 — the recurring agreement is fulfilled out of one branche's
    # stock (BR-BRA-01); LivraisonPartielle below inherits it.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="abonnements_client",
        verbose_name="الفرع",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        related_name="abonnements",
        verbose_name="المنتج النهائي",
    )
    date_debut = models.DateField(verbose_name="تاريخ البدء")
    date_fin = models.DateField(
        null=True,
        blank=True,
        verbose_name="تاريخ الانتهاء",
        help_text="اتركه فارغاً لاشتراك مستمر بدون تاريخ نهاية محدد.",
    )
    frequence = models.CharField(
        max_length=20,
        choices=FREQUENCE_CHOICES,
        default=FREQUENCE_MENSUEL,
        verbose_name="التواتر",
    )
    quantite_totale_prevue = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="الكمية الإجمالية المتعاقد عليها",
        help_text="0 = بدون سقف كمية (تُتابع التسليمات بدون حد أقصى).",
    )
    prix_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        verbose_name="سعر الوحدة (د.ج)",
        validators=[MinValueValidator(0)],
    )
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_ACTIF,
        verbose_name="الحالة",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="abonnements_client_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "اشتراك عميل"
        verbose_name_plural = "اشتراكات العملاء"
        ordering = ["-date_debut"]

    def __str__(self):
        return f"{self.client.nom} — {self.produit_fini.designation} ({self.get_frequence_display()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.date_fin and self.date_debut and self.date_fin < self.date_debut:
            raise ValidationError(
                {"date_fin": "تاريخ الانتهاء يجب أن يكون بعد تاريخ البدء."}
            )

    @property
    def quantite_livree_cumulee(self):
        result = self.livraisons.aggregate(total=models.Sum("quantite_livree"))["total"]
        return result or Decimal("0")

    @property
    def solde_restant(self):
        """None when no quota is set — an unlimited/ongoing subscription."""
        if not self.quantite_totale_prevue:
            return None
        return self.quantite_totale_prevue - self.quantite_livree_cumulee

    @property
    def est_actif(self):
        return self.statut == self.STATUT_ACTIF


class VoyageLivraison(models.Model):
    """
    One truck trip that may serve several clients/subscriptions in a single
    run (e.g. a fertilizer delivery round). Purely organisational — the
    stock effect lives on LivraisonPartielle, not here.

    v1.4 note: intentionally left WITHOUT a `branche` FK. A single trip can
    in principle serve subscriptions from more than one branche (it is
    logistics, not a stock-impacting document); each LivraisonPartielle it
    covers carries its own branche (inherited from its abonnement) and
    that is what scopes the actual stock movement (BR-BRA-01).
    """

    date_voyage = models.DateField(verbose_name="تاريخ الرحلة")
    chauffeur = models.CharField(max_length=150, blank=True, verbose_name="السائق")
    vehicule = models.CharField(max_length=100, blank=True, verbose_name="المركبة")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voyages_livraison_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "رحلة توصيل"
        verbose_name_plural = "رحلات التوصيل"
        ordering = ["-date_voyage"]

    def __str__(self):
        return f"رحلة {self.date_voyage} — {self.chauffeur or 'بدون سائق محدد'}"

    @property
    def quantite_totale_livree(self):
        result = self.livraisons.aggregate(total=models.Sum("quantite_livree"))["total"]
        return result or Decimal("0")


class LivraisonPartielle(models.Model):
    """
    One metered delivery against an AbonnementClient.

    On creation, decreases StockProduitFini for the subscription's product
    and logs a StockMouvement (sortie) — same spirit as BLClientLigne, but
    for a recurring agreement instead of a one-shot BL. Records are
    immutable after creation (mirrors PaiementClientAllocation); deleting
    one reverses its stock effect (see clients/signals.py).
    """

    abonnement = models.ForeignKey(
        AbonnementClient,
        on_delete=models.PROTECT,
        related_name="livraisons",
        verbose_name="الاشتراك",
    )
    voyage = models.ForeignKey(
        VoyageLivraison,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="livraisons",
        verbose_name="رحلة التوصيل",
    )
    date = models.DateField(verbose_name="تاريخ التسليم")
    quantite_livree = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="الكمية المسلَّمة",
        validators=[MinValueValidator(0.001)],
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="livraisons_partielles_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تسليم جزئي"
        verbose_name_plural = "التسليمات الجزئية"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.abonnement} — {self.quantite_livree} ({self.date})"

    @property
    def branche(self):
        """v1.4 — inherited from the parent abonnement (BR-BRA-01), not stored."""
        return self.abonnement.branche if self.abonnement_id else None

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            self.abonnement_id
            and self.abonnement.statut != AbonnementClient.STATUT_ACTIF
        ):
            raise ValidationError(
                "Impossible d'enregistrer une livraison sur un abonnement non actif."
            )

        # Quota guard — only enforced when a quota is actually configured.
        if (
            self.abonnement_id
            and self.quantite_livree
            and self.abonnement.quantite_totale_prevue
        ):
            deja_livre = self.abonnement.quantite_livree_cumulee
            if self.pk:
                ancienne = (
                    LivraisonPartielle.objects.filter(pk=self.pk)
                    .values_list("quantite_livree", flat=True)
                    .first()
                )
                if ancienne is not None:
                    deja_livre -= ancienne
            if (
                deja_livre + self.quantite_livree
                > self.abonnement.quantite_totale_prevue
            ):
                raise ValidationError(
                    f"الكمية المسلَّمة الإجمالية ({deja_livre + self.quantite_livree}) "
                    f"تتجاوز الكمية المتعاقد عليها ({self.abonnement.quantite_totale_prevue})."
                )


# ---------------------------------------------------------------------------
# PrixMarche — daily egg market price history (dynamic pricing)
# ---------------------------------------------------------------------------


class PrixMarche(models.Model):
    """
    Historical market price for a ProduitFini on a specific date.

    Enables the «fiche des dettes» to compare the actual invoice price
    recorded on a BL to the prevailing market price on that delivery date,
    computing the margin (or discount) granted to the client.

    One record per (produit_fini, date) pair; later entries for the same
    pair overwrite in-app via update (enforce unique_together).

    v1.4 note: intentionally left WITHOUT a `branche` FK. The market price
    is an external reference value (what the wider market is charging),
    not a transaction the farm itself books — it stays global like
    CompanyInfo and the master-data catalogues (§3.5.3), so every branche
    compares its BL prices against the same market reference.
    """

    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        related_name="prix_marche",
        verbose_name="المنتج النهائي",
    )
    date = models.DateField(verbose_name="تاريخ السعر")
    prix_marche = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        verbose_name="سعر السوق (د.ج/وحدة)",
        validators=[MinValueValidator(0)],
    )
    source = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="المصدر",
        help_text="مثال: ONAB، السوق المحلي، إلخ.",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prix_marche_saisis",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سعر السوق"
        verbose_name_plural = "أسعار السوق"
        ordering = ["-date"]
        unique_together = [("produit_fini", "date")]

    def __str__(self):
        return (
            f"{self.produit_fini.designation} — {self.date} : "
            f"{self.prix_marche} د.ج"
        )

    @classmethod
    def get_price_on(cls, produit_fini, date):
        """
        Return the most recent market price on or before *date* for
        *produit_fini*, or None if no price has ever been recorded.
        """
        return (
            cls.objects.filter(produit_fini=produit_fini, date__lte=date)
            .order_by("-date")
            .values_list("prix_marche", flat=True)
            .first()
        )
