# Data model

_Derived from SQLModel tables and the Alembic migration timeline._

## SQLModel tables (2)

| table | column | type | nullable | pk | fk |
|---|---|---|---|---|---|
| item | `created_at` | datetime \| None | yes | no | — |
| item | `description` | str \| None | yes | no | — |
| item | `id` | uuid.UUID | no | yes | — |
| item | `owner_id` | uuid.UUID | no | no | user.id |
| item | `title` | str | no | no | — |
| user | `created_at` | datetime \| None | yes | no | — |
| user | `email` | EmailStr | no | no | — |
| user | `full_name` | str \| None | yes | no | — |
| user | `hashed_password` | str | no | no | — |
| user | `id` | uuid.UUID | no | yes | — |
| user | `is_active` | bool | no | no | — |
| user | `is_superuser` | bool | no | no | — |

## Alembic migration timeline (5)

_Canonical topological order; the revision id breaks ties at equal depth._

| # | revision | down_revision | message |
|---|---|---|---|
| 1 | `e2412789c190` | (base) | Initialize models |
| 2 | `9c0a54914c78` | `e2412789c190` | Add max length for string(varchar) fields in User and Items models |
| 3 | `d98dd8ec85a3` | `9c0a54914c78` | Edit replace id integers in all models to use UUID instead |
| 4 | `1a31ce608336` | `d98dd8ec85a3` | Add cascade delete relationships |
| 5 | `fe56fa70289e` | `1a31ce608336` | Add created_at to User and Item |

## Residuals

- A SQLModel `Relationship()` attribute is an ORM-level link rather than a column, so it is not a row here. The foreign-key column it travels with is.
- Field inheritance is resolved WITHIN one module. A model whose base class is imported from another module renders only the fields it declares itself, because resolving the base would mean deciding which of several same-named classes across the repository the import meant.
