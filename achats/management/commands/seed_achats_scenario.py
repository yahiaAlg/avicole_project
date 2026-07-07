"""
management/commands/seed_achats_scenario.py

Peuplement rapide de la Phase 1 du scénario fresh-start — Achats Intrants :
    BL Fournisseur (5) → Facture Fournisseur (5) → Règlement Fournisseur (5)

Objectif : éviter la saisie manuelle via ACHATS → BL Fournisseur / Factures /
Règlements, décrite dans scenario_avicole_full_cycle_fresh_start.md §3.

Utilisation :
    # Peuplement complet (BLF + FRN + REG)
    python manage.py seed_achats_scenario

    # Cibler une branche précise (code Branche) — défaut STF
    python manage.py seed_achats_scenario --branche STF

    # Ne créer que les BL (pas encore de factures/règlements)
    python manage.py seed_achats_scenario --what bls

    # Ne créer que les factures (nécessite que les BL existent déjà, statut Reçu)
    python manage.py seed_achats_scenario --what factures

    # Ne créer que les règlements (nécessite que les factures existent déjà)
    python manage.py seed_achats_scenario --what reglements

    # Supprimer puis recréer (dans l'ordre inverse des dépendances)
    python manage.py seed_achats_scenario --clear

⚠️ Séquence recommandée :
    1. python manage.py seed_db_minimal    ← Branche STF + catégories + ProduitFini
    2. python manage.py seed_phase0        ← Fournisseurs / Clients / Intrants
    3. python manage.py seed_buildings     ← Bâtiments (branch-scoped)
    4. python manage.py seed_achats_scenario  ← ce script (BLF/FRN/REG)
    5. Ouverture du Lot d'Élevage (Phase 2, saisie manuelle ou script dédié)

Détails (scenario §3.2 → §3.5) :
    BLF-2026-0001 — Poussins CCA               (2 000 × Poussin Ross 308 @ 32,00)
    BLF-2026-0002 — Aliments ONAB (lot 1/2)    (200 sacs Démarrage + 180 sacs Croissance)
    BLF-2026-0003 — Aliments ONAB (finition)   (150 sacs Finition)
    BLF-2026-0004 — Médicaments Sanofi         (Vaccins Newcastle/Gumboro + Amox. + Vitamines)
    BLF-2026-0005 — Matières premières ONAB    (500 kg Maïs concassé + 300 kg Tourteau de
                                                 soja — ingrédients FeedFormula, §5.3bis)

    FRN-2026-0001 → 0005 — une facture par BL, montant_total auto-calculé (BR-FAF-01)
    REG-2026-0001 → 0005 — allocation FIFO automatique (BR-REG-03) via signal
                            post_save sur ReglementFournisseur.

Idempotent : get_or_create sur `reference` pour BLF/FRN, et sur la combinaison
(fournisseur, date_reglement, montant, reference_paiement) pour REG (les
règlements n'ont pas de champ reference unique dans le modèle).

Toutes les lignes de BL sont créées AVANT le passage du BL au statut "Reçu"
(la transition brouillon → reçu se fait via une 2ème sauvegarde explicite),
ce qui garantit que le signal `bl_fournisseur_post_save` trouve les lignes
déjà présentes et crée correctement les entrées de stock (StockIntrant ↑,
PMP, StockMouvement) — cf. achats/signals.py.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"

WHAT_CHOICES = ["all", "bls", "factures", "reglements"]

# ---------------------------------------------------------------------------
# Données du scénario (scenario_avicole_full_cycle_fresh_start.md §3)
# ---------------------------------------------------------------------------

# Format : (reference, fournisseur_nom, date_bl, reference_fournisseur,
#           notes_reception, [ (intrant_designation, quantite, prix_unitaire, notes), ... ])
BLF_DATA = [
    (
        "BLF-2026-0001",
        "Couvoirs du Centre — CCA",
        datetime.date(2026, 5, 5),
        "BC-CCA-0512-2026",
        "Arrivée 07h30 — camion frigorifique — bonne condition",
        [
            (
                "كتكوت روس 308 (يوم واحد)",
                Decimal("2000"),
                Decimal("32.0000"),
                "Sexage mixte",
            ),
        ],
    ),
    (
        "BLF-2026-0002",
        "ONAB Setifien",
        datetime.date(2026, 5, 7),
        "ONAB-BL-20260507-088",
        "",
        [
            (
                "علف البداية — الطور الأول (0–14 يوم)",
                Decimal("200"),
                Decimal("1850.0000"),
                "",
            ),
            (
                "علف النمو — الطور الثاني (15–28 يوم)",
                Decimal("180"),
                Decimal("1950.0000"),
                "",
            ),
        ],
    ),
    (
        "BLF-2026-0003",
        "ONAB Setifien",
        datetime.date(2026, 5, 30),
        "",
        "",
        [
            (
                "علف التسمين — الطور الثالث (29 يوم فأكثر)",
                Decimal("150"),
                Decimal("2050.0000"),
                "",
            ),
        ],
    ),
    (
        "BLF-2026-0004",
        "Sanofi Algérie (Vétérinaire)",
        datetime.date(2026, 5, 8),
        "",
        "",
        [
            ("لقاح نيوكاسل (هيتشنر B1)", Decimal("4000"), Decimal("4.5000"), ""),
            ("لقاح غامبورو (IBD متوسط)", Decimal("4000"), Decimal("4.8000"), ""),
            ("أموكسيسيلين 50% مسحوق", Decimal("500"), Decimal("12.0000"), ""),
            ("فيتامينات + إلكتروليتات (مركّب)", Decimal("10"), Decimal("850.0000"), ""),
        ],
    ),
    (
        "BLF-2026-0005",
        "ONAB Setifien",
        datetime.date(2026, 5, 18),
        "",
        "",
        [
            # Matières premières pour la formule "In-House Grower Formula"
            # (§5.3bis, FeedFormula/FeedProduction).
            ("ذرة مجروشة (Maïs concassé)", Decimal("500"), Decimal("45.0000"), ""),
            ("كسب الصويا (Tourteau de soja)", Decimal("300"), Decimal("65.0000"), ""),
        ],
    ),
]

# Format : (reference, fournisseur_nom, [bl_references], date_facture, date_echeance)
FRN_DATA = [
    (
        "FRN-2026-0001",
        "Couvoirs du Centre — CCA",
        ["BLF-2026-0001"],
        datetime.date(2026, 5, 6),
        datetime.date(2026, 6, 5),
    ),
    (
        "FRN-2026-0002",
        "ONAB Setifien",
        ["BLF-2026-0002"],
        datetime.date(2026, 5, 8),
        datetime.date(2026, 6, 7),
    ),
    (
        "FRN-2026-0003",
        "ONAB Setifien",
        ["BLF-2026-0003"],
        datetime.date(2026, 5, 31),
        datetime.date(2026, 6, 30),
    ),
    (
        "FRN-2026-0004",
        "Sanofi Algérie (Vétérinaire)",
        ["BLF-2026-0004"],
        datetime.date(2026, 5, 9),
        datetime.date(2026, 6, 8),
    ),
    (
        "FRN-2026-0005",
        "ONAB Setifien",
        ["BLF-2026-0005"],
        datetime.date(2026, 5, 19),
        datetime.date(2026, 6, 18),
    ),
]

# Format : (fournisseur_nom, date_reglement, montant, mode_paiement, reference_paiement)
REG_DATA = [
    (
        "Couvoirs du Centre — CCA",
        datetime.date(2026, 5, 10),
        Decimal("64000.00"),
        "virement",
        "VIR-BNA-10052026-001",
    ),
    (
        "ONAB Setifien",
        datetime.date(2026, 5, 10),
        Decimal("400000.00"),
        "cheque",
        "CHQ-0455",
    ),
    (
        "ONAB Setifien",
        datetime.date(2026, 5, 25),
        Decimal("321000.00"),
        "virement",
        "",
    ),
    (
        "Sanofi Algérie (Vétérinaire)",
        datetime.date(2026, 5, 15),
        Decimal("51700.00"),
        "virement",
        "",
    ),
    (
        "ONAB Setifien",
        datetime.date(2026, 6, 1),
        Decimal("42000.00"),
        "virement",
        "",
    ),
]


class Command(BaseCommand):
    help = (
        "Phase 1 du scénario fresh-start : BL Fournisseur / Factures / "
        "Règlements (scenario_avicole_full_cycle_fresh_start.md §3). "
        "À exécuter après seed_phase0 (Fournisseurs/Intrants doivent exister)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--branche",
            type=str,
            default=DEFAULT_BRANCHE_CODE,
            help=f"Code de la branche cible (défaut : «{DEFAULT_BRANCHE_CODE}»).",
        )
        parser.add_argument(
            "--what",
            choices=WHAT_CHOICES,
            default="all",
            help="Sous-ensemble à peupler (défaut : all).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Supprime REG puis FRN puis BLF (dans cet ordre) avant de "
                "recréer. Échouera si des règlements/factures dépendent de "
                "données non listées ici."
            ),
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        branche_code = options["branche"].strip().upper()
        what = options["what"]

        from core.models import Branche

        try:
            branche = Branche.objects.get(code=branche_code)
        except Branche.DoesNotExist:
            raise CommandError(
                f"Branche introuvable : «{branche_code}». "
                "Exécutez d'abord 'python manage.py seed_db_minimal'."
            )

        admin = User.objects.filter(is_superuser=True).first()

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n=== seed_achats_scenario — Branche : «{branche}» ===\n"
            )
        )

        if options["clear"]:
            self._clear()

        if what in ("all", "bls"):
            self._seed_bls(branche, admin)
        if what in ("all", "factures"):
            self._seed_factures(branche, admin)
        if what in ("all", "reglements"):
            self._seed_reglements(branche, admin)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_achats_scenario terminé.\n"))

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(self):
        from achats.models import (
            BLFournisseur,
            FactureFournisseur,
            ReglementFournisseur,
        )

        self.stdout.write(self.style.WARNING("  Suppression Règlements/Factures/BL…"))
        refs_blf = [row[0] for row in BLF_DATA]
        refs_frn = [row[0] for row in FRN_DATA]
        fourn_noms = {row[0] for row in REG_DATA}

        try:
            ReglementFournisseur.objects.filter(
                fournisseur__nom__in=fourn_noms
            ).delete()
            FactureFournisseur.objects.filter(reference__in=refs_frn).delete()
            BLFournisseur.objects.filter(reference__in=refs_blf).delete()
        except Exception as exc:
            raise CommandError(f"Impossible de nettoyer proprement : {exc}") from exc
        self.stdout.write("  Terminé.\n")

    # ------------------------------------------------------------------
    # BL Fournisseur
    # ------------------------------------------------------------------

    def _seed_bls(self, branche, admin):
        from achats.models import BLFournisseur, BLFournisseurLigne
        from intrants.models import Fournisseur, Intrant

        created_count = 0
        for reference, fourn_nom, date_bl, ref_fourn, notes_recep, lignes in BLF_DATA:
            try:
                fournisseur = Fournisseur.objects.get(nom=fourn_nom)
            except Fournisseur.DoesNotExist:
                raise CommandError(
                    f"Fournisseur introuvable : «{fourn_nom}». "
                    "Exécutez d'abord 'python manage.py seed_phase0'."
                )

            bl, created = BLFournisseur.objects.get_or_create(
                reference=reference,
                defaults=dict(
                    branche=branche,
                    fournisseur=fournisseur,
                    date_bl=date_bl,
                    reference_fournisseur=ref_fourn,
                    type_document=BLFournisseur.TYPE_BL_CLASSIQUE,
                    statut=BLFournisseur.STATUT_BROUILLON,
                    notes_reception=notes_recep,
                    created_by=admin,
                ),
            )

            if not created:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {reference}  (déjà existant)")
                )
                continue

            for designation, quantite, prix_unitaire, notes in lignes:
                try:
                    intrant = Intrant.objects.get(designation=designation)
                except Intrant.DoesNotExist:
                    raise CommandError(
                        f"Intrant introuvable : «{designation}». "
                        "Exécutez d'abord 'python manage.py seed_phase0'."
                    )
                BLFournisseurLigne.objects.create(
                    bl=bl,
                    intrant=intrant,
                    quantite=quantite,
                    prix_unitaire=prix_unitaire,
                    notes=notes,
                )

            # Transition explicite brouillon → reçu : les lignes existent déjà,
            # donc le signal post_save (bl_fournisseur_post_save) trouve les
            # lignes et crée correctement les entrées de stock (PMP, StockMouvement).
            bl.statut = BLFournisseur.STATUT_RECU
            bl.save()

            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {reference}  {fourn_nom:<30}  {len(lignes)} ligne(s) → statut Reçu"
                )
            )

        self.stdout.write(
            f"\n  BL Fournisseur : {created_count} créé(s) / {len(BLF_DATA)} total\n"
        )

    # ------------------------------------------------------------------
    # Facture Fournisseur
    # ------------------------------------------------------------------

    def _seed_factures(self, branche, admin):
        from achats.models import BLFournisseur, FactureFournisseur
        from intrants.models import Fournisseur

        created_count = 0
        for reference, fourn_nom, bl_refs, date_facture, date_echeance in FRN_DATA:
            try:
                fournisseur = Fournisseur.objects.get(nom=fourn_nom)
            except Fournisseur.DoesNotExist:
                raise CommandError(f"Fournisseur introuvable : «{fourn_nom}».")

            facture, created = FactureFournisseur.objects.get_or_create(
                reference=reference,
                defaults=dict(
                    branche=branche,
                    fournisseur=fournisseur,
                    date_facture=date_facture,
                    date_echeance=date_echeance,
                    type_facture=FactureFournisseur.TYPE_MARCHANDISES,
                    created_by=admin,
                ),
            )

            if not created:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {reference}  (déjà existant)")
                )
                continue

            bls = list(BLFournisseur.objects.filter(reference__in=bl_refs))
            if len(bls) != len(bl_refs):
                found = {b.reference for b in bls}
                missing = set(bl_refs) - found
                raise CommandError(
                    f"BL Fournisseur introuvable pour {reference} : {missing}. "
                    "Exécutez d'abord 'seed_achats_scenario --what bls'."
                )

            # Déclenche le signal m2m_changed (post_add) qui calcule
            # montant_total depuis les lignes BL et verrouille les BL (BR-FAF-01/03).
            facture.bls.set(bls)

            facture.refresh_from_db()
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {reference}  {fourn_nom:<30}  montant_total={facture.montant_total} DZD"
                )
            )

        self.stdout.write(
            f"\n  Facture Fournisseur : {created_count} créé(s) / {len(FRN_DATA)} total\n"
        )

    # ------------------------------------------------------------------
    # Règlement Fournisseur
    # ------------------------------------------------------------------

    def _seed_reglements(self, branche, admin):
        from achats.models import ReglementFournisseur
        from intrants.models import Fournisseur

        created_count = 0
        for fourn_nom, date_reglement, montant, mode_paiement, ref_paiement in REG_DATA:
            try:
                fournisseur = Fournisseur.objects.get(nom=fourn_nom)
            except Fournisseur.DoesNotExist:
                raise CommandError(f"Fournisseur introuvable : «{fourn_nom}».")

            existing = ReglementFournisseur.objects.filter(
                fournisseur=fournisseur,
                date_reglement=date_reglement,
                montant=montant,
            ).first()
            if existing:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Règlement {fourn_nom} — {montant} DZD ({date_reglement})  (déjà existant)"
                    )
                )
                continue

            # La création déclenche automatiquement le signal post_save qui
            # exécute achats.utils.appliquer_reglement_fifo (allocation FIFO
            # sur les factures impayées de ce fournisseur, dans cette branche).
            ReglementFournisseur.objects.create(
                fournisseur=fournisseur,
                branche=branche,
                date_reglement=date_reglement,
                montant=montant,
                mode_paiement=mode_paiement,
                reference_paiement=ref_paiement,
                created_by=admin,
            )
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Règlement {fourn_nom:<30}  {montant} DZD ({date_reglement}) → FIFO appliqué"
                )
            )

        self.stdout.write(
            f"\n  Règlement Fournisseur : {created_count} créé(s) / {len(REG_DATA)} total\n"
        )
