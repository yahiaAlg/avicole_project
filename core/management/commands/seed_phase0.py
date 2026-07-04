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

ما يتم إنشاؤه (مطابق لـ scenario_avicole_full_cycle_fresh_start.md §Phase 0) :
    • Fournisseur (5)  — CCA / ONAB / Sanofi / Proxi-Aliments / Techno-Avicole
    • Client (5)       — Marché de Gros / Boucherie Amrane / Restaurant Le Palmier /
                          Épicerie Centrale Azazga / Grossiste Alger Sud
    • Intrant (13)     — Poussin Ross 308, Aliments (démarrage/croissance/finition),
                          Vaccins (Newcastle/Gumboro), Amoxicilline, Vitamines,
                          Poussine ISA Brown, Aliments Pré-Ponte/Ponte,
                          Poussin Cobb 500, Litière

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
        self._seed_intrants(fournisseurs)

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

        Intrant.objects.all().delete()
        Fournisseur.objects.all().delete()
        Client.objects.all().delete()
        self.stdout.write("  Terminé.\n")

    # ------------------------------------------------------------------
    # Seeders
    # ------------------------------------------------------------------

    def _seed_fournisseurs(self):
        """FOURN-1 → FOURN-5 (scenario §0.1)."""
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
                nom="Couvoirs du Centre — CCA",
                type_principal=type_("POUSSINS"),
                adresse="Zone Agro-industrielle, Blida",
                wilaya="Blida",
                telephone="025 55 66 77",
                nif="009000000002",
                rc="09/00-0000002 B 02",
            ),
            dict(
                nom="ONAB Setifien",
                type_principal=type_("ALIMENTS"),
                adresse="Route de Boghni, Setifien",
                wilaya="Setifien",
                telephone="026 12 34 56",
                nif="099000000001",
                rc="16/00-0000001 B 01",
            ),
            dict(
                nom="Sanofi Algérie (Vétérinaire)",
                type_principal=type_("MEDICAMENTS"),
                adresse="Rue Hassiba Ben Bouali, Alger",
                wilaya="Alger",
                telephone="021 99 00 11",
                nif="016000000003",
                rc="16/00-0000003 B 03",
            ),
            dict(
                nom="Proxi-Aliments Boumerdès",
                type_principal=type_("ALIMENTS"),
                adresse="Zone Industrielle, Boumerdès",
                wilaya="Boumerdès",
                telephone="024 81 22 33",
            ),
            dict(
                nom="Techno-Avicole Services",
                type_principal=type_("SERVICES"),
                adresse="Rue des Frères Bouadou, Birtouta, Alger",
                wilaya="Alger",
                telephone="021 30 40 50",
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
        """CLI-1 → CLI-5 (scenario §0.2)."""
        from clients.models import Client

        specs = [
            dict(
                nom="Marché de Gros Setifien",
                type_client="grossiste",
                wilaya="Setifien",
                telephone="0555 11 22 33",
                plafond_credit=Decimal("500000.00"),
            ),
            dict(
                nom="Boucherie Amrane & Fils",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0660 33 44 55",
                plafond_credit=Decimal("200000.00"),
            ),
            dict(
                nom="Restaurant Le Palmier",
                type_client="restauration",
                wilaya="Setifien",
                telephone="0770 22 33 44",
                plafond_credit=Decimal("150000.00"),
            ),
            dict(
                nom="Épicerie Centrale Azazga",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0555 44 55 66",
                plafond_credit=Decimal("80000.00"),
            ),
            dict(
                nom="Grossiste Alger Sud",
                type_client="grossiste",
                wilaya="Alger",
                telephone="021 88 77 66",
                plafond_credit=Decimal("1000000.00"),
            ),
        ]
        created_count = 0
        for s in specs:
            _, created = Client.objects.get_or_create(nom=s["nom"], defaults=s)
            if created:
                created_count += 1
        self._log(f"Client ({len(specs)})", created_count > 0)

    def _seed_intrants(self, fournisseurs):
        """INT-1 → INT-10 (scenario §0.4)."""
        from intrants.models import Intrant, CategorieIntrant

        def cat(code):
            try:
                return CategorieIntrant.objects.get(code=code)
            except CategorieIntrant.DoesNotExist:
                raise CommandError(
                    f"CategorieIntrant '{code}' introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

        cca = fournisseurs["Couvoirs du Centre — CCA"]
        onab = fournisseurs["ONAB Setifien"]
        sanofi = fournisseurs["Sanofi Algérie (Vétérinaire)"]

        specs = [
            dict(
                designation="كتكوت روس 308 (يوم واحد)",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="unite",
                seuil_alerte=Decimal("100"),
                fournisseurs=[cca],
            ),
            dict(
                designation="علف البداية — الطور الأول (0–14 يوم)",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure="sac",
                seuil_alerte=Decimal("10"),
                fournisseurs=[onab],
            ),
            dict(
                designation="علف النمو — الطور الثاني (15–28 يوم)",
                categorie=cat("ALIMENT"),
                # tous — le lot reste en Poussinière tout le cycle (voir
                # scenario §0.4, note INT-3) : stade=croissance le rendrait
                # invisible dans ConsommationForm pour un lot en poussinière.
                stade=Intrant.STADE_TOUS,
                unite_mesure="sac",
                seuil_alerte=Decimal("15"),
                fournisseurs=[onab],
            ),
            dict(
                designation="علف التسمين — الطور الثالث (29 يوم فأكثر)",
                categorie=cat("ALIMENT"),
                stade=Intrant.STADE_TOUS,  # même raison que ci-dessus
                unite_mesure="sac",
                seuil_alerte=Decimal("20"),
                fournisseurs=[onab],
            ),
            dict(
                designation="لقاح نيوكاسل (هيتشنر B1)",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="dose",
                seuil_alerte=Decimal("500"),
                fournisseurs=[sanofi],
            ),
            dict(
                designation="لقاح غامبورو (IBD متوسط)",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="dose",
                seuil_alerte=Decimal("500"),
                fournisseurs=[sanofi],
            ),
            dict(
                designation="أموكسيسيلين 50% مسحوق",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="g",
                seuil_alerte=Decimal("200"),
                fournisseurs=[sanofi],
            ),
            dict(
                designation="فيتامينات + إلكتروليتات (مركّب)",
                categorie=cat("MEDICAMENT"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="litre",
                seuil_alerte=Decimal("5"),
                fournisseurs=[sanofi],
            ),
            dict(
                designation="كتكوت دجاج بياض ISA Brown (يوم واحد)",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="unite",
                seuil_alerte=Decimal("100"),
                fournisseurs=[cca],
            ),
            dict(
                designation="علف ما قبل الإنتاج — Pré-Ponte (15–18 أسبوع)",
                categorie=cat("ALIMENT"),
                # demarrage : la pondeuse est encore en Poussinière à cet âge
                # (transfert au point de ponte, ~126 j — cf. scenario §5.6).
                stade=Intrant.STADE_DEMARRAGE,
                unite_mesure="sac",
                seuil_alerte=Decimal("10"),
                fournisseurs=[onab],
            ),
            dict(
                designation="علف الإنتاج — Ponte (عالي الكالسيوم)",
                categorie=cat("ALIMENT"),
                # ⚠️ CRITIQUE : stade=croissance et NON stade=ponte. Le
                # mapping LotElevage.stade_intrant_attendu ne renvoie jamais
                # STADE_PONTE (Poulailler → STADE_CROISSANCE uniquement) ;
                # un intrant en stade=ponte serait donc INVISIBLE dans
                # ConsommationForm une fois le lot transféré au poulailler.
                stade=Intrant.STADE_CROISSANCE,
                unite_mesure="sac",
                seuil_alerte=Decimal("15"),
                fournisseurs=[onab],
            ),
            dict(
                designation="كتكوت كوب 500 (يوم واحد)",
                categorie=cat("POUSSIN"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="unite",
                seuil_alerte=Decimal("100"),
                fournisseurs=[cca],
            ),
            dict(
                designation="فراش (نشارة خشب)",
                categorie=cat("AUTRE"),
                stade=Intrant.STADE_TOUS,
                unite_mesure="sac",
                seuil_alerte=Decimal("20"),
                fournisseurs=[],  # laissé vide, comme dans le scénario
            ),
        ]

        created_count = 0
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
        self._log(f"Intrant ({len(specs)})", created_count > 0)

    # ------------------------------------------------------------------

    def _log(self, label: str, created: bool):
        symbol = self.style.SUCCESS("  ✓") if created else self.style.WARNING("  ~")
        self.stdout.write(f"{symbol} {label}")
