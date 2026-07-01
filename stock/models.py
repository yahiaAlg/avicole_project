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
    Current balance for one Intrant **within one Branche** (v1.4, BR-BRA-07).

    Pre-v1.4 this was a One-to-One on `intrant`; now the same catalogue
    item can have a different quantity and weighted-average cost in each
    branch, so the unique key becomes (branche, intrant). The balance is
    never edited directly — it is updated exclusively through
    StockMouvement records triggered by validated BL Fournisseur,
    Consommation, and StockAjustement events, all scoped to this branche.
    """

    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="stocks_intrants",
        verbose_name="الفرع",
    )
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.CASCADE,
        related_name="stocks",
        verbose_name="المدخل",
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="الكمية في المخزون",
    )
    # Weighted average cost — updated each time a BL line is validated.
    prix_moyen_pondere = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        verbose_name="متوسط التكلفة الموزون (د.ج)",
    )
    derniere_mise_a_jour = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    class Meta:
        verbose_name = "مخزون المدخلات"
        verbose_name_plural = "مخازن المدخلات"
        constraints = [
            models.UniqueConstraint(
                fields=["branche", "intrant"], name="unique_stock_intrant_par_branche"
            )
        ]

    def __str__(self):
        return (
            f"{self.intrant.designation} [{self.branche.code}] — "
            f"{self.quantite} {self.intrant.unite_mesure}"
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
    Current balance for one ProduitFini **within one Branche** (v1.4, BR-BRA-07).

    Pre-v1.4 this was a One-to-One on `produit_fini`; now the same
    finished-product type can sit at a different quantity and average
    production cost in each branch, so the unique key becomes
    (branche, produit_fini). Increases via validated ProductionRecord
    lines for that branch. Decreases via validated BL Client lines for
    that branch.
    """

    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="stocks_produits_finis",
        verbose_name="الفرع",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.CASCADE,
        related_name="stocks",
        verbose_name="المنتج النهائي",
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="الكمية في المخزون",
    )
    cout_moyen_production = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=0,
        verbose_name="متوسط تكلفة الإنتاج (د.ج)",
    )
    seuil_alerte = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        verbose_name="حد التنبيه",
    )
    derniere_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مخزون المنتج النهائي"
        verbose_name_plural = "مخازن المنتجات النهائية"
        constraints = [
            models.UniqueConstraint(
                fields=["branche", "produit_fini"],
                name="unique_stock_produit_fini_par_branche",
            )
        ]

    def __str__(self):
        return (
            f"{self.produit_fini.designation} [{self.branche.code}] — "
            f"{self.quantite} {self.produit_fini.unite_mesure}"
        )

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
        (TYPE_ENTREE, "إدخال"),
        (TYPE_SORTIE, "إخراج"),
        (TYPE_AJUSTEMENT, "تسوية"),
    ]

    SOURCE_BL_FOURNISSEUR = "bl_fournisseur"
    SOURCE_CONSOMMATION = "consommation"
    SOURCE_PRODUCTION = "production"
    SOURCE_BL_CLIENT = "bl_client"
    SOURCE_AJUSTEMENT = "ajustement"
    SOURCE_MORTALITE = "mortalite"
    SOURCE_PONTE = "ponte"
    SOURCE_FERTILISANT = "fertilisant"
    SOURCE_LIVRAISON_ABONNEMENT = "livraison_abonnement"

    SOURCE_CHOICES = [
        (SOURCE_BL_FOURNISSEUR, "وصل تسليم المورد"),
        (SOURCE_CONSOMMATION, "استهلاك الدفعة"),
        (SOURCE_PRODUCTION, "الإنتاج"),
        (SOURCE_BL_CLIENT, "وصل تسليم العميل"),
        (SOURCE_AJUSTEMENT, "تسوية يدوية"),
        (SOURCE_MORTALITE, "نفوق (خصم كتاكيت)"),
        (SOURCE_PONTE, "إنتاج البيض (ponte)"),
        (SOURCE_FERTILISANT, "معالجة السماد"),
        (SOURCE_LIVRAISON_ABONNEMENT, "تسليم اشتراك عميل"),
    ]

    # v1.4 — quantite_avant/quantite_apres now refer to that branche's
    # StockIntrant/StockProduitFini row, not a single farm-wide balance
    # (BR-BRA-07). Required and explicit (not derived) since this is an
    # immutable audit record that must stay correct even if the source
    # document is later reassigned.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="mouvements_stock",
        verbose_name="الفرع",
    )

    # Target stock item — exactly one must be set.
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mouvements",
        verbose_name="المدخل",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mouvements",
        verbose_name="المنتج النهائي",
    )

    type_mouvement = models.CharField(
        max_length=20, choices=TYPE_CHOICES, verbose_name="النوع"
    )
    source = models.CharField(
        max_length=30, choices=SOURCE_CHOICES, verbose_name="المصدر"
    )
    quantite = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        verbose_name="الكمية",
        help_text="دائماً موجبة؛ الاتجاه يُحدده نوع الحركة.",
    )
    quantite_avant = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="المخزون قبل"
    )
    quantite_apres = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="المخزون بعد"
    )
    date_mouvement = models.DateField(verbose_name="تاريخ الحركة")
    # Soft references to source documents (store PK as string for flexibility)
    reference_id = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="معرف الوثيقة المصدر"
    )
    reference_label = models.CharField(
        max_length=100, blank=True, verbose_name="مرجع الوثيقة"
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "حركة مخزون"
        verbose_name_plural = "حركات المخزون"
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
        (SEGMENT_INTRANT, "مخزون المدخلات"),
        (SEGMENT_PRODUIT_FINI, "مخزون المنتجات النهائية"),
    ]

    segment = models.CharField(
        max_length=20, choices=SEGMENT_CHOICES, verbose_name="قطاع المخزون"
    )
    # v1.4 — identifies which branch's StockIntrant/StockProduitFini row is
    # being corrected (BR-BRA-07). Required and explicit for the same
    # immutability reason as StockMouvement.branche above.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="ajustements_stock",
        verbose_name="الفرع",
    )
    # Exactly one of the two FKs below is populated.
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ajustements",
        verbose_name="المدخل",
    )
    produit_fini = models.ForeignKey(
        "production.ProduitFini",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ajustements",
        verbose_name="المنتج النهائي",
    )
    date_ajustement = models.DateField(verbose_name="تاريخ التسوية")
    quantite_avant = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="الكمية قبل التسوية"
    )
    quantite_apres = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="الكمية بعد التسوية"
    )
    raison = models.TextField(
        verbose_name="السبب / المبرر",
        help_text="إلزامي — صف الفرق المُلاحظ.",
    )
    effectue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ajustements_stock",
        verbose_name="منفذ من قبل",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تسوية مخزون"
        verbose_name_plural = "تسويات المخزون"
        ordering = ["-date_ajustement"]

    def __str__(self):
        item = self.intrant or self.produit_fini
        delta = self.quantite_apres - self.quantite_avant
        sign = "+" if delta >= 0 else ""
        return f"Ajustement {item} : {sign}{delta} ({self.date_ajustement})"
