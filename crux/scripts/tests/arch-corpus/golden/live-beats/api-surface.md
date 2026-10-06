# API surface

_Static parse of the Phoenix router `lib/live_beats_web/router.ex`; the DSL is not executed._

_Pipelines (per-scope labels, not columns): `:browser`, `:redirect_if_user_is_authenticated`._

## Routes (8)

| method | path | controller |
|---|---|---|
| GET | `/` | `LiveBeatsWeb.RedirectController#redirect_authenticated` |
| LIVE | `/:profile_username` | `LiveBeatsWeb.ProfileLive#show` |
| LIVE | `/:profile_username/songs/new` | `LiveBeatsWeb.ProfileLive#new` |
| GET | `/files/:id` | `LiveBeatsWeb.FileController#show` |
| GET | `/oauth/callbacks/:provider` | `LiveBeatsWeb.OAuthCallbackController#new` |
| LIVE | `/profile/settings` | `LiveBeatsWeb.SettingsLive#edit` |
| LIVE | `/signin` | `LiveBeatsWeb.SignInLive#index` |
| DELETE | `/signout` | `LiveBeatsWeb.OAuthCallbackController#sign_out` |

## Residuals

- Best-effort static parse: `forward`, `resources` path-restructuring options (`param:`, `singleton:`, `member`/`collection` blocks), unrecognized `scope` forms, dynamic scope aliases, and metaprogrammed/macro-defined routes are not expanded (named residuals).
- A `forward` route was found and not expanded (a named residual).
