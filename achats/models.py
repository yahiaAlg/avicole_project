"""
achats/models.py

Supplier procurement cycle:
  BLFournisseur → FactureFournisseur → ReglementFournisseur (FIFO)
  AcompteFournisseur captures overpayment surplus.
"""

import datetime
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from core.models import PieceJointe


class BLFournisseur(models.Model):
    # ------------------------------------------------------------------
    # Document type — classic BL vs supplier access authorization
    # (e.g. ONAB "Autorisation d'accès" issued before port pickup)
    # ------------------------------------------------------------------
    TYPE_BL_CLASSIQUE = "bl_classique"
    TYPE_AUTORISATION_ACCES = "autorisation_acces"

    TYPE_DOCUMENT_CHOICES = [
        (TYPE_BL_CLASSIQUE, "وصل تسليم كلاسيكي"),
        (TYPE_AUTORISATION_ACCES, "تفويض وصول (ONAB وما شابه)"),
    ]

    # ------------------------------------------------------------------
    # Statut — AUTORISE is exclusive to TYPE_AUTORISATION_ACCES and
    # represents an issued authorization that has not yet been picked up.
    # Transition: AUTORISE → RECU triggers the same stock entry as usual.
    # ------------------------------------------------------------------
    STATUT_AUTORISE = "autorise"
    STATUT_BROUILLON = "brouillon"
    STATUT_RECU = "recu"
    STATUT_FACTURE = "facture"
    STATUT_LITIGE = "litige"

    STATUT_CHOICES = [
        (STATUT_AUTORISE, "مفوَّض (في انتظار الاستلام)"),
        (STATUT_BROUILLON, "مسودة"),
        (STATUT_RECU, "مستلم"),
        (STATUT_FACTURE, "مفوتر"),
        (STATUT_LITIGE, "في نزاع"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="مرجع وصل التسليم"
    )
    # v1.4 — Fournisseur stays global (§3.5.3), but the delivery note
    # itself belongs to exactly one branche (BR-BRA-01): the goods land
    # in that branche's StockIntrant. Set explicitly at creation — the
    # chef de branche's own branche, or chosen by an admin.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="bls_fournisseur",
        verbose_name="الفرع",
    )
    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="bls_fournisseur",
        verbose_name="المورد",
    )
    date_bl = models.DateField(verbose_name="تاريخ وصل التسليم / تاريخ التفويض")
    reference_fournisseur = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="مرجع المورد",
        help_text="للتفويض: رقم أمر الشراء عند المورد (مثال: C632/15).",
    )
    type_document = models.CharField(
        max_length=25,
        choices=TYPE_DOCUMENT_CHOICES,
        default=TYPE_BL_CLASSIQUE,
        verbose_name="نوع الوثيقة",
    )
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="الحالة",
    )
    notes_reception = models.TextField(blank=True, verbose_name="ملاحظات الاستلام")
    # v1.5 — replaced the single `piece_jointe` FileField with the generic
    # PieceJointe model (core.models) so a BL can carry several proofs
    # (scanned BL + photo of the truck gate pass, etc.).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="bl_fournisseur"
    )

    # ------------------------------------------------------------------
    # Autorisation d'accès fields — only populated when
    # type_document == TYPE_AUTORISATION_ACCES
    # ------------------------------------------------------------------
    numero_autorisation = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="رقم التفويض",
        help_text="الرقم الصادر من المورد (مثال: 27196 لـ ONAB).",
    )
    date_expiration_autorisation = models.DateField(
        null=True,
        blank=True,
        verbose_name="تاريخ انتهاء التفويض",
        help_text="يجب استلام البضاعة قبل هذا التاريخ (BR-BLF-05).",
    )
    nom_chauffeur = models.CharField(
        max_length=150, blank=True, verbose_name="اسم السائق"
    )
    matricule_camion = models.CharField(
        max_length=50, blank=True, verbose_name="رقم تسجيل الشاحنة"
    )
    numero_permis = models.CharField(
        max_length=50, blank=True, verbose_name="رقم رخصة القيادة"
    )
    portail_entree = models.CharField(
        max_length=50, blank=True, verbose_name="بوابة الدخول"
    )
    portail_sortie = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="بوابة الخروج",
        help_text="يُملأ عند خروج الشاحنة محملة — يؤكد اكتمال الاستلام الفعلي.",
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
        verbose_name = "وصل تسليم المورد"
        verbose_name_plural = "وصولات تسليم المورد"
        ordering = ["-date_bl", "-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.fournisseur.nom} ({self.date_bl})"

    @property
    def montant_total(self):
        return sum(ligne.montant_total for ligne in self.lignes.all())

    @property
    def a_piece_jointe(self):
        return self.pieces_jointes.exists()

    @property
    def est_verrouille(self):
        """Locked BLs cannot be edited or re-invoiced (BR-BLF-02)."""
        return self.statut == self.STATUT_FACTURE

    @property
    def est_expire(self):
        """
        BR-BLF-05: True when an autorisation_acces is past its expiry date
        and the goods have not yet been picked up (statut still AUTORISE).
        Expired authorizations cannot be confirmed as RECU.
        """
        return (
            self.type_document == self.TYPE_AUTORISATION_ACCES
            and self.statut == self.STATUT_AUTORISE
            and self.date_expiration_autorisation is not None
            and datetime.date.today() > self.date_expiration_autorisation
        )

    @property
    def est_autorisation_acces(self):
        return self.type_document == self.TYPE_AUTORISATION_ACCES


class BLFournisseurLigne(models.Model):
    bl = models.ForeignKey(
        BLFournisseur,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="وصل تسليم المورد",
    )
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="lignes_bl_fournisseur",
        verbose_name="المدخل",
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
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")

    class Meta:
        verbose_name = "سطر وصل تسليم المورد"
        verbose_name_plural = "أسطر وصل تسليم المورد"

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
        (STATUT_NON_PAYE, "غير مدفوعة"),
        (STATUT_PARTIELLEMENT_PAYE, "مدفوعة جزئياً"),
        (STATUT_PAYE, "مدفوعة"),
        (STATUT_EN_LITIGE, "في نزاع"),
    ]

    TYPE_MARCHANDISES = "marchandises"
    TYPE_SERVICE = "service"

    TYPE_CHOICES = [
        (TYPE_MARCHANDISES, "بضائع"),
        (TYPE_SERVICE, "خدمة"),
    ]

    reference = models.CharField(
        max_length=50, unique=True, verbose_name="مرجع الفاتورة"
    )
    # v1.4 — must match the branche of every BL included in `bls` below
    # (enforced at the view/M2M-assignment layer, since `bls` can only be
    # validated once this record has a pk — BR-BRA-01).
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="factures_fournisseur",
        verbose_name="الفرع",
    )
    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="factures_fournisseur",
        verbose_name="المورد",
    )
    # BLs included in this invoice — set at creation; locked afterwards (BR-FAF-03)
    bls = models.ManyToManyField(
        BLFournisseur,
        blank=True,
        related_name="factures",
        verbose_name="وصولات التسليم المضمنة",
    )
    date_facture = models.DateField(verbose_name="تاريخ الفاتورة")
    date_echeance = models.DateField(
        null=True, blank=True, verbose_name="تاريخ الاستحقاق"
    )
    type_facture = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_MARCHANDISES,
        verbose_name="نوع الفاتورة",
    )
    # Auto-computed from BL lines at invoice creation (BR-FAF-01); stored for performance.
    montant_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ الإجمالي (د.ج)",
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
        default=STATUT_NON_PAYE,
        verbose_name="الحالة",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="factures_fournisseur_creees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # v1.5 — proof documents (scanned invoice, credit note, ...).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="facture_fournisseur"
    )

    class Meta:
        verbose_name = "فاتورة المورد"
        verbose_name_plural = "فواتير الموردين"
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
        (MODE_ESPECES, "نقداً"),
        (MODE_CHEQUE, "شيك"),
        (MODE_VIREMENT, "تحويل بنكي"),
        (MODE_AUTRE, "أخرى"),
    ]

    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="reglements",
        verbose_name="المورد",
    )
    # v1.4 — the FIFO engine (achats.utils.appliquer_reglement_fifo) must
    # only allocate this règlement across FactureFournisseur rows in the
    # SAME branche (BR-BRA-01) — a payment recorded in one branch cannot
    # silently settle another branch's invoices.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="reglements_fournisseur",
        verbose_name="الفرع",
    )
    date_reglement = models.DateField(verbose_name="تاريخ التسوية")
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
        related_name="reglements_fournisseur_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.5 — proof of payment (bank transfer confirmation, cheque scan, ...).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="reglement_fournisseur"
    )

    class Meta:
        verbose_name = "تسوية المورد"
        verbose_name_plural = "تسويات الموردين"
        ordering = ["-date_reglement", "-created_at"]

    def __str__(self):
        return (
            f"تسوية {self.fournisseur.nom} — "
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
        verbose_name="التسوية",
    )
    facture = models.ForeignKey(
        FactureFournisseur,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="الفاتورة",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ المخصص (د.ج)",
    )

    class Meta:
        verbose_name = "تخصيص التسوية"
        verbose_name_plural = "تخصيصات التسويات"

    def __str__(self):
        return (
            f"{self.reglement} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )

    def clean(self):
        from django.core.exceptions import ValidationError

        # v1.4 — the FIFO engine must only allocate within the same
        # branche (BR-BRA-01); mirrors PaiementClientAllocation.clean().
        if (
            self.reglement_id
            and self.facture_id
            and self.reglement.branche_id != self.facture.branche_id
        ):
            raise ValidationError(
                "BR-BRA-01 : le règlement et la facture doivent appartenir "
                "à la même branche."
            )


class AcompteFournisseur(models.Model):
    """
    Advance payment / overpayment surplus credited to the supplier for future
    invoices (BR-REG-04, BR-REG-07). Created automatically by the FIFO engine
    either from a règlement's surplus (once open invoices are covered) or, for
    an explicit "paiement anticipé" (a règlement recorded when the supplier
    has no open debt at all — e.g. a cheque handed over before any facture
    exists), from the entire règlement amount.

    Consumed incrementally (BR-REG-07): every time a new FactureFournisseur is
    created for the same fournisseur + branche, achats.utils.consommer_acomptes_fifo
    draws down the oldest unused acomptes first, one invoice at a time, via
    AllocationAcompte records — mirroring the règlement→facture FIFO engine.
    `montant_restant` tracks what's left; `utilise` becomes True once it hits 0.
    """

    fournisseur = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="acomptes",
        verbose_name="المورد",
    )
    # v1.4 — credited against this branche only; auto-synced from the
    # source règlement's branche in save() when one is set (BR-BRA-01).
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="acomptes_fournisseur",
        verbose_name="الفرع",
    )
    reglement = models.OneToOneField(
        ReglementFournisseur,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acompte",
        verbose_name="التسوية المصدر",
    )
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    date = models.DateField(verbose_name="التاريخ")
    # BR-REG-07: how much of `montant` is still unconsumed. Set to `montant`
    # on creation and decremented as consommer_acomptes_fifo() draws it down
    # against new factures. `utilise` flips to True once this hits 0.
    montant_restant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name="المبلغ المتبقي (د.ج)",
        validators=[MinValueValidator(0)],
    )
    utilise = models.BooleanField(default=False, verbose_name="مستخدمة بالكامل")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.5 — proof (e.g. reçu confirming the surplus), esp. when `reglement`
    # is left blank (no source règlement to inherit proof from).
    pieces_jointes = GenericRelation(
        PieceJointe, related_query_name="acompte_fournisseur"
    )

    class Meta:
        verbose_name = "دفعة مقدمة للمورد"
        verbose_name_plural = "دفعات مقدمة للموردين"
        ordering = ["-date"]

    def __str__(self):
        status = "مستخدمة" if self.utilise else "قيد الانتظار"
        return f"Acompte {self.fournisseur.nom} — {self.montant} DZD [{status}]"

    def save(self, *args, **kwargs):
        if self.reglement_id:
            self.branche_id = self.reglement.branche_id
        if self.pk is None and self.montant_restant is None:
            self.montant_restant = self.montant
        super().save(*args, **kwargs)


class AllocationAcompte(models.Model):
    """
    Immutable line: portion of one AcompteFournisseur (advance/prepayment)
    consumed against one FactureFournisseur (BR-REG-07). Created exclusively
    by achats.utils.consommer_acomptes_fifo, triggered whenever a new facture
    is created for a fournisseur that still holds unused advances — mirrors
    AllocationReglement, but for prepayments instead of post-invoice règlements.
    """

    acompte = models.ForeignKey(
        AcompteFournisseur,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="الدفعة المقدمة",
    )
    facture = models.ForeignKey(
        FactureFournisseur,
        on_delete=models.PROTECT,
        related_name="allocations_acompte",
        verbose_name="الفاتورة",
    )
    montant_alloue = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ المخصص (د.ج)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تخصيص الدفعة المقدمة"
        verbose_name_plural = "تخصيصات الدفعات المقدمة"

    def __str__(self):
        return (
            f"{self.acompte} → {self.facture.reference} : "
            f"{self.montant_alloue} DZD"
        )

    def clean(self):
        from django.core.exceptions import ValidationError

        # BR-BRA-01 : the advance and the invoice it funds must belong to
        # the same branche, mirroring AllocationReglement.clean().
        if (
            self.acompte_id
            and self.facture_id
            and self.acompte.branche_id != self.facture.branche_id
        ):
            raise ValidationError(
                "BR-BRA-01 : l'avance et la facture doivent appartenir "
                "à la même branche."
            )
