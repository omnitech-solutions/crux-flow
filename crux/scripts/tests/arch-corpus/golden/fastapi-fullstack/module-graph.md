# Module graph

_Intra-package imports of `app` under `backend/app/` (25 modules, 36 edges)._

```mermaid
graph LR
  alembic_env[alembic.env] --> core_config[core.config]
  alembic_env[alembic.env] --> models[models]
  api_deps[api.deps] --> core[core]
  api_deps[api.deps] --> core_config[core.config]
  api_deps[api.deps] --> core_db[core.db]
  api_deps[api.deps] --> models[models]
  api_main[api.main] --> api_routes[api.routes]
  api_main[api.main] --> core_config[core.config]
  api_routes_items[api.routes.items] --> api_deps[api.deps]
  api_routes_items[api.routes.items] --> models[models]
  api_routes_login[api.routes.login] --> api_deps[api.deps]
  api_routes_login[api.routes.login] --> core[core]
  api_routes_login[api.routes.login] --> core_config[core.config]
  api_routes_login[api.routes.login] --> models[models]
  api_routes_login[api.routes.login] --> utils[utils]
  api_routes_private[api.routes.private] --> api_deps[api.deps]
  api_routes_private[api.routes.private] --> core_security[core.security]
  api_routes_private[api.routes.private] --> models[models]
  api_routes_users[api.routes.users] --> api_deps[api.deps]
  api_routes_users[api.routes.users] --> core_config[core.config]
  api_routes_users[api.routes.users] --> core_security[core.security]
  api_routes_users[api.routes.users] --> models[models]
  api_routes_users[api.routes.users] --> utils[utils]
  api_routes_utils[api.routes.utils] --> api_deps[api.deps]
  api_routes_utils[api.routes.utils] --> models[models]
  api_routes_utils[api.routes.utils] --> utils[utils]
  core_db[core.db] --> core_config[core.config]
  core_db[core.db] --> models[models]
  core_security[core.security] --> core_config[core.config]
  crud[crud] --> core_security[core.security]
  crud[crud] --> models[models]
  initial_data[initial_data] --> core_db[core.db]
  main[main] --> api_main[api.main]
  main[main] --> core_config[core.config]
  utils[utils] --> core[core]
  utils[utils] --> core_config[core.config]
```

## Isolated modules (7)

_No intra-package import edge (leaf or standalone):_

`__init__`, `alembic.versions.1a31ce608336_add_cascade_delete_relationships`, `alembic.versions.9c0a54914c78_add_max_length_for_string_varchar_`, `alembic.versions.d98dd8ec85a3_edit_replace_id_integers_in_all_models_`, `alembic.versions.e2412789c190_initialize_models`, `alembic.versions.fe56fa70289e_add_created_at_to_user_and_item`, `api`
