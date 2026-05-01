#!/usr/bin/env bash
# =============================================================================
# build.sh — Bootstrap the Avicole Django backend project
# Usage  : chmod +x build.sh && ./build.sh
# Prereq : Python 3.11+, pip, virtualenv (or python -m venv)
# =============================================================================
set -euo pipefail

PROJECT_NAME="avicole_farm_project"
APPS=(core intrants stock elevage production achats clients depenses reporting)


# ---------------------------------------------------------------------------
# 2. Django project scaffold
# ---------------------------------------------------------------------------
echo ">>> Scaffolding Django project: $PROJECT_NAME"
django-admin startproject "$PROJECT_NAME" .

# ---------------------------------------------------------------------------
# 3. Create Django apps
# ---------------------------------------------------------------------------
echo ">>> Creating apps..."
for APP in "${APPS[@]}"; do
    python manage.py startapp "$APP"
    echo "    [+] $APP"
done

# ---------------------------------------------------------------------------
# 4. Per-app extra files
#    Every app gets: admin.py (exists), models.py (exists), views.py (exists)
#    Plus: urls.py, forms.py, signals.py, utils.py, resources.py
#    reporting: no models.py overwrite needed (no domain models)
# ---------------------------------------------------------------------------
echo ">>> Adding extra module files to each app..."

for APP in "${APPS[@]}"; do
    APP_DIR="./$APP"
    
    # urls.py
    cat > "$APP_DIR/urls.py" <<PYEOF
from django.urls import path
from . import views

app_name = "$APP"

urlpatterns = [
    # TODO: add $APP URL patterns here
]
PYEOF
    
    # forms.py
    cat > "$APP_DIR/forms.py" <<PYEOF
# $APP/forms.py
# Django forms for the $APP application.
PYEOF
    
    # signals.py
    cat > "$APP_DIR/signals.py" <<PYEOF
# $APP/signals.py
# Django signals for the $APP application.
# Register receivers here and connect via AppConfig.ready().
from django.dispatch import receiver  # noqa: F401
PYEOF
    
    # utils.py
    cat > "$APP_DIR/utils.py" <<PYEOF
# $APP/utils.py
# Utility / helper functions for the $APP application.
PYEOF
    
    # resources.py  (django-import-export)
    cat > "$APP_DIR/resources.py" <<PYEOF
# $APP/resources.py
# django-import-export ModelResource definitions for the $APP application.
from import_export import resources  # noqa: F401
PYEOF
    
    # Patch AppConfig to wire up signals automatically
    cat > "$APP_DIR/apps.py" <<PYEOF
from django.apps import AppConfig


class $(echo "${APP^}")Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "$APP"

    def ready(self):
        import $APP.signals  # noqa: F401
PYEOF
    
done

# ---------------------------------------------------------------------------
# 5. reporting app — no domain models; give it a dedicated views stub
# ---------------------------------------------------------------------------
echo ">>> Patching reporting app (no models needed)..."
cat > ./reporting/models.py <<PYEOF
# reporting/models.py
# This app has no domain models.
# All data is read from other apps via cross-app queries.
PYEOF

cat > ./reporting/views.py <<PYEOF
# reporting/views.py
# Dashboard and report views aggregating data from multiple apps.
PYEOF

cat > ./reporting/urls.py <<PYEOF
from django.urls import path
from . import views

app_name = "reporting"

urlpatterns = [
    # e.g. path("dashboard/", views.dashboard, name="dashboard"),
    # e.g. path("lot/<int:pk>/", views.lot_report, name="lot_report"),
]
PYEOF

# ---------------------------------------------------------------------------
# 6. Copy uploaded model files into their respective apps
# ---------------------------------------------------------------------------
echo ">>> Placing domain model files..."
# These paths assume the uploaded files are alongside build.sh.
# Adjust source paths as needed.

copy_if_exists() {
    SRC="$1"; DEST="$2"
    if [ -f "$SRC" ]; then
        cp "$SRC" "$DEST"
        echo "    [+] $DEST"
    else
        echo "    [!] $SRC not found — skipping"
    fi
}

# Model files (rename from the uploaded slugs to models.py)
copy_if_exists "models (0).py"  "./core/models.py"
copy_if_exists "models (1).py"  "./intrants/models.py"
copy_if_exists "models (2).py"  "./stock/models.py"
copy_if_exists "models (3).py"  "./elevage/models.py"
copy_if_exists "models (4).py"  "./production/models.py"
copy_if_exists "models (5).py"  "./achats/models.py"
copy_if_exists "models (6).py"  "./clients/models.py"
copy_if_exists "models (7).py"  "./depenses/models.py"

# ---------------------------------------------------------------------------
# 7. Wire apps into settings.py
# ---------------------------------------------------------------------------
echo ">>> Patching settings.py..."
SETTINGS_FILE="./$PROJECT_NAME/settings.py"

# Append INSTALLED_APPS entries (idempotent-ish: appended once)
python - <<PYEOF
import re

with open("$SETTINGS_FILE") as f:
    content = f.read()

extra_apps = """    # Project apps
    "core",
    "intrants",
    "stock",
    "elevage",
    "production",
    "achats",
    "clients",
    "depenses",
    "reporting",
    # Third-party
    "rest_framework",
    "corsheaders",
    "import_export",
"""

# Insert before the closing bracket of INSTALLED_APPS
content = re.sub(
    r"(INSTALLED_APPS\s*=\s*\[)(.*?)(\])",
    lambda m: m.group(1) + m.group(2) + extra_apps + m.group(3),
    content,
    flags=re.DOTALL,
    count=1,
)

# Add CORS middleware after SecurityMiddleware
content = content.replace(
    '"django.middleware.security.SecurityMiddleware",',
    '"django.middleware.security.SecurityMiddleware",\n    "corsheaders.middleware.CorsMiddleware",',
)

# Media files
if "MEDIA_ROOT" not in content:
    content += """
# ---------------------------------------------------------------------------
# Media files
# ---------------------------------------------------------------------------
import os  # noqa: E402
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# CORS (development — tighten in production)
CORS_ALLOW_ALL_ORIGINS = True
"""

with open("$SETTINGS_FILE", "w") as f:
    f.write(content)

print("    settings.py patched.")
PYEOF

# ---------------------------------------------------------------------------
# 8. Root urls.py — include each app's urls
# ---------------------------------------------------------------------------
echo ">>> Patching root urls.py..."
cat > "./$PROJECT_NAME/urls.py" <<PYEOF
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/core/",       include("core.urls",       namespace="core")),
    path("api/intrants/",   include("intrants.urls",   namespace="intrants")),
    path("api/stock/",      include("stock.urls",       namespace="stock")),
    path("api/elevage/",    include("elevage.urls",     namespace="elevage")),
    path("api/production/", include("production.urls",  namespace="production")),
    path("api/achats/",     include("achats.urls",      namespace="achats")),
    path("api/clients/",    include("clients.urls",     namespace="clients")),
    path("api/depenses/",   include("depenses.urls",    namespace="depenses")),
    path("api/reporting/",  include("reporting.urls",   namespace="reporting")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
PYEOF

# ---------------------------------------------------------------------------
# 9. Initial migrations
# ---------------------------------------------------------------------------
echo ">>> Running initial migrations..."
python manage.py makemigrations --no-input
python manage.py migrate --no-input

# ---------------------------------------------------------------------------
# 10. Print project tree
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Project structure"
echo "============================================================"
find . -not -path './.venv/*' \
-not -path './__pycache__/*' \
-not -name '*.pyc' \
-not -path './.git/*' \
| sort | head -120

echo ""
echo "============================================================"
echo " Done!  Next steps:"
echo "   source .venv/bin/activate"
echo "   python manage.py createsuperuser"
echo "   python manage.py runserver"
echo "============================================================"