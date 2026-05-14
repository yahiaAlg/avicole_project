"""
depenses/models.py

Operational expense tracking, strictly separated from accounts payable (AP).

Key business rules:
  BR-DEP-01  A facture fournisseur for goods NEVER auto-generates a dépense.
  BR-DEP-02  AP and dépenses draw from mutually exclusive data sources.
  BR-DEP-03  A dépense may OPTIONALLY link to a Service-type supplier invoice —
             only by explicit user action; never automatically.
  BR-DEP-04  Dépenses may optionally be attributed to a specific lot for
             per-lot profitability calculations.
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings


class CategorieDepense(models.Model):
    """
    User-managed expense categories (salaire, énergie, maintenance, etc.).

    A set of common categories is pre-loaded via a data migration, but
    administrators can add, rename, or deactivate categories freely.
    The `code` field provides a stable programmatic key for any future
    integrations or reports.
    """

    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="الرمز",
        help_text="معرف قصير فريد، مثال: ENERGIE, MAINTENANCE.",
    )
    libelle = models.CharField(max_length=150, verbose_name="التسمية")
    description = models.TextField(blank=True, verbose_name="الوصف")
    actif = models.BooleanField(default=True, verbose_name="نشط")
    # Display order in dropdowns
    ordre = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="ترتيب العرض",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "فئة المصروف"
        verbose_name_plural = "فئات المصاريف"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class Depense(models.Model):
    """
    A single operational expense record.

    Two optional foreign keys enrich a dépense for reporting purposes:
      - `lot`             : attributes the cost to a specific production lot
                            (used in per-lot profitability calculations – BR-DEP-04).
      - `facture_liee`    : links to a Service-type FactureFournisseur when
                            the user explicitly makes that connection
                            (BR-DEP-03); NEVER populated automatically.

    The `facture_liee` FK is constrained via a DB check in the view/form
    layer to only allow Service-type invoices (BR-DEP-03).

    Records are not soft-deleted; incorrect entries should be cancelled via
    a corrective entry with a negative amount or notes explaining the reversal
    (rare edge case — normal workflow is delete within the same business day
    before any period close).  Administrators may hard-delete via the admin.
    """

    MODE_ESPECES = "especes"
    MODE_CHEQUE = "cheque"
    MODE_VIREMENT = "virement"
    MODE_CARTE = "carte"
    MODE_AUTRE = "autre"

    MODE_CHOICES = [
        (MODE_ESPECES, "نقداً"),
        (MODE_CHEQUE, "شيك"),
        (MODE_VIREMENT, "تحويل بنكي"),
        (MODE_CARTE, "بطاقة بنكية"),
        (MODE_AUTRE, "أخرى"),
    ]

    date = models.DateField(verbose_name="تاريخ المصروف")

    categorie = models.ForeignKey(
        CategorieDepense,
        on_delete=models.PROTECT,
        related_name="depenses",
        verbose_name="الفئة",
    )

    description = models.CharField(
        max_length=500,
        verbose_name="الوصف / الموضوع",
    )

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

    reference_document = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="مرجع الوثيقة (فاتورة / وصل)",
        help_text="رقم الوثيقة المثبتة الورقية.",
    )

    piece_jointe = models.FileField(
        upload_to="depenses/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="مرفق (PDF/JPG/PNG)",
    )

    # Optional lot attribution (BR-DEP-04)
    lot = models.ForeignKey(
        "elevage.LotElevage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses",
        verbose_name="الدفعة المخصصة",
        help_text="اختياري — لحساب الربحية لكل دفعة.",
    )

    # Optional service-invoice link (BR-DEP-03) — NEVER auto-populated.
    facture_liee = models.ForeignKey(
        "achats.FactureFournisseur",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_liees",
        verbose_name="فاتورة المورد المرتبطة (خدمة فقط)",
        help_text=(
            "للفواتير من نوع الخدمة فقط. " "لا تربط أبداً بفاتورة بضائع (BR-DEP-01)."
        ),
    )

    notes = models.TextField(blank=True, verbose_name="ملاحظات")

    enregistre_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_enregistrees",
        verbose_name="مسجّل من قبل",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مصروف"
        verbose_name_plural = "مصاريف"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return (
            f"{self.date} | {self.categorie.libelle} | "
            f"{self.description[:60]} | {self.montant} DZD"
        )

    @property
    def a_piece_jointe(self):
        return bool(self.piece_jointe)
