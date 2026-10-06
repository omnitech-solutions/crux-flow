# API surface

_Static route scan (Express/Fastify verb calls + NestJS decorators, labeling GraphQL resolvers as a residual rather than reading them); the app is not executed._

_Declared route frameworks: `express` (labels only; the scan is receiver-agnostic)._

## Routes (14)

| method | path | handler |
|---|---|---|
| DELETE | `/v1/users/:userId` | userController.deleteUser |
| GET | `/v1/docs` | — |
| GET | `/v1/users` | userController.getUsers |
| GET | `/v1/users/:userId` | userController.getUser |
| PATCH | `/v1/users/:userId` | userController.updateUser |
| POST | `/v1/auth/forgot-password` | authController.forgotPassword |
| POST | `/v1/auth/login` | authController.login |
| POST | `/v1/auth/logout` | authController.logout |
| POST | `/v1/auth/refresh-tokens` | authController.refreshTokens |
| POST | `/v1/auth/register` | authController.register |
| POST | `/v1/auth/reset-password` | authController.resetPassword |
| POST | `/v1/auth/send-verification-email` | authController.sendVerificationEmail |
| POST | `/v1/auth/verify-email` | authController.verifyEmail |
| POST | `/v1/users` | userController.createUser |
