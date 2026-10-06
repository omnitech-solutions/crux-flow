# API surface

_Static route scan (Express/Fastify verb calls + NestJS decorators, labeling GraphQL resolvers as a residual rather than reading them); the app is not executed._

## Routes (1)

| method | path | handler |
|---|---|---|
| GET | `/` | — |

## Residuals

- 5 route declarations dropped: the path is composed at runtime and has no static value, and the application is not executed, so no path is rendered for them (a named residual) — `express-main-example/express/app.js` lines 41, 47, 53, 59, 65.
