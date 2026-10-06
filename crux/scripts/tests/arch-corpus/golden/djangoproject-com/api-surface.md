# API surface

_Static `ast` parse of the project's own Python sources (FastAPI and Flask route decorators, Django `urlpatterns`); the app is never imported._

## Routes (109)

| method | path | handler |
|---|---|---|
| — | `/` | `TemplateView.as_view(…)` |
| — | `/` | `views.index` |
| — | `/(.*)/index/` | `views.redirect_index` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/` | `views.document` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/(?P<subpath>_downloads\|_source)/(?P<path>.*)` | `views_debug.sphinx_static` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/(?P<url>[\\w./-]*)/` | `views.document` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/_images/(?P<path>.*)` | `views_debug.sphinx_static` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/_objects/` | `views_debug.objects_inventory` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/search/` | `views.search_results` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/search/description/` | `views.search_description` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>[\\w.-]+)/search/suggestions/` | `views.search_suggestions` |
| — | `/(?P<lang>[a-z-]+)/(?P<version>stable)/(?P<url>.*)` | `views.stable` |
| — | `/.well-known/security.txt` | `TemplateView.as_view(…)` |
| — | `/400/` | `partial(…)` |
| — | `/403/` | `partial(…)` |
| — | `/404/` | `partial(…)` |
| — | `/410/` | `gone` |
| — | `/500/` | `defaults.server_error` |
| — | `/<lang>/` | `views.language` |
| — | `/[a-z-]+/[\\w.-]+/internals/team/` | `RedirectView.as_view(…)` |
| — | `/about/` | `RedirectView.as_view(…)` |
| — | `/accounts/delete/` | `account_views.delete_profile` |
| — | `/accounts/delete/success/` | `account_views.delete_profile_success` |
| — | `/accounts/edit/` | `account_views.edit_profile` |
| — | `/accounts/register/` | `RegistrationView.as_view(form_class=RegistrationFormWithCaptcha)` |
| — | `/admin/` | `admin.site.urls` |
| — | `/admin/password_reset/` | `auth_views.PasswordResetView.as_view()` |
| — | `/admin/password_reset/done/` | `auth_views.PasswordResetDoneView.as_view()` |
| — | `/checklists/release/<str:version>/` | `views.release_checklist` |
| — | `/checklists/security/issue/<str:cve_id>/` | `views.cve_json_record` |
| — | `/checklists/security/release/<int:pk>/` | `views.securityrelease_checklist` |
| — | `/comments/` | `gone` |
| — | `/community/` | `views.index` |
| — | `/community/<feed_type_slug>/` | `views.FeedListView.as_view()` |
| — | `/community/add/<feed_type_slug>/` | `views.add_feed` |
| — | `/community/delete/<int:feed_id>/` | `views.delete_feed` |
| — | `/community/ecosystem/` | `TemplateView.as_view(…)` |
| — | `/community/edit/<int:feed_id>/` | `views.edit_feed` |
| — | `/community/local/` | `views.LocalDjangoCommunitiesListView.as_view()` |
| — | `/community/mine/` | `views.my_feeds` |
| — | `/conduct/` | `TemplateView.as_view(…)` |
| — | `/conduct/changes/` | `TemplateView.as_view(…)` |
| — | `/conduct/enforcement-manual/` | `TemplateView.as_view(…)` |
| — | `/conduct/faq/` | `TemplateView.as_view(…)` |
| — | `/conduct/reporting/` | `TemplateView.as_view(…)` |
| — | `/contact/foundation/` | `ContactFoundation.as_view()` |
| — | `/contact/sent/` | `TemplateView.as_view(…)` |
| — | `/diversity/` | `TemplateView.as_view(…)` |
| — | `/diversity/changes/` | `TemplateView.as_view(…)` |
| — | `/documentation` | `gone` |
| — | `/download/` | `index` |
| — | `/download/(?P<series>(?:\\d{1,2}\\.[0-2]\|\\d{4}))/roadmap/` | `roadmap` |
| — | `/download/([0-9a-z_.-]+)/(tarball\|wheel\|checksum)/` | `redirect` |
| — | `/foundation/banners/<int:pk>/preview/` | `BannerPreview.as_view()` |
| — | `/foundation/corporate-members/` | `corporate_member_list_view` |
| — | `/foundation/corporate-members/badges/` | `CorporateMemberBadgesView.as_view()` |
| — | `/foundation/corporate-membership/join/` | `CorporateMemberSignUpView.as_view()` |
| — | `/foundation/corporate-membership/join/thanks/` | `TemplateView.as_view(…)` |
| — | `/foundation/corporate-membership/renew/<token>/` | `CorporateMemberRenewView.as_view()` |
| — | `/foundation/developer-members/` | `RedirectView.as_view(…)` |
| — | `/foundation/django_core/` | `CoreDevelopers.as_view()` |
| — | `/foundation/individual-members/` | `IndividualMemberListView.as_view()` |
| — | `/foundation/individual-membership-nomination/` | `RedirectView.as_view(…)` |
| — | `/foundation/minutes/` | `views.MeetingArchiveIndex.as_view()` |
| — | `/foundation/minutes/<int:year>/` | `views.MeetingArchiveYear.as_view()` |
| — | `/foundation/minutes/<int:year>/<str:month>/` | `views.MeetingArchiveMonth.as_view()` |
| — | `/foundation/minutes/<int:year>/<str:month>/<int:day>/` | `views.MeetingArchiveDay.as_view()` |
| — | `/foundation/minutes/<int:year>/<str:month>/<int:day>/<str:slug>/` | `views.MeetingDetail.as_view()` |
| — | `/foundation/teams/` | `TeamsListView.as_view()` |
| — | `/fundraising/` | `views.index` |
| — | `/fundraising/donation-session` | `views.configure_checkout_session` |
| — | `/fundraising/manage-donations/<hero>/` | `views.manage_donations` |
| — | `/fundraising/manage-donations/<hero>/cancel/` | `views.cancel_donation` |
| — | `/fundraising/receive-webhook/` | `views.receive_webhook` |
| — | `/fundraising/thank-you/` | `views.thank_you` |
| — | `/fundraising/update-card/` | `views.update_card` |
| — | `/google79eabba6bf6fd6d3.html` | `lambda req: HttpResponse('google-site-verification: google79eabba6bf6fd6d3.html…` |
| — | `/m/<path:path>` | `serve` |
| — | `/metric/` | `views.index` |
| — | `/metric/<slug:metric_slug>.json` | `views.metric_json` |
| — | `/metric/<slug:metric_slug>/` | `views.metric_detail` |
| — | `/overview/` | `RedirectView.as_view(…)` |
| — | `/pots/(?P<pot_name>\\w+\\.pot)` | `views_debug.pot_file` |
| — | `/r/(?P<content_type_id>\\d+)/(?P<object_id>.*)/` | `contenttypes_views.shortcut` |
| — | `/reset/<uidb64>/<token>/` | `auth_views.PasswordResetConfirmView.as_view()` |
| — | `/reset/done/` | `auth_views.PasswordResetCompleteView.as_view()` |
| — | `/rss/comments/` | `gone` |
| — | `/rss/community/` | `RedirectView.as_view(…)` |
| — | `/rss/community/<slug>/` | `CommunityAggregatorFeed()` |
| — | `/rss/community/firehose/` | `CommunityAggregatorFirehoseFeed()` |
| — | `/rss/foundation/minutes/` | `FoundationMinutesFeed()` |
| — | `/rss/weblog/` | `WeblogEntryFeed()` |
| — | `/search/` | `views.redirect_search` |
| — | `/sitemap-<section>.xml` | `sitemap` |
| — | `/sitemap.xml` | `cache_page(60 * 60 * 6)(sitemap_views.sitemap)` |
| — | `/sitemap.xml` | `sitemap_index` |
| — | `/sponsor/` | `fundraising_views.sponsor` |
| — | `/start/` | `TemplateView.as_view(…)` |
| — | `/start/overview/` | `TemplateView.as_view(…)` |
| — | `/styleguide/` | `TemplateView.as_view(…)` |
| — | `/svntogit/<int:svn_revision>/` | `redirect_to_github` |
| — | `/trac/api/tickets/<int:ticket_id>` | `views.api_ticket` |
| — | `/trac/bouncing/` | `views.bouncing_tickets` |
| — | `/weblog/` | `views.BlogArchiveIndexView.as_view()` |
| — | `/weblog/<int:year>/` | `views.BlogYearArchiveView.as_view()` |
| — | `/weblog/<int:year>/<str:month>/` | `views.BlogMonthArchiveView.as_view()` |
| — | `/weblog/<int:year>/<str:month>/<int:day>/` | `views.BlogDayArchiveView.as_view()` |
| — | `/weblog/<int:year>/<str:month>/<int:day>/<slug>/` | `views.BlogDateDetailView.as_view()` |
| — | `/~<username>/` | `account_views.user_profile` |

## Residuals

- This table is a floor, not a census. A static parse sees a route only where a decorator or a `urlpatterns` element DECLARES it; a route registered by `add_api_route`, `add_url_rule`, a loop, or an application factory is not declared anywhere this parse can read.
- A `urlpatterns` assignment nested inside an `if` block is read unconditionally, because the condition is evaluated at import time and this parse evaluates nothing. A development-only pattern therefore appears here.
- 4 mount(s) name a target this repository does not declare, so their routes are absent: `include('django.contrib.auth.urls')`, `include('django_push.subscriber.urls')`, `include('registration.backends.default.urls')`, `include(debug_toolbar.urls)`.
