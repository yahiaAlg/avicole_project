"""
management/commands/seed_db.py

أمر تعبئة قاعدة البيانات الشامل لنظام تربية الدواجن.

الاستخدام:
    python manage.py seed_db                    # تعبئة كاملة (آمن للتكرار)
    python manage.py seed_db --mode minimal     # البيانات الأساسية فقط (بدون سجلات تشغيلية)
    python manage.py seed_db --mode demo        # بيانات تجريبية كاملة (الافتراضي)
    python manage.py seed_db --clear            # مسح البيانات التشغيلية ثم إعادة التعبئة
    python manage.py seed_db --clear --all      # مسح كل شيء (بما في ذلك البيانات الأساسية) ثم إعادة التعبئة

آمن للتكرار: يستخدم get_or_create في كل مكان.
جميع المبالغ المالية بالدينار الجزائري (DZD). التواريخ نسبية إلى اليوم.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def today() -> date:
    return date.today()


def d(days_ago: int) -> date:
    return today() - timedelta(days=days_ago)


def rnd(lo: float, hi: float, decimals: int = 2) -> Decimal:
    return Decimal(str(round(random.uniform(lo, hi), decimals)))


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "تعبئة قاعدة البيانات بالبيانات الأساسية وسجلات تجريبية اختيارية.\n"
        "minimal: شركة + مستخدمون + تصنيفات + منتجات نهائية فقط "
        "(بدون موردين / عملاء / مباني / مدخلات).\n"
        "demo   : كل بيانات minimal + بيانات تجريبية تشغيلية كاملة."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["minimal", "demo"],
            default="demo",
            help=(
                "'minimal' = شركة + مستخدمون + تصنيفات + منتجات نهائية فقط "
                "(بدون موردين/عملاء/مباني/مدخلات)؛ "
                "'demo' = كل شيء + بيانات تشغيلية كاملة."
            ),
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="حذف البيانات التشغيلية الحالية قبل التعبئة.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="clear_all",
            help="مع --clear: يحذف أيضاً البيانات الأساسية (التصنيفات، المستخدمون، إلخ).",
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        mode = options["mode"]
        clear = options["clear"]
        clear_all = options["clear_all"]

        if clear:
            self._clear(all_data=clear_all)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "\n=== تربية الدواجن — تعبئة قاعدة البيانات ===\n"
            )
        )

        # ── Categories & company (always seeded — safe to run on a blank DB) ──
        self._seed_company()
        self._seed_parametrage_elevage()
        self._seed_users()
        categories_intrant = self._seed_categories_intrant()
        types_fournisseur = self._seed_types_fournisseur()
        categories_depense = self._seed_categories_depense()
        self._seed_categories_qualite()
        produits_finis = self._seed_produits_finis()

        if mode == "minimal":
            self.stdout.write(
                self.style.SUCCESS(
                    "\n✓ Minimal seed complete "
                    "(company · parametrage · users · categories · qualite · produits_finis only).\n"
                    "  Next: create fournisseurs, clients, bâtiments and intrants\n"
                    "  via the admin interface, then run the full ERP scenario.\n"
                )
            )
            return

        # ── Physical master data (demo mode only) ────────────────────────
        fournisseurs = self._seed_fournisseurs(types_fournisseur)
        clients = self._seed_clients()
        batiments = self._seed_batiments()
        intrants = self._seed_intrants(categories_intrant, fournisseurs)

        # ── Operational / demo data ──────────────────────────────────────
        self._seed_stock_initial(intrants)
        bl_fournisseurs = self._seed_bl_fournisseurs(fournisseurs, intrants)
        factures_fournisseur = self._seed_factures_fournisseur(
            fournisseurs, bl_fournisseurs
        )
        self._seed_reglements_fournisseur(fournisseurs, factures_fournisseur)

        lots = self._seed_lots(fournisseurs, batiments)
        self._seed_mortalites(lots)
        self._seed_consommations(lots, intrants)
        productions = self._seed_productions(lots, produits_finis)

        bl_clients = self._seed_bl_clients(clients, produits_finis)
        factures_client = self._seed_factures_client(clients, bl_clients)
        self._seed_paiements_client(clients, factures_client)
        self._seed_prix_marche(produits_finis)

        self._seed_depenses(categories_depense, lots, factures_fournisseur)
        self._seed_associes()
        self._seed_employes_rh(batiments)
        self._seed_fertilisants(batiments)

        self.stdout.write(self.style.SUCCESS("\n✓ Full demo seed complete.\n"))

    # ------------------------------------------------------------------
    # Clear helpers
    # ------------------------------------------------------------------

    def _clear(self, all_data: bool):
        self.stdout.write(self.style.WARNING("  Clearing operational data..."))
        # Import here to avoid circular import before Django is ready

        # ── Clients app ────────────────────────────────────────────────
        from clients.models import (
            LivraisonPartielle,
            VoyageLivraison,
            AbonnementClient,
            PaiementClientAllocation,
            PaiementClient,
            FactureClient,
            BLClientLigne,
            BLClient,
            PrixMarche,
        )

        # ── Achats app ─────────────────────────────────────────────────
        from achats.models import (
            AllocationReglement,
            ReglementFournisseur,
            AcompteFournisseur,
            FactureFournisseur,
            BLFournisseurLigne,
            BLFournisseur,
        )

        # ── Elevage app ────────────────────────────────────────────────
        from elevage.models import (
            RecolteOeufs,
            PeseeEchantillon,
            TransfertLot,
            Consommation,
            Mortalite,
            LotElevage,
        )

        # ── Production app ─────────────────────────────────────────────
        from production.models import (
            CollecteFertilisant,
            TraitementFertilisant,
            ProductionLigne,
            ProductionRecord,
        )

        # ── Stock app ──────────────────────────────────────────────────
        from stock.models import (
            StockMouvement,
            StockAjustement,
            StockProduitFini,
            StockIntrant,
        )

        # ── Depenses app ───────────────────────────────────────────────
        from depenses.models import (
            BulletinPaie,
            AcompteEmploye,
            CongeEmploye,
            Pointage,
            Employe,
            RetraitAssocie,
            Associe,
            Depense,
        )

        # Ordered to respect FK constraints (children before parents)
        BulletinPaie.objects.all().delete()
        AcompteEmploye.objects.all().delete()
        CongeEmploye.objects.all().delete()
        Pointage.objects.all().delete()
        Employe.objects.all().delete()
        RetraitAssocie.objects.all().delete()
        Associe.objects.all().delete()
        Depense.objects.all().delete()

        LivraisonPartielle.objects.all().delete()
        VoyageLivraison.objects.all().delete()
        AbonnementClient.objects.all().delete()
        PaiementClientAllocation.objects.all().delete()
        PaiementClient.objects.all().delete()
        FactureClient.objects.all().delete()
        BLClientLigne.objects.all().delete()
        BLClient.objects.all().delete()
        PrixMarche.objects.all().delete()

        AllocationReglement.objects.all().delete()
        ReglementFournisseur.objects.all().delete()
        AcompteFournisseur.objects.all().delete()
        FactureFournisseur.objects.all().delete()
        BLFournisseurLigne.objects.all().delete()
        BLFournisseur.objects.all().delete()

        RecolteOeufs.objects.all().delete()
        PeseeEchantillon.objects.all().delete()
        TransfertLot.objects.all().delete()
        Consommation.objects.all().delete()
        Mortalite.objects.all().delete()
        LotElevage.objects.all().delete()

        CollecteFertilisant.objects.all().delete()
        TraitementFertilisant.objects.all().delete()
        ProductionLigne.objects.all().delete()
        ProductionRecord.objects.all().delete()

        StockMouvement.objects.all().delete()
        StockAjustement.objects.all().delete()
        StockProduitFini.objects.all().delete()
        StockIntrant.objects.all().delete()

        if all_data:
            self.stdout.write(self.style.WARNING("  Clearing master data..."))
            from intrants.models import (
                CategorieQualite,
                Intrant,
                Batiment,
                Fournisseur,
                TypeFournisseur,
                CategorieIntrant,
            )
            from production.models import ProduitFini
            from depenses.models import CategorieDepense
            from clients.models import Client
            from core.models import CompanyInfo, UserProfile
            from elevage.models import ParametrageElevage

            Intrant.objects.all().delete()
            Batiment.objects.all().delete()
            Fournisseur.objects.all().delete()
            CategorieQualite.objects.all().delete()
            TypeFournisseur.objects.all().delete()
            CategorieIntrant.objects.all().delete()
            ProduitFini.objects.all().delete()
            CategorieDepense.objects.all().delete()
            Client.objects.all().delete()
            UserProfile.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            CompanyInfo.objects.all().delete()
            ParametrageElevage.objects.all().delete()

        self.stdout.write("  Done.\n")

    # ------------------------------------------------------------------
    # ── MASTER DATA ────────────────────────────────────────────────────
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
        return obj

    def _seed_parametrage_elevage(self):
        """
        Singleton farm-wide age thresholds (BR-LOT-05).
        - age_transfert_poussiniere_jours: age at which chicks must move from
          Poussinière → Poulailler.
        - age_maturite_vente_jours: minimum age before slaughter/harvest is
          allowed on a ProductionRecord.
        """
        from elevage.models import ParametrageElevage

        obj, created = ParametrageElevage.objects.get_or_create(
            pk=1,
            defaults=dict(
                age_transfert_poussiniere_jours=21,
                age_maturite_vente_jours=35,
            ),
        )
        self._log("ParametrageElevage", created)
        return obj

    def _seed_users(self):
        from core.models import UserProfile

        users_spec = [
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
        for spec in users_spec:
            role = spec.pop("role")
            password = spec.pop("password")
            user, created = User.objects.get_or_create(
                username=spec["username"],
                defaults=spec,
            )
            if created:
                user.set_password(password)
                user.save()
                created_count += 1
            UserProfile.objects.get_or_create(user=user, defaults={"role": role})
        self._log(f"Users ({len(users_spec)})", created_count > 0)

    # ------------------------------------------------------------------

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
        result = {}
        for s in seeds:
            obj, created = CategorieIntrant.objects.get_or_create(
                code=s["code"], defaults=s
            )
            result[s["code"]] = obj
        self._log("CategorieIntrant (4)", True)
        return result

    def _seed_types_fournisseur(self):
        from intrants.models import TypeFournisseur

        seeds = [
            dict(code="ALIMENTS", libelle="أعلاف", ordre=1, actif=True),
            dict(code="POUSSINS", libelle="كتاكيت", ordre=2, actif=True),
            dict(code="MEDICAMENTS", libelle="أدوية / بيطريين", ordre=3, actif=True),
            dict(code="SERVICES", libelle="خدمات", ordre=4, actif=True),
            dict(code="AUTRE", libelle="أخرى", ordre=5, actif=True),
        ]
        result = {}
        for s in seeds:
            obj, _ = TypeFournisseur.objects.get_or_create(code=s["code"], defaults=s)
            result[s["code"]] = obj
        self._log("TypeFournisseur (5)", True)
        return result

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
        result = {}
        for s in seeds:
            obj, _ = CategorieDepense.objects.get_or_create(code=s["code"], defaults=s)
            result[s["code"]] = obj
        self._log("CategorieDepense (8)", True)
        return result

    def _seed_categories_qualite(self):
        """
        Seed default quality-grading brackets for birds and eggs.

        Birds (oiseaux) — graded by average live weight in grams:
          S / Standard / L / XL

        Eggs (oeufs) — graded by average egg weight in grams:
          S / M / L / XL  (aligned with EU egg-grading norms)

        The unique constraint is (code, type_pesee) so the same letter code
        can exist independently for each scale.
        """
        from intrants.models import CategorieQualite

        seeds = [
            # ── Oiseaux ────────────────────────────────────────────────
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
            # ── Oeufs ──────────────────────────────────────────────────
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

    # ------------------------------------------------------------------

    def _seed_fournisseurs(self, types):
        from intrants.models import Fournisseur

        specs = [
            dict(
                nom="ONAB Setifien",
                adresse="Route de Boghni, Setifien",
                wilaya="Setifien",
                telephone="026 12 34 56",
                type_code="ALIMENTS",
                nif="099000000001",
                rc="16/00-0000001 B 01",
            ),
            dict(
                nom="Couvoirs du Centre — CCA",
                adresse="Zone Agro-industrielle, Blida",
                wilaya="Blida",
                telephone="025 55 66 77",
                type_code="POUSSINS",
                nif="009000000002",
                rc="09/00-0000002 B 02",
            ),
            dict(
                nom="Sanofi Algérie (Vétérinaire)",
                adresse="Rue Hassiba Ben Bouali, Alger",
                wilaya="Alger",
                telephone="021 99 00 11",
                type_code="MEDICAMENTS",
                nif="016000000003",
                rc="16/00-0000003 B 03",
            ),
            dict(
                nom="Proxi-Aliments Boumerdès",
                adresse="Cité Industrielle, Boumerdès",
                wilaya="Boumerdès",
                telephone="024 88 77 66",
                type_code="ALIMENTS",
                nif="035000000004",
                rc="35/00-0000004 B 04",
            ),
            dict(
                nom="Techno-Avicole Services",
                adresse="Rue du 1er Novembre, Alger",
                wilaya="Alger",
                telephone="021 44 55 66",
                type_code="SERVICES",
                nif="016000000005",
                rc="16/00-0000005 B 05",
            ),
        ]
        result = {}
        for s in specs:
            type_code = s.pop("type_code")
            obj, created = Fournisseur.objects.get_or_create(
                nom=s["nom"],
                defaults={**s, "type_principal": types[type_code]},
            )
            result[obj.nom] = obj
        self._log(f"Fournisseurs ({len(specs)})", True)
        return result

    def _seed_clients(self):
        from clients.models import Client

        specs = [
            dict(
                nom="Marché de Gros Setifien",
                type_client="grossiste",
                wilaya="Setifien",
                telephone="0555 11 22 33",
                plafond_credit=Decimal("500000"),
            ),
            dict(
                nom="Restaurant Le Palmier",
                type_client="restauration",
                wilaya="Setifien",
                telephone="0770 22 33 44",
                plafond_credit=Decimal("150000"),
            ),
            dict(
                nom="Boucherie Amrane & Fils",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0660 33 44 55",
                plafond_credit=Decimal("200000"),
            ),
            dict(
                nom="Épicerie Centrale Azazga",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0555 44 55 66",
                plafond_credit=Decimal("80000"),
            ),
            dict(
                nom="Grossiste Alger Sud",
                type_client="grossiste",
                wilaya="Alger",
                telephone="021 88 77 66",
                plafond_credit=Decimal("1000000"),
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = Client.objects.get_or_create(nom=s["nom"], defaults=s)
            result[obj.nom] = obj
        self._log(f"Clients ({len(specs)})", True)
        return result

    def _seed_batiments(self):
        """
        Seed one of each building type:
          - Bâtiment A / B  : Poulailler   (grow-out / production buildings)
          - Poussinière 1   : Poussinière  (brooding — chicks start here, transfer to
                              Poulailler once they exceed ParametrageElevage.age_transfert)
          - Entrepôt Fertilisant : Entrepôt (fertiliser storage — requires categorie_stockage)

        Using the typed model prevents downstream logic errors in LotElevage.phase,
        doit_etre_transfere, and stade_intrant_attendu.
        """
        from intrants.models import Batiment

        specs = [
            dict(
                nom="Bâtiment A",
                type_batiment=Batiment.TYPE_POULAILLER,
                capacite=5000,
                description="الحظيرة الرئيسية — تهوية ميكانيكية",
            ),
            dict(
                nom="Bâtiment B",
                type_batiment=Batiment.TYPE_POULAILLER,
                capacite=4000,
                description="الحظيرة الثانوية — تهوية طبيعية",
            ),
            dict(
                nom="Poussinière 1",
                type_batiment=Batiment.TYPE_POUSSINIERE,
                capacite=10000,
                description="حضانة الكتاكيت — الدفعات تبدأ هنا وتُنقل بعد بلوغ سن النقل",
            ),
            dict(
                nom="Entrepôt Fertilisant",
                type_batiment=Batiment.TYPE_ENTREPOT,
                categorie_stockage=Batiment.STOCKAGE_FERTILISANT,
                capacite=None,
                description="مستودع تخزين السماد المعالج قبل الشحن",
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = Batiment.objects.get_or_create(nom=s["nom"], defaults=s)
            result[obj.nom] = obj
        self._log(f"Bâtiments ({len(specs)})", True)
        return result

    def _seed_intrants(self, cats, fournisseurs):
        """
        Seed the input-goods catalogue with the correct `stade` for each item:
          - Feed démarrage  → STADE_DEMARRAGE  (Poussinière phase, age 0–14 days)
          - Feed croissance → STADE_CROISSANCE  (Poulailler grow-out, age 15–28 days)
          - Feed finition   → STADE_CROISSANCE  (Poulailler finishing, age 29+ days)
          - Poussins        → STADE_DEMARRAGE   (received at day-old, enter brooding)
          - Medicines / other → STADE_TOUS      (used across all phases)

        This aligns with LotElevage.stade_intrant_attendu which narrows
        the Consommation form's intrant queryset by building type.
        """
        from intrants.models import Intrant

        specs = [
            # ── Aliments ──────────────────────────────────────────────
            dict(
                code="ALIM-DEM",
                cat="ALIMENT",
                stade=Intrant.STADE_DEMARRAGE,
                designation="علف البداية — الطور الأول (0–14 يوم)",
                unite="sac",
                seuil=10,
                fnoms=["ONAB Setifien", "Proxi-Aliments Boumerdès"],
            ),
            dict(
                code="ALIM-CRO",
                cat="ALIMENT",
                stade=Intrant.STADE_CROISSANCE,
                designation="علف النمو — الطور الثاني (15–28 يوم)",
                unite="sac",
                seuil=15,
                fnoms=["ONAB Setifien", "Proxi-Aliments Boumerdès"],
            ),
            dict(
                code="ALIM-FIN",
                cat="ALIMENT",
                stade=Intrant.STADE_CROISSANCE,
                designation="علف التسمين — الطور الثالث (29 يوم فأكثر)",
                unite="sac",
                seuil=20,
                fnoms=["ONAB Setifien"],
            ),
            # ── Poussins ──────────────────────────────────────────────
            dict(
                code="POUSS-R308",
                cat="POUSSIN",
                stade=Intrant.STADE_DEMARRAGE,
                designation="كتكوت روس 308 (يوم واحد)",
                unite="unite",
                seuil=100,
                fnoms=["Couvoirs du Centre — CCA"],
            ),
            dict(
                code="POUSS-C500",
                cat="POUSSIN",
                stade=Intrant.STADE_DEMARRAGE,
                designation="كتكوت كوب 500 (يوم واحد)",
                unite="unite",
                seuil=100,
                fnoms=["Couvoirs du Centre — CCA"],
            ),
            # ── Médicaments ───────────────────────────────────────────
            dict(
                code="MED-NEWC",
                cat="MEDICAMENT",
                stade=Intrant.STADE_TOUS,
                designation="لقاح نيوكاسل (هيتشنر B1)",
                unite="dose",
                seuil=500,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-GCOR",
                cat="MEDICAMENT",
                stade=Intrant.STADE_TOUS,
                designation="لقاح غامبورو (IBD متوسط)",
                unite="dose",
                seuil=500,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-AMOX",
                cat="MEDICAMENT",
                stade=Intrant.STADE_TOUS,
                designation="أموكسيسيلين 50% مسحوق",
                unite="g",
                seuil=200,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-VITA",
                cat="MEDICAMENT",
                stade=Intrant.STADE_TOUS,
                designation="فيتامينات + إلكتروليتات (مركّب)",
                unite="litre",
                seuil=5,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            # ── Autres ────────────────────────────────────────────────
            dict(
                code="AUT-LITIERE",
                cat="AUTRE",
                stade=Intrant.STADE_TOUS,
                designation="فراش (نشارة خشب)",
                unite="sac",
                seuil=20,
                fnoms=[],
            ),
        ]
        result = {}
        for s in specs:
            cat = cats[s["cat"]]
            obj, _ = Intrant.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    categorie=cat,
                    stade=s["stade"],
                    unite_mesure=s["unite"],
                    seuil_alerte=Decimal(str(s["seuil"])),
                    actif=True,
                ),
            )
            for fname in s["fnoms"]:
                if fname in fournisseurs:
                    obj.fournisseurs.add(fournisseurs[fname])
            result[s["code"]] = obj
        self._log(f"Intrants ({len(specs)})", True)
        return result

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
            dict(
                designation="صدر دجاج",
                type_produit="decoupe",
                unite="kg",
                prix=1100,
            ),
            dict(
                designation="فخذ كامل",
                type_produit="decoupe",
                unite="kg",
                prix=780,
            ),
            dict(
                designation="جناح دجاج",
                type_produit="decoupe",
                unite="kg",
                prix=620,
            ),
            dict(designation="كبد دجاج", type_produit="abats", unite="kg", prix=420),
            dict(
                designation="قانصة دجاج",
                type_produit="abats",
                unite="kg",
                prix=350,
            ),
            # ── Oeufs ─────────────────────────────────────────────────────────
            dict(
                designation="صينية بيض (30 بيضة)",
                type_produit="oeufs",
                unite="plateau",
                prix=350,
            ),
            # ── Fertilisants ──────────────────────────────────────────────────
            dict(
                designation="سماد دواجن معالج (مجفف)",
                type_produit="fertilisant",
                unite="kg",
                prix=28,
            ),
            dict(
                designation="سماد دواجن خام (غير معالج)",
                type_produit="fertilisant",
                unite="kg",
                prix=12,
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = ProduitFini.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    type_produit=s["type_produit"],
                    unite_mesure=s["unite"],
                    prix_vente_defaut=Decimal(str(s["prix"])),
                    actif=True,
                ),
            )
            result[s["designation"]] = obj
        self._log(f"ProduitsFinis ({len(specs)} — incl. 2 fertilisants)", True)
        return result

    # ------------------------------------------------------------------
    # ── OPERATIONAL / DEMO DATA ────────────────────────────────────────
    # ------------------------------------------------------------------

    def _seed_fertilisants(self, batiments):
        """
        Seed CollecteFertilisant (raw manure pickups) and one validated
        TraitementFertilisant batch that produces finished fertilizer stock.
        Pattern mirrors _seed_productions: create BROUILLON, attach collectes,
        then transition to VALIDE so the stock signal fires correctly.
        """
        from production.models import (
            CollecteFertilisant,
            TraitementFertilisant,
            ProduitFini,
        )

        admin = User.objects.filter(is_superuser=True).first()
        bat_a = batiments.get("Bâtiment A")
        bat_b = batiments.get("Bâtiment B")
        prod_fert = ProduitFini.objects.filter(
            type_produit=ProduitFini.TYPE_FERTILISANT,
            designation="سماد دواجن معالج (مجفف)",
        ).first()
        if not prod_fert:
            self._log("Fertilisants — skipped (no fertilisant product found)", False)
            return

        # ── Traitement 1 — validated batch (stock credited via signal) ──
        trt, trt_created = TraitementFertilisant.objects.get_or_create(
            date_traitement=d(18),
            defaults=dict(
                methode="تجفيف طبيعي في الهواء الطلق",
                produit_fini=prod_fert,
                quantite_obtenue_kg=Decimal("820.000"),
                cout_unitaire_estime=Decimal("9.5000"),
                statut=TraitementFertilisant.STATUT_BROUILLON,
                created_by=admin,
            ),
        )
        collectes_created = 0
        for bat, date_c, qty in [
            (bat_a, d(25), Decimal("620.000")),
            (bat_a, d(22), Decimal("590.000")),
            (bat_b, d(21), Decimal("480.000")),
        ]:
            if bat:
                _, c = CollecteFertilisant.objects.get_or_create(
                    batiment=bat,
                    date_collecte=date_c,
                    defaults=dict(
                        quantite_brute_kg=qty,
                        traitement=trt,
                        created_by=admin,
                    ),
                )
                if c:
                    collectes_created += 1

        if trt_created:
            trt.statut = TraitementFertilisant.STATUT_VALIDE
            trt.save()

        # ── Traitement 2 — brouillon (not yet validated) ────────────────
        trt2, trt2_created = TraitementFertilisant.objects.get_or_create(
            date_traitement=d(3),
            defaults=dict(
                methode="تخمير (compostage)",
                produit_fini=prod_fert,
                quantite_obtenue_kg=Decimal("0.000"),
                cout_unitaire_estime=Decimal("0.0000"),
                statut=TraitementFertilisant.STATUT_BROUILLON,
                created_by=admin,
            ),
        )
        for bat, date_c, qty in [
            (bat_a, d(8), Decimal("540.000")),
            (bat_b, d(6), Decimal("460.000")),
        ]:
            if bat:
                _, c = CollecteFertilisant.objects.get_or_create(
                    batiment=bat,
                    date_collecte=date_c,
                    defaults=dict(
                        quantite_brute_kg=qty,
                        traitement=trt2,
                        created_by=admin,
                    ),
                )
                if c:
                    collectes_created += 1

        self._log(
            f"CollecteFertilisant ({collectes_created}) + "
            f"TraitementFertilisant (1 validé + 1 brouillon)",
            trt_created,
        )

    # ------------------------------------------------------------------

    def _seed_associes(self):
        """
        Seed Associe (stakeholder) master records and sample RetraitAssocie
        (equity withdrawals — BR-ASSOC-01/02, never inserted into Depense).
        """
        from depenses.models import Associe, RetraitAssocie

        admin = User.objects.filter(is_superuser=True).first()

        assoc_specs = [
            dict(
                nom="كريم مزياني",
                telephone="0555 123 456",
                pourcentage_parts=Decimal("60.00"),
            ),
            dict(
                nom="ليندة عودية",
                telephone="0770 987 654",
                pourcentage_parts=Decimal("40.00"),
            ),
        ]
        associes = {}
        for s in assoc_specs:
            obj, _ = Associe.objects.get_or_create(nom=s["nom"], defaults=s)
            associes[s["nom"]] = obj

        retrait_specs = [
            dict(
                assoc="كريم مزياني",
                date=d(60),
                montant=Decimal("150000"),
                mode="especes",
                motif="سحب شخصي — يناير 2025",
            ),
            dict(
                assoc="ليندة عودية",
                date=d(60),
                montant=Decimal("100000"),
                mode="virement",
                motif="سحب شخصي — يناير 2025",
            ),
            dict(
                assoc="كريم مزياني",
                date=d(30),
                montant=Decimal("120000"),
                mode="especes",
                motif="تسبيق على الأرباح — فبراير 2025",
            ),
            dict(
                assoc="ليندة عودية",
                date=d(30),
                montant=Decimal("80000"),
                mode="cheque",
                motif="تسبيق على الأرباح — فبراير 2025",
            ),
        ]
        ret_count = 0
        for s in retrait_specs:
            assoc = associes[s["assoc"]]
            _, created = RetraitAssocie.objects.get_or_create(
                associe=assoc,
                date=s["date"],
                montant=s["montant"],
                defaults=dict(
                    mode_paiement=s["mode"],
                    motif=s["motif"],
                    enregistre_par=admin,
                ),
            )
            if created:
                ret_count += 1

        self._log(
            f"Associés (2) + RetraitAssocie ({ret_count})",
            True,
        )

    # ------------------------------------------------------------------

    def _seed_employes_rh(self, batiments):
        """
        Seed Employe master records, 30-day Pointage history, one AcompteEmploye
        advance per employee, and a validated BulletinPaie for last month.

        Rotation rule (BR-RH-01):
          emp1 repos = vendredi (4), emp2 repos = samedi (5); they are binômes.
        Daily rate BR-RH-02: salaire_base / 25.
        """
        from depenses.models import Employe, Pointage, AcompteEmploye, BulletinPaie

        import calendar

        admin = User.objects.filter(is_superuser=True).first()
        bat_a = batiments.get("Bâtiment A")

        # ── Employees ──────────────────────────────────────────────────
        emp1, _ = Employe.objects.get_or_create(
            matricule="EMP-001",
            defaults=dict(
                nom_complet="يوسف بوزيدي",
                fonction="عامل تربية",
                telephone="0661 11 22 33",
                date_embauche=d(365),
                batiment=bat_a,
                jour_repos_habituel=Employe.JOUR_VENDREDI,
                salaire_base_mensuel=Decimal("32000.00"),
                heures_normales_jour=Decimal("8.00"),
                taux_majoration_heure_sup=Decimal("1.50"),
                actif=True,
            ),
        )
        emp2, _ = Employe.objects.get_or_create(
            matricule="EMP-002",
            defaults=dict(
                nom_complet="محمد صهراوي",
                fonction="عامل تربية",
                telephone="0772 44 55 66",
                date_embauche=d(300),
                batiment=bat_a,
                jour_repos_habituel=Employe.JOUR_SAMEDI,
                binome=emp1,
                salaire_base_mensuel=Decimal("32000.00"),
                heures_normales_jour=Decimal("8.00"),
                taux_majoration_heure_sup=Decimal("1.50"),
                actif=True,
            ),
        )
        # Set emp1.binome = emp2 now that emp2 exists
        if not emp1.binome_id:
            emp1.binome = emp2
            emp1.save(update_fields=["binome"])

        # ── Pointage — last 30 days for both employees ──────────────────
        pointage_count = 0
        for emp in [emp1, emp2]:
            for offset in range(30, 0, -1):
                day = d(offset)
                wday = day.weekday()
                if wday == emp.jour_repos_habituel:
                    statut = Pointage.STATUT_REPOS
                    heures_sup = Decimal("0.00")
                elif offset in (15, 22):  # two absence days per employee
                    statut = Pointage.STATUT_ABSENT
                    heures_sup = Decimal("0.00")
                else:
                    statut = Pointage.STATUT_PRESENT
                    heures_sup = (
                        Decimal("1.50") if wday == 3 else Decimal("0.00")
                    )  # Thu overtime
                _, created = Pointage.objects.get_or_create(
                    employe=emp,
                    date=day,
                    defaults=dict(statut=statut, heures_supplementaires=heures_sup),
                )
                if created:
                    pointage_count += 1

        # ── AcompteEmploye — one advance per employee ───────────────────
        acompte_count = 0
        for emp, montant, motif in [
            (emp1, Decimal("5000.00"), "تسبيق — منتصف شهر مارس 2025"),
            (emp2, Decimal("4000.00"), "تسبيق — منتصف شهر مارس 2025"),
        ]:
            _, created = AcompteEmploye.objects.get_or_create(
                employe=emp,
                date=d(15),
                defaults=dict(
                    montant=montant,
                    mode_paiement="especes",
                    motif=motif,
                    enregistre_par=admin,
                ),
            )
            if created:
                acompte_count += 1

        # ── BulletinPaie — validated payslip for previous full month ────
        # Determine last full month
        today_date = date.today()
        if today_date.month == 1:
            bp_annee, bp_mois = today_date.year - 1, 12
        else:
            bp_annee, bp_mois = today_date.year, today_date.month - 1

        bulletin_count = 0
        taux_j = (Decimal("32000.00") / Decimal("25")).quantize(Decimal("0.01"))
        days_in_month = calendar.monthrange(bp_annee, bp_mois)[1]

        for emp in [emp1, emp2]:
            # Count repos days in that month for this employee
            repos = sum(
                1
                for day_n in range(1, days_in_month + 1)
                if date(bp_annee, bp_mois, day_n).weekday() == emp.jour_repos_habituel
            )
            jours_pres = days_in_month - repos - 2  # 2 absences
            jours_sup_hrs = Decimal("4.50")  # 3 Thursdays × 1.5 h
            montant_sup = (taux_j / Decimal("8")) * Decimal("1.5") * jours_sup_hrs
            montant_brut = taux_j * jours_pres + montant_sup
            acompte_deduit = Decimal("5000.00") if emp == emp1 else Decimal("4000.00")
            montant_net = max(montant_brut - acompte_deduit, Decimal("0.00"))

            bp, bp_created = BulletinPaie.objects.get_or_create(
                employe=emp,
                annee=bp_annee,
                mois=bp_mois,
                defaults=dict(
                    jours_presence=jours_pres,
                    jours_absence=2,
                    jours_repos=repos,
                    jours_conge=0,
                    total_heures_supplementaires=jours_sup_hrs,
                    salaire_base_reference=Decimal("32000.00"),
                    taux_journalier=taux_j,
                    montant_heures_sup=montant_sup.quantize(Decimal("0.01")),
                    montant_brut=montant_brut.quantize(Decimal("0.01")),
                    total_acomptes=acompte_deduit,
                    montant_net=montant_net.quantize(Decimal("0.01")),
                    statut=BulletinPaie.STATUT_PAYE,
                    date_paiement=d(5),
                    mode_paiement="especes",
                    genere_par=admin,
                ),
            )
            if bp_created:
                bulletin_count += 1
                # Link the acompte to this bulletin
                AcompteEmploye.objects.filter(
                    employe=emp, date=d(15), bulletin_paie__isnull=True
                ).update(bulletin_paie=bp)

        self._log(
            f"Employés (2) + Pointage ({pointage_count}) + "
            f"AcompteEmploye ({acompte_count}) + BulletinPaie ({bulletin_count})",
            True,
        )

    # ------------------------------------------------------------------

    def _seed_stock_initial(self, intrants):
        """
        Bootstrap StockIntrant rows (one-to-one with each Intrant).
        The balance here represents opening stock BEFORE any BL.
        In a real migration you'd use StockAjustement records instead;
        for seeding we write directly to stay simple.
        """
        from stock.models import StockIntrant

        opening = {
            "ALIM-DEM": (Decimal("80"), Decimal("1850.00")),
            "ALIM-CRO": (Decimal("120"), Decimal("1750.00")),
            "ALIM-FIN": (Decimal("60"), Decimal("1700.00")),
            "POUSS-R308": (Decimal("0"), Decimal("0")),
            "POUSS-C500": (Decimal("0"), Decimal("0")),
            "MED-NEWC": (Decimal("2000"), Decimal("18.50")),
            "MED-GCOR": (Decimal("1500"), Decimal("22.00")),
            "MED-AMOX": (Decimal("800"), Decimal("45.00")),
            "MED-VITA": (Decimal("20"), Decimal("1200.00")),
            "AUT-LITIERE": (Decimal("30"), Decimal("600.00")),
        }
        for code, intrant in intrants.items():
            qty, pmp = opening.get(code, (Decimal("0"), Decimal("0")))
            # update_or_create (not get_or_create) so we overwrite the
            # zero-balance StockIntrant that the Intrant post_save signal
            # auto-creates on every new catalogue entry.  Using get_or_create
            # would silently leave every opening balance at 0.
            StockIntrant.objects.update_or_create(
                intrant=intrant,
                defaults={"quantite": qty, "prix_moyen_pondere": pmp},
            )
        self._log(f"StockIntrant (opening balances for {len(opening)} intrants)", True)

    # ------------------------------------------------------------------

    def _seed_bl_fournisseurs(self, fournisseurs, intrants):
        """
        Create 7 BL Fournisseurs covering aliments, poussins, and médicaments.
        2 are in 'reçu' state (ready to invoice), 2 are already 'facturé',
        1 is still a 'brouillon', 1 is 'en litige', 1 is 'autorisation_acces'.
        """
        from achats.models import BLFournisseur, BLFournisseurLigne
        from stock.models import StockIntrant

        bl_specs = [
            dict(
                ref="BLF-2025-001",
                fnom="ONAB Setifien",
                date_ago=60,
                statut=BLFournisseur.STATUT_FACTURE,
                lines=[
                    ("ALIM-DEM", Decimal("100"), Decimal("1800.00")),
                    ("ALIM-CRO", Decimal("150"), Decimal("1720.00")),
                ],
            ),
            dict(
                ref="BLF-2025-002",
                fnom="Couvoirs du Centre — CCA",
                date_ago=55,
                statut=BLFournisseur.STATUT_FACTURE,
                lines=[
                    ("POUSS-R308", Decimal("5000"), Decimal("42.00")),
                ],
            ),
            dict(
                ref="BLF-2025-003",
                fnom="Sanofi Algérie (Vétérinaire)",
                date_ago=50,
                statut=BLFournisseur.STATUT_RECU,
                lines=[
                    (
                        "MED-NEWC",
                        Decimal("14000"),
                        Decimal("18.00"),
                    ),  # 3 lots × up to 6000 birds each − opening 2000
                    (
                        "MED-GCOR",
                        Decimal("8000"),
                        Decimal("21.50"),
                    ),  # Lots A+B × 5000+4000 birds − opening 1500
                    ("MED-AMOX", Decimal("2000"), Decimal("44.00")),
                ],
            ),
            dict(
                ref="BLF-2025-004",
                fnom="ONAB Setifien",
                date_ago=30,
                statut=BLFournisseur.STATUT_RECU,
                lines=[
                    ("ALIM-DEM", Decimal("80"), Decimal("1850.00")),
                    ("ALIM-CRO", Decimal("100"), Decimal("1760.00")),
                    (
                        "ALIM-FIN",
                        Decimal("200"),
                        Decimal("1690.00"),
                    ),  # Lots A(150)+B(96)=246 sacs needed; opening=60
                ],
            ),
            dict(
                ref="BLF-2025-005",
                fnom="Couvoirs du Centre — CCA",
                date_ago=20,
                statut=BLFournisseur.STATUT_RECU,
                lines=[
                    ("POUSS-C500", Decimal("4000"), Decimal("45.00")),
                ],
            ),
            dict(
                ref="BLF-2025-006",
                fnom="Proxi-Aliments Boumerdès",
                date_ago=10,
                statut=BLFournisseur.STATUT_BROUILLON,
                lines=[
                    ("ALIM-FIN", Decimal("200"), Decimal("1700.00")),
                ],
            ),
            dict(
                ref="BLF-2025-007",
                fnom="ONAB Setifien",
                date_ago=45,
                statut=BLFournisseur.STATUT_LITIGE,
                lines=[
                    ("ALIM-CRO", Decimal("50"), Decimal("1750.00")),
                ],
            ),
        ]

        admin = User.objects.filter(is_superuser=True).first()
        result = {}
        for spec in bl_specs:
            bl, created = BLFournisseur.objects.get_or_create(
                reference=spec["ref"],
                defaults=dict(
                    fournisseur=fournisseurs[spec["fnom"]],
                    date_bl=d(spec["date_ago"]),
                    statut=spec["statut"],
                    created_by=admin,
                ),
            )
            if created:
                for icode, qty, pu in spec["lines"]:
                    BLFournisseurLigne.objects.create(
                        bl=bl,
                        intrant=intrants[icode],
                        quantite=qty,
                        prix_unitaire=pu,
                    )
                    # Update StockIntrant for received BLs (simulate signal)
                    if spec["statut"] in (
                        BLFournisseur.STATUT_RECU,
                        BLFournisseur.STATUT_FACTURE,
                    ):
                        si, _ = StockIntrant.objects.get_or_create(
                            intrant=intrants[icode],
                            defaults={
                                "quantite": Decimal("0"),
                                "prix_moyen_pondere": pu,
                            },
                        )
                        old_qty = si.quantite
                        old_pmp = si.prix_moyen_pondere
                        new_qty = old_qty + qty
                        # Weighted average update
                        if new_qty > 0:
                            si.prix_moyen_pondere = (
                                (old_qty * old_pmp + qty * pu) / new_qty
                            ).quantize(Decimal("0.0001"))
                        si.quantite = new_qty
                        si.save()
            result[spec["ref"]] = bl

        self._log(f"BLFournisseurs ({len(bl_specs)})", True)
        return result

    def _seed_factures_fournisseur(self, fournisseurs, bls):
        """
        Create 2 Factures Fournisseurs from the 'facture'-status BLs.
        One fully paid, one partially paid.
        """
        from achats.models import FactureFournisseur

        admin = User.objects.filter(is_superuser=True).first()

        # Compute montant from BLs
        bl1 = bls["BLF-2025-001"]
        bl2 = bls["BLF-2025-002"]

        mt1 = sum(l.montant_total for l in bl1.lignes.all())  # aliments
        mt2 = sum(l.montant_total for l in bl2.lignes.all())  # poussins

        result = {}

        # Facture 1 — Aliments (ONAB) — fully paid
        f1, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-001",
            defaults=dict(
                fournisseur=fournisseurs["ONAB Setifien"],
                date_facture=d(58),
                date_echeance=d(28),
                type_facture="marchandises",
                montant_total=mt1,
                montant_regle=mt1,
                reste_a_payer=Decimal("0"),
                statut=FactureFournisseur.STATUT_PAYE,
                created_by=admin,
            ),
        )
        if created:
            f1.bls.set([bl1])
        result["FRN-2025-001"] = f1

        # Facture 2 — Poussins (CCA) — partially paid
        f2, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-002",
            defaults=dict(
                fournisseur=fournisseurs["Couvoirs du Centre — CCA"],
                date_facture=d(53),
                date_echeance=d(23),
                type_facture="marchandises",
                montant_total=mt2,
                montant_regle=(mt2 / 2).quantize(Decimal("0.01")),
                reste_a_payer=(mt2 / 2).quantize(Decimal("0.01")),
                statut=FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
                created_by=admin,
            ),
        )
        if created:
            f2.bls.set([bl2])
        result["FRN-2025-002"] = f2

        # Facture 3 — Service (Techno-Avicole) — unpaid (for dépense link demo)
        f3, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-003",
            defaults=dict(
                fournisseur=fournisseurs["Techno-Avicole Services"],
                date_facture=d(15),
                date_echeance=d(1),
                type_facture="service",
                montant_total=Decimal("45000.00"),
                montant_regle=Decimal("0"),
                reste_a_payer=Decimal("45000.00"),
                statut=FactureFournisseur.STATUT_NON_PAYE,
                created_by=admin,
            ),
        )
        result["FRN-2025-003"] = f3

        self._log("FacturesFournisseurs (3)", True)
        return result

    def _seed_reglements_fournisseur(self, fournisseurs, factures):
        """
        Seed règlements + allocations for the supplier settlement chain.
        Simulates the FIFO engine output manually.
        """
        from achats.models import ReglementFournisseur, AllocationReglement

        admin = User.objects.filter(is_superuser=True).first()

        f1 = factures["FRN-2025-001"]
        f2 = factures["FRN-2025-002"]

        # Règlement 1 — fully covers FRN-2025-001
        r1, created = ReglementFournisseur.objects.get_or_create(
            fournisseur=fournisseurs["ONAB Setifien"],
            date_reglement=d(40),
            defaults=dict(
                montant=f1.montant_total,
                mode_paiement="cheque",
                reference_paiement="CHQ-0012345",
                created_by=admin,
            ),
        )
        if created:
            AllocationReglement.objects.create(
                reglement=r1, facture=f1, montant_alloue=f1.montant_total
            )

        # Règlement 2 — partial on FRN-2025-002 (half the amount)
        r2, created = ReglementFournisseur.objects.get_or_create(
            fournisseur=fournisseurs["Couvoirs du Centre — CCA"],
            date_reglement=d(35),
            defaults=dict(
                montant=f2.montant_regle,
                mode_paiement="especes",
                created_by=admin,
            ),
        )
        if created:
            AllocationReglement.objects.create(
                reglement=r2, facture=f2, montant_alloue=f2.montant_regle
            )

        self._log("ReglementsFournisseurs (2) + Allocations", True)

    # ------------------------------------------------------------------

    def _seed_lots(self, fournisseurs, batiments):
        """
        Seed 3 lots:
          - Lot A (fermé, 75 days ago) — started in Poussinière, already transferred
          - Lot B (ouvert, 40 days ago) — in Bâtiment A (poulailler), active production
          - Lot C (ouvert, 10 days ago) — in Poussinière 1, not yet mature for transfer
        """
        from elevage.models import LotElevage

        admin = User.objects.filter(is_superuser=True).first()
        cca = fournisseurs["Couvoirs du Centre — CCA"]

        specs = [
            dict(
                designation="Lot Janvier 2025 — Bâtiment A",
                date_ouverture=d(75),
                date_fermeture=d(30),
                statut=LotElevage.STATUT_FERME,
                nombre_poussins_initial=5000,
                fournisseur_poussins=cca,
                batiment=batiments["Bâtiment A"],
                souche="Ross 308",
            ),
            dict(
                designation="Lot Mars 2025 — Bâtiment A",
                date_ouverture=d(40),
                date_fermeture=None,
                statut=LotElevage.STATUT_OUVERT,
                nombre_poussins_initial=4000,
                fournisseur_poussins=cca,
                batiment=batiments["Bâtiment A"],
                souche="Cobb 500",
            ),
            dict(
                designation="Lot Avril 2025 — Poussinière 1",
                date_ouverture=d(10),
                date_fermeture=None,
                statut=LotElevage.STATUT_OUVERT,
                nombre_poussins_initial=6000,
                fournisseur_poussins=cca,
                batiment=batiments["Poussinière 1"],
                souche="Ross 308",
            ),
        ]

        result = {}
        for s in specs:
            obj, _ = LotElevage.objects.get_or_create(
                designation=s["designation"],
                defaults={**s, "created_by": admin},
            )
            result[s["designation"]] = obj
        self._log(f"Lots d'élevage ({len(specs)})", True)
        return result

    def _seed_mortalites(self, lots):
        from elevage.models import Mortalite

        mortalite_data = {
            "Lot Janvier 2025 — Bâtiment A": [
                # (days_ago, count, cause)
                (73, 12, "Mort-né / faiblesse"),
                (70, 8, "Infection respiratoire"),
                (65, 5, ""),
                (60, 15, "Coccidiose suspectée"),
                (50, 3, ""),
                (40, 6, "Choc thermique"),
                (35, 4, ""),
            ],
            "Lot Mars 2025 — Bâtiment A": [
                (38, 10, "Mort-né / faiblesse"),
                (35, 6, ""),
                (28, 8, "Infection respiratoire"),
                (20, 4, ""),
                (12, 3, ""),
            ],
            "Lot Avril 2025 — Poussinière 1": [
                (8, 15, "Mort-né / faiblesse"),
                (5, 7, ""),
            ],
        }

        count = 0
        for designation, records in mortalite_data.items():
            lot = lots.get(designation)
            if not lot:
                continue
            for days_ago, nombre, cause in records:
                Mortalite.objects.get_or_create(
                    lot=lot,
                    date=d(days_ago),
                    nombre=nombre,
                    defaults={"cause": cause},
                )
                count += 1
        self._log(f"Mortalités ({count} records)", True)

    def _seed_consommations(self, lots, intrants):
        """
        Seed realistic daily feed + medicine consumption for each lot.
        Pattern: starter feed first 2 weeks, grower next 2 weeks, finisher after.
        """
        from elevage.models import Consommation

        admin = User.objects.filter(is_superuser=True).first()

        def _cons_for_lot(lot, open_ago, close_ago_or_none, initial_birds):
            """Generate consumption records every 3 days for a lot."""
            records = []
            close_ago = close_ago_or_none if close_ago_or_none else 0
            day = open_ago - 1
            while day >= close_ago:
                age_days = open_ago - day
                # Feed phase
                if age_days <= 14:
                    icode = "ALIM-DEM"
                    qty_per_bird = Decimal("0.030")  # 30g/day at start
                elif age_days <= 28:
                    icode = "ALIM-CRO"
                    qty_per_bird = Decimal("0.090")  # 90g/day growing
                else:
                    icode = "ALIM-FIN"
                    qty_per_bird = Decimal("0.150")  # 150g/day finishing

                # 25-kg sac count (rounded to nearest 0.5 sac)
                sacs = (initial_birds * qty_per_bird / 25).quantize(Decimal("0.5"))
                if sacs > 0:
                    records.append((d(day), intrants[icode], sacs))

                # Medicines: vaccinate at day 7 and 21
                if age_days == 7:
                    records.append(
                        (d(day), intrants["MED-NEWC"], Decimal(str(initial_birds)))
                    )
                if age_days == 21:
                    records.append(
                        (d(day), intrants["MED-GCOR"], Decimal(str(initial_birds)))
                    )
                if age_days in (10, 25):
                    records.append((d(day), intrants["MED-VITA"], Decimal("2")))

                day -= 3  # every 3 days (not daily to keep seed manageable)
            return records

        lot_params = {
            "Lot Janvier 2025 — Bâtiment A": (75, 30, 5000),
            "Lot Mars 2025 — Bâtiment A": (40, None, 4000),
            "Lot Avril 2025 — Poussinière 1": (10, None, 6000),
        }

        count = 0
        for designation, (open_ago, close_ago, birds) in lot_params.items():
            lot = lots.get(designation)
            if not lot:
                continue
            for cdate, intrant, qty in _cons_for_lot(lot, open_ago, close_ago, birds):
                Consommation.objects.get_or_create(
                    lot=lot,
                    date=cdate,
                    intrant=intrant,
                    defaults={"quantite": qty, "created_by": admin},
                )
                count += 1
        self._log(f"Consommations ({count} records)", True)

    # ------------------------------------------------------------------

    def _seed_productions(self, lots, produits_finis):
        """
        Create two production records:
          - Lot A (fermé, ~31 days ago) — full harvest covering all product types
          - Lot B (ouvert, ~5 days ago)  — partial mid-cycle harvest

        FIX: records are created as BROUILLON first, lines are inserted, then
        the record is transitioned to VALIDE so the post_save signal fires with
        lines already present in the DB and correctly increments StockProduitFini.
        The old pattern (create as valide + get_or_create stock manually) left
        every StockProduitFini at 0 because:
          1. The signal fired before lines existed → bailed immediately.
          2. get_or_create found the zero-balance row already created by the
             ProduitFini signal and did nothing.
        """
        from production.models import ProductionRecord, ProductionLigne

        admin = User.objects.filter(is_superuser=True).first()

        # ── Shortcuts ──────────────────────────────────────────────────
        pf = produits_finis  # alias for readability
        lot_a = lots.get("Lot Janvier 2025 — Bâtiment A")
        lot_b = lots.get("Lot Mars 2025 — Bâtiment A")

        productions_created = 0

        # ── Production A — Lot Janvier (fermé) ─────────────────────────
        # 4 947 birds harvested:  3 000 sold live, 1 947 processed into cuts
        if lot_a:
            pr_a, created = ProductionRecord.objects.get_or_create(
                lot=lot_a,
                date_production=d(31),
                defaults=dict(
                    nombre_oiseaux_abattus=4947,
                    poids_total_kg=Decimal("10389.00"),
                    poids_moyen_kg=Decimal("2.100"),
                    statut=ProductionRecord.STATUT_BROUILLON,  # ← brouillon first
                    created_by=admin,
                ),
            )
            if created:
                lines_a = [
                    # (produit_fini,               qty,              poids_unit, cout_unit)
                    (
                        pf["دجاج حي (الوزن الكامل)"],
                        Decimal("3000"),
                        Decimal("2.100"),
                        Decimal("89.00"),
                    ),
                    (
                        pf["جثة كاملة منزوعة الأحشاء"],
                        Decimal("800"),
                        Decimal("1.650"),
                        Decimal("72.00"),
                    ),
                    (
                        pf["صدر دجاج"],
                        Decimal("480"),
                        Decimal("0.350"),
                        Decimal("68.00"),
                    ),
                    (
                        pf["فخذ كامل"],
                        Decimal("600"),
                        Decimal("0.280"),
                        Decimal("55.00"),
                    ),
                    (
                        pf["جناح دجاج"],
                        Decimal("350"),
                        Decimal("0.180"),
                        Decimal("42.00"),
                    ),
                    (
                        pf["كبد دجاج"],
                        Decimal("280"),
                        Decimal("0.090"),
                        Decimal("28.00"),
                    ),
                    (
                        pf["قانصة دجاج"],
                        Decimal("240"),
                        Decimal("0.075"),
                        Decimal("22.00"),
                    ),
                ]
                for produit, qty, poids, cout in lines_a:
                    ProductionLigne.objects.create(
                        production=pr_a,
                        produit_fini=produit,
                        quantite=qty,
                        poids_unitaire_kg=poids,
                        cout_unitaire_estime=cout,
                    )
                # NOW transition to valide → signal fires with lines in DB
                pr_a.statut = ProductionRecord.STATUT_VALIDE
                pr_a.save()
                productions_created += 1

        # ── Production B — Lot Mars (ouvert, partial harvest) ──────────
        if lot_b:
            pr_b, created = ProductionRecord.objects.get_or_create(
                lot=lot_b,
                date_production=d(5),
                defaults=dict(
                    nombre_oiseaux_abattus=1200,
                    poids_total_kg=Decimal("2640.00"),
                    poids_moyen_kg=Decimal("2.200"),
                    statut=ProductionRecord.STATUT_BROUILLON,
                    created_by=admin,
                ),
            )
            if created:
                lines_b = [
                    (
                        pf["جثة كاملة منزوعة الأحشاء"],
                        Decimal("500"),
                        Decimal("1.700"),
                        Decimal("74.00"),
                    ),
                    (
                        pf["صدر دجاج"],
                        Decimal("220"),
                        Decimal("0.360"),
                        Decimal("70.00"),
                    ),
                    (
                        pf["فخذ كامل"],
                        Decimal("280"),
                        Decimal("0.290"),
                        Decimal("57.00"),
                    ),
                    (
                        pf["جناح دجاج"],
                        Decimal("160"),
                        Decimal("0.185"),
                        Decimal("44.00"),
                    ),
                    (
                        pf["كبد دجاج"],
                        Decimal("120"),
                        Decimal("0.092"),
                        Decimal("29.00"),
                    ),
                    (
                        pf["قانصة دجاج"],
                        Decimal("100"),
                        Decimal("0.078"),
                        Decimal("23.00"),
                    ),
                ]
                for produit, qty, poids, cout in lines_b:
                    ProductionLigne.objects.create(
                        production=pr_b,
                        produit_fini=produit,
                        quantite=qty,
                        poids_unitaire_kg=poids,
                        cout_unitaire_estime=cout,
                    )
                pr_b.statut = ProductionRecord.STATUT_VALIDE
                pr_b.save()
                productions_created += 1

        self._log(
            f"ProductionRecords ({productions_created} created / validated, stock updated via signal)",
            True,
        )
        return {}

    # ------------------------------------------------------------------

    def _seed_bl_clients(self, clients, produits_finis):
        from clients.models import BLClient, BLClientLigne

        admin = User.objects.filter(is_superuser=True).first()
        vivant = produits_finis["دجاج حي (الوزن الكامل)"]
        carcasse = produits_finis["جثة كاملة منزوعة الأحشاء"]
        oeuf = produits_finis.get("صينية بيض (30 بيضة)")
        marche = clients["Marché de Gros Setifien"]
        palmier = clients["Restaurant Le Palmier"]
        amrane = clients["Boucherie Amrane & Fils"]
        epicerie = clients["Épicerie Centrale Azazga"]
        grossiste = clients["Grossiste Alger Sud"]

        # ── Ensure egg stock exists (eggs are not created by a ProductionRecord) ──
        if oeuf:
            from stock.models import StockProduitFini as _SPF

            _SPF.objects.update_or_create(
                produit_fini=oeuf,
                defaults={"quantite": Decimal("3000")},
            )

        bl_specs = [
            dict(
                ref="BLC-2025-001",
                client=marche,
                date_ago=28,
                statut=BLClient.STATUT_FACTURE,
                lines=[(vivant, Decimal("1500"), Decimal("480"))],
            ),
            dict(
                ref="BLC-2025-002",
                client=palmier,
                date_ago=25,
                statut=BLClient.STATUT_FACTURE,
                lines=[(carcasse, Decimal("300"), Decimal("750"))],
            ),
            dict(
                ref="BLC-2025-003",
                client=amrane,
                date_ago=20,
                statut=BLClient.STATUT_LIVRE,
                lines=[(carcasse, Decimal("200"), Decimal("750"))],
            ),
            dict(
                ref="BLC-2025-004",
                client=marche,
                date_ago=15,
                statut=BLClient.STATUT_LIVRE,
                lines=[(vivant, Decimal("800"), Decimal("490"))],
            ),
            dict(
                ref="BLC-2025-005",
                client=palmier,
                date_ago=5,
                statut=BLClient.STATUT_BROUILLON,
                lines=[(carcasse, Decimal("100"), Decimal("760"))],
            ),
            # ── Egg BLs (drive fiche_dettes_client demo data) ────────────────
            dict(
                ref="BLC-2025-006",
                client=marche,
                date_ago=32,
                statut=BLClient.STATUT_LIVRE,
                lines=[(oeuf, Decimal("500"), Decimal("350.00"))],
            ),
            dict(
                ref="BLC-2025-007",
                client=epicerie,
                date_ago=22,
                statut=BLClient.STATUT_LIVRE,
                lines=[(oeuf, Decimal("200"), Decimal("345.00"))],
            ),
            dict(
                ref="BLC-2025-008",
                client=grossiste,
                date_ago=12,
                statut=BLClient.STATUT_LIVRE,
                lines=[(oeuf, Decimal("800"), Decimal("355.00"))],
            ),
        ]

        result = {}
        for spec in bl_specs:
            # Skip egg BLs if the egg product wasn't seeded
            if any(pf is None for pf, *_ in spec["lines"]):
                continue

            target_statut = spec["statut"]

            # Always create as BROUILLON first so lines can be inserted before
            # the transition signal fires (mirrors the ProductionRecord pattern).
            bl, created = BLClient.objects.get_or_create(
                reference=spec["ref"],
                defaults=dict(
                    client=spec["client"],
                    date_bl=d(spec["date_ago"]),
                    statut=BLClient.STATUT_BROUILLON,
                    created_by=admin,
                ),
            )
            if created:
                for pf, qty, pu in spec["lines"]:
                    BLClientLigne.objects.create(
                        bl=bl, produit_fini=pf, quantite=qty, prix_unitaire=pu
                    )
                # Transition to LIVRE so the post_save signal fires with lines
                # already in the DB and correctly decrements StockProduitFini.
                # FACTURE BLs also go through LIVRE here — _seed_factures_client
                # will lock them to FACTURE via the FactureClient.bls m2m signal.
                if target_statut in (BLClient.STATUT_LIVRE, BLClient.STATUT_FACTURE):
                    bl.statut = BLClient.STATUT_LIVRE
                    bl.save()

            result[spec["ref"]] = bl

        self._log(f"BLClients ({len(bl_specs)})", True)
        return result

    def _seed_factures_client(self, clients, bls):
        from clients.models import FactureClient

        admin = User.objects.filter(is_superuser=True).first()

        bl1 = bls["BLC-2025-001"]  # marché
        bl2 = bls["BLC-2025-002"]  # palmier

        mt1_ht = sum(l.montant_total for l in bl1.lignes.all())
        mt2_ht = sum(l.montant_total for l in bl2.lignes.all())
        tva = Decimal("19.00")

        def ttc(ht, taux):
            return (ht * (1 + taux / 100)).quantize(Decimal("0.01"))

        def tva_amt(ht, taux):
            return (ht * taux / 100).quantize(Decimal("0.01"))

        factures = {}

        # Facture 1 — Marché — partially paid
        f1, created = FactureClient.objects.get_or_create(
            reference="FAC-2025-001",
            defaults=dict(
                client=clients["Marché de Gros Setifien"],
                date_facture=d(27),
                date_echeance=d(1),
                montant_ht=mt1_ht,
                taux_tva=tva,
                montant_tva=tva_amt(mt1_ht, tva),
                montant_ttc=ttc(mt1_ht, tva),
                montant_regle=Decimal("400000.00"),
                reste_a_payer=ttc(mt1_ht, tva) - Decimal("400000.00"),
                statut=FactureClient.STATUT_PARTIELLEMENT_PAYEE,
                created_by=admin,
            ),
        )
        if created:
            f1.bls.set([bl1])
        factures["FAC-2025-001"] = f1

        # Facture 2 — Palmier — fully paid
        f2, created = FactureClient.objects.get_or_create(
            reference="FAC-2025-002",
            defaults=dict(
                client=clients["Restaurant Le Palmier"],
                date_facture=d(24),
                date_echeance=d(9),
                montant_ht=mt2_ht,
                taux_tva=tva,
                montant_tva=tva_amt(mt2_ht, tva),
                montant_ttc=ttc(mt2_ht, tva),
                montant_regle=ttc(mt2_ht, tva),
                reste_a_payer=Decimal("0"),
                statut=FactureClient.STATUT_PAYEE,
                created_by=admin,
            ),
        )
        if created:
            f2.bls.set([bl2])
        factures["FAC-2025-002"] = f2

        self._log("FacturesClient (2)", True)
        return factures

    def _seed_paiements_client(self, clients, factures):
        from clients.models import PaiementClient, PaiementClientAllocation

        admin = User.objects.filter(is_superuser=True).first()
        f1 = factures["FAC-2025-001"]
        f2 = factures["FAC-2025-002"]

        # Partial payment on FAC-2025-001
        p1, created = PaiementClient.objects.get_or_create(
            client=clients["Marché de Gros Setifien"],
            date_paiement=d(20),
            montant=Decimal("400000.00"),
            defaults=dict(
                mode_paiement="cheque",
                reference_paiement="CHQ-CLI-0091",
                created_by=admin,
            ),
        )
        if created:
            PaiementClientAllocation.objects.create(
                paiement=p1, facture=f1, montant_alloue=Decimal("400000.00")
            )

        # Full payment on FAC-2025-002
        p2, created = PaiementClient.objects.get_or_create(
            client=clients["Restaurant Le Palmier"],
            date_paiement=d(18),
            montant=f2.montant_ttc,
            defaults=dict(mode_paiement="especes", created_by=admin),
        )
        if created:
            PaiementClientAllocation.objects.create(
                paiement=p2, facture=f2, montant_alloue=f2.montant_ttc
            )

        self._log("PaiementsClient (2) + Allocations", True)

    # ------------------------------------------------------------------

    def _seed_prix_marche(self, produits_finis):
        """
        Seed historical egg market prices (PrixMarche) that span the egg BL dates
        so the fiche_dettes_client can compute meaningful margins:

          BLC-2025-006  d(32)  actual 350 → market on d(32) = last price ≤ d(32)
                                = record at d(35): 330 → margin = +20 (above market)
          BLC-2025-007  d(22)  actual 345 → market on d(22) = record at d(25): 340
                                → margin = +5  (above market)
          BLC-2025-008  d(12)  actual 355 → market on d(12) = record at d(15): 348
                                → margin = +7  (above market)
        """
        from clients.models import PrixMarche

        oeuf = produits_finis.get("صينية بيض (30 بيضة)")
        if not oeuf:
            self._log("PrixMarche — skipped (no egg product found)", False)
            return

        admin = User.objects.filter(is_superuser=True).first()

        # (days_ago, prix_marche, source)
        prix_specs = [
            (45, Decimal("320.00"), "ONAB"),
            (35, Decimal("330.00"), "ONAB"),  # used for BLC-2025-006 (d32)
            (25, Decimal("340.00"), "السوق المحلي"),  # used for BLC-2025-007 (d22)
            (15, Decimal("348.00"), "السوق المحلي"),  # used for BLC-2025-008 (d12)
            (5, Decimal("358.00"), "ONAB"),
        ]

        count = 0
        for days_ago, prix, source in prix_specs:
            _, created = PrixMarche.objects.get_or_create(
                produit_fini=oeuf,
                date=d(days_ago),
                defaults=dict(
                    prix_marche=prix,
                    source=source,
                    notes="",
                    created_by=admin,
                ),
            )
            if created:
                count += 1

        self._log(f"PrixMarche ({count} records — صينية بيض)", True)

    # ------------------------------------------------------------------

    def _seed_depenses(self, categories, lots, factures):
        from depenses.models import Depense

        admin = User.objects.filter(is_superuser=True).first()
        lot_a = lots.get("Lot Janvier 2025 — Bâtiment A")
        lot_b = lots.get("Lot Mars 2025 — Bâtiment A")
        lot_c = lots.get("Lot Avril 2025 — Poussinière 1")
        f_service = factures.get("FRN-2025-003")  # service invoice for linking demo

        specs = [
            dict(
                date=d(70),
                cat="SALAIRES",
                desc="Salaires ouvriers — Janvier 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_a,
            ),
            dict(
                date=d(70),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Janvier 2025",
                montant=Decimal("32000"),
                mode="cheque",
                lot=lot_a,
            ),
            dict(
                date=d(65),
                cat="VETERINAIRE",
                desc="Visite vétérinaire Lot Janvier",
                montant=Decimal("8500"),
                mode="especes",
                lot=lot_a,
            ),
            dict(
                date=d(60),
                cat="MAINTENANCE",
                desc="Réparation système de ventilation Bât A",
                montant=Decimal("15000"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(55),
                cat="TRANSPORT",
                desc="Carburant livraison — Semaine 10",
                montant=Decimal("4200"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(40),
                cat="SALAIRES",
                desc="Salaires ouvriers — Février 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_b,
            ),
            dict(
                date=d(40),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Février 2025",
                montant=Decimal("29500"),
                mode="cheque",
                lot=lot_b,
            ),
            dict(
                date=d(35),
                cat="FOURNITURES",
                desc="Achat litière supplémentaire",
                montant=Decimal("6000"),
                mode="especes",
                lot=lot_b,
            ),
            dict(
                date=d(30),
                cat="VETERINAIRE",
                desc="Visite vétérinaire Lot Mars",
                montant=Decimal("7000"),
                mode="especes",
                lot=lot_b,
            ),
            dict(
                date=d(20),
                cat="SALAIRES",
                desc="Salaires ouvriers — Mars 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_c,
            ),
            dict(
                date=d(20),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Mars 2025",
                montant=Decimal("31000"),
                mode="cheque",
                lot=lot_c,
            ),
            dict(
                date=d(15),
                cat="MAINTENANCE",
                desc="Entretien maintenance préventive",
                montant=Decimal("45000"),
                mode="virement",
                lot=None,
                facture_liee=f_service,
            ),  # BR-DEP-03 service invoice link demo
            dict(
                date=d(10),
                cat="TRANSPORT",
                desc="Carburant livraison — Semaine 15",
                montant=Decimal("3800"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(5),
                cat="TAXES",
                desc="Taxe locale trimestrielle",
                montant=Decimal("12000"),
                mode="cheque",
                lot=None,
            ),
            dict(
                date=d(2),
                cat="DIVERS",
                desc="Matériel divers entretien",
                montant=Decimal("2200"),
                mode="especes",
                lot=None,
            ),
        ]

        count = 0
        for s in specs:
            Depense.objects.get_or_create(
                date=s["date"],
                categorie=categories[s["cat"]],
                description=s["desc"],
                defaults=dict(
                    montant=s["montant"],
                    mode_paiement=s["mode"],
                    lot=s.get("lot"),
                    facture_liee=s.get("facture_liee"),
                    enregistre_par=admin,
                ),
            )
            count += 1
        self._log(f"Dépenses ({count} records)", True)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _log(self, label: str, created: bool):
        symbol = self.style.SUCCESS("  ✓") if created else self.style.WARNING("  ~")
        self.stdout.write(f"{symbol} {label}")
