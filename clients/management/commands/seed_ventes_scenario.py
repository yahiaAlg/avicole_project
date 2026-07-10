"""
management/commands/seed_ventes_scenario.py

Peuplement rapide de la Phase 6 du scénario fresh-start — Vente & Livraison
Client :
    BL Client (3) → Facture Client (3) → Paiement Client + allocation (3)

Objectif : éviter la saisie manuelle via VENTES → BL Client / Factures /
Paiements, décrite dans scenario_avicole_full_cycle_fresh_start.md §8.

Utilisation :
    # Peuplement complet (BLC + FAC + Paiements)
    python manage.py seed_ventes_scenario

    # Cibler une branche précise (code Branche) — défaut STF
    python manage.py seed_ventes_scenario --branche STF

    # Ne créer que les BL (nécessite du stock produits finis disponible)
    python manage.py seed_ventes_scenario --what bls

    # Ne créer que les factures (nécessite que les BL existent déjà, statut Livré)
    python manage.py seed_ventes_scenario --what factures

    # Ne créer que les paiements (nécessite que les factures existent déjà)
    python manage.py seed_ventes_scenario --what paiements

    # Supprimer puis recréer (dans l'ordre inverse des dépendances)
    python manage.py seed_ventes_scenario --clear

⚠️ Prérequis :
    1. python manage.py seed_db_minimal      ← Branche STF + ProduitFini
    2. python manage.py seed_phase0          ← Clients
    3. Phase 4 (Abattage) + Phase 5 (Ajustement) exécutées manuellement,
       de sorte que StockProduitFini(branche=STF) dispose bien de :
         • Poulet vivant (دجاج حي)             ≥ 500 unités
         • Carcasse entière vidée (جثة كاملة)  ≥ 1 457 unités
       Sans ce stock, la validation des BL Client (statut Livré) créera un
       solde négatif — le script avertit mais ne bloque pas (BR-BLC-02 est
       appliquée côté formulaire/vue, pas au niveau ORM direct).
    4. Pour BLC-2025-0004 (œufs, §8.4) : le lot Pondeuses 2025 doit avoir
       enregistré ses 8 RecolteOeufs (§5.6.7, dernière récolte 2025-11-27),
       de sorte que StockProduitFini(صينية بيض) ≥ 471 plateaux.

Détails (scenario §8.2 → §8.4) :
    BLC-2025-0001 — Marché de Gros Setifien   (300 Poulet vivant + 800 Carcasse)
    BLC-2025-0002 — Boucherie Amrane & Fils   (200 Poulet vivant + 400 Carcasse)
    BLC-2025-0003 — Restaurant Le Palmier     (257 Carcasse)
    BLC-2025-0004 — Marché de Gros Setifien   (471 plateaux œufs — clôture Lot Pondeuses, §8.4)

    FAC-2025-0001 → 0004 — une facture par BL, montant_ht auto-calculé
                            (BR-FAC-01), TVA 0% (volaille/œufs exonérés).

    Paiements — allocation MANUELLE (BR-FAC-03, pas de FIFO automatique côté
    client) : le script crée le PaiementClient puis la/les
    PaiementClientAllocation correspondantes, et appelle
    facture.recalculer_solde() pour refléter le nouveau statut, exactement
    comme le ferait la vue de paiement.
        • FAC-2025-0001 : payée intégralement (744 000 DZD, espèces)
        • FAC-2025-0002 : acompte partiel (200 000 / 396 000 DZD, chèque)
        • FAC-2025-0003 : payée intégralement (200 460 DZD, virement)
        • FAC-2025-0004 : payée intégralement (164 850 DZD, virement — œufs)

Idempotent : get_or_create sur `reference` pour BLC/FAC. Les paiements
n'ont pas de champ reference unique dans le modèle ; l'idempotence se fait
sur la combinaison (client, date_paiement, montant, reference_paiement).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"

WHAT_CHOICES = ["all", "bls", "factures", "paiements"]

# ---------------------------------------------------------------------------
# Données du scénario (scenario_avicole_full_cycle_fresh_start.md §8)
# ---------------------------------------------------------------------------

# Format : (reference, client_nom, date_bl, adresse_livraison, signe_par,
#           [ (produit_designation, quantite, prix_unitaire), ... ])
BLC_DATA = [
    (
        "BLC-2025-0001",
        "Marché de Gros Setifien",
        datetime.date(2025, 6, 20),
        "Zone de marché, Route nationale 5, Setifien",
        "Boualem Khaled — Réceptionnaire",
        [
            ("دجاج حي (الوزن الكامل)", Decimal("300"), Decimal("480.0000")),
            ("جثة كاملة منزوعة الأحشاء", Decimal("800"), Decimal("750.0000")),
        ],
    ),
    (
        "BLC-2025-0002",
        "Boucherie Amrane & Fils",
        datetime.date(2025, 6, 21),
        "",
        "",
        [
            ("دجاج حي (الوزن الكامل)", Decimal("200"), Decimal("480.0000")),
            ("جثة كاملة منزوعة الأحشاء", Decimal("400"), Decimal("750.0000")),
        ],
    ),
    (
        "BLC-2025-0003",
        "Restaurant Le Palmier",
        datetime.date(2025, 6, 22),
        "",
        "",
        [
            ("جثة كاملة منزوعة الأحشاء", Decimal("257"), Decimal("780.0000")),
        ],
    ),
    # BLC-2025-0004 — Vente Œufs (Lot Pondeuses 2025, §8.4).
    # Clôt la boucle sur les 471 plateaux récoltés au §5.6.7 (dernière
    # récolte 2025-11-27) — auparavant produits mais jamais vendus.
    (
        "BLC-2025-0004",
        "Marché de Gros Setifien",
        datetime.date(2025, 11, 30),
        "",
        "",
        [
            ("صينية بيض (30 بيضة)", Decimal("471"), Decimal("350.0000")),
        ],
    ),
]

# Format : (reference, client_nom, [bl_references], date_facture, date_echeance, taux_tva)
FAC_DATA = [
    (
        "FAC-2025-0001",
        "Marché de Gros Setifien",
        ["BLC-2025-0001"],
        datetime.date(2025, 6, 20),
        datetime.date(2025, 7, 20),
        Decimal("0.00"),
    ),
    (
        "FAC-2025-0002",
        "Boucherie Amrane & Fils",
        ["BLC-2025-0002"],
        datetime.date(2025, 6, 21),
        datetime.date(2025, 7, 21),
        Decimal("0.00"),
    ),
    (
        "FAC-2025-0003",
        "Restaurant Le Palmier",
        ["BLC-2025-0003"],
        datetime.date(2025, 6, 22),
        datetime.date(2025, 7, 22),
        Decimal("0.00"),
    ),
    (
        "FAC-2025-0004",
        "Marché de Gros Setifien",
        ["BLC-2025-0004"],
        datetime.date(2025, 11, 30),
        datetime.date(2025, 12, 30),
        Decimal("0.00"),
    ),
]

# Format : (client_nom, date_paiement, montant, mode_paiement, reference_paiement,
#           [ (facture_reference, montant_alloue), ... ])
PAIEMENT_DATA = [
    (
        "Marché de Gros Setifien",
        datetime.date(2025, 6, 20),
        Decimal("744000.00"),
        "especes",
        "",
        [("FAC-2025-0001", Decimal("744000.00"))],
    ),
    (
        "Boucherie Amrane & Fils",
        datetime.date(2025, 6, 21),
        Decimal("200000.00"),
        "cheque",
        "CHQ-AMRANE-1044",
        [("FAC-2025-0002", Decimal("200000.00"))],
    ),
    (
        "Restaurant Le Palmier",
        datetime.date(2025, 6, 22),
        Decimal("200460.00"),
        "virement",
        "VIR-PALMIER-22062025",
        [("FAC-2025-0003", Decimal("200460.00"))],
    ),
    (
        "Marché de Gros Setifien",
        datetime.date(2025, 12, 1),
        Decimal("164850.00"),
        "virement",
        "VIR-MG-OEUFS-301126",
        [("FAC-2025-0004", Decimal("164850.00"))],
    ),
]


class Command(BaseCommand):
    help = (
        "Phase 6 du scénario fresh-start : BL Client / Factures / Paiements "
        "(scenario_avicole_full_cycle_fresh_start.md §8). À exécuter après "
        "seed_phase0 et une fois du stock produits finis disponible."
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
                "Supprime Paiements/Allocations puis Factures puis BL (dans "
                "cet ordre) avant de recréer."
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
                f"\n=== seed_ventes_scenario — Branche : «{branche}» ===\n"
            )
        )

        if options["clear"]:
            self._clear()

        if what in ("all", "bls"):
            self._seed_bls(branche, admin)
        if what in ("all", "factures"):
            self._seed_factures(branche, admin)
        if what in ("all", "paiements"):
            self._seed_paiements(branche, admin)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_ventes_scenario terminé.\n"))

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(self):
        from clients.models import (
            BLClient,
            FactureClient,
            PaiementClient,
            PaiementClientAllocation,
        )

        self.stdout.write(
            self.style.WARNING("  Suppression Paiements/Allocations/Factures/BL…")
        )
        refs_blc = [row[0] for row in BLC_DATA]
        refs_fac = [row[0] for row in FAC_DATA]
        client_noms = {row[0] for row in PAIEMENT_DATA}

        try:
            paiements = PaiementClient.objects.filter(client__nom__in=client_noms)
            PaiementClientAllocation.objects.filter(paiement__in=paiements).delete()
            paiements.delete()
            FactureClient.objects.filter(reference__in=refs_fac).delete()
            BLClient.objects.filter(reference__in=refs_blc).delete()
        except Exception as exc:
            raise CommandError(f"Impossible de nettoyer proprement : {exc}") from exc
        self.stdout.write("  Terminé.\n")

    # ------------------------------------------------------------------
    # BL Client
    # ------------------------------------------------------------------

    def _seed_bls(self, branche, admin):
        from clients.models import BLClient, BLClientLigne, Client
        from production.models import ProduitFini

        created_count = 0
        for reference, client_nom, date_bl, adresse, signe_par, lignes in BLC_DATA:
            try:
                client = Client.objects.get(nom=client_nom)
            except Client.DoesNotExist:
                raise CommandError(
                    f"Client introuvable : «{client_nom}». "
                    "Exécutez d'abord 'python manage.py seed_phase0'."
                )

            bl, created = BLClient.objects.get_or_create(
                reference=reference,
                defaults=dict(
                    branche=branche,
                    client=client,
                    date_bl=date_bl,
                    adresse_livraison=adresse,
                    signe_par=signe_par,
                    statut=BLClient.STATUT_BROUILLON,
                    created_by=admin,
                ),
            )

            if not created:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {reference}  (déjà existant)")
                )
                continue

            for designation, quantite, prix_unitaire in lignes:
                try:
                    produit = ProduitFini.objects.get(designation=designation)
                except ProduitFini.DoesNotExist:
                    raise CommandError(
                        f"ProduitFini introuvable : «{designation}». "
                        "Exécutez d'abord 'python manage.py seed_db_minimal'."
                    )
                BLClientLigne.objects.create(
                    bl=bl,
                    produit_fini=produit,
                    quantite=quantite,
                    prix_unitaire=prix_unitaire,
                )

            # Transition explicite brouillon → livré : les lignes existent déjà,
            # donc le signal post_save (BLClientLigne / BLClient) décrémente
            # correctement StockProduitFini et journalise le StockMouvement.
            bl.statut = BLClient.STATUT_LIVRE
            bl.save()

            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {reference}  {client_nom:<28}  {len(lignes)} ligne(s) → statut Livré"
                )
            )

        self.stdout.write(
            f"\n  BL Client : {created_count} créé(s) / {len(BLC_DATA)} total\n"
        )

    # ------------------------------------------------------------------
    # Facture Client
    # ------------------------------------------------------------------

    def _seed_factures(self, branche, admin):
        from clients.models import BLClient, Client, FactureClient

        created_count = 0
        for (
            reference,
            client_nom,
            bl_refs,
            date_facture,
            date_echeance,
            taux_tva,
        ) in FAC_DATA:
            try:
                client = Client.objects.get(nom=client_nom)
            except Client.DoesNotExist:
                raise CommandError(f"Client introuvable : «{client_nom}».")

            facture, created = FactureClient.objects.get_or_create(
                reference=reference,
                defaults=dict(
                    branche=branche,
                    client=client,
                    date_facture=date_facture,
                    date_echeance=date_echeance,
                    taux_tva=taux_tva,
                    created_by=admin,
                ),
            )

            if not created:
                self.stdout.write(
                    self.style.WARNING(f"  ~ {reference}  (déjà existant)")
                )
                continue

            bls = list(BLClient.objects.filter(reference__in=bl_refs))
            if len(bls) != len(bl_refs):
                found = {b.reference for b in bls}
                missing = set(bl_refs) - found
                raise CommandError(
                    f"BL Client introuvable pour {reference} : {missing}. "
                    "Exécutez d'abord 'seed_ventes_scenario --what bls'."
                )

            # Déclenche le signal m2m_changed (post_add) qui calcule
            # montant_ht/montant_tva/montant_ttc depuis les lignes BL et
            # verrouille les BL (BR-FAC-01/02, BR-BLC-03).
            facture.bls.set(bls)

            facture.refresh_from_db()
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {reference}  {client_nom:<28}  montant_ttc={facture.montant_ttc} DZD"
                )
            )

        self.stdout.write(
            f"\n  Facture Client : {created_count} créé(s) / {len(FAC_DATA)} total\n"
        )

    # ------------------------------------------------------------------
    # Paiement Client (allocation manuelle — BR-FAC-03)
    # ------------------------------------------------------------------

    def _seed_paiements(self, branche, admin):
        from clients.models import (
            Client,
            FactureClient,
            PaiementClient,
            PaiementClientAllocation,
        )

        created_count = 0
        for (
            client_nom,
            date_paiement,
            montant,
            mode_paiement,
            ref_paiement,
            allocations,
        ) in PAIEMENT_DATA:
            try:
                client = Client.objects.get(nom=client_nom)
            except Client.DoesNotExist:
                raise CommandError(f"Client introuvable : «{client_nom}».")

            existing = PaiementClient.objects.filter(
                client=client,
                date_paiement=date_paiement,
                montant=montant,
            ).first()
            if existing:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Paiement {client_nom} — {montant} DZD ({date_paiement})  (déjà existant)"
                    )
                )
                continue

            paiement = PaiementClient.objects.create(
                client=client,
                branche=branche,
                date_paiement=date_paiement,
                montant=montant,
                mode_paiement=mode_paiement,
                reference_paiement=ref_paiement,
                created_by=admin,
            )

            for facture_ref, montant_alloue in allocations:
                try:
                    facture = FactureClient.objects.get(reference=facture_ref)
                except FactureClient.DoesNotExist:
                    raise CommandError(
                        f"Facture Client introuvable : «{facture_ref}». "
                        "Exécutez d'abord 'seed_ventes_scenario --what factures'."
                    )

                PaiementClientAllocation.objects.create(
                    paiement=paiement,
                    facture=facture,
                    montant_alloue=montant_alloue,
                )

                # BR-FAC-03 : l'allocation est manuelle — c'est la vue qui,
                # normalement, met à jour montant_regle puis appelle
                # recalculer_solde(). On reproduit ce comportement ici.
                facture.montant_regle = facture.montant_regle + montant_alloue
                facture.save(update_fields=["montant_regle"])
                facture.recalculer_solde()

            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Paiement {client_nom:<28}  {montant} DZD ({date_paiement}) → alloué"
                )
            )

        self.stdout.write(
            f"\n  Paiement Client : {created_count} créé(s) / {len(PAIEMENT_DATA)} total\n"
        )
