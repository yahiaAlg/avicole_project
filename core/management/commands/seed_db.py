"""
management/commands/seed_db.py

Comprehensive database population command for the Élevage Avicole system.

Usage:
    python manage.py seed_db                    # full seed (idempotent)
    python manage.py seed_db --mode minimal     # master-data only (no operational records)
    python manage.py seed_db --mode demo        # full demo data (default)
    python manage.py seed_db --clear            # wipe operational data then re-seed
    python manage.py seed_db --clear --all      # wipe EVERYTHING (including master data) then re-seed

Idempotent: safe to run multiple times — uses get_or_create everywhere.
All monetary amounts are in DZD. Dates are relative to today so the demo
always feels current.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def today() -> date:
    return date.today()


def d(days_ago: int) -> date:
    return today() - timedelta(days=days_ago)


def rnd(lo: float, hi: float, decimals: int = 2) -> Decimal:
    return Decimal(str(round(random.uniform(lo, hi), decimals)))


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Seed the database with master data and (optionally) demo operational records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["minimal", "demo"],
            default="demo",
            help="'minimal' = master data only; 'demo' = full operational demo data.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing operational data before seeding.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="clear_all",
            help="Together with --clear: also delete master data (categories, users, etc.).",
        )

    # ------------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        mode = options["mode"]
        clear = options["clear"]
        clear_all = options["clear_all"]

        if clear:
            self._clear(all_data=clear_all)

        self.stdout.write(
            self.style.MIGRATE_HEADING("\n=== ÉLEVAGE AVICOLE — Database Seed ===\n")
        )

        # ── Master data (always seeded) ──────────────────────────────────
        self._seed_company()
        self._seed_users()
        categories_intrant = self._seed_categories_intrant()
        types_fournisseur = self._seed_types_fournisseur()
        categories_depense = self._seed_categories_depense()
        fournisseurs = self._seed_fournisseurs(types_fournisseur)
        clients = self._seed_clients()
        batiments = self._seed_batiments()
        intrants = self._seed_intrants(categories_intrant, fournisseurs)
        produits_finis = self._seed_produits_finis()

        if mode == "minimal":
            self.stdout.write(
                self.style.SUCCESS("\n✓ Minimal seed complete (master data only).\n")
            )
            return

        # ── Operational / demo data ──────────────────────────────────────
        self._seed_stock_initial(intrants)
        bl_fournisseurs = self._seed_bl_fournisseurs(fournisseurs, intrants)
        factures_fournisseur = self._seed_factures_fournisseur(
            fournisseurs, bl_fournisseurs
        )
        self._seed_reglements_fournisseur(fournisseurs, factures_fournisseur)

        lots = self._seed_lots(fournisseurs, batiments)
        self._seed_mortalites(lots)
        self._seed_consommations(lots, intrants)
        productions = self._seed_productions(lots, produits_finis)

        bl_clients = self._seed_bl_clients(clients, produits_finis)
        factures_client = self._seed_factures_client(clients, bl_clients)
        self._seed_paiements_client(clients, factures_client)

        self._seed_depenses(categories_depense, lots, factures_fournisseur)

        self.stdout.write(self.style.SUCCESS("\n✓ Full demo seed complete.\n"))

    # ------------------------------------------------------------------
    # Clear helpers
    # ------------------------------------------------------------------

    def _clear(self, all_data: bool):
        self.stdout.write(self.style.WARNING("  Clearing operational data..."))
        # Import here to avoid circular import before Django is ready
        from depenses.models import Depense
        from clients.models import (
            PaiementClientAllocation,
            PaiementClient,
            FactureClient,
            BLClientLigne,
            BLClient,
        )
        from achats.models import (
            AllocationReglement,
            ReglementFournisseur,
            AcompteFournisseur,
            FactureFournisseur,
            BLFournisseurLigne,
            BLFournisseur,
        )
        from elevage.models import Consommation, Mortalite, LotElevage
        from production.models import ProductionLigne, ProductionRecord
        from stock.models import (
            StockMouvement,
            StockAjustement,
            StockProduitFini,
            StockIntrant,
        )

        # Ordered to respect FK constraints
        Depense.objects.all().delete()
        PaiementClientAllocation.objects.all().delete()
        PaiementClient.objects.all().delete()
        FactureClient.objects.all().delete()
        BLClientLigne.objects.all().delete()
        BLClient.objects.all().delete()
        AllocationReglement.objects.all().delete()
        ReglementFournisseur.objects.all().delete()
        AcompteFournisseur.objects.all().delete()
        FactureFournisseur.objects.all().delete()
        BLFournisseurLigne.objects.all().delete()
        BLFournisseur.objects.all().delete()
        Consommation.objects.all().delete()
        Mortalite.objects.all().delete()
        LotElevage.objects.all().delete()
        ProductionLigne.objects.all().delete()
        ProductionRecord.objects.all().delete()
        StockMouvement.objects.all().delete()
        StockAjustement.objects.all().delete()
        StockProduitFini.objects.all().delete()
        StockIntrant.objects.all().delete()

        if all_data:
            self.stdout.write(self.style.WARNING("  Clearing master data..."))
            from intrants.models import (
                Intrant,
                Batiment,
                Fournisseur,
                TypeFournisseur,
                CategorieIntrant,
            )
            from production.models import ProduitFini
            from depenses.models import CategorieDepense
            from clients.models import Client
            from core.models import CompanyInfo, UserProfile

            Intrant.objects.all().delete()
            Batiment.objects.all().delete()
            Fournisseur.objects.all().delete()
            TypeFournisseur.objects.all().delete()
            CategorieIntrant.objects.all().delete()
            ProduitFini.objects.all().delete()
            CategorieDepense.objects.all().delete()
            Client.objects.all().delete()
            UserProfile.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            CompanyInfo.objects.all().delete()

        self.stdout.write("  Done.\n")

    # ------------------------------------------------------------------
    # ── MASTER DATA ────────────────────────────────────────────────────
    # ------------------------------------------------------------------

    def _seed_company(self):
        from core.models import CompanyInfo

        obj, created = CompanyInfo.objects.get_or_create(
            pk=1,
            defaults=dict(
                nom="Élevage Avicole Setifien",
                adresse="Zone Industrielle, Route Nationale 12",
                wilaya="Setifien",
                telephone="0555 123 456",
                telephone_2="0770 987 654",
                email="contact@avicole-to.dz",
                nif="099123456789012",
                rc="16/00-1234567 B 12",
                ai="16123456789",
                nis="099123456789012345",
                regime_fiscal=CompanyInfo.REGIME_REEL,
                assujetti_tva=True,
                taux_tva=Decimal("19.00"),
                rib="00799999000123456789 12",
                banque="BNA — Agence Setifien Centre",
                devise="DZD",
                pied_de_page=(
                    "Merci de votre confiance — Élevage Avicole Setifien\n"
                    "Tél : 0555 123 456 | Email : contact@avicole-to.dz"
                ),
                prefixe_bl_client="BLC",
                prefixe_bl_fournisseur="BLF",
                prefixe_facture_client="FAC",
                prefixe_facture_fournisseur="FRN",
            ),
        )
        self._log("CompanyInfo", created)
        return obj

    def _seed_users(self):
        from core.models import UserProfile

        users_spec = [
            dict(
                username="admin",
                first_name="Karim",
                last_name="Meziani",
                email="admin@avicole.dz",
                is_superuser=True,
                is_staff=True,
                role="admin",
                password="admin1234",
            ),
            dict(
                username="gerant",
                first_name="Lynda",
                last_name="Aoudia",
                email="gerant@avicole.dz",
                is_superuser=False,
                is_staff=True,
                role="manager",
                password="gerant1234",
            ),
            dict(
                username="operateur1",
                first_name="Samir",
                last_name="Boudiaf",
                email="op1@avicole.dz",
                is_superuser=False,
                is_staff=False,
                role="operateur",
                password="op1_1234",
            ),
            dict(
                username="comptable",
                first_name="Nadia",
                last_name="Hamdi",
                email="comptable@avicole.dz",
                is_superuser=False,
                is_staff=False,
                role="comptable",
                password="compta1234",
            ),
        ]
        created_count = 0
        for spec in users_spec:
            role = spec.pop("role")
            password = spec.pop("password")
            user, created = User.objects.get_or_create(
                username=spec["username"],
                defaults=spec,
            )
            if created:
                user.set_password(password)
                user.save()
                created_count += 1
            UserProfile.objects.get_or_create(user=user, defaults={"role": role})
        self._log(f"Users ({len(users_spec)})", created_count > 0)

    # ------------------------------------------------------------------

    def _seed_categories_intrant(self):
        from intrants.models import CategorieIntrant

        seeds = [
            dict(code="ALIMENT", libelle="Aliment", consommable_en_lot=True, ordre=1),
            dict(
                code="POUSSIN",
                libelle="Poussin (Volaille vivante)",
                consommable_en_lot=False,
                ordre=2,
            ),
            dict(
                code="MEDICAMENT",
                libelle="Médicament / Vétérinaire",
                consommable_en_lot=True,
                ordre=3,
            ),
            dict(
                code="AUTRE", libelle="Autre intrant", consommable_en_lot=False, ordre=4
            ),
        ]
        result = {}
        for s in seeds:
            obj, created = CategorieIntrant.objects.get_or_create(
                code=s["code"], defaults=s
            )
            result[s["code"]] = obj
        self._log("CategorieIntrant (4)", True)
        return result

    def _seed_types_fournisseur(self):
        from intrants.models import TypeFournisseur

        seeds = [
            dict(code="ALIMENTS", libelle="Aliments", ordre=1),
            dict(code="POUSSINS", libelle="Poussins", ordre=2),
            dict(code="MEDICAMENTS", libelle="Médicaments / Vétérinaires", ordre=3),
            dict(code="SERVICES", libelle="Services", ordre=4),
            dict(code="AUTRE", libelle="Autre", ordre=5),
        ]
        result = {}
        for s in seeds:
            obj, _ = TypeFournisseur.objects.get_or_create(code=s["code"], defaults=s)
            result[s["code"]] = obj
        self._log("TypeFournisseur (5)", True)
        return result

    def _seed_categories_depense(self):
        from depenses.models import CategorieDepense

        seeds = [
            dict(
                code="SALAIRES",
                libelle="Salaires & Main-d'œuvre",
                ordre=1,
                description="Salaires des ouvriers et journaliers",
            ),
            dict(
                code="ENERGIE",
                libelle="Énergie (Électricité / Gaz)",
                ordre=2,
                description="Factures d'électricité, gaz, fioul chauffage",
            ),
            dict(
                code="MAINTENANCE",
                libelle="Maintenance & Réparations",
                ordre=3,
                description="Réparations équipements, bâtiments",
            ),
            dict(
                code="TRANSPORT",
                libelle="Transport & Carburant",
                ordre=4,
                description="Carburant véhicules de livraison",
            ),
            dict(
                code="VETERINAIRE",
                libelle="Frais Vétérinaires",
                ordre=5,
                description="Honoraires vétérinaires (hors médicaments)",
            ),
            dict(
                code="FOURNITURES",
                libelle="Fournitures & Emballages",
                ordre=6,
                description="Sacs, caisses, packaging, fournitures bureau",
            ),
            dict(
                code="TAXES",
                libelle="Taxes & Impôts",
                ordre=7,
                description="Taxes locales, patente, droits divers",
            ),
            dict(
                code="DIVERS",
                libelle="Dépenses diverses",
                ordre=8,
                description="Toute dépense non couverte ci-dessus",
            ),
        ]
        result = {}
        for s in seeds:
            obj, _ = CategorieDepense.objects.get_or_create(code=s["code"], defaults=s)
            result[s["code"]] = obj
        self._log("CategorieDepense (8)", True)
        return result

    # ------------------------------------------------------------------

    def _seed_fournisseurs(self, types):
        from intrants.models import Fournisseur

        specs = [
            dict(
                nom="ONAB Setifien",
                adresse="Route de Boghni, Setifien",
                wilaya="Setifien",
                telephone="026 12 34 56",
                type_code="ALIMENTS",
                nif="099000000001",
                rc="16/00-0000001 B 01",
            ),
            dict(
                nom="Couvoirs du Centre — CCA",
                adresse="Zone Agro-industrielle, Blida",
                wilaya="Blida",
                telephone="025 55 66 77",
                type_code="POUSSINS",
                nif="009000000002",
                rc="09/00-0000002 B 02",
            ),
            dict(
                nom="Sanofi Algérie (Vétérinaire)",
                adresse="Rue Hassiba Ben Bouali, Alger",
                wilaya="Alger",
                telephone="021 99 00 11",
                type_code="MEDICAMENTS",
                nif="016000000003",
                rc="16/00-0000003 B 03",
            ),
            dict(
                nom="Proxi-Aliments Boumerdès",
                adresse="Cité Industrielle, Boumerdès",
                wilaya="Boumerdès",
                telephone="024 88 77 66",
                type_code="ALIMENTS",
                nif="035000000004",
                rc="35/00-0000004 B 04",
            ),
            dict(
                nom="Techno-Avicole Services",
                adresse="Rue du 1er Novembre, Alger",
                wilaya="Alger",
                telephone="021 44 55 66",
                type_code="SERVICES",
                nif="016000000005",
                rc="16/00-0000005 B 05",
            ),
        ]
        result = {}
        for s in specs:
            type_code = s.pop("type_code")
            obj, created = Fournisseur.objects.get_or_create(
                nom=s["nom"],
                defaults={**s, "type_principal": types[type_code]},
            )
            result[obj.nom] = obj
        self._log(f"Fournisseurs ({len(specs)})", True)
        return result

    def _seed_clients(self):
        from clients.models import Client

        specs = [
            dict(
                nom="Marché de Gros Setifien",
                type_client="grossiste",
                wilaya="Setifien",
                telephone="0555 11 22 33",
                plafond_credit=Decimal("500000"),
            ),
            dict(
                nom="Restaurant Le Palmier",
                type_client="restauration",
                wilaya="Setifien",
                telephone="0770 22 33 44",
                plafond_credit=Decimal("150000"),
            ),
            dict(
                nom="Boucherie Amrane & Fils",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0660 33 44 55",
                plafond_credit=Decimal("200000"),
            ),
            dict(
                nom="Épicerie Centrale Azazga",
                type_client="detaillant",
                wilaya="Setifien",
                telephone="0555 44 55 66",
                plafond_credit=Decimal("80000"),
            ),
            dict(
                nom="Grossiste Alger Sud",
                type_client="grossiste",
                wilaya="Alger",
                telephone="021 88 77 66",
                plafond_credit=Decimal("1000000"),
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = Client.objects.get_or_create(nom=s["nom"], defaults=s)
            result[obj.nom] = obj
        self._log(f"Clients ({len(specs)})", True)
        return result

    def _seed_batiments(self):
        from intrants.models import Batiment

        specs = [
            dict(
                nom="Bâtiment A",
                capacite=5000,
                description="Poulailler principal — ventilation dynamique",
            ),
            dict(
                nom="Bâtiment B",
                capacite=4000,
                description="Poulailler secondaire — ventilation naturelle",
            ),
            dict(
                nom="Bâtiment C",
                capacite=6000,
                description="Nouveau poulailler — isolation renforcée",
            ),
            dict(
                nom="Dépôt Aliments",
                capacite=None,
                description="Entrepôt stockage aliments et intrants",
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = Batiment.objects.get_or_create(nom=s["nom"], defaults=s)
            result[obj.nom] = obj
        self._log(f"Bâtiments ({len(specs)})", True)
        return result

    def _seed_intrants(self, cats, fournisseurs):
        from intrants.models import Intrant

        specs = [
            # ── Aliments ──────────────────────────────────────
            dict(
                code="ALIM-DEM",
                cat="ALIMENT",
                designation="Aliment Démarrage 1er Âge (0–14j)",
                unite="sac",
                seuil=10,
                fnoms=["ONAB Setifien", "Proxi-Aliments Boumerdès"],
            ),
            dict(
                code="ALIM-CRO",
                cat="ALIMENT",
                designation="Aliment Croissance 2ème Âge (15–28j)",
                unite="sac",
                seuil=15,
                fnoms=["ONAB Setifien", "Proxi-Aliments Boumerdès"],
            ),
            dict(
                code="ALIM-FIN",
                cat="ALIMENT",
                designation="Aliment Finition 3ème Âge (29j+)",
                unite="sac",
                seuil=20,
                fnoms=["ONAB Setifien"],
            ),
            # ── Poussins ─────────────────────────────────────
            dict(
                code="POUSS-R308",
                cat="POUSSIN",
                designation="Poussin Ross 308 (1 jour)",
                unite="unite",
                seuil=100,
                fnoms=["Couvoirs du Centre — CCA"],
            ),
            dict(
                code="POUSS-C500",
                cat="POUSSIN",
                designation="Poussin Cobb 500 (1 jour)",
                unite="unite",
                seuil=100,
                fnoms=["Couvoirs du Centre — CCA"],
            ),
            # ── Médicaments ──────────────────────────────────
            dict(
                code="MED-NEWC",
                cat="MEDICAMENT",
                designation="Vaccin Newcastle (Hitchner B1)",
                unite="dose",
                seuil=500,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-GCOR",
                cat="MEDICAMENT",
                designation="Vaccin Gumboro (IBD Intermediate)",
                unite="dose",
                seuil=500,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-AMOX",
                cat="MEDICAMENT",
                designation="Amoxicilline 50% poudre",
                unite="g",
                seuil=200,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            dict(
                code="MED-VITA",
                cat="MEDICAMENT",
                designation="Vitamines + Électrolytes (complexe)",
                unite="litre",
                seuil=5,
                fnoms=["Sanofi Algérie (Vétérinaire)"],
            ),
            # ── Autres ───────────────────────────────────────
            dict(
                code="AUT-LITIERE",
                cat="AUTRE",
                designation="Litière (copeaux de bois)",
                unite="sac",
                seuil=20,
                fnoms=[],
            ),
        ]
        result = {}
        for s in specs:
            cat = cats[s["cat"]]
            obj, _ = Intrant.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    categorie=cat,
                    unite_mesure=s["unite"],
                    seuil_alerte=Decimal(str(s["seuil"])),
                    actif=True,
                ),
            )
            for fname in s["fnoms"]:
                if fname in fournisseurs:
                    obj.fournisseurs.add(fournisseurs[fname])
            result[s["code"]] = obj
        self._log(f"Intrants ({len(specs)})", True)
        return result

    def _seed_produits_finis(self):
        from production.models import ProduitFini

        specs = [
            dict(
                designation="Poulet vivant (plein poids)",
                type_produit="volaille_vivante",
                unite="unite",
                prix=480,
            ),
            dict(
                designation="Carcasse entière vidée",
                type_produit="carcasse",
                unite="kg",
                prix=750,
            ),
            dict(
                designation="Blanc de poulet",
                type_produit="decoupe",
                unite="kg",
                prix=1100,
            ),
            dict(
                designation="Cuisse entière",
                type_produit="decoupe",
                unite="kg",
                prix=780,
            ),
            dict(
                designation="Aile de poulet",
                type_produit="decoupe",
                unite="kg",
                prix=620,
            ),
            dict(
                designation="Foie de poulet", type_produit="abats", unite="kg", prix=420
            ),
            dict(
                designation="Gésier de poulet",
                type_produit="abats",
                unite="kg",
                prix=350,
            ),
        ]
        result = {}
        for s in specs:
            obj, _ = ProduitFini.objects.get_or_create(
                designation=s["designation"],
                defaults=dict(
                    type_produit=s["type_produit"],
                    unite_mesure=s["unite"],
                    prix_vente_defaut=Decimal(str(s["prix"])),
                    actif=True,
                ),
            )
            result[s["designation"]] = obj
        self._log(f"ProduitsFinis ({len(specs)})", True)
        return result

    # ------------------------------------------------------------------
    # ── OPERATIONAL / DEMO DATA ────────────────────────────────────────
    # ------------------------------------------------------------------

    def _seed_stock_initial(self, intrants):
        """
        Bootstrap StockIntrant rows (one-to-one with each Intrant).
        The balance here represents opening stock BEFORE any BL.
        In a real migration you'd use StockAjustement records instead;
        for seeding we write directly to stay simple.
        """
        from stock.models import StockIntrant

        opening = {
            "ALIM-DEM": (Decimal("80"), Decimal("1850.00")),
            "ALIM-CRO": (Decimal("120"), Decimal("1750.00")),
            "ALIM-FIN": (Decimal("60"), Decimal("1700.00")),
            "POUSS-R308": (Decimal("0"), Decimal("0")),
            "POUSS-C500": (Decimal("0"), Decimal("0")),
            "MED-NEWC": (Decimal("2000"), Decimal("18.50")),
            "MED-GCOR": (Decimal("1500"), Decimal("22.00")),
            "MED-AMOX": (Decimal("800"), Decimal("45.00")),
            "MED-VITA": (Decimal("20"), Decimal("1200.00")),
            "AUT-LITIERE": (Decimal("30"), Decimal("600.00")),
        }
        for code, intrant in intrants.items():
            qty, pmp = opening.get(code, (Decimal("0"), Decimal("0")))
            StockIntrant.objects.get_or_create(
                intrant=intrant,
                defaults={"quantite": qty, "prix_moyen_pondere": pmp},
            )
        self._log(f"StockIntrant (opening balances for {len(opening)} intrants)", True)

    # ------------------------------------------------------------------

    def _seed_bl_fournisseurs(self, fournisseurs, intrants):
        """
        Create 6 BL Fournisseurs covering aliments, poussins, and médicaments.
        2 are in 'reçu' state (ready to invoice), 2 are already 'facturé',
        1 is still a 'brouillon', 1 is 'en litige'.
        """
        from achats.models import BLFournisseur, BLFournisseurLigne
        from stock.models import StockIntrant

        bl_specs = [
            dict(
                ref="BLF-2025-001",
                fnom="ONAB Setifien",
                date_ago=60,
                statut="facture",
                lines=[
                    ("ALIM-DEM", Decimal("100"), Decimal("1800.00")),
                    ("ALIM-CRO", Decimal("150"), Decimal("1720.00")),
                ],
            ),
            dict(
                ref="BLF-2025-002",
                fnom="Couvoirs du Centre — CCA",
                date_ago=55,
                statut="facture",
                lines=[
                    ("POUSS-R308", Decimal("5000"), Decimal("42.00")),
                ],
            ),
            dict(
                ref="BLF-2025-003",
                fnom="Sanofi Algérie (Vétérinaire)",
                date_ago=50,
                statut="recu",
                lines=[
                    ("MED-NEWC", Decimal("5000"), Decimal("18.00")),
                    ("MED-GCOR", Decimal("3000"), Decimal("21.50")),
                    ("MED-AMOX", Decimal("2000"), Decimal("44.00")),
                ],
            ),
            dict(
                ref="BLF-2025-004",
                fnom="ONAB Setifien",
                date_ago=30,
                statut="recu",
                lines=[
                    ("ALIM-DEM", Decimal("80"), Decimal("1850.00")),
                    ("ALIM-CRO", Decimal("100"), Decimal("1760.00")),
                    ("ALIM-FIN", Decimal("120"), Decimal("1690.00")),
                ],
            ),
            dict(
                ref="BLF-2025-005",
                fnom="Couvoirs du Centre — CCA",
                date_ago=20,
                statut="recu",
                lines=[
                    ("POUSS-C500", Decimal("4000"), Decimal("45.00")),
                ],
            ),
            dict(
                ref="BLF-2025-006",
                fnom="Proxi-Aliments Boumerdès",
                date_ago=10,
                statut="brouillon",
                lines=[
                    ("ALIM-FIN", Decimal("200"), Decimal("1700.00")),
                ],
            ),
            dict(
                ref="BLF-2025-007",
                fnom="ONAB Setifien",
                date_ago=45,
                statut="litige",
                lines=[
                    ("ALIM-CRO", Decimal("50"), Decimal("1750.00")),
                ],
            ),
        ]

        admin = User.objects.filter(is_superuser=True).first()
        result = {}
        for spec in bl_specs:
            bl, created = BLFournisseur.objects.get_or_create(
                reference=spec["ref"],
                defaults=dict(
                    fournisseur=fournisseurs[spec["fnom"]],
                    date_bl=d(spec["date_ago"]),
                    statut=spec["statut"],
                    created_by=admin,
                ),
            )
            if created:
                for icode, qty, pu in spec["lines"]:
                    BLFournisseurLigne.objects.create(
                        bl=bl,
                        intrant=intrants[icode],
                        quantite=qty,
                        prix_unitaire=pu,
                    )
                    # Update StockIntrant for received BLs (simulate signal)
                    if spec["statut"] in ("recu", "facture"):
                        si, _ = __import__(
                            "stock.models", fromlist=["StockIntrant"]
                        ).StockIntrant.objects.get_or_create(
                            intrant=intrants[icode],
                            defaults={
                                "quantite": Decimal("0"),
                                "prix_moyen_pondere": pu,
                            },
                        )
                        old_qty = si.quantite
                        old_pmp = si.prix_moyen_pondere
                        new_qty = old_qty + qty
                        # Weighted average update
                        if new_qty > 0:
                            si.prix_moyen_pondere = (
                                (old_qty * old_pmp + qty * pu) / new_qty
                            ).quantize(Decimal("0.0001"))
                        si.quantite = new_qty
                        si.save()
            result[spec["ref"]] = bl

        self._log(f"BLFournisseurs ({len(bl_specs)})", True)
        return result

    def _seed_factures_fournisseur(self, fournisseurs, bls):
        """
        Create 2 Factures Fournisseurs from the 'facture'-status BLs.
        One fully paid, one partially paid.
        """
        from achats.models import FactureFournisseur

        admin = User.objects.filter(is_superuser=True).first()

        # Compute montant from BLs
        bl1 = bls["BLF-2025-001"]
        bl2 = bls["BLF-2025-002"]

        mt1 = sum(l.montant_total for l in bl1.lignes.all())  # aliments
        mt2 = sum(l.montant_total for l in bl2.lignes.all())  # poussins

        result = {}

        # Facture 1 — Aliments (ONAB) — fully paid
        f1, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-001",
            defaults=dict(
                fournisseur=fournisseurs["ONAB Setifien"],
                date_facture=d(58),
                date_echeance=d(28),
                type_facture="marchandises",
                montant_total=mt1,
                montant_regle=mt1,
                reste_a_payer=Decimal("0"),
                statut=FactureFournisseur.STATUT_PAYE,
                created_by=admin,
            ),
        )
        if created:
            f1.bls.set([bl1])
        result["FRN-2025-001"] = f1

        # Facture 2 — Poussins (CCA) — partially paid
        f2, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-002",
            defaults=dict(
                fournisseur=fournisseurs["Couvoirs du Centre — CCA"],
                date_facture=d(53),
                date_echeance=d(23),
                type_facture="marchandises",
                montant_total=mt2,
                montant_regle=(mt2 / 2).quantize(Decimal("0.01")),
                reste_a_payer=(mt2 / 2).quantize(Decimal("0.01")),
                statut=FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
                created_by=admin,
            ),
        )
        if created:
            f2.bls.set([bl2])
        result["FRN-2025-002"] = f2

        # Facture 3 — Service (Techno-Avicole) — unpaid (for dépense link demo)
        f3, created = FactureFournisseur.objects.get_or_create(
            reference="FRN-2025-003",
            defaults=dict(
                fournisseur=fournisseurs["Techno-Avicole Services"],
                date_facture=d(15),
                date_echeance=d(0),
                type_facture="service",
                montant_total=Decimal("45000.00"),
                montant_regle=Decimal("0"),
                reste_a_payer=Decimal("45000.00"),
                statut=FactureFournisseur.STATUT_NON_PAYE,
                created_by=admin,
            ),
        )
        result["FRN-2025-003"] = f3

        self._log("FacturesFournisseurs (3)", True)
        return result

    def _seed_reglements_fournisseur(self, fournisseurs, factures):
        """
        Seed règlements + allocations for the supplier settlement chain.
        Simulates the FIFO engine output manually.
        """
        from achats.models import ReglementFournisseur, AllocationReglement

        admin = User.objects.filter(is_superuser=True).first()

        f1 = factures["FRN-2025-001"]
        f2 = factures["FRN-2025-002"]

        # Règlement 1 — fully covers FRN-2025-001
        r1, created = ReglementFournisseur.objects.get_or_create(
            fournisseur=fournisseurs["ONAB Setifien"],
            date_reglement=d(40),
            defaults=dict(
                montant=f1.montant_total,
                mode_paiement="cheque",
                reference_paiement="CHQ-0012345",
                created_by=admin,
            ),
        )
        if created:
            AllocationReglement.objects.create(
                reglement=r1, facture=f1, montant_alloue=f1.montant_total
            )

        # Règlement 2 — partial on FRN-2025-002 (half the amount)
        r2, created = ReglementFournisseur.objects.get_or_create(
            fournisseur=fournisseurs["Couvoirs du Centre — CCA"],
            date_reglement=d(35),
            defaults=dict(
                montant=f2.montant_regle,
                mode_paiement="especes",
                created_by=admin,
            ),
        )
        if created:
            AllocationReglement.objects.create(
                reglement=r2, facture=f2, montant_alloue=f2.montant_regle
            )

        self._log("ReglementsFournisseurs (2) + Allocations", True)

    # ------------------------------------------------------------------

    def _seed_lots(self, fournisseurs, batiments):
        """
        Seed 3 lots:
          - Lot A (fermé, 45 days ago)
          - Lot B (ouvert, 30 days ago)
          - Lot C (ouvert, 10 days ago)
        """
        from elevage.models import LotElevage

        admin = User.objects.filter(is_superuser=True).first()
        cca = fournisseurs["Couvoirs du Centre — CCA"]

        specs = [
            dict(
                designation="Lot Janvier 2025 — Bâtiment A",
                date_ouverture=d(75),
                date_fermeture=d(30),
                statut=LotElevage.STATUT_FERME,
                nombre_poussins_initial=5000,
                fournisseur_poussins=cca,
                batiment=batiments["Bâtiment A"],
                souche="Ross 308",
            ),
            dict(
                designation="Lot Mars 2025 — Bâtiment B",
                date_ouverture=d(40),
                date_fermeture=None,
                statut=LotElevage.STATUT_OUVERT,
                nombre_poussins_initial=4000,
                fournisseur_poussins=cca,
                batiment=batiments["Bâtiment B"],
                souche="Cobb 500",
            ),
            dict(
                designation="Lot Avril 2025 — Bâtiment C",
                date_ouverture=d(10),
                date_fermeture=None,
                statut=LotElevage.STATUT_OUVERT,
                nombre_poussins_initial=6000,
                fournisseur_poussins=cca,
                batiment=batiments["Bâtiment C"],
                souche="Ross 308",
            ),
        ]

        result = {}
        for s in specs:
            obj, _ = LotElevage.objects.get_or_create(
                designation=s["designation"],
                defaults={**s, "created_by": admin},
            )
            result[s["designation"]] = obj
        self._log(f"Lots d'élevage ({len(specs)})", True)
        return result

    def _seed_mortalites(self, lots):
        from elevage.models import Mortalite, LotElevage

        mortalite_data = {
            "Lot Janvier 2025 — Bâtiment A": [
                # (days_ago, count, cause)
                (73, 12, "Mort-né / faiblesse"),
                (70, 8, "Infection respiratoire"),
                (65, 5, ""),
                (60, 15, "Coccidiose suspectée"),
                (50, 3, ""),
                (40, 6, "Choc thermique"),
                (35, 4, ""),
            ],
            "Lot Mars 2025 — Bâtiment B": [
                (38, 10, "Mort-né / faiblesse"),
                (35, 6, ""),
                (28, 8, "Infection respiratoire"),
                (20, 4, ""),
                (12, 3, ""),
            ],
            "Lot Avril 2025 — Bâtiment C": [
                (8, 15, "Mort-né / faiblesse"),
                (5, 7, ""),
            ],
        }

        count = 0
        for designation, records in mortalite_data.items():
            lot = lots.get(designation)
            if not lot:
                continue
            for days_ago, nombre, cause in records:
                Mortalite.objects.get_or_create(
                    lot=lot,
                    date=d(days_ago),
                    nombre=nombre,
                    defaults={"cause": cause},
                )
                count += 1
        self._log(f"Mortalités ({count} records)", True)

    def _seed_consommations(self, lots, intrants):
        """
        Seed realistic daily feed + medicine consumption for each lot.
        Pattern: starter feed first 2 weeks, grower next 2 weeks, finisher after.
        """
        from elevage.models import Consommation, LotElevage

        admin = User.objects.filter(is_superuser=True).first()

        def _cons_for_lot(lot, open_ago, close_ago_or_none, initial_birds, suffix_key):
            """Generate consumption records every 3 days for a lot."""
            records = []
            close_ago = close_ago_or_none if close_ago_or_none else 0
            day = open_ago - 1
            while day >= close_ago:
                age_days = open_ago - day
                # Feed phase
                if age_days <= 14:
                    icode = "ALIM-DEM"
                    qty_per_bird = Decimal("0.030")  # 30g/day at start
                elif age_days <= 28:
                    icode = "ALIM-CRO"
                    qty_per_bird = Decimal("0.090")  # 90g/day growing
                else:
                    icode = "ALIM-FIN"
                    qty_per_bird = Decimal("0.150")  # 150g/day finishing

                # 25-kg sac count (rounded to nearest 0.5 sac)
                sacs = (initial_birds * qty_per_bird / 25).quantize(Decimal("0.5"))
                if sacs > 0:
                    records.append((d(day), intrants[icode], sacs))

                # Medicines: vaccinate at day 7 and 21
                if age_days == 7:
                    records.append(
                        (d(day), intrants["MED-NEWC"], Decimal(str(initial_birds)))
                    )
                if age_days == 21:
                    records.append(
                        (d(day), intrants["MED-GCOR"], Decimal(str(initial_birds)))
                    )
                if age_days in (10, 25):
                    records.append((d(day), intrants["MED-VITA"], Decimal("2")))

                day -= 3  # every 3 days (not daily to keep seed manageable)
            return records

        lot_params = {
            "Lot Janvier 2025 — Bâtiment A": (75, 30, 5000),
            "Lot Mars 2025 — Bâtiment B": (40, None, 4000),
            "Lot Avril 2025 — Bâtiment C": (10, None, 6000),
        }

        count = 0
        for designation, (open_ago, close_ago, birds) in lot_params.items():
            lot = lots.get(designation)
            if not lot:
                continue
            for cdate, intrant, qty in _cons_for_lot(
                lot, open_ago, close_ago, birds, designation
            ):
                Consommation.objects.get_or_create(
                    lot=lot,
                    date=cdate,
                    intrant=intrant,
                    defaults={"quantite": qty, "created_by": admin},
                )
                count += 1
        self._log(f"Consommations ({count} records)", True)

    # ------------------------------------------------------------------

    def _seed_productions(self, lots, produits_finis):
        """
        Create two production records:
          - Lot A (fermé, ~31 days ago) — full harvest covering all product types
          - Lot B (ouvert, ~5 days ago)  — partial mid-cycle harvest

        FIX: records are created as BROUILLON first, lines are inserted, then
        the record is transitioned to VALIDE so the post_save signal fires with
        lines already present in the DB and correctly increments StockProduitFini.
        The old pattern (create as valide + get_or_create stock manually) left
        every StockProduitFini at 0 because:
          1. The signal fired before lines existed → bailed immediately.
          2. get_or_create found the zero-balance row already created by the
             ProduitFini signal and did nothing.
        """
        from production.models import ProductionRecord, ProductionLigne

        admin = User.objects.filter(is_superuser=True).first()

        # ── Shortcuts ──────────────────────────────────────────────────
        pf = produits_finis  # alias for readability
        lot_a = lots.get("Lot Janvier 2025 — Bâtiment A")
        lot_b = lots.get("Lot Mars 2025 — Bâtiment B")

        productions_created = 0

        # ── Production A — Lot Janvier (fermé) ─────────────────────────
        # 4 947 birds harvested:  3 000 sold live, 1 947 processed into cuts
        if lot_a:
            pr_a, created = ProductionRecord.objects.get_or_create(
                lot=lot_a,
                date_production=d(31),
                defaults=dict(
                    nombre_oiseaux_abattus=4947,
                    poids_total_kg=Decimal("10389.00"),
                    poids_moyen_kg=Decimal("2.100"),
                    statut=ProductionRecord.STATUT_BROUILLON,  # ← brouillon first
                    created_by=admin,
                ),
            )
            if created:
                # All 7 seeded product types represented so the catalogue
                # looks populated from day one.
                lines_a = [
                    # (produit_fini,               qty,              poids_unit, cout_unit)
                    (
                        pf["Poulet vivant (plein poids)"],
                        Decimal("3000"),
                        Decimal("2.100"),
                        Decimal("89.00"),
                    ),
                    (
                        pf["Carcasse entière vidée"],
                        Decimal("800"),
                        Decimal("1.650"),
                        Decimal("72.00"),
                    ),
                    (
                        pf["Blanc de poulet"],
                        Decimal("480"),
                        Decimal("0.350"),
                        Decimal("68.00"),
                    ),
                    (
                        pf["Cuisse entière"],
                        Decimal("600"),
                        Decimal("0.280"),
                        Decimal("55.00"),
                    ),
                    (
                        pf["Aile de poulet"],
                        Decimal("350"),
                        Decimal("0.180"),
                        Decimal("42.00"),
                    ),
                    (
                        pf["Foie de poulet"],
                        Decimal("280"),
                        Decimal("0.090"),
                        Decimal("28.00"),
                    ),
                    (
                        pf["Gésier de poulet"],
                        Decimal("240"),
                        Decimal("0.075"),
                        Decimal("22.00"),
                    ),
                ]
                for produit, qty, poids, cout in lines_a:
                    ProductionLigne.objects.create(
                        production=pr_a,
                        produit_fini=produit,
                        quantite=qty,
                        poids_unitaire_kg=poids,
                        cout_unitaire_estime=cout,
                    )
                # NOW transition to valide → signal fires with lines in DB
                pr_a.statut = ProductionRecord.STATUT_VALIDE
                pr_a.save()
                productions_created += 1

        # ── Production B — Lot Mars (ouvert, partial harvest) ──────────
        if lot_b:
            pr_b, created = ProductionRecord.objects.get_or_create(
                lot=lot_b,
                date_production=d(5),
                defaults=dict(
                    nombre_oiseaux_abattus=1200,
                    poids_total_kg=Decimal("2640.00"),
                    poids_moyen_kg=Decimal("2.200"),
                    statut=ProductionRecord.STATUT_BROUILLON,
                    created_by=admin,
                ),
            )
            if created:
                lines_b = [
                    (
                        pf["Carcasse entière vidée"],
                        Decimal("500"),
                        Decimal("1.700"),
                        Decimal("74.00"),
                    ),
                    (
                        pf["Blanc de poulet"],
                        Decimal("220"),
                        Decimal("0.360"),
                        Decimal("70.00"),
                    ),
                    (
                        pf["Cuisse entière"],
                        Decimal("280"),
                        Decimal("0.290"),
                        Decimal("57.00"),
                    ),
                    (
                        pf["Aile de poulet"],
                        Decimal("160"),
                        Decimal("0.185"),
                        Decimal("44.00"),
                    ),
                    (
                        pf["Foie de poulet"],
                        Decimal("120"),
                        Decimal("0.092"),
                        Decimal("29.00"),
                    ),
                    (
                        pf["Gésier de poulet"],
                        Decimal("100"),
                        Decimal("0.078"),
                        Decimal("23.00"),
                    ),
                ]
                for produit, qty, poids, cout in lines_b:
                    ProductionLigne.objects.create(
                        production=pr_b,
                        produit_fini=produit,
                        quantite=qty,
                        poids_unitaire_kg=poids,
                        cout_unitaire_estime=cout,
                    )
                pr_b.statut = ProductionRecord.STATUT_VALIDE
                pr_b.save()
                productions_created += 1

        self._log(
            f"ProductionRecords ({productions_created} created / validated, stock updated via signal)",
            True,
        )
        return {}

    # ------------------------------------------------------------------

    def _seed_bl_clients(self, clients, produits_finis):
        from clients.models import BLClient, BLClientLigne
        from stock.models import StockProduitFini

        admin = User.objects.filter(is_superuser=True).first()
        vivant = produits_finis["Poulet vivant (plein poids)"]
        carcasse = produits_finis["Carcasse entière vidée"]
        marche = clients["Marché de Gros Setifien"]
        palmier = clients["Restaurant Le Palmier"]
        amrane = clients["Boucherie Amrane & Fils"]

        bl_specs = [
            dict(
                ref="BLC-2025-001",
                client=marche,
                date_ago=28,
                statut="facture",
                lines=[(vivant, Decimal("1500"), Decimal("480"))],
            ),
            dict(
                ref="BLC-2025-002",
                client=palmier,
                date_ago=25,
                statut="facture",
                lines=[(carcasse, Decimal("300"), Decimal("750"))],
            ),
            dict(
                ref="BLC-2025-003",
                client=amrane,
                date_ago=20,
                statut="livre",
                lines=[(carcasse, Decimal("200"), Decimal("750"))],
            ),
            dict(
                ref="BLC-2025-004",
                client=marche,
                date_ago=15,
                statut="livre",
                lines=[(vivant, Decimal("800"), Decimal("490"))],
            ),
            dict(
                ref="BLC-2025-005",
                client=palmier,
                date_ago=5,
                statut="brouillon",
                lines=[(carcasse, Decimal("100"), Decimal("760"))],
            ),
        ]

        result = {}
        for spec in bl_specs:
            bl, created = BLClient.objects.get_or_create(
                reference=spec["ref"],
                defaults=dict(
                    client=spec["client"],
                    date_bl=d(spec["date_ago"]),
                    statut=spec["statut"],
                    created_by=admin,
                ),
            )
            if created:
                for pf, qty, pu in spec["lines"]:
                    BLClientLigne.objects.create(
                        bl=bl, produit_fini=pf, quantite=qty, prix_unitaire=pu
                    )
                    # Deduct from stock for validated BLs
                    if spec["statut"] in ("livre", "facture"):
                        try:
                            spf = StockProduitFini.objects.get(produit_fini=pf)
                            spf.quantite = max(Decimal("0"), spf.quantite - qty)
                            spf.save()
                        except StockProduitFini.DoesNotExist:
                            pass
            result[spec["ref"]] = bl

        self._log(f"BLClients ({len(bl_specs)})", True)
        return result

    def _seed_factures_client(self, clients, bls):
        from clients.models import FactureClient

        admin = User.objects.filter(is_superuser=True).first()

        bl1 = bls["BLC-2025-001"]  # marché
        bl2 = bls["BLC-2025-002"]  # palmier

        mt1_ht = sum(l.montant_total for l in bl1.lignes.all())
        mt2_ht = sum(l.montant_total for l in bl2.lignes.all())
        tva = Decimal("19.00")

        def ttc(ht, taux):
            return (ht * (1 + taux / 100)).quantize(Decimal("0.01"))

        def tva_amt(ht, taux):
            return (ht * taux / 100).quantize(Decimal("0.01"))

        factures = {}

        # Facture 1 — Marché — partially paid
        f1, created = FactureClient.objects.get_or_create(
            reference="FAC-2025-001",
            defaults=dict(
                client=clients["Marché de Gros Setifien"],
                date_facture=d(27),
                date_echeance=d(0),
                montant_ht=mt1_ht,
                taux_tva=tva,
                montant_tva=tva_amt(mt1_ht, tva),
                montant_ttc=ttc(mt1_ht, tva),
                montant_regle=Decimal("400000.00"),
                reste_a_payer=ttc(mt1_ht, tva) - Decimal("400000.00"),
                statut=FactureClient.STATUT_PARTIELLEMENT_PAYEE,
                created_by=admin,
            ),
        )
        if created:
            f1.bls.set([bl1])
        factures["FAC-2025-001"] = f1

        # Facture 2 — Palmier — fully paid
        f2, created = FactureClient.objects.get_or_create(
            reference="FAC-2025-002",
            defaults=dict(
                client=clients["Restaurant Le Palmier"],
                date_facture=d(24),
                date_echeance=d(9),
                montant_ht=mt2_ht,
                taux_tva=tva,
                montant_tva=tva_amt(mt2_ht, tva),
                montant_ttc=ttc(mt2_ht, tva),
                montant_regle=ttc(mt2_ht, tva),
                reste_a_payer=Decimal("0"),
                statut=FactureClient.STATUT_PAYEE,
                created_by=admin,
            ),
        )
        if created:
            f2.bls.set([bl2])
        factures["FAC-2025-002"] = f2

        self._log("FacturesClient (2)", True)
        return factures

    def _seed_paiements_client(self, clients, factures):
        from clients.models import PaiementClient, PaiementClientAllocation

        admin = User.objects.filter(is_superuser=True).first()
        f1 = factures["FAC-2025-001"]
        f2 = factures["FAC-2025-002"]

        # Partial payment on FAC-2025-001
        p1, created = PaiementClient.objects.get_or_create(
            client=clients["Marché de Gros Setifien"],
            date_paiement=d(20),
            montant=Decimal("400000.00"),
            defaults=dict(
                mode_paiement="cheque",
                reference_paiement="CHQ-CLI-0091",
                created_by=admin,
            ),
        )
        if created:
            PaiementClientAllocation.objects.create(
                paiement=p1, facture=f1, montant_alloue=Decimal("400000.00")
            )

        # Full payment on FAC-2025-002
        p2, created = PaiementClient.objects.get_or_create(
            client=clients["Restaurant Le Palmier"],
            date_paiement=d(18),
            montant=f2.montant_ttc,
            defaults=dict(mode_paiement="especes", created_by=admin),
        )
        if created:
            PaiementClientAllocation.objects.create(
                paiement=p2, facture=f2, montant_alloue=f2.montant_ttc
            )

        self._log("PaiementsClient (2) + Allocations", True)

    # ------------------------------------------------------------------

    def _seed_depenses(self, categories, lots, factures):
        from depenses.models import Depense

        admin = User.objects.filter(is_superuser=True).first()
        lot_a = lots.get("Lot Janvier 2025 — Bâtiment A")
        lot_b = lots.get("Lot Mars 2025 — Bâtiment B")
        lot_c = lots.get("Lot Avril 2025 — Bâtiment C")
        f_service = factures.get("FRN-2025-003")  # service invoice for linking demo

        specs = [
            dict(
                date=d(70),
                cat="SALAIRES",
                desc="Salaires ouvriers — Janvier 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_a,
            ),
            dict(
                date=d(70),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Janvier 2025",
                montant=Decimal("32000"),
                mode="cheque",
                lot=lot_a,
            ),
            dict(
                date=d(65),
                cat="VETERINAIRE",
                desc="Visite vétérinaire Lot Janvier",
                montant=Decimal("8500"),
                mode="especes",
                lot=lot_a,
            ),
            dict(
                date=d(60),
                cat="MAINTENANCE",
                desc="Réparation système de ventilation Bât A",
                montant=Decimal("15000"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(55),
                cat="TRANSPORT",
                desc="Carburant livraison — Semaine 10",
                montant=Decimal("4200"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(40),
                cat="SALAIRES",
                desc="Salaires ouvriers — Février 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_b,
            ),
            dict(
                date=d(40),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Février 2025",
                montant=Decimal("29500"),
                mode="cheque",
                lot=lot_b,
            ),
            dict(
                date=d(35),
                cat="FOURNITURES",
                desc="Achat litière supplémentaire",
                montant=Decimal("6000"),
                mode="especes",
                lot=lot_b,
            ),
            dict(
                date=d(30),
                cat="VETERINAIRE",
                desc="Visite vétérinaire Lot Mars",
                montant=Decimal("7000"),
                mode="especes",
                lot=lot_b,
            ),
            dict(
                date=d(20),
                cat="SALAIRES",
                desc="Salaires ouvriers — Mars 2025",
                montant=Decimal("85000"),
                mode="virement",
                lot=lot_c,
            ),
            dict(
                date=d(20),
                cat="ENERGIE",
                desc="Facture Sonelgaz — Mars 2025",
                montant=Decimal("31000"),
                mode="cheque",
                lot=lot_c,
            ),
            dict(
                date=d(15),
                cat="MAINTENANCE",
                desc="Entretien maintenance préventive",
                montant=Decimal("45000"),
                mode="virement",
                lot=None,
                facture_liee=f_service,
            ),  # BR-DEP-03 service invoice link demo
            dict(
                date=d(10),
                cat="TRANSPORT",
                desc="Carburant livraison — Semaine 15",
                montant=Decimal("3800"),
                mode="especes",
                lot=None,
            ),
            dict(
                date=d(5),
                cat="TAXES",
                desc="Taxe locale trimestrielle",
                montant=Decimal("12000"),
                mode="cheque",
                lot=None,
            ),
            dict(
                date=d(2),
                cat="DIVERS",
                desc="Matériel divers entretien",
                montant=Decimal("2200"),
                mode="especes",
                lot=None,
            ),
        ]

        count = 0
        for s in specs:
            Depense.objects.get_or_create(
                date=s["date"],
                categorie=categories[s["cat"]],
                description=s["desc"],
                defaults=dict(
                    montant=s["montant"],
                    mode_paiement=s["mode"],
                    lot=s.get("lot"),
                    facture_liee=s.get("facture_liee"),
                    enregistre_par=admin,
                ),
            )
            count += 1
        self._log(f"Dépenses ({count} records)", True)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _log(self, label: str, created: bool):
        symbol = self.style.SUCCESS("  ✓") if created else self.style.WARNING("  ~")
        self.stdout.write(f"{symbol} {label}")
