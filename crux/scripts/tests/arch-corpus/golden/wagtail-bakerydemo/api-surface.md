# API surface

_Static `ast` parse of the project's own Python sources (FastAPI and Flask route decorators, Django `urlpatterns`); the app is never imported._

## Routes (9)

| method | path | handler |
|---|---|---|
| — | `/api/v2/` | `api_router.urls` |
| — | `/api/v3-preview/` | `api.urls` |
| — | `/django-admin/` | `admin.site.urls` |
| — | `/favicon.ico` | `RedirectView.as_view(…)` |
| — | `/images/([^/]*)/(\\d*)/([^/]*)/[^/]*` | `ServeView.as_view()` |
| — | `/search/` | `search_views.search` |
| — | `/sitemap.xml` | `sitemap` |
| — | `/test404/` | `TemplateView.as_view(…)` |
| — | `/test500/` | `TemplateView.as_view(…)` |

## Residuals

- This table is a floor, not a census. A static parse sees a route only where a decorator or a `urlpatterns` element DECLARES it; a route registered by `add_api_route`, `add_url_rule`, a loop, or an application factory is not declared anywhere this parse can read.
- A `urlpatterns` assignment nested inside an `if` block is read unconditionally, because the condition is evaluated at import time and this parse evaluates nothing. A development-only pattern therefore appears here.
- 4 mount(s) name a target this repository does not declare, so their routes are absent: `include(debug_toolbar.urls)`, `include(wagtail_urls)`, `include(wagtailadmin_urls)`, `include(wagtaildocs_urls)`.
