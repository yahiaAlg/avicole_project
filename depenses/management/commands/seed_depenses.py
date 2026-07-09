"""
management/commands/seed_depenses.py

Peuplement rapide de la Phase 7 du scénario fresh-start — Dépenses
d'exploitation : DEP-002 (Énergie) / DEP-003 (Vétérinaire) / DEP-004
(Transport), pour éviter la saisie manuelle via DEPENSES → Nouvelle dépense.

⚠️ DEP-001 (Salaires) n'est PAS créée ici : dans le scénario de référence
(scenario_avicole_full_cycle_fresh_start_en.md §9.2/§9.2bis), la ligne
Salaires n'est plus une Dépense unique mais est produite par le cycle
RH & Paie (Employee → TimeSheet → EmployeeAdvance → Payslip), qui fait
l'objet d'un script dédié séparé (seed_rh_paie, non couvert ici).

Utilisation :
    # Peuplement complet (les 3 dépenses du scénario)
    python manage.py seed_depenses

    # Cibler une branche précise (code Branche) — défaut STF
    python manage.py seed_depenses --branche STF

    # Rattacher les dépenses à un autre lot que celui par défaut
    python manage.py seed_depenses --lot "Lot Mai 2026 — Bâtiment A"

    # Supprimer puis recréer
    python manage.py seed_depenses --clear

⚠️ Séquence recommandée :
    1. python manage.py seed_db_minimal      ← Branche STF + CategorieDepense (8)
    2. python manage.py seed_buildings       ← Bâtiments
    3. python manage.py seed_achats_scenario ← BLF/FRN/REG
    4. python manage.py seed_elevage_lot     ← Lot d'élevage (mortalités, aliments…)
    5. python manage.py seed_depenses        ← ce script (DEP-002/003/004)

Détails (scenario §9.2) :
    DEP-002 — Sonelgaz Électricité   : 2026-06-30, 18 000.00 DZD, virement
    DEP-003 — Honoraires vétérinaire : 2026-06-05, 12 000.00 DZD, espèces
    DEP-004 — Transport livraison    : 2026-06-20,  8 500.00 DZD, espèces

Idempotent : get_or_create sur la combinaison (description, date, montant),
la Dépense n'ayant pas de champ `reference` unique dans le modèle.

Toutes les dépenses sont rattachées au lot par défaut (--lot) pour
l'attribution analytique optionnelle (BR-DEP-04) et à la branche ciblée
(BR-BRA-01, champ obligatoire).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"
DEFAULT_LOT_DESIGNATION = "Lot Mai 2026 — Bâtiment A"

# Format : (categorie_code, date, description, montant, mode_paiement,
#           beneficiaire, reference_document, notes)
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
    ),
]


class Command(BaseCommand):
    help = (
        "Phase 7 du scénario fresh-start : Dépenses d'exploitation "
        "DEP-002/003/004 (scenario_avicole_full_cycle_fresh_start_en.md §9.2). "
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
                f"Désignation exacte du lot d'attribution analytique "
                f"(défaut: «{DEFAULT_LOT_DESIGNATION}»). Passez une chaîne "
                "vide '' pour ne rattacher les dépenses à aucun lot."
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

        lot = None
        if lot_designation:
            from elevage.models import LotElevage

            try:
                lot = LotElevage.objects.get(designation=lot_designation)
            except LotElevage.DoesNotExist:
                if lot_designation == DEFAULT_LOT_DESIGNATION:
                    # Le lot par défaut du scénario n'existe pas encore —
                    # on délègue sa création à seed_elevage_lot (--what none
                    # = résout/crée le lot sans peupler mortalités/aliments…),
                    # plutôt que d'échouer immédiatement.
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Lot «{lot_designation}» introuvable — "
                            "création automatique via seed_elevage_lot…"
                        )
                    )
                    from django.core.management import call_command

                    call_command("seed_elevage_lot", lot=lot_designation, what="none")
                    lot = LotElevage.objects.get(designation=lot_designation)
                else:
                    raise CommandError(
                        f"Lot introuvable : «{lot_designation}». "
                        "Exécutez d'abord 'python manage.py seed_elevage_lot', "
                        "ou passez --lot '' pour ne rattacher aucun lot."
                    )

        admin = User.objects.filter(is_superuser=True).first()

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n=== seed_depenses — Branche : «{branche}» ===\n"
            )
        )

        if options["clear"]:
            self._clear()

        self._seed(branche, lot, admin)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_depenses terminé.\n"))

    # ------------------------------------------------------------------

    def _clear(self):
        from depenses.models import Depense

        self.stdout.write(self.style.WARNING("  Suppression des dépenses du scénario…"))
        descriptions = [row[2] for row in DEPENSE_DATA]
        count, _ = Depense.objects.filter(description__in=descriptions).delete()
        self.stdout.write(f"  {count} supprimée(s).\n")

    def _seed(self, branche, lot, admin):
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
        ) in DEPENSE_DATA:
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
