# Data model

_Derived from `prisma/schema.prisma` (Prisma schema; static parse, no Prisma executed)._

## Entities (2 models)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| Post | `authorId` | String | yes | — | User |
| Post | `content` | String | yes | — | — |
| Post | `createdAt` | DateTime | no | now() | — |
| Post | `id` | String | no | cuid() | — |
| Post | `published` | Boolean | no | — | — |
| Post | `title` | String | no | — | — |
| Post | `updatedAt` | DateTime | no | — | — |
| User | `createdAt` | DateTime | no | now() | — |
| User | `email` | String | no | — | — |
| User | `firstname` | String | yes | — | — |
| User | `id` | String | no | cuid() | — |
| User | `lastname` | String | yes | — | — |
| User | `password` | String | no | — | — |
| User | `role` | Role | no | — | — |
| User | `updatedAt` | DateTime | no | — | — |

## Indexes

- `Post`: `id` primary key
- `User`: `id` primary key; `email` unique

## Relations

_Model-typed fields (not columns):_

- `Post`: `author` → `User`
- `User`: `posts` → `Post[]`

## Enums

- `Role`: `ADMIN`, `USER`
