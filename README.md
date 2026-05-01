# 🐔 Élevage Avicole — Internal Management System

A full-cycle internal management system for small Algerian poultry farming operations (_élevages avicoles_). Covers everything from chick arrival and daily lot operations through production, client deliveries, invoicing, and supplier settlement — with a strict FIFO accounts-payable engine and complete stock audit trail.

---

## Table of Contents

- [Business Context](#business-context)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Data Model Overview](#data-model-overview)
- [Business Rules Reference](#business-rules-reference)
- [Getting Started](#getting-started)
- [Database Seeding](#database-seeding)
- [Key Workflows](#key-workflows)
- [Roles & Permissions](#roles--permissions)
- [Alerts & Thresholds](#alerts--thresholds)
- [Reporting](#reporting)
- [Development Notes](#development-notes)

---

## Business Context

A small _élevage avicole_ cycles through distinct phases:

```
Supplier → BL Fournisseur → Stock Intrants
                                  ↓
                          Lot d'Élevage (poussins + aliments + médicaments)
                                  ↓ mortalités / consommations quotidiennes
                             Production (abattage)
                                  ↓
                          Stock Produits Finis
                                  ↓
                Client ← BL Client ← Facture Client ← Paiement Client
```

On the supplier side:

```
BL Fournisseur → Facture Fournisseur → Règlement (FIFO auto-allocation) → Solde
```

---

## Features

| Domain                    | What the system does                                                                                                                                 |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Intrants**              | Catalogue of feed, chicks, medicines; stock entry exclusively via validated BL fournisseur                                                           |
| **Lots d'élevage**        | Full batch lifecycle — opening, daily mortality, feed/medicine consumption, closure lockout                                                          |
| **Production**            | Harvest records linking a lot to finished-product stock entries                                                                                      |
| **Stock**                 | Two-segment inventory (intrants + produits finis); weighted average cost; full movement audit trail; manual adjustments with mandatory justification |
| **BL Fournisseur**        | 4-state delivery notes (brouillon → reçu → facturé → litige); sole stock entry point                                                                 |
| **Factures Fournisseur**  | Built by selecting validated BLs — total auto-computed, no manual entry; FIFO settlement engine                                                      |
| **BL Client**             | Client delivery notes; stock deducted on validation; locked when invoiced                                                                            |
| **Factures Client**       | Built from selected BL clients; HT + TVA + TTC breakdown; manual payment allocation                                                                  |
| **Règlement Fournisseur** | Single FIFO engine: user enters a sum, system allocates oldest-invoice-first automatically                                                           |
| **Dépenses**              | Operational expenses strictly separated from AP; optional lot attribution; optional service-invoice link                                             |
| **Documents imprimables** | BL and invoice print views for all supplier and client documents                                                                                     |
| **Alertes**               | Low-stock alerts, overdue invoices, abnormal mortality detection                                                                                     |

---

## Architecture

```
Django 4.x / Python 3.11+
PostgreSQL (recommended) or SQLite (dev)

Apps
├── core/          — CompanyInfo (singleton), UserProfile
├── intrants/      — CategorieIntrant, TypeFournisseur, Fournisseur, Batiment, Intrant
├── stock/         — StockIntrant, StockProduitFini, StockMouvement, StockAjustement
├── elevage/       — LotElevage, Mortalite, Consommation
├── production/    — ProduitFini, ProductionRecord, ProductionLigne
├── achats/        — BLFournisseur, FactureFournisseur, ReglementFournisseur,
│                    AllocationReglement, AcompteFournisseur
├── clients/       — Client, BLClient, FactureClient, PaiementClient,
│                    PaiementClientAllocation
└── depenses/      — CategorieDepense, Depense
```

Stock mutations are **never** applied directly to balance fields. Every change flows through a `StockMouvement` record created by Django signals, giving a complete, immutable audit trail.

---

## Project Structure

```
avicole/
├── avicole/                  # Django project settings
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py
│
├── core/
│   ├── models.py             # CompanyInfo, UserProfile
│   ├── admin.py
│   └── management/
│       └── commands/
│           └── seed_db.py    # ← database seeding command
│
├── intrants/
│   ├── models.py
│   ├── migrations/
│   │   └── 0002_seed_categories.py   # data migration for seed codes
│   └── ...
│
├── stock/
│   ├── models.py
│   ├── signals.py            # stock balance updates on BL/Consommation/Production
│   └── ...
│
├── elevage/
│   ├── models.py
│   ├── signals.py            # poussin stock deduction on lot opening
│   └── ...
│
├── achats/
│   ├── models.py
│   ├── utils.py              # appliquer_reglement_fifo() — single atomic FIFO engine
│   └── ...
│
├── clients/
│   ├── models.py
│   └── ...
│
├── depenses/
│   ├── models.py
│   └── ...
│
├── production/
│   ├── models.py
│   └── ...
│
├── requirements.txt
├── manage.py
└── README.md
```

---

## Data Model Overview

### Intrants & Stock

```
CategorieIntrant (ALIMENT · POUSSIN · MEDICAMENT · AUTRE)
    └─< Intrant >──── StockIntrant  (qty + PMP, updated by signal)
                           └─< StockMouvement  (immutable audit trail)
                 └─< StockAjustement (manual correction, justified)
```

### Supplier Cycle

```
Fournisseur
  └─< BLFournisseur [brouillon|recu|facturé|litige]
        └─< BLFournisseurLigne (intrant + qty + unit price)
              ↓ (validation → StockMouvement entrée)
  └─< FactureFournisseur [non_paye|partiellement_paye|paye|en_litige]
        ├── M2M → BLFournisseur (locked on invoice creation)
        └─< AllocationReglement
              └── ReglementFournisseur  (FIFO engine output)
  └─< AcompteFournisseur  (overpayment surplus)
```

### Production Cycle

```
LotElevage [ouvert|fermé]
  ├─< Mortalite
  ├─< Consommation  (→ StockMouvement sortie)
  └─< ProductionRecord
        └─< ProductionLigne → ProduitFini → StockProduitFini
```

### Client Cycle

```
Client
  └─< BLClient [brouillon|livre|facture|litige]
        └─< BLClientLigne (produit_fini + qty + price)
              ↓ (validation → StockMouvement sortie)
  └─< FactureClient [non_payee|partiellement_payee|payee|en_litige]
        ├── M2M → BLClient
        └─< PaiementClientAllocation
              └── PaiementClient  (user-selected allocation)
```

### Expenses

```
CategorieDepense
  └─< Depense
        ├── FK(optional) → LotElevage     (lot cost attribution)
        └── FK(optional) → FactureFournisseur  (service invoices only — BR-DEP-03)
```

---

## Business Rules Reference

| Code          | Rule                                                            | Enforced in                                      |
| ------------- | --------------------------------------------------------------- | ------------------------------------------------ |
| **BR-BLF-01** | Stock updates only on BL validation (`recu`), never on drafts   | `stock/signals.py`                               |
| **BR-BLF-02** | A `facturé` BL is locked — cannot be edited or re-invoiced      | `BLFournisseur.est_verrouille`                   |
| **BR-FAF-01** | Invoice total = auto-sum of selected BL lines, no manual entry  | `FactureFournisseur.clean()` + view              |
| **BR-FAF-03** | BL set is locked after invoice creation                         | View layer                                       |
| **BR-REG-01** | FIFO allocation: oldest invoice settled first, automatically    | `achats/utils.py` (`transaction.atomic`)         |
| **BR-REG-04** | Overpayment → `AcompteFournisseur`, never discarded             | `achats/utils.py`                                |
| **BR-REG-06** | `AllocationReglement` records are immutable after creation      | Model — no `update()` exposed                    |
| **BR-BLC-01** | `StockProduitFini` decreases only on BL Client validation       | `stock/signals.py`                               |
| **BR-BLC-02** | BL validation blocked if qty > available stock                  | View/form layer                                  |
| **BR-BLC-03** | A `facturé` BL Client is locked                                 | `BLClient.est_verrouille`                        |
| **BR-FAC-01** | Client invoice HT = auto-sum of selected BL lines               | `FactureClient.clean()` + `compute_montant_ht()` |
| **BR-FAC-03** | Client selects which invoice(s) a payment applies to            | `PaiementClientAllocation`                       |
| **BR-DEP-01** | A goods invoice NEVER auto-generates a dépense                  | No automatic link anywhere                       |
| **BR-DEP-03** | `facture_liee` only allowed on `service`-type invoices          | `Depense.clean()`                                |
| **BR-DEP-04** | Dépenses optionally attributed to a lot for profitability       | `Depense.lot` FK                                 |
| **BR-LOT-01** | Closed lots reject new Mortalite/Consommation entries           | `Mortalite.clean()`, `Consommation.clean()`      |
| **BR-STK-01** | Stock never goes negative — all exit paths check before writing | `stock/signals.py` (`select_for_update`)         |

---

## Getting Started

### Prerequisites

- Python ≥ 3.11
- PostgreSQL ≥ 14 (or SQLite for development)
- pip / virtualenv

### Installation

```bash
git clone https://github.com/your-org/avicole.git
cd avicole

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```ini
SECRET_KEY=your-secret-key-here
DEBUG=True
DATABASE_URL=postgres://user:password@localhost:5432/avicole
ALLOWED_HOSTS=localhost,127.0.0.1

# Media files (logos, attachments)
MEDIA_ROOT=/var/avicole/media
MEDIA_URL=/media/
```

### Database Setup

```bash
python manage.py migrate
python manage.py seed_db          # loads all master + demo data
```

### Run

```bash
python manage.py runserver
```

Default admin credentials after seeding: `admin` / `admin1234` — **change immediately in production.**

---

## Database Seeding

The `seed_db` management command populates a fully working demo environment.

```bash
# Full demo seed (idempotent)
python manage.py seed_db

# Master data only — no operational records
python manage.py seed_db --mode minimal

# Clear operational data, then re-seed
python manage.py seed_db --clear

# Full reset — wipes everything including master data
python manage.py seed_db --clear --all
```

**What gets seeded:**

| Layer                      | Records                                                            |
| -------------------------- | ------------------------------------------------------------------ |
| Company                    | 1 CompanyInfo singleton                                            |
| Users                      | 4 (admin, gérant, opérateur, comptable)                            |
| Categories                 | 4 intrant · 5 fournisseur · 8 dépense                              |
| Intrants                   | 10 (3 aliments, 2 poussin souches, 4 médicaments, 1 litière)       |
| Fournisseurs / Clients     | 5 fournisseurs · 5 clients                                         |
| Bâtiments / Produits finis | 4 bâtiments · 7 produits finis                                     |
| Stock                      | Opening balances for all intrants                                  |
| BL Fournisseur             | 7 (across all 4 statuses)                                          |
| AP cycle                   | 3 factures fournisseur + règlements + FIFO allocations             |
| Lots                       | 3 (1 fermé, 2 ouverts) with mortalités and consommations           |
| Production                 | 1 harvest record → finished-goods stock                            |
| BL Clients                 | 5 (across all statuses)                                            |
| AR cycle                   | 2 factures client + paiements + allocations                        |
| Dépenses                   | 15 (all categories, some lot-attributed, 1 service-invoice linked) |

---

## Key Workflows

### 1 — Supplier Procurement (BL → Facture → Règlement)

```
1. Create BLFournisseur (brouillon)
2. Add lines (intrant + qty + unit price)
3. Validate → statut = recu
   └─ Signal fires: StockIntrant.quantite += qty
                    PMP recalculated (weighted average)
                    StockMouvement (entree, source=bl_fournisseur) created

4. Create FactureFournisseur
   └─ Select one or more recu BLs for the same supplier
   └─ montant_total auto-computed — no manual entry (BR-FAF-01)
   └─ Selected BLs marked facture (locked)

5. Register ReglementFournisseur (payment sum)
   └─ achats.utils.appliquer_reglement_fifo() runs inside transaction.atomic()
   └─ FIFO: oldest invoice first, partial on last reached
   └─ AllocationReglement records created per impacted invoice
   └─ Surplus → AcompteFournisseur
```

### 2 — Lot Lifecycle

```
1. Open LotElevage (link to BL fournisseur for poussins)
   └─ Signal: StockIntrant for poussin intrant -= initial_count

2. Daily:  add Mortalite records  (lot.effectif_vivant decreases)
           add Consommation records (StockIntrant decreases per item)

3. At harvest: create ProductionRecord + ProductionLigne(s)
   └─ Validate → StockProduitFini += qty per line

4. Close lot (LotElevage.fermer())
   └─ statut = ferme; further Mortalite/Consommation rejected (BR-LOT-01)
```

### 3 — Client Sales (BL → Facture → Paiement)

```
1. Create BLClient (brouillon) → add lines (produit_fini + qty + price)
2. Validate → statut = livre
   └─ Signal: StockProduitFini.quantite -= qty (blocked if insufficient)

3. Create FactureClient
   └─ Select livre BLs for the same client
   └─ montant_ht auto-computed; TVA applied; TTC stored

4. Record PaiementClient
   └─ User manually selects which invoice(s) to allocate to (BR-FAC-03)
   └─ PaiementClientAllocation created; FactureClient.recalculer_solde() called
```

---

## Roles & Permissions

| Role               | Key Access                                                                |
| ------------------ | ------------------------------------------------------------------------- |
| **Administrateur** | Full access including CompanyInfo, user management, hard deletes          |
| **Gérant**         | All operational modules; cannot manage users or system settings           |
| **Opérateur**      | Lots, consommations, mortalités, BL creation (supplier + client)          |
| **Comptable**      | Factures, règlements, dépenses, reports; read-only on operational modules |

---

## Alerts & Thresholds

| Alert                      | Trigger                                                         | Where                      |
| -------------------------- | --------------------------------------------------------------- | -------------------------- |
| **Stock bas**              | `StockIntrant.quantite ≤ Intrant.seuil_alerte`                  | Stock dashboard            |
| **Facture en retard**      | `date_echeance < today` and not `paye`                          | AP / AR dashboards         |
| **Mortalité anormale**     | Daily mortality rate exceeds configurable threshold             | Lot detail view            |
| **Plafond crédit dépassé** | `Client.creance_globale > Client.plafond_credit`                | Client record, BL creation |
| **Acompte disponible**     | `AcompteFournisseur` exists for a supplier with `utilise=False` | Supplier account view      |

---

## Reporting

| Report                     | Data Source                                                                |
| -------------------------- | -------------------------------------------------------------------------- |
| État des stocks            | `StockIntrant` + `StockProduitFini`                                        |
| Journal des mouvements     | `StockMouvement` (filterable by date, type, source)                        |
| Dettes fournisseurs        | `FactureFournisseur` (non_paye + partiellement_paye)                       |
| Créances clients           | `FactureClient` (non_payee + partiellement_payee)                          |
| Répartition des règlements | `AllocationReglement` joined to `ReglementFournisseur`                     |
| Rentabilité par lot        | `LotElevage` + `Consommation` + `ProductionRecord` + `BLClientLigne`       |
| Résumé trésorerie          | `PaiementClient` (inflows) + `ReglementFournisseur` + `Depense` (outflows) |

---

## Development Notes

### Running Tests

```bash
python manage.py test
# or with coverage:
coverage run manage.py test && coverage report
```

### Key Signal Contracts

All signals live in `<app>/signals.py` and are connected in `<app>/apps.py` via `ready()`.

| Signal      | Sender               | Effect                                                                                               |
| ----------- | -------------------- | ---------------------------------------------------------------------------------------------------- |
| `post_save` | `BLFournisseurLigne` | Updates `StockIntrant` + creates `StockMouvement(entree)` when BL status = `recu`                    |
| `post_save` | `LotElevage`         | Deducts poussin stock on creation (`StockMouvement sortie`)                                          |
| `post_save` | `Consommation`       | Decreases `StockIntrant` + creates `StockMouvement(sortie)`                                          |
| `post_save` | `ProductionLigne`    | Increases `StockProduitFini` + creates `StockMouvement(entree)` when production `valide`             |
| `post_save` | `BLClientLigne`      | Decreases `StockProduitFini` + creates `StockMouvement(sortie)` when BL = `livre`                    |
| `post_save` | `StockAjustement`    | Writes corrected balance to `StockIntrant`/`StockProduitFini` + creates `StockMouvement(ajustement)` |

> ⚠️ All stock-exit signals must use `select_for_update()` on the balance row and reject the operation if the resulting quantity would be negative (BR-STK-01).

### FIFO Engine

`achats/utils.py::appliquer_reglement_fifo(reglement)` is the **single authoritative** payment allocation function. It must:

- Run inside `transaction.atomic()`
- Lock invoice rows with `select_for_update()`
- Order invoices by `date_facture ASC` then `created_at ASC`
- Cover invoices fully until funds run out; partial on last reached
- Create one `AllocationReglement` per touched invoice
- Create `AcompteFournisseur` if payment exceeds total debt
- Call `facture.recalculer_solde()` on every touched invoice

### Stable Category Codes

`CategorieIntrant.code` values are seeded and must not be renamed:

| Code         | Meaning                                   |
| ------------ | ----------------------------------------- |
| `ALIMENT`    | Feed — consumable in lots                 |
| `POUSSIN`    | Live chicks                               |
| `MEDICAMENT` | Veterinary medicines — consumable in lots |
| `AUTRE`      | Other inputs                              |

Business logic guards (e.g. `Consommation.intrant` queryset, `consommation_totale_aliment`) use `categorie__code` lookups against these stable codes, **not** against string-compared FK values.

### Environment Variables

| Variable        | Default  | Description                                  |
| --------------- | -------- | -------------------------------------------- |
| `SECRET_KEY`    | —        | Django secret key (required)                 |
| `DEBUG`         | `False`  | Enable debug mode                            |
| `DATABASE_URL`  | —        | PostgreSQL connection string                 |
| `ALLOWED_HOSTS` | —        | Comma-separated hostnames                    |
| `MEDIA_ROOT`    | `media/` | Upload storage path                          |
| `TVA_DEFAULT`   | `19.00`  | Default TVA rate (overridden by CompanyInfo) |

---

## License

Internal use only — _Élevage Avicole Tizi-Ouzou_.
