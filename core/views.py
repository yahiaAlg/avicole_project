"""
core/views.py

Views for:
  - Authentication        : login, logout
  - Multi-branch context  : active-branche resolution, branch switcher,
                             Branche CRUD (v1.4, §3.5)
  - Dashboard             : main home page with key KPIs and alerts
  - Company Information   : view / edit singleton CompanyInfo
  - User Management       : list, create, edit, deactivate, password change
"""

import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db.models import Count, Sum, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import (
    BrancheForm,
    BrancheSwitchForm,
    CompanyInfoForm,
    UserCreateForm,
    UserUpdateForm,
)
from core.models import Branche, CompanyInfo, UserProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role / permission helpers
# ---------------------------------------------------------------------------


def is_admin(user):
    """True when the authenticated user has the 'admin' role in UserProfile."""
    try:
        return user.profile.role == "admin"
    except UserProfile.DoesNotExist:
        return False


def is_admin_or_manager(user):
    try:
        return user.profile.role in ("admin", "manager")
    except UserProfile.DoesNotExist:
        return False


admin_required = user_passes_test(is_admin, login_url="core:login")
admin_or_manager_required = user_passes_test(
    is_admin_or_manager, login_url="core:login"
)


# ---------------------------------------------------------------------------
# Multi-branch context helpers (v1.4, §3.5)
# ---------------------------------------------------------------------------
#
# There is no per-branch URL segment and no middleware: the active branche
# context is resolved per-request from the user's profile (chef de branche /
# opérateur / branch-bound comptable — BR-BRA-02) or, for admin and an
# unbound comptable (BR-BRA-04), from the session-stored selection set via
# `branche_switch` below (BR-BRA-03). Every app's views call
# `get_active_branche(request)` and filter accordingly; `None` always means
# Vue Globale, mirroring the `branche=None` convention already used
# throughout core/achats/clients/depenses/elevage/production/stock utils.

BRANCHE_SESSION_KEY = "active_branche_id"


def get_user_profile(user):
    """Return the UserProfile for *user*, or None if it doesn't exist."""
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def get_active_branche(request):
    """
    Resolve the Branche the current request is operating in (§3.5.4).

      - chef_branche / opérateur (BR-BRA-02): always their own
        profile.branche — locked, no switcher.
      - comptable bound to a branche: always that branche.
      - admin, or comptable left unbound (profile.a_vue_globale,
        BR-BRA-04): the branche stored in session by `branche_switch`,
        or None when the session holds no selection (default = Vue Globale).

    Returns a Branche instance, or None for Vue Globale.
    """
    profile = get_user_profile(request.user)
    if profile is None:
        return None
    if not profile.a_vue_globale:
        return profile.branche

    branche_id = request.session.get(BRANCHE_SESSION_KEY)
    if not branche_id:
        return None
    return Branche.objects.filter(pk=branche_id, actif=True).first()


def est_vue_globale(request):
    """True when the request is currently in Vue Globale (BR-BRA-04)."""
    return get_active_branche(request) is None


def peut_changer_de_branche(request):
    """True when the user gets a branch switcher in the UI (§3.5.4)."""
    profile = get_user_profile(request.user)
    return bool(profile and profile.peut_changer_de_branche)


def branche_scope_kwargs(request, field_name="branche"):
    """
    Convenience for simple `Model.objects.filter(**kwargs)` calls:
    `{}` in Vue Globale (no filter == every branche), else
    `{field_name: active_branche}`. Most list/detail views below use this
    directly; views that need the branche/vue_globale flags too should call
    `get_active_branche` themselves instead.
    """
    branche = get_active_branche(request)
    if branche is None:
        return {}
    return {field_name: branche}


def require_branche_context(view_func):
    """
    Decorator for create/edit views: BR-BRA-04 — Vue Globale is read-only,
    never used to create or own a new record. Admin/comptable must select a
    concrete branche via the switcher before reaching a creation form; chef
    de branche/opérateur are always pinned to one and never hit this guard.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if est_vue_globale(request):
            messages.error(
                request,
                "وضع « الرؤية الشاملة » للعرض فقط — يرجى اختيار فرع محدد "
                "قبل إنشاء أو تعديل السجلات.",
            )
            return redirect("core:branche_switch")
        return view_func(request, *args, **kwargs)

    return _wrapped


def branche_object_or_404(request, model, **kwargs):
    """
    get_object_or_404, additionally enforcing that the object's `branche`
    matches the request's active branche when not in Vue Globale (BR-BRA-02:
    a chef de branche/opérateur must never reach another branch's record
    even by guessing its pk in the URL). In Vue Globale the object is
    returned regardless of which branche it belongs to.
    """
    obj = get_object_or_404(model, **kwargs)
    branche = get_active_branche(request)
    if branche is not None and getattr(obj, "branche_id", None) != branche.id:
        from django.http import Http404

        raise Http404("Cet enregistrement appartient à une autre branche.")
    return obj


def branche_matches(request, obj):
    """
    True when *obj* belongs to the request's active branche, or the
    request is in Vue Globale. Unlike `branche_object_or_404`, this works
    for objects whose `branche` is a derived Python property rather than a
    stored FK (e.g. Mortalite.branche, Employe.branche — BR-BRA-09) since
    it reads `.branche` instead of relying on a `branche_id` column.
    """
    branche = get_active_branche(request)
    if branche is None:
        return True
    obj_branche = getattr(obj, "branche", None)
    return obj_branche is not None and obj_branche.id == branche.id


# ---------------------------------------------------------------------------
# Branch switcher (§3.5.4, BR-BRA-03/04)
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
def branche_switch(request):
    """
    Admin / unbound comptable switches the active branche context, or picks
    Vue Globale. Chef de branche/opérateur (and a branch-bound comptable)
    never see this — they have no choice to make (BR-BRA-02).
    """
    if not peut_changer_de_branche(request):
        messages.error(request, "ليس لديك صلاحية تبديل الفرع.")
        return redirect("core:dashboard")

    next_url = request.POST.get("next") or request.GET.get("next") or "core:dashboard"

    if request.method == "POST":
        form = BrancheSwitchForm(request.POST)
        if form.is_valid():
            branche = form.cleaned_data.get("branche")
            if branche:
                request.session[BRANCHE_SESSION_KEY] = branche.pk
                messages.success(request, f"تم التبديل إلى فرع « {branche.nom} ».")
            else:
                request.session.pop(BRANCHE_SESSION_KEY, None)
                messages.success(
                    request, "تم التبديل إلى الرؤية الشاملة (جميع الفروع)."
                )
            return redirect(next_url)
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = BrancheSwitchForm(initial={"branche": get_active_branche(request)})

    return render(
        request,
        "core/branche_switch.html",
        {
            "form": form,
            "title": "تبديل الفرع",
            "next": next_url,
            "active_branche": get_active_branche(request),
        },
    )


# ---------------------------------------------------------------------------
# Branche CRUD (admin only — BR-BRA-06)
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
@admin_required
def branche_list(request):
    """List all branches with their chef de branche (admin only)."""
    branches = Branche.objects.select_related("chef_de_branche").order_by("nom")
    return render(
        request,
        "core/branche_list.html",
        {"branches": branches, "title": "الفروع"},
    )


@login_required(login_url="core:login")
@admin_required
def branche_create(request):
    """Create a new Branche (admin only — BR-BRA-06)."""
    if request.method == "POST":
        form = BrancheForm(request.POST)
        if form.is_valid():
            branche = form.save()
            messages.success(request, f"تم إنشاء الفرع « {branche.nom} » بنجاح.")
            logger.info(
                "Branche '%s' created by '%s'.", branche.code, request.user.username
            )
            return redirect("core:branche_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = BrancheForm()

    return render(
        request,
        "core/branche_form.html",
        {"form": form, "title": "إنشاء فرع", "action_label": "إنشاء"},
    )


@login_required(login_url="core:login")
@admin_required
def branche_edit(request, pk):
    """Edit an existing Branche (admin only — BR-BRA-06)."""
    instance = get_object_or_404(Branche, pk=pk)

    if request.method == "POST":
        form = BrancheForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث الفرع « {instance.nom} » بنجاح.")
            logger.info(
                "Branche '%s' edited by '%s'.", instance.code, request.user.username
            )
            return redirect("core:branche_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = BrancheForm(instance=instance)

    return render(
        request,
        "core/branche_form.html",
        {
            "form": form,
            "instance": instance,
            "title": f"تعديل الفرع « {instance.nom} »",
            "action_label": "حفظ",
        },
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def login_view(request):
    """
    Standard username/password login.
    Redirects to dashboard on success, or to GET parameter ``next``.
    """
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    error = None

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        next_url = request.POST.get("next", "core:dashboard")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.is_active:
                login(request, user)
                logger.info("User '%s' logged in.", user.username)
                return redirect(next_url if next_url else "core:dashboard")
            else:
                error = "هذا الحساب معطَّل. يرجى التواصل مع المسؤول."
        else:
            error = "اسم المستخدم أو كلمة المرور غير صحيحة."

    return render(
        request,
        "core/login.html",
        {
            "error": error,
            "next": request.GET.get("next", ""),
        },
    )


@login_required(login_url="core:login")
@require_POST
def logout_view(request):
    """POST-only logout (CSRF-protected)."""
    logger.info("User '%s' logged out.", request.user.username)
    logout(request)
    messages.success(request, "تم تسجيل الخروج بنجاح.")
    return redirect("core:login")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
def dashboard(request):
    """
    Main dashboard.  Aggregates KPIs from every domain:
      - Open lots and alert conditions
      - Stock alerts (intrants + produits finis below seuil)
      - Overdue supplier invoices (dette en retard)
      - Overdue client invoices (créances en retard)
      - Uninvoiced supplier BLs awaiting invoicing
      - Recent stock movements (last 10)

    v1.4 (§3.5.5): scoped to the request's active branche (Vue par Branche
    — exactly what a chef de branche already sees); admin/comptable in Vue
    Globale (`branche is None`) see the same KPIs aggregated across every
    branche, with no per-branche filter applied.
    """
    import datetime
    from decimal import Decimal

    today = datetime.date.today()
    branche = get_active_branche(request)
    vue_globale = branche is None

    # ── Lots d'élevage ───────────────────────────────────────────────────
    try:
        from elevage.models import LotElevage

        lots_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche is not None:
            lots_qs = lots_qs.filter(branche=branche)
        lots_ouverts_count = lots_qs.count()
    except Exception:
        lots_ouverts_count = 0

    # ── Stock alerts ─────────────────────────────────────────────────────
    try:
        from stock.utils import get_alertes_stock

        alertes_stock = get_alertes_stock()
        if branche is not None:
            alertes_stock = {
                "intrants": [
                    a
                    for a in alertes_stock.get("intrants", [])
                    if a.branche_id == branche.id
                ],
                "produits_finis": [
                    a
                    for a in alertes_stock.get("produits_finis", [])
                    if a.branche_id == branche.id
                ],
            }
        nb_alertes_intrants = len(alertes_stock.get("intrants", []))
        nb_alertes_produits = len(alertes_stock.get("produits_finis", []))
    except Exception:
        nb_alertes_intrants = 0
        nb_alertes_produits = 0
        alertes_stock = {"intrants": [], "produits_finis": []}

    # ── Supplier AP ──────────────────────────────────────────────────────
    try:
        from achats.models import FactureFournisseur

        factures_fournisseur_qs = FactureFournisseur.objects.filter(
            statut__in=[
                FactureFournisseur.STATUT_NON_PAYE,
                FactureFournisseur.STATUT_PARTIELLEMENT_PAYE,
            ]
        )
        if branche is not None:
            factures_fournisseur_qs = factures_fournisseur_qs.filter(branche=branche)

        factures_fournisseur_retard = factures_fournisseur_qs.filter(
            date_echeance__lt=today
        ).count()

        dette_globale_total = factures_fournisseur_qs.aggregate(
            total=Sum("reste_a_payer")
        )["total"] or Decimal("0")
    except Exception:
        factures_fournisseur_retard = 0
        dette_globale_total = Decimal("0")

    # ── Uninvoiced supplier BLs (RECU but not yet FACTURE) ───────────────
    try:
        from achats.models import BLFournisseur

        bls_non_factures_qs = BLFournisseur.objects.filter(
            statut=BLFournisseur.STATUT_RECU
        )
        if branche is not None:
            bls_non_factures_qs = bls_non_factures_qs.filter(branche=branche)
        bls_non_factures = bls_non_factures_qs.count()
    except Exception:
        bls_non_factures = 0

    # ── Client AR ────────────────────────────────────────────────────────
    try:
        from clients.models import FactureClient

        factures_client_qs = FactureClient.objects.filter(
            statut__in=[
                FactureClient.STATUT_NON_PAYEE,
                FactureClient.STATUT_PARTIELLEMENT_PAYEE,
            ]
        )
        if branche is not None:
            factures_client_qs = factures_client_qs.filter(branche=branche)

        factures_client_retard = factures_client_qs.filter(
            date_echeance__lt=today
        ).count()

        creance_globale_total = factures_client_qs.aggregate(
            total=Sum("reste_a_payer")
        )["total"] or Decimal("0")
    except Exception:
        factures_client_retard = 0
        creance_globale_total = Decimal("0")

    # ── Uninvoiced client BLs (LIVRE but not yet FACTURE) ────────────────
    try:
        from clients.models import BLClient

        bls_client_non_factures_qs = BLClient.objects.filter(
            statut=BLClient.STATUT_LIVRE
        )
        if branche is not None:
            bls_client_non_factures_qs = bls_client_non_factures_qs.filter(
                branche=branche
            )
        bls_client_non_factures = bls_client_non_factures_qs.count()
    except Exception:
        bls_client_non_factures = 0

    # ── Recent stock movements (last 10, both segments) ──────────────────
    try:
        from stock.models import StockMouvement

        mouvements_recents_qs = StockMouvement.objects.select_related(
            "intrant", "produit_fini"
        )
        if branche is not None:
            mouvements_recents_qs = mouvements_recents_qs.filter(branche=branche)
        mouvements_recents = mouvements_recents_qs.order_by(
            "-date_mouvement", "-created_at"
        )[:10]
    except Exception:
        mouvements_recents = []

    # ── Open lots summary (for widget) ────────────────────────────────────
    try:
        from elevage.models import LotElevage

        lots_actifs_qs = LotElevage.objects.filter(statut=LotElevage.STATUT_OUVERT)
        if branche is not None:
            lots_actifs_qs = lots_actifs_qs.filter(branche=branche)
        lots_actifs = lots_actifs_qs.select_related("batiment").order_by(
            "-date_ouverture"
        )[:5]
    except Exception:
        lots_actifs = []

    context = {
        # Multi-branch context (§3.5.5)
        "active_branche": branche,
        "vue_globale": vue_globale,
        "peut_changer_de_branche": peut_changer_de_branche(request),
        # Lots
        "lots_ouverts_count": lots_ouverts_count,
        "lots_actifs": lots_actifs,
        # Stock alerts
        "nb_alertes_intrants": nb_alertes_intrants,
        "nb_alertes_produits": nb_alertes_produits,
        "alertes_stock": alertes_stock,
        # Supplier
        "factures_fournisseur_retard": factures_fournisseur_retard,
        "dette_globale_total": dette_globale_total,
        "bls_non_factures": bls_non_factures,
        # Client
        "factures_client_retard": factures_client_retard,
        "creance_globale_total": creance_globale_total,
        "bls_client_non_factures": bls_client_non_factures,
        # Recent activity
        "mouvements_recents": mouvements_recents,
        # Alert badge totals
        "total_alertes": (
            nb_alertes_intrants
            + nb_alertes_produits
            + factures_fournisseur_retard
            + factures_client_retard
        ),
    }
    return render(request, "core/dashboard.html", context)


# ---------------------------------------------------------------------------
# Company Information (singleton)
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
@admin_required
def company_info_view(request):
    """
    View / edit the singleton CompanyInfo record.
    GET  → display current values + pre-filled form.
    POST → validate and save; redirect back with success message (PRG).
    """
    instance = CompanyInfo.get_instance()

    if request.method == "POST":
        form = CompanyInfoForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "تم تحديث معلومات الشركة بنجاح.",
            )
            logger.info("CompanyInfo updated by user '%s'.", request.user.username)
            return redirect("core:company_info")
        else:
            messages.error(
                request,
                "يرجى تصحيح الأخطاء في النموذج.",
            )
    else:
        form = CompanyInfoForm(instance=instance)

    return render(
        request,
        "core/company_info.html",
        {
            "form": form,
            "instance": instance,
            "title": "معلومات الشركة",
        },
    )


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------


@login_required(login_url="core:login")
@admin_required
def user_list(request):
    """
    List all application users with their profile roles.
    Active users are shown first, then inactive, alphabetically by username.
    """
    users = User.objects.select_related("profile").order_by("-is_active", "username")
    return render(
        request,
        "core/user_list.html",
        {
            "users": users,
            "title": "إدارة المستخدمين",
        },
    )


@login_required(login_url="core:login")
@admin_required
def user_create(request):
    """
    Create a new application user + linked UserProfile.
    POST-Redirect-Get: on success redirects to user_list.
    """
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f"تم إنشاء المستخدم « {user.username} » بنجاح.",
            )
            logger.info(
                "User '%s' created by '%s'.",
                user.username,
                request.user.username,
            )
            return redirect("core:user_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = UserCreateForm()

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "title": "إنشاء مستخدم",
            "action_label": "إنشاء",
        },
    )


@login_required(login_url="core:login")
@admin_required
def user_edit(request, pk):
    """
    Edit an existing user's info and UserProfile.
    Password changes are handled separately via user_password_change.
    """
    target_user = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=target_user)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"تم تحديث المستخدم « {target_user.username} » بنجاح.",
            )
            logger.info(
                "User '%s' edited by '%s'.",
                target_user.username,
                request.user.username,
            )
            return redirect("core:user_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = UserUpdateForm(instance=target_user)

    return render(
        request,
        "core/user_form.html",
        {
            "form": form,
            "target_user": target_user,
            "title": f"تعديل المستخدم « {target_user.username} »",
            "action_label": "حفظ",
        },
    )


@login_required(login_url="core:login")
@admin_required
@require_POST
def user_toggle_active(request, pk):
    """
    Activate or deactivate a user account (POST only, CSRF-protected).
    An admin cannot deactivate their own account.
    """
    target_user = get_object_or_404(User, pk=pk)

    if target_user == request.user:
        messages.error(
            request,
            "لا يمكنك تعطيل حسابك الخاص.",
        )
        return redirect("core:user_list")

    target_user.is_active = not target_user.is_active
    target_user.save(update_fields=["is_active"])

    if target_user.is_active:
        messages.success(
            request,
            f"تم تفعيل حساب « {target_user.username} ».",
        )
        logger.info(
            "User '%s' activated by '%s'.",
            target_user.username,
            request.user.username,
        )
    else:
        messages.warning(
            request,
            f"تم تعطيل حساب « {target_user.username} ».",
        )
        logger.info(
            "User '%s' deactivated by '%s'.",
            target_user.username,
            request.user.username,
        )

    return redirect("core:user_list")


@login_required(login_url="core:login")
def user_password_change(request, pk):
    """
    Change a user's password.

    Admins may change any user's password.
    Non-admins may only change their own password, and must supply the
    current password (Django's built-in PasswordChangeForm).

    POST-Redirect-Get: on success the session is updated to prevent logout
    (for self-change) and a success message is displayed.
    """
    target_user = get_object_or_404(User, pk=pk)

    # Permission gate: only admins or the user themselves.
    if not (is_admin(request.user) or request.user == target_user):
        messages.error(
            request,
            "ليس لديك صلاحية تغيير كلمة المرور هذه.",
        )
        return redirect("core:dashboard")

    if request.method == "POST":
        form = PasswordChangeForm(user=target_user, data=request.POST)
        if form.is_valid():
            form.save()
            # Keep the requesting user's session alive if they changed their own password.
            if request.user == target_user:
                update_session_auth_hash(request, target_user)
            messages.success(
                request,
                f"تم تغيير كلمة مرور « {target_user.username} » بنجاح.",
            )
            logger.info(
                "Password changed for user '%s' by '%s'.",
                target_user.username,
                request.user.username,
            )
            return redirect(
                "core:user_list" if is_admin(request.user) else "core:dashboard"
            )
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = PasswordChangeForm(user=target_user)

    return render(
        request,
        "core/password_change.html",
        {
            "form": form,
            "target_user": target_user,
            "title": f"تغيير كلمة المرور — {target_user.username}",
        },
    )


@login_required(login_url="core:login")
def own_password_change(request):
    """
    Shortcut for an authenticated user to change their own password.
    Delegates to user_password_change with the current user's pk.
    """
    return user_password_change(request, pk=request.user.pk)


@login_required(login_url="core:login")
def profile_view(request):
    """
    Authenticated user views and edits their own profile (name, email, phone).
    Role is NOT editable by the user themselves — admin-only via user_edit.
    Password change is a separate link.
    """
    target_user = request.user

    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=target_user)
        if form.is_valid():
            # Preserve the existing role — prevent self-escalation.
            saved_user = form.save(commit=False)
            saved_user.save()
            try:
                profile = saved_user.profile
                profile.telephone = form.cleaned_data.get("telephone", "")
                profile.notes = form.cleaned_data.get("notes", "")
                # Role is not changed here.
                profile.save(update_fields=["telephone", "notes", "updated_at"])
            except UserProfile.DoesNotExist:
                pass
            messages.success(request, "تم تحديث ملفك الشخصي.")
            return redirect("core:profile")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء في النموذج.")
    else:
        form = UserUpdateForm(instance=target_user)
        # Disable role field for non-admins.
        if not is_admin(request.user):
            form.fields["role"].disabled = True

    return render(
        request,
        "core/profile.html",
        {
            "form": form,
            "target_user": target_user,
            "title": "ملفي الشخصي",
        },
    )
