# Bibliothèque de prompts — Documents Preuves du Cycle Complet

> **Usage** : chaque prompt ci-dessous est conçu pour être envoyé à GPT-Image-2 **avec les deux images déjà générées en pièces jointes de référence** (`BLF-2026-0001` et `FAC-2026-0001` — CCA Blida). Le modèle doit reproduire exactement la même grammaire visuelle (mise en page, typographie, cadres, tampons, signatures) et ne changer que les champs de données indiqués.
>
> Deux gabarits visuels de référence existent déjà :
> - **Gabarit A — "Bon de Livraison"** (image 1) : utilisé pour tous les BL (Fournisseur *et* Client).
> - **Gabarit B — "Facture"** (image 2) : utilisé pour toutes les factures (Fournisseur *et* Client).
>
> Deux gabarits supplémentaires sont introduits ici, dérivés du même langage visuel (mêmes polices, cadres arrondis, tampons bleus, signatures manuscrites) :
> - **Gabarit C — "Reçu / Quittance de Paiement"** : pour les Règlements Fournisseur et Paiements Client.
> - **Gabarit D — "Justificatif de Dépense"** : documents tiers hétérogènes (paie, énergie, vétérinaire, transport).
>
> Identité canonique **Élevage Avicole Setifien** (reprise telle qu'établie dans les deux images de référence — à ne jamais faire varier) :
> `Route de Batna, Aïn El Kebira, Sétif 19000, Algérie` · Tél `036 51 23 45` · NIF `001916099876543` · NIS `191609987654321`

---

## Style de base commun (à inclure implicitement dans chaque prompt)

```
Photorealistic scan of a professional French-language Algerian business document,
A4 portrait, clean white paper with a thin black outer frame, subtle paper texture
and soft scanner shadow at the edges. Corporate letterhead top-left (small flat
logo icon + bold company name + address/contact block in small sans-serif type).
Top-right boxed title in bold uppercase inside a rounded-corner border, with an
italic subtitle, and a small reference table below it (numéro / date / références).
Two side-by-side bordered rectangular boxes beneath the header for the two
parties. A thin ruled item table with alternating column headers in bold caps:
N° / DÉSIGNATION / SPÉCIFICATION / QUANTITÉ / UNITÉ / PRIX UNIT. (DZD) / MONTANT
(DZD), with 2-3 empty rows left for realism. A bordered "amount in words" box
bottom-left and a small totals table bottom-right. A two-column signature zone
at the bottom ("left party" / "right party"), each with Nom / Fonction / Date /
"Signature & Cachet :" lines, a slightly rotated blue circular or rectangular ink
stamp, and a blue ballpoint-pen handwritten signature scrawl. Centered small
italic legal footer text. All currency in DZD, all text in French, numbers
formatted with spaces as thousand separators (e.g. "64 000,00"). Sharp, legible
text, no distortion, no watermark.
```

---

## Phase 1 — Achats Intrants (10 documents)

### 1. BLF-2026-0002 — Aliments ONAB (lot 1/2) — *Gabarit A*
```
Using Gabarit A exactly (same layout, fonts, stamp style as the CCA Blida bon de
livraison reference), generate a "BON DE LIVRAISON FOURNISSEUR (ORIGINAL)" for
ONAB SETIFIEN.

Issuer (top-left + FOURNISSEUR box) : "ONAB SETIFIEN — Office National des
Aliments du Bétail" — small wheat/grain flat logo icon in green — Route de
Boghni, Setifien, Algérie — Tél : 026 12 34 56 — Email : contact@onab-setif.dz —
RC : 16/00-0000001 B 01 — NIF : 099000000001 — NIS : 099000000001098.

Top-right box : N° BLF-2026-0002, Date : 07/05/2026, Réf. Commande :
CMD-2026-0002.

DESTINATAIRE box : Élevage Avicole Setifien — Route de Batna, Aïn El Kebira,
Sétif 19000, Algérie — Tél : 036 51 23 45 — NIF : 001916099876543 — NIS :
191609987654321.

Transport row : Mode de transport : Camion plateau bâché — Transporteur :
ONAB Logistique — Immatriculation : 16045 118 19. Date d'expédition :
07/05/2026 — Date de livraison : 07/05/2026 — Lieu de livraison : Bâtiment A /
Dépôt Aliments — Élevage Avicole Setifien.

Item table :
1 | Aliment Démarrage 1er Âge | Sac 25 kg — granulés démarrage 0-14j | 200,000 | sac | 1 850,0000 | 370 000,00
2 | Aliment Croissance 2ème Âge | Sac 25 kg — granulés croissance 15-28j | 180,000 | sac | 1 950,0000 | 351 000,00

Amount in words box : "SEPT CENT VINGT ET UN MILLE DINARS ALGÉRIENS (721 000,00 DZD)".
Totals box : TOTAL BRUT 721 000,00 / REMISE 0,00 / NET À PAYER 721 000,00.

Observations : "Livraison des aliments 1er et 2ème âge pour le lot Mai 2026 —
Bâtiment A. Contrôle qualité effectué à réception."

Signature zone — left "REÇU PAR (Élevage Avicole Setifien)": blank Nom/Fonction
lines, Date 07/05/2026, blue rectangular stamp "ÉLEVAGE AVICOLE SETIFIEN /
Route de Batna, Ain El Kebira / Sétif 19000 / NIF : 001916099876543" with a
handwritten signature. Right "LIVRÉ PAR (ONAB Setifien)": Nom "K. Benali",
Fonction "Agent Commercial", Date 07/05/2026, blue circular stamp "ONAB SETIFIEN
— OFFICE NATIONAL DES ALIMENTS DU BÉTAIL" with a handwritten signature.

Footer : "Ce bon de livraison n'est valable que s'il est signé et cacheté par
les deux parties. Une copie doit accompagner la marchandise."
```

### 2. FRN-2026-0002 — Facture Aliments ONAB (lot 1/2) — *Gabarit B*
```
Using Gabarit B exactly (same layout/fonts/stamp style as the CCA Blida facture
reference), generate a "FACTURE FOURNISSEUR — ORIGINALE" for ONAB SETIFIEN, same
issuer letterhead as prompt 1 above.

Top-right box : N° FAC-2026-0002, Date : 08/05/2026, Réf. BL : BLF-2026-0002,
Réf. Commande : CMD-2026-0002, Échéance : 07/06/2026.

FOURNISSEUR / DESTINATAIRE boxes identical to prompt 1.

Item table (same two lines as the BL) :
1 | Aliment Démarrage 1er Âge | Sac 25 kg — granulés démarrage 0-14j | 200,000 | sac | 1 850,0000 | 370 000,00
2 | Aliment Croissance 2ème Âge | Sac 25 kg — granulés croissance 15-28j | 180,000 | sac | 1 950,0000 | 351 000,00

Amount in words box : "SEPT CENT VINGT ET UN MILLE DINARS ALGÉRIENS (721 000,00 DZD)".
Totals box : TOTAL HT 721 000,00 / TVA (19%) 136 990,00 / TOTAL TTC 857 990,00 /
Acompte-Règlement 0,00 / NET À PAYER 857 990,00 (bold last row, shaded).

Observations box : "Facture relative à la livraison d'aliments 1er et 2ème âge
selon BLF-2026-0002 du 07/05/2026."

Signature zone — left "ÉTABLIE PAR (ONAB Setifien)" with green circular ONAB
stamp and handwritten signature, Date 08/05/2026. Right "REÇUE PAR (Élevage
Avicole Setifien)" with the blue rectangular Élevage Avicole Setifien stamp,
blank Nom/Fonction, Date 08/05/2026.

Footer : "Pénalités de retard : 1,5% par mois de retard conformément à la
réglementation en vigueur. Merci de votre confiance."
```

### 3. BLF-2026-0003 — Aliment Finition ONAB (J+20) — *Gabarit A*
```
Same ONAB letterhead/style as prompt 1 (Gabarit A).

Top-right box : N° BLF-2026-0003, Date : 30/05/2026, Réf. Commande :
CMD-2026-0003.

DESTINATAIRE box : identical Élevage Avicole Setifien block as above.

Transport row : Mode de transport : Camion plateau bâché — Transporteur :
ONAB Logistique — Immatriculation : 16045 118 19. Date d'expédition :
30/05/2026 — Date de livraison : 30/05/2026 — Lieu de livraison : Bâtiment A /
Dépôt Aliments — Élevage Avicole Setifien.

Item table :
1 | Aliment Finition 3ème Âge | Sac 25 kg — granulés finition 29j+ | 150,000 | sac | 2 050,0000 | 307 500,00

Amount in words box : "TROIS CENT SEPT MILLE CINQ CENTS DINARS ALGÉRIENS
(307 500,00 DZD)". Totals box : TOTAL BRUT 307 500,00 / REMISE 0,00 / NET À
PAYER 307 500,00.

Observations : "Livraison de l'aliment de finition (3ème âge) pour le lot Mai
2026 — Bâtiment A."

Signature zone identical structure/stamps to prompt 1, dated 30/05/2026, same
signatory "K. Benali — Agent Commercial" for ONAB.
```

### 4. FRN-2026-0003 — Facture Aliment Finition ONAB — *Gabarit B*
```
Same ONAB letterhead/style as prompt 2 (Gabarit B).

Top-right box : N° FAC-2026-0003, Date : 31/05/2026, Réf. BL : BLF-2026-0003,
Réf. Commande : CMD-2026-0003, Échéance : 30/06/2026.

Item table :
1 | Aliment Finition 3ème Âge | Sac 25 kg — granulés finition 29j+ | 150,000 | sac | 2 050,0000 | 307 500,00

Amount in words box : "TROIS CENT SEPT MILLE CINQ CENTS DINARS ALGÉRIENS
(307 500,00 DZD)". Totals box : TOTAL HT 307 500,00 / TVA (19%) 58 425,00 /
TOTAL TTC 365 925,00 / Acompte-Règlement 0,00 / NET À PAYER 365 925,00.

Observations box : "Facture relative à la livraison de l'aliment de finition
selon BLF-2026-0003 du 30/05/2026."

Signature zone identical structure to prompt 2, dated 31/05/2026.
```

### 5. BLF-2026-0004 — Médicaments Sanofi Algérie — *Gabarit A*
```
Using Gabarit A exactly, generate a "BON DE LIVRAISON FOURNISSEUR (ORIGINAL)"
for SANOFI ALGÉRIE (VÉTÉRINAIRE).

Issuer (top-left + FOURNISSEUR box) : "SANOFI ALGÉRIE — Division Santé
Animale" — small red/blue pharmaceutical cross-and-flask flat logo icon — Rue
Hassiba Ben Bouali, Alger, Algérie — Tél : 021 99 00 11 — Email :
sante-animale@sanofi-dz.com — RC : 16/00-0000003 B 03 — NIF : 016000000003 —
NIS : 016000000003987.

Top-right box : N° BLF-2026-0004, Date : 08/05/2026, Réf. Commande :
CMD-2026-0004.

DESTINATAIRE box : Élevage Avicole Setifien (identical canonical block).

Transport row : Mode de transport : Camionnette réfrigérée (chaîne du froid
vaccins) — Transporteur : Sanofi Distribution — Immatriculation : 16 302 041
16. Date d'expédition : 08/05/2026 — Date de livraison : 08/05/2026 — Lieu de
livraison : Bâtiment A — Élevage Avicole Setifien.

Item table :
1 | Vaccin Newcastle (Hitchner B1) | 4 000 doses — conservation 2-8°C | 4 000 | dose | 4,5000 | 18 000,00
2 | Vaccin Gumboro (IBD) | 4 000 doses — souche intermédiaire | 4 000 | dose | 4,8000 | 19 200,00
3 | Amoxicilline 50% poudre | Sachet 500g — usage vétérinaire | 500 | g | 12,0000 | 6 000,00
4 | Vitamines + Électrolytes | Bidon 10L — complexe polyvitaminé | 10 | litre | 850,0000 | 8 500,00

Amount in words box : "CINQUANTE ET UN MILLE SEPT CENTS DINARS ALGÉRIENS
(51 700,00 DZD)". Totals box : TOTAL BRUT 51 700,00 / REMISE 0,00 / NET À
PAYER 51 700,00.

Observations : "Livraison vaccins et produits vétérinaires — chaîne du froid
respectée — lot Mai 2026, Bâtiment A."

Signature zone — right "LIVRÉ PAR (Sanofi Algérie)" : Nom "Dr. Yacine Cherif",
Fonction "Délégué Vétérinaire", blue circular stamp "SANOFI ALGÉRIE — DIVISION
SANTÉ ANIMALE", handwritten signature, Date 08/05/2026. Left identical
"ÉLEVAGE AVICOLE SETIFIEN" stamp as previous documents.
```

### 6. FRN-2026-0004 — Facture Médicaments Sanofi — *Gabarit B*
```
Same Sanofi letterhead/style as prompt 5 (Gabarit B).

Top-right box : N° FAC-2026-0004, Date : 09/05/2026, Réf. BL : BLF-2026-0004,
Réf. Commande : CMD-2026-0004, Échéance : 08/06/2026.

Item table identical to prompt 5's four lines.

Amount in words box : "CINQUANTE ET UN MILLE SEPT CENTS DINARS ALGÉRIENS
(51 700,00 DZD)". Totals box : TOTAL HT 51 700,00 / TVA (19%) 9 823,00 / TOTAL
TTC 61 523,00 / Acompte-Règlement 0,00 / NET À PAYER 61 523,00.

Observations box : "Facture relative à la livraison de vaccins et produits
vétérinaires selon BLF-2026-0004 du 08/05/2026."

Signature zone identical structure to prompt 5, dated 09/05/2026, signatory
"Dr. Yacine Cherif — Délégué Vétérinaire".
```

### 7. REG-2026-0001 — Quittance de paiement CCA (virement) — *Gabarit C (nouveau)*
```
Generate a "REÇU / QUITTANCE DE PAIEMENT" — a simpler single-box receipt in the
exact same visual family as the reference documents (same corporate frame,
fonts, blue ink stamp, handwritten signature) but shorter: no item table, no
transport row — just a payment confirmation slip, roughly half-A4 height,
centered on the page.

Issuer letterhead top-left : CCA BLIDA (same logo/contact block as the BLF
reference).

Boxed title top-right : "QUITTANCE DE RÈGLEMENT" — N° REG-2026-0001 — Date :
10/05/2026.

Body text in a bordered box : "Reçu de : Élevage Avicole Setifien — la somme
de SOIXANTE-QUATRE MILLE DINARS ALGÉRIENS (64 000,00 DZD) — Mode de paiement :
Virement bancaire — Référence : VIR-BNA-10052026-001 — En règlement de la
facture N° FRN-2026-0001 du 06/05/2026 — Facture soldée intégralement."

Signature zone : single box, right-aligned, "POUR CCA BLIDA" — Nom "Y.
Bouzidi", Fonction "Responsable Logistique", blue circular CCA Blida stamp,
handwritten signature, Date 10/05/2026.

Footer : small italic "Ce document tient lieu de quittance de paiement."
```

### 8. REG-2026-0002 — Quittance acompte ONAB (chèque) — *Gabarit C*
```
Same Gabarit C structure as prompt 7, ONAB Setifien letterhead (same as prompt
1's FOURNISSEUR block).

Boxed title : "QUITTANCE DE RÈGLEMENT" — N° REG-2026-0002 — Date : 10/05/2026.

Body box : "Reçu de : Élevage Avicole Setifien — la somme de QUATRE CENT MILLE
DINARS ALGÉRIENS (400 000,00 DZD) — Mode de paiement : Chèque bancaire N°
0455 — Banque tirée : BNA Agence Setifien Centre — En règlement partiel de la
facture N° FRN-2026-0002 du 08/05/2026 (montant facture : 721 000,00 DZD) —
Reste à payer après ce règlement : 321 000,00 DZD."

Signature zone : "POUR ONAB SETIFIEN" — Nom "K. Benali", Fonction "Agent
Commercial", green circular ONAB stamp, handwritten signature, Date
10/05/2026.
```

### 9. REG-2026-0003 — Quittance solde ONAB (virement) — *Gabarit C*
```
Same Gabarit C structure, ONAB Setifien letterhead.

Boxed title : "QUITTANCE DE RÈGLEMENT" — N° REG-2026-0003 — Date : 25/05/2026.

Body box : "Reçu de : Élevage Avicole Setifien — la somme de TROIS CENT VINGT
ET UN MILLE DINARS ALGÉRIENS (321 000,00 DZD) — Mode de paiement : Virement
bancaire — Référence : VIR-BNA-25052026-002 — En règlement du solde de la
facture N° FRN-2026-0002 du 08/05/2026 — Facture soldée intégralement."

Signature zone : "POUR ONAB SETIFIEN" — Nom "K. Benali", Fonction "Agent
Commercial", green circular ONAB stamp, handwritten signature, Date
25/05/2026.
```

### 10. REG-2026-0004 — Quittance Sanofi (virement) — *Gabarit C*
```
Same Gabarit C structure, Sanofi Algérie letterhead (same as prompt 5's
FOURNISSEUR block).

Boxed title : "QUITTANCE DE RÈGLEMENT" — N° REG-2026-0004 — Date : 15/05/2026.

Body box : "Reçu de : Élevage Avicole Setifien — la somme de CINQUANTE ET UN
MILLE SEPT CENTS DINARS ALGÉRIENS (51 700,00 DZD) — Mode de paiement :
Virement bancaire — Référence : VIR-BNA-15052026-003 — En règlement intégral de
la facture N° FRN-2026-0004 du 09/05/2026 — Facture soldée intégralement."

Signature zone : "POUR SANOFI ALGÉRIE" — Nom "Dr. Yacine Cherif", Fonction
"Délégué Vétérinaire", blue circular Sanofi stamp, handwritten signature,
Date 15/05/2026.
```

---

## Phase 6 — Ventes & Livraison Client (9 documents)

> Ici **Élevage Avicole Setifien devient l'émetteur** (bloc FOURNISSEUR/gauche),
> et les clients deviennent DESTINATAIRE (bloc droit). Utiliser exactement le
> logo/bloc contact canonique de la ferme établi plus haut, en position
> "émetteur" cette fois (miroir du rôle qu'elle occupait comme DESTINATAIRE
> dans les documents d'achat).

### 11. BLC-2026-0001 — BL Client Marché de Gros Setifien — *Gabarit A*
```
Using Gabarit A exactly, generate a "BON DE LIVRAISON CLIENT (ORIGINAL)".

Issuer (top-left + block gauche, now labeled "EXPÉDITEUR") : Élevage Avicole
Setifien — small stylized rooster/chicken flat logo icon in orange/brown —
Route de Batna, Aïn El Kebira, Sétif 19000, Algérie — Tél : 036 51 23 45 — NIF
: 001916099876543 — NIS : 191609987654321.

Top-right box : N° BLC-2026-0001, Date : 20/06/2026.

DESTINATAIRE box : Marché de Gros Setifien — Zone de marché, Route nationale
5, Setifien, Algérie — Tél : 0555 11 22 33.

Transport row : Mode de transport : Camion frigorifique — Transporteur :
Transport Express Algérie — Immatriculation : 19 220 077 19. Date
d'expédition : 20/06/2026 — Date de livraison : 20/06/2026 — Lieu de
livraison : Zone de marché, Route nationale 5, Setifien.

Item table :
1 | Poulet vivant | Poids moyen 2,100 kg | 300,000 | unite | 480,0000 | 144 000,00
2 | Carcasse entière vidée | Découpe complète, chaîne du froid | 800,000 | kg | 750,0000 | 600 000,00

Amount in words box : "SEPT CENT QUARANTE-QUATRE MILLE DINARS ALGÉRIENS
(744 000,00 DZD)". Totals box : TOTAL BRUT 744 000,00 / REMISE 0,00 / NET À
PAYER 744 000,00.

Signature zone — left "LIVRÉ PAR (Élevage Avicole Setifien)" with the farm's
own blue rectangular stamp and a handwritten signature. Right "REÇU PAR
(Marché de Gros Setifien)" : Nom "Boualem Khaled", Fonction "Réceptionnaire",
generic market receiving stamp, Date 20/06/2026.
```

### 12. BLC-2026-0002 — BL Client Boucherie Amrane & Fils — *Gabarit A*
```
Same issuer block as prompt 11 (Élevage Avicole Setifien).

Top-right box : N° BLC-2026-0002, Date : 21/06/2026.

DESTINATAIRE box : Boucherie Amrane & Fils — Setifien, Algérie — Tél : 0660
33 44 55.

Transport row : Mode de transport : Camionnette frigorifique — Transporteur :
Transport Express Algérie — Immatriculation : 19 220 077 19. Date
d'expédition/livraison : 21/06/2026 — Lieu de livraison : Boucherie Amrane &
Fils, Setifien.

Item table :
1 | Poulet vivant | Poids moyen 2,100 kg | 200,000 | unite | 480,0000 | 96 000,00
2 | Carcasse entière vidée | Découpe complète, chaîne du froid | 400,000 | kg | 750,0000 | 300 000,00

Amount in words box : "TROIS CENT QUATRE-VINGT-SEIZE MILLE DINARS ALGÉRIENS
(396 000,00 DZD)". Totals box : TOTAL BRUT 396 000,00 / REMISE 0,00 / NET À
PAYER 396 000,00.

Signature zone — right "REÇU PAR (Boucherie Amrane & Fils)" : Nom "Rachid
Amrane", Fonction "Gérant", small butcher-shop rubber stamp, Date 21/06/2026.
```

### 13. BLC-2026-0003 — BL Client Restaurant Le Palmier — *Gabarit A*
```
Same issuer block as prompt 11.

Top-right box : N° BLC-2026-0003, Date : 22/06/2026.

DESTINATAIRE box : Restaurant Le Palmier — Setifien, Algérie — Tél : 0770 22
33 44.

Transport row : Mode de transport : Camionnette frigorifique — Transporteur :
Transport Express Algérie — Immatriculation : 19 220 077 19. Date
d'expédition/livraison : 22/06/2026 — Lieu de livraison : Restaurant Le
Palmier, Setifien.

Item table :
1 | Carcasse entière vidée | Découpe complète, chaîne du froid | 257,000 | kg | 780,0000 | 200 460,00

Amount in words box : "DEUX CENT MILLE QUATRE CENT SOIXANTE DINARS ALGÉRIENS
(200 460,00 DZD)". Totals box : TOTAL BRUT 200 460,00 / REMISE 0,00 / NET À
PAYER 200 460,00.

Signature zone — right "REÇU PAR (Restaurant Le Palmier)" : Nom "Sonia
Belkacem", Fonction "Responsable Achats", restaurant rubber stamp, Date
22/06/2026.
```

### 14. FAC-2026-0001 — Facture Client Marché de Gros — *Gabarit B*
```
Using Gabarit B exactly. Issuer letterhead identical to prompt 11 (Élevage
Avicole Setifien, now the invoicing party).

Top-right box : N° FAC-2026-0001, Date : 20/06/2026, Réf. BL : BLC-2026-0001,
Échéance : 20/07/2026.

DESTINATAIRE box : Marché de Gros Setifien (same as prompt 11).

Item table identical to prompt 11's two lines.

Amount in words box : "SEPT CENT QUARANTE-QUATRE MILLE DINARS ALGÉRIENS
(744 000,00 DZD)". Totals box : TOTAL HT 744 000,00 / TVA (0% — volaille
exonérée) 0,00 / TOTAL TTC 744 000,00 / Acompte-Règlement 744 000,00 / NET À
PAYER 0,00 — visually stamp diagonally in red across the totals box : "PAYÉE".

Observations box : "Facture relative à la livraison selon BLC-2026-0001 du
20/06/2026. Produits avicoles — exonérés de TVA."

Signature zone — left "ÉTABLIE PAR (Élevage Avicole Setifien)" with farm
stamp. Right "REÇUE PAR (Marché de Gros Setifien)" with market stamp, Date
20/06/2026.
```

### 15. FAC-2026-0002 — Facture Client Boucherie Amrane — *Gabarit B*
```
Same issuer letterhead as prompt 14.

Top-right box : N° FAC-2026-0002, Date : 21/06/2026, Réf. BL : BLC-2026-0002,
Échéance : 21/07/2026.

DESTINATAIRE box : Boucherie Amrane & Fils (same as prompt 12).

Item table identical to prompt 12's two lines.

Amount in words box : "TROIS CENT QUATRE-VINGT-SEIZE MILLE DINARS ALGÉRIENS
(396 000,00 DZD)". Totals box : TOTAL HT 396 000,00 / TVA (0%) 0,00 / TOTAL
TTC 396 000,00 / Acompte-Règlement 200 000,00 / NET À PAYER 196 000,00 — stamp
diagonally in orange across the totals box : "PARTIELLEMENT PAYÉE".

Observations box : "Facture relative à la livraison selon BLC-2026-0002 du
21/06/2026. Produits avicoles — exonérés de TVA."

Signature zone identical structure to prompt 14, right side stamped by
Boucherie Amrane & Fils, Date 21/06/2026.
```

### 16. FAC-2026-0003 — Facture Client Restaurant Le Palmier — *Gabarit B*
```
Same issuer letterhead as prompt 14.

Top-right box : N° FAC-2026-0003, Date : 22/06/2026, Réf. BL : BLC-2026-0003,
Échéance : 22/07/2026.

DESTINATAIRE box : Restaurant Le Palmier (same as prompt 13).

Item table identical to prompt 13's single line.

Amount in words box : "DEUX CENT MILLE QUATRE CENT SOIXANTE DINARS ALGÉRIENS
(200 460,00 DZD)". Totals box : TOTAL HT 200 460,00 / TVA (0%) 0,00 / TOTAL
TTC 200 460,00 / Acompte-Règlement 200 460,00 / NET À PAYER 0,00 — stamp
diagonally in red across the totals box : "PAYÉE".

Observations box : "Facture relative à la livraison selon BLC-2026-0003 du
22/06/2026. Produits avicoles — exonérés de TVA."

Signature zone identical structure to prompt 14, right side stamped by
Restaurant Le Palmier, Date 22/06/2026.
```

### 17. Paiement 1 — Reçu Marché de Gros (espèces) — *Gabarit C*
```
Same Gabarit C structure as the fournisseur quittances, but issuer letterhead
is now Élevage Avicole Setifien (the farm is issuing the receipt to its
client).

Boxed title : "REÇU DE PAIEMENT CLIENT" — N° PAY-0001 — Date : 20/06/2026.

Body box : "Reçu de : Marché de Gros Setifien — la somme de SEPT CENT
QUARANTE-QUATRE MILLE DINARS ALGÉRIENS (744 000,00 DZD) — Mode de paiement :
Espèces — En règlement intégral de la facture N° FAC-2026-0001 du 20/06/2026
— Facture soldée intégralement."

Signature zone : "POUR ÉLEVAGE AVICOLE SETIFIEN" with the farm's blue
rectangular stamp and handwritten signature, Date 20/06/2026.
```

### 18. Paiement 2 — Reçu Boucherie Amrane (chèque) — *Gabarit C*
```
Same Gabarit C structure, farm letterhead.

Boxed title : "REÇU DE PAIEMENT CLIENT" — N° PAY-0002 — Date : 21/06/2026.

Body box : "Reçu de : Boucherie Amrane & Fils — la somme de DEUX CENT MILLE
DINARS ALGÉRIENS (200 000,00 DZD) — Mode de paiement : Chèque N°
CHQ-AMRANE-1044 — En règlement partiel de la facture N° FAC-2026-0002 du
21/06/2026 (montant facture : 396 000,00 DZD) — Reste à payer après ce
règlement : 196 000,00 DZD."

Signature zone : "POUR ÉLEVAGE AVICOLE SETIFIEN" with farm stamp, Date
21/06/2026.
```

### 19. Paiement 3 — Reçu Restaurant Le Palmier (virement) — *Gabarit C*
```
Same Gabarit C structure, farm letterhead.

Boxed title : "REÇU DE PAIEMENT CLIENT" — N° PAY-0003 — Date : 22/06/2026.

Body box : "Reçu de : Restaurant Le Palmier — la somme de DEUX CENT MILLE
QUATRE CENT SOIXANTE DINARS ALGÉRIENS (200 460,00 DZD) — Mode de paiement :
Virement bancaire — Référence : VIR-PALMIER-22062026 — En règlement intégral
de la facture N° FAC-2026-0003 du 22/06/2026 — Facture soldée intégralement."

Signature zone : "POUR ÉLEVAGE AVICOLE SETIFIEN" with farm stamp, Date
22/06/2026.
```

---

## Phase 7 — Dépenses Opérationnelles (4 documents)

> Ces justificatifs sont volontairement plus hétérogènes visuellement — chacun
> vient d'un tiers différent — mais doivent rester dans la même famille
> "scan de document papier réaliste algérien" que les autres.

### 20. DEP-001 — Bordereau de paie juin 2026 — *Gabarit D (nouveau)*
```
Generate an internal "BORDEREAU DE PAIE COLLECTIF" — a simple typed internal
company form (not fancy, plain black-and-white, printed on Élevage Avicole
Setifien letterhead, no elaborate stamp needed — just a signature).

Header : Élevage Avicole Setifien (canonical block), title "BORDEREAU DE PAIE
— JUIN 2026", Référence : FP-JUIN-2026, Date d'émission : 30/06/2026.

A simple 3-row table : "Nom | Fonction | Montant (DZD)" listing three
anonymized farm workers ("Ouvrier 1 — Manutention élevage — 15 000,00",
"Ouvrier 2 — Manutention élevage — 15 000,00", "Ouvrier 3 — Manutention
élevage — 15 000,00"), with a bold total row "TOTAL — 45 000,00 DZD".

Note line below the table : "Lot attribué : Lot Mai 2026 — Bâtiment A — Mode
de paiement : Espèces."

Bottom-right : "Établi par : le Gérant" with a plain handwritten signature,
no stamp needed (internal document). Simple black ruled table, no color, no
logo artwork beyond the plain text company name at top.
```

### 21. DEP-002 — Facture Sonelgaz (électricité) — *Gabarit B variant*
```
Generate a generic Algerian public-utility electricity invoice in a distinct
visual identity from the farm/supplier documents (different color accent —
use a plain green/blue header band, generic utility-company aesthetic,
without reproducing any specific real trademarked logo artwork — just a plain
bold wordmark reading "SONELGAZ — Société Nationale de l'Électricité et du
Gaz" in a green sans-serif font, and a simple lightning-bolt + flame line-icon
that is clearly generic, not the real corporate logo).

Header info : Compte client : "0455-8854-Setif" — Adresse de consommation :
Route de Batna, Aïn El Kebira, Sétif 19000 (Bâtiment A) — Titulaire : Élevage
Avicole Setifien.

Boxed title top-right : "FACTURE D'ÉLECTRICITÉ" — N° SONELGAZ-2026-06-8854 —
Période : Juin 2026 — Date d'émission : 30/06/2026 — Date limite de paiement :
15/07/2026.

A simple consumption table : "Ancien index | Nouvel index | Consommation
(kWh) | Prix unitaire | Montant" with one row of plausible values summing to
18 000,00 DZD total, and a bold "MONTANT À PAYER : 18 000,00 DZD" box.

Footer : small print about payment methods (Algérie Poste, agences Sonelgaz,
virement), no stamp/signature needed — a normal utility bill has none.
```

### 22. DEP-003 — Reçu honoraires vétérinaire — *Gabarit D*
```
Generate a simple veterinary consultation receipt, styled like a small
prescription-pad slip (narrower proportions, ~A5 look scanned onto A4), plain
white with a thin border, minimal branding.

Header : "Dr. Ammar Bouzid — Docteur Vétérinaire" with a small caduceus/paw
line-icon, "Cabinet Vétérinaire Rural — Sétif", Tél : 0661 22 33 44.

Body : "Reçu de : Élevage Avicole Setifien — Objet : Visite sanitaire +
diagnostic coccidiose — lot Mai 2026, Bâtiment A — Date : 05/06/2026 —
Montant honoraires : 12 000,00 DZD — Mode de paiement : Espèces."

A short handwritten note below in blue ink, doctor's cursive handwriting
style : "Protocole : Amoxicilline 50% — 250g — traitement 5 jours." with a
signature and a small round veterinary practice ink stamp.
```

### 23. DEP-004 — Reçu de transport — *Gabarit D*
```
Generate a very simple generic handwritten-style cash receipt ("carnet de
reçus" / duplicata book style) — a small pre-printed receipt template with a
red decorative border commonly used by independent transporters in Algeria,
partially filled in by hand in blue ballpoint pen.

Pre-printed header text (plain, generic) : "REÇU N° _____ — Date : ____/____/
______".
Handwritten fill-ins : Reçu N° "0231", Date "20/06/2026".
Handwritten body : "Reçu de Élevage Avicole Setifien la somme de HUIT MILLE
CINQ CENTS DINARS ALGÉRIENS (8 500,00 DZD) pour : Transport abattage +
livraisons clients — 20 & 21 juin 2026."
Pre-printed footer line : "Signature :" with a simple handwritten scrawl next
to it. Mode de paiement (handwritten) : "Espèces". No company stamp — this
is an informal individual transporter's receipt.
```

---

## Récapitulatif — 23 prompts / 25 documents totaux du cycle

| # | Document | Gabarit | Déjà généré |
|---|---|---|---|
| — | BLF-2026-0001 (CCA) | A | ✅ (référence fournie) |
| — | FAC-2026-0001 (CCA) | B | ✅ (référence fournie) |
| 1 | BLF-2026-0002 (ONAB) | A | à générer |
| 2 | FRN-2026-0002 (ONAB) | B | à générer |
| 3 | BLF-2026-0003 (ONAB finition) | A | à générer |
| 4 | FRN-2026-0003 (ONAB finition) | B | à générer |
| 5 | BLF-2026-0004 (Sanofi) | A | à générer |
| 6 | FRN-2026-0004 (Sanofi) | B | à générer |
| 7-10 | REG-2026-0001 à 0004 | C | à générer |
| 11-13 | BLC-2026-0001 à 0003 | A | à générer |
| 14-16 | FAC-2026-0001 à 0003 (client) | B | à générer |
| 17-19 | Paiements client 1-3 | C | à générer |
| 20-23 | DEP-001 à DEP-004 | D | à générer |

**Note sur REG-2026-0002/0003/0004 et Paiements 2/3** : les références de
paiement `VIR-BNA-25052026-002`, `VIR-BNA-15052026-003` ne sont pas données
dans le scénario source — je les ai construites par cohérence avec le format
`VIR-BNA-10052026-001` déjà établi ; ajustez librement si vous avez une autre
convention.
