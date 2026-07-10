"""
management/commands/seed_depenses.py

Peuplement rapide de la Phase 7 du scénario fresh-start — Dépenses
d'exploitation : DEP-002 (Énergie) / DEP-003 (Vétérinaire) / DEP-004
(Transport) / DEP-005..008 (Maintenance / Transport / Fournitures,
rattachées aux 2 lots — broiler ET pondeuses), pour éviter la saisie
manuelle via DEPENSES → Nouvelle dépense.

⚠️ DEP-001 (Salaires) n'est PAS créée ici : dans le scénario de référence
(scenario_avicole_full_cycle_fresh_start_en.md §9.2/§9.2bis), la ligne
Salaires n'est plus une Dépense unique mais est produite par le cycle
RH & Paie (Employee → TimeSheet → EmployeeAdvance → Payslip), qui fait
l'objet d'un script dédié séparé (seed_rh_paie, non couvert ici).

Utilisation :
    # Peuplement complet (les 8 dépenses du scénario)
    python manage.py seed_depenses

    # Cibler une branche précise (code Branche) — défaut STF
    python manage.py seed_depenses --branche STF

    # Rattacher les dépenses "broiler" à un autre lot que celui par défaut
    python manage.py seed_depenses --lot "Lot Mai 2026 — Bâtiment A"

    # Idem pour les dépenses "pondeuses" (DEP-006/007)
    python manage.py seed_depenses --lot-pondeuses "Lot Pondeuses 2025"

    # Supprimer puis recréer
    python manage.py seed_depenses --clear

⚠️ Séquence recommandée :
    1. python manage.py seed_db_minimal      ← Branche STF + CategorieDepense (8)
    2. python manage.py seed_buildings       ← Bâtiments
    3. python manage.py seed_achats_scenario ← BLF/FRN/REG
    4. python manage.py seed_elevage_lot     ← Lot d'élevage (mortalités, aliments…)
    5. python manage.py seed_depenses        ← ce script (DEP-002/003/004)

Détails (scenario §9.2) :
    DEP-002 — Sonelgaz Électricité       : 2026-06-30, 18 000.00 DZD, virement    (lot broiler)
    DEP-003 — Honoraires vétérinaire     : 2026-06-05, 12 000.00 DZD, espèces     (lot broiler)
    DEP-004 — Transport livraison        : 2026-06-20,  8 500.00 DZD, espèces     (lot broiler)
    DEP-005 — Maintenance ventilation    : 2026-06-12, 15 000.00 DZD, espèces     (lot broiler)
    DEP-006 — Fournitures nettoyage      : 2026-06-01,  4 200.00 DZD, espèces     (lot broiler)
    DEP-007 — Maintenance mangeoires     : 2026-06-25,  9 500.00 DZD, espèces     (lot pondeuses)
    DEP-008 — Transport livraison œufs   : 2026-06-18,  6 000.00 DZD, espèces     (lot pondeuses)

⚠️ DEP-005..008 sont nouvelles (Maintenance / Fournitures, en plus de
l'Énergie/Vétérinaire/Transport déjà couverts) et sont réparties sur les
2 lots du scénario — le lot broiler (--lot) ET le lot pondeuses
(--lot-pondeuses) — pour illustrer BR-DEP-04 (attribution analytique
multi-lot) sur autre chose que le seul lot broiler.

Idempotent : get_or_create sur la combinaison (description, date, montant),
la Dépense n'ayant pas de champ `reference` unique dans le modèle.

Chaque dépense est rattachée à son lot (broiler ou pondeuses, cf. tableau
ci-dessus) pour l'attribution analytique optionnelle (BR-DEP-04) et à la
branche ciblée (BR-BRA-01, champ obligatoire).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"
DEFAULT_LOT_DESIGNATION = "Lot Mai 2026 — Bâtiment A"
# Doit matcher seed_elevage_lot.DEFAULT_LOT_PONDEUSES_DESIGNATION pour que la
# création automatique (call_command("seed_elevage_lot", ...)) fonctionne.
DEFAULT_LOT_PONDEUSES_DESIGNATION = "Lot Pondeuses 2025"

LOT_BROILER = "broiler"
LOT_PONDEUSES = "pondeuses"

# Format : (categorie_code, date, description, montant, mode_paiement,
#           beneficiaire, reference_document, notes, lot_ref)
# lot_ref ∈ {LOT_BROILER, LOT_PONDEUSES, None} — désigne quel lot résolu
# (--lot / --lot-pondeuses) est attribué à la dépense (BR-DEP-04).
DEPENSE_DATA = [
    (
        "ENERGIE",
        datetime.date(2026, 6, 30),
        "Facture électricité juin 2026 — ventilation + éclairage Bâtiment A",
        Decimal("18000.00"),
        "virement",
        "Sonelgaz",
        "SONELGAZ-2026-06-8854",
        "",
        LOT_BROILER,
    ),
    (
        "VETERINAIRE",
        datetime.date(2026, 6, 5),
        "Visite sanitaire + diagnostic coccidiose — Dr. Ammar Bouzid",
        Decimal("12000.00"),
        "especes",
        "Dr. Ammar Bouzid",
        "",
        "Prescription + protocole Amoxicilline 250g",
        LOT_BROILER,
    ),
    (
        "TRANSPORT",
        datetime.date(2026, 6, 20),
        "Transport abattage + livraisons clients — 20 & 21 juin",
        Decimal("8500.00"),
        "especes",
        "",
        "",
        "",
        LOT_BROILER,
    ),
    (
        "MAINTENANCE",
        datetime.date(2026, 6, 12),
        "Réparation système de ventilation — Bâtiment A",
        Decimal("15000.00"),
        "especes",
        "Atelier Frères Khelifi",
        "",
        "Remplacement 2 extracteurs + courroies",
        LOT_BROILER,
    ),
    (
        "FOURNITURES",
        datetime.date(2026, 6, 1),
        "Achat produits de nettoyage et désinfection — Bâtiment A",
        Decimal("4200.00"),
        "especes",
        "",
        "",
        "Vide sanitaire avant mise en place du lot",
        LOT_BROILER,
    ),
    (
        "MAINTENANCE",
        datetime.date(2026, 6, 25),
        "Entretien mangeoires et abreuvoirs automatiques — Bâtiment C",
        Decimal("9500.00"),
        "especes",
        "Atelier Frères Khelifi",
        "",
        "",
        LOT_PONDEUSES,
    ),
    (
        "TRANSPORT",
        datetime.date(2026, 6, 18),
        "Transport livraison œufs — tournée clients détail",
        Decimal("6000.00"),
        "especes",
        "",
        "",
        "",
        LOT_PONDEUSES,
    ),
]


class Command(BaseCommand):
    help = (
        "Phase 7 du scénario fresh-start : Dépenses d'exploitation "
        "DEP-002..008 (scenario_avicole_full_cycle_fresh_start_en.md §9.2), "
        "réparties sur le lot broiler ET le lot pondeuses. "
        "Salaires (DEP-001) exclu — produit par le module RH & Paie."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--branche",
            type=str,
            default=DEFAULT_BRANCHE_CODE,
            help=f"Code de la branche cible (défaut : «{DEFAULT_BRANCHE_CODE}»).",
        )
        parser.add_argument(
            "--lot",
            type=str,
            default=DEFAULT_LOT_DESIGNATION,
            metavar="DESIGNATION",
            help=(
                f"Désignation exacte du lot BROILER d'attribution analytique, "
                f"pour DEP-002/003/004/005/006 (défaut: «{DEFAULT_LOT_DESIGNATION}»). "
                "Passez une chaîne vide '' pour ne rattacher ces dépenses à aucun lot."
            ),
        )
        parser.add_argument(
            "--lot-pondeuses",
            type=str,
            default=DEFAULT_LOT_PONDEUSES_DESIGNATION,
            metavar="DESIGNATION",
            dest="lot_pondeuses",
            help=(
                f"Désignation exacte du lot pondeuses d'attribution analytique "
                f"pour DEP-007/008 (défaut: «{DEFAULT_LOT_PONDEUSES_DESIGNATION}»). "
                "Passez une chaîne vide '' pour ne rattacher ces dépenses à aucun lot."
            ),
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Supprime les dépenses du scénario avant de les recréer.",
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        branche_code = options["branche"].strip().upper()
        lot_designation = options["lot"]

        from core.models import Branche

        try:
            branche = Branche.objects.get(code=branche_code)
        except Branche.DoesNotExist:
            raise CommandError(
                f"Branche introuvable : «{branche_code}». "
                "Exécutez d'abord 'python manage.py seed_db_minimal'."
            )

        lot_pondeuses_designation = options["lot_pondeuses"]

        lot_broiler = self._resolve_lot(lot_designation, DEFAULT_LOT_DESIGNATION)
        lot_pondeuses = self._resolve_lot(
            lot_pondeuses_designation,
            DEFAULT_LOT_PONDEUSES_DESIGNATION,
            lot_pondeuses_arg=lot_pondeuses_designation,
        )

        lots_by_ref = {LOT_BROILER: lot_broiler, LOT_PONDEUSES: lot_pondeuses}

        admin = User.objects.filter(is_superuser=True).first()

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n=== seed_depenses — Branche : «{branche}» ===\n"
            )
        )

        if options["clear"]:
            self._clear()

        self._seed(branche, lots_by_ref, admin)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_depenses terminé.\n"))

    # ------------------------------------------------------------------

    def _resolve_lot(self, designation, default_designation, lot_pondeuses_arg=None):
        """
        Résout un LotElevage par sa désignation exacte, avec création
        automatique (délégation à seed_elevage_lot --what none) UNIQUEMENT
        si la désignation demandée est celle du scénario par défaut —
        sinon échec explicite (le lot doit déjà exister).
        """
        if not designation:
            return None

        from elevage.models import LotElevage

        try:
            return LotElevage.objects.get(designation=designation)
        except LotElevage.DoesNotExist:
            if designation != default_designation:
                raise CommandError(
                    f"Lot introuvable : «{designation}». "
                    "Exécutez d'abord 'python manage.py seed_elevage_lot', "
                    "ou passez --lot / --lot-pondeuses '' pour ne rattacher aucun lot."
                )

            self.stdout.write(
                self.style.WARNING(
                    f"  Lot «{designation}» introuvable — "
                    "création automatique via seed_elevage_lot…"
                )
            )
            from django.core.management import call_command

            # ⚠️ seed_elevage_lot ne crée le lot PONDEUSES qu'à l'intérieur
            # des sections --what oeufs/pondeuse-elevage/pondeuse-transfert/all
            # (via _resolve_lot_pondeuses) — jamais avec --what none, qui ne
            # résout que le lot BROILER (_ensure_lot_broiler, toujours
            # exécuté). 'pondeuse-elevage' est donc utilisé ici pour le lot
            # pondeuses ; c'est idempotent (get_or_create par ligne) et
            # équivalent à ce qu'un run complet aurait déjà fait.
            if lot_pondeuses_arg is not None:
                call_command(
                    "seed_elevage_lot",
                    lot_pondeuses=designation,
                    what="pondeuse-elevage",
                )
            else:
                call_command("seed_elevage_lot", lot=designation, what="none")
            return LotElevage.objects.get(designation=designation)

    def _clear(self):
        from depenses.models import Depense

        self.stdout.write(self.style.WARNING("  Suppression des dépenses du scénario…"))
        descriptions = [row[2] for row in DEPENSE_DATA]
        count, _ = Depense.objects.filter(description__in=descriptions).delete()
        self.stdout.write(f"  {count} supprimée(s).\n")

    def _seed(self, branche, lots_by_ref, admin):
        from depenses.models import CategorieDepense, Depense

        created_count = 0
        for (
            categorie_code,
            date,
            description,
            montant,
            mode_paiement,
            beneficiaire,
            reference_document,
            notes,
            lot_ref,
        ) in DEPENSE_DATA:
            lot = lots_by_ref.get(lot_ref)
            try:
                categorie = CategorieDepense.objects.get(code=categorie_code)
            except CategorieDepense.DoesNotExist:
                raise CommandError(
                    f"CategorieDepense «{categorie_code}» introuvable — "
                    "exécutez d'abord 'python manage.py seed_db_minimal'."
                )

            existing = Depense.objects.filter(
                description=description, date=date, montant=montant
            ).first()
            if existing:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {description[:50]:<50}  (déjà existante)")
                )
                continue

            Depense.objects.create(
                branche=branche,
                categorie=categorie,
                date=date,
                description=description,
                montant=montant,
                mode_paiement=mode_paiement,
                beneficiaire=beneficiaire,
                reference_document=reference_document,
                lot=lot,
                notes=notes,
                created_by=admin,
            )
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {categorie_code:<12} {montant:>12} DZD  {description[:45]}"
                )
            )

        self.stdout.write(
            f"\n  Dépense : {created_count} créée(s) / {len(DEPENSE_DATA)} total\n"
        )
