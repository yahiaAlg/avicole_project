"""
management/commands/seed_buildings.py

Peuplement rapide des bâtiments (Batiment) du scénario fresh-start, pour
éviter la saisie manuelle via STOCK → Bâtiments → Nouveau bâtiment.

⚠️ Contrairement à Fournisseur / Client / Intrant (Phase 0, catalogue
global), les Bâtiments sont rattachés à une Branche (BR-BRA-01) — c'est
pour cela qu'ils ne sont PAS inclus dans `seed_phase0` et sont peuplés
séparément ici, une fois la branche connue.

Utilisation :
    # Peuplement complet (les 4 bâtiments du scénario), branche par défaut STF
    python manage.py seed_buildings

    # Cibler une branche précise (code Branche)
    python manage.py seed_buildings --branche STF

    # Un seul bâtiment
    python manage.py seed_buildings --only "Bâtiment A"

    # Supprimer puis recréer (échoue si des lots/mouvements y sont rattachés)
    python manage.py seed_buildings --clear

⚠️ Séquence recommandée :
    1. python manage.py seed_db_minimal   ← crée la Branche par défaut (STF)
    2. python manage.py seed_phase0       ← Fournisseurs / Clients / Intrants
    3. python manage.py seed_buildings    ← ce script (Bâtiments)
    4. Lancer Phase 1 — Achats Intrants (BLF-2026-0001 …)

Bâtiments créés (scenario_avicole_full_cycle_fresh_start.md §0.3) :
    BAT-1 — Bâtiment A     : poussiniere, capacité 5 000  ← requis pour le lot
    BAT-2 — Bâtiment B     : poulailler,  capacité 4 000  (optionnel)
    BAT-3 — Bâtiment C     : poulailler,  capacité 6 000  (optionnel)
    BAT-4 — Dépôt Aliments : entrepot                       (optionnel)

Idempotent : utilise get_or_create sur (nom, branche).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"

# Format : (nom, type_batiment, capacite, categorie_stockage, description)
BATIMENT_DATA = [
    (
        "Bâtiment A",
        "poussiniere",
        5000,
        "",
        "الحظيرة الرئيسية — تهوية ميكانيكية",
    ),
    (
        "Bâtiment B",
        "poulailler",
        4000,
        "",
        "الحظيرة الثانوية — تهوية طبيعية",
    ),
    (
        "Bâtiment C",
        "poulailler",
        6000,
        "",
        "حظيرة جديدة — عزل مُحسَّن",
    ),
    (
        "Dépôt Aliments",
        "entrepot",
        None,
        "",
        "مستودع تخزين الأعلاف والمدخلات",
    ),
]


class Command(BaseCommand):
    help = (
        "Peuplement rapide des bâtiments (Batiment) du scénario fresh-start "
        "pour une branche donnée (défaut : branche «STF» créée par seed_db_minimal)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--branche",
            type=str,
            default=DEFAULT_BRANCHE_CODE,
            help=(
                f"Code de la branche cible (défaut: «{DEFAULT_BRANCHE_CODE}», "
                "créée par seed_db_minimal). La branche doit déjà exister."
            ),
        )
        parser.add_argument(
            "--only",
            type=str,
            default=None,
            metavar="NOM",
            help=(
                "Ne créer qu'un seul bâtiment, désigné par son nom exact "
                "(ex : «Bâtiment A»). Par défaut, les 4 bâtiments sont créés."
            ),
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Supprime d'abord les bâtiments de cette branche portant les "
                "noms du scénario, avant de les recréer. Échouera si des lots "
                "ou mouvements y sont déjà rattachés (PROTECT)."
            ),
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        branche_code = options["branche"].strip().upper()
        only = options["only"]

        try:
            from core.models import Branche
        except ImportError as exc:
            raise CommandError(f"Impossible d'importer core.models : {exc}") from exc

        try:
            branche = Branche.objects.get(code=branche_code)
        except Branche.DoesNotExist:
            raise CommandError(
                f"Branche introuvable : «{branche_code}».\n"
                "Exécutez d'abord 'python manage.py seed_db_minimal' "
                "(qui crée la branche par défaut «STF»), ou créez la branche "
                "via CORE → Branches → Nouveau, ou passez --branche <CODE>."
            )

        data = BATIMENT_DATA
        if only:
            data = [row for row in BATIMENT_DATA if row[0] == only]
            if not data:
                noms = ", ".join(f"«{row[0]}»" for row in BATIMENT_DATA)
                raise CommandError(
                    f"Bâtiment «{only}» introuvable dans le scénario. "
                    f"Noms disponibles : {noms}."
                )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\n=== seed_buildings — Branche : «{branche}» ===\n"
            )
        )

        if options["clear"]:
            self._clear(branche, data)

        self._seed(branche, data)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_buildings terminé.\n"))

    # ------------------------------------------------------------------

    def _clear(self, branche, data):
        from intrants.models import Batiment

        noms = [row[0] for row in data]
        self.stdout.write(
            self.style.WARNING(
                f"  Suppression des bâtiments existants ({', '.join(noms)}) "
                f"pour la branche «{branche}»…"
            )
        )
        qs = Batiment.objects.filter(branche=branche, nom__in=noms)
        count = qs.count()
        try:
            qs.delete()
        except Exception as exc:  # PROTECT FKs from lots/mouvements, etc.
            raise CommandError(
                f"Impossible de supprimer certains bâtiments — probablement "
                f"référencés par des lots ou mouvements existants : {exc}"
            ) from exc
        self.stdout.write(f"  {count} supprimé(s).\n")

    def _seed(self, branche, data):
        from intrants.models import Batiment

        created_count = 0
        for nom, type_batiment, capacite, categorie_stockage, description in data:
            obj, created = Batiment.objects.get_or_create(
                nom=nom,
                branche=branche,
                defaults=dict(
                    type_batiment=type_batiment,
                    capacite=capacite,
                    categorie_stockage=categorie_stockage,
                    description=description,
                    actif=True,
                ),
            )
            if created:
                created_count += 1
                cap_str = f"{capacite} têtes" if capacite else "—"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {nom:<16}  {type_batiment:<12}  cap. {cap_str}"
                    )
                )
            else:
                self.stdout.write(self.style.WARNING(f"  ~ {nom:<16}  (déjà existant)"))

        self.stdout.write(
            f"\n  {'✓' if created_count > 0 else '~'} Bâtiments : "
            f"{created_count} créé(s) / {len(data)} total\n"
        )
