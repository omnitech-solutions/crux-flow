# Data model

_Derived from Ecto schemas (static parse; no Elixir executed)._

## Entities (40 tables)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| accounts | `id` | binary_id | no | — | — |
| accounts | `inserted_at` | naive_datetime | — | — | — |
| accounts | `updated_at` | naive_datetime | — | — | — |
| browser_replay_events | `id` | binary_id | no | — | — |
| browser_replay_events | `inserted_at` | naive_datetime | — | — | — |
| browser_replay_events | `updated_at` | naive_datetime | — | — | — |
| browser_sessions | `id` | binary_id | no | — | — |
| browser_sessions | `inserted_at` | naive_datetime | — | — | — |
| browser_sessions | `updated_at` | naive_datetime | — | — | — |
| canned_responses | `content` | string | — | — | — |
| canned_responses | `id` | binary_id | no | — | — |
| canned_responses | `inserted_at` | naive_datetime | — | — | — |
| canned_responses | `name` | string | — | — | — |
| canned_responses | `updated_at` | naive_datetime | — | — | — |
| companies | `id` | binary_id | no | — | — |
| companies | `inserted_at` | naive_datetime | — | — | — |
| companies | `updated_at` | naive_datetime | — | — | — |
| conversation_issues | `id` | binary_id | no | — | — |
| conversation_issues | `inserted_at` | naive_datetime | — | — | — |
| conversation_issues | `updated_at` | naive_datetime | — | — | — |
| conversation_tags | `id` | binary_id | no | — | — |
| conversation_tags | `inserted_at` | naive_datetime | — | — | — |
| conversation_tags | `updated_at` | naive_datetime | — | — | — |
| conversations | `id` | binary_id | no | — | — |
| conversations | `inserted_at` | naive_datetime | — | — | — |
| conversations | `updated_at` | naive_datetime | — | — | — |
| customer_issues | `id` | binary_id | no | — | — |
| customer_issues | `inserted_at` | naive_datetime | — | — | — |
| customer_issues | `updated_at` | naive_datetime | — | — | — |
| customer_tags | `id` | binary_id | no | — | — |
| customer_tags | `inserted_at` | naive_datetime | — | — | — |
| customer_tags | `updated_at` | naive_datetime | — | — | — |
| customers | `id` | binary_id | no | — | — |
| customers | `inserted_at` | naive_datetime | — | — | — |
| customers | `updated_at` | naive_datetime | — | — | — |
| event_subscriptions | `id` | binary_id | no | — | — |
| event_subscriptions | `inserted_at` | naive_datetime | — | — | — |
| event_subscriptions | `scope` | string | — | — | — |
| event_subscriptions | `updated_at` | naive_datetime | — | — | — |
| event_subscriptions | `verified` | boolean | — | false | — |
| event_subscriptions | `webhook_url` | string | — | — | — |
| files | `id` | binary_id | no | — | — |
| files | `inserted_at` | naive_datetime | — | — | — |
| files | `updated_at` | naive_datetime | — | — | — |
| forwarding_addresses | `id` | binary_id | no | — | — |
| forwarding_addresses | `inserted_at` | naive_datetime | — | — | — |
| forwarding_addresses | `updated_at` | naive_datetime | — | — | — |
| github_authorizations | `id` | binary_id | no | — | — |
| github_authorizations | `inserted_at` | naive_datetime | — | — | — |
| github_authorizations | `updated_at` | naive_datetime | — | — | — |
| gmail_conversation_threads | `id` | binary_id | no | — | — |
| gmail_conversation_threads | `inserted_at` | naive_datetime | — | — | — |
| gmail_conversation_threads | `updated_at` | naive_datetime | — | — | — |
| google_authorizations | `id` | binary_id | no | — | — |
| google_authorizations | `inserted_at` | naive_datetime | — | — | — |
| google_authorizations | `updated_at` | naive_datetime | — | — | — |
| hubspot_authorizations | `id` | binary_id | no | — | — |
| hubspot_authorizations | `inserted_at` | naive_datetime | — | — | — |
| hubspot_authorizations | `updated_at` | naive_datetime | — | — | — |
| inbox_members | `id` | binary_id | no | — | — |
| inbox_members | `inserted_at` | naive_datetime | — | — | — |
| inbox_members | `updated_at` | naive_datetime | — | — | — |
| inboxes | `id` | binary_id | no | — | — |
| inboxes | `inserted_at` | naive_datetime | — | — | — |
| inboxes | `updated_at` | naive_datetime | — | — | — |
| intercom_authorizations | `id` | binary_id | no | — | — |
| intercom_authorizations | `inserted_at` | naive_datetime | — | — | — |
| intercom_authorizations | `updated_at` | naive_datetime | — | — | — |
| issues | `id` | binary_id | no | — | — |
| issues | `inserted_at` | naive_datetime | — | — | — |
| issues | `updated_at` | naive_datetime | — | — | — |
| lambdas | `id` | binary_id | no | — | — |
| lambdas | `inserted_at` | naive_datetime | — | — | — |
| lambdas | `updated_at` | naive_datetime | — | — | — |
| mattermost_authorizations | `id` | binary_id | no | — | — |
| mattermost_authorizations | `inserted_at` | naive_datetime | — | — | — |
| mattermost_authorizations | `updated_at` | naive_datetime | — | — | — |
| mattermost_conversation_threads | `id` | binary_id | no | — | — |
| mattermost_conversation_threads | `inserted_at` | naive_datetime | — | — | — |
| mattermost_conversation_threads | `updated_at` | naive_datetime | — | — | — |
| mentions | `id` | binary_id | no | — | — |
| mentions | `inserted_at` | naive_datetime | — | — | — |
| mentions | `updated_at` | naive_datetime | — | — | — |
| message_files | `id` | binary_id | no | — | — |
| message_files | `inserted_at` | naive_datetime | — | — | — |
| message_files | `updated_at` | naive_datetime | — | — | — |
| messages | `id` | binary_id | no | — | — |
| messages | `inserted_at` | naive_datetime | — | — | — |
| messages | `updated_at` | naive_datetime | — | — | — |
| notes | `id` | binary_id | no | — | — |
| notes | `inserted_at` | naive_datetime | — | — | — |
| notes | `updated_at` | naive_datetime | — | — | — |
| personal_api_keys | `id` | binary_id | no | — | — |
| personal_api_keys | `inserted_at` | naive_datetime | — | — | — |
| personal_api_keys | `updated_at` | naive_datetime | — | — | — |
| pow_sessions | `expires_at` | utc_datetime | — | — | — |
| pow_sessions | `key` | {:array, :binary} | — | — | — |
| pow_sessions | `namespace` | string | — | — | — |
| pow_sessions | `original_key` | binary | — | — | — |
| pow_sessions | `value` | binary | — | — | — |
| slack_authorizations | `id` | binary_id | no | — | — |
| slack_authorizations | `inserted_at` | naive_datetime | — | — | — |
| slack_authorizations | `updated_at` | naive_datetime | — | — | — |
| slack_conversation_threads | `id` | binary_id | no | — | — |
| slack_conversation_threads | `inserted_at` | naive_datetime | — | — | — |
| slack_conversation_threads | `updated_at` | naive_datetime | — | — | — |
| tags | `id` | binary_id | no | — | — |
| tags | `inserted_at` | naive_datetime | — | — | — |
| tags | `updated_at` | naive_datetime | — | — | — |
| twilio_authorizations | `id` | binary_id | no | — | — |
| twilio_authorizations | `inserted_at` | naive_datetime | — | — | — |
| twilio_authorizations | `updated_at` | naive_datetime | — | — | — |
| user_invitations | `id` | binary_id | no | — | — |
| user_invitations | `inserted_at` | naive_datetime | — | — | — |
| user_invitations | `updated_at` | naive_datetime | — | — | — |
| user_profiles | `display_name` | string | — | — | — |
| user_profiles | `full_name` | string | — | — | — |
| user_profiles | `id` | binary_id | no | — | — |
| user_profiles | `inserted_at` | naive_datetime | — | — | — |
| user_profiles | `profile_photo_url` | string | — | — | — |
| user_profiles | `slack_user_id` | string | — | — | — |
| user_profiles | `updated_at` | naive_datetime | — | — | — |
| user_settings | `id` | binary_id | no | — | — |
| user_settings | `inserted_at` | naive_datetime | — | — | — |
| user_settings | `updated_at` | naive_datetime | — | — | — |
| users | `id` | id | no | — | — |
| users | `inserted_at` | naive_datetime | — | — | — |
| users | `updated_at` | naive_datetime | — | — | — |
| widget_settings | `id` | binary_id | no | — | — |
| widget_settings | `inserted_at` | naive_datetime | — | — | — |
| widget_settings | `updated_at` | naive_datetime | — | — | — |

## Embedded schemas

_Not database tables:_

- 3 `embedded_schema` module(s) — no DB table, not rendered as entities.

## Residuals

- An Ecto schema cannot express `null` or indexes — NOT-NULL constraints and DB indexes are declared in `priv/repo/migrations/`, not the schema. So `null` is `—` for every non-primary-key column, including a `belongs_to` FK column, whose required-ness is a migration constraint.
- A non-bare `timestamps(...)` call was not expanded to inserted_at/updated_at (a named residual).
