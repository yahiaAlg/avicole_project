"""
management/commands/seed_ventes_scenario.py

Peuplement rapide de la Phase 6 du scénario fresh-start — Vente & Livraison
Client :
    BL Client (3) → Facture Client (3) → Paiement Client + allocation (3)
    + v1.6 : AbonnementClient forfait/prépayé (1) → Paiement avance (1) →
             Échéances générées (2) → Paiement solde (1)

Objectif : éviter la saisie manuelle via VENTES → BL Client / Factures /
Paiements, décrite dans scenario_avicole_full_cycle_fresh_start.md §8.
Le bloc v1.6 (forfait/prépayé) couvre §8.7 du même scénario (version _en).

Utilisation :
    # Peuplement complet (BLC + FAC + Paiements + Abonnement forfait)
    python manage.py seed_ventes_scenario

    # Cibler une branche précise (code Branche) — défaut STF
    python manage.py seed_ventes_scenario --branche STF

    # Ne créer que les BL (nécessite du stock produits finis disponible)
    python manage.py seed_ventes_scenario --what bls

    # Ne créer que les factures (nécessite que les BL existent déjà, statut Livré)
    python manage.py seed_ventes_scenario --what factures

    # Ne créer que les paiements (nécessite que les factures existent déjà)
    python manage.py seed_ventes_scenario --what paiements

    # v1.6 — Abonnement forfaitaire + prépaiement (§8.7), dans cet ordre :
    python manage.py seed_ventes_scenario --what abonnements
    python manage.py seed_ventes_scenario --what paiement_avance
    python manage.py seed_ventes_scenario --what echeances
    python manage.py seed_ventes_scenario --what paiement_solde

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
    5. Pour le bloc v1.6 (--what abonnements/paiement_avance/echeances/
       paiement_solde) : le ProduitFini «سماد دواجن معالج (مجفف)» doit
       exister (aucun STOCK requis — un abonnement forfaitaire ne consomme
       pas StockProduitFini, BR-ABO-03).

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

Détails v1.6 (scenario_avicole_full_cycle_fresh_start_en.md §8.7) :
    Abonnement forfaitaire — Boucherie Amrane & Fils, 6 000 DZD/mois, prépayé.
        • Avance de 10 000 DZD (chèque) enregistrée le 2025-06-28, AVANT toute
          échéance → surplus intégral → AcompteClient (10 000 DZD restants).
        • Échéance Juillet (2025-07-01 → 07-31) : 6 000 DZD entièrement
          absorbés par l'avance → facture payée, avance restante 4 000 DZD.
        • Échéance Août (2025-08-01 → 08-31) : seuls 4 000 DZD restent sur
          l'avance → absorption PARTIELLE → facture « partiellement payée »
          (2 000 DZD restant dus), avance épuisée (utilise=True).
        • Paiement complémentaire du 2025-08-15 (2 000 DZD, espèces,
          allocation manuelle classique BR-FAC-03) → solde de l'échéance
          d'Août ramené à 0.

Idempotent : get_or_create sur `reference` pour BLC/FAC. Les paiements
n'ont pas de champ reference unique dans le modèle ; l'idempotence se fait
sur la combinaison (client, date_paiement, montant, reference_paiement).
L'abonnement forfaitaire n'a pas non plus de champ reference unique ;
l'idempotence se fait sur (client, produit_fini, date_debut). Les échéances
sont idempotentes via AbonnementClient.echeance_deja_facturee().
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_BRANCHE_CODE = "STF"

WHAT_CHOICES = [
    "all",
    "bls",
    "factures",
    "paiements",
    "abonnements",
    "paiement_avance",
    "echeances",
    "paiement_solde",
]

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

# ---------------------------------------------------------------------------
# v1.6 — AbonnementClient forfait (BR-ABO-03) + prépaiement (BR-ABO-03bis) —
# scenario_avicole_full_cycle_fresh_start_en.md §8.7.
#
# Unlike BLC/FAC above, a forfait subscription bills a FIXED monthly amount
# regardless of stock/delivery — no StockProduitFini is required at all for
# this block (LivraisonPartielle stays fully optional/informational under
# forfait, see AbonnementClient docstring).
#
# Scripted lifecycle (mirrors what the "توليد فاتورة"/GenererEcheanceForm
# view + the payment views do, step by step) :
#   1. Client prepays 10 000 DZD before any échéance exists → the whole
#      amount is left unallocated (no facture yet) → becomes an
#      AcompteClient via creer_acompte_client_si_surplus, exactly like
#      appliquer_paiement_client would for any unattributed surplus.
#   2. July échéance (6 000 DZD) is generated via generer_facture_abonnement
#      → consommer_acomptes_client_fifo drains 6 000 from the 10 000
#      advance → invoice fully paid, advance left at 4 000.
#   3. August échéance (6 000 DZD) is generated the same way → only 4 000
#      is left on the advance → PARTIAL auto-consumption → invoice ends up
#      partiellement_payée (2 000 DZD still due) → advance fully exhausted.
#   4. A manual PaiementClient (ordinary postpayé-style allocation,
#      BR-FAC-03) closes the remaining 2 000 DZD on the August échéance.
# ---------------------------------------------------------------------------

ABO_PRODUIT_DESIGNATION = "سماد دواجن معالج (مجفف)"

# NB: only the ProduitFini record needs to exist (FK requirement) — a
# forfait subscription needs NO stock quantity (BR-ABO-03). If it is
# missing, create it manually (production ▸ Produits Finis) or run
# seed_db_minimal if it is included there.
ABONNEMENT_FORFAIT = dict(
    client_nom="Boucherie Amrane & Fils",
    produit_designation=ABO_PRODUIT_DESIGNATION,
    date_debut=datetime.date(2025, 7, 1),
    frequence="mensuel",
    mode_facturation="forfait",
    montant_forfait=Decimal("6000.00"),
    mode_paiement="prepaye",
)

# (client_nom, date_paiement, montant, mode_paiement, reference_paiement)
# — created with ZERO allocations (no facture exists yet): the entire
# amount becomes a surplus → AcompteClient.
PAIEMENT_AVANCE_DATA = (
    "Boucherie Amrane & Fils",
    datetime.date(2025, 6, 28),
    Decimal("10000.00"),
    "cheque",
    "CHQ-AMRANE-SUB-77",
)

# [ (periode_debut, periode_fin, date_facture), ... ] — billed in order via
# generer_facture_abonnement(); July drains the advance in full, August only
# partially (advance runs out mid-invoice).
ECHEANCE_DATA = [
    (datetime.date(2025, 7, 1), datetime.date(2025, 7, 31), datetime.date(2025, 7, 1)),
    (datetime.date(2025, 8, 1), datetime.date(2025, 8, 31), datetime.date(2025, 8, 1)),
]

# (client_nom, date_paiement, montant, mode_paiement, reference_paiement,
#  (periode_debut, periode_fin) of the échéance it closes)
PAIEMENT_SOLDE_DATA = (
    "Boucherie Amrane & Fils",
    datetime.date(2025, 8, 15),
    Decimal("2000.00"),
    "especes",
    "",
    (datetime.date(2025, 8, 1), datetime.date(2025, 8, 31)),
)


class Command(BaseCommand):
    help = (
        "Phase 6 du scénario fresh-start : BL Client / Factures / Paiements "
        "(scenario_avicole_full_cycle_fresh_start.md §8), + v1.6 Abonnement "
        "forfaitaire/prépayé (§8.7). À exécuter après seed_phase0 et une "
        "fois du stock produits finis disponible (le bloc forfait n'a besoin "
        "d'aucun stock, BR-ABO-03)."
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
        if what in ("all", "abonnements"):
            self._seed_abonnements(branche, admin)
        if what in ("all", "paiement_avance"):
            self._seed_paiement_avance(branche, admin)
        if what in ("all", "echeances"):
            self._seed_echeances(branche, admin)
        if what in ("all", "paiement_solde"):
            self._seed_paiement_solde(branche, admin)

        self.stdout.write(self.style.SUCCESS("\n✓ seed_ventes_scenario terminé.\n"))

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(self):
        from clients.models import (
            AbonnementClient,
            AllocationAcompteClient,
            BLClient,
            Client,
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

        # ── v1.6 — Abonnement forfaitaire/prépayé (§8.7) ──────────────────
        self.stdout.write(
            self.style.WARNING(
                "  Suppression Paiement solde/Échéances/Avance/Abonnement forfaitaire…"
            )
        )
        try:
            client = Client.objects.filter(nom=ABONNEMENT_FORFAIT["client_nom"]).first()
            abo = None
            if client is not None:
                abo = AbonnementClient.objects.filter(
                    client=client, date_debut=ABONNEMENT_FORFAIT["date_debut"]
                ).first()

            # Paiement solde (manual allocation on the August échéance).
            solde_client_nom, solde_date, solde_montant = (
                PAIEMENT_SOLDE_DATA[0],
                PAIEMENT_SOLDE_DATA[1],
                PAIEMENT_SOLDE_DATA[2],
            )
            paiement_solde = PaiementClient.objects.filter(
                client__nom=solde_client_nom,
                date_paiement=solde_date,
                montant=solde_montant,
            )
            PaiementClientAllocation.objects.filter(
                paiement__in=paiement_solde
            ).delete()
            paiement_solde.delete()

            if abo is not None:
                factures_abo = FactureClient.objects.filter(abonnement=abo)
                AllocationAcompteClient.objects.filter(
                    facture__in=factures_abo
                ).delete()
                factures_abo.delete()

            # Paiement avance — deleting it cascades to its AcompteClient
            # (OneToOneField(on_delete=CASCADE)); its AllocationAcompteClient
            # rows are already gone above (facture deleted first, PROTECT-safe).
            avance_client_nom, avance_date, avance_montant = (
                PAIEMENT_AVANCE_DATA[0],
                PAIEMENT_AVANCE_DATA[1],
                PAIEMENT_AVANCE_DATA[2],
            )
            PaiementClient.objects.filter(
                client__nom=avance_client_nom,
                date_paiement=avance_date,
                montant=avance_montant,
            ).delete()

            if abo is not None:
                abo.delete()
        except Exception as exc:
            raise CommandError(
                f"Impossible de nettoyer proprement le bloc abonnement forfaitaire : {exc}"
            ) from exc
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

    # ------------------------------------------------------------------
    # v1.6 — AbonnementClient forfait (BR-ABO-03)
    # ------------------------------------------------------------------

    def _seed_abonnements(self, branche, admin):
        from clients.models import AbonnementClient, Client
        from production.models import ProduitFini

        data = ABONNEMENT_FORFAIT
        try:
            client = Client.objects.get(nom=data["client_nom"])
        except Client.DoesNotExist:
            raise CommandError(
                f"Client introuvable : «{data['client_nom']}». "
                "Exécutez d'abord 'python manage.py seed_phase0'."
            )
        try:
            produit = ProduitFini.objects.get(designation=data["produit_designation"])
        except ProduitFini.DoesNotExist:
            raise CommandError(
                f"ProduitFini introuvable : «{data['produit_designation']}». "
                "Créez-le (production ▸ Produits Finis) ou ajoutez-le à "
                "seed_db_minimal — aucun STOCK n'est requis pour un "
                "abonnement forfaitaire (BR-ABO-03)."
            )

        abo, created = AbonnementClient.objects.get_or_create(
            client=client,
            produit_fini=produit,
            date_debut=data["date_debut"],
            defaults=dict(
                branche=branche,
                frequence=data["frequence"],
                mode_facturation=data["mode_facturation"],
                montant_forfait=data["montant_forfait"],
                mode_paiement=data["mode_paiement"],
                statut=AbonnementClient.STATUT_ACTIF,
                created_by=admin,
            ),
        )

        if not created:
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ Abonnement forfaitaire {client.nom}  (déjà existant)"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Abonnement forfaitaire — {client.nom}  "
                f"{data['montant_forfait']} DZD/mois  ({data['mode_paiement']})"
            )
        )
        self.stdout.write("\n  AbonnementClient : 1 créé\n")

    # ------------------------------------------------------------------
    # v1.6 — Paiement avance (prépaiement) → AcompteClient par surplus
    # ------------------------------------------------------------------

    def _seed_paiement_avance(self, branche, admin):
        from clients.models import Client, PaiementClient
        from clients.utils import creer_acompte_client_si_surplus

        client_nom, date_paiement, montant, mode_paiement, ref = PAIEMENT_AVANCE_DATA
        try:
            client = Client.objects.get(nom=client_nom)
        except Client.DoesNotExist:
            raise CommandError(f"Client introuvable : «{client_nom}».")

        existing = PaiementClient.objects.filter(
            client=client, date_paiement=date_paiement, montant=montant
        ).first()
        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ Paiement avance {client_nom} — {montant} DZD ({date_paiement})  (déjà existant)"
                )
            )
            return

        paiement = PaiementClient.objects.create(
            client=client,
            branche=branche,
            date_paiement=date_paiement,
            montant=montant,
            mode_paiement=mode_paiement,
            reference_paiement=ref,
            created_by=admin,
        )

        # No facture exists yet for this subscription → the whole amount is
        # left unallocated → it becomes a surplus, exactly like
        # appliquer_paiement_client would compute for any unattributed
        # remainder (BR-FAC-03).
        acompte = creer_acompte_client_si_surplus(paiement, montant)

        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Paiement avance {client_nom:<28}  {montant} DZD ({date_paiement}) "
                f"→ AcompteClient {acompte.montant_restant} DZD disponibles"
            )
        )
        self.stdout.write("\n  Paiement avance : 1 créé\n")

    # ------------------------------------------------------------------
    # v1.6 — Échéances (generer_facture_abonnement, consomme l'avance)
    # ------------------------------------------------------------------

    def _seed_echeances(self, branche, admin):
        from clients.models import AbonnementClient, Client
        from clients.utils import generer_facture_abonnement

        data = ABONNEMENT_FORFAIT
        try:
            client = Client.objects.get(nom=data["client_nom"])
        except Client.DoesNotExist:
            raise CommandError(f"Client introuvable : «{data['client_nom']}».")

        try:
            abo = AbonnementClient.objects.get(
                client=client, date_debut=data["date_debut"]
            )
        except AbonnementClient.DoesNotExist:
            raise CommandError(
                "AbonnementClient introuvable. Exécutez d'abord "
                "'seed_ventes_scenario --what abonnements'."
            )

        created_count = 0
        for periode_debut, periode_fin, date_facture in ECHEANCE_DATA:
            if abo.echeance_deja_facturee(periode_debut, periode_fin):
                self.stdout.write(
                    self.style.WARNING(
                        f"  ~ Échéance {periode_debut} → {periode_fin}  (déjà facturée)"
                    )
                )
                continue

            facture = generer_facture_abonnement(
                abo,
                periode_debut=periode_debut,
                periode_fin=periode_fin,
                date_facture=date_facture,
                created_by=admin,
            )
            created_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {facture.reference}  échéance {periode_debut} → {periode_fin}  "
                    f"montant_ttc={facture.montant_ttc} DZD  reste_a_payer={facture.reste_a_payer} DZD  "
                    f"statut={facture.get_statut_display()}"
                )
            )

        self.stdout.write(
            f"\n  Échéances abonnement : {created_count} créée(s) / {len(ECHEANCE_DATA)} total\n"
        )

    # ------------------------------------------------------------------
    # v1.6 — Paiement solde (allocation manuelle classique, BR-FAC-03)
    # ------------------------------------------------------------------

    def _seed_paiement_solde(self, branche, admin):
        from clients.models import (
            Client,
            FactureClient,
            PaiementClient,
            PaiementClientAllocation,
        )

        (
            client_nom,
            date_paiement,
            montant,
            mode_paiement,
            ref_paiement,
            (periode_debut, periode_fin),
        ) = PAIEMENT_SOLDE_DATA

        try:
            client = Client.objects.get(nom=client_nom)
        except Client.DoesNotExist:
            raise CommandError(f"Client introuvable : «{client_nom}».")

        existing = PaiementClient.objects.filter(
            client=client, date_paiement=date_paiement, montant=montant
        ).first()
        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f"  ~ Paiement solde {client_nom} — {montant} DZD ({date_paiement})  (déjà existant)"
                )
            )
            return

        try:
            facture = FactureClient.objects.get(
                client=client, periode_debut=periode_debut, periode_fin=periode_fin
            )
        except FactureClient.DoesNotExist:
            raise CommandError(
                f"Facture d'échéance introuvable pour la période {periode_debut} → "
                f"{periode_fin}. Exécutez d'abord 'seed_ventes_scenario --what echeances'."
            )

        paiement = PaiementClient.objects.create(
            client=client,
            branche=branche,
            date_paiement=date_paiement,
            montant=montant,
            mode_paiement=mode_paiement,
            reference_paiement=ref_paiement,
            created_by=admin,
        )

        PaiementClientAllocation.objects.create(
            paiement=paiement,
            facture=facture,
            montant_alloue=montant,
        )

        # BR-FAC-03 : allocation manuelle classique — on reproduit ce que la
        # vue de paiement ferait (mise à jour de montant_regle puis
        # recalculer_solde()).
        facture.montant_regle = facture.montant_regle + montant
        facture.save(update_fields=["montant_regle"])
        facture.recalculer_solde()

        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Paiement solde {client_nom:<28}  {montant} DZD ({date_paiement}) "
                f"→ {facture.reference} : reste_a_payer={facture.reste_a_payer} DZD, "
                f"statut={facture.get_statut_display()}"
            )
        )
        self.stdout.write("\n  Paiement solde : 1 créé\n")
