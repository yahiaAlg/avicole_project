from django.db import models
from django.contrib.auth.models import User


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


class UserProfile(models.Model):
    """
    Extends Django's built-in User with farm-specific role information.
    """

    ROLE_CHOICES = [
        ("admin", "مدير"),
        ("manager", "مسيّر"),
        ("operateur", "مشغّل"),
        ("comptable", "محاسب"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default="operateur", verbose_name="الدور"
    )
    telephone = models.CharField(max_length=30, verbose_name="الهاتف", blank=True)
    notes = models.TextField(verbose_name="ملاحظات", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ملف المستخدم"
        verbose_name_plural = "ملفات المستخدمين"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"
