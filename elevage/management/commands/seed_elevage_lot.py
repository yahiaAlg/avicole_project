"""
management/commands/seed_elevage_lot.py

Commande de peuplement rapide des données d'élevage pour un lot existant.
Utile pendant la démo du cycle ERP complet pour éviter la saisie manuelle
de toutes les mortalités, consommations d'aliments et de médicaments.

Utilisation :
    # Peuplement complet (mortalités + aliments + médicaments + fertilisant
    # + œufs si le lot pondeuses existe déjà)
    python manage.py seed_elevage_lot --lot "Lot Mai 2026 — Bâtiment A"

    # Seulement les mortalités
    python manage.py seed_elevage_lot --lot "Lot Mai 2026 — Bâtiment A" --what mortalites

    # Seulement les consommations aliments
    python manage.py seed_elevage_lot --lot "Lot Mai 2026 — Bâtiment A" --what aliments

    # Seulement les consommations médicaments
    python manage.py seed_elevage_lot --lot "Lot Mai 2026 — Bâtiment A" --what medics

    # Seulement la collecte + traitement du fertilisant (bâtiment du lot broiler)
    python manage.py seed_elevage_lot --lot "Lot Mai 2026 — Bâtiment A" --what fertilisant

    # Cycle Lot Pondeuses (séparé, cf. §5.6 du scénario) — dans l'ordre :
    #   1) Mortalités + Aliments + Médicaments + Pesées, phase Poussinière
    python manage.py seed_elevage_lot --what pondeuse-elevage
    #   2) TransfertLot Poussinière (Bât. C) → Poulailler (Bât. B) à J+126
    python manage.py seed_elevage_lot --what pondeuse-transfert
    #   3) Aliment Ponte + récolte d'œufs (post-transfert uniquement)
    python manage.py seed_elevage_lot --what oeufs
    python manage.py seed_elevage_lot --what oeufs --lot-pondeuses "Lot Pondeuses 2026"

    # Utiliser le lot par défaut (celui ouvert dans Bâtiment A par seed_db.py)
    python manage.py seed_elevage_lot

Notes :
    - La commande est idempotente (get_or_create partout).
    - Le lot doit déjà exister avec le statut OUVERT (créé par `seed_db`, qui
      seed "Lot Mai 2026 — Bâtiment A" avec une date_ouverture RELATIVE —
      date.today() - 40 jours — et non une date calendaire fixe).
    - Les intrants référencés doivent exister dans la base (créés via l'interface
      après avoir exécuté seed_db_minimal pour les catégories).
    - Les dates ci-dessous sont exprimées en JOURS ÉCOULÉS DEPUIS
      lot.date_ouverture (et non en dates calendaires absolues), pour rester
      cohérentes quel que soit le jour d'exécution de `seed_db`.
      --date-offset ajoute un décalage supplémentaire (en jours) si besoin.
    - Le fertilisant (CollecteFertilisant/TraitementFertilisant) est rattaché
      au BÂTIMENT du lot broiler (Bâtiment A) et non au lot lui-même — la
      litière est un sous-produit du bâtiment, pas d'un lot en particulier
      (cf. modèle CollecteFertilisant). Aucun lot pondeuses n'est requis
      pour cette partie.
    - Le Lot Pondeuses (RecolteOeufs, TransfertLot, PeseeEchantillon) est
      un lot SÉPARÉ, ouvert manuellement comme poussines d'un jour dans une
      Poussinière (Bâtiment C) — le lot broiler Ross 308 par défaut n'a
      jamais de phase de ponte. Ce lot doit être créé au préalable via
      l'interface (le script ne le crée pas). Le cycle pondeuses se peuple
      en 3 étapes ordonnées (voir Usage ci-dessus) : élevage → transfert →
      ponte. `--what oeufs` (comme `--what pondeuse-elevage`) échoue si le
      lot pondeuses n'existe pas encore quand demandé explicitement, et
      est simplement ignoré avec un avertissement dans `--what all`.
    - `--what pondeuse-transfert` crée le TransfertLot (MODE_FULL) qui met
      à jour lot.batiment vers le Poulailler — il ne peut être exécuté
      qu'une fois (immuable) et seulement après `pondeuse-elevage`.

Données incluses (scénario Lot Mai 2026 — Bâtiment A, 2 000 poussins Ross 308) :
    Mortalités   : 5 événements, 40 oiseaux au total
    Aliments     : 11 saisies (démarrage / croissance / finition)
    Médicaments  : 7 saisies (vaccins + amoxicilline + vitamines)
    Fertilisant  : 4 collectes brutes (Bâtiment A) + 1 traitement validé
    (Offsets vérifiés contre scenario_avicole_full_cycle_fresh_start.md §5.2-5.4 :
    date_ouverture = 2026-05-10 (J0) ; ex. mortalité J+3 = 2026-05-13, etc.)

Données incluses (scénario Lot Pondeuses 2026, cycle biologique complet §5.6) :
    Mortalités   : 6 événements phase élevage, 105 oiseaux (≈3,5 %)
    Aliments     : 11 saisies démarrage/croissance/pré-ponte + 4 aliment Ponte
    Médicaments  : 7 saisies (vaccins + amoxicilline + vitamines)
    Pesées       : 4 PeseeEchantillon (J0/J42/J84/J126 — courbe de croissance)
    Transfert    : 1 TransfertLot MODE_FULL à J+126 (Poussinière → Poulailler)
    Œufs         : 8 récoltes (montée en ponte réaliste post-transfert)
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# ---------------------------------------------------------------------------
# Données du scénario — Lot Mai 2026 — Bâtiment A
# Toutes les dates sont exprimées en jours écoulés depuis lot.date_ouverture
# (J0 = date d'ouverture) pour rester valides quelle que soit la date réelle
# à laquelle seed_db a été exécuté. Modifiez ces constantes si vous utilisez
# un autre scénario.
# ---------------------------------------------------------------------------

DEFAULT_LOT_DESIGNATION = "Lot Mai 2026 — Bâtiment A"

# Données de création automatique du lot broiler par défaut (scénario §4.2)
# — utilisées uniquement quand --lot garde sa valeur par défaut ET que le
# lot n'existe pas encore en base (auto-création idempotente, voir
# _ensure_lot_broiler ci-dessous). Pour tout autre --lot, le lot doit
# continuer d'être créé manuellement via l'interface.
DEFAULT_BATIMENT_NOM = "Bâtiment A"
DEFAULT_FOURNISSEUR_NOM = "Couvoirs du Centre — CCA"
DEFAULT_BL_REFERENCE = "BLF-2026-0001"
DEFAULT_SOUCHE = "Ross 308"
DEFAULT_NOMBRE_POUSSINS = 2000
DEFAULT_LOT_AGE_JOURS = 40  # date_ouverture = date.today() - 40j (cf. §5.1)

# Format : (jour_depuis_ouverture, nombre, cause)
MORTALITE_DATA = [
    (3, 5, "Stress transport / déshydratation"),
    (8, 10, "Infection respiratoire précoce"),
    (14, 12, "Aspergillose suspectée"),
    (22, 8, "Coccidiose — traitement lancé"),
    (35, 5, "Cause indéterminée"),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
# Désignations EXACTES telles qu'elles existent dans la table Intrant.
ALIMENT_DATA = [
    # Démarrage J0→J14
    (2, "علف البداية — الطور الأول (0–14 يوم)", Decimal("25.000")),
    (4, "علف البداية — الطور الأول (0–14 يوم)", Decimal("25.000")),
    (7, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    (11, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    (14, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    # Croissance J15→J28
    (15, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    (22, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    (29, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("60.000")),
    # Finition J29→J40
    (29, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
    (34, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
    (39, "علف التسمين — الطور الثالث (29 يوم فأكثر)", Decimal("50.000")),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
MEDIC_DATA = [
    (3, "فيتامينات + إلكتروليتات (مركّب)", Decimal("2.000")),
    (8, "أموكسيسيلين 50% مسحوق", Decimal("250.000")),
    (8, "فيتامينات + إلكتروليتات (مركّب)", Decimal("3.000")),
    (14, "لقاح نيوكاسل (هيتشنر B1)", Decimal("2000.000")),
    (22, "لقاح غامبورو (IBD متوسط)", Decimal("1965.000")),
    (22, "أموكسيسيلين 50% مسحوق", Decimal("250.000")),
    (22, "فيتامينات + إلكتروليتات (مركّب)", Decimal("5.000")),
]

# ---------------------------------------------------------------------------
# Fertilisant — collecte brute + traitement (rattachés au BÂTIMENT du lot
# broiler, Bâtiment A — pas au lot lui-même : la litière est un sous-produit
# du bâtiment, cf. modèle CollecteFertilisant). Offsets relatifs à
# lot.date_ouverture, comme pour les mortalités/consommations ci-dessus.
# ---------------------------------------------------------------------------

# Format : (jour_depuis_ouverture, quantite_brute_kg)
FERTILISANT_COLLECTE_DATA = [
    (10, Decimal("180.000")),
    (20, Decimal("220.000")),
    (30, Decimal("240.000")),
    (39, Decimal("200.000")),  # nettoyage de fin de cycle
]

# Lot de traitement unique regroupant les 4 collectes ci-dessus. Validé
# directement (statut=VALIDE) pour créditer le stock de sacs finis, comme
# ProductionRecord dans le scénario principal.
FERTILISANT_TRAITEMENT = dict(
    jour=45,
    methode="تجفيف طبيعي بالشمس",
    produit_fini_designation="سماد دواجن معالج (مجفف)",
    quantite_obtenue_kg=Decimal("720.000"),
    cout_unitaire_estime=Decimal("9.5000"),
)

# ---------------------------------------------------------------------------
# Lot Pondeuses — cycle biologique complet : ouvert comme poussines d'un jour
# en Poussinière (Bâtiment C), élevées ~18 semaines (126 j), puis transférées
# (TransfertLot MODE_FULL) vers le Poulailler (Bâtiment B) au point de ponte,
# où la récolte d'œufs démarre avec une montée en cadence réaliste.
# Offsets relatifs à CE lot pondeuses' date_ouverture (cf. scenario §5.6).
# Un lot broiler Ross 308 n'a jamais cette phase (cf. Annexe B du scénario).
# ---------------------------------------------------------------------------

DEFAULT_LOT_PONDEUSES_DESIGNATION = "Lot Pondeuses 2026"

# Format : (jour_depuis_ouverture, nombre, cause) — mortalité phase élevage
# (Poussinière), avant transfert. Cumul visé ≈ 3,5 % à J+126 (standard pour
# une bande de poulettes pondeuses ISA Brown).
MORTALITE_DATA_PONDEUSES = [
    (4, 15, "Stress transport / déshydratation"),
    (12, 20, "Infection respiratoire précoce"),
    (30, 18, "Coccidiose — traitement lancé"),
    (55, 15, "Cause diverse (élevage)"),
    (80, 20, "Piétinement / casse"),
    (110, 17, "Cause indéterminée — avant transfert"),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
# Phase élevage (Poussinière) : démarrage → croissance → pré-ponte.
# Réutilise les mêmes aliments démarrage/croissance que le lot broiler
# (catalogue minimal), puis l'aliment Pré-Ponte dédié dans les 2-3 dernières
# semaines avant transfert (cf. INT-10 du scénario, stade=demarrage).
ALIMENT_DATA_PONDEUSES = [
    # Démarrage — semaines 0-6 (J0→J42) : 180 sacs
    (5, "علف البداية — الطور الأول (0–14 يوم)", Decimal("40.000")),
    (15, "علف البداية — الطور الأول (0–14 يوم)", Decimal("50.000")),
    (25, "علف البداية — الطور الأول (0–14 يوم)", Decimal("45.000")),
    (35, "علف البداية — الطور الأول (0–14 يوم)", Decimal("45.000")),
    # Croissance — semaines 6-15 (J42→J105) : 420 sacs
    (50, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("80.000")),
    (65, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("90.000")),
    (80, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("90.000")),
    (95, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("80.000")),
    (104, "علف النمو — الطور الثاني (15–28 يوم)", Decimal("80.000")),
    # Pré-Ponte — semaines 16-18 (J105→J126) : 90 sacs
    (112, "علف ما قبل الإنتاج — Pré-Ponte (15–18 أسبوع)", Decimal("45.000")),
    (124, "علف ما قبل الإنتاج — Pré-Ponte (15–18 أسبوع)", Decimal("45.000")),
]

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
MEDIC_DATA_PONDEUSES = [
    (4, "فيتامينات + إلكتروليتات (مركّب)", Decimal("3.000")),
    (10, "لقاح نيوكاسل (هيتشنر B1)", Decimal("3000.000")),
    (18, "لقاح غامبورو (IBD متوسط)", Decimal("2965.000")),
    (30, "أموكسيسيلين 50% مسحوق", Decimal("300.000")),
    (30, "فيتامينات + إلكتروليتات (مركّب)", Decimal("4.000")),
    (60, "لقاح نيوكاسل (هيتشنر B1)", Decimal("2925.000")),
    (115, "فيتامينات + إلكتروليتات (مركّب)", Decimal("5.000")),
]

# Format : (jour_depuis_ouverture, nombre_sujets, poids_total_g)
# Suivi de croissance corporelle — cibles standard ISA Brown (poulette) :
# ~40 g au jour 1, ~430 g à 6 sem., ~1 050 g à 12 sem., ~1 500 g à 18 sem.
PESEE_DATA_PONDEUSES = [
    (0, 50, Decimal("2000.00")),
    (42, 50, Decimal("21500.00")),
    (84, 50, Decimal("52500.00")),
    (126, 50, Decimal("75000.00")),
]

# TransfertLot (MODE_FULL) : Poussinière (Bâtiment C) → Poulailler (Bâtiment B)
# au point de ponte (18 semaines / 126 j). effectif_transfere est résolu
# dynamiquement dans _seed_transfert() = lot.effectif_vivant à cette date.
TRANSFERT_PONDEUSE = dict(
    jour=126,
    batiment_origine_nom="Bâtiment C",
    batiment_destination_nom="Bâtiment B",
    motif="Point de ponte atteint (18 semaines) — transfert vers poulailler",
)

# Format : (jour_depuis_ouverture, designation_intrant, quantite)
# Aliment Ponte (haute teneur en calcium) — post-transfert uniquement,
# stade=croissance (cf. INT-11 : jamais stade=ponte, sinon invisible dans
# ConsommationForm une fois le lot au Poulailler — piège documenté §5.6.6).
ALIMENT_PONTE_DATA = [
    (130, "علف الإنتاج — Ponte (عالي الكالسيوم)", Decimal("80.000")),
    (150, "علف الإنتاج — Ponte (عالي الكالسيوم)", Decimal("100.000")),
    (170, "علف الإنتاج — Ponte (عالي الكالسيوم)", Decimal("100.000")),
    (190, "علف الإنتاج — Ponte (عالي الكالسيوم)", Decimal("100.000")),
]

# Format : (jour_depuis_ouverture, nombre_oeufs)
# Montée en ponte réaliste post-transfert (J+126) : premiers œufs ~J+133
# (19 sem., ~5 % hen-day), pic de production ~92 % vers J+182-196 (26-28 sem.),
# calculée sur un effectif d'environ 2 895 poules survivantes au transfert.
RECOLTE_OEUFS_DATA = [
    (133, 145),  # 19 sem. — 5 %  (premiers œufs)
    (140, 637),  # 20 sem. — 22 %
    (147, 1303),  # 21 sem. — 45 %
    (154, 1882),  # 22 sem. — 65 %
    (161, 2316),  # 23 sem. — 80 %
    (168, 2548),  # 24 sem. — 88 %
    (182, 2635),  # 26 sem. — 91 %
    (196, 2664),  # 28 sem. — 92 % (plateau de pic)
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
            "--lot-pondeuses",
            type=str,
            default=DEFAULT_LOT_PONDEUSES_DESIGNATION,
            dest="lot_pondeuses",
            help=(
                f"Désignation exacte du lot pondeuses (défaut: "
                f"«{DEFAULT_LOT_PONDEUSES_DESIGNATION}»), utilisée pour les "
                "sections --what oeufs/pondeuse-elevage/pondeuse-transfert/all. "
                "Doit exister avec statut ouvert, créé manuellement au "
                "préalable (Poussinière Bâtiment C avant transfert, "
                "Poulailler Bâtiment B après)."
            ),
        )
        parser.add_argument(
            "--what",
            choices=[
                "all",
                "mortalites",
                "aliments",
                "medics",
                "fertilisant",
                "oeufs",
                "pondeuse-elevage",
                "pondeuse-transfert",
                "none",
            ],
            default="all",
            help=(
                "Sous-ensemble à peupler : 'all' (défaut, lot broiler uniquement) / "
                "'mortalites' / 'aliments' / 'medics' / 'fertilisant' / "
                "'oeufs' (post-transfert) / 'pondeuse-elevage' (mortalités+aliments+"
                "médics+pesées, phase Poussinière) / 'pondeuse-transfert' "
                "(TransfertLot Poussinière → Poulailler, J+126) / 'none' "
                "(résout/crée le lot sans peupler de données — utilisé par "
                "d'autres commandes de seed comme seed_depenses)."
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

        # ── Resolve (or auto-create) lot ─────────────────────────────────
        try:
            from elevage.models import LotElevage
        except ImportError as exc:
            raise CommandError(f"Impossible d'importer elevage.models : {exc}") from exc

        lot = self._ensure_lot_broiler(lot_designation)

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

        if what in ("all", "fertilisant"):
            self._seed_fertilisant(lot, admin, offset)

        if what in ("all", "oeufs"):
            self._seed_oeufs(options["lot_pondeuses"], offset, strict=(what == "oeufs"))

        if what == "pondeuse-elevage":
            self._seed_pondeuse_elevage(options["lot_pondeuses"], offset)

        if what == "pondeuse-transfert":
            self._seed_transfert(options["lot_pondeuses"], admin, offset)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_elevage_lot terminé.\n"))

    # ------------------------------------------------------------------
    # Résolution / auto-création du lot broiler par défaut
    # ------------------------------------------------------------------

    def _ensure_lot_broiler(self, designation: str):
        """
        Retourne le LotElevage «designation», en le créant automatiquement
        s'il n'existe pas ENCORE et qu'il s'agit de la désignation par
        défaut du scénario (DEFAULT_LOT_DESIGNATION).

        Auparavant ce lot était créé par le script monolithique seed_db.py ;
        avec la séquence granulaire actuelle (seed_db_minimal → seed_buildings
        → seed_achats_scenario → seed_elevage_lot → seed_depenses), plus rien
        ne le créait, d'où le «Lot introuvable» — cette méthode comble ce
        manque en recréant le lot du §4.2 du scénario à la volée.

        Pour toute autre désignation (--lot personnalisé, lot pondeuses…),
        le comportement historique est conservé : le lot doit déjà exister.
        """
        from elevage.models import LotElevage

        try:
            return LotElevage.objects.get(designation=designation)
        except LotElevage.DoesNotExist:
            pass

        if designation != DEFAULT_LOT_DESIGNATION:
            raise CommandError(
                f"Lot introuvable : «{designation}».\n"
                "Créez le lot via l'interface (ÉLEVAGE → Lots → Ouvrir un nouveau lot) "
                "avant d'exécuter cette commande, ou utilisez la désignation par "
                f"défaut «{DEFAULT_LOT_DESIGNATION}» pour bénéficier de la "
                "création automatique."
            )

        self.stdout.write(
            self.style.WARNING(
                f"  ~ Lot «{designation}» introuvable — création automatique "
                f"({DEFAULT_NOMBRE_POUSSINS} poussins {DEFAULT_SOUCHE}, "
                f"{DEFAULT_BATIMENT_NOM})…"
            )
        )

        from intrants.models import Batiment

        try:
            batiment = Batiment.objects.get(nom=DEFAULT_BATIMENT_NOM)
        except Batiment.DoesNotExist:
            raise CommandError(
                f"Bâtiment «{DEFAULT_BATIMENT_NOM}» introuvable — "
                "exécutez d'abord 'python manage.py seed_buildings'."
            )
        except Batiment.MultipleObjectsReturned:
            batiment = Batiment.objects.filter(nom=DEFAULT_BATIMENT_NOM).first()

        try:
            from intrants.models import Fournisseur
        except ImportError as exc:
            raise CommandError(f"Impossible d'importer achats.models : {exc}") from exc

        try:
            fournisseur = Fournisseur.objects.get(nom=DEFAULT_FOURNISSEUR_NOM)
        except Fournisseur.DoesNotExist:
            raise CommandError(
                f"Fournisseur «{DEFAULT_FOURNISSEUR_NOM}» introuvable — "
                "exécutez d'abord 'python manage.py seed_achats_scenario'."
            )
        except Fournisseur.MultipleObjectsReturned:
            fournisseur = Fournisseur.objects.filter(
                nom=DEFAULT_FOURNISSEUR_NOM
            ).first()

        from achats.models import BLFournisseur

        bl_poussins = BLFournisseur.objects.filter(
            reference=DEFAULT_BL_REFERENCE
        ).first()
        if bl_poussins is None:
            self.stdout.write(
                self.style.WARNING(
                    f"    (BL fournisseur «{DEFAULT_BL_REFERENCE}» introuvable — "
                    "le lot sera créé sans lien vers le BL, champ optionnel)"
                )
            )

        lot = LotElevage.objects.create(
            designation=designation,
            date_ouverture=date.today() - timedelta(days=DEFAULT_LOT_AGE_JOURS),
            nombre_poussins_initial=DEFAULT_NOMBRE_POUSSINS,
            fournisseur_poussins=fournisseur,
            bl_fournisseur_poussins=bl_poussins,
            batiment=batiment,
            souche=DEFAULT_SOUCHE,
            notes="Créé automatiquement par seed_elevage_lot (lot par défaut du scénario).",
        )
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Lot créé : «{lot.designation}» (id={lot.pk})\n")
        )
        return lot

    # ------------------------------------------------------------------
    # Lot Pondeuses — phase élevage (Poussinière) : mortalités, aliments,
    # médicaments, pesées. Doit être exécuté AVANT pondeuse-transfert.
    # ------------------------------------------------------------------

    def _resolve_lot_pondeuses(self, designation: str):
        from elevage.models import LotElevage

        try:
            return LotElevage.objects.get(designation=designation)
        except LotElevage.DoesNotExist:
            raise CommandError(
                f"Lot pondeuses introuvable : «{designation}».\n"
                "Créez-le via l'interface (ÉLEVAGE → Lots → Ouvrir un nouveau "
                "lot, bâtiment = Bâtiment C / poussinière) avant d'exécuter "
                "cette section."
            )

    def _seed_pondeuse_elevage(self, lot_pondeuses_designation: str, offset: timedelta):
        from elevage.models import Mortalite, PeseeEchantillon

        lot_pondeuses = self._resolve_lot_pondeuses(lot_pondeuses_designation)
        admin = User.objects.filter(is_superuser=True).first()

        # ── Mortalités ──────────────────────────────────────────────────
        created_count = 0
        for jour, nombre, cause in MORTALITE_DATA_PONDEUSES:
            dt = lot_pondeuses.date_ouverture + timedelta(days=jour) + offset
            _, created = Mortalite.objects.get_or_create(
                lot=lot_pondeuses, date=dt, nombre=nombre, defaults={"cause": cause}
            )
            if created:
                created_count += 1
        self._log_summary(
            "Pondeuses — Mortalités (élevage)",
            created_count,
            len(MORTALITE_DATA_PONDEUSES),
        )

        # ── Aliments (démarrage/croissance/pré-ponte) ──────────────────
        self._seed_consommations(
            lot_pondeuses,
            ALIMENT_DATA_PONDEUSES,
            "Pondeuses — Aliments (élevage)",
            admin,
            offset,
            "ALIMENT",
        )

        # ── Médicaments ─────────────────────────────────────────────────
        self._seed_consommations(
            lot_pondeuses,
            MEDIC_DATA_PONDEUSES,
            "Pondeuses — Médicaments",
            admin,
            offset,
            "MEDICAMENT",
        )

        # ── Pesées d'échantillon (courbe de croissance) ────────────────
        created_count = 0
        for jour, nombre_sujets, poids_total_g in PESEE_DATA_PONDEUSES:
            dt = lot_pondeuses.date_ouverture + timedelta(days=jour) + offset
            _, created = PeseeEchantillon.objects.get_or_create(
                lot=lot_pondeuses,
                date=dt,
                type_pesee=PeseeEchantillon.TYPE_OISEAUX,
                defaults={
                    "nombre_sujets": nombre_sujets,
                    "poids_total_g": poids_total_g,
                    "created_by": admin,
                },
            )
            if created:
                created_count += 1
        self._log_summary(
            "Pondeuses — Pesées", created_count, len(PESEE_DATA_PONDEUSES)
        )

    # ------------------------------------------------------------------
    # Lot Pondeuses — TransfertLot (Poussinière → Poulailler, J+126)
    # ------------------------------------------------------------------

    def _seed_transfert(self, lot_pondeuses_designation: str, admin, offset: timedelta):
        from elevage.models import TransfertLot
        from intrants.models import Batiment

        lot_pondeuses = self._resolve_lot_pondeuses(lot_pondeuses_designation)
        spec = TRANSFERT_PONDEUSE

        if lot_pondeuses.transferts.exists():
            self.stdout.write(
                self.style.WARNING(
                    "  ~ Transfert déjà enregistré pour ce lot pondeuses — ignoré "
                    "(TransfertLot est immuable)."
                )
            )
            return

        try:
            batiment_origine = Batiment.objects.get(nom=spec["batiment_origine_nom"])
            batiment_destination = Batiment.objects.get(
                nom=spec["batiment_destination_nom"]
            )
        except Batiment.DoesNotExist as exc:
            raise CommandError(
                f"Bâtiment introuvable pour le transfert pondeuses : {exc}. "
                "Créez Bâtiment B (poulailler) et Bâtiment C (poussinière) "
                "au préalable (STOCK → Bâtiments → Nouveau)."
            ) from exc

        dt = lot_pondeuses.date_ouverture + timedelta(days=spec["jour"]) + offset
        effectif = lot_pondeuses.effectif_vivant

        transfert = TransfertLot(
            lot=lot_pondeuses,
            batiment_origine=batiment_origine,
            batiment_destination=batiment_destination,
            date_transfert=dt,
            age_jours_transfert=spec["jour"],
            effectif_transfere=effectif,
            motif=spec["motif"],
            mode=TransfertLot.MODE_FULL,
            created_by=admin,
        )
        transfert.full_clean()
        transfert.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ TransfertLot {dt}  {effectif} oiseaux  "
                f"{batiment_origine.nom} → {batiment_destination.nom}"
            )
        )
        self._log_summary("Pondeuses — Transfert", 1, 1)

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
    # Fertilisant — collecte brute (bâtiment du lot) + traitement
    # ------------------------------------------------------------------

    def _seed_fertilisant(self, lot, admin, offset: timedelta):
        """
        Seed CollecteFertilisant (raw manure, per bâtiment) then a single
        TraitementFertilisant batch consuming all of them, validated
        directly to credit stock (mirrors ProductionRecord's
        BROUILLON→VALIDE pattern, cf. production/signals.py).
        """
        from production.models import CollecteFertilisant, TraitementFertilisant
        from production.models import ProduitFini

        batiment = lot.batiment

        # ── Collectes brutes ───────────────────────────────────────────
        collectes = []
        created_count = 0
        for jour, quantite in FERTILISANT_COLLECTE_DATA:
            dt = lot.date_ouverture + timedelta(days=jour) + offset
            obj, created = CollecteFertilisant.objects.get_or_create(
                batiment=batiment,
                date_collecte=dt,
                quantite_brute_kg=quantite,
                defaults={"created_by": admin},
            )
            collectes.append(obj)
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Collecte fertilisant {dt}  {quantite} kg  ({batiment.nom})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Collecte fertilisant {dt}  {quantite} kg  (déjà existante)"
                    )
                )
        self._log_summary(
            "Collectes fertilisant", created_count, len(FERTILISANT_COLLECTE_DATA)
        )

        # ── Traitement (batch unique) ──────────────────────────────────
        spec = FERTILISANT_TRAITEMENT
        try:
            produit_fini = ProduitFini.objects.get(
                designation=spec["produit_fini_designation"]
            )
        except ProduitFini.DoesNotExist:
            raise CommandError(
                f"ProduitFini introuvable : «{spec['produit_fini_designation']}» — "
                "exécutez d'abord 'python manage.py seed_db_minimal' "
                "(catégorie fertilisant)."
            )

        dt_traitement = lot.date_ouverture + timedelta(days=spec["jour"]) + offset
        traitement, created = TraitementFertilisant.objects.get_or_create(
            branche=batiment.branche,
            date_traitement=dt_traitement,
            produit_fini=produit_fini,
            defaults=dict(
                methode=spec["methode"],
                quantite_obtenue_kg=spec["quantite_obtenue_kg"],
                cout_unitaire_estime=spec["cout_unitaire_estime"],
                statut=TraitementFertilisant.STATUT_VALIDE,
                created_by=admin,
            ),
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Traitement fertilisant {dt_traitement}  "
                    f"{spec['quantite_obtenue_kg']} kg → {produit_fini.designation}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ Traitement fertilisant {dt_traitement}  (déjà existant)"
                )
            )

        # Assign every raw collecte to this batch (idempotent — save() is a
        # no-op if traitement_id is already set to the same value).
        assigned = 0
        for c in collectes:
            if c.traitement_id != traitement.pk:
                c.traitement = traitement
                c.save(update_fields=["traitement"])
                assigned += 1
        if assigned:
            self.stdout.write(f"  ✓ {assigned} collecte(s) assignée(s) au traitement")

        self._log_summary("Traitement fertilisant", 1 if created else 0, 1)

    # ------------------------------------------------------------------
    # Œufs — lot pondeuses séparé (Bâtiment B)
    # ------------------------------------------------------------------

    def _seed_oeufs(
        self, lot_pondeuses_designation: str, offset: timedelta, strict: bool
    ):
        """
        Seed RecolteOeufs for the (separate) laying lot.

        `strict=True` (explicit `--what oeufs`) raises if the lot pondeuses
        doesn't exist yet. `strict=False` (part of `--what all`) just skips
        with a warning, so the default broiler-only run keeps working even
        before the laying lot has been created via the interface.
        """
        from elevage.models import LotElevage, RecolteOeufs

        try:
            lot_pondeuses = LotElevage.objects.get(
                designation=lot_pondeuses_designation
            )
        except LotElevage.DoesNotExist:
            message = (
                f"Lot pondeuses introuvable : «{lot_pondeuses_designation}».\n"
                "Créez-le via l'interface (ÉLEVAGE → Lots → Ouvrir un nouveau lot, "
                "bâtiment = Bâtiment B / poulailler) avant d'exécuter cette section."
            )
            if strict:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(f"  ~ Œufs ignorés : {message}"))
            return

        if lot_pondeuses.statut != LotElevage.STATUT_OUVERT:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠  Le lot pondeuses est au statut «{lot_pondeuses.statut}» "
                    "(attendu: ouvert). Les récoltes ne pourront pas être ajoutées "
                    "sur un lot fermé. Continuer quand même…"
                )
            )

        if not lot_pondeuses.transferts.exists():
            self.stdout.write(
                self.style.WARNING(
                    "  ⚠  Aucun TransfertLot enregistré — ce lot est probablement "
                    "encore en Poussinière (Bâtiment C). Exécutez d'abord "
                    "'--what pondeuse-transfert'. La ponte réelle ne démarre "
                    "qu'après le transfert au Poulailler."
                )
            )

        # Aliment Ponte (haute teneur en calcium) — post-transfert
        admin = User.objects.filter(is_superuser=True).first()
        self._seed_consommations(
            lot_pondeuses,
            ALIMENT_PONTE_DATA,
            "Pondeuses — Aliment Ponte",
            admin,
            offset,
            "ALIMENT",
        )

        created_count = 0
        for jour, nombre_oeufs in RECOLTE_OEUFS_DATA:
            dt = lot_pondeuses.date_ouverture + timedelta(days=jour) + offset
            _, created = RecolteOeufs.objects.get_or_create(
                lot=lot_pondeuses,
                date=dt,
                nombre_oeufs=nombre_oeufs,
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Récolte œufs {dt}  {nombre_oeufs} œufs")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Récolte œufs {dt}  {nombre_oeufs} œufs  (déjà existante)"
                    )
                )

        self._log_summary("Œufs", created_count, len(RECOLTE_OEUFS_DATA))

    # ------------------------------------------------------------------

    def _log_summary(self, label: str, created: int, total: int):
        self.stdout.write(
            f"\n  {'✓' if created > 0 else '~'} {label} : "
            f"{created} créé(s) / {total} total\n"
        )
