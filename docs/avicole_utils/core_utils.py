"""
core/utils.py

Shared utilities used across all apps:
  - Sequential document reference generation (generic engine used by all apps)
  - Pagination helper
  - Date helpers
"""

import datetime
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic sequential reference generator
# ---------------------------------------------------------------------------

def generer_reference(
    model_class,
    prefix: str,
    champ_reference: str = "reference",
    year: int | None = None,
    padding: int = 4,
) -> str:
    """
    Generate the next sequential document reference for any model.

    Format: <prefix>-<YYYY>-<NNNN>
    Example: BLC-2025-0001, FAC-2025-0042

    The function queries the last existing reference that starts with
    ``<prefix>-<year>-`` and increments the trailing sequence number.
    It is NOT wrapped in a transaction here — the caller (view or signal)
    must ensure atomicity when the reference is assigned.

    Args:
        model_class:      The Django model whose table is queried.
        prefix (str):     Document prefix (e.g. "BLC", "FAC", "BLF").
        champ_reference:  Name of the reference CharField on the model.
        year (int|None):  Target year; defaults to current year.
        padding (int):    Zero-padding width for the sequence number.

    Returns:
        str: Next available reference string.
    """
    year = year or datetime.date.today().year
    pattern = f"{prefix}-{year}-"

    filtre = {f"{champ_reference}__startswith": pattern}
    last = (
        model_class.objects
        .filter(**filtre)
        .order_by(champ_reference)
        .last()
    )

    if last:
        try:
            last_seq = int(getattr(last, champ_reference).split("-")[-1])
        except (ValueError, IndexError):
            last_seq = 0
    else:
        last_seq = 0

    return f"{pattern}{last_seq + 1:0{padding}d}"


# ---------------------------------------------------------------------------
# Company-prefix wrappers (convenience — resolve prefix from CompanyInfo)
# ---------------------------------------------------------------------------

def get_company_prefix(attribute: str) -> str:
    """
    Return a document prefix from the singleton CompanyInfo record.
    Falls back to a hard-coded default if the record does not exist yet.

    Args:
        attribute (str): CompanyInfo field name, e.g. ``prefixe_bl_client``.
    """
    try:
        from core.models import CompanyInfo
        return getattr(CompanyInfo.get_instance(), attribute)
    except Exception:
        defaults = {
            "prefixe_bl_client": "BLC",
            "prefixe_bl_fournisseur": "BLF",
            "prefixe_facture_client": "FAC",
            "prefixe_facture_fournisseur": "FRN",
        }
        return defaults.get(attribute, "DOC")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def paginer(queryset, page_number, par_page: int = 25):
    """
    Paginate a queryset and return a Django Page object.

    Args:
        queryset:    Any Django queryset.
        page_number: Current page number (string or int; invalid values → page 1).
        par_page:    Items per page.

    Returns:
        django.core.paginator.Page
    """
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

    paginator = Paginator(queryset, par_page)
    try:
        page = paginator.page(page_number)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)
    return page


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def today() -> datetime.date:
    """Return today's date. Centralised so tests can mock it."""
    return datetime.date.today()


def date_range_from_params(date_debut_str: str | None, date_fin_str: str | None):
    """
    Parse date range strings from GET parameters.

    Returns:
        tuple[datetime.date | None, datetime.date | None]
    """
    fmt = "%Y-%m-%d"
    date_debut = None
    date_fin = None
    try:
        if date_debut_str:
            date_debut = datetime.datetime.strptime(date_debut_str, fmt).date()
    except (ValueError, TypeError):
        pass
    try:
        if date_fin_str:
            date_fin = datetime.datetime.strptime(date_fin_str, fmt).date()
    except (ValueError, TypeError):
        pass
    return date_debut, date_fin
