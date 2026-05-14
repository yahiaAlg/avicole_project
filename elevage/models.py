"""
elevage/models.py

Central production domain.  A LotElevage (poultry batch) is the unit around
which all daily operations — mortality, feed consumption, medicine
administration — are organised.
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings


class LotElevage(models.Model):
    """
    A single cohort of birds raised together from arrival to harvest.
    Everything operational is tracked at lot level.
    """

    STATUT_OUVERT = "ouvert"
    STATUT_FERME = "ferme"
    STATUT_CHOICES = [
        (STATUT_OUVERT, "مفتوح"),
        (STATUT_FERME, "مغلق"),
    ]

    designation = models.CharField(
        max_length=255,
        verbose_name="تسمية الدفعة",
        help_text='مثال: "دفعة أبريل 2025 – المبنى 1"',
    )
    date_ouverture = models.DateField(verbose_name="تاريخ الفتح")
    date_fermeture = models.DateField(
        null=True, blank=True, verbose_name="تاريخ الإغلاق"
    )
    statut = models.CharField(
        max_length=10,
        choices=STATUT_CHOICES,
        default=STATUT_OUVERT,
        verbose_name="الحالة",
    )

    # Chick sourcing
    nombre_poussins_initial = models.PositiveIntegerField(
        verbose_name="عدد الكتاكيت الأولي",
        validators=[MinValueValidator(1)],
    )
    fournisseur_poussins = models.ForeignKey(
        "intrants.Fournisseur",
        on_delete=models.PROTECT,
        related_name="lots_eleves",
        verbose_name="مورد الكتاكيت",
    )
    # Optional link to the BL fournisseur that delivered the chicks
    bl_fournisseur_poussins = models.ForeignKey(
        "achats.BLFournisseur",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lots_ouverts",
        verbose_name="وصل تسليم المورد (كتاكيت)",
    )

    batiment = models.ForeignKey(
        "intrants.Batiment",
        on_delete=models.PROTECT,
        related_name="lots",
        verbose_name="المبنى",
    )
    souche = models.CharField(
        max_length=100,
        verbose_name="السلالة",
        blank=True,
        help_text="مثال: Ross 308, Cobb 500",
    )
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lots_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "دفعة تربية"
        verbose_name_plural = "دفعات التربية"
        ordering = ["-date_ouverture"]

    def __str__(self):
        return self.designation

    def fermer(self, date_fermeture=None):
        """
        Close the lot.  Sets statut → fermé and records closure date.
        After this, no Mortalite or Consommation can be added (enforced in
        those models' clean() methods).
        """
        from datetime import date as _date

        self.statut = self.STATUT_FERME
        self.date_fermeture = date_fermeture or _date.today()
        self.save(update_fields=["statut", "date_fermeture", "updated_at"])

    # ------------------------------------------------------------------
    # Computed indicators (calculated on-demand — cache at view layer)
    # ------------------------------------------------------------------

    @property
    def total_mortalite(self):
        """Cumulative bird deaths recorded for this lot."""
        result = self.mortalites.aggregate(total=models.Sum("nombre"))["total"]
        return result or 0

    @property
    def effectif_vivant(self):
        """Current live bird count = initial – cumulative deaths – already slaughtered."""
        from django.db.models import Sum

        abattus = (
            self.productions.filter(statut="valide").aggregate(
                total=Sum("nombre_oiseaux_abattus")
            )["total"]
            or 0
        )
        return self.nombre_poussins_initial - self.total_mortalite - abattus

    @property
    def taux_mortalite(self):
        """Mortality rate as a percentage."""
        if self.nombre_poussins_initial == 0:
            return 0
        return round(self.total_mortalite / self.nombre_poussins_initial * 100, 2)

    @property
    def duree_elevage(self):
        """Days from opening to closure (or latest recorded activity if still open)."""
        from datetime import date
        from django.db.models import Max

        if self.date_fermeture:
            end = self.date_fermeture
        else:
            latest_mortalite = self.mortalites.aggregate(m=Max("date"))["m"]
            latest_conso = self.consommations.aggregate(m=Max("date"))["m"]
            candidates = [d for d in (latest_mortalite, latest_conso) if d is not None]
            end = max(candidates) if candidates else date.today()

        return (end - self.date_ouverture).days

    @property
    def consommation_totale_aliment(self):
        """Total feed quantity consumed across all feed consumption records."""
        from django.db.models import Sum

        # Must compare via the stable seed code, NOT the string "aliment"
        # (categorie is a FK to CategorieIntrant, not a CharField).
        result = self.consommations.filter(
            intrant__categorie__code="ALIMENT"
        ).aggregate(total=Sum("quantite"))["total"]
        return result or 0

    @property
    def cout_total_intrants(self):
        """
        Estimated total input cost = Σ (quantite × prix_unitaire_moyen)
        for all consommation records where PMP is available.
        """
        total = 0
        for c in self.consommations.select_related("intrant__stock").all():
            try:
                pmp = c.intrant.stock.prix_moyen_pondere
            except Exception:
                pmp = 0
            total += float(c.quantite) * float(pmp)
        return round(total, 2)


class Mortalite(models.Model):
    """
    Daily mortality record for a lot.
    Reduces the live bird count (effectif vivant) of the lot.
    """

    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.CASCADE,
        related_name="mortalites",
        verbose_name="دفعة التربية",
    )
    date = models.DateField(verbose_name="التاريخ")
    nombre = models.PositiveIntegerField(
        verbose_name="عدد النافقات",
        validators=[MinValueValidator(1)],
    )
    cause = models.CharField(max_length=255, verbose_name="السبب (إن عُرف)", blank=True)
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نفوق"
        verbose_name_plural = "النفوق"
        ordering = ["lot", "-date"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.lot_id and self.lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError(
                "Impossible d'ajouter une mortalité sur un lot fermé (spec §5.4)."
            )

    def __str__(self):
        return f"{self.lot} — {self.nombre} morts le {self.date}"


class Consommation(models.Model):
    """
    Input consumption event attributed to an active lot.

    On validation (save), two effects occur (handled via signals):
      1. The StockIntrant balance of the consumed intrant is decreased.
      2. A StockMouvement (sortie) is recorded for audit.

    Applies to feed (aliments) and medicines (médicaments).
    Chick assignment is handled at lot opening, not here.
    """

    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.CASCADE,
        related_name="consommations",
        verbose_name="دفعة التربية",
    )
    date = models.DateField(verbose_name="تاريخ الاستهلاك")
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="consommations",
        verbose_name="المدخل المستهلك",
        # Use categorie__consommable_en_lot so the filter works on the FK
        # relation, not on a bare string comparison (which would silently
        # match nothing because categorie is an integer FK, not a CharField).
        limit_choices_to={"categorie__consommable_en_lot": True},
    )
    quantite = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="الكمية",
        validators=[MinValueValidator(0.001)],
    )
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consommations_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "استهلاك"
        verbose_name_plural = "الاستهلاكات"
        ordering = ["lot", "-date"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.lot_id and self.lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError(
                "Impossible d'enregistrer une consommation sur un lot fermé (spec §5.4)."
            )

    def __str__(self):
        return (
            f"{self.lot.designation} — {self.intrant.designation} "
            f"{self.quantite} {self.intrant.unite_mesure} ({self.date})"
        )
