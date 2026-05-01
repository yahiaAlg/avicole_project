from django.db import models
from django.contrib.auth.models import User


class CompanyInfo(models.Model):
    """
    Singleton model holding the farm / company identity used on
    printed documents (BL, factures, etc.).
    Enforced as a singleton via save() override.
    """

    nom = models.CharField(max_length=255, verbose_name="Nom de l'entreprise")
    adresse = models.TextField(verbose_name="Adresse")
    wilaya = models.CharField(max_length=100, verbose_name="Wilaya", blank=True)
    telephone = models.CharField(max_length=30, verbose_name="Téléphone", blank=True)
    telephone_2 = models.CharField(
        max_length=30, verbose_name="Téléphone 2", blank=True
    )
    email = models.EmailField(verbose_name="Email", blank=True)
    nif = models.CharField(
        max_length=50, verbose_name="NIF (Numéro d'identification fiscale)", blank=True
    )
    rc = models.CharField(
        max_length=50, verbose_name="RC (Registre de Commerce)", blank=True
    )
    ai = models.CharField(
        max_length=50, verbose_name="AI (Article d'imposition)", blank=True
    )
    nis = models.CharField(
        max_length=50,
        verbose_name="NIS (Numéro d'identification statistique)",
        blank=True,
    )
    logo = models.ImageField(
        upload_to="company/", verbose_name="Logo", blank=True, null=True
    )
    pied_de_page = models.TextField(
        verbose_name="Pied de page des documents",
        blank=True,
        help_text="Texte affiché en bas des factures et BL imprimés.",
    )

    # --------------- Fiscal / Tax information ---------------
    REGIME_REEL = "reel"
    REGIME_FORFAIT = "forfait"
    REGIME_EXONERE = "exonere"
    REGIME_CHOICES = [
        (REGIME_REEL, "Régime réel"),
        (REGIME_FORFAIT, "Régime forfaitaire"),
        (REGIME_EXONERE, "Exonéré"),
    ]

    regime_fiscal = models.CharField(
        max_length=20,
        choices=REGIME_CHOICES,
        default=REGIME_REEL,
        verbose_name="Régime fiscal",
        blank=True,
    )
    assujetti_tva = models.BooleanField(
        default=True,
        verbose_name="Assujetti à la TVA",
        help_text="Décocher si l'entreprise est exonérée de TVA.",
    )
    taux_tva = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=19.00,
        verbose_name="Taux TVA (%)",
        help_text="Taux de TVA appliqué par défaut sur les documents (en %).",
    )
    # Additional Algerian tax identifiers
    tap = models.CharField(
        max_length=50,
        verbose_name="TAP (Taxe sur l'Activité Professionnelle)",
        blank=True,
    )
    rib = models.CharField(
        max_length=50,
        verbose_name="RIB / Compte bancaire",
        blank=True,
    )
    banque = models.CharField(
        max_length=150,
        verbose_name="Banque",
        blank=True,
    )
    # --------------- System / Application settings ---------------
    devise = models.CharField(
        max_length=10,
        default="DZD",
        verbose_name="Devise",
    )
    format_date = models.CharField(
        max_length=20,
        default="DD/MM/YYYY",
        verbose_name="Format de date affiché",
        help_text="Cosmétique uniquement — la base de données stocke ISO 8601.",
    )
    prefixe_bl_client = models.CharField(
        max_length=10,
        default="BLC",
        verbose_name="Préfixe BL Client",
    )
    prefixe_bl_fournisseur = models.CharField(
        max_length=10,
        default="BLF",
        verbose_name="Préfixe BL Fournisseur",
    )
    prefixe_facture_client = models.CharField(
        max_length=10,
        default="FAC",
        verbose_name="Préfixe Facture Client",
    )
    prefixe_facture_fournisseur = models.CharField(
        max_length=10,
        default="FRN",
        verbose_name="Préfixe Facture Fournisseur",
    )

    class Meta:
        verbose_name = "Informations de l'entreprise"
        verbose_name_plural = "Informations de l'entreprise"

    def __str__(self):
        return self.nom

    def save(self, *args, **kwargs):
        # Singleton: only one record allowed.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_instance(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"nom": "Élevage Avicole"})
        return obj


class UserProfile(models.Model):
    """
    Extends Django's built-in User with farm-specific role information.
    """

    ROLE_CHOICES = [
        ("admin", "Administrateur"),
        ("manager", "Gérant"),
        ("operateur", "Opérateur"),
        ("comptable", "Comptable"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default="operateur", verbose_name="Rôle"
    )
    telephone = models.CharField(max_length=30, verbose_name="Téléphone", blank=True)
    notes = models.TextField(verbose_name="Notes", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"
