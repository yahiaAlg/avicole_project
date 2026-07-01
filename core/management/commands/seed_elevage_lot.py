"""
management/commands/seed_elevage_lot.py

Commande de peuplement rapide des données d'élevage pour un lot existant.
Utile pendant la démo du cycle ERP complet pour éviter la saisie manuelle
de toutes les mortalités, consommations d'aliments et de médicaments.

Utilisation :
    # Peuplement complet (mortalités + aliments + médicaments)
    python manage.py seed_elevage_lot --lot "Lot Mars 2025 — Bâtiment A"

    # Seulement les mortalités
    python manage.py seed_elevage_lot --lot "Lot Mars 2025 — Bâtiment A" --what mortalites

    # Seulement les consommations aliments
    python manage.py seed_elevage_lot --lot "Lot Mars 2025 — Bâtiment A" --what aliments

    # Seulement les consommations médicaments
    python manage.py seed_elevage_lot --lot "Lot Mars 2025 — Bâtiment A" --what medics

    # Utiliser le lot par défaut (celui ouvert dans Bâtiment A par seed_db.py)
    python manage.py seed_elevage_lot

Notes :
    - La commande est idempotente (get_or_create partout).
    - Le lot doit déjà exister avec le statut OUVERT (créé par `seed_db`, qui
      seed "Lot Mars 2025 — Bâtiment A" avec une date_ouverture RELATIVE —
      date.today() - 40 jours — et non une date calendaire fixe).
    - Les intrants référencés doivent exister dans la base (créés via l'interface
      après avoir exécuté seed_db_minimal pour les catégories).
    - Les dates ci-dessous sont exprimées en JOURS ÉCOULÉS DEPUIS
      lot.date_ouverture (et non en dates calendaires absolues), pour rester
      cohérentes quel que soit le jour d'exécution de `seed_db`.
      --date-offset ajoute un décalage supplémentaire (en jours) si besoin.

Données incluses (scénario Lot Mars 2025 — Bâtiment A, 4 000 poussins Cobb 500) :
    Mortalités   : 5 événements, 40 oiseaux au total
    Aliments     : 11 saisies (démarrage / croissance / finition)
    Médicaments  : 7 saisies (vaccins + amoxicilline + vitamines)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# ---------------------------------------------------------------------------
# Données du scénario — Lot Mars 2025 — Bâtiment A
# Toutes les dates sont exprimées en jours écoulés depuis lot.date_ouverture
# (J0 = date d'ouverture) pour rester valides quelle que soit la date réelle
# à laquelle seed_db a été exécuté. Modifiez ces constantes si vous utilisez
# un autre scénario.
# ---------------------------------------------------------------------------

DEFAULT_LOT_DESIGNATION = "Lot Mars 2025 — Bâtiment A"

# Format : (jour_depuis_ouverture, nombre, cause)
MORTALITE_DATA = [
    (1, 5, "Stress transport / déshydratation"),
    (6, 10, "Infection respiratoire précoce"),
    (12, 12, "Aspergillose suspectée"),
    (20, 8, "Coccidiose — traitement lancé"),
    (33, 5, "Cause indéterminée"),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
# Désignations EXACTES telles qu'elles existent dans la table Intrant.
ALIMENT_DATA = [
    # Démarrage J0→J14
    (0, "علف البداية — الطور الأول (0–14 يوم)", Decimal("25.000")),
    (2, "علف البداية — الطور الأول (0–14 يوم)", Decimal("25.000")),
    (5, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    (9, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    (12, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    # Croissance J15→J28
    (13, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    (20, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    (27, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    # Finition J29→J40
    (27, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
    (32, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
    (37, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
MEDIC_DATA = [
    (1, "فيتامينات + إلكتروليتات (مركّب)", Decimal("2.000")),
    (6, "أموكسيسيلين 50% مسحوق", Decimal("250.000")),
    (6, "فيتامينات + إلكتروليتات (مركّب)", Decimal("3.000")),
    (12, "لقاح نيوكاسل (هيتشنر B1)", Decimal("2000.000")),
    (20, "لقاح غامبورو (IBD متوسط)", Decimal("1965.000")),
    (20, "أموكسيسيلين 50% مسحوق", Decimal("250.000")),
    (20, "فيتامينات + إلكتروليتات (مركّب)", Decimal("5.000")),
]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Peuplement rapide des mortalités, consommations d'aliments et de médicaments "
        "pour un lot d'élevage existant (scénario Lot Mai 2026 par défaut)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--lot",
            type=str,
            default=DEFAULT_LOT_DESIGNATION,
            help=(
                f"Désignation exacte du lot (défaut: «{DEFAULT_LOT_DESIGNATION}»). "
                "Le lot doit exister avec statut ouvert."
            ),
        )
        parser.add_argument(
            "--what",
            choices=["all", "mortalites", "aliments", "medics"],
            default="all",
            help=(
                "Sous-ensemble à peupler : "
                "'all' (défaut) / 'mortalites' / 'aliments' / 'medics'."
            ),
        )
        parser.add_argument(
            "--date-offset",
            type=int,
            default=0,
            metavar="JOURS",
            dest="date_offset",
            help=(
                "Décalage en jours ajouté à toutes les dates du scénario. "
                "Utile si votre lot a démarré à une date différente. "
                "Exemple : --date-offset 30 décale toutes les dates de +30 jours."
            ),
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        lot_designation = options["lot"]
        what = options["what"]
        offset = timedelta(days=options["date_offset"])

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n=== seed_elevage_lot — Lot : «{lot_designation}» ===\n"
                f"    Sous-ensemble : {what}   |   Décalage date : {options['date_offset']}j\n"
            )
        )

        # ── Resolve lot ───────────────────────────────────────────────────
        try:
            from elevage.models import LotElevage
        except ImportError as exc:
            raise CommandError(f"Impossible d'importer elevage.models : {exc}") from exc

        try:
            lot = LotElevage.objects.get(designation=lot_designation)
        except LotElevage.DoesNotExist:
            raise CommandError(
                f"Lot introuvable : «{lot_designation}».\n"
                "Créez le lot via l'interface (ÉLEVAGE → Lots → Ouvrir un nouveau lot) "
                "avant d'exécuter cette commande."
            )

        if lot.statut != LotElevage.STATUT_OUVERT:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠  Le lot est au statut «{lot.statut}» (attendu: ouvert). "
                    "Les mortalités et consommations ne pourront pas être ajoutées "
                    "sur un lot fermé (BR-LOT-03). Continuer quand même…"
                )
            )

        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            raise CommandError(
                "Aucun super-utilisateur trouvé. "
                "Exécutez seed_db_minimal en premier."
            )

        # ── Seed sections ─────────────────────────────────────────────────
        if what in ("all", "mortalites"):
            self._seed_mortalites(lot, offset)

        if what in ("all", "aliments"):
            self._seed_consommations(
                lot, ALIMENT_DATA, "Aliments", admin, offset, "ALIMENT"
            )

        if what in ("all", "medics"):
            self._seed_consommations(
                lot, MEDIC_DATA, "Médicaments", admin, offset, "MEDICAMENT"
            )

        self.stdout.write(self.style.SUCCESS("\n✓ seed_elevage_lot terminé.\n"))

    # ------------------------------------------------------------------
    # Mortalités
    # ------------------------------------------------------------------

    def _seed_mortalites(self, lot, offset: timedelta):
        from elevage.models import Mortalite

        created_count = 0
        for jour, nombre, cause in MORTALITE_DATA:
            dt = lot.date_ouverture + timedelta(days=jour) + offset
            _, created = Mortalite.objects.get_or_create(
                lot=lot,
                date=dt,
                nombre=nombre,
                defaults={"cause": cause},
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Mortalité {dt}  {nombre} oiseaux  {cause or '—'}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Mortalité {dt}  {nombre} oiseaux  (déjà existante)"
                    )
                )

        self._log_summary("Mortalités", created_count, len(MORTALITE_DATA))

    # ------------------------------------------------------------------
    # Consommations (aliments ou médicaments)
    # ------------------------------------------------------------------

    def _seed_consommations(
        self, lot, data, label: str, admin, offset: timedelta, categorie_code: str
    ):
        from elevage.models import Consommation
        from intrants.models import Intrant

        # Pre-resolve intrants by designation + category to give clear errors early
        # and guard against MultipleObjectsReturned when designations are reused
        # across different categories (the Intrant.designation field is not unique).
        intrant_cache: dict[str, object] = {}
        missing: list[str] = []
        for _jour, designation, _quantite in data:
            if designation not in intrant_cache:
                try:
                    intrant_cache[designation] = Intrant.objects.get(
                        designation=designation,
                        categorie__code=categorie_code,
                    )
                except Intrant.DoesNotExist:
                    if designation not in missing:
                        missing.append(designation)

        if missing:
            self.stdout.write(
                self.style.ERROR(
                    f"\n  ✗ Intrants introuvables pour {label} "
                    f"(catégorie {categorie_code} — vérifiez la désignation exacte dans la base) :\n"
                    + "\n".join(f"      • {d}" for d in missing)
                )
            )
            raise CommandError(
                f"{len(missing)} intrant(s) manquant(s) — "
                "créez-les via l'interface (STOCK → Intrants → Nouvel intrant) "
                f"avec la catégorie {categorie_code}."
            )

        created_count = 0
        for jour, designation, quantite in data:
            dt = lot.date_ouverture + timedelta(days=jour) + offset
            intrant = intrant_cache[designation]
            _, created = Consommation.objects.get_or_create(
                lot=lot,
                date=dt,
                intrant=intrant,
                defaults={"quantite": quantite, "created_by": admin},
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Conso {dt}  {intrant.designation[:40]:<40}  "
                        f"{quantite} {intrant.unite_mesure}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Conso {dt}  {intrant.designation[:40]:<40}  (déjà existante)"
                    )
                )

        self._log_summary(label, created_count, len(data))

    # ------------------------------------------------------------------

    def _log_summary(self, label: str, created: int, total: int):
        self.stdout.write(
            f"\n  {'✓' if created > 0 else '~'} {label} : "
            f"{created} créé(s) / {total} total\n"
        )
