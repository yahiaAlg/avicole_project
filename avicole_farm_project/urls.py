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
