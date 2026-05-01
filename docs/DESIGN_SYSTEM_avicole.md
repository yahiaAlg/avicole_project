# Élevage Avicole — Design System Specification
> Version 1.0 — Internal Farm Management Platform (Django / Bootstrap 5)

---

## 1. Design Philosophy

**Aesthetic direction:** Operational Ground Truth — dark, dense, ledger-like. The interface is built for a farm owner who needs to read a debt figure, a live bird count, or a stock level at a glance — under any lighting condition, with zero visual noise. Every element earns its space. Green is the only warm accent: it signals life, production, and financial health against a cold slate background.

**Core principles:**
- Dark-first. No light mode.
- Data is the hero — DZD figures, lot counts, and stock quantities must be immediately readable.
- One accent color only (`--accent: #22c55e`). Used for primary actions, active navigation, and positive financial states.
- Borders over shadows for structural separation. No decorative gradients.
- French domain labels rendered exactly as specified (BL, Lot, Facture, Règlement — never translated).
- Animations are subtle, fast (150–300ms), and purposeful — never decorative.

---

## 2. Color Palette

### Base Surfaces (darkest → lightest)
| Token | Hex | Usage |
|---|---|---|
| `--bg-base` | `#0c0e10` | Page background |
| `--bg-surface` | `#12151a` | Cards, sidebar, topbar |
| `--bg-raised` | `#191d24` | Nested cards, dropdowns, inputs |
| `--bg-hover` | `#1f242d` | Row / item hover state |

### Borders
| Token | Value | Usage |
|---|---|---|
| `--border` | `rgba(255,255,255,.07)` | Default dividers, card edges |
| `--border-strong` | `rgba(255,255,255,.12)` | Focused inputs, hovered cards |

### Accent (Green)
| Token | Value | Usage |
|---|---|---|
| `--accent` | `#22c55e` | Primary CTA, active nav, positive indicators |
| `--accent-dim` | `rgba(34,197,94,.13)` | Accent backgrounds, highlight rows |
| `--accent-glow` | `rgba(34,197,94,.30)` | Focus rings |

### Text
| Token | Hex | Usage |
|---|---|---|
| `--text-primary` | `#edf0f3` | Headings, values, primary content |
| `--text-secondary` | `#8892a0` | Body text, table cells, descriptions |
| `--text-muted` | `#50586a` | Labels, timestamps, placeholders |

### Semantic Colors
| Role | Hex | Alpha bg | Usage |
|---|---|---|---|
| Success | `#22c55e` / display `#4ade80` | `rgba(34,197,94,.12)` | Payé, stock normal, closed lot |
| Danger | `#ef4444` / display `#f87171` | `rgba(239,68,68,.12)` | Overdue, rupture stock, litige |
| Warning | `#f59e0b` / display `#fbbf24` | `rgba(245,158,11,.15)` | Partiellement payé, seuil, brouillon |
| Info | `#38bdf8` / display `#7dd3fc` | `rgba(56,189,248,.12)` | Neutral status, reçu, livré |

### Status Pill Mapping

| Status | Pill Class | Label |
|--------|-----------|-------|
| Lot open | `pill-success` | Ouvert |
| Lot closed | `pill-neutral` | Clôturé |
| BL Brouillon | `pill-warning` | Brouillon |
| BL Reçu | `pill-info` | Reçu |
| BL Facturé | `pill-success` | Facturé |
| BL En Litige | `pill-danger` | En Litige |
| Non Payé | `pill-danger` | Non Payé |
| Partiellement Payé | `pill-warning` | Part. Payé |
| Payé | `pill-success` | Payé |
| Stock Rupture | `pill-danger` | Rupture |
| Stock Seuil | `pill-warning` | Seuil |
| Stock Normal | `pill-success` | Normal |

### Chart / Data Visualization Color Sequence
Used in order for multi-series charts, doughnuts, bar charts:
```
#22c55e  (green — primary)
#38bdf8  (sky blue)
#f59e0b  (amber)
#c4b5fd  (violet)
#fb923c  (orange)
#5eead4  (teal)
#f9a8d4  (pink)
#a5b4fc  (indigo)
#f87171  (red)
```

### Icon Background Variants (utility classes)
```css
.ic-green  → rgba(34,197,94,.12)  / #4ade80
.ic-blue   → rgba(56,189,248,.12) / #7dd3fc
.ic-amber  → rgba(245,158,11,.15) / #fbbf24
.ic-purple → rgba(167,139,250,.12)/ #c4b5fd
.ic-red    → rgba(239,68,68,.12)  / #f87171
.ic-teal   → rgba(45,212,191,.12) / #5eead4
.ic-orange → rgba(251,146,60,.12) / #fb923c
.ic-indigo → rgba(99,102,241,.12) / #a5b4fc
```

---

## 3. Typography

### Font Stack
| Role | Family | Weights | Usage |
|---|---|---|---|
| Display / UI | **Syne** (Google Fonts) | 400, 600, 700, 800 | Headings, metric values, card titles, nav labels, buttons |
| Body / Data | **DM Sans** (Google Fonts) | 300, 400, 500 | Body text, table cells, form inputs, descriptions |

```html
<!-- Required in <head> -->
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;1,9..40,300&display=swap" rel="stylesheet">
```

### Type Scale
| Element | Font | Size | Weight | Letter-spacing | Color |
|---|---|---|---|---|---|
| Page heading (H1) | Syne | 22px | 800 | `-0.025em` | `--text-primary` |
| Card / section title | Syne | 14px | 700 | `-0.01em` | `--text-primary` |
| Metric value (large) | Syne | 26px | 800 | none | `--text-primary` |
| Metric value (small) | Syne | 20px | 800 | none | `--text-primary` |
| Nav link label | DM Sans | 13px | 400 | none | `--text-secondary` |
| Body / table cell | DM Sans | 13px | 400 | none | `--text-secondary` |
| Section eyebrow | DM Sans | 10.5px | 600 | `0.1em` | `--text-muted` |
| Table header | DM Sans | 10.5px | 600 | `0.07em` | `--text-muted` |
| Badge / pill | DM Sans | 11px | 500 | `0.02em` | varies |
| Caption / timestamp | DM Sans | 11–12px | 400 | none | `--text-muted` |

---

## 4. Spacing & Layout

### CSS Variables
```css
--sidebar-w:    240px;   /* expanded */
--sidebar-w-sm: 68px;    /* collapsed */
--topbar-h:     60px;
```

### Border Radius
| Token | Value | Usage |
|---|---|---|
| `--radius-sm` | `6px` | Buttons, inputs, icon boxes, nav links |
| `--radius-md` | `10px` | Dropdowns, toasts, form fields |
| `--radius-lg` | `16px` | Cards, chart panels, main containers |

### Page Padding
- Desktop page body: `28px 32px`
- Mobile page body: `20px 16px`
- Card internal padding: `18–22px 20px`
- Table cell padding: `13px 14px`

### Grid System
- Bootstrap 5 `row / col-*` for responsive column grids.
- Dashboard KPI row: 4-column `col-xl-3 col-md-6`.
- Two-column splits: `col-xl-8 / col-xl-4` or `col-xl-7 / col-xl-5`.
- Gap between cards: `14–18px` (`gap: 14px` or `g-3` in Bootstrap).

---

## 5. Component Library

### Cards
```css
/* Standard card */
background: var(--bg-surface);
border: 1px solid var(--border);
border-radius: var(--radius-lg);

/* On hover: */
border-color: var(--border-strong);
transform: translateY(-1px);
/* Optional accent top line on hover */
::after { height: 2px; background: var(--accent); top: 0; border-radius: var(--radius-lg) var(--radius-lg) 0 0; }
```

### Metric Cards
- Icon box: 40–44px square, `var(--radius-sm)`, uses `.ic-*` color variant.
- Eyebrow label: 10.5px, uppercase, `--text-muted`, `letter-spacing: .1em`.
- Value: Syne 800, 20–26px, `--text-primary`.
- Delta badge: pill with arrow icon, semantic color bg at 12% opacity.

### Pills / Badges
```css
/* Base */
display: inline-flex; align-items: center; gap: 4px;
padding: 3px 9px; border-radius: 20px;
font-size: 11px; font-weight: 500; font-family: 'DM Sans', sans-serif;

/* Variants */
.pill-success → bg rgba(34,197,94,.12),  color #4ade80
.pill-danger  → bg rgba(239,68,68,.12),  color #f87171
.pill-warning → bg rgba(245,158,11,.15), color #fbbf24
.pill-info    → bg rgba(56,189,248,.12), color #7dd3fc
.pill-neutral → bg var(--bg-hover),      color var(--text-secondary)
```

### Tables
```css
/* Header row */
font-size: 10.5px; font-weight: 600; letter-spacing: 0.07em;
text-transform: uppercase; color: var(--text-muted);
border-bottom: 1px solid var(--border);

/* Data cells */
font-size: 13px; color: var(--text-secondary);
padding: 13px 14px;
border-bottom: 1px solid var(--border);

/* Row hover */
background: var(--bg-hover);
```

### Buttons
```css
/* Primary (accent) */
background: var(--accent); color: #0c0e10;
font-family: 'Syne', sans-serif; font-weight: 700; font-size: 13.5px;
border-radius: var(--radius-md); padding: 10px 18px;
transition: opacity .18s, transform .12s;
:hover { opacity: .88 }
:active { transform: scale(.99) }

/* Ghost / outline */
background: transparent;
border: 1px solid var(--border);
color: var(--text-secondary);
:hover { background: var(--bg-hover); border-color: var(--border-strong); color: var(--text-primary); }

/* Danger */
background: rgba(239,68,68,.12);
border: 1px solid rgba(239,68,68,.25);
color: #f87171;

/* Warning (e.g. litige actions) */
background: rgba(245,158,11,.12);
border: 1px solid rgba(245,158,11,.25);
color: #fbbf24;
```

### Form Inputs
```css
background: var(--bg-surface);
border: 1px solid var(--border);
border-radius: var(--radius-md);
color: var(--text-primary);
font-family: 'DM Sans', sans-serif; font-size: 13.5px;
padding: 11px 13px 11px 40px; /* 40px left = icon space */

:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(34,197,94,.15);
  outline: none;
}
::placeholder { color: var(--text-muted); }
```
- Left icon: `position: absolute; left: 13px;` — Bootstrap Icon, `--text-muted`, transitions to `--accent` on focus.
- Labels: 12px, weight 500, `--text-secondary`, `letter-spacing: .03em`, block above input.
- Error messages: 12px, `#f87171`, flex row with `bi-x-circle` icon.
- Read-only / locked fields (e.g. auto-computed facture total): `opacity: .6`, `cursor: not-allowed`, `bg: var(--bg-raised)`.

### Navigation (Sidebar)
- Width: 240px expanded / 68px collapsed. Toggle persists in `localStorage`.
- Active link: `color: var(--accent)`, `background: var(--accent-dim)`, left `3px` green bar `::before`.
- Section group labels: 9.5px, uppercase, `--text-muted`, `letter-spacing: .1em`; hidden on collapse.
- Collapsed state: `.nav-link-label`, `.brand-text`, `.user-info` → `opacity: 0; width: 0; overflow: hidden`.

### Alerts / Toasts
```css
background: var(--bg-raised);
border: 1px solid var(--border-strong);
border-radius: var(--radius-md);
padding: 12px 16px;
box-shadow: 0 8px 32px rgba(0,0,0,.45);
border-left: 3px solid <semantic color>;
```
Auto-dismiss after 4500ms with `opacity → 0` + `translateX(20px)` transition.

### FIFO Preview Block

Used on the *Règlement Fournisseur* form to show the auto-allocation before confirmation.

```html
<div class="fifo-preview">
  <div class="fifo-preview__header">
    <i class="bi bi-distribute-vertical"></i> Allocation FIFO automatique
  </div>
  <div class="fifo-preview__row">
    <span class="fifo-preview__ref">F-2025-011</span>
    <span class="fifo-preview__amount">25 000 DZD</span>
    <span class="pill-success">Payé</span>
  </div>
  <div class="fifo-preview__row fifo-preview__row--partial">
    <span class="fifo-preview__ref">F-2025-019</span>
    <span class="fifo-preview__amount">45 000 DZD</span>
    <span class="pill-warning">Part. Payé</span>
  </div>
  <div class="fifo-preview__footer">
    Nouvelle dette : <strong>45 000 DZD</strong>
  </div>
</div>
```
```css
.fifo-preview { background: var(--bg-raised); border: 1px solid var(--border-strong); border-radius: var(--radius-md); padding: 14px 16px; margin-top: 14px; }
.fifo-preview__header { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px; }
.fifo-preview__row { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.fifo-preview__row--partial { color: #fbbf24; }
.fifo-preview__ref { flex: 1; font-family: 'DM Sans', monospace; color: var(--text-secondary); }
.fifo-preview__footer { font-size: 12px; color: var(--text-muted); margin-top: 10px; }
```

### Lot Card (Dashboard)

Compact at-a-glance card per open lot:

```html
<div class="lot-card">
  <div class="lot-card__header">
    <span class="lot-card__name">Lot Avril 2025 — Bât. 1</span>
    <span class="pill-success">Ouvert</span>
  </div>
  <div class="lot-card__grid">
    <div><div class="lot-stat-label">Effectif vivant</div><div class="lot-stat-val">4 780</div></div>
    <div><div class="lot-stat-label">Mortalité</div><div class="lot-stat-val danger">4.4%</div></div>
    <div><div class="lot-stat-label">IC estimé</div><div class="lot-stat-val">1.85</div></div>
    <div><div class="lot-stat-label">Jours</div><div class="lot-stat-val">28</div></div>
  </div>
</div>
```
```css
.lot-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 16px 18px; }
.lot-card__header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.lot-card__name { font-family: 'Syne', sans-serif; font-weight: 700; font-size: 13.5px; color: var(--text-primary); }
.lot-card__grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.lot-stat-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--text-muted); margin-bottom: 4px; }
.lot-stat-val { font-family: 'Syne', sans-serif; font-weight: 800; font-size: 18px; color: var(--text-primary); }
.lot-stat-val.danger { color: #f87171; }
```

### Debt Indicator Bar (*Dette Fournisseur*)

```html
<div class="debt-bar">
  <div class="debt-bar__label">
    <span>Dette — Fournisseur Tahar</span>
    <span class="debt-bar__amount">115 000 DZD</span>
  </div>
  <div class="debt-bar__track">
    <div class="debt-bar__fill" style="width: 72%"></div>
  </div>
</div>
```
```css
.debt-bar__track { height: 4px; background: var(--bg-hover); border-radius: 4px; margin-top: 6px; }
.debt-bar__fill { height: 100%; border-radius: 4px; background: var(--accent); transition: width .4s ease; }
/* Fill turns warning at >60%, danger at >85% via JS class toggle */
.debt-bar__fill.warn { background: #f59e0b; }
.debt-bar__fill.crit { background: #ef4444; }
```

---

## 6. Iconography

**Library:** Bootstrap Icons 1.11.3
**CDN:** `https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css`
**Usage:** `<i class="bi bi-{icon-name}"></i>`

### Icon Size Conventions
| Context | Size |
|---|---|
| Sidebar nav | `16px` |
| Metric card icon box | `18–20px` |
| Form field prefix | `15px` |
| Topbar action buttons | `15px` |
| Table action icons | `14px` |
| Inline body text | `13–14px` |

### Standard Icon Mapping
| Module / Concept | Icon |
|---|---|
| Dashboard | `bi-speedometer2` |
| Lot d'Élevage | `bi-grid-3x3-gap-fill` |
| Consommation | `bi-droplet-fill` |
| Mortalité | `bi-heartbreak` |
| Production | `bi-arrow-repeat` |
| Intrants / Catalog | `bi-boxes` |
| Stock Intrants | `bi-archive-fill` |
| Stock Produits Finis | `bi-bag-check-fill` |
| Fournisseur | `bi-building` |
| BL Fournisseur | `bi-truck` |
| Facture Fournisseur | `bi-file-earmark-text` |
| Règlement Fournisseur | `bi-cash-coin` |
| Client | `bi-people-fill` |
| BL Client | `bi-box-arrow-up-right` |
| Facture Client | `bi-receipt` |
| Paiement Client | `bi-credit-card-fill` |
| Dépenses | `bi-wallet2` |
| Alertes | `bi-bell-fill` |
| Rapports | `bi-bar-chart-line` |
| Paramètres | `bi-sliders` |
| Acompte / Avance | `bi-piggy-bank` |
| Dette / Solde | `bi-hourglass-split` |
| Pièce jointe | `bi-paperclip` |
| Sans pièce jointe | `bi-paperclip` + `color: #f87171` |
| FIFO / Allocation | `bi-distribute-vertical` |
| Lot fermé | `bi-lock-fill` |
| Mortalité spike | `bi-exclamation-triangle-fill` |
| Print | `bi-printer` |
| Export CSV | `bi-file-earmark-spreadsheet` |
| Add / Create | `bi-plus-lg` |
| Edit | `bi-pencil` |
| Deactivate | `bi-slash-circle` |
| Search | `bi-search` |
| Filter | `bi-funnel` |
| Calendar | `bi-calendar3` |
| Success / Check | `bi-check-circle-fill` |
| Error | `bi-exclamation-circle-fill` |
| Warning | `bi-exclamation-triangle-fill` |
| Info | `bi-info-circle` |
| Back | `bi-arrow-left` |
| User / Login | `bi-person` |
| Sign out | `bi-box-arrow-left` |

---

## 7. Data Visualization (Chart.js 4)

**Library:** Chart.js 4.4.3
**CDN:** `https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js`

### Global Defaults (apply once in base.html)
```js
Chart.defaults.color = '#8892a0';
Chart.defaults.borderColor = 'rgba(255,255,255,0.07)';
Chart.defaults.font.family = "'DM Sans', sans-serif";
```

### Tooltip Standard Config
```js
tooltip: {
  backgroundColor: '#191d24',
  borderColor: 'rgba(255,255,255,.12)',
  borderWidth: 1,
  titleColor: '#edf0f3',
  bodyColor: '#8892a0',
  padding: 10,
  cornerRadius: 8,
}
```

### Grid Lines
```js
grid: { color: 'rgba(255,255,255,0.05)', drawBorder: false }
ticks: { color: '#50586a', font: { size: 10.5 } }
```

### Chart Types & Use Cases
| Chart | Use Case | Key Config |
|---|---|---|
| Area line | Feed consumption over time / cash flow trend | `fill: true`, green gradient, `tension: 0.4`, `borderColor: #22c55e` |
| Horizontal bar | Consommation par lot / supplier debt ranking | `indexAxis: 'y'`, `borderRadius: 6`, `borderSkipped: false` |
| Doughnut | Stock composition / expense breakdown by category | `cutout: '68%'`, `borderColor: #12151a`, `borderWidth: 3` |
| Vertical bar | Monthly production volume / revenue by period | `borderRadius: 6`, `borderSkipped: false` |
| Grouped bar | IC comparison across lots | dual dataset, palette positions 1 & 2 |

### Area Chart Gradient Recipe
```js
const gradient = ctx.createLinearGradient(0, 0, 0, chartHeight);
gradient.addColorStop(0, 'rgba(34,197,94,0.22)');
gradient.addColorStop(1, 'rgba(34,197,94,0)');
// then: backgroundColor: gradient
```

---

## 8. Animation Conventions

| Pattern | Values |
|---|---|
| Default transition | `0.18s ease` |
| Card hover lift | `transform: translateY(-2px)` |
| Page entry | `opacity 0→1` + `translateY(10px→0)`, `0.3s ease` |
| Staggered list entry | `animation-delay: nth-child × 35ms` (cap at 350ms) |
| Toast slide-in | `translateX(20px→0)`, `0.2s ease` |
| Toast dismiss | `opacity→0` + `translateX(20px)`, `0.3s ease` |
| FIFO preview expand | `max-height 0→auto`, `opacity 0→1`, `0.25s ease` |
| Sidebar collapse | `width` transition `0.18s ease` |

```css
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes slideIn {
  from { opacity: 0; transform: translateX(20px); }
  to   { opacity: 1; transform: translateX(0); }
}
```

---

## 9. Template Architecture

### Inheritance Chain
```
base.html
├── registration/login.html          (standalone — no base)
├── dashboard/index.html             (extends base)
├── lots/{list,detail,form}.html     (extends base)
├── consumption/{list,form}.html     (extends base)
├── production/{list,detail,form}.html
├── stock/{intrants,produits}.html
├── suppliers/{list,detail,form}.html
├── bl_fournisseur/{list,detail,form}.html
├── factures_fournisseurs/{list,detail,form}.html
├── reglements/{form,detail}.html
├── clients/{list,detail,form}.html
├── bl_clients/{list,detail,form}.html
├── facturation/{list,detail,form}.html
├── paiements_clients/{form,detail}.html
├── depenses/{list,form}.html
├── alerts/dashboard.html
├── reporting/{report_name}.html
└── documents/{document_type}/print.html  (print-only, no base)
```

### Blocks Available in base.html
| Block | Purpose |
|---|---|
| `{% block title %}` | `<title>` tag content |
| `{% block nav_{name} %}` | Inject `active` class to highlight sidebar link |
| `{% block page_title %}` | Topbar breadcrumb main text |
| `{% block page_sub_text %}` | Topbar subtitle (lot count, date, balance, etc.) |
| `{% block extra_css %}` | Page-specific `<style>` blocks |
| `{% block content %}` | Main page body inside `.page-body` |
| `{% block extra_js %}` | Page-specific scripts before `</body>` |

### Available Nav Block Names
```
nav_dashboard
nav_lots           nav_consumption    nav_production
nav_stock_intrants nav_stock_produits
nav_fournisseurs   nav_bl_fournisseur nav_factures_fournisseurs nav_reglements
nav_clients        nav_bl_clients     nav_facturation           nav_paiements_clients
nav_depenses
nav_alerts
nav_rapports
nav_settings
```

### Sidebar Group Structure
```
─ Dashboard
─ Élevage
    Lots d'Élevage         nav_lots
    Consommation           nav_consumption
    Production             nav_production
─ Stock
    Stock Intrants         nav_stock_intrants
    Stock Produits Finis   nav_stock_produits
─ Fournisseurs
    Fournisseurs           nav_fournisseurs
    BL Fournisseur         nav_bl_fournisseur
    Factures               nav_factures_fournisseurs
    Règlements             nav_reglements
─ Clients
    Clients                nav_clients
    BL Clients             nav_bl_clients
    Facturation            nav_facturation
    Paiements              nav_paiements_clients
─ Dépenses                 nav_depenses
─ Alertes                  nav_alerts
─ Rapports                 nav_rapports
─ Paramètres               nav_settings
```

### Context Variables Always Available (from context processor)
- `request.user` — authenticated user
- `request.user.userprofile` — UserProfile with `.role`, `.is_admin`, `.is_staff`
- `app_name` — `"Élevage Avicole"`
- `unresolved_alerts_count` — integer for topbar bell badge

---

## 10. Reusable HTML Snippets

### Section Eyebrow
```html
<div class="section-eyebrow">Titre de section</div>
<!-- 10.5px, uppercase, letter-spacing .1em, --text-muted, font-weight 600 -->
```

### Section Header with Link
```html
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
  <span style="font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);font-weight:600;">Section</span>
  <a href="{% url 'app:view' %}" style="font-size:12px;color:var(--accent);display:flex;align-items:center;gap:4px;">
    Voir tout <i class="bi bi-arrow-right"></i>
  </a>
</div>
```

### Metric Card Shell
```html
<div class="metric-card">
  <div class="metric-icon ic-{color}"><i class="bi bi-{icon}"></i></div>
  <div class="metric-label">LABEL</div>
  <div class="metric-value">VALUE</div>
  <span class="metric-delta up|down|neutral">
    <i class="bi bi-arrow-up-right|arrow-down-right|dash"></i> texte
  </span>
</div>
```

### Empty State
```html
<div style="text-align:center;padding:48px 20px;color:var(--text-muted);">
  <i class="bi bi-{icon}" style="font-size:32px;display:block;margin-bottom:12px;color:var(--border-strong);"></i>
  <div style="font-family:'Syne',sans-serif;font-size:15px;font-weight:700;color:var(--text-secondary);margin-bottom:6px;">Aucun enregistrement</div>
  <div style="font-size:13px;">Message descriptif ici.</div>
  <a href="{% url 'app:create' %}" class="btn-primary" style="margin-top:16px;display:inline-flex;">
    <i class="bi bi-plus-lg"></i> Créer
  </a>
</div>
```

### Locked Lot Warning Banner
```html
<div style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:var(--radius-md);padding:12px 16px;display:flex;align-items:center;gap:10px;margin-bottom:20px;">
  <i class="bi bi-lock-fill" style="color:#fbbf24;font-size:15px;"></i>
  <span style="font-size:13px;color:var(--text-secondary);">Ce lot est <strong style="color:#fbbf24;">clôturé</strong> — aucune saisie n'est possible.</span>
</div>
```

### Danger Zone Box
```html
<div style="background:rgba(239,68,68,.07);border:1px solid rgba(239,68,68,.2);border-radius:var(--radius-md);padding:16px 18px;">
  <div style="font-family:'Syne',sans-serif;font-weight:700;color:#f87171;margin-bottom:4px;">
    <i class="bi bi-exclamation-triangle me-1"></i> Zone sensible
  </div>
  <p style="font-size:13px;color:var(--text-secondary);margin:0 0 12px;">Message d'avertissement.</p>
  <button class="btn-danger">Action irréversible</button>
</div>
```

### Sans Pièce Jointe Flag
```html
<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;color:#f87171;">
  <i class="bi bi-paperclip"></i> Sans pièce jointe
</span>
```

---

## 11. Printable Document Rules

Print templates (`documents/{type}/print.html`) use **no base.html**, no sidebar, no topbar.

```css
/* Print template base */
@media print {
  body { background: #fff; color: #111; font-family: 'DM Sans', sans-serif; font-size: 12px; }
  .no-print { display: none !important; }
}
/* On-screen preview */
.print-preview { max-width: 720px; margin: 32px auto; background: #fff; color: #111;
  padding: 40px 48px; border-radius: var(--radius-lg); box-shadow: 0 4px 32px rgba(0,0,0,.4); }
.print-preview h1 { font-family: 'Syne', sans-serif; font-size: 20px; font-weight: 800; color: #111; }
.print-preview table { width: 100%; border-collapse: collapse; font-size: 12px; }
.print-preview th { background: #f3f4f6; padding: 8px 10px; text-align: left; font-weight: 600; }
.print-preview td { padding: 8px 10px; border-bottom: 1px solid #e5e7eb; }
.print-preview .total-row td { font-weight: 700; border-top: 2px solid #111; }
```

Each print view must include a `<button class="no-print" onclick="window.print()">` trigger visible on-screen only.

---

## 12. Django-Specific Rules

- **Never** use Django template filters that don't exist: no `|split`, no `|math`, no `|add` with floats.
- All monetary values (DZD): format with `|floatformat:0` and append `DZD` as text — e.g. `{{ amount|floatformat:0 }} DZD`.
- Quantities: `|floatformat:0` for whole units (birds, sacs); `|floatformat:2` for kg/weights.
- Percentages (mortalité, IC): `|floatformat:2` with `%`.
- Dates: `|date:"d/m/Y"` (Algerian format) throughout; `|date:"l d N Y"` for long display only.
- `{% csrf_token %}` in every POST form, no exceptions.
- Use `{% url 'app_name:view_name' %}` for all internal links.
- Auto-computed fields (facture totals, reste à payer, effectif vivant): render as `<span>` or read-only input — never an editable `<input>`. Add `cursor: not-allowed; opacity: .6`.
- FIFO preview: fetch via `JsonResponse` on règlement amount change; render inline before the confirm button — never post without showing the preview first.
- Guard all `userprofile` accesses with `{% if request.user.userprofile %}`.
- Admin-only sections: wrap with `{% if request.user.userprofile.is_admin %}` — not `is_staff`.
