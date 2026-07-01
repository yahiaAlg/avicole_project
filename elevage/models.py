"""
elevage/models.py

Central production domain.  A LotElevage (poultry batch) is the unit around
which all daily operations — mortality, feed consumption, medicine
administration — are organised.
"""

from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from decimal import Decimal


class ParametrageElevage(models.Model):
    """
    Singleton configuration row holding farm-wide age thresholds.

    age_transfert_poussiniere_jours — age at which a lot in a Poussinière
        must be moved to a Poulailler (see TransfertLot / LotElevage.doit_etre_transfere).
    age_maturite_vente_jours — minimum age before a lot may be slaughtered /
        sold (see LotElevage.est_mature_pour_vente, enforced on ProductionRecord).

    Both are farm-level defaults; only one row should ever exist (pk=1).
    """

    age_transfert_poussiniere_jours = models.PositiveIntegerField(
        default=126,
        verbose_name="سن النقل من الحضانة (أيام)",
        help_text="السن الذي يجب عنده نقل الدفعة من حضانة الكتاكيت إلى حظيرة التربية.",
    )
    age_maturite_vente_jours = models.PositiveIntegerField(
        default=45,
        verbose_name="سن النضج الأدنى للبيع/الذبح (أيام)",
        help_text="لا يمكن تسجيل سجل إنتاج (ذبح/بيع) لدفعة لم تبلغ هذا السن بعد.",
    )

    class Meta:
        verbose_name = "إعدادات التربية"
        verbose_name_plural = "إعدادات التربية"

    def __str__(self):
        return "إعدادات التربية"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton row is never deleted

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


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
    # v1.4 — denormalized from batiment.branche for direct filtering/indexing
    # (BR-BRA-01); kept in sync automatically in save(), never set by hand.
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="lots",
        verbose_name="الفرع",
        editable=False,
    )
    souche = models.CharField(
        max_length=100,
        verbose_name="السلالة",
        blank=True,
        help_text="مثال: Ross 308, Cobb 500",
    )
    notes = models.TextField(verbose_name="ملاحظات", blank=True)

    # Lineage: set when this lot is created by a SPLIT_NEW transfer
    lot_parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lots_enfants",
        verbose_name="الدفعة الأم (عند التقسيم)",
    )

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

    def save(self, *args, **kwargs):
        # v1.4 — branche always mirrors the assigned bâtiment's branche
        # (BR-BRA-01); this keeps every downstream query on `branche`
        # consistent without requiring callers to set it explicitly.
        if self.batiment_id:
            self.branche_id = self.batiment.branche_id
        super().save(*args, **kwargs)

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
    def nombre_poussins_reference(self):
        """
        True denominator for mortality-rate calculations.

        When a TransfertLot is saved, the signal decrements
        nombre_poussins_initial by effectif_transfere so that effectif_vivant
        stays correct (it never subtracts transferred birds explicitly).
        As a side-effect, using nombre_poussins_initial directly as the
        mortality denominator inflates taux_mortalite after any transfer
        (worst case: 100 % after a full transfer even when mortality is low).

        The correct denominator is the total birds this lot ever housed:
            initial_before_transfers = nombre_poussins_initial
                                       + Σ transferts_sortants.effectif_transfere

        This restores the original cohort size by adding back all birds that
        left via TransfertLot, without touching the working baseline that
        effectif_vivant depends on.
        """
        from django.db.models import Sum

        transferts_out = (
            self.transferts.aggregate(total=Sum("effectif_transfere"))["total"] or 0
        )
        return self.nombre_poussins_initial + transferts_out

    @property
    def taux_mortalite(self):
        """
        Mortality rate as a percentage of the true initial cohort.

        Uses nombre_poussins_reference (not nombre_poussins_initial) so that
        transfers do not inflate the rate — a full transfer of 5 978 birds out
        of 6 000 (with 22 deaths) correctly yields 22/6 000 ≈ 0.37 %, not
        22/22 = 100 %.
        """
        ref = self.nombre_poussins_reference
        if ref == 0:
            return 0
        return round(self.total_mortalite / ref * 100, 2)

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
    def age_jours(self):
        """
        Age of the lot in days, counted from date_ouverture to today (or
        date_fermeture if closed). Unlike duree_elevage, this always reflects
        calendar age — used to drive transfer/maturity thresholds, which must
        keep advancing even on days with no recorded activity.
        """
        from datetime import date

        end = self.date_fermeture or date.today()
        return (end - self.date_ouverture).days

    @property
    def phase(self):
        """Current building type the lot is housed in (poussiniere/poulailler/None)."""
        return self.batiment.type_batiment if self.batiment_id else None

    @property
    def doit_etre_transfere(self):
        """
        True when the lot is still in a Poussinière and has reached (or
        passed) the configured transfer age threshold.
        """
        from intrants.models import Batiment

        if (
            not self.batiment_id
            or self.batiment.type_batiment != Batiment.TYPE_POUSSINIERE
        ):
            return False
        seuil = ParametrageElevage.get_solo().age_transfert_poussiniere_jours
        return self.age_jours >= seuil

    @property
    def est_mature_pour_vente(self):
        """
        True once the lot has reached the minimum maturity age required
        before any ProductionRecord (slaughter/harvest) can be validated.
        """
        seuil = ParametrageElevage.get_solo().age_maturite_vente_jours
        return self.age_jours >= seuil

    @property
    def stade_intrant_attendu(self):
        """
        Maps the lot's current building type to the Intrant.stade it should
        be consuming. Poulailler covers both grow-out and laying birds —
        ConsommationForm narrows further by also allowing STADE_TOUS items.
        """
        from intrants.models import Batiment, Intrant

        if not self.batiment_id:
            return Intrant.STADE_TOUS
        mapping = {
            Batiment.TYPE_POUSSINIERE: Intrant.STADE_DEMARRAGE,
            Batiment.TYPE_POULAILLER: Intrant.STADE_CROISSANCE,
        }
        return mapping.get(self.batiment.type_batiment, Intrant.STADE_TOUS)

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

    @property
    def branche(self):
        """v1.4 — inherited from the parent lot (BR-BRA-01), not stored."""
        return self.lot.branche if self.lot_id else None


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

    @property
    def branche(self):
        """v1.4 — inherited from the parent lot (BR-BRA-01), not stored."""
        return self.lot.branche if self.lot_id else None


class TransfertLot(models.Model):
    """
    Records the move of a lot (full or partial split) from one building to another.

    Modes
    -----
    MODE_FULL        — Whole live flock moves; lot.batiment updated to destination.
                       Baseline unchanged (same cohort, different building).
    MODE_SPLIT_NEW   — Partial move; a child LotElevage is created at destination
                       (inheriting souche/fournisseur/date_ouverture from parent).
                       Source lot.nombre_poussins_initial decreases by effectif_transfere.
    MODE_SPLIT_MERGE — Partial move; effectif_transfere birds are merged into an
                       existing open lot at destination (lot_destination).
                       Source baseline decreases; destination baseline increases.

    On creation the transfert_lot_post_save signal applies the chosen mode.
    TransfertLot records are immutable (no edit/delete view).
    """

    MODE_FULL = "full"
    MODE_SPLIT_NEW = "split_new"
    MODE_SPLIT_MERGE = "split_merge"
    MODE_CHOICES = [
        (MODE_FULL, "نقل كامل — الدفعة بأكملها تنتقل"),
        (MODE_SPLIT_NEW, "تقسيم — إنشاء دفعة فرعية جديدة في الوجهة"),
        (MODE_SPLIT_MERGE, "تقسيم — دمج في دفعة موجودة في الوجهة"),
    ]

    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.CASCADE,
        related_name="transferts",
        verbose_name="دفعة التربية",
    )
    batiment_origine = models.ForeignKey(
        "intrants.Batiment",
        on_delete=models.PROTECT,
        related_name="transferts_sortants",
        verbose_name="المبنى الأصلي",
    )
    batiment_destination = models.ForeignKey(
        "intrants.Batiment",
        on_delete=models.PROTECT,
        related_name="transferts_entrants",
        verbose_name="المبنى الجديد",
    )
    date_transfert = models.DateField(verbose_name="تاريخ النقل")
    age_jours_transfert = models.PositiveIntegerField(
        verbose_name="عمر الدفعة عند النقل (أيام)"
    )
    effectif_transfere = models.PositiveIntegerField(
        verbose_name="عدد الطيور المنقولة",
        validators=[MinValueValidator(1)],
    )
    motif = models.CharField(max_length=255, blank=True, verbose_name="السبب")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")

    # Split-mode fields (null for MODE_FULL)
    mode = models.CharField(
        max_length=15,
        choices=MODE_CHOICES,
        default=MODE_FULL,
        verbose_name="نوع النقل",
    )
    # SPLIT_MERGE: the existing lot that receives the birds
    lot_destination = models.ForeignKey(
        LotElevage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transferts_recus_fusion",
        verbose_name="الدفعة الوجهة (عند الدمج)",
    )
    # SPLIT_NEW: child lot created by the signal (set post-save)
    lot_enfant = models.OneToOneField(
        LotElevage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfert_origine",
        verbose_name="الدفعة الفرعية المنشأة",
    )
    # SPLIT_NEW: designation for the new child lot (defaults to auto-generated)
    designation_lot_enfant = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="تسمية الدفعة الفرعية",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transferts_crees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نقل دفعة"
        verbose_name_plural = "عمليات نقل الدفعات"
        ordering = ["-date_transfert"]

    def __str__(self):
        return (
            f"{self.lot.designation}: {self.batiment_origine} → "
            f"{self.batiment_destination} ({self.date_transfert})"
        )

    @property
    def branche(self):
        """v1.4 — inherited from the source lot (BR-BRA-01), not stored."""
        return self.lot.branche if self.lot_id else None

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.lot_id and self.lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError("Impossible de transférer un lot fermé.")
        if (
            self.batiment_origine_id
            and self.batiment_destination_id
            and self.batiment_origine_id == self.batiment_destination_id
        ):
            raise ValidationError(
                "Le bâtiment de destination doit être différent du bâtiment d'origine."
            )
        # v1.4 — a lot belongs to exactly one branche (BR-BRA-01); transfers
        # move birds between buildings WITHIN that same branche only. Moving
        # birds to a building in another branche is not a TransfertLot —
        # it would require closing the lot and reopening a new one there.
        if (
            self.batiment_origine_id
            and self.batiment_destination_id
            and self.batiment_origine.branche_id != self.batiment_destination.branche_id
        ):
            raise ValidationError(
                "BR-BRA-01 : le transfert doit rester à l'intérieur d'une même "
                "branche — le bâtiment d'origine et celui de destination "
                "appartiennent à des branches différentes."
            )
        if (
            self.lot_id
            and self.batiment_origine_id
            and self.lot.branche_id != self.batiment_origine.branche_id
        ):
            raise ValidationError(
                "BR-BRA-01 : le bâtiment d'origine doit appartenir à la même "
                "branche que le lot."
            )
        if self.lot_id and self.effectif_transfere:
            if self.effectif_transfere > self.lot.effectif_vivant:
                raise ValidationError(
                    f"L'effectif transféré ({self.effectif_transfere}) dépasse "
                    f"l'effectif vivant du lot ({self.lot.effectif_vivant})."
                )
        # Split-mode: partial transfer only
        if self.mode in (self.MODE_SPLIT_NEW, self.MODE_SPLIT_MERGE):
            if self.lot_id and self.effectif_transfere:
                if self.effectif_transfere >= self.lot.effectif_vivant:
                    raise ValidationError(
                        "في نمط التقسيم، يجب أن يكون عدد الطيور المنقولة أقل من العدد الحي الكلي."
                    )
        # Merge mode: lot_destination required and must be open at destination building
        if self.mode == self.MODE_SPLIT_MERGE:
            if not self.lot_destination_id:
                raise ValidationError("يجب تحديد الدفعة الوجهة عند اختيار نمط الدمج.")
            if self.lot_destination_id == self.lot_id:
                raise ValidationError("لا يمكن دمج الدفعة مع نفسها.")
            if (
                self.lot_destination_id
                and self.lot_destination.statut == LotElevage.STATUT_FERME
            ):
                raise ValidationError("الدفعة الوجهة مغلقة — اختر دفعة مفتوحة.")


class PeseeEchantillon(models.Model):
    """
    A sample weighing event: N subjects (birds or eggs) weighed together on
    a given day, used to track the average weight over time and to resolve
    a quality grade via intrants.CategorieQualite.
    """

    TYPE_OISEAUX = "oiseaux"
    TYPE_OEUFS = "oeufs"
    TYPE_CHOICES = [
        (TYPE_OISEAUX, "طيور"),
        (TYPE_OEUFS, "بيض"),
    ]

    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.CASCADE,
        related_name="pesees",
        verbose_name="دفعة التربية",
    )
    date = models.DateField(verbose_name="تاريخ القياس")
    type_pesee = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        default=TYPE_OISEAUX,
        verbose_name="نوع القياس",
    )
    nombre_sujets = models.PositiveIntegerField(
        verbose_name="عدد العينات",
        validators=[MinValueValidator(1)],
    )
    poids_total_g = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="الوزن الإجمالي للعينة (غ)",
        validators=[MinValueValidator(0.01)],
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pesees_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "وزن عينة"
        verbose_name_plural = "أوزان العينات"
        ordering = ["lot", "-date"]

    def __str__(self):
        return f"{self.lot.designation} — {self.get_type_pesee_display()} {self.date}"

    @property
    def branche(self):
        """v1.4 — inherited from the parent lot (BR-BRA-01), not stored."""
        return self.lot.branche if self.lot_id else None

    @property
    def poids_moyen_g(self):
        if not self.nombre_sujets:
            return Decimal("0")
        return round(Decimal(str(self.poids_total_g)) / self.nombre_sujets, 2)

    @property
    def qualite(self):
        """Resolve the matching CategorieQualite bracket, or None if no bracket fits."""
        from intrants.utils import determiner_qualite

        return determiner_qualite(self.poids_moyen_g, self.type_pesee)


class RecolteOeufs(models.Model):
    """
    Daily egg-collection event for a lot in laying phase (Poulailler).

    Eggs are stored/sold by the plateau (30 eggs); nombre_oeufs is the raw
    count and nombre_plateaux/oeufs_hors_plateau derive the storage units.
    Quality is taken from the same-day PeseeEchantillon (type_pesee=oeufs)
    if one was recorded, since heavier eggs grade higher.

    On save, a signal credits StockProduitFini for the farm's egg product
    and logs a StockMouvement (mirrors Consommation/Mortalite stock signals).
    """

    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.CASCADE,
        related_name="recoltes_oeufs",
        verbose_name="دفعة التربية",
    )
    date = models.DateField(verbose_name="تاريخ الجمع")
    nombre_oeufs = models.PositiveIntegerField(
        verbose_name="عدد البيض",
        validators=[MinValueValidator(1)],
    )
    pesee = models.ForeignKey(
        PeseeEchantillon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recoltes",
        verbose_name="قياس الوزن المرجعي",
        help_text="قياس وزن عينة من البيض في نفس اليوم تقريباً، لتحديد الجودة.",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recoltes_oeufs_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    PLATEAU_SIZE = 30

    class Meta:
        verbose_name = "جمع بيض"
        verbose_name_plural = "عمليات جمع البيض"
        ordering = ["lot", "-date"]

    def __str__(self):
        return f"{self.lot.designation} — {self.nombre_oeufs} بيضة ({self.date})"

    @property
    def branche(self):
        """v1.4 — inherited from the parent lot (BR-BRA-01), not stored."""
        return self.lot.branche if self.lot_id else None

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.lot_id and self.lot.statut == LotElevage.STATUT_FERME:
            raise ValidationError(
                "Impossible d'enregistrer une récolte d'œufs sur un lot fermé."
            )

    @property
    def nombre_plateaux(self):
        return self.nombre_oeufs // self.PLATEAU_SIZE

    @property
    def oeufs_hors_plateau(self):
        return self.nombre_oeufs % self.PLATEAU_SIZE

    @property
    def qualite(self):
        return self.pesee.qualite if self.pesee_id else None
