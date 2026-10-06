# Data model

_Derived from Mongoose `Schema`/`.model` definitions (parse-tree read, no Node executed)._

## Entities (13 fields across 5 models)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| Project | `name` | String | no | — | — |
| Task | `name` | String | no | — | — |
| Task | `project` | ObjectId | no | — | Project |
| Token | `blacklisted` | Boolean | yes | false | — |
| Token | `expires` | Date | no | — | — |
| Token | `token` | String | no | — | — |
| Token | `type` | String | no | — | — |
| Token | `user` | ObjectId | no | — | User |
| User | `email` | String | no | — | — |
| User | `isEmailVerified` | Boolean | yes | false | — |
| User | `name` | String | no | — | — |
| User | `password` | String | no | — | — |
| User | `role` | String | yes | user | — |

## Residuals

- Model name not resolved via `mongoose.model(...)` — the schema identifier rendered instead (a named residual): `schema`.
