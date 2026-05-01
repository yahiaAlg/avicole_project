"""
production/utils.py

Business-logic helpers for the production module.

  allouer_cout_production   — Distribute lot total cost across ProductionLigne
                               records (sets cout_unitaire_estime).
  get_production_dashboard  — Cross-lot production KPI table (for reporting).
  get_rendement_abattage    — Slaughter yield % for a ProductionRecord.
"""

from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cost allocation to production lines
# ---------------------------------------------------------------------------


def allouer_cout_production(production_record) -> None:
    """
    Distribute the source lot's total estimated cost across the
    ProductionLigne records of a ProductionRecord, proportionally by
    quantity (weight-adjusted when poids_unitaire_kg is provided).

    Algorithm
    ---------
    1. Compute the lot's ``cout_total_intrants`` (Σ consommation × PMP)
       plus ``cout_total_depenses`` (Σ dépenses attributed to the lot).
    2. Calculate each line's weight share of the total production weight.
       Fall back to quantity share if no weight data is available.
    3. Set ``ProductionLigne.cout_unitaire_estime`` = allocated cost / quantity
       for each line and save with ``update_fields``.

    This function is idempotent — calling it multiple times overwrites the
    previous allocation.  It must be called BEFORE the record is validated
    (statut → VALIDE) so the signal picks up the correct unit costs when
    updating StockProduitFini.cout_moyen_production.

    Args:
        production_record (ProductionRecord): The draft production record
            whose lines should receive cost allocations.

    Raises:
        ValueError: If the record has no lines.
    """
    lignes = list(production_record.lignes.all())
    if not lignes:
        raise ValueError(
            f"ProductionRecord pk={production_record.pk} has no lines — "
            "cannot allocate costs."
        )

    lot = production_record.lot

    # --- 1. Total lot cost ------------------------------------------------
    cout_intrants = Decimal(str(lot.cout_total_intrants))

    # Operational expenses attributed to this lot
    from django.db.models import Sum

    cout_depenses = lot.depenses.aggregate(total=Sum("montant"))["total"] or Decimal(
        "0"
    )
    cout_total = cout_intrants + Decimal(str(cout_depenses))

    if cout_total <= 0:
        logger.warning(
            "allouer_cout_production: lot pk=%s has zero total cost. "
            "cout_unitaire_estime will be set to 0 for all lines.",
            lot.pk,
        )
        for ligne in lignes:
            ligne.cout_unitaire_estime = Decimal("0")
            ligne.save(update_fields=["cout_unitaire_estime"])
        return

    # --- 2. Compute weight shares (fall back to quantity shares) ----------
    # Total weight = Σ (quantite × poids_unitaire_kg) per line
    def _poids_ligne(l):
        poids = Decimal(str(l.poids_unitaire_kg or 0))
        return Decimal(str(l.quantite)) * poids if poids > 0 else Decimal("0")

    poids_totals = [_poids_ligne(l) for l in lignes]
    poids_global = sum(poids_totals)

    if poids_global > 0:
        # Weight-based allocation
        shares = [p / poids_global for p in poids_totals]
        logger.debug(
            "allouer_cout_production: pk=%s — using weight-based shares (%s kg total).",
            production_record.pk,
            poids_global,
        )
    else:
        # Fallback: quantity-based allocation
        quantite_totale = sum(Decimal(str(l.quantite)) for l in lignes)
        if quantite_totale <= 0:
            logger.error(
                "allouer_cout_production: pk=%s — zero total quantity. Skipping.",
                production_record.pk,
            )
            return
        shares = [Decimal(str(l.quantite)) / quantite_totale for l in lignes]
        logger.debug(
            "allouer_cout_production: pk=%s — using quantity-based shares (%s units total).",
            production_record.pk,
            quantite_totale,
        )

    # --- 3. Set cout_unitaire_estime per line ----------------------------
    for ligne, share in zip(lignes, shares):
        qte = Decimal(str(ligne.quantite))
        if qte <= 0:
            ligne.cout_unitaire_estime = Decimal("0")
        else:
            cout_ligne = (cout_total * share).quantize(Decimal("0.0001"))
            ligne.cout_unitaire_estime = round(cout_ligne / qte, 4)
        ligne.save(update_fields=["cout_unitaire_estime"])
        logger.debug(
            "Ligne pk=%s: share=%.4f cout_unitaire=%.4f DZD.",
            ligne.pk,
            float(share),
            float(ligne.cout_unitaire_estime),
        )

    logger.info(
        "allouer_cout_production: pk=%s — total cost %s DZD distributed across %d line(s).",
        production_record.pk,
        cout_total,
        len(lignes),
    )


# ---------------------------------------------------------------------------
# Slaughter yield
# ---------------------------------------------------------------------------


def get_rendement_abattage(production_record) -> Decimal | None:
    """
    Slaughter yield = poids_total_kg / (nombre_oiseaux_abattus × poids_moyen_vif)

    Because the live weight is not stored on the record, this is approximated
    as the ratio of carcass weight to the lot's average bird weight at harvest
    time — only available if poids_moyen_kg is non-zero on the record.

    Returns None when the denominator is zero or unavailable.

    Args:
        production_record (ProductionRecord): A validated record with
            poids_total_kg and nombre_oiseaux_abattus populated.
    """
    if (
        not production_record.poids_moyen_kg
        or production_record.nombre_oiseaux_abattus == 0
    ):
        return None

    # For live birds the "slaughter yield" is 100 % by definition;
    # for carcasses the ratio is meaningful.
    from production.models import ProduitFini

    carcasse_lignes = production_record.lignes.filter(
        produit_fini__type_produit__in=[
            ProduitFini.TYPE_CARCASSE,
            ProduitFini.TYPE_DECOUPE,
        ]
    )

    if not carcasse_lignes.exists():
        return None

    from django.db.models import Sum, F, ExpressionWrapper, DecimalField

    poids_carcasse = carcasse_lignes.aggregate(
        total=Sum(
            ExpressionWrapper(
                F("quantite") * F("poids_unitaire_kg"),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            )
        )
    )["total"] or Decimal("0")

    poids_vif_total = Decimal(str(production_record.nombre_oiseaux_abattus)) * Decimal(
        str(production_record.poids_moyen_kg)
    )

    if poids_vif_total <= 0:
        return None

    return round(poids_carcasse / poids_vif_total * 100, 2)


# ---------------------------------------------------------------------------
# Cross-lot production dashboard
# ---------------------------------------------------------------------------


def get_production_dashboard(date_debut=None, date_fin=None) -> list[dict]:
    """
    Return a per-lot production summary table for the dashboard or the
    lot-profitability report (spec §20.5).

    Each row contains:
        lot                  (LotElevage)
        date_production      (date of most recent validated record for that lot)
        nb_oiseaux_abattus   (int — cumulative across all records for the lot)
        poids_total_kg       (Decimal)
        poids_moyen_kg       (Decimal — weighted average across records)
        cout_total_dzd       (Decimal)
        rendement_pct        (Decimal | None)

    Args:
        date_debut (date | None): Filter production records from this date.
        date_fin   (date | None): Filter production records up to this date.

    Returns:
        list[dict]: One dict per lot, sorted by date_production descending.
    """
    from production.models import ProductionRecord
    from django.db.models import Sum, Max

    qs = ProductionRecord.objects.filter(
        statut=ProductionRecord.STATUT_VALIDE
    ).select_related("lot")

    if date_debut:
        qs = qs.filter(date_production__gte=date_debut)
    if date_fin:
        qs = qs.filter(date_production__lte=date_fin)

    # Group by lot
    from collections import defaultdict

    rows: dict[int, dict] = defaultdict(
        lambda: {
            "lot": None,
            "date_production": None,
            "nb_oiseaux_abattus": 0,
            "poids_total_kg": Decimal("0"),
            "poids_somme_ponderee": Decimal("0"),  # used to compute weighted avg weight
            "cout_total_dzd": Decimal("0"),
        }
    )

    for record in qs:
        row = rows[record.lot_id]
        row["lot"] = record.lot
        row["nb_oiseaux_abattus"] += record.nombre_oiseaux_abattus
        row["poids_total_kg"] += Decimal(str(record.poids_total_kg))
        row["poids_somme_ponderee"] += Decimal(str(record.poids_moyen_kg)) * Decimal(
            str(record.nombre_oiseaux_abattus)
        )
        if (
            row["date_production"] is None
            or record.date_production > row["date_production"]
        ):
            row["date_production"] = record.date_production

        # Sum line costs
        for ligne in record.lignes.all():
            row["cout_total_dzd"] += ligne.valeur_totale

    result = []
    for row in rows.values():
        nb = row["nb_oiseaux_abattus"]
        poids_moyen = (
            row["poids_somme_ponderee"] / Decimal(str(nb)) if nb > 0 else Decimal("0")
        )
        result.append(
            {
                "lot": row["lot"],
                "date_production": row["date_production"],
                "nb_oiseaux_abattus": nb,
                "poids_total_kg": row["poids_total_kg"],
                "poids_moyen_kg": round(poids_moyen, 3),
                "cout_total_dzd": row["cout_total_dzd"],
            }
        )

    return sorted(result, key=lambda r: r["date_production"] or "", reverse=True)
