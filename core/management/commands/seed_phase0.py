"""
management/commands/seed_phase0.py

تهيئة "Phase 0" لسيناريو الدورة الكاملة (fresh start) — الموردون، العملاء،
والمدخلات (Intrants) — بحيث لا يحتاج المستخدم لإدخالها يدوياً عبر الواجهة.

الاستخدام:
    python manage.py seed_phase0          # تعبئة (آمن للتكرار)
    python manage.py seed_phase0 --clear  # حذف الموردين/العملاء/المدخلات ثم إعادة التعبئة

⚠️ التسلسل :
    1. python manage.py seed_db_minimal   ← يجب تشغيله أولاً (التصنيفات + ProduitFini)
    2. python manage.py seed_phase0       ← هذا الملف (الموردون/العملاء/المدخلات)
    3. Bâtiments                          ← تُنشأ يدوياً (branch-scoped, لا يوجد مصنع افتراضي)
    4. لانطلاق سيناريو الدورة : Phase 1 — Achats Intrants (BLF-2026-0001 …)

ما يتم إنشاؤه (بيانات حقيقية للمزرعة — سطيف) :
    • Fournisseur (8)  — ONAB, عبد الحكيم, kamel el eulma, Sanvital,
                          Vétérinaire Tarek, SARL EL-REDHOUANE, Kavim Rachid,
                          Khabchache Moussa
    • Client (4)       — IDIR AMBULANT BEJAIA, samir bejia, MOUHAMAD KALAI,
                          ETS BOUAOUDIA
    • Intrant (53)     — poussins (ISA Brown/Lohmann Brown/Bovans Brown), أعلاف خام (MAIS/SOJA/Phosphate/CMV×2/Sanvital)
                          + علفان جاهزان (Aliment Démarrage Poussin،
                          Aliment Ponte Poule) يُصنَّعان داخلياً عبر
                          ProductionAliment, répartis désormais sur 5
                          catégories dédiées :
                          VACCIN (Variant, NDIB, LTI, ND, H120, D78, H9, H5,
                          EDS), VITAMINE (Watervit, Bplus, Vit C, Ultravit,
                          Anylite C, Artimix, Respimint, Stressvit,
                          Vitaprol, Ossebiotic, Selevit), ANTIBIOTIQUE
                          (neomeriol, Tylan, Amoxy, Doxatrim, Tetracycline,
                          Colivet, Neomycine, Hepadyn, Sogecoli, Amoxid,
                          Ampicoli, Phosfomycine), DESINFECTANT (acide,
                          Desinfectant), MEDICAMENT (Aldekol, mastersorb,
                          Amprol, Piperazine, Toxidren, Zinc, Lumans,
                          Vemarom)
    • FormuleAliment (2) — « Démarrage Poussin — standard » et
                          « Ponte Poule — standard », composées uniquement
                          de matières premières (MAIS/SOJA/Phosphate/CMV/
                          Sanvital) — jamais de l'aliment fini lui-même.

ما لا يتم إنشاؤه هنا (يُسجَّل يدوياً عبر الواجهة — branch-scoped) :
    • Bâtiments (STOCK › Bâtiments › Nouveau) — nécessitent une Branche explicite
    • Toute donnée opérationnelle (BL, lots, factures, mouvements de stock …)

Prérequis : seed_db_minimal doit avoir été exécuté avant (CategorieIntrant et
TypeFournisseur doivent déjà exister — sinon ce script échoue proprement).

آمن للتكرار: يستخدم get_or_create في كل مكان (بالاعتماد على `nom` لـ
Fournisseur/Client و`designation` لـ Intrant).
جميع المبالغ بالدينار الجزائري (DZD).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Phase 0 du scénario fresh-start : Fournisseurs, Clients, Intrants.\n"
        "À exécuter après seed_db_minimal. Ne crée aucun bâtiment "
        "(branch-scoped, à créer manuellement)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "احذف جميع الموردين والعملاء والمدخلات قبل إعادة التعبئة. "
                "تحذير: سيفشل إذا كانت هناك سجلات تشغيلية مرتبطة (BL, لوت …)."
            ),
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            self._clear()

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "\n=== seed_phase0 — Fournisseurs / Clients / Intrants ===\n"
            )
        )

        fournisseurs = self._seed_fournisseurs()
        self._seed_clients()
        intrants = self._seed_intrants(fournisseurs)
        self._seed_formules_aliment(intrants)

        self.stdout.write(
            self.style.SUCCESS(
                "\n✓ Phase 0 (catalogue) terminée.\n"
                "  Étapes suivantes :\n"
                "    1. Créer les bâtiments  → STOCK › Bâtiments › Nouveau\n"
                "       (Bâtiment A, type=poussiniere, requis pour ouvrir le lot)\n"
                "    2. Lancer Phase 1 — Achats Intrants (BLF-2026-0001 …)\n"
            )
        )

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(self):
        self.stdout.write(
            self.style.WARNING("  Suppression Fournisseurs/Clients/Intrants…")
        )
        from intrants.models import Intrant, Fournisseur
        from clients.models import Client
        from elevage.models import FormuleAliment

        FormuleAliment.objects.all().delete()
        Intrant.objects.all().delete()
        Fournisseur.objects.all().delete()
        Client.objects.all().delete()
        self.stdout.write("  Terminé.\n")

    # ------------------------------------------------------------------
    # Seeders
    # ------------------------------------------------------------------

    def _seed_fournisseurs(self):
        """Catalogue réel des fournisseurs de la ferme (Sétif)."""
        from intrants.models import Fournisseur, TypeFournisseur

        def type_(code):
            try:
                return TypeFournisseur.objects.get(code=code)
            except TypeFournisseur.DoesNotExist:
                raise CommandError(
                    f"TypeFournisseur '{code}' introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

        specs = [
            dict(
                nom="ONAB",
                type_principal=type_("ALIMENTS"),
                adresse="بجاية",
                wilaya="بجاية",
            ),
            dict(
                nom="عبد الحكيم",
                type_principal=type_("AUTRE"),
                adresse="بجاية",
                wilaya="sétif",
                telephone="0561799337",
                telephone_2="0561910005",
            ),
            dict(
                nom="kamel el eulma",
                type_principal=type_("ALIMENTS"),
                adresse="العلمة",
                wilaya="sétif",
                telephone="0770222235",
                telephone_2="0550850996",
            ),
            dict(
                nom="Sanvital",
                type_principal=type_("ALIMENTS"),
                adresse="rue des aures ihdadden 06000",
                wilaya="bejaia",
                telephone="034169315",
                email="sanvital2001@yahoo.fr",
                nif="000106010578946",
                rc="01b183808",
            ),
            dict(
                nom="Vétérinaire Tarek",
                type_principal=type_("MEDICAMENTS"),
                adresse="setif",
                wilaya="setif",
                telephone="0661318376",
            ),
            dict(
                nom="SARL EL-REDHOUANE",
                type_principal=type_("POUSSINS"),
                adresse="SIDI OKBA",
                wilaya="biskra",
                telephone="0550901192",
                telephone_2="0550981180",
            ),
            dict(
                nom="Kavim Rachid",
                type_principal=type_("PLATEAU"),
                wilaya="mostghanem",
                telephone="0660799530",
                telephone_2="0550203468",
            ),
            dict(
                nom="Khabchache Moussa",
                type_principal=type_("ALIMENTS"),
                adresse="ouricia",
                wilaya="setif",
                telephone="0661565493",
            ),
        ]
        created_count = 0
        objs = {}
        for s in specs:
            obj, created = Fournisseur.objects.get_or_create(nom=s["nom"], defaults=s)
            objs[s["nom"]] = obj
            if created:
                created_count += 1
        self._log(f"Fournisseur ({len(specs)})", created_count > 0)
        return objs

    def _seed_clients(self):
        """Catalogue réel des clients de la ferme (Sétif)."""
        from clients.models import Client, TypeClient

        def type_client(code):
            try:
                return TypeClient.objects.get(code=code)
            except TypeClient.DoesNotExist:
                raise CommandError(
                    f"TypeClient '{code}' introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

        specs = [
            dict(
                nom="IDIR AMBULANT BEJAIA",
                type_client=type_client("GROSSISTE"),
                adresse="bejaia",
                wilaya="bejaia",
                telephone="0770914240",
                telephone_2="0658059900",
                email="idirkarim2@gmail.com",
                rc="97A0910402-06/00",
                plafond_credit=Decimal("2000000.00"),
                actif=True,
            ),
            dict(
                nom="samir bejia",
                type_client=type_client("GROSSISTE"),
                adresse="بجاية",
                telephone="0550041172",
                plafond_credit=Decimal("0.00"),
                actif=False,
            ),
            dict(
                nom="MOUHAMAD KALAI",
                type_client=type_client("DETAILLANT"),
                adresse="حشمي حي 50 مسكن اجتماعي عمارة 5 حصة 01 و 02",
                wilaya="sétif",
                telephone="0550680684",
                rc="533792221-19/00",
                plafond_credit=Decimal("0.00"),
                notes="يسمى نذير",
                actif=True,
            ),
            dict(
                nom="ETS BOUAOUDIA",
                type_client=type_client("GROSSISTE"),
                adresse="cite douaniere ighil ouazzoug",
                wilaya="bejaia",
                telephone="0550041172",
                plafond_credit=Decimal("0.00"),
                actif=True,
            ),
        ]
        created_count = 0
        for s in specs:
            _, created = Client.objects.get_or_create(nom=s["nom"], defaults=s)
            if created:
                created_count += 1
        self._log(f"Client ({len(specs)})", created_count > 0)

    def _seed_intrants(self, fournisseurs):
        """Catalogue reel des intrants de la ferme (Setif)."""
        from intrants.models import Intrant, CategorieIntrant, UniteMesure

        def cat(code):
            try:
                return CategorieIntrant.objects.get(code=code)
            except CategorieIntrant.DoesNotExist:
                raise CommandError(
                    f"CategorieIntrant '{code}' introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

        def unite(code):
            try:
                return UniteMesure.objects.get(code=code)
            except UniteMesure.DoesNotExist:
                raise CommandError(
                    f"UniteMesure '{code}' introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

        onab = fournisseurs["ONAB"]
        kamel = fournisseurs["kamel el eulma"]
        sanvital = fournisseurs["Sanvital"]
        tarek = fournisseurs["Vétérinaire Tarek"]
        redouane = fournisseurs["SARL EL-REDHOUANE"]

        specs = [
            # -- Poussins ------------------------------------------------
            # -- Poussins (races pondeuses réellement utilisées en Algérie) --
            dict(
                designation="Poussin ISA Brown",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure=unite("UNITE"),
                seuil_alerte=Decimal("100"),
                fournisseurs=[onab, redouane],
            ),
            dict(
                designation="Poussin Lohmann Brown",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure=unite("UNITE"),
                seuil_alerte=Decimal("100"),
                fournisseurs=[onab, redouane],
            ),
            dict(
                designation="Poussin Bovans Brown",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure=unite("UNITE"),
                seuil_alerte=Decimal("100"),
                fournisseurs=[onab, redouane],
            ),
            # -- Aliments (sacs de 100 kg -- mais, soja, phosphate, CMV) -
            dict(
                designation="MAIS ARG",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("200"),
                fournisseurs=[kamel, onab],
            ),
            dict(
                designation="SOJA",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("50"),
                fournisseurs=[kamel, onab],
            ),
            dict(
                designation="Phosphate",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("10"),
                fournisseurs=[kamel],
            ),
            dict(
                designation="CMV  PONDEUSE 1.5%",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("1"),
                fournisseurs=[sanvital],
            ),
            dict(
                designation="CMV FUTURE PONDEUSE PFP 1.25%",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("1"),
                fournisseurs=[sanvital],
            ),
            dict(
                designation="SANVITAL VITASTART VOLAILE 0.3125%",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("1"),
                fournisseurs=[sanvital],
            ),
            # -- Aliments finis (produits en interne via ProductionAliment,
            #    voir _seed_formules_aliment ci-dessous) -------------------
            dict(
                designation="Aliment Démarrage Poussin",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("100"),
                fournisseurs=[],
            ),
            dict(
                designation="Aliment Ponte Poule",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_PONTE,
                unite_mesure=unite("SAC100"),
                seuil_alerte=Decimal("100"),
                fournisseurs=[],
            ),
            # -- Medicaments / veterinaire --------------------------------
            dict(
                designation="Watervit",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Bplus",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="neomeriol",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="acide",
                categorie=cat("DESINFECTANT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Aldekol",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="mastersorb",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Tylan",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Vit C",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("G"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            # -- Medicaments / vitamines / desinfectants (extension) -----
            dict(
                designation="Ultravit",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Amoxy",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Anylite C",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Doxatrim",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Artimix",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Respimint",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Amprol",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("LITRE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Piperazine",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Stressvit",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Vitaprol",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Ossebiotic",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Desinfectant",
                categorie=cat("DESINFECTANT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("LITRE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Tetracycline",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Colivet",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Neomycine",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("LITRE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Hepadyn",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("LITRE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Sogecoli",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Toxidren",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Amoxid",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Ampicoli",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Zinc",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Lumans",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Phosfomycine",
                categorie=cat("ANTIBIOTIQUE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("KG"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Vemarom",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("LITRE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="Selevit",
                categorie=cat("VITAMINE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            # -- Vaccins buvables (oraux) ----------------------------------
            dict(
                designation="Variant",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="NDIB",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="LTI",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="ND",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="H120",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="D78",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            # -- Vaccins injectables ---------------------------------------
            dict(
                designation="H9",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="H5",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
            dict(
                designation="EDS",
                categorie=cat("VACCIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure=unite("DOSE"),
                seuil_alerte=Decimal("0"),
                fournisseurs=[tarek],
            ),
        ]

        created_count = 0
        objs = {}
        for s in specs:
            m2m_fournisseurs = s.pop("fournisseurs")
            obj, created = Intrant.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    categorie=s["categorie"],
                    stade=s["stade"],
                    unite_mesure=s["unite_mesure"],
                    seuil_alerte=s["seuil_alerte"],
                    actif=True,
                ),
            )
            if m2m_fournisseurs:
                obj.fournisseurs.set(m2m_fournisseurs)
            if created:
                created_count += 1
            objs[s["designation"]] = obj
        self._log(f"Intrant ({len(specs)})", created_count > 0)  # 53 total
        return objs

    def _seed_formules_aliment(self, intrants):
        """Recettes de départ pour les deux alimenents finis semés ci-dessus.

        Chaque FormuleAlimentLigne référence un intrant MATIÈRE PREMIÈRE
        (maïs, soja, phosphate, CMV/prémix) — jamais l'aliment fini
        (`intrant_produit`) lui-même : une formule qui se contiendrait comme
        ingrédient créerait une boucle de stock incohérente (on décompterait
        le produit fini de son propre stock au moment même où on le
        crédite). `unique_together = (formule, intrant)` empêcherait de
        toute façon un doublon, mais on évite ici jusqu'à la possibilité de
        se tromper de sens.
        """
        from elevage.models import FormuleAliment, FormuleAlimentLigne

        mais = intrants["MAIS ARG"]
        soja = intrants["SOJA"]
        phosphate = intrants["Phosphate"]
        cmv_pondeuse = intrants["CMV  PONDEUSE 1.5%"]
        cmv_pre_pondeuse = intrants["CMV FUTURE PONDEUSE PFP 1.25%"]
        vitastart = intrants["SANVITAL VITASTART VOLAILE 0.3125%"]
        aliment_poussin = intrants["Aliment Démarrage Poussin"]
        aliment_poule = intrants["Aliment Ponte Poule"]

        specs = [
            dict(
                nom="Démarrage Poussin — standard",
                intrant_produit=aliment_poussin,
                lignes=[
                    (mais, Decimal("56.000")),
                    (soja, Decimal("30.000")),
                    (phosphate, Decimal("3.000")),
                    (vitastart, Decimal("0.3125")),
                ],
            ),
            dict(
                nom="Ponte Poule — standard",
                intrant_produit=aliment_poule,
                lignes=[
                    (mais, Decimal("58.000")),
                    (soja, Decimal("22.000")),
                    (phosphate, Decimal("4.000")),
                    (cmv_pondeuse, Decimal("1.500")),
                    (cmv_pre_pondeuse, Decimal("1.250")),
                ],
            ),
        ]

        created_count = 0
        for s in specs:
            # Safety net matching FormuleAliment.clean()-style expectations:
            # never let the produced feed sneak into its own ingredient list.
            lignes = [
                (ingredient, qte)
                for ingredient, qte in s["lignes"]
                if ingredient.pk != s["intrant_produit"].pk
            ]

            formule, created = FormuleAliment.objects.get_or_create(
                nom=s["nom"],
                defaults=dict(
                    intrant_produit=s["intrant_produit"],
                    actif=True,
                ),
            )
            for ingredient, proportion_kg in lignes:
                FormuleAlimentLigne.objects.get_or_create(
                    formule=formule,
                    intrant=ingredient,
                    defaults=dict(proportion_kg=proportion_kg),
                )
            if created:
                created_count += 1
        self._log(f"FormuleAliment ({len(specs)})", created_count > 0)

    # ------------------------------------------------------------------

    def _log(self, label: str, created: bool):
        symbol = self.style.SUCCESS("  ✓") if created else self.style.WARNING("  ~")
        self.stdout.write(f"{symbol} {label}")
