"""
core/urls.py

URL patterns for:
  - Authentication   (login, logout)
  - Dashboard        (/)
  - Company Info     (/parametres/entreprise/)
  - User Management  (/parametres/utilisateurs/)
  - My Profile       (/profil/)
"""

from django.urls import path
from core import views

app_name = "core"

urlpatterns = [
    # ── Authentication ──────────────────────────────────────────────────
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # ── Dashboard ───────────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),
    # ── My Profile ──────────────────────────────────────────────────────
    path("profil/", views.profile_view, name="profile"),
    path(
        "profil/mot-de-passe/",
        views.own_password_change,
        name="own_password_change",
    ),
    # ── Company Information ─────────────────────────────────────────────
    path(
        "parametres/entreprise/",
        views.company_info_view,
        name="company_info",
    ),
    # ── User Management (admin only) ────────────────────────────────────
    path(
        "parametres/utilisateurs/",
        views.user_list,
        name="user_list",
    ),
    path(
        "parametres/utilisateurs/creer/",
        views.user_create,
        name="user_create",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/modifier/",
        views.user_edit,
        name="user_edit",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/activer/",
        views.user_toggle_active,
        name="user_toggle_active",
    ),
    path(
        "parametres/utilisateurs/<int:pk>/mot-de-passe/",
        views.user_password_change,
        name="user_password_change",
    ),
]
