# API surface

_Static route scan (Express/Fastify verb calls + NestJS decorators, labeling GraphQL resolvers as a residual rather than reading them); the app is not executed._

_Declared route frameworks: `@nestjs/common`, `@nestjs/core` (labels only; the scan is receiver-agnostic)._

## Routes (2)

| method | path | handler |
|---|---|---|
| GET | `/` | getHello |
| GET | `/hello/:name` | getHelloName |

## Residuals

- A GraphQL surface is present but not read by this scan (a named residual) -- schema files `graphql/auth.graphql`, `graphql/post.graphql`, `graphql/user.graphql`, `src/schema.graphql`; resolver decorators in `src/app.resolver.ts`, `src/auth/auth.resolver.ts`, `src/posts/posts.resolver.ts`, `src/users/users.resolver.ts`. api-surface covers REST route declarations only.
