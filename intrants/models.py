"""
intrants/models.py

Master-data for the farm:
  - CategorieIntrant   : dynamic intrant categories (seeded via data migration)
  - TypeFournisseur    : dynamic supplier types    (seeded via data migration)
  - UniteMesure        : dynamic units of measure   (seeded via data migration)
                         shared by Intrant (here) and ProduitFini (production)
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
        verbose_name="الرمز",
        help_text="مفتاح ثابت: ALIMENT, POUSSIN, MEDICAMENT, AUTRE — لا تعيد تسميته.",
    )
    libelle = models.CharField(max_length=150, verbose_name="التسمية")
    # Whether items in this category are consumable inside a lot (feed/medicine)
    consommable_en_lot = models.BooleanField(
        default=False,
        verbose_name="قابل للاستهلاك في الدفعة",
        help_text="ضع علامة للفئات التي يمكن إدخالها في الاستهلاك.",
    )
    ordre = models.PositiveSmallIntegerField(default=0, verbose_name="ترتيب العرض")
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "فئة مدخل"
        verbose_name_plural = "فئات المدخلات"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class TypeFournisseur(models.Model):
    """
    User-manageable supplier types.
    Seeded: Aliments, Poussins, Médicaments/Vétérinaires, Services, Autre.
    """

    code = models.CharField(max_length=50, unique=True, verbose_name="الرمز")
    libelle = models.CharField(max_length=150, verbose_name="التسمية")
    ordre = models.PositiveSmallIntegerField(default=0, verbose_name="ترتيب العرض")
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "نوع المورد"
        verbose_name_plural = "أنواع الموردين"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class UniteMesure(models.Model):
    """
    Shared unit-of-measure master, used by both Intrant (this app) and
    ProduitFini (production app) — a single table so "kg" means the same
    row everywhere rather than two parallel hardcoded choice lists.

    Seeded: KG, SAC, UNITE, LITRE, FLACON, DOSE, ML, G, PLATEAU, CAISSE,
    PAQUET. The `code` field is the stable programmatic key; administrators
    may add units but should not rename the seed codes.
    """

    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="الرمز",
        help_text="مفتاح ثابت (مثال: KG, SAC, UNITE) — لا تعيد تسميته.",
    )
    libelle = models.CharField(max_length=100, verbose_name="التسمية")
    ordre = models.PositiveSmallIntegerField(default=0, verbose_name="ترتيب العرض")
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "وحدة قياس"
        verbose_name_plural = "وحدات القياس"
        ordering = ["ordre", "libelle"]

    def __str__(self):
        return self.libelle


class CategorieQualite(models.Model):
    """
    User-manageable quality-grading brackets, keyed by average sample weight.

    Used to grade both live-bird condition and egg quality from
    PeseeEchantillon (elevage app): a weight in [poids_min, poids_max]
    resolves to this bracket. Two independent scales are kept (oiseaux /
    oeufs) since the meaningful weight ranges differ completely.
    """

    TYPE_OISEAUX = "oiseaux"
    TYPE_OEUFS = "oeufs"
    TYPE_CHOICES = [
        (TYPE_OISEAUX, "طيور"),
        (TYPE_OEUFS, "بيض"),
    ]

    code = models.CharField(max_length=50, verbose_name="الرمز")
    libelle = models.CharField(max_length=150, verbose_name="التسمية")
    type_pesee = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        verbose_name="نوع القياس",
        help_text="هل هذه الفئة لتصنيف الطيور أم البيض؟",
    )
    poids_min = models.DecimalField(
        max_digits=8, decimal_places=2, verbose_name="الوزن الأدنى (غ)"
    )
    poids_max = models.DecimalField(
        max_digits=8, decimal_places=2, verbose_name="الوزن الأقصى (غ)"
    )
    ordre = models.PositiveSmallIntegerField(default=0, verbose_name="ترتيب العرض")
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "فئة جودة"
        verbose_name_plural = "فئات الجودة"
        ordering = ["type_pesee", "ordre"]
        unique_together = [("code", "type_pesee")]

    def __str__(self):
        return f"{self.get_type_pesee_display()} — {self.libelle} ({self.poids_min}-{self.poids_max} غ)"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.poids_min is not None and self.poids_max is not None:
            if self.poids_min >= self.poids_max:
                raise ValidationError("الوزن الأدنى يجب أن يكون أصغر من الوزن الأقصى.")


# ---------------------------------------------------------------------------
# Fournisseur
# ---------------------------------------------------------------------------


class Fournisseur(models.Model):
    """
    Supplier master record.  Referenced by BL Fournisseur, Facture Fournisseur,
    Règlement Fournisseur, and the Intrant catalogue.
    """

    nom = models.CharField(max_length=255, verbose_name="اسم المورد")
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
    type_principal = models.ForeignKey(
        TypeFournisseur,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fournisseurs",
        verbose_name="النوع الرئيسي",
    )
    actif = models.BooleanField(default=True, verbose_name="نشط")
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fournisseurs_crees",
        verbose_name="أنشئ بواسطة",
        help_text=(
            "يُملأ تلقائياً عند الإنشاء. يُستخدم لتقييد رؤية حساب السائق "
            "على الموردين الذين أنشأهم بنفسه فقط."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مورد"
        verbose_name_plural = "الموردون"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    # ------------------------------------------------------------------
    # Financial helpers (delegated to achats app via reverse relations)
    #
    # v1.4 — Fournisseur itself stays global (§3.5.3: a supplier can
    # transact with several branches), but BLFournisseur/FactureFournisseur/
    # ReglementFournisseur/AcompteFournisseur are branch-scoped. So
    # `dette_globale` / `acompte_disponible` below are the Vue Globale
    # figures (sum across all branches, §3.5.3 ¶4); pass `branche` to get
    # the figure for one branch only, exactly as a chef de branche sees it.
    # ------------------------------------------------------------------
    def dette_globale(self, branche=None):
        """
        Sum of *reste_a_payer* across all Non Payé and Partiellement Payé
        factures fournisseurs.  Computed on-demand; cache at view layer.
        Pass `branche` to scope to one branch; omit for Vue Globale.
        """
        from achats.models import FactureFournisseur

        qs = self.factures_fournisseur.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ]
        )
        if branche is not None:
            qs = qs.filter(branche=branche)
        total = qs.aggregate(total=models.Sum("reste_a_payer"))["total"]
        return total or 0

    def acompte_disponible(self, branche=None):
        qs = self.acomptes.filter(utilise=False)
        if branche is not None:
            qs = qs.filter(branche=branche)
        result = qs.aggregate(total=models.Sum("montant"))["total"]
        return result or 0

    # Backwards-compatible properties — Vue Globale (all branches summed).
    @property
    def dette_globale_toutes_branches(self):
        return self.dette_globale()

    @property
    def acompte_disponible_toutes_branches(self):
        return self.acompte_disponible()


# ---------------------------------------------------------------------------
# Batiment
# ---------------------------------------------------------------------------


class Batiment(models.Model):
    """
    Physical building on the farm.

    Three operational kinds:
      - poussiniere : brooding house for young chicks (LotElevage opens here).
      - poulailler  : grow-out / laying house (chicks are transferred here once
                      they pass the age threshold — see elevage.TransfertLot).
      - entrepot    : non-rearing storage (eggs in plateaux, or sanitized
                      fertilizer awaiting truck pickup) — categorie_stockage
                      distinguishes which.

    Each *lot d'élevage* is assigned to one building at a time; it moves
    from poussiniere → poulailler via a TransfertLot record, not by editing
    the lot directly, so the move is auditable.
    """

    TYPE_POUSSINIERE = "poussiniere"
    TYPE_POULAILLER = "poulailler"
    TYPE_ENTREPOT = "entrepot"
    TYPE_CHOICES = [
        (TYPE_POUSSINIERE, "حضانة كتاكيت (Poussinière)"),
        (TYPE_POULAILLER, "حظيرة دجاج (تربية / بيض)"),
        (TYPE_ENTREPOT, "مستودع تخزين"),
    ]

    STOCKAGE_OEUFS = "oeufs"
    STOCKAGE_FERTILISANT = "fertilisant"
    STOCKAGE_CHOICES = [
        (STOCKAGE_OEUFS, "بيض / أطباق"),
        (STOCKAGE_FERTILISANT, "سماد معالج"),
    ]

    nom = models.CharField(max_length=100, verbose_name="الاسم / الرقم")
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="batiments",
        verbose_name="الفرع",
        help_text="الفرع الذي ينتمي إليه هذا المبنى (BR-BRA-01) — لا يتغير بعد الإنشاء عملياً.",
    )
    type_batiment = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_POULAILLER,
        verbose_name="نوع المبنى",
    )
    categorie_stockage = models.CharField(
        max_length=20,
        choices=STOCKAGE_CHOICES,
        blank=True,
        verbose_name="نوع التخزين (للمستودعات فقط)",
        help_text="يُملأ فقط عندما يكون نوع المبنى = مستودع.",
    )
    capacite = models.PositiveIntegerField(
        verbose_name="الطاقة الاستيعابية (رأس)", null=True, blank=True
    )
    description = models.TextField(verbose_name="الوصف", blank=True)
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "مبنى"
        verbose_name_plural = "المباني"
        ordering = ["branche", "nom"]
        unique_together = [("branche", "nom")]

    def __str__(self):
        return f"{self.nom} ({self.get_type_batiment_display()}) — {self.branche.code}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.type_batiment == self.TYPE_ENTREPOT and not self.categorie_stockage:
            raise ValidationError(
                {
                    "categorie_stockage": "مطلوب تحديد نوع التخزين عندما يكون المبنى مستودعاً."
                }
            )
        if self.type_batiment != self.TYPE_ENTREPOT and self.categorie_stockage:
            raise ValidationError(
                {
                    "categorie_stockage": "اترك هذا الحقل فارغاً إلا إذا كان المبنى مستودعاً."
                }
            )


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

    `unite_mesure` is a FK to UniteMesure (shared with production.ProduitFini).
    Code that previously compared unite_mesure == "kg" must now compare
    unite_mesure.code == "KG".
    """

    STADE_DEMARRAGE = "demarrage"
    STADE_CROISSANCE = "croissance"
    STADE_PONTE = "ponte"
    STADE_TOUS = "tous"
    STADE_CHOICES = [
        (STADE_DEMARRAGE, "بداية (كتاكيت / حضانة)"),
        (STADE_CROISSANCE, "نمو (دجاج لاحم)"),
        (STADE_PONTE, "بيّاض (دجاج بيّاض)"),
        (STADE_TOUS, "كل المراحل"),
    ]

    designation = models.CharField(max_length=255, verbose_name="التسمية")
    categorie = models.ForeignKey(
        CategorieIntrant,
        on_delete=models.PROTECT,
        related_name="intrants",
        verbose_name="الفئة",
    )
    stade = models.CharField(
        max_length=20,
        choices=STADE_CHOICES,
        default=STADE_TOUS,
        verbose_name="مرحلة الاستخدام",
        help_text="يحدد ما إذا كان هذا المدخل مخصصاً للكتاكيت (حضانة) أو الدجاج البالغ (نمو/بيّاض).",
    )
    unite_mesure = models.ForeignKey(
        UniteMesure,
        on_delete=models.PROTECT,
        related_name="intrants",
        verbose_name="وحدة القياس",
    )
    # Suppliers that provide this intrant (informational M2M)
    fournisseurs = models.ManyToManyField(
        Fournisseur,
        blank=True,
        related_name="intrants",
        verbose_name="الموردون المرتبطون",
    )
    seuil_alerte = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="حد تنبيه المخزون",
        default=0,
        help_text="تنبيه إذا انخفض المخزون عن هذا الحد.",
    )
    actif = models.BooleanField(default=True, verbose_name="نشط")
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مدخل"
        verbose_name_plural = "المدخلات"
        ordering = ["categorie__libelle", "designation"]

    def __str__(self):
        return f"{self.categorie.libelle} — {self.designation}"

    # ------------------------------------------------------------------
    # v1.4 — StockIntrant is now one row per (branche, intrant) (BR-BRA-07),
    # not one row per intrant. `quantite_en_stock`/`en_alerte` below take an
    # optional `branche` to read one branch's balance (chef de branche view);
    # omit it for the Vue Globale total across every branch.
    # ------------------------------------------------------------------
    def quantite_en_stock(self, branche=None):
        if branche is not None:
            try:
                return self.stocks.get(branche=branche).quantite
            except Exception:
                return 0
        result = self.stocks.aggregate(total=models.Sum("quantite"))["total"]
        return result or 0

    def en_alerte(self, branche=None):
        if branche is not None:
            row = self.stocks.filter(branche=branche).first()
            return row.en_alerte if row else False
        return self.quantite_en_stock() <= self.seuil_alerte

    @property
    def est_consommable_en_lot(self):
        """True when the intrant's category is flagged as lot-consumable."""
        return self.categorie.consommable_en_lot
