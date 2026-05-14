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

    nom = models.CharField(max_length=100, verbose_name="الاسم / الرقم")
    capacite = models.PositiveIntegerField(
        verbose_name="الطاقة الاستيعابية (رأس)", null=True, blank=True
    )
    description = models.TextField(verbose_name="الوصف", blank=True)
    actif = models.BooleanField(default=True, verbose_name="نشط")

    class Meta:
        verbose_name = "مبنى"
        verbose_name_plural = "المباني"
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
        ("kg", "كيلوغرام (كغ)"),
        ("sac", "كيس (25 كغ)"),
        ("unite", "وحدة / رأس"),
        ("litre", "لتر"),
        ("flacon", "قارورة"),
        ("dose", "جرعة"),
        ("ml", "مليلتر (مل)"),
        ("g", "غرام (غ)"),
    ]

    designation = models.CharField(max_length=255, verbose_name="التسمية")
    categorie = models.ForeignKey(
        CategorieIntrant,
        on_delete=models.PROTECT,
        related_name="intrants",
        verbose_name="الفئة",
    )
    unite_mesure = models.CharField(
        max_length=20,
        choices=UNITE_CHOICES,
        verbose_name="وحدة القياس",
        default="kg",
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
