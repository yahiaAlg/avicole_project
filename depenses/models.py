"""
depenses/models.py

Operational expense tracking, strictly separated from accounts payable (AP).
Also hosts two special expense families that are NOT generic Depense rows:
associés withdrawals, and the RH (payroll/attendance) system.

Key business rules:
  BR-DEP-01  A facture fournisseur for goods NEVER auto-generates a dépense.
  BR-DEP-02  AP and dépenses draw from mutually exclusive data sources.
  BR-DEP-03  A dépense may OPTIONALLY link to a Service-type supplier invoice —
             only by explicit user action; never automatically.
  BR-DEP-04  Dépenses may optionally be attributed to a specific lot for
             per-lot profitability calculations.

  BR-ASSOC-01  Stakeholder withdrawals (retraits) are equity draws, not P&L
               expenses; they are tracked in their own table — never mixed
               into the generic Depense table (mirrors BR-DEP-02).
  BR-ASSOC-02  A retrait is always a deliberate, manual entry per stakeholder;
               nothing is generated automatically.

  BR-RH-01  Each employee follows a 6-jours-travaillés / 1-jour-repos weekly
            rotation. The rest day is a fixed weekday (`jour_repos_habituel`)
            covered by a `binome` (partner) on the opposite rotation —
            informational, not enforced by the scheduler.
  BR-RH-02  The monthly base salary is referenced to 25 worked days/month;
            daily rate = salaire_base_mensuel / 25 (JOURS_REFERENCE_MENSUEL).
  BR-RH-03  Paid leave (congé) accrues at 2.5 days per month worked (15 days
            after 6 months); a congé day is paid at the full daily rate and
            never counts as an absence.
  BR-RH-04  Salary advances (acomptes) are deducted from the net pay of the
            payslip (BulletinPaie) they end up attributed to; like BR-DEP-02,
            they are never inserted into the generic Depense table.
  BR-RH-05  A payslip is auto-calculated from the employee's daily attendance
            records (Pointage) for the month — see depenses.utils.
"""

from decimal import Decimal

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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

    # v1.4 — every dépense belongs to exactly one branche (BR-BRA-01).
    branche = models.ForeignKey(
        "core.Branche",
        on_delete=models.PROTECT,
        related_name="depenses",
        verbose_name="الفرع",
    )

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

    def clean(self):
        from django.core.exceptions import ValidationError

        # v1.4 — an optional lot attribution (BR-DEP-04) must stay within
        # the dépense's own branche (BR-BRA-01).
        if self.lot_id and self.branche_id and self.lot.branche_id != self.branche_id:
            raise ValidationError(
                {
                    "lot": (
                        "BR-BRA-01 : la dépense et le lot attribué doivent "
                        "appartenir à la même branche."
                    )
                }
            )
        # Same guard for the optional linked Service invoice (BR-DEP-03).
        if (
            self.facture_liee_id
            and self.branche_id
            and self.facture_liee.branche_id != self.branche_id
        ):
            raise ValidationError(
                {
                    "facture_liee": (
                        "BR-BRA-01 : la dépense et la facture fournisseur "
                        "liée doivent appartenir à la même branche."
                    )
                }
            )

    @property
    def a_piece_jointe(self):
        return bool(self.piece_jointe)


# ===========================================================================
# Associés — Stakeholder withdrawals  (BR-ASSOC-01 / BR-ASSOC-02)
#
# v1.4 note: Associe and RetraitAssocie are intentionally WITHOUT a
# `branche` FK. Per BR-BRA-08, equity withdrawals belong to the company as
# a whole, not to any one branch — they stay global/company-wide exactly
# like CompanyInfo, alongside the master-data catalogues (§3.5.3).
# ===========================================================================


class Associe(models.Model):
    """
    A stakeholder / partner of the business.

    `pourcentage_parts` is informational only (e.g. for future profit-share
    reporting) and is never used to compute or cap withdrawals.
    """

    nom = models.CharField(max_length=150, verbose_name="الاسم")
    telephone = models.CharField(max_length=30, blank=True, verbose_name="الهاتف")
    pourcentage_parts = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name="نسبة الحصة (%)",
        help_text="معلوماتي فقط — لا يُستخدم لحساب أو تحديد سقف السحوبات.",
    )
    actif = models.BooleanField(default=True, verbose_name="نشط")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "شريك"
        verbose_name_plural = "الشركاء"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class RetraitAssocie(models.Model):
    """
    A single withdrawal (retrait) of cash by a stakeholder.

    BR-ASSOC-01: this is an equity draw, not an operational expense —
    it is never written into the generic Depense table, but IS counted
    as a cash outflow in get_cash_flow_summary() (depenses/utils.py).
    BR-ASSOC-02: always manual; nothing creates this record automatically.
    """

    associe = models.ForeignKey(
        Associe,
        on_delete=models.PROTECT,
        related_name="retraits",
        verbose_name="الشريك",
    )
    date = models.DateField(verbose_name="تاريخ السحب")
    montant = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name="المبلغ (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    mode_paiement = models.CharField(
        max_length=20,
        choices=Depense.MODE_CHOICES,
        default=Depense.MODE_ESPECES,
        verbose_name="طريقة الدفع",
    )
    motif = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="السبب",
        help_text="مثال: سحب شخصي، تسبيق على الأرباح…",
    )
    reference_document = models.CharField(
        max_length=150, blank=True, verbose_name="مرجع الوثيقة"
    )
    piece_jointe = models.FileField(
        upload_to="associes/retraits/%Y/%m/",
        blank=True,
        null=True,
        verbose_name="مرفق (إيصال)",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    enregistre_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retraits_enregistres",
        verbose_name="مسجّل من قبل",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سحب شريك"
        verbose_name_plural = "سحوبات الشركاء"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.date} | {self.associe.nom} | {self.montant} DZD"


# ===========================================================================
# RH — Employees, attendance, leave, advances, payroll  (BR-RH-01..05)
# ===========================================================================

#: Monthly reference used to derive the daily rate (BR-RH-02).
JOURS_REFERENCE_MENSUEL = Decimal("25")

#: Paid-leave accrual rate per month worked (BR-RH-03): 2.5 × 6 = 15 days.
CONGE_JOURS_PAR_MOIS = Decimal("2.5")


class Employe(models.Model):
    """
    An employee on the 6-jours/1-repos rotation.

    `jour_repos_habituel` follows Python's date.weekday() convention
    (0 = lundi … 6 = dimanche). `binome` is the partner who covers this
    employee's rest day under the rotation (BR-RH-01) — purely
    informational; payroll is always computed from actual Pointage rows.
    """

    JOUR_LUNDI = 0
    JOUR_MARDI = 1
    JOUR_MERCREDI = 2
    JOUR_JEUDI = 3
    JOUR_VENDREDI = 4
    JOUR_SAMEDI = 5
    JOUR_DIMANCHE = 6

    JOUR_CHOICES = [
        (JOUR_LUNDI, "الإثنين"),
        (JOUR_MARDI, "الثلاثاء"),
        (JOUR_MERCREDI, "الأربعاء"),
        (JOUR_JEUDI, "الخميس"),
        (JOUR_VENDREDI, "الجمعة"),
        (JOUR_SAMEDI, "السبت"),
        (JOUR_DIMANCHE, "الأحد"),
    ]

    matricule = models.CharField(
        max_length=30, unique=True, verbose_name="الرقم التعريفي"
    )
    nom_complet = models.CharField(max_length=150, verbose_name="الاسم الكامل")
    fonction = models.CharField(max_length=100, blank=True, verbose_name="الوظيفة")
    telephone = models.CharField(max_length=30, blank=True, verbose_name="الهاتف")
    date_embauche = models.DateField(verbose_name="تاريخ التوظيف")

    batiment = models.ForeignKey(
        "intrants.Batiment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employes",
        verbose_name="المبنى المخصص",
    )

    jour_repos_habituel = models.PositiveSmallIntegerField(
        choices=JOUR_CHOICES,
        default=JOUR_VENDREDI,
        verbose_name="يوم الراحة الأسبوعي",
    )
    binome = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="remplace_pour",
        verbose_name="الزميل البديل (دوران)",
        help_text="العامل الذي يعمل بدلاً عنه يوم راحته (BR-RH-01).",
    )

    salaire_base_mensuel = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="الراتب الأساسي الشهري (د.ج)",
        validators=[MinValueValidator(0.01)],
        help_text="مرجعي لـ 25 يوم عمل/الشهر (BR-RH-02).",
    )
    heures_normales_jour = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("8.00"),
        verbose_name="ساعات العمل العادية/اليوم",
        validators=[MinValueValidator(0.01)],
    )
    taux_majoration_heure_sup = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("1.50"),
        verbose_name="معامل الساعات الإضافية",
        help_text="مثال: 1.50 = زيادة 50% عن السعر العادي للساعة.",
    )

    actif = models.BooleanField(default=True, verbose_name="نشط")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "عامل"
        verbose_name_plural = "العمال"
        ordering = ["nom_complet"]

    def __str__(self):
        return f"{self.matricule} — {self.nom_complet}"

    def clean(self):
        if self.binome_id and self.binome_id == self.pk:
            raise ValidationError({"binome": "لا يمكن أن يكون العامل بديلاً لنفسه."})

    @property
    def taux_journalier(self) -> Decimal:
        """Daily rate — BR-RH-02."""
        return (self.salaire_base_mensuel / JOURS_REFERENCE_MENSUEL).quantize(
            Decimal("0.01")
        )

    @property
    def taux_horaire(self) -> Decimal:
        if not self.heures_normales_jour:
            return Decimal("0.00")
        return (self.taux_journalier / self.heures_normales_jour).quantize(
            Decimal("0.01")
        )

    def anciennete_mois(self, as_of=None) -> int:
        """Full months worked since date_embauche, used for leave accrual."""
        import datetime

        as_of = as_of or datetime.date.today()
        if as_of < self.date_embauche:
            return 0
        mois = (as_of.year - self.date_embauche.year) * 12 + (
            as_of.month - self.date_embauche.month
        )
        if as_of.day < self.date_embauche.day:
            mois -= 1
        return max(mois, 0)

    @property
    def branche(self):
        """
        v1.4 — an employee's branch is DERIVED from their assigned
        bâtiment, not stored directly (BR-BRA-09). None when the employee
        has not yet been assigned to a building — such an employee will
        not appear in any branch-scoped payroll view until assigned.
        """
        return self.batiment.branche if self.batiment_id else None


class Pointage(models.Model):
    """
    Daily attendance record for one employee.

    STATUT_REPOS is the scheduled weekly rest day (covered by `binome`) —
    it is neither an absence nor an extra paid day; the 25-day monthly
    reference (BR-RH-02) already accounts for it. STATUT_CONGE is paid
    leave, billed at the full daily rate and debited from the leave
    balance (BR-RH-03). Only STATUT_PRESENT and STATUT_CONGE earn pay;
    STATUT_ABSENT simply earns nothing for that day.
    """

    STATUT_PRESENT = "present"
    STATUT_ABSENT = "absent"
    STATUT_REPOS = "repos"
    STATUT_CONGE = "conge"

    STATUT_CHOICES = [
        (STATUT_PRESENT, "حاضر"),
        (STATUT_ABSENT, "غائب"),
        (STATUT_REPOS, "راحة أسبوعية"),
        (STATUT_CONGE, "في عطلة مدفوعة"),
    ]

    employe = models.ForeignKey(
        Employe,
        on_delete=models.CASCADE,
        related_name="pointages",
        verbose_name="العامل",
    )
    date = models.DateField(verbose_name="التاريخ")
    statut = models.CharField(
        max_length=10,
        choices=STATUT_CHOICES,
        default=STATUT_PRESENT,
        verbose_name="الحالة",
    )
    heures_supplementaires = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        verbose_name="ساعات إضافية",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تسجيل حضور"
        verbose_name_plural = "تسجيلات الحضور"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["employe", "date"], name="unique_pointage_par_jour"
            )
        ]

    def __str__(self):
        return f"{self.employe.nom_complet} — {self.date} — {self.get_statut_display()}"

    def clean(self):
        if (
            self.statut in (self.STATUT_REPOS, self.STATUT_ABSENT)
            and self.heures_supplementaires
        ):
            raise ValidationError(
                {
                    "heures_supplementaires": (
                        "لا يمكن تسجيل ساعات إضافية في يوم راحة أو غياب."
                    )
                }
            )

    @property
    def branche(self):
        """v1.4 — inherited from employe.branche (BR-BRA-09), not stored."""
        return self.employe.branche if self.employe_id else None


class CongeEmploye(models.Model):
    """
    A block of paid leave taken by an employee (BR-RH-03).

    `nb_jours` is auto-computed (if left blank) as the count of days in
    [date_debut, date_fin] that are NOT the employee's scheduled rest day —
    those are the only days actually debited from the leave balance.
    Saving a CongeEmploye does not, by itself, touch Pointage; the view
    layer calls depenses.utils.appliquer_conge_aux_pointages() so the
    payroll calculation always reads from Pointage as the single source
    of truth (BR-RH-05).
    """

    employe = models.ForeignKey(
        Employe, on_delete=models.CASCADE, related_name="conges", verbose_name="العامل"
    )
    date_debut = models.DateField(verbose_name="من تاريخ")
    date_fin = models.DateField(verbose_name="إلى تاريخ")
    nb_jours = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="عدد الأيام المخصومة"
    )
    motif = models.CharField(max_length=255, blank=True, verbose_name="السبب")
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "عطلة عامل"
        verbose_name_plural = "عطل العمال"
        ordering = ["-date_debut"]

    def __str__(self):
        return f"{self.employe.nom_complet} — {self.date_debut} → {self.date_fin}"

    @property
    def branche(self):
        """v1.4 — inherited from employe.branche (BR-BRA-09), not stored."""
        return self.employe.branche if self.employe_id else None

    def clean(self):
        if self.date_fin and self.date_debut and self.date_fin < self.date_debut:
            raise ValidationError(
                {"date_fin": "تاريخ النهاية يجب أن يكون بعد تاريخ البداية."}
            )

    def save(self, *args, **kwargs):
        if not self.nb_jours and self.employe_id and self.date_debut and self.date_fin:
            import datetime

            jour = self.date_debut
            jours_decompres = 0
            while jour <= self.date_fin:
                if jour.weekday() != self.employe.jour_repos_habituel:
                    jours_decompres += 1
                jour += datetime.timedelta(days=1)
            self.nb_jours = jours_decompres
        super().save(*args, **kwargs)


class AcompteEmploye(models.Model):
    """
    A salary advance (acompte) given to an employee (BR-RH-04).

    Counted as a cash outflow on `date` in get_cash_flow_summary(). Once
    the month-end payslip is generated, it is linked via `bulletin_paie`
    and deducted from that payslip's montant_net — never inserted into
    the generic Depense table.
    """

    employe = models.ForeignKey(
        Employe,
        on_delete=models.PROTECT,
        related_name="acomptes",
        verbose_name="العامل",
    )
    date = models.DateField(verbose_name="تاريخ التسبيق")
    montant = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="المبلغ (د.ج)",
        validators=[MinValueValidator(0.01)],
    )
    mode_paiement = models.CharField(
        max_length=20,
        choices=Depense.MODE_CHOICES,
        default=Depense.MODE_ESPECES,
        verbose_name="طريقة الدفع",
    )
    motif = models.CharField(max_length=255, blank=True, verbose_name="السبب")
    bulletin_paie = models.ForeignKey(
        "BulletinPaie",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acomptes_deduits",
        verbose_name="مخصوم من كشف الراتب",
    )
    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    enregistre_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acomptes_employes_enregistres",
        verbose_name="مسجّل من قبل",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تسبيق على الراتب"
        verbose_name_plural = "تسبيقات على الرواتب"
        ordering = ["-date"]

    def __str__(self):
        status = "مخصوم" if self.bulletin_paie_id else "قيد الانتظار"
        return f"{self.employe.nom_complet} — {self.montant} DZD [{status}]"

    @property
    def deduit(self) -> bool:
        return bool(self.bulletin_paie_id)

    @property
    def branche(self):
        """v1.4 — inherited from employe.branche (BR-BRA-09), not stored."""
        return self.employe.branche if self.employe_id else None


class BulletinPaie(models.Model):
    """
    Monthly payslip, auto-calculated from Pointage rows (BR-RH-05).

    All amount/day fields are SNAPSHOTS taken at calculation time, so the
    payslip stays historically accurate even if the employee's base salary
    or rest day later changes. See depenses.utils.calculer_donnees_paie()
    for the computation; views call it then persist the result here.
    """

    STATUT_BROUILLON = "brouillon"
    STATUT_VALIDE = "valide"
    STATUT_PAYE = "paye"

    STATUT_CHOICES = [
        (STATUT_BROUILLON, "مسودة"),
        (STATUT_VALIDE, "مصادق عليه"),
        (STATUT_PAYE, "مدفوع"),
    ]

    employe = models.ForeignKey(
        Employe,
        on_delete=models.PROTECT,
        related_name="bulletins_paie",
        verbose_name="العامل",
    )
    annee = models.PositiveSmallIntegerField(verbose_name="السنة")
    mois = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name="الشهر",
    )

    # Snapshot of attendance for the period (BR-RH-05)
    jours_presence = models.PositiveSmallIntegerField(
        default=0, verbose_name="أيام الحضور"
    )
    jours_absence = models.PositiveSmallIntegerField(
        default=0, verbose_name="أيام الغياب"
    )
    jours_repos = models.PositiveSmallIntegerField(
        default=0, verbose_name="أيام الراحة"
    )
    jours_conge = models.PositiveSmallIntegerField(
        default=0, verbose_name="أيام العطلة المدفوعة"
    )
    total_heures_supplementaires = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="إجمالي الساعات الإضافية",
    )

    # Snapshot of pay parameters + computed amounts
    salaire_base_reference = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="الراتب الأساسي المرجعي"
    )
    taux_journalier = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="السعر اليومي"
    )
    montant_heures_sup = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="مبلغ الساعات الإضافية",
    )
    montant_brut = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="المبلغ الإجمالي (خام)"
    )
    total_acomptes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="إجمالي التسبيقات",
    )
    montant_net = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="المبلغ الصافي"
    )

    statut = models.CharField(
        max_length=10,
        choices=STATUT_CHOICES,
        default=STATUT_BROUILLON,
        verbose_name="الحالة",
    )
    date_paiement = models.DateField(null=True, blank=True, verbose_name="تاريخ الدفع")
    mode_paiement = models.CharField(
        max_length=20,
        choices=Depense.MODE_CHOICES,
        default=Depense.MODE_VIREMENT,
        verbose_name="طريقة الدفع",
    )

    notes = models.TextField(blank=True, verbose_name="ملاحظات")
    genere_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulletins_paie_generes",
        verbose_name="أنشئ من قبل",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "كشف راتب"
        verbose_name_plural = "كشوف الرواتب"
        ordering = ["-annee", "-mois"]
        constraints = [
            models.UniqueConstraint(
                fields=["employe", "annee", "mois"], name="unique_bulletin_par_mois"
            )
        ]

    def __str__(self):
        return (
            f"{self.employe.nom_complet} — {self.mois:02d}/{self.annee} — "
            f"{self.montant_net} DZD [{self.get_statut_display()}]"
        )

    @property
    def periode_label(self) -> str:
        return f"{self.mois:02d}/{self.annee}"

    @property
    def branche(self):
        """v1.4 — inherited from employe.branche (BR-BRA-09), not stored."""
        return self.employe.branche if self.employe_id else None
