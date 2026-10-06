# Data model

_Derived from Ecto schemas (static parse; no Elixir executed)._

## Entities (4 tables)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| genres | `id` | id | no | — | — |
| genres | `slug` | string | — | — | — |
| genres | `title` | string | — | — | — |
| identities | `id` | id | no | — | — |
| identities | `inserted_at` | naive_datetime | — | — | — |
| identities | `provider` | string | — | — | — |
| identities | `provider_email` | string | — | — | — |
| identities | `provider_id` | string | — | — | — |
| identities | `provider_login` | string | — | — | — |
| identities | `provider_meta` | map | — | — | — |
| identities | `provider_name` | string | — | — | — |
| identities | `provider_token` | string | — | — | — |
| identities | `updated_at` | naive_datetime | — | — | — |
| identities | `user_id` | id | — | — | User |
| songs | `album_artist` | string | — | — | — |
| songs | `artist` | string | — | — | — |
| songs | `attribution` | string | — | — | — |
| songs | `date_recorded` | naive_datetime | — | — | — |
| songs | `date_released` | naive_datetime | — | — | — |
| songs | `duration` | integer | — | — | — |
| songs | `genre_id` | id | — | — | LiveBeats.MediaLibrary.Genre |
| songs | `id` | id | no | — | — |
| songs | `inserted_at` | naive_datetime | — | — | — |
| songs | `mp3_filename` | string | — | — | — |
| songs | `mp3_filepath` | string | — | — | — |
| songs | `mp3_filesize` | integer | — | 0 | — |
| songs | `mp3_url` | string | — | — | — |
| songs | `paused_at` | utc_datetime | — | — | — |
| songs | `played_at` | utc_datetime | — | — | — |
| songs | `position` | integer | — | 0 | — |
| songs | `server_ip` | string | — | — | — |
| songs | `ss` | integer | — | — | — |
| songs | `status` | Ecto.Enum | — | stopped | — |
| songs | `text` | string | — | — | — |
| songs | `title` | string | — | — | — |
| songs | `updated_at` | naive_datetime | — | — | — |
| songs | `user_id` | id | — | — | Accounts.User |
| users | `active_profile_user_id` | id | — | — | — |
| users | `avatar_url` | string | — | — | — |
| users | `confirmed_at` | naive_datetime | — | — | — |
| users | `email` | string | — | — | — |
| users | `external_homepage_url` | string | — | — | — |
| users | `id` | id | no | — | — |
| users | `inserted_at` | naive_datetime | — | — | — |
| users | `name` | string | — | — | — |
| users | `profile_tagline` | string | — | — | — |
| users | `role` | string | — | subscriber | — |
| users | `songs_count` | integer | — | — | — |
| users | `updated_at` | naive_datetime | — | — | — |
| users | `username` | string | — | — | — |

## Relations

_Associations (not columns):_

- `users`: `identities` → `Identity` (has_many)

## Enums

_`Ecto.Enum` columns:_

- `songs`.`status`: `stopped`, `playing`, `paused`

## Embedded schemas

_Not database tables:_

- `songs`.`segments` → `Segment` (embeds_many)
- `songs`.`transcript` → `Transcript` (embeds_one)

## Residuals

- An Ecto schema cannot express `null` or indexes — NOT-NULL constraints and DB indexes are declared in `priv/repo/migrations/`, not the schema. So `null` is `—` for every non-primary-key column, including a `belongs_to` FK column, whose required-ness is a migration constraint.
