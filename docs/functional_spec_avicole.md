# Functional Specification
## Internal Management System — *Élevage Avicole*
### A Mini-Book for Small-Scale Algerian Poultry Farming Operations

---

> **Document Type:** Functional Specification (purely descriptive — no code, no UI design)
> **Domain:** Agri-industrial Operations / Information Systems
> **Context:** Small Algerian *élevage avicole* (poultry farming business)
> **Language:** English with French domain terminology preserved
> **Version:** 1.1

---

## Table of Contents

1. [Introduction & Business Context](#1-introduction--business-context)
2. [Current State: Manual & Semi-Manual Processes](#2-current-state-manual--semi-manual-processes)
3. [System Vision & Full Lifecycle Overview](#3-system-vision--full-lifecycle-overview)
4. [Domain 01 — Gestion des Intrants (Input Management)](#4-domain-01--gestion-des-intrants-input-management)
5. [Domain 02 — Lots d'Élevage (Poultry Batches)](#5-domain-02--lots-délevage-poultry-batches)
6. [Domain 03 — Consommation (Feed & Input Consumption)](#6-domain-03--consommation-feed--input-consumption)
7. [Domain 04 — Production (Finished Product Output)](#7-domain-04--production-finished-product-output)
8. [Domain 05 — Stock (Inventory Management)](#8-domain-05--stock-inventory-management)
9. [Domain 06 — BL Fournisseur (Supplier Delivery Notes)](#9-domain-06--bl-fournisseur-supplier-delivery-notes)
10. [Domain 07 — Factures Fournisseurs (Supplier Invoices / Accounts Payable)](#10-domain-07--factures-fournisseurs-supplier-invoices--accounts-payable)
11. [Domain 08 — BL Clients (Client Delivery Notes)](#11-domain-08--bl-clients-client-delivery-notes)
12. [Domain 09 — Facturation Clients (Client Invoicing)](#12-domain-09--facturation-clients-client-invoicing)
13. [Domain 10 — Paiements Clients (Client Payments)](#13-domain-10--paiements-clients-client-payments)
14. [Domain 11 — Dépenses (Operational Expenses)](#14-domain-11--dépenses-operational-expenses)
15. [Domain 12 — Règlement des Fournisseurs (Supplier Settlement)](#15-domain-12--règlement-des-fournisseurs-supplier-settlement)
16. [Domain 13 — Documents Imprimables (Printable Documents & Proofs)](#16-domain-13--documents-imprimables-printable-documents--proofs)
17. [Accounting Separation: Factures vs. Dépenses](#17-accounting-separation-factures-vs-dépenses)
18. [Supplier Financial Status Management](#18-supplier-financial-status-management)
19. [Traceability Chain: Procurement to Payment](#19-traceability-chain-procurement-to-payment)
20. [Anomaly & Alert Mechanisms](#20-anomaly--alert-mechanisms)
21. [Reporting & Financial Intelligence](#21-reporting--financial-intelligence)
22. [Consistency & Auditability Review](#22-consistency--auditability-review)
23. [Glossary of Key Terms](#23-glossary-of-key-terms)

---

## 1. Introduction & Business Context

### 1.1 About the Business

A small Algerian *élevage avicole* (poultry farming enterprise) is a vertically integrated micro-operation that cycles through distinct phases: receiving live chicks (*poussins*) and feed (*aliments*) from suppliers, raising the birds across one or multiple simultaneous *lots* (batches), processing or selling them as live birds or derived products, and delivering the finished goods to local clients such as markets, restaurants, and wholesalers.

These businesses, while modest in scale, generate a continuous flow of procurement transactions, daily operational records, and sales activities that collectively form a complex financial and operational picture. Despite this complexity, most such operations in Algeria are managed with paper-based ledgers, basic spreadsheets, or informal verbal agreements — an approach that worked when volumes were small but rapidly becomes untenable as the business grows.

### 1.2 Purpose of This Document

This document is a **Functional Specification** for an internal management system tailored to the needs of a small Algerian *élevage avicole*. It describes, in plain operational language, what the system must do — not how it does it technically. It is intended to be read and understood by:

- The **business owner** or farm manager who will use the system daily
- An **analyst or developer** who will design and build the system
- Any **auditor or consultant** reviewing the operational logic

The specification covers the complete lifecycle of the business: from the arrival of inputs (*intrants*) through farming operations, production, stock management, client deliveries, invoicing, and payment collection — as well as the full supplier-side financial cycle including accounts payable, FIFO settlement logic, and debt tracking.

### 1.3 Scope & Boundaries

This document covers the following functional domains:

| # | Domain | French Label |
|---|--------|--------------|
| 01 | Input Management | Gestion des Intrants |
| 02 | Poultry Batches | Lots d'Élevage |
| 03 | Consumption Tracking | Consommation |
| 04 | Production Output | Production |
| 05 | Inventory | Stock |
| 06 | Supplier Delivery Notes | BL Fournisseur |
| 07 | Supplier Invoices (Accounts Payable) | Factures Fournisseurs |
| 08 | Client Delivery Notes | BL Clients |
| 09 | Client Invoicing | Facturation Clients |
| 10 | Client Payments | Paiements Clients |
| 11 | Operational Expenses | Dépenses |
| 12 | Supplier Settlement (FIFO) | Règlement des Fournisseurs |
| 13 | Printable Documents & Proofs | Documents Imprimables |

The document does **not** cover: payroll management, tax filing, accounting journal exports, or any technical implementation details.

---

## 2. Current State: Manual & Semi-Manual Processes

### 2.1 How the Farm Operates Today

In the absence of a dedicated system, poultry farm managers rely on a patchwork of tools and habits:

- **Paper notebooks** record daily chick counts, mortality numbers, and feed distributed per batch
- **Excel spreadsheets** (when available) track stock quantities and sometimes client sales
- **Handwritten receipts** serve as delivery proofs for both supplier and client transactions
- **Phone calls and memory** govern the understanding of what is owed to suppliers

This system, while familiar, breaks down quickly when multiple suppliers, multiple batches, and multiple clients operate simultaneously.

### 2.2 Identified Pain Points

#### 2.2.1 Tracking Feed Consumption (*Aliments*)

Feed is the largest recurring cost in poultry production. Today, bags of feed are received from suppliers and stored in bulk. Workers distribute feed to the batches daily based on habit or verbal instruction. There is rarely a written record linking:

- The quantity of feed delivered by the supplier
- The quantity of feed actually consumed by a specific batch
- The remaining stock in the *dépôt* (warehouse)

As a result, consumption data is unavailable, making cost-per-batch calculations impossible.

#### 2.2.2 Chick Arrivals (*Poussins*)

When a new batch of chicks arrives, the farm records the count — often informally — and begins the farming cycle. The arrival is rarely linked to a specific supplier document, nor is it connected to the eventual mortality (*mortalité*) tracking that follows. The audit trail between a supplier delivery note (*BL fournisseur*) for chicks and the final production results is typically nonexistent.

#### 2.2.3 Mortality Tracking (*Mortalité*)

Dead birds are a normal part of poultry farming. However, without systematic recording, the farm cannot:

- Detect abnormal mortality spikes that may signal disease
- Accurately calculate the number of birds available for sale
- Account for mortality-related cost impacts on batch profitability

#### 2.2.4 Supplier Debt Visibility (*Dettes Fournisseurs*)

This is perhaps the most critical operational gap. Suppliers often deliver goods on credit, expecting payment within a negotiated period. Because deliveries arrive frequently and from multiple suppliers, the farm's total outstanding debt (*dette fournisseur globale*) is almost never accurately known at any given moment. Common problems include:

- The owner cannot quickly answer: *"How much do I owe Supplier X in total?"*
- Multiple unpaid *BL fournisseur* accumulate without a corresponding invoice being matched
- Partial payments are made with no clear record of which invoices they reduce
- Cash payments handed directly to a supplier representative are never documented

#### 2.2.5 Client Delivery and Payment Gaps

On the sales side, clients receive goods via delivery notes (*BL clients*) and are invoiced periodically. Without a system, it is difficult to know:

- Which deliveries have been invoiced and which have not
- Which invoices remain unpaid
- Total receivables outstanding

#### 2.2.6 Operational Expense Tracking (*Dépenses*)

Day-to-day operational expenses — fuel, electricity, wages, maintenance, veterinary visits — are paid in cash and rarely documented. This creates a completely opaque picture of actual farm operating costs, making it impossible to assess batch-level or farm-level profitability.

### 2.3 Conclusion: The Case for a System

The cumulative effect of these gaps is that the farm operates largely "blind": production decisions are made on instinct, financial exposure is unknown, and the potential for errors, disputes, or insolvency goes undetected until it becomes a crisis. A structured management system, even a simple one, would transform this picture — not by adding bureaucracy, but by capturing information that already exists in the daily workflow and making it visible and usable.

---

## 3. System Vision & Full Lifecycle Overview

### 3.1 Guiding Principles

The system must be:

- **Simple enough** for a farm worker or owner with basic digital literacy to use
- **Complete enough** to capture the full production-to-payment cycle
- **Traceable enough** that any financial figure can be traced back to a source document
- **Honest enough** to reflect real financial positions, including debt and pending obligations

### 3.2 The Full Lifecycle

The system models the following end-to-end operational flow:

```
SUPPLIER SIDE
─────────────────────────────────────────────────────────────────────────
  Supplier → BL Fournisseur → Input Stock (Intrants)
                           ↓
             Facture Fournisseur
             (created by selecting one or more BLs → total auto-computed)
                           ↓
             Règlement Fournisseur
             (user enters payment sum → FIFO auto-allocation oldest → newest)
                           ↓
             Solde Fournisseur Updated

PRODUCTION SIDE
─────────────────────────────────────────────────────────────────────────
  Input Stock → Lot d'Élevage → Consommation Quotidienne
                              → Mortalité Enregistrée
                              → Production Constatée
                              ↓
                         Stock Produits Finis

CLIENT SIDE
─────────────────────────────────────────────────────────────────────────
  Stock → BL Client → Facture Client → Paiement Client
                    → Mise à Jour Stock

EXPENSE SIDE
─────────────────────────────────────────────────────────────────────────
  Dépenses Opérationnelles → Catégorisées → Impact Trésorerie
  (salaires, énergie, maintenance — SÉPARÉES des factures fournisseurs)
```

### 3.3 Core Data Entities

| Entity | Description |
|--------|-------------|
| **Fournisseur** | Supplier (feed, chicks, medicine, services) |
| **Intrant** | Input product (aliment, poussin, médicament) |
| **BL Fournisseur** | Supplier delivery note |
| **Facture Fournisseur** | Supplier invoice built from selected BLs |
| **Règlement Fournisseur** | Supplier payment record (FIFO-allocated sum) |
| **Lot d'Élevage** | A poultry batch (group of birds raised together) |
| **Consommation** | Daily input consumption per batch |
| **Production** | Harvested output from a batch |
| **Produit Fini** | Finished product (live bird, processed carcass, eggs, etc.) |
| **Stock** | Current inventory (intrants + produits finis) |
| **Client** | Buyer receiving and paying for poultry products |
| **BL Client** | Client delivery note |
| **Facture Client** | Client invoice |
| **Paiement Client** | Client payment record |
| **Dépense** | Operational expense record |

---

## 4. Domain 01 — Gestion des Intrants (Input Management)

### 4.1 What Are Intrants?

*Intrants* are all input goods that enter the farm from external suppliers. They fall into three primary categories:

| Category | French Label | Examples |
|----------|--------------|---------|
| Feed | Aliments | Starter feed, grower feed, finisher feed |
| Live chicks | Poussins | Day-old chicks (*poussins d'un jour*) |
| Veterinary products | Médicaments / Produits Vétérinaires | Vaccines, antibiotics, vitamins |

Each *intrant* type has distinct characteristics. Feed is measured in kilograms or bags (*sacs*), chicks are counted as units, and medicines are tracked in bottles, doses, or milliliters. The system must accommodate these different units of measure.

### 4.2 Defining an Intrant

The system maintains a catalog of all *intrants* used by the farm. For each *intrant*, the following information is recorded:

- **Name / designation** (*désignation*): e.g., "Aliment Démarrage 1er Âge"
- **Category**: aliment, poussin, médicament, other
- **Unit of measure** (*unité de mesure*): kg, sac (25 kg), unit, bottle, dose, liter
- **Supplier(s)** associated with this input
- **Current stock balance** (maintained automatically by the system)

### 4.3 Stock Entry of Intrants

*Intrants* enter stock exclusively through validated *BL fournisseur* (supplier delivery notes). There is no other mechanism for adding intrant stock. This ensures that every unit of feed or every chick that enters the system is traceable to a supplier document.

### 4.4 Stock Exit of Intrants

*Intrants* exit stock through *consommation* records — when they are consumed by a *lot d'élevage*. For chicks, the exit occurs at the time they are assigned to a new batch. For medicines, exits are recorded when administered to a batch.

---

## 5. Domain 02 — Lots d'Élevage (Poultry Batches)

### 5.1 Concept of a Lot

A *lot d'élevage* represents a single cohort of birds raised together from arrival to harvest. It is the central production unit of the farm. Everything that happens operationally — feed consumption, mortality, medicine administration, and final production — is tracked at the lot level.

### 5.2 Opening a Lot

A lot is opened when a batch of chicks arrives at the farm. The opening of a lot requires:

- **Lot identifier / name** (*désignation du lot*): e.g., "Lot Avril 2025 – Bâtiment 1"
- **Opening date** (*date d'ouverture*)
- **Initial chick count** (*nombre de poussins initial*): sourced from a *BL fournisseur*
- **Supplier** of the chicks
- **Assigned building** (*bâtiment*)
- **Bird strain** (*souche*): e.g., Ross 308, Cobb 500

When a lot is opened, the chick count is deducted from intrant stock and assigned to the lot.

### 5.3 During the Lot: Daily Operations

#### 5.3.1 Daily Mortality (*Mortalité*)

Each day, the farm records the number of birds that died. This reduces the live bird count (*effectif vivant*). Records include date, count, cause (if known), and cumulative mortality.

#### 5.3.2 Feed Consumption (*Consommation d'Aliments*)

Feed distributed to the lot is recorded daily. This reduces feed stock and accumulates total feed consumed by the lot.

#### 5.3.3 Medicine Administration (*Médicaments*)

When treatments are administered, the system records the date, product, dosage, and batch affected. This deducts from medicine stock and builds a health history for the lot.

### 5.4 Closing a Lot

A lot is closed at harvest. The closure records the date, final live count, and links to the production records. Once closed, no further entries can be added.

### 5.5 Key Calculated Indicators Per Lot

| Indicator | Description |
|-----------|-------------|
| **Effectif vivant** | Current live bird count = Initial – Deaths |
| **Taux de mortalité** | Mortality rate = Deaths / Initial × 100 |
| **Consommation totale** | Total feed consumed across the lot lifecycle |
| **IC (Indice de Consommation)** | Feed conversion ratio = Feed consumed / Weight gain |
| **Poids moyen estimé** | Estimated average weight per bird |
| **Durée d'élevage** | Days from opening to closure |

---

## 6. Domain 03 — Consommation (Feed & Input Consumption)

### 6.1 Purpose

The *consommation* domain captures all outflows of *intrants* attributed to active lots. It simultaneously maintains accurate stock balances and builds a cost picture for each lot.

### 6.2 Recording a Consumption Event

| Field | Description |
|-------|-------------|
| **Date** | Date of consumption |
| **Lot** | The lot the consumption is attributed to |
| **Intrant** | Product consumed (aliment, médicament) |
| **Quantity** | Amount in the intrant's unit of measure |
| **Notes** | Optional remarks |

Every validated consumption record automatically reduces the stock balance of the corresponding *intrant*.

### 6.3 Cost Accumulation

If the average purchase price of each intrant is known (derived from *BL fournisseur* data), the system estimates:

- Total feed cost for the lot (*coût aliments total*)
- Total medicine cost for the lot (*coût médicaments total*)
- Combined input cost (*coût intrants total*)

This is a management estimate used for batch-level profitability analysis.

---

## 7. Domain 04 — Production (Finished Product Output)

### 7.1 From Lot to Product

When a *lot d'élevage* is harvested, live birds are converted into one or more types of *produits finis*. The production domain captures this transformation.

### 7.2 Types of Produits Finis

| Type | Description |
|------|-------------|
| **Volaille vivante** | Live birds sold directly |
| **Carcasse entière** | Whole processed carcasses |
| **Découpes** | Cut pieces (breast, thigh, wing) |
| **Abats** | Offal (liver, gizzard, heart) |
| **Œufs** | Eggs (for laying operations) |

### 7.3 Recording a Production Event

A production record captures:

- Date of harvest
- Source lot
- Number of birds harvested
- Products generated: type, quantity, unit
- Total weight and average weight per bird
- Notes

When validated, the finished products are added to *stock produits finis* and become available for *BL clients*.

---

## 8. Domain 05 — Stock (Inventory Management)

### 8.1 Two Distinct Stock Segments

| Segment | Contents | Enters Via | Exits Via |
|---------|----------|------------|-----------|
| **Stock Intrants** | Aliments, poussins, médicaments | BL Fournisseur | Consommation |
| **Stock Produits Finis** | Volaille vivante, carcasses, découpes | Production | BL Client |

### 8.2 Stock Card (*Fiche de Stock*)

For each item, the system maintains a card showing opening balance, all inflows (with source reference), all outflows (with destination reference), current balance, and a minimum alert threshold (*seuil d'alerte*).

### 8.3 Stock Valuation

For *intrants*, stock is valued at the purchase price recorded on the *BL fournisseur* using a weighted average cost method. For *produits finis*, stock is valued at the production cost allocated from the source lot.

### 8.4 Stock Adjustments

Manual adjustments are permitted when physical counts reveal discrepancies, provided they are dated, attributed to a user, and justified with a reason — flagged in the stock history for audit review.

---

## 9. Domain 06 — BL Fournisseur (Supplier Delivery Notes)

### 9.1 What Is a BL Fournisseur?

A *bon de livraison fournisseur* is the document recording the physical delivery of goods from a supplier. It is the sole entry point for *intrants* into the system and the foundation of the entire supplier financial chain.

### 9.2 Information Captured

| Field | Description |
|-------|-------------|
| **BL number** | Unique reference |
| **Date of delivery** | Actual physical delivery date |
| **Supplier** (*fournisseur*) | Who delivered the goods |
| **Lines** | One or more lines: intrant + quantity + unit price |
| **Total amount** | Sum of all lines |
| **Destination lot** | For chicks/medicines: the lot they are assigned to |
| **Reception status** | Received in full / partial / refused |
| **Notes** | Driver, vehicle, observations |

### 9.3 Stock Impact

When a *BL fournisseur* is **validated**, the system automatically updates *intrant* stock. Drafts have no stock impact.

### 9.4 States of a BL Fournisseur

| State | Meaning |
|-------|---------|
| **Brouillon** | Draft, not yet validated |
| **Reçu** | Validated; stock updated |
| **Facturé** | Included in a *facture fournisseur*; locked |
| **En litige** | Dispute raised |

Once a BL is marked **Facturé**, it is locked and cannot be included in another invoice.

---

## 10. Domain 07 — Factures Fournisseurs (Supplier Invoices / Accounts Payable)

### 10.1 Role of the Facture Fournisseur

A *facture fournisseur* consolidates one or more *BL fournisseurs* from the same supplier into a single payable obligation. It creates an **account payable** — a debt the farm owes to the supplier — and is the anchor point for all supplier settlement activity.

### 10.2 Creating a Facture Fournisseur

Invoice creation follows a simple, fixed workflow:

1. The user selects the supplier
2. The system displays all validated, non-invoiced (*Reçu*) *BL fournisseurs* for that supplier
3. The user selects one or more BLs to include
4. The system automatically sets the invoice total as the **sum of all selected BL line totals** — no separate amount is entered or verified
5. The user fills in the invoice metadata: the supplier's own invoice number, date, and due date
6. The invoice is validated

Upon validation:
- Each selected BL is marked **Facturé** (locked)
- The invoice is created with status **Non Payé**
- The invoice total is added to the supplier's *dette globale*

### 10.3 Information on a Facture Fournisseur

| Field | Description |
|-------|-------------|
| **Invoice number** (*numéro de facture*) | Supplier's own reference |
| **Date of invoice** (*date de facture*) | Issuance date |
| **Due date** (*date d'échéance*) | Payment deadline |
| **Supplier** | Issuing *fournisseur* |
| **BL references** | List of BLs included |
| **Total amount** (*montant total*) | Auto-computed from selected BL totals |
| **Amount allocated** (*montant réglé*) | Cumulative settlements applied to date |
| **Remaining balance** (*reste à payer*) | Total – Allocated |
| **Status** | Non payé / Partiellement payé / Payé / En litige |
| **Invoice type** | Goods (*marchandises*) or Service (*service*) |

### 10.4 Payment History Per Invoice

The invoice record maintains a read-only allocation history showing which *règlement fournisseur* records contributed to it and by how much. Payments are never initiated from the invoice screen — they are always registered through the *règlement* module (Domain 12) and reflected here automatically.

---

## 11. Domain 08 — BL Clients (Client Delivery Notes)

### 11.1 Purpose

A *bon de livraison client* documents the physical delivery of finished products to a buyer. It is the starting point of the sales and receivables cycle.

### 11.2 Information on a BL Client

| Field | Description |
|-------|-------------|
| **BL number** | Unique delivery reference |
| **Date of delivery** | Physical delivery date |
| **Client** | Name and reference of the buyer |
| **Lines** | Products: produit fini + quantity + unit price |
| **Total amount** | Sum of lines |
| **Delivery address** | Optional |
| **Signed by** | Driver or receiver |
| **Status** | Livré / Facturé / En litige |

### 11.3 Stock Impact

When validated, the delivered quantities are deducted from *stock produits finis*. A validated *BL client* becomes the basis for a *facture client* and is locked once invoiced.

---

## 12. Domain 09 — Facturation Clients (Client Invoicing)

### 12.1 Creating a Client Invoice

Client invoices are generated by selecting one or more *BL clients* for the same client — mirroring the supplier invoicing logic. The system computes the invoice total automatically from the selected BLs. The user reviews and validates.

### 12.2 Information on a Facture Client

| Field | Description |
|-------|-------------|
| **Invoice number** | Unique sequential reference |
| **Date** | Issuance date |
| **Client** | Billed client |
| **BL references** | BLs included |
| **Lines** | Products, quantities, unit prices, totals |
| **Gross total** (*montant HT*) | Pre-tax amount |
| **Tax** (*TVA*) | If applicable |
| **Net total** (*montant TTC*) | Amount the client owes |
| **Payment terms** | Due date or conditions |
| **Status** | Non payée / Partiellement payée / Payée |

The system tracks total client receivables (*créances clients*), overdue invoices, and partial balances at all times.

---

## 13. Domain 10 — Paiements Clients (Client Payments)

### 13.1 Recording a Client Payment

| Field | Description |
|-------|-------------|
| **Date** | Payment date |
| **Client** | Payer |
| **Amount received** | Payment sum |
| **Payment method** | Espèces, virement, chèque |
| **Reference** | Cheque or transfer ID |
| **Invoice(s) applied to** | One or more invoices settled |
| **Remainder** | Outstanding balance if partial |

Each payment reduces the balance of the corresponding *facture client*. Fully covered invoices become **Payée**; partially covered remain **Partiellement Payée**.

---

## 14. Domain 11 — Dépenses (Operational Expenses)

### 14.1 What Counts as a Dépense?

*Dépenses* are all operational costs that are **not** the purchase of inventory goods from suppliers:

| Category | Examples |
|----------|---------|
| **Salaires** | Worker wages, daily labor |
| **Énergie** | Electricity, gas, heating fuel |
| **Maintenance** | Equipment repairs, building upkeep |
| **Transport** | Delivery fuel, hired transport |
| **Frais vétérinaires** | Vet visit fees (not medicines as goods) |
| **Fournitures** | Packaging, office supplies |
| **Taxes & Impôts** | Local taxes, license fees |
| **Divers** | Miscellaneous operational costs |

### 14.2 Recording a Dépense

| Field | Description |
|-------|-------------|
| **Date** | Expense date |
| **Category** | *Catégorie de dépense* |
| **Description** | *Libellé* |
| **Amount** | *Montant* |
| **Payment method** | Cash, transfer, cheque |
| **Document reference** | Receipt number (optional) |
| **Lot d'élevage** | Optional batch attribution |

### 14.3 Strict Separation from Factures Fournisseurs

> ⚠️ **Critical Rule:** A supplier invoice for goods must **never** automatically generate a *dépense* record. These two domains are entirely separate and must never be merged or automatically linked.

This separation prevents double-counting: a *facture fournisseur* for feed already represents a procurement cost. Recording it again as a *dépense* would count the same cost twice across all financial summaries. The only permitted exception is described in Section 17.

---

## 15. Domain 12 — Règlement des Fournisseurs (Supplier Settlement)

This domain governs the single, unified mechanism by which the farm records all payments made to suppliers.

### 15.1 The Operational Reality

In practice, a farm owner hands a cash sum to a supplier representative, or initiates a transfer, without designating which specific invoice it applies to. The payment is a global sum against the total amount owed. The system must accommodate this reality directly — without requiring the user to manually match each payment to a specific invoice.

### 15.2 Defining Dette Fournisseur Globale

The **dette fournisseur globale** for a given supplier is the sum of all outstanding invoice balances at a given point in time:

> **Dette Fournisseur Globale = Σ (Reste à Payer) across all Non Payé and Partiellement Payé Factures Fournisseurs for that supplier**

This figure is recalculated in real time after every settlement. Fully paid invoices are excluded. Disputed invoices (*en litige*) may be excluded or included based on configuration.

### 15.3 The Single Payment Mechanism

There is **one and only one** way to record a payment to a supplier: the user enters a payment sum, and the system automatically allocates it across open invoices using a **FIFO rule — from the oldest invoice to the most recent**.

The user does not choose which invoice to pay. The allocation is entirely automatic.

### 15.4 The FIFO Settlement Process — Step by Step

#### Step 1 — User Enters the Payment

The user opens the supplier account view, which displays:

- Current *dette fournisseur globale*
- List of open invoices ordered from oldest to most recent

The user enters:

- Payment amount (*montant du règlement*)
- Payment date (*date du règlement*)
- Payment method: espèces, chèque, virement
- Optional reference (cheque number, transfer ID)

#### Step 2 — System Orders Open Invoices (Oldest First)

The system automatically retrieves all **Non Payé** and **Partiellement Payé** invoices for that supplier and orders them **from least recent to most recent** (oldest invoice date first). No user selection is required.

#### Step 3 — Sequential FIFO Allocation

The payment is distributed through the ordered invoice list:

```
Payment Amount = P

For each invoice I, ordered from oldest to most recent:

  If P = 0 → STOP

  If I.reste_à_payer ≤ P:
    → Full remaining balance of I is covered
    → Mark I as "Payé"
    → P = P – I.reste_à_payer
    → Continue to next invoice

  If I.reste_à_payer > P:
    → P is applied partially to I
    → I.reste_à_payer = I.reste_à_payer – P
    → Mark I as "Partiellement Payé"
    → P = 0 → STOP
```

The last invoice reached may be only partially compensated if the payment is insufficient to cover it fully. This is the expected and correct behavior.

#### Step 4 — Invoice Statuses Updated

| Invoice | Result |
|---------|--------|
| Fully covered | Status → **Payé**, *reste à payer* → 0 |
| Last reached, partially covered | Status → **Partiellement Payé**, *reste à payer* reduced |
| Not yet reached | Status unchanged |

#### Step 5 — Settlement Record Created

The system creates a *règlement fournisseur* record documenting the full allocation:

| Field | Content |
|-------|---------|
| Supplier | *Fournisseur* name |
| Date | Settlement date |
| Total amount | Full payment sum |
| Allocation lines | Per impacted invoice: invoice ref + amount applied |
| Payment method | Espèces / Chèque / Virement |
| Notes | Optional |

#### Step 6 — Supplier Debt Recalculated

The supplier's *dette globale* is recalculated immediately and reflects the updated balances.

### 15.5 Handling an Overpayment

If the entered amount exceeds the current *dette globale*, all open invoices are fully covered. The surplus is flagged as an **acompte fournisseur** (advance) and tracked against the supplier account for application to future invoices.

### 15.6 Practical Example

Suppose the farm owes **Fournisseur Aliments Tahar** the following:

| Invoice | Date | Total | Already Allocated | Reste à Payer |
|---------|------|-------|------------------|---------------|
| F-2025-011 | 05 Jan 2025 | 45,000 DZD | 20,000 DZD | 25,000 DZD |
| F-2025-019 | 18 Jan 2025 | 60,000 DZD | 0 DZD | 60,000 DZD |
| F-2025-027 | 02 Feb 2025 | 30,000 DZD | 0 DZD | 30,000 DZD |

**Current dette globale = 115,000 DZD**

The owner registers a *règlement* of **70,000 DZD**.

FIFO allocation (oldest first):

1. **F-2025-011** (05 Jan): 25,000 DZD ≤ 70,000 DZD → fully covered → **Payé** → Remaining: 45,000 DZD
2. **F-2025-019** (18 Jan): 60,000 DZD > 45,000 DZD → partially covered → **Partiellement Payé**, new *reste à payer* = 15,000 DZD → Remaining: 0 DZD → **STOP**
3. **F-2025-027** (02 Feb): not reached → unchanged → **Non Payé**

**New dette globale = 15,000 + 30,000 = 45,000 DZD**

The settlement record captures:
- F-2025-011 → 25,000 DZD applied
- F-2025-019 → 45,000 DZD applied
- Total: 70,000 DZD ✓

---

## 16. Domain 13 — Documents Imprimables (Printable Documents & Proofs)

Every operational action in the system that involves a physical exchange of goods, money, or a formal commitment must be backed by a printable document. This domain defines the set of printable outputs the system must be able to generate, their purpose, their content, and the operational moment at which they are produced.

These documents serve three functions simultaneously: they are operational proofs handed to the counterparty (supplier or client), internal records kept by the farm, and audit evidence that links physical events to financial entries in the system.

### 16.1 Printable Documents Overview

| Document | French Label | Direction | Triggered By |
|----------|-------------|-----------|--------------|
| Supplier Delivery Receipt | Accusé de Réception BL Fournisseur | Farm ← Supplier | BL Fournisseur validation |
| Supplier Payment Receipt | Reçu de Règlement Fournisseur | Farm → Supplier | Règlement Fournisseur |
| Supplier Invoice Print | Impression Facture Fournisseur | Internal / Supplier | Facture Fournisseur |
| Client Delivery Note | Bon de Livraison Client | Farm → Client | BL Client validation |
| Client Invoice | Facture Client | Farm → Client | Facture Client |
| Client Payment Receipt | Reçu de Paiement Client | Farm → Client | Paiement Client |
| Expense Voucher | Pièce Justificative de Dépense | Internal | Dépense |
| Stock Movement Slip | Bon de Mouvement de Stock | Internal | Consommation / Adjustment |

---

### 16.2 Supplier Delivery Note (*Bon de Livraison Fournisseur*) — External Document + Upload

#### Nature: Externally Issued, Uploaded and Re-entered

A *BL fournisseur* originates from the **supplier**, not from the farm. When the supplier's driver arrives with a delivery, they carry a physical paper delivery note issued by the supplier. The farm does not generate this document — it receives it.

The workflow for this document is therefore:

1. **Physical receipt:** The supplier hands over their paper BL at the time of delivery.
2. **Upload:** The farm user scans or photographs the supplier's BL and uploads the file into the system. The upload is attached directly to the BL record being created. Accepted formats include PDF, JPG, and PNG.
3. **Form re-entry:** The user manually re-enters the BL information into the system's structured form (supplier, date, line items, quantities, unit prices). The uploaded file serves as the visual reference during this entry.
4. **Validation:** Once entered and verified against the uploaded document, the BL is validated — triggering the stock update.

The uploaded document is permanently stored as an **attachment** to the *BL fournisseur* record. It can be consulted at any time from the BL view.

#### Information Re-entered in the Form

| Field | Description |
|-------|-------------|
| Supplier's own BL reference | As printed on the supplier's document |
| Date of delivery | As stated on the document |
| Supplier | *Fournisseur* |
| Line items | Intrant + quantity + unit + unit price + line total |
| Total amount | Sum of lines |
| Reception notes | Observed discrepancies, partial quantities, condition |
| Received by | Farm receiver name |

#### Attachment Status

Once an upload is associated with a BL record, the record displays an attachment indicator. If no document has been uploaded, the record is flagged as **"Sans pièce jointe"** — a visual reminder that the physical paper has not yet been digitized.

---

### 16.3 Supplier Payment Receipt (*Reçu de Règlement Fournisseur*)

#### Purpose
This document is the formal proof that a payment was made to a supplier. It is generated after a *règlement fournisseur* is validated. One copy is handed to the supplier (or their representative), and the farm retains the original. It is the primary document preventing payment disputes.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Reçu de Règlement Fournisseur" |
| Farm name & address | Issuing entity |
| Receipt number | Unique sequential system-generated reference |
| Date of payment | *Date du règlement* |
| Supplier name | *Fournisseur* |
| Amount paid | Total settlement sum in DZD (in figures and in words) |
| Payment method | Espèces / Chèque (+ cheque number) / Virement (+ reference) |
| Allocation detail | List of invoices covered: invoice ref + amount applied to each |
| Remaining balance | Updated *dette globale* after this payment |
| Paid by | Farm representative name and signature field |
| Received by | Supplier representative name and signature field |
| Stamp / seal field | Optional |

The allocation detail section is critical: it transforms the receipt into a reconciliation document, showing exactly which invoices the payment covers and to what extent.

---

### 16.4 Supplier Invoice Print (*Impression Facture Fournisseur*)

#### Purpose
A formatted printout of the supplier invoice as recorded in the system. This serves as the farm's internal copy of the supplier's invoice, filed against the corresponding BL documents. It may also be presented to an auditor or accountant as part of the accounts payable record.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Facture Fournisseur" |
| Farm name & address | Recipient (the farm) |
| Supplier name & address | Issuing supplier |
| Invoice number | Supplier's own reference |
| Invoice date & due date | |
| BL references included | List of BLs covered by this invoice |
| Line items | Intrant + quantity + unit price + line total |
| Total amount | Invoice total (auto-computed from BLs) |
| Amount already paid | Cumulative allocations to date |
| Remaining balance | *Reste à payer* |
| Current status | Non payé / Partiellement payé / Payé |

---

### 16.5 Client Delivery Note (*Bon de Livraison Client*)

#### Purpose
This is the primary commercial document handed to the client at the point of physical delivery. It lists exactly what was delivered, in what quantity, and at what price. The client (or their receiver) signs this document, making it a legally valid proof of delivery. It also triggers the stock deduction in the system.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Bon de Livraison" |
| Farm name & address | Issuing entity |
| BL number | Unique system reference |
| Date of delivery | |
| Client name & address | Recipient |
| Line items | Produit fini + quantity + unit + unit price + line total |
| Total amount | Sum of delivered lines |
| Delivery address | If different from client address |
| Delivered by | Driver name and signature field |
| Received by | Client representative name and signature field |
| Notes | Optional observations (e.g., weight variances, return of crates) |
| Stamp / seal field | Optional |

---

### 16.6 Client Invoice (*Facture Client*)

#### Purpose
The formal billing document issued to the client, requesting payment for one or more deliveries. It is generated from the *facturation clients* module and covers one or more *BL clients*. This is the document the client uses to authorize payment through their own accounting process.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Facture" |
| Farm name, address, legal ID | Issuing entity |
| Invoice number | Sequential system reference |
| Invoice date & payment due date | |
| Client name & address | Billed entity |
| BL references | Deliveries covered |
| Line items | Product + quantity + unit price + line total |
| Subtotal (*montant HT*) | Pre-tax total |
| TVA | Tax amount (if applicable) |
| Total (*montant TTC*) | Amount due |
| Payment instructions | Bank details or cash instructions |
| Authorized by | Farm representative signature field |
| Stamp / seal field | Optional |

---

### 16.7 Client Payment Receipt (*Reçu de Paiement Client*)

#### Purpose
This document confirms that the farm has received a payment from a client. It is generated after a *paiement client* is recorded. A copy is issued to the client as proof of payment, and the farm retains the original. It protects both parties in case of later payment disputes.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Reçu de Paiement" |
| Farm name & address | Issuing entity |
| Receipt number | Unique sequential reference |
| Date of payment | |
| Client name | Paying party |
| Amount received | In DZD, in figures and in words |
| Payment method | Espèces / Chèque (+ number) / Virement (+ reference) |
| Invoice(s) settled | List of invoices covered + amount applied to each |
| Remaining balance | Updated *créance* outstanding after this payment |
| Received by | Farm representative name and signature field |
| Stamp / seal field | Optional |

---

### 16.8 Expense Voucher (*Pièce Justificative de Dépense*)

#### Purpose
The expense voucher is the internal document that formalizes and records a cash or non-cash operational expense. It is not handed to an external party but is kept by the farm as internal audit evidence for each *dépense* entry. It substitutes for or accompanies the external receipt when one is available.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Pièce Justificative de Dépense" |
| Farm name | |
| Voucher number | Unique system reference |
| Date | Expense date |
| Expense category | *Catégorie* (salaire, énergie, maintenance, etc.) |
| Description | *Libellé* — what was paid for |
| Amount | In DZD |
| Payment method | Espèces / Chèque / Virement |
| Lot attributed to | If applicable |
| External reference | Receipt number or supplier invoice number (if any) |
| Approved by | Farm manager name and signature field |
| Note | Any additional remarks |

---

### 16.9 Stock Movement Slip (*Bon de Mouvement de Stock*)

#### Purpose
A stock movement slip documents any significant internal movement or adjustment of stock — most commonly a manual adjustment following a physical count discrepancy, or a formal record of a consommation event for a specific lot. It is an internal audit document.

#### Content

| Field | Description |
|-------|-------------|
| Document title | "Bon de Mouvement de Stock" |
| Farm name | |
| Slip number | Unique reference |
| Date | Movement date |
| Movement type | Entrée (entry) / Sortie (exit) / Ajustement (adjustment) |
| Item | Intrant or produit fini |
| Quantity | Amount moved, in the item's unit |
| Reason / justification | *Motif* (consumption, adjustment, waste, etc.) |
| Source or destination | Lot attributed (for consumption) or location |
| Previous balance | Stock before the movement |
| New balance | Stock after the movement |
| Recorded by | User name and signature field |

---

### 16.10 General Printing Rules

The following rules apply to all printable documents in the system:

**Numbering:** Every printable document has a unique, sequential, system-generated reference number that cannot be reused or manually edited. This number is the primary link between the physical paper and the digital record.

**Date and time stamp:** All documents carry the date of the underlying event (delivery, payment, expense) as well as the date and time of printing.

**Copies:** The system allows printing multiple copies of any document, each marked with its copy designation (Original / Copie).

**Reprint:** Any document can be reprinted at any time. Reprints are clearly marked "DUPLICATE" or "RÉIMPRESSION" to distinguish them from originals.

**Void / cancelled documents:** If a document's underlying record is cancelled or disputed, the system marks the document as **ANNULÉ** on any reprint, preserving the original number in the sequence.

**Language:** All documents are printed in French, consistent with the Algerian business and legal context.

**Format:** Document layout must be legible on standard A4 paper and should leave room for handwritten signatures.


---

## 17. Accounting Separation: Factures vs. Dépenses

### 16.1 The Core Principle

The system enforces a **strict accounting separation** between:

- **Factures Fournisseurs** — procurement costs creating accounts payable
- **Dépenses** — operational overhead charges

### 16.2 Why This Matters: Avoiding Double-Counting

If a *facture fournisseur* for 80,000 DZD of feed were automatically converted into a *dépense*, that amount would appear twice in financial totals — once as an account payable and once as an operational charge. All cost reports would be inflated and unreliable.

### 16.3 The Correct Model

| Transaction Type | Where It Goes | What It Represents |
|-----------------|---------------|-------------------|
| Feed purchase | *Facture Fournisseur* (AP) | Procurement cost / debt |
| Chick purchase | *Facture Fournisseur* (AP) | Procurement cost / debt |
| Medicine from supplier | *Facture Fournisseur* (AP) | Procurement cost / debt |
| Vet visit fee | *Dépense* | Operational service charge |
| Electricity bill | *Dépense* | Utility overhead |
| Fuel for farm vehicle | *Dépense* | Operational transport cost |

### 16.4 The Allowed Exception: Service Invoices

The only case where a *facture fournisseur* may optionally be linked to a *dépense* is when the invoice covers a **service** (not goods) — such as an equipment repair billed by a maintenance company. In this case:

- The user explicitly marks the invoice type as **"Service"**
- The user deliberately creates or links the corresponding *dépense*
- The system **never** does this automatically

This is an explicit user action representing a named exception to the general rule.

### 16.5 Summary of Separation Rules

| Rule | Description |
|------|-------------|
| Default behavior | Factures → AP only; no *dépense* created |
| Goods invoices | Always in AP; never linked to *dépenses* |
| Service invoices | May be manually linked by the user |
| Automatic conversion | Never permitted |

---

## 18. Supplier Financial Status Management

### 17.1 Invoice-Level Statuses

| Status | French Label | Meaning |
|--------|-------------|---------|
| Unpaid | Non Payé | No allocation applied; full amount due |
| Partially Paid | Partiellement Payé | Allocations applied; balance remains |
| Paid | Payé | Fully settled; *reste à payer* = 0 |
| In Dispute | En Litige | Dispute raised; payment on hold |

### 17.2 Status Transition Rules

```
Non Payé → (partial allocation) → Partiellement Payé
Non Payé → (full allocation) → Payé
Partiellement Payé → (allocation covers remainder) → Payé
Any status → (dispute raised) → En Litige
En Litige → (dispute resolved) → previous status or Payé
```

A status cannot be manually set to **Payé** without a settlement record that accounts for the full invoice amount.

### 17.3 Supplier-Level Financial Summary

| Indicator | Description |
|-----------|-------------|
| **Dette totale actuelle** | Sum of all *reste à payer* across unpaid/partial invoices |
| **Factures en attente** | Count and list of open invoices |
| **Total réglé (période)** | Payments allocated in a selected date range |
| **Solde restant dû** | Current outstanding balance |
| **Historique des règlements** | Chronological log of all settlements and their allocations |

### 17.4 Treasury Impact (*Impact sur Trésorerie*)

Every *règlement fournisseur* is a real cash outflow. The system tracks cumulative cash paid per supplier and across all suppliers, enabling a view of total disbursements for a period, remaining obligations, and projected future cash needs by due date.

---

## 19. Traceability Chain: Procurement to Payment

### 18.1 The Full Chain

```
FOURNISSEUR
    │
    ▼
BL FOURNISSEUR
(Validated → stock updated → status: Reçu)
    │
    ▼
FACTURE FOURNISSEUR
(User selects BLs → total auto-computed → status: Non Payé)
(BLs locked as Facturé)
    │
    ▼
RÈGLEMENT FOURNISSEUR
(User enters sum → FIFO: oldest invoice first)
    │
    ├── Oldest invoice(s): fully covered → Payé
    ├── Last reached invoice: partially covered → Partiellement Payé
    └── Remaining invoices: untouched → Non Payé
    │
    ▼
DETTE GLOBALE RECALCULATED IN REAL TIME
    │
    (Only if invoice type = Service, manual user action)
    ▼
LINKED DÉPENSE (exception case)
```

### 18.2 Navigation Paths

The system allows forward and backward traversal at any point in the chain:

**Forward:** BL → Invoice → Settlement allocations → Current balance

**Backward:** Settlement record → Invoices covered → BLs composing those invoices → Stock movements from those BLs

**Lot view:** Lot → Intrants consumed → BLs delivering those intrants → Invoices covering those BLs

**Supplier view:** Fournisseur → All BLs, invoices, settlements, and current debt in one consolidated screen

---

## 20. Anomaly & Alert Mechanisms

The system monitors the operational and financial state of the farm and surfaces actionable alerts for the following conditions.

### 19.1 Overdue Invoices (*Factures en Retard*)

When the current date passes an invoice's *date d'échéance* and the invoice remains unpaid or partially paid, it is flagged as **overdue** and highlighted prominently in the supplier account view.

### 19.2 Abnormal Debt Accumulation

Alerts are triggered when:

- The *dette globale* to a single supplier exceeds a configurable ceiling
- No settlement has been registered for a supplier in more than a configurable number of days
- The number of open invoices for a supplier exceeds a configurable count

### 19.3 Settlement Overpayment

If a settlement sum exceeds the current *dette globale*, the surplus is immediately flagged as an *acompte* and the user is notified rather than the excess being silently discarded.

### 19.4 Uninvoiced BLs (*BL Sans Facture*)

If a *BL fournisseur* has been in **Reçu** status for more than a configurable number of days without being included in an invoice, the system flags it as a pending uninvoiced delivery, prompting the user to follow up with the supplier.

### 19.5 Stock Alerts

Alerts are triggered when:

- A consumption record would reduce an intrant stock below zero
- A *BL client* would ship more product than available in *stock produits finis*
- Any stock item falls below its defined minimum threshold (*seuil d'alerte*)
- A manual stock adjustment deviates significantly from the expected book balance

---

## 21. Reporting & Financial Intelligence

### 20.1 Supplier Balance Aging Report (*Balance Fournisseur par Ancienneté*)

Outstanding debt per supplier broken down by age bracket:

| Supplier | 0–30 Days | 31–60 Days | 61–90 Days | 90+ Days | Total Debt |
|----------|-----------|-----------|-----------|----------|------------|
| Fournisseur A | 45,000 DZD | 20,000 DZD | 0 | 0 | 65,000 DZD |
| Fournisseur B | 0 | 35,000 DZD | 15,000 DZD | 10,000 DZD | 60,000 DZD |
| **Total** | **45,000** | **55,000** | **15,000** | **10,000** | **125,000 DZD** |

This report is the primary tool for prioritizing supplier payments and managing cash obligations.

### 20.2 Settlement History (*Historique des Règlements*)

Chronological log of all settlements per supplier:

| Date | Amount | Method | Invoices Covered |
|------|--------|--------|-----------------|
| 10 Jan 2025 | 30,000 DZD | Cash | F-2025-004 (fully) |
| 25 Jan 2025 | 70,000 DZD | Virement | F-2025-011 (fully), F-2025-019 (partial – 45,000 DZD) |

### 20.3 Payment Distribution Report (*Répartition des Règlements*)

Summary of how settlements were distributed across invoices during a period, broken down by supplier, by invoice, and by payment method.

### 20.4 Current Debt by Supplier (*Dettes en Cours par Fournisseur*)

Live dashboard per active supplier showing: total debt, number of open invoices, oldest unpaid invoice date, days since last settlement, and next due date.

### 20.5 Lot Profitability Report (*Rentabilité par Lot*)

For closed lots:

| Metric | Example Value |
|--------|--------------|
| Initial chick count | 5,000 birds |
| Final count at harvest | 4,780 birds |
| Mortality rate | 4.4% |
| Total feed consumed | 12,500 kg |
| Feed conversion ratio (IC) | 1.85 |
| Total input cost | 285,000 DZD |
| Revenue from sales | 520,000 DZD |
| Gross margin | 235,000 DZD |

### 20.6 Cash Flow Summary (*Résumé de Trésorerie*)

Period-based summary showing:

- Cash inflows: client payments received
- Cash outflows: supplier settlements made + *dépenses* paid
- Net cash position for the period

### 20.7 Stock Status Report (*État des Stocks*)

Snapshot of current stock levels for all *intrants* and *produits finis*, including current quantity, alert status relative to minimum threshold, and valuation at purchase price.

---

## 22. Consistency & Auditability Review

### 21.1 Accurate Tracking of Supplier Debt

✅ The system maintains a real-time *dette fournisseur globale* calculated from all unpaid and partially paid invoice balances.

✅ Every settlement immediately updates affected invoice balances and the supplier's total debt.

✅ The debt figure is always grounded in actual invoice records derived from validated BLs — never estimated or entered manually.

✅ Disputed invoices (*en litige*) are clearly separated and can be included or excluded from the debt calculation.

### 21.2 Correct Allocation of Payments

✅ All payments use a single, deterministic FIFO algorithm ordered from oldest to most recent invoice — the same input always produces the same allocation.

✅ A settlement allocation record documents exactly how each payment was distributed, invoice by invoice.

✅ Partial coverage of the last reached invoice is the expected and correctly handled outcome when the payment sum is insufficient to cover it fully.

✅ If the payment exceeds the total debt, the surplus is captured as an *acompte* and flagged rather than discarded.

### 21.3 No Financial Double-Counting

✅ Supplier invoices for goods are never automatically converted into *dépenses*.

✅ The separation between *Factures Fournisseurs* (AP) and *Dépenses* (operational expenses) is enforced by system design.

✅ The only permitted link is for service invoices manually marked by the user.

✅ Financial reports draw from mutually exclusive data sources — AP from *factures*, operational costs from *dépenses*.

### 21.4 Full Traceability of Procurement-to-Payment Flows

✅ Every intrant in stock is traceable to a *BL fournisseur*.

✅ Every *facture fournisseur* is composed exclusively of selected, validated BLs — the invoice total is derived directly from those BLs with no manual entry.

✅ Every settlement is traceable to a specific record with date, amount, method, and a line-by-line allocation showing which invoices were covered and by how much.

✅ Full reverse traversal is supported: from any settlement → allocations → invoices → BLs → stock movements.

### 21.5 Practical Usability

✅ The system mirrors how payments actually happen: the owner pays a sum to a supplier; the system handles the accounting allocation automatically.

✅ Invoice creation requires only selecting BLs — no amount entry, no cross-checking required from the user.

✅ Alert mechanisms provide proactive signals without requiring daily report analysis.

✅ All statuses (*payé, non payé, partiellement payé*) are unambiguous, and every financial figure is shown with its underlying breakdown.

---

## 23. Glossary of Key Terms

| Term | Language | Definition |
|------|----------|------------|
| Acompte Fournisseur | FR | Supplier advance — surplus payment credited for future invoices |
| Aliment | FR | Animal feed (in bags or bulk) |
| BL Client | FR | *Bon de Livraison Client* — client delivery note |
| BL Fournisseur | FR | *Bon de Livraison Fournisseur* — supplier delivery note |
| Bâtiment | FR | Farm building / poultry house |
| Consommation | FR | Input consumption attributed to a lot |
| Créance | FR | Receivable (amount owed to the farm by a client) |
| Dépense | FR | Operational expense |
| Dette Fournisseur Globale | FR | Total outstanding debt owed to a supplier |
| Effectif Vivant | FR | Current live bird count in a lot |
| En Litige | FR | In dispute |
| Facture Client | FR | Client invoice (accounts receivable) |
| Facture Fournisseur | FR | Supplier invoice (accounts payable) — built from selected BLs |
| FIFO | EN | First In First Out — oldest invoice settled before more recent ones |
| Fournisseur | FR | Supplier |
| IC (Indice de Consommation) | FR | Feed Conversion Ratio (FCR) |
| Intrant | FR | Input product (feed, chicks, medicine) |
| Litige | FR | Dispute |
| Lot d'Élevage | FR | Poultry batch |
| Médicament | FR | Veterinary medicine / treatment product |
| Mortalité | FR | Bird mortality within a lot |
| Non Payé | FR | Unpaid (invoice status) |
| Partiellement Payé | FR | Partially paid (invoice status) |
| Payé | FR | Fully settled (invoice status) |
| Poussin | FR | Day-old chick |
| Produit Fini | FR | Finished product (live bird, carcass, cut piece, etc.) |
| Règlement Fournisseur | FR | Supplier settlement — a payment sum auto-allocated via FIFO |
| Reste à Payer | FR | Remaining balance on an invoice after allocations |
| Solde | FR | Financial balance |
| Souche | FR | Bird strain or breed |
| Stock Intrants | FR | Inventory of input goods |
| Stock Produits Finis | FR | Inventory of finished/harvested products |
| Trésorerie | FR | Cash flow / treasury |
| Volaille | FR | Poultry (live birds) |
| Volaille Vivante | FR | Live poultry for direct sale |

---

*End of Functional Specification — Élevage Avicole Internal Management System*

---

> **Document prepared by:** Information Systems & Agri-industrial Operations Analysis
> **Applicable standard:** Functional specification (no technical implementation)
> **Target business size:** Small to medium *élevage avicole*, Algeria
> **Version note (v1.1):** Reconciliation logic removed throughout. Invoice creation simplified to BL selection with auto-computed total — no manual amount entry or cross-checking. Supplier payment consolidated into a single FIFO settlement mechanism: user enters a sum, system allocates from oldest invoice to most recent, with partial coverage on the last reached invoice.
> **Next step:** System design phase (database schema, user interface wireframes, workflow validation with operations team)
