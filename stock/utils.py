"""
stock/utils.py

Business-logic helpers for the stock (inventory management) module.

  get_stock_status_report   — §20.7  Snapshot of all stock levels, alert
                               statuses, and valuations (intrants + produits finis).
  get_fiche_stock_intrant   — §8.2   Full stock card for one Intrant:
                               opening balance, every in/out movement with
                               source reference, current balance.
  get_fiche_stock_produit   — §8.2   Same for one ProduitFini.
  get_alertes_stock         — §19.5  Return all stock items currently below
                               their seuil d'alerte (both segments).
  get_ajustements_flagges   — §8.4   Return StockAjustement records where the
                               applied delta deviates significantly from the
                               book balance at the time of adjustment (audit
                               flag for manual corrections).
"""

from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §20.7 — Stock Status Report
# ---------------------------------------------------------------------------


def get_stock_status_report() -> dict:
    """
    Build a complete snapshot of current stock levels for every intrant and
    produit fini in the catalogue.

    Returns a dict with two lists:

    ``intrants``
        One entry per StockIntrant (joined to its Intrant catalogue record):
          - designation, categorie, unite_mesure
          - quantite            — current balance
          - seuil_alerte        — minimum threshold
          - en_alerte           — bool (quantite <= seuil_alerte)
          - prix_moyen_pondere  — weighted-average cost (DZD / unit)
          - valeur_stock        — quantite × PMP (DZD)
          - stock_pk            — StockIntrant PK
          - intrant_pk          — Intrant PK

    ``produits_finis``
        One entry per StockProduitFini (joined to its ProduitFini record):
          - designation, type_produit, unite_mesure
          - quantite
          - seuil_alerte
          - en_alerte           — bool (quantite <= seuil_alerte)
          - cout_moyen_production
          - valeur_stock
          - stock_pk
          - produit_pk

    ``totaux``
        Aggregated valuation totals:
          - valeur_totale_intrants  (Decimal)
          - valeur_totale_produits  (Decimal)
          - nb_alertes_intrants     (int)
          - nb_alertes_produits     (int)
    """
    from stock.models import StockIntrant, StockProduitFini

    intrant_rows = []
    valeur_totale_intrants = Decimal("0")
    nb_alertes_intrants = 0

    intrant_stocks = StockIntrant.objects.select_related(
        "intrant", "intrant__categorie"
    ).order_by("intrant__categorie__libelle", "intrant__designation")

    for s in intrant_stocks:
        en_alerte = s.en_alerte  # uses model property
        valeur = s.valeur_stock  # uses model property
        valeur_totale_intrants += valeur
        if en_alerte:
            nb_alertes_intrants += 1

        intrant_rows.append(
            {
                "stock_pk": s.pk,
                "intrant_pk": s.intrant_id,
                "designation": s.intrant.designation,
                "categorie": s.intrant.categorie.libelle,
                "unite_mesure": s.intrant.unite_mesure,
                "quantite": s.quantite,
                "seuil_alerte": s.intrant.seuil_alerte,
                "en_alerte": en_alerte,
                "prix_moyen_pondere": s.prix_moyen_pondere,
                "valeur_stock": valeur,
            }
        )

    produit_rows = []
    valeur_totale_produits = Decimal("0")
    nb_alertes_produits = 0

    produit_stocks = StockProduitFini.objects.select_related("produit_fini").order_by(
        "produit_fini__type_produit", "produit_fini__designation"
    )

    for s in produit_stocks:
        en_alerte = s.en_alerte  # uses model property
        valeur = s.valeur_stock  # uses model property
        valeur_totale_produits += valeur
        if en_alerte:
            nb_alertes_produits += 1

        produit_rows.append(
            {
                "stock_pk": s.pk,
                "produit_pk": s.produit_fini_id,
                "designation": s.produit_fini.designation,
                "type_produit": s.produit_fini.get_type_produit_display(),
                "unite_mesure": s.produit_fini.unite_mesure,
                "quantite": s.quantite,
                "seuil_alerte": s.seuil_alerte,
                "en_alerte": en_alerte,
                "cout_moyen_production": s.cout_moyen_production,
                "valeur_stock": valeur,
            }
        )

    return {
        "intrants": intrant_rows,
        "produits_finis": produit_rows,
        "totaux": {
            "valeur_totale_intrants": valeur_totale_intrants,
            "valeur_totale_produits": valeur_totale_produits,
            "nb_alertes_intrants": nb_alertes_intrants,
            "nb_alertes_produits": nb_alertes_produits,
        },
    }


# ---------------------------------------------------------------------------
# §8.2 — Stock Card for one Intrant
# ---------------------------------------------------------------------------


def get_fiche_stock_intrant(intrant, date_debut=None, date_fin=None) -> dict:
    """
    Build a complete stock card (fiche de stock) for a single Intrant.

    The card lists every StockMouvement in chronological order, reconstructing
    the running balance from the movements themselves so the view can display
    the opening balance, each in/out event with its source reference, and the
    closing balance.

    Args:
        intrant   (Intrant):        The intrant catalogue entry.
        date_debut (date | None):   Filter movements from this date (inclusive).
        date_fin   (date | None):   Filter movements up to this date (inclusive).

    Returns dict with keys:
        intrant                — the Intrant instance
        stock                  — StockIntrant instance (current state)
        mouvements             — list of movement dicts (chronological)
        quantite_ouverture     — balance before the filtered period
        quantite_cloture       — balance at end of filtered period
        valeur_stock_actuelle  — current market value (quantite × PMP)
        total_entrees          — Decimal sum of all entrée quantities in period
        total_sorties          — Decimal sum of all sortie quantities in period
    """
    from stock.models import StockIntrant, StockMouvement

    try:
        stock = StockIntrant.objects.get(intrant=intrant)
    except StockIntrant.DoesNotExist:
        stock = None

    qs = StockMouvement.objects.filter(intrant=intrant).order_by(
        "date_mouvement", "created_at"
    )

    # Opening balance = stock before date_debut (sum of movements before period)
    if date_debut:
        mouvements_avant = StockMouvement.objects.filter(
            intrant=intrant, date_mouvement__lt=date_debut
        )
        quantite_ouverture = _calculer_solde_depuis_mouvements(mouvements_avant)
        qs = qs.filter(date_mouvement__gte=date_debut)
    else:
        quantite_ouverture = Decimal("0")

    if date_fin:
        qs = qs.filter(date_mouvement__lte=date_fin)

    mouvements = list(qs.select_related("created_by"))

    total_entrees = Decimal("0")
    total_sorties = Decimal("0")
    mouvement_rows = []

    solde_courant = quantite_ouverture
    for m in mouvements:
        if m.type_mouvement == StockMouvement.TYPE_ENTREE:
            total_entrees += m.quantite
            solde_courant += m.quantite
        elif m.type_mouvement == StockMouvement.TYPE_SORTIE:
            total_sorties += m.quantite
            solde_courant -= m.quantite
        else:
            # Ajustement — direction derived from quantite_avant / quantite_apres
            delta = m.quantite_apres - m.quantite_avant
            solde_courant += delta

        mouvement_rows.append(
            {
                "pk": m.pk,
                "date": m.date_mouvement,
                "type": m.get_type_mouvement_display(),
                "source": m.get_source_display(),
                "quantite": m.quantite,
                "quantite_avant": m.quantite_avant,
                "quantite_apres": m.quantite_apres,
                "reference_label": m.reference_label,
                "reference_id": m.reference_id,
                "notes": m.notes,
                "created_by": m.created_by,
            }
        )

    return {
        "intrant": intrant,
        "stock": stock,
        "mouvements": mouvement_rows,
        "quantite_ouverture": quantite_ouverture,
        "quantite_cloture": solde_courant,
        "valeur_stock_actuelle": stock.valeur_stock if stock else Decimal("0"),
        "total_entrees": total_entrees,
        "total_sorties": total_sorties,
    }


# ---------------------------------------------------------------------------
# §8.2 — Stock Card for one ProduitFini
# ---------------------------------------------------------------------------


def get_fiche_stock_produit(produit_fini, date_debut=None, date_fin=None) -> dict:
    """
    Build a complete stock card (fiche de stock) for a single ProduitFini.

    Identical structure to get_fiche_stock_intrant but for the finished-goods
    segment.  Movements are sourced from StockMouvement records whose
    ``produit_fini`` FK matches the given instance.

    Args:
        produit_fini (ProduitFini): The finished product catalogue entry.
        date_debut   (date | None): Start of period filter.
        date_fin     (date | None): End of period filter.

    Returns dict with keys:
        produit_fini           — the ProduitFini instance
        stock                  — StockProduitFini instance (current state)
        mouvements             — list of movement dicts (chronological)
        quantite_ouverture     — balance before the filtered period
        quantite_cloture       — balance at end of period
        valeur_stock_actuelle  — current value (quantite × CMP)
        total_entrees          — Decimal sum of entrée quantities in period
        total_sorties          — Decimal sum of sortie quantities in period
    """
    from stock.models import StockProduitFini, StockMouvement

    try:
        stock = StockProduitFini.objects.get(produit_fini=produit_fini)
    except StockProduitFini.DoesNotExist:
        stock = None

    qs = StockMouvement.objects.filter(produit_fini=produit_fini).order_by(
        "date_mouvement", "created_at"
    )

    if date_debut:
        mouvements_avant = StockMouvement.objects.filter(
            produit_fini=produit_fini, date_mouvement__lt=date_debut
        )
        quantite_ouverture = _calculer_solde_depuis_mouvements(mouvements_avant)
        qs = qs.filter(date_mouvement__gte=date_debut)
    else:
        quantite_ouverture = Decimal("0")

    if date_fin:
        qs = qs.filter(date_mouvement__lte=date_fin)

    mouvements = list(qs.select_related("created_by"))

    total_entrees = Decimal("0")
    total_sorties = Decimal("0")
    mouvement_rows = []

    solde_courant = quantite_ouverture
    for m in mouvements:
        if m.type_mouvement == StockMouvement.TYPE_ENTREE:
            total_entrees += m.quantite
            solde_courant += m.quantite
        elif m.type_mouvement == StockMouvement.TYPE_SORTIE:
            total_sorties += m.quantite
            solde_courant -= m.quantite
        else:
            delta = m.quantite_apres - m.quantite_avant
            solde_courant += delta

        mouvement_rows.append(
            {
                "pk": m.pk,
                "date": m.date_mouvement,
                "type": m.get_type_mouvement_display(),
                "source": m.get_source_display(),
                "quantite": m.quantite,
                "quantite_avant": m.quantite_avant,
                "quantite_apres": m.quantite_apres,
                "reference_label": m.reference_label,
                "reference_id": m.reference_id,
                "notes": m.notes,
                "created_by": m.created_by,
            }
        )

    return {
        "produit_fini": produit_fini,
        "stock": stock,
        "mouvements": mouvement_rows,
        "quantite_ouverture": quantite_ouverture,
        "quantite_cloture": solde_courant,
        "valeur_stock_actuelle": stock.valeur_stock if stock else Decimal("0"),
        "total_entrees": total_entrees,
        "total_sorties": total_sorties,
    }


# ---------------------------------------------------------------------------
# §19.5 — Stock Alerts
# ---------------------------------------------------------------------------


def get_alertes_stock() -> dict:
    """
    Return all stock items currently at or below their seuil d'alerte.

    Covers both segments (intrants and produits finis).  Results are sorted
    by how critically low the stock is: items at zero appear first, then
    items with the smallest quantity relative to their threshold.

    Returns dict with keys:
        intrants       — list of StockIntrant instances below threshold
        produits_finis — list of StockProduitFini instances below threshold
        nb_total       — total count of alerted items across both segments
    """
    from stock.models import StockIntrant, StockProduitFini
    from django.db.models import F

    # Use DB-level filter (quantite <= intrant.seuil_alerte) for efficiency.
    # The F() expression compares across related fields correctly.
    alertes_intrants = list(
        StockIntrant.objects.select_related("intrant", "intrant__categorie")
        .filter(quantite__lte=F("intrant__seuil_alerte"))
        .order_by("quantite", "intrant__designation")
    )

    alertes_produits = list(
        StockProduitFini.objects.select_related("produit_fini")
        .filter(quantite__lte=F("seuil_alerte"))
        .order_by("quantite", "produit_fini__designation")
    )

    nb_total = len(alertes_intrants) + len(alertes_produits)

    if nb_total:
        logger.info(
            "get_alertes_stock: %d intrant(s) and %d produit(s) below seuil_alerte.",
            len(alertes_intrants),
            len(alertes_produits),
        )

    return {
        "intrants": alertes_intrants,
        "produits_finis": alertes_produits,
        "nb_total": nb_total,
    }


# ---------------------------------------------------------------------------
# §8.4 — Flagged Manual Adjustments (audit)
# ---------------------------------------------------------------------------


def get_ajustements_flagges(seuil_pct: float = 20.0) -> list[dict]:
    """
    Return StockAjustement records where the applied delta exceeds
    *seuil_pct* percent of the book balance at adjustment time.

    These are highlighted in the audit view (§8.4 — "flagged in the stock
    history for audit review").

    A threshold of 20 % is used by default; callers may override.

    Args:
        seuil_pct (float): Percentage deviation that triggers flagging.

    Returns:
        list[dict]: One entry per flagged adjustment, with keys:
            ajustement   — StockAjustement instance
            delta        — signed Decimal difference (apres − avant)
            delta_pct    — signed percentage relative to quantite_avant
            segment_label — human-readable segment name
    """
    from stock.models import StockAjustement

    ajustements = StockAjustement.objects.select_related(
        "intrant", "produit_fini", "effectue_par"
    ).order_by("-date_ajustement")

    flagged = []
    for a in ajustements:
        avant = Decimal(str(a.quantite_avant))
        apres = Decimal(str(a.quantite_apres))
        delta = apres - avant

        # Percentage deviation relative to book balance before adjustment.
        if avant != 0:
            delta_pct = float(delta / avant * 100)
        else:
            # Any non-zero delta on a zero stock is always flagged.
            delta_pct = float("inf") if delta != 0 else 0.0

        if abs(delta_pct) >= seuil_pct:
            segment_label = (
                "Stock Intrants"
                if a.segment == StockAjustement.SEGMENT_INTRANT
                else "Stock Produits Finis"
            )
            flagged.append(
                {
                    "ajustement": a,
                    "delta": delta,
                    "delta_pct": delta_pct,
                    "segment_label": segment_label,
                }
            )

    return flagged


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _calculer_solde_depuis_mouvements(mouvements_qs) -> Decimal:
    """
    Reconstruct the cumulative stock balance from a queryset of
    StockMouvement records.

    Entrée movements add to the balance; sortie movements subtract.
    Ajustement movements use (quantite_apres − quantite_avant) as the delta.

    Used internally to compute the opening balance for a filtered stock card.
    """
    from stock.models import StockMouvement

    solde = Decimal("0")
    for m in mouvements_qs:
        if m.type_mouvement == StockMouvement.TYPE_ENTREE:
            solde += m.quantite
        elif m.type_mouvement == StockMouvement.TYPE_SORTIE:
            solde -= m.quantite
        else:
            solde += m.quantite_apres - m.quantite_avant
    return solde
