"""
management/commands/seed_db_minimal.py

تهيئة قاعدة البيانات الأولية — التصنيفات والإعدادات الثابتة فقط.

الاستخدام:
    python manage.py seed_db_minimal          # تعبئة (آمن للتكرار)
    python manage.py seed_db_minimal --clear  # مسح التصنيفات ثم إعادة التعبئة

ما يتم إنشاؤه:
    • CompanyInfo           — بيانات الشركة (pk=1)
    • ParametrageElevage    — إعدادات التربية الافتراضية (pk=1)
    • Users (4)             — admin / gerant / operateur1 / comptable
    • CategorieIntrant      — ALIMENT / POUSSIN / MEDICAMENT / AUTRE
    • TypeFournisseur       — ALIMENTS / POUSSINS / MEDICAMENTS / SERVICES / AUTRE
    • CategorieDepense      — SALAIRES / ENERGIE / MAINTENANCE / TRANSPORT /
                              VETERINAIRE / FOURNITURES / TAXES / DIVERS
    • CategorieQualite (8)  — 4 tranches oiseaux + 4 tranches œufs
    • ProduitFini (7)       — دجاج حي / جثة / صدر / فخذ / جناح / كبد / قانصة

ما لا يتم إنشاؤه (يُسجَّل يدوياً عبر الواجهة):
    • Fournisseurs  • Clients  • Bâtiments  • Intrants
    • أي بيانات تشغيلية (BL، فواتير، دفعات، مخزون …)

آمن للتكرار: يستخدم get_or_create في كل مكان.
جميع المبالغ بالدينار الجزائري (DZD).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = (
        "تهيئة قاعدة البيانات بالتصنيفات الثابتة والإعدادات الأولية فقط.\n"
        "لا يُنشئ موردين، عملاء، مباني، أو مدخلات."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "احذف جميع التصنيفات والمستخدمين وبيانات الشركة "
                "قبل إعادة التعبئة. "
                "تحذير: سيفشل إذا كانت هناك سجلات تشغيلية مرتبطة."
            ),
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            self._clear()

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "\n=== seed_db_minimal — تهيئة التصنيفات الأولية ===\n"
            )
        )

        self._seed_company()
        self._seed_parametrage_elevage()
        self._seed_users()
        self._seed_categories_intrant()
        self._seed_types_fournisseur()
        self._seed_categories_depense()
        self._seed_categories_qualite()
        self._seed_produits_finis()

        self.stdout.write(
            self.style.SUCCESS(
                "\n✓ Minimal seed terminé.\n"
                "  Étapes suivantes :\n"
                "    1. Créer les fournisseurs  → ACHATS › Fournisseurs › Nouveau\n"
                "    2. Créer les clients       → VENTES › Clients › Nouveau\n"
                "    3. Créer les bâtiments     → STOCK › Bâtiments › Nouveau\n"
                "    4. Créer les intrants      → STOCK › Intrants › Nouvel intrant\n"
                "    5. Lancer le cycle ERP     → scénario Phase 1 — Achats Intrants\n"
            )
        )

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(self):
        self.stdout.write(self.style.WARNING("  Suppression des données de base…"))
        from intrants.models import CategorieIntrant, CategorieQualite, TypeFournisseur
        from depenses.models import CategorieDepense
        from production.models import ProduitFini
        from core.models import CompanyInfo, UserProfile
        from elevage.models import ParametrageElevage

        ProduitFini.objects.all().delete()
        CategorieQualite.objects.all().delete()
        CategorieDepense.objects.all().delete()
        CategorieIntrant.objects.all().delete()
        TypeFournisseur.objects.all().delete()
        UserProfile.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        CompanyInfo.objects.all().delete()
        ParametrageElevage.objects.all().delete()
        self.stdout.write("  Terminé.\n")

    # ------------------------------------------------------------------
    # Seeders
    # ------------------------------------------------------------------

    def _seed_company(self):
        from core.models import CompanyInfo

        obj, created = CompanyInfo.objects.get_or_create(
            pk=1,
            defaults=dict(
                nom="Élevage Avicole Setifien",
                adresse="Zone Industrielle, Route Nationale 12",
                wilaya="Setifien",
                telephone="0555 123 456",
                telephone_2="0770 987 654",
                email="contact@avicole-to.dz",
                nif="099123456789012",
                rc="16/00-1234567 B 12",
                ai="16123456789",
                nis="099123456789012345",
                regime_fiscal=CompanyInfo.REGIME_REEL,
                assujetti_tva=True,
                taux_tva=Decimal("19.00"),
                rib="00799999000123456789 12",
                banque="BNA — Agence Setifien Centre",
                tap="16970099123456",
                devise="DZD",
                pied_de_page=(
                    "Merci de votre confiance — Élevage Avicole Setifien\n"
                    "Tél : 0555 123 456 | Email : contact@avicole-to.dz"
                ),
                prefixe_bl_client="BLC",
                prefixe_bl_fournisseur="BLF",
                prefixe_facture_client="FAC",
                prefixe_facture_fournisseur="FRN",
            ),
        )
        self._log("CompanyInfo", created)

    def _seed_parametrage_elevage(self):
        from elevage.models import ParametrageElevage

        obj, created = ParametrageElevage.objects.get_or_create(
            pk=1,
            defaults=dict(
                age_transfert_poussiniere_jours=21,
                age_maturite_vente_jours=35,
            ),
        )
        self._log("ParametrageElevage", created)

    def _seed_users(self):
        from core.models import UserProfile

        specs = [
            dict(
                username="admin",
                first_name="Karim",
                last_name="Meziani",
                email="admin@avicole.dz",
                is_superuser=True,
                is_staff=True,
                role="admin",
                password="admin1234",
            ),
            dict(
                username="gerant",
                first_name="Lynda",
                last_name="Aoudia",
                email="gerant@avicole.dz",
                is_superuser=False,
                is_staff=True,
                role="manager",
                password="gerant1234",
            ),
            dict(
                username="operateur1",
                first_name="Samir",
                last_name="Boudiaf",
                email="op1@avicole.dz",
                is_superuser=False,
                is_staff=False,
                role="operateur",
                password="op1_1234",
            ),
            dict(
                username="comptable",
                first_name="Nadia",
                last_name="Hamdi",
                email="comptable@avicole.dz",
                is_superuser=False,
                is_staff=False,
                role="comptable",
                password="compta1234",
            ),
        ]
        created_count = 0
        for spec in specs:
            role = spec.pop("role")
            password = spec.pop("password")
            user, created = User.objects.get_or_create(
                username=spec["username"], defaults=spec
            )
            if created:
                user.set_password(password)
                user.save()
                created_count += 1
            UserProfile.objects.get_or_create(user=user, defaults={"role": role})
        self._log(f"Users ({len(specs)})", created_count > 0)

    def _seed_categories_intrant(self):
        from intrants.models import CategorieIntrant

        seeds = [
            dict(
                code="ALIMENT",
                libelle="علف",
                consommable_en_lot=True,
                ordre=1,
                actif=True,
            ),
            dict(
                code="POUSSIN",
                libelle="كتكوت (دواجن حية)",
                consommable_en_lot=False,
                ordre=2,
                actif=True,
            ),
            dict(
                code="MEDICAMENT",
                libelle="دواء / بيطري",
                consommable_en_lot=True,
                ordre=3,
                actif=True,
            ),
            dict(
                code="AUTRE",
                libelle="مدخل آخر",
                consommable_en_lot=False,
                ordre=4,
                actif=True,
            ),
        ]
        for s in seeds:
            CategorieIntrant.objects.get_or_create(code=s["code"], defaults=s)
        self._log("CategorieIntrant (4)", True)

    def _seed_types_fournisseur(self):
        from intrants.models import TypeFournisseur

        seeds = [
            dict(code="ALIMENTS", libelle="أعلاف", ordre=1, actif=True),
            dict(code="POUSSINS", libelle="كتاكيت", ordre=2, actif=True),
            dict(code="MEDICAMENTS", libelle="أدوية / بيطريين", ordre=3, actif=True),
            dict(code="SERVICES", libelle="خدمات", ordre=4, actif=True),
            dict(code="AUTRE", libelle="أخرى", ordre=5, actif=True),
        ]
        for s in seeds:
            TypeFournisseur.objects.get_or_create(code=s["code"], defaults=s)
        self._log("TypeFournisseur (5)", True)

    def _seed_categories_depense(self):
        from depenses.models import CategorieDepense

        seeds = [
            dict(
                code="SALAIRES",
                libelle="الرواتب والأجور",
                ordre=1,
                description="رواتب العمال والعمال اليوميين",
            ),
            dict(
                code="ENERGIE",
                libelle="الطاقة (كهرباء / غاز)",
                ordre=2,
                description="فواتير الكهرباء والغاز ووقود التدفئة",
            ),
            dict(
                code="MAINTENANCE",
                libelle="الصيانة والإصلاحات",
                ordre=3,
                description="إصلاح المعدات والمباني",
            ),
            dict(
                code="TRANSPORT",
                libelle="النقل والوقود",
                ordre=4,
                description="وقود مركبات التوصيل",
            ),
            dict(
                code="VETERINAIRE",
                libelle="المصاريف البيطرية",
                ordre=5,
                description="أتعاب البيطريين (باستثناء الأدوية)",
            ),
            dict(
                code="FOURNITURES",
                libelle="اللوازم والتغليف",
                ordre=6,
                description="أكياس، صناديق، تغليف، لوازم مكتبية",
            ),
            dict(
                code="TAXES",
                libelle="الضرائب والرسوم",
                ordre=7,
                description="الضرائب المحلية، الرسوم التجارية، الحقوق المتنوعة",
            ),
            dict(
                code="DIVERS",
                libelle="مصاريف متنوعة",
                ordre=8,
                description="كل مصروف غير مشمول في الفئات أعلاه",
            ),
        ]
        for s in seeds:
            CategorieDepense.objects.get_or_create(code=s["code"], defaults=s)
        self._log("CategorieDepense (8)", True)

    def _seed_categories_qualite(self):
        """
        Seed default quality-grading brackets for birds and eggs.

        Birds (oiseaux) — graded by average live weight in grams:
          S / Standard / L / XL

        Eggs (oeufs) — graded by average egg weight in grams:
          S / M / L / XL  (aligned with EU egg-grading norms)

        Administrators may add, rename, or adjust ranges freely; the `code`
        field is the programmatic key so it should not be renamed.
        """
        from intrants.models import CategorieQualite

        seeds = [
            # ── Oiseaux (live bird weight grades) ─────────────────────
            dict(
                code="S",
                type_pesee=CategorieQualite.TYPE_OISEAUX,
                libelle="Petit (S)",
                poids_min=Decimal("1400.00"),
                poids_max=Decimal("1800.00"),
                ordre=1,
                actif=True,
            ),
            dict(
                code="STANDARD",
                type_pesee=CategorieQualite.TYPE_OISEAUX,
                libelle="Standard",
                poids_min=Decimal("1800.00"),
                poids_max=Decimal("2200.00"),
                ordre=2,
                actif=True,
            ),
            dict(
                code="L",
                type_pesee=CategorieQualite.TYPE_OISEAUX,
                libelle="Grand (L)",
                poids_min=Decimal("2200.00"),
                poids_max=Decimal("2600.00"),
                ordre=3,
                actif=True,
            ),
            dict(
                code="XL",
                type_pesee=CategorieQualite.TYPE_OISEAUX,
                libelle="Très grand (XL)",
                poids_min=Decimal("2600.00"),
                poids_max=Decimal("3500.00"),
                ordre=4,
                actif=True,
            ),
            # ── Oeufs (egg weight grades, EU norm) ────────────────────
            dict(
                code="S",
                type_pesee=CategorieQualite.TYPE_OEUFS,
                libelle="Petit (S)",
                poids_min=Decimal("43.00"),
                poids_max=Decimal("53.00"),
                ordre=1,
                actif=True,
            ),
            dict(
                code="M",
                type_pesee=CategorieQualite.TYPE_OEUFS,
                libelle="Moyen (M)",
                poids_min=Decimal("53.00"),
                poids_max=Decimal("63.00"),
                ordre=2,
                actif=True,
            ),
            dict(
                code="L",
                type_pesee=CategorieQualite.TYPE_OEUFS,
                libelle="Grand (L)",
                poids_min=Decimal("63.00"),
                poids_max=Decimal("73.00"),
                ordre=3,
                actif=True,
            ),
            dict(
                code="XL",
                type_pesee=CategorieQualite.TYPE_OEUFS,
                libelle="Très grand (XL)",
                poids_min=Decimal("73.00"),
                poids_max=Decimal("90.00"),
                ordre=4,
                actif=True,
            ),
        ]
        for s in seeds:
            CategorieQualite.objects.get_or_create(
                code=s["code"],
                type_pesee=s["type_pesee"],
                defaults=s,
            )
        self._log("CategorieQualite (8 — 4 oiseaux + 4 oeufs)", True)

    def _seed_produits_finis(self):
        from production.models import ProduitFini

        specs = [
            dict(
                designation="دجاج حي (الوزن الكامل)",
                type_produit="volaille_vivante",
                unite="unite",
                prix=480,
            ),
            dict(
                designation="جثة كاملة منزوعة الأحشاء",
                type_produit="carcasse",
                unite="kg",
                prix=750,
            ),
            dict(designation="صدر دجاج", type_produit="decoupe", unite="kg", prix=1100),
            dict(designation="فخذ كامل", type_produit="decoupe", unite="kg", prix=780),
            dict(designation="جناح دجاج", type_produit="decoupe", unite="kg", prix=620),
            dict(designation="كبد دجاج", type_produit="abats", unite="kg", prix=420),
            dict(designation="قانصة دجاج", type_produit="abats", unite="kg", prix=350),
        ]
        for s in specs:
            ProduitFini.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    type_produit=s["type_produit"],
                    unite_mesure=s["unite"],
                    prix_vente_defaut=Decimal(str(s["prix"])),
                    actif=True,
                ),
            )
        self._log(f"ProduitsFinis ({len(specs)})", True)

    # ------------------------------------------------------------------

    def _log(self, label: str, created: bool):
        symbol = self.style.SUCCESS("  ✓") if created else self.style.WARNING("  ~")
        self.stdout.write(f"{symbol} {label}")
