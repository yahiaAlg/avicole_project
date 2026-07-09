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
        from elevage.models import RetraitOeufs

        if self.date_fermeture:
            end = self.date_fermeture
        else:
            latest_mortalite = self.mortalites.aggregate(m=Max("date"))["m"]
            latest_conso = self.consommations.aggregate(m=Max("date"))["m"]
            latest_oeufs = self.recoltes_oeufs.aggregate(m=Max("date"))["m"]
            latest_retrait = RetraitOeufs.objects.filter(lot=self).aggregate(
                m=Max("date")
            )["m"]
            candidates = [
                d
                for d in (
                    latest_mortalite,
                    latest_conso,
                    latest_oeufs,
                    latest_retrait,
                )
                if d is not None
            ]
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
        be consuming. Only two finished feeds actually exist in the catalogue
        — "Aliment Démarrage Poussin" (STADE_DEMARRAGE) and "Aliment Ponte
        Poule" (STADE_PONTE); there is no STADE_CROISSANCE feed. Poulailler
        lots (grow-out/laying, past the poussinière stage) therefore map to
        STADE_PONTE, the only finished feed available to them — mapping them
        to STADE_CROISSANCE left the consumption dropdown empty for every
        poulailler lot since nothing in the catalogue carries that stade.
        ConsommationForm narrows further by also allowing STADE_TOUS items.
        """
        from intrants.models import Batiment, Intrant

        if not self.batiment_id:
            return Intrant.STADE_TOUS
        mapping = {
            Batiment.TYPE_POUSSINIERE: Intrant.STADE_DEMARRAGE,
            Batiment.TYPE_POULAILLER: Intrant.STADE_PONTE,
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
    def consommations_medicament_par_unite(self):
        """
        Médicament/vaccin/vitamine/antibiotique/désinfectant consumption
        totals, grouped by unité de mesure (unlike feed, which is always
        KG, médicaments span several units — ML, DOSE, FLACON…) so each
        unit's quantities are summed together rather than added across
        incompatible units.

        Returns a list of dicts: [{"libelle": "مل", "total": Decimal(...)}, …]
        ordered by the unit's display order.
        """
        from django.db.models import Sum

        rows = (
            self.consommations.exclude(intrant__categorie__code="ALIMENT")
            .values(
                "intrant__unite_mesure__libelle",
                "intrant__unite_mesure__ordre",
            )
            .annotate(total=Sum("quantite"))
            .order_by("intrant__unite_mesure__ordre")
        )
        return [
            {"libelle": row["intrant__unite_mesure__libelle"], "total": row["total"]}
            for row in rows
        ]

    def _cout_consommations(self, *, exclude_aliment=False, only_aliment=False):
        """
        Internal helper: Σ (quantite × PMP) over this lot's Consommation
        records, scoped to the given catégorie filter.

        exclude_aliment=True  → médicaments/vaccins/vitamines/antibiotiques/
                                 désinfectants only (everything consommable_en_lot
                                 except ALIMENT — mirrors
                                 ConsommationMedicamentForm._intrant_base_queryset).
        only_aliment=True     → feed (catégorie ALIMENT) only (mirrors
                                 ConsommationForm._intrant_base_queryset).
        Neither flag          → every consommation regardless of catégorie.
        """
        from django.db.models import Prefetch
        from stock.models import StockIntrant

        qs = self.consommations.select_related("intrant", "intrant__categorie")
        if only_aliment:
            qs = qs.filter(intrant__categorie__code="ALIMENT")
        elif exclude_aliment:
            qs = qs.exclude(intrant__categorie__code="ALIMENT")

        qs = qs.prefetch_related(
            Prefetch(
                "intrant__stocks",
                queryset=StockIntrant.objects.filter(branche=self.branche),
                to_attr="stocks_branche",
            )
        )

        total = 0
        for c in qs.all():
            try:
                pmp = c.intrant.stocks_branche[0].prix_moyen_pondere
            except (IndexError, AttributeError):
                pmp = 0
            total += float(c.quantite) * float(pmp)
        return round(total, 2)

    @property
    def cout_aliments(self):
        """Estimated feed cost = Σ (quantite × PMP) over ALIMENT consommations."""
        return self._cout_consommations(only_aliment=True)

    @property
    def cout_medicaments(self):
        """
        Estimated médicament/vaccin/vitamine/antibiotique/désinfectant cost
        = Σ (quantite × PMP) over every non-ALIMENT consommation.
        """
        return self._cout_consommations(exclude_aliment=True)

    @property
    def cout_mortalite_poussins(self):
        """
        Estimated value of chicks lost to mortality = total_mortalite × PMP
        of the poussin intrant tied to this lot (scoped to its branche).

        These birds leave StockIntrant via a Mortalite-triggered "sortie"
        (see signals._appliquer_sortie_mortalite) but, before v1.5, that
        stock movement was never reflected in any cost total — understating
        cout_total_intrants and therefore inflating marge_brute. They are
        genuine consumed input cost like feed or médicaments.
        """
        if not self.total_mortalite:
            return 0

        from elevage.signals import _get_poussin_intrant

        poussin_intrant = _get_poussin_intrant(self)
        if poussin_intrant is None:
            return 0

        stock_poussin = poussin_intrant.stocks.filter(branche=self.branche).first()
        if stock_poussin is None:
            return 0

        return round(
            float(self.total_mortalite) * float(stock_poussin.prix_moyen_pondere), 2
        )

    @property
    def cout_total_intrants(self):
        """
        Estimated total input cost = cout_aliments + cout_medicaments +
        cout_mortalite_poussins.

        v1.4 (BR-BRA-07): StockIntrant moved from a one-to-one on `intrant`
        to a per-(branche, intrant) row (related_name "stocks"), so the PMP
        must be looked up for THIS lot's own branche — an intrant can now
        have a different weighted-average cost in another branche.
        """
        return round(
            self.cout_aliments + self.cout_medicaments + self.cout_mortalite_poussins,
            2,
        )


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

    # Costing / payment tracking (médicament/vaccin only — BR-request).
    # Feed (ALIMENT) consumption never sets these; ConsommationForm doesn't
    # expose the field, so it stays at 0 there. Mirrors the ProductionAliment
    # prix_unitaire pattern: entered directly → auto-Depense at creation
    # (see views._auto_creer_depense_consommation_medicament); left at 0 →
    # stays unpaid until batched into ONE team/vet Depense (see
    # views.consommation_medicament_paiement_create), same as a
    # formule-based ProductionAliment awaiting its labor-cost payment.
    prix_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
        verbose_name="سعر الوحدة (اختياري)",
        validators=[MinValueValidator(0)],
        help_text=(
            "سعر الوحدة الواحدة من الدواء/اللقاح — اختياري. أدخله هنا عند "
            "معرفة السعر فوراً (يُنشئ مصروفاً تلقائياً). اتركه 0 لتجميع "
            "هذا الاستهلاك لاحقاً ضمن دفعة أجرة طبيب/فريق بيطري واحدة "
            "(انظر: استهلاكات الأدوية ← دفع أجرة)."
        ),
    )
    depense_paiement = models.ForeignKey(
        "depenses.Depense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consommations_medicament_payees",
        verbose_name="مصروف دفع أجرة الطبيب/الفريق المرتبط",
        help_text=(
            "يُملأ تلقائياً عند تجميع هذا الاستهلاك ضمن دفعة أجرة "
            "طبيب/فريق بيطري (انظر: دفع أجرة الأدوية)."
        ),
    )
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

    @property
    def est_medicament(self):
        """True for médicament/vaccin/vitamine/… consumption (anything not
        ALIMENT) — the only kind that ever carries a costing/payment."""
        return bool(self.intrant_id) and self.intrant.categorie.code != "ALIMENT"

    @property
    def montant_total(self):
        """Cost of this consumption, when a unit price was entered directly
        (0 when prix_unitaire is 0 — e.g. médicament entries awaiting a
        batched team/vet payment instead, see signals/views).

        For médicament/vaccin, prix_unitaire is a per-chick/bird price, not
        a per-dose price — it's multiplied by the lot's current
        effectif_vivant (the birds the batch was administered to), not by
        the dose quantite (BR-request)."""
        if self.prix_unitaire and self.est_medicament and self.lot_id:
            return self.lot.effectif_vivant * self.prix_unitaire
        return self.quantite * self.prix_unitaire

    @property
    def est_paye(self):
        """True once this médicament consumption's cost has been batched
        into a Depense — see depense_paiement /
        consommation_medicament_paiement_create."""
        return self.depense_paiement_id is not None

    @property
    def necessite_paiement(self):
        """True for médicament/vaccin consumptions still awaiting a
        team/vet Depense (priced-at-entry consumptions are auto-expensed
        at creation instead — see
        views._auto_creer_depense_consommation_medicament). Feed (ALIMENT)
        consumption never needs this."""
        return (
            self.est_medicament
            and not self.prix_unitaire
            and self.depense_paiement_id is None
        )


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


# ---------------------------------------------------------------------------
# Aliment (feed) production / replenishment — new feature
# ---------------------------------------------------------------------------
#
# Consommation already lets a lot consume any Intrant of category ALIMENT
# directly (exact quantity, decrementing that Intrant's StockIntrant) — that
# path stays unchanged and is what feeds the "ALIMENT" column of the daily
# lot table below.
#
# What was missing: the *replenishment* side. A finished/mixed feed (e.g.
# "Aliment démarrage") can be topped up either as a bare quantity (fast path,
# no ingredient bookkeeping) or via a FormuleAliment recipe, in which case the
# raw ingredient Intrants are optionally decremented proportionally. Both
# paths simply increase the finished feed's own StockIntrant balance.


class FormuleAliment(models.Model):
    """
    Optional feed recipe: which raw Intrant ingredients (and in what
    proportion) go into one finished feed Intrant. Purely for traceability —
    ProductionAliment can always be entered without one.
    """

    nom = models.CharField(max_length=150, verbose_name="اسم التركيبة")
    intrant_produit = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="formules_aliment",
        verbose_name="العلف الناتج",
        limit_choices_to={"categorie__code": "ALIMENT"},
        help_text="العلف الجاهز الذي تُضاف إليه الكمية المصنّعة إلى المخزون.",
    )
    actif = models.BooleanField(default=True, verbose_name="نشطة")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تركيبة علف"
        verbose_name_plural = "تركيبات العلف"
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    @property
    def total_proportion_kg(self):
        from django.db.models import Sum

        return self.lignes.aggregate(total=Sum("proportion_kg"))["total"] or Decimal(
            "0"
        )


class FormuleAlimentLigne(models.Model):
    """
    One ingredient line of a FormuleAliment, expressed as kg of that raw
    Intrant per 100 kg of finished feed produced (proportion_kg / 100 gives
    the ratio applied to whatever quantite_produite is entered).
    """

    formule = models.ForeignKey(
        FormuleAliment,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="التركيبة",
    )
    intrant = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="lignes_formule_aliment",
        verbose_name="المدخل (مكوّن)",
    )
    proportion_kg = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        verbose_name="كغ لكل 100 كغ علف ناتج",
        validators=[MinValueValidator(0.001)],
    )

    class Meta:
        verbose_name = "مكوّن تركيبة"
        verbose_name_plural = "مكوّنات التركيبة"
        unique_together = ("formule", "intrant")
        ordering = ["formule", "-proportion_kg"]

    def __str__(self):
        return f"{self.formule.nom} — {self.intrant.designation} ({self.proportion_kg} kg/100kg)"


class ProductionAliment(models.Model):
    """
    Replenishment event for a finished feed Intrant: "we just milled/mixed
    *quantite_produite_kg* of feed X". Always credits intrant_produit's own
    StockIntrant (scoped to `branche`).

    `formule` is optional (BR-request: "quantity added without specifying the
    intrants milled into it"). When provided, each FormuleAlimentLigne is used
    to also debit the corresponding raw-ingredient StockIntrant, proportional
    to quantite_produite_kg — pure bookkeeping, never blocks the save if an
    ingredient's stock goes negative (mirrors the lenient pattern already
    used for Consommation/Mortalite stock corrections in signals.py).

    Costing (BR-request): `prix_unitaire` is an optional per-kg cost, mostly
    used on the direct entry (no `formule`) — a straight purchase/refill at a
    known price. When set (> 0), the feed's StockIntrant.prix_moyen_pondere
    is updated by the usual weighted-average formula. When a `formule` is
    used instead, the unit cost is derived automatically from the current
    PMP of each debited ingredient (so `prix_unitaire` can be left at 0) —
    see elevage.signals.production_aliment_post_save for both paths.
    """

    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="productions_aliment",
        verbose_name="الفرع",
    )
    date = models.DateField(verbose_name="تاريخ التصنيع/التزويد")
    formule = models.ForeignKey(
        FormuleAliment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productions",
        verbose_name="التركيبة (اختياري)",
    )
    intrant_produit = models.ForeignKey(
        "intrants.Intrant",
        on_delete=models.PROTECT,
        related_name="productions_aliment",
        verbose_name="العلف المصنّع",
        limit_choices_to={"categorie__code": "ALIMENT"},
    )
    quantite_produite_kg = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name="الكمية المصنّعة (كغ)",
        validators=[MinValueValidator(0.001)],
    )
    prix_unitaire = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
        verbose_name="سعر الوحدة (د.ج/كغ) — اختياري",
        validators=[MinValueValidator(0)],
        help_text=(
            "تكلفة الكيلوغرام الواحد من هذا التزويد. عند تركه 0 (الحالة "
            "الافتراضية)، لا يُعاد حساب متوسط سعر مخزون هذا العلف — يبقى "
            "PMP كما هو. هذا هو الحقل الأكثر استعمالاً عند الإضافة المباشرة "
            "(بدون تركيبة). عند اختيار تركيبة، يمكن تركه 0 لأن التكلفة "
            "تُشتق تلقائياً من أسعار مكوّناتها الحالية في المخزون (انظر "
            "elevage.signals.production_aliment_post_save)."
        ),
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productions_aliment_enregistrees",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Labor-cost payment tracking (BR-request): a `formule`-based production
    # never gets an auto Depense (its implied cost is only the ingredients'
    # PMP — see production_aliment_post_save), so the feed-mill worker's
    # labor is still unpaid until batched into one consolidated Depense via
    # views.production_aliment_paiement_create. Once linked here, this
    # production drops out of future unpaid-batches. SET_NULL so deleting
    # the Depense (rare, admin-only) simply reopens it for payment again.
    depense_paiement = models.ForeignKey(
        "depenses.Depense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productions_aliment_payees",
        verbose_name="مصروف دفع اليد العاملة المرتبط",
        help_text=(
            "يُملأ تلقائياً عند تجميع هذه العملية ضمن دفعة أجرة تصنيع علف "
            "(انظر: دفع تصنيع العلف)."
        ),
    )

    class Meta:
        verbose_name = "تصنيع/تزويد علف"
        verbose_name_plural = "عمليات تصنيع العلف"
        ordering = ["-date"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            self.formule_id
            and self.intrant_produit_id
            and self.formule.intrant_produit_id != self.intrant_produit_id
        ):
            raise ValidationError(
                "التركيبة المختارة تُنتج علفاً مختلفاً عن العلف المحدد هنا."
            )

    def __str__(self):
        return f"{self.intrant_produit.designation} +{self.quantite_produite_kg}kg ({self.date})"

    @property
    def montant_total(self):
        """Cost of this replenishment, when a unit price was entered directly
        (0 when prix_unitaire is 0 — e.g. formule-based entries priced from
        their ingredients instead, see signals.py)."""
        return self.quantite_produite_kg * self.prix_unitaire

    @property
    def est_paye(self):
        """True once this production's labor cost has been batched into a
        Depense — see depense_paiement / production_aliment_paiement_create."""
        return self.depense_paiement_id is not None

    @property
    def necessite_paiement(self):
        """True for formule-based productions still awaiting a labor-cost
        Depense (direct/no-formule entries are priced via prix_unitaire and
        auto-expensed at creation instead — see
        _auto_creer_depense_production_aliment)."""
        return self.formule_id is not None and self.depense_paiement_id is None


# ---------------------------------------------------------------------------
# Egg withdrawals — new feature (accumulation via RecolteOeufs, withdrawal here)
# ---------------------------------------------------------------------------


class RetraitOeufs(models.Model):
    """
    Non-invoiced egg withdrawal from stock: sold directly off the truck to a
    walk-in client, given away, or lost/discarded — anything that isn't a
    formal BLClient sale (which already debits StockProduitFini on its own).

    Always debits the same egg StockProduitFini that RecolteOeufs credits
    (see signals._get_produit_oeufs), scoped to `branche`. `lot` is optional
    and purely informational — it lets a withdrawal be attributed to (or
    just noted against) a specific lot's daily table without pretending the
    physical egg stock is split by lot.

    If `client` is set (motif=client_camion), the create view auto-generates
    a formal BLClient + BLClientLigne for this quantity (see
    views.retrait_oeufs_create) and links it via `bl_genere`. In that case
    THIS record no longer debits stock itself — signals.retrait_oeufs_post_save
    / _pre_delete skip their own stock movement whenever `bl_genere_id` is
    set, since the BLClientLigne's own signal already owns that debit. This
    avoids double-deducting the same eggs from two signals.
    """

    MOTIF_CLIENT_CAMION = "client_camion"
    MOTIF_DON = "don"
    MOTIF_AUTRE = "autre"
    MOTIF_CHOICES = [
        (MOTIF_CLIENT_CAMION, "بيع مباشر (شاحنة/زبون)"),
        (MOTIF_DON, "هدية / عيّنة مجانية"),
        (MOTIF_AUTRE, "أخرى (فقدان، كسر...)"),
    ]

    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="retraits_oeufs",
        verbose_name="الفرع",
    )
    lot = models.ForeignKey(
        LotElevage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retraits_oeufs",
        verbose_name="الدفعة (اختياري)",
    )
    date = models.DateField(verbose_name="تاريخ السحب")
    quantite_oeufs = models.PositiveIntegerField(
        verbose_name="عدد البيض المسحوب",
        validators=[MinValueValidator(1)],
    )
    motif = models.CharField(
        max_length=20,
        choices=MOTIF_CHOICES,
        default=MOTIF_CLIENT_CAMION,
        verbose_name="السبب",
    )
    client = models.ForeignKey(
        "clients.Client",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retraits_oeufs",
        verbose_name="العميل",
        help_text="اختر عميلاً مسجلاً لإنشاء وصل تسليم (BL) رسمي بهذه الكمية تلقائياً.",
    )
    bl_genere = models.OneToOneField(
        "clients.BLClient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="retrait_oeufs_origine",
        verbose_name="وصل التسليم المُنشأ",
    )
    destinataire = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="الجهة المستفيدة",
        help_text="اسم الزبون أو المستفيد إن لم يكن عميلاً مسجلاً (اختياري).",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retraits_oeufs_enregistres",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سحب بيض"
        verbose_name_plural = "عمليات سحب البيض"
        ordering = ["-date"]

    def __str__(self):
        return f"-{self.quantite_oeufs} œufs ({self.get_motif_display()}) — {self.date}"
