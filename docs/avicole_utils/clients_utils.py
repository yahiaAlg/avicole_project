"""
clients/utils.py

Business-logic helpers for the client AR cycle.

  appliquer_paiement_client        — Apply a payment to selected invoices (BR-FAC-03)
  generer_reference_bl_client      — Sequential BLC reference
  generer_reference_facture_client — Sequential FAC reference
  get_client_solde                 — Full financial snapshot for one client
  get_client_aging_buckets         — Aged-receivable analysis for reporting
"""

from decimal import Decimal
import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manual payment allocation  (BR-FAC-03)
# ---------------------------------------------------------------------------

def appliquer_paiement_client(paiement, allocations: list[dict]) -> dict:
    """
    Apply a PaiementClient to one or more FactureClient records according to
    the user's explicit selection (BR-FAC-03 — unlike supplier FIFO, the
    client selects which invoices to pay).

    Each entry in *allocations* is a dict::

        {"facture": <FactureClient instance>, "montant_alloue": <Decimal>}

    Validation performed here (raises ValueError on failure):
      - Sum of allocations must not exceed paiement.montant.
      - Each individual montant_alloue must not exceed facture.reste_a_payer.
      - All factures must belong to paiement.client.

    On success:
      - Creates PaiementClientAllocation records.
      - Calls facture.recalculer_solde() for each touched invoice.

    Args:
        paiement (PaiementClient): Freshly-created (or existing) payment record.
        allocations (list[dict]):  Validated allocation entries from the view.

    Returns:
        dict: Summary with keys ``allocations_creees`` (int) and
              ``montant_alloue_total`` (Decimal).
    """
    from clients.models import PaiementClientAllocation

    montant_disponible = Decimal(str(paiement.montant))
    montant_alloue_total = Decimal("0")
    allocations_creees = 0

    for entry in allocations:
        facture = entry["facture"]
        montant = Decimal(str(entry["montant_alloue"]))

        if montant <= 0:
            continue

        # Guard: invoice must belong to the same client.
        if facture.client_id != paiement.client_id:
            raise ValueError(
                f"La facture {facture.reference} n'appartient pas au client "
                f"{paiement.client.nom}."
            )

        # Guard: do not over-allocate a single invoice.
        if montant > facture.reste_a_payer:
            raise ValueError(
                f"Le montant alloué ({montant} DZD) dépasse le reste à payer "
                f"de la facture {facture.reference} ({facture.reste_a_payer} DZD)."
            )

        # Guard: do not exceed the available payment balance.
        if montant_alloue_total + montant > montant_disponible:
            raise ValueError(
                f"La somme des allocations ({montant_alloue_total + montant} DZD) "
                f"dépasse le montant du paiement ({montant_disponible} DZD)."
            )

        PaiementClientAllocation.objects.create(
            paiement=paiement,
            facture=facture,
            montant_alloue=montant,
        )

        facture.montant_regle = facture.montant_regle + montant
        facture.recalculer_solde()

        montant_alloue_total += montant
        allocations_creees += 1
        logger.debug(
            "Paiement client pk=%s: allocated %s DZD to facture %s.",
            paiement.pk,
            montant,
            facture.reference,
        )

    logger.info(
        "appliquer_paiement_client: paiement pk=%s — %d allocation(s) totalling %s DZD.",
        paiement.pk,
        allocations_creees,
        montant_alloue_total,
    )

    return {
        "allocations_creees": allocations_creees,
        "montant_alloue_total": montant_alloue_total,
    }


# ---------------------------------------------------------------------------
# Sequential reference generators
# ---------------------------------------------------------------------------

def generer_reference_bl_client() -> str:
    """
    Generate the next BL Client reference.
    Format: <prefixe_bl_client>-<YYYY>-<NNNN>   e.g. BLC-2025-0001
    """
    from clients.models import BLClient
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_bl_client")
    return generer_reference(BLClient, prefix)


def generer_reference_facture_client() -> str:
    """
    Generate the next Facture Client reference.
    Format: <prefixe_facture_client>-<YYYY>-<NNNN>   e.g. FAC-2025-0001
    """
    from clients.models import FactureClient
    from core.utils import generer_reference, get_company_prefix

    prefix = get_company_prefix("prefixe_facture_client")
    return generer_reference(FactureClient, prefix)


# ---------------------------------------------------------------------------
# Client financial summary
# ---------------------------------------------------------------------------

def get_client_solde(client) -> dict:
    """
    Return a complete financial snapshot for one client.

    Keys:
        creance_globale      — sum of reste_a_payer on open invoices
        factures_ouvertes    — queryset ordered by date_facture ASC
        total_paiements      — sum of all payments ever recorded
        nb_factures_retard   — count of overdue invoices
        depasse_plafond      — bool: créance > plafond_credit (if configured)

    Args:
        client (Client): The client instance.
    """
    from clients.models import FactureClient, PaiementClient
    from django.db.models import Sum

    factures_ouvertes = FactureClient.objects.filter(
        client=client,
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ],
    ).order_by("date_facture", "pk")

    creance_globale = (
        factures_ouvertes.aggregate(total=Sum("reste_a_payer"))["total"] or Decimal("0")
    )

    total_paiements = (
        PaiementClient.objects.filter(client=client)
        .aggregate(total=Sum("montant"))["total"] or Decimal("0")
    )

    today = datetime.date.today()
    nb_factures_retard = factures_ouvertes.filter(date_echeance__lt=today).count()

    depasse_plafond = bool(
        client.plafond_credit
        and client.plafond_credit > 0
        and creance_globale > client.plafond_credit
    )

    return {
        "creance_globale": creance_globale,
        "factures_ouvertes": factures_ouvertes,
        "total_paiements": total_paiements,
        "nb_factures_retard": nb_factures_retard,
        "depasse_plafond": depasse_plafond,
    }


# ---------------------------------------------------------------------------
# Client aged-receivable analysis  (for reporting — §9.12)
# ---------------------------------------------------------------------------

def get_client_aging_buckets(client=None) -> list[dict]:
    """
    Compute an aged-receivable breakdown for one client or all clients.

    Aging buckets (days past due date):
        current   — not yet due
        1_30      — 1–30 days overdue
        31_60     — 31–60 days overdue
        61_90     — 61–90 days overdue
        over_90   — > 90 days overdue

    Args:
        client (Client | None): Filter to one client; None = all.

    Returns:
        list[dict]: One dict per client with bucket totals.
    """
    from clients.models import FactureClient

    today = datetime.date.today()

    qs = FactureClient.objects.filter(
        statut__in=[
            FactureClient.STATUT_NON_PAYEE,
            FactureClient.STATUT_PARTIELLEMENT_PAYEE,
        ]
    ).select_related("client")

    if client:
        qs = qs.filter(client=client)

    buckets_by_client: dict[int, dict] = {}

    for facture in qs:
        cli = facture.client
        if cli.pk not in buckets_by_client:
            buckets_by_client[cli.pk] = {
                "client": cli,
                "current": Decimal("0"),
                "1_30": Decimal("0"),
                "31_60": Decimal("0"),
                "61_90": Decimal("0"),
                "over_90": Decimal("0"),
                "total": Decimal("0"),
            }

        entry = buckets_by_client[cli.pk]
        rap = facture.reste_a_payer
        entry["total"] += rap

        if not facture.date_echeance or facture.date_echeance >= today:
            entry["current"] += rap
        else:
            days_late = (today - facture.date_echeance).days
            if days_late <= 30:
                entry["1_30"] += rap
            elif days_late <= 60:
                entry["31_60"] += rap
            elif days_late <= 90:
                entry["61_90"] += rap
            else:
                entry["over_90"] += rap

    return sorted(buckets_by_client.values(), key=lambda x: x["client"].nom)
