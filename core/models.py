from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


class CompanyInfo(models.Model):
    """
    Singleton model holding the farm / company identity used on
    printed documents (BL, factures, etc.).
    Enforced as a singleton via save() override.
    """

    nom = models.CharField(max_length=255, verbose_name="اسم الشركة")
    adresse = models.TextField(verbose_name="العنوان")
    wilaya = models.CharField(max_length=100, verbose_name="الولاية", blank=True)
    telephone = models.CharField(max_length=30, verbose_name="الهاتف", blank=True)
    telephone_2 = models.CharField(max_length=30, verbose_name="الهاتف 2", blank=True)
    email = models.EmailField(verbose_name="البريد الإلكتروني", blank=True)
    nif = models.CharField(
        max_length=50, verbose_name="NIF (الرقم الجبائي)", blank=True
    )
    rc = models.CharField(max_length=50, verbose_name="RC (السجل التجاري)", blank=True)
    ai = models.CharField(
        max_length=50, verbose_name="AI (المادة الضريبية)", blank=True
    )
    nis = models.CharField(
        max_length=50,
        verbose_name="NIS (رقم التعريف الإحصائي)",
        blank=True,
    )
    logo = models.ImageField(
        upload_to="company/", verbose_name="الشعار", blank=True, null=True
    )
    pied_de_page = models.TextField(
        verbose_name="تذييل الوثائق",
        blank=True,
        help_text="النص الظاهر أسفل الفواتير ووصولات التسليم المطبوعة.",
    )

    # --------------- Fiscal / Tax information ---------------
    REGIME_REEL = "reel"
    REGIME_FORFAIT = "forfait"
    REGIME_EXONERE = "exonere"
    REGIME_CHOICES = [
        (REGIME_REEL, "النظام الحقيقي"),
        (REGIME_FORFAIT, "النظام الجزافي"),
        (REGIME_EXONERE, "معفى"),
    ]

    regime_fiscal = models.CharField(
        max_length=20,
        choices=REGIME_CHOICES,
        default=REGIME_REEL,
        verbose_name="النظام الضريبي",
        blank=True,
    )
    assujetti_tva = models.BooleanField(
        default=True,
        verbose_name="خاضع للضريبة على القيمة المضافة",
        help_text="أزل التحديد إذا كانت الشركة معفاة من الضريبة على القيمة المضافة.",
    )
    taux_tva = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=19.00,
        verbose_name="نسبة الضريبة على القيمة المضافة (%)",
        help_text="نسبة الضريبة المطبقة افتراضياً على الوثائق (بالمئة).",
    )
    # Additional Algerian tax identifiers
    tap = models.CharField(
        max_length=50,
        verbose_name="TAP (رسم النشاط المهني)",
        blank=True,
    )
    rib = models.CharField(
        max_length=50,
        verbose_name="RIB / حساب بنكي",
        blank=True,
    )
    banque = models.CharField(
        max_length=150,
        verbose_name="البنك",
        blank=True,
    )
    # --------------- System / Application settings ---------------
    devise = models.CharField(
        max_length=10,
        default="DZD",
        verbose_name="العملة",
    )
    format_date = models.CharField(
        max_length=20,
        default="DD/MM/YYYY",
        verbose_name="صيغة عرض التاريخ",
        help_text="شكلي فقط — قاعدة البيانات تخزن بصيغة ISO 8601.",
    )
    prefixe_bl_client = models.CharField(
        max_length=10,
        default="BLC",
        verbose_name="بادئة وصل تسليم العميل",
    )
    prefixe_bl_fournisseur = models.CharField(
        max_length=10,
        default="BLF",
        verbose_name="بادئة وصل تسليم المورد",
    )
    prefixe_facture_client = models.CharField(
        max_length=10,
        default="FAC",
        verbose_name="بادئة فاتورة العميل",
    )
    prefixe_facture_fournisseur = models.CharField(
        max_length=10,
        default="FRN",
        verbose_name="بادئة فاتورة المورد",
    )

    class Meta:
        verbose_name = "معلومات الشركة"
        verbose_name_plural = "معلومات الشركة"

    def __str__(self):
        return self.nom

    def save(self, *args, **kwargs):
        # Singleton: only one record allowed.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_instance(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"nom": "تربية الدواجن"})
        return obj


# ---------------------------------------------------------------------------
# Branche (v1.4 — Multi-Branch Architecture, spec §3.5)
# ---------------------------------------------------------------------------


class Branche(models.Model):
    """
    An operational branch (site) of the one company described by CompanyInfo.

    From v1.4 the farm can run several branches — e.g. a poussinière/
    poulailler complex per wilaya — each with its own buildings, lots,
    stock, documents, and dépenses (spec §3.5.3). CompanyInfo itself stays
    a single company-wide singleton; branches are subdivisions inside it,
    not separate companies (§3.4).

    `code` is embedded in every generated document reference number
    (`<prefix>-<code_branche>-<YYYY>-<NNNN>`, §3.5.4 / BR-BRA-05) so
    numbering sequences never collide across branches — keep it short
    and stable once documents have been issued under it.

    Deleting a Branche is a deliberate, admin-only action from the admin
    site. Every branch-scoped model (StockIntrant, StockProduitFini,
    StockMouvement, StockAjustement, UserProfile, ...) points back here
    with on_delete=CASCADE, so deleting a branch wipes all of its stock,
    movement/adjustment history, and detaches (deletes) any UserProfile
    bound to it in one action, with no separate decommissioning step —
    this is an intentional design choice, not an oversight.
    """

    nom = models.CharField(max_length=150, verbose_name="اسم الفرع")
    code = models.CharField(
        max_length=10,
        unique=True,
        verbose_name="الرمز",
        help_text="رمز قصير فريد يُستخدم في ترقيم الوثائق (مثال: EST, OUEST).",
    )
    wilaya = models.CharField(max_length=100, verbose_name="الولاية", blank=True)
    adresse = models.TextField(verbose_name="العنوان", blank=True)
    telephone = models.CharField(max_length=30, verbose_name="الهاتف", blank=True)
    chef_de_branche = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branche_dirigee",
        verbose_name="رئيس الفرع",
        help_text="مستخدم بدور 'رئيس فرع' — مرتبط بهذا الفرع حصرياً (BR-BRA-02).",
    )
    actif = models.BooleanField(default=True, verbose_name="نشط")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "فرع"
        verbose_name_plural = "الفروع"
        ordering = ["nom"]

    def __str__(self):
        return f"{self.nom} ({self.code})"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        from django.core.exceptions import ValidationError

        if (
            self.chef_de_branche_id
            and hasattr(self.chef_de_branche, "profile")
            and self.chef_de_branche.profile.role != UserProfile.ROLE_CHEF_BRANCHE
        ):
            raise ValidationError(
                {
                    "chef_de_branche": (
                        "BR-BRA-02 : يجب أن يحمل المستخدم المعيّن رئيساً "
                        "للفرع الدور 'رئيس فرع'."
                    )
                }
            )


class UserProfile(models.Model):
    """
    Extends Django's built-in User with farm-specific role information.

    Branch binding (spec §3.5.2 / BR-BRA-02, BR-BRA-03):
      - admin       : `branche` always None. Not bound to a single branch —
                       switches the *active* branch context per session
                       (session-level concept, outside this model) or works
                       in Vue Globale.
      - chef_branche / operateur : `branche` is REQUIRED — locked to exactly
                       one branch; every record they create/see is
                       implicitly filtered to it, with no switcher.
      - comptable   : `branche` is OPTIONAL — set for branch-only visibility,
                       left None for company-wide (Vue Globale) visibility.

    `branche` uses on_delete=CASCADE (not PROTECT): deleting a Branche from
    the admin is a deliberate wipe, and this profile goes with it — this
    ALSO deletes the underlying User's profile row outright (not just
    unbinds it), so a chef_branche/operateur whose only branch was deleted
    loses their profile entirely and `request.user.profile` will raise
    RelatedObjectDoesNotExist for that account until a new profile is
    created for them. This is an accepted tradeoff of "delete = fully
    gone, no extra steps" (stakeholder decision) — reassign or recreate
    affected users' profiles manually after a branch deletion.
    """

    ROLE_ADMIN = "admin"
    ROLE_MANAGER = "manager"
    ROLE_CHEF_BRANCHE = "chef_branche"
    ROLE_OPERATEUR = "operateur"
    ROLE_COMPTABLE = "comptable"

    ROLE_CHOICES = [
        (ROLE_ADMIN, "مدير"),
        (ROLE_MANAGER, "مسيّر"),
        (ROLE_CHEF_BRANCHE, "رئيس فرع"),
        (ROLE_OPERATEUR, "مشغّل"),
        (ROLE_COMPTABLE, "محاسب"),
    ]

    #: Roles that MUST be bound to exactly one branch (BR-BRA-02).
    ROLES_LIES_A_UNE_BRANCHE = (ROLE_CHEF_BRANCHE, ROLE_OPERATEUR)

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_OPERATEUR,
        verbose_name="الدور",
    )
    branche = models.ForeignKey(
        Branche,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="utilisateurs",
        verbose_name="الفرع",
        help_text=(
            "إلزامي لرئيس الفرع والمشغّل (BR-BRA-02). اختياري للمحاسب "
            "(فارغ = رؤية شاملة لجميع الفروع). يُترك فارغاً دائماً للمدير."
        ),
    )
    telephone = models.CharField(max_length=30, verbose_name="الهاتف", blank=True)
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    # BR-RH-06 — links this login account to the RH Employe record it was
    # auto-provisioned for (depenses.provisionner_compte_operateur). Only
    # set for opérateur accounts created that way — a manually-created
    # opérateur (or any other role) leaves this null. Used to scope such
    # accounts to the التربية (elevage) app only, further restricted to
    # their own bâtiment's OPEN lots (see est_operateur_terrain below).
    #
    # on_delete=CASCADE mirrors the `branche` field's tradeoff on this same
    # model: deleting the Employe is a deliberate RH action, and the linked
    # login's profile goes with it (the underlying User row survives,
    # profile-less, same as a deleted-branche chef_branche/opérateur).
    employe = models.OneToOneField(
        "depenses.Employe",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="compte_utilisateur",
        verbose_name="العامل المرتبط",
        help_text="يُملأ تلقائياً عند إنشاء حساب مشغّل من بطاقة عامل (RH) — BR-RH-06.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ملف المستخدم"
        verbose_name_plural = "ملفات المستخدمين"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.role in self.ROLES_LIES_A_UNE_BRANCHE and not self.branche_id:
            raise ValidationError(
                {"branche": ("BR-BRA-02 : هذا الدور يتطلب تحديد فرع واحد إلزامياً.")}
            )
        if self.role == self.ROLE_ADMIN and self.branche_id:
            raise ValidationError(
                {
                    "branche": (
                        "BR-BRA-03 : المدير غير مرتبط بفرع واحد — اترك هذا "
                        "الحقل فارغاً (يتم تبديل الفرع النشط عبر الجلسة)."
                    )
                }
            )
        if self.employe_id and self.role != self.ROLE_OPERATEUR:
            raise ValidationError(
                {"employe": ("BR-RH-06 : حساب مرتبط بعامل يجب أن يكون دوره «مشغّل».")}
            )

    @property
    def a_vue_globale(self):
        """
        True when this user can see/aggregate data across ALL branches
        (admin always; comptable only when left unbound) — §3.5.2 / BR-BRA-04.
        """
        return self.role == self.ROLE_ADMIN or (
            self.role == self.ROLE_COMPTABLE and self.branche_id is None
        )

    @property
    def peut_changer_de_branche(self):
        """True for roles that get a branch switcher in the UI (§3.5.4)."""
        return self.a_vue_globale

    @property
    def est_operateur_terrain(self):
        """
        True for an opérateur account auto-provisioned from an RH Employe
        record (BR-RH-06). These accounts are scoped tighter than a regular
        opérateur: limited to the التربية (elevage) app, and — within it —
        only to their own bâtiment's currently-open lots (enforced in
        elevage.views, not here; this flag just marks the account).
        """
        return self.role == self.ROLE_OPERATEUR and self.employe_id is not None


# ---------------------------------------------------------------------------
# PieceJointe — generic document-proof model (replaces the ad-hoc
# `piece_jointe` FileField previously duplicated on BLFournisseur, Depense,
# RetraitAssocie, ...). Attaches to ANY model (BL, facture, règlement/
# paiement, dépense, retrait, ...) via ContentType, and supports MULTIPLE
# files per record (a facture may need the scanned invoice + a bank
# transfer confirmation + a delivery signature, for example).
# ---------------------------------------------------------------------------


class PieceJointe(models.Model):
    """
    A single proof/attachment file linked to any other model instance
    via GenericForeignKey. Use the reverse `GenericRelation` declared on
    the target model (e.g. `bl.pieces_jointes.all()`) to query/attach.
    """

    TYPE_FACTURE = "facture"
    TYPE_RECU = "recu"
    TYPE_VIREMENT = "virement"
    TYPE_BL = "bl"
    TYPE_CHEQUE = "cheque"
    TYPE_PHOTO = "photo"
    TYPE_AUTRE = "autre"

    TYPE_CHOICES = [
        (TYPE_FACTURE, "فاتورة (نسخة ممسوحة)"),
        (TYPE_RECU, "إيصال"),
        (TYPE_VIREMENT, "تأكيد تحويل بنكي"),
        (TYPE_BL, "وصل تسليم (نسخة ممسوحة)"),
        (TYPE_CHEQUE, "صورة الشيك"),
        (TYPE_PHOTO, "صورة"),
        (TYPE_AUTRE, "أخرى"),
    ]

    # --- Generic link to the owning record (BL, facture, règlement, ...) ---
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="pieces_jointes",
        verbose_name="نوع السجل",
    )
    object_id = models.PositiveIntegerField(verbose_name="معرّف السجل")
    content_object = GenericForeignKey("content_type", "object_id")

    fichier = models.FileField(
        upload_to="pieces_jointes/%Y/%m/",
        verbose_name="الملف (PDF/JPG/PNG)",
    )
    type_document = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_AUTRE,
        verbose_name="نوع الوثيقة",
    )
    description = models.CharField(max_length=200, blank=True, verbose_name="وصف مختصر")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pieces_jointes_ajoutees",
        verbose_name="أضيف من قبل",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإضافة")

    class Meta:
        verbose_name = "مرفق / وثيقة إثبات"
        verbose_name_plural = "المرفقات / وثائق الإثبات"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.get_type_document_display()} — {self.content_object} ({self.created_at:%Y-%m-%d})"
