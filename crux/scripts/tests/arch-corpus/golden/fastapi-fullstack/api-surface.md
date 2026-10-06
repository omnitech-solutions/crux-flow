# API surface

_Static `ast` parse of the project's own Python sources (FastAPI and Flask route decorators, Django `urlpatterns`); the app is never imported._

## Routes (23)

| method | path | handler |
|---|---|---|
| GET | `${settings.API_V1_STR}/items/` | `read_items` |
| POST | `${settings.API_V1_STR}/items/` | `create_item` |
| DELETE | `${settings.API_V1_STR}/items/{id}` | `delete_item` |
| GET | `${settings.API_V1_STR}/items/{id}` | `read_item` |
| PUT | `${settings.API_V1_STR}/items/{id}` | `update_item` |
| POST | `${settings.API_V1_STR}/login/access-token` | `login_access_token` |
| POST | `${settings.API_V1_STR}/login/test-token` | `test_token` |
| POST | `${settings.API_V1_STR}/password-recovery-html-content/{email}` | `recover_password_html_content` |
| POST | `${settings.API_V1_STR}/password-recovery/{email}` | `recover_password` |
| POST | `${settings.API_V1_STR}/private/users/` | `create_user` |
| POST | `${settings.API_V1_STR}/reset-password/` | `reset_password` |
| GET | `${settings.API_V1_STR}/users/` | `read_users` |
| POST | `${settings.API_V1_STR}/users/` | `create_user` |
| DELETE | `${settings.API_V1_STR}/users/me` | `delete_user_me` |
| GET | `${settings.API_V1_STR}/users/me` | `read_user_me` |
| PATCH | `${settings.API_V1_STR}/users/me` | `update_user_me` |
| PATCH | `${settings.API_V1_STR}/users/me/password` | `update_password_me` |
| POST | `${settings.API_V1_STR}/users/signup` | `register_user` |
| DELETE | `${settings.API_V1_STR}/users/{user_id}` | `delete_user` |
| GET | `${settings.API_V1_STR}/users/{user_id}` | `read_user_by_id` |
| PATCH | `${settings.API_V1_STR}/users/{user_id}` | `update_user` |
| GET | `${settings.API_V1_STR}/utils/health-check/` | `health_check` |
| POST | `${settings.API_V1_STR}/utils/test-email/` | `test_email` |

## Residuals

- This table is a floor, not a census. A static parse sees a route only where a decorator or a `urlpatterns` element DECLARES it; a route registered by `add_api_route`, `add_url_rule`, a loop, or an application factory is not declared anywhere this parse can read.
- 1 mount prefix expression(s) are decided at import time and cannot be evaluated by a parse, so every path under them renders the expression VERBATIM as `${…}` rather than a guessed value: `settings.API_V1_STR`.
- Source(s) Python's parser refused — malformed, or nested past the parser's own limits (hashed, not parsed): `backend/app/api/deps.py`.
