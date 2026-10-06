# API surface

_Static parse of `config/routes.rb`; the DSL is not executed._

## Routes (42)

| method | path | controller |
|---|---|---|
| GET | `/*full_name` | `repos#show` |
| PATCH | `/*full_name` | `repos#update` |
| GET | `/*full_name/badges/:badge_type(.:format)` | `badges#show` |
| GET | `/*full_name/edit` | `repos#edit` |
| GET | `/*full_name/info(.:format)` | `api_info#show` |
| GET | `/*full_name/subscribers` | `subscribers#show` |
| GET | `/doc_methods/:id` | `doc_methods#show` |
| GET | `/doc_methods/:id/users/:user_id/click` | `doc_methods#click_method_redirect` |
| GET | `/doc_methods/:id/users/:user_id/source_click` | `doc_methods#click_source_redirect` |
| GET | `/example_app` | `university#show` |
| POST | `/issue_assignments` | `issue_assignments#create` |
| GET | `/issue_assignments/:id/users/:user_id/click` | `issue_assignments#click_issue_redirect` |
| GET | `/privacy` | `pages#privacy` |
| GET | `/rebase` | `university#show` |
| POST | `/repo_subscriptions` | `repo_subscriptions#create` |
| DELETE | `/repo_subscriptions/:id` | `repo_subscriptions#destroy` |
| PATCH | `/repo_subscriptions/:id` | `repo_subscriptions#update` |
| PUT | `/repo_subscriptions/:id` | `repo_subscriptions#update` |
| GET | `/repos` | `repos#index` |
| POST | `/repos` | `repos#create` |
| GET | `/repos/list` | `repos#list` |
| GET | `/repos/new` | `repos#new` |
| GET | `/repro` | `university#show` |
| GET | `/reproduction` | `university#show` |
| GET | `/squash` | `university#show` |
| GET | `/support` | `pages#support` |
| GET | `/topics/:id` | `topics#show` |
| GET | `/university` | `university#index` |
| GET | `/university/:id` | `university#show` |
| PATCH | `/users` | `users#update` |
| DELETE | `/users/:id` | `users#destroy` |
| GET | `/users/:id` | `users#show` |
| PATCH | `/users/:id` | `users#update` |
| PUT | `/users/:id` | `users#update` |
| GET | `/users/:id/edit` | `users#edit` |
| GET | `/users/after_signup/:id` | `users/after_signup#show` |
| PATCH | `/users/after_signup/:id` | `users/after_signup#update` |
| PUT | `/users/after_signup/:id` | `users/after_signup#update` |
| GET | `/users/edit` | `users#edit` |
| DELETE | `/users/unsubscribe/:account_delete_token` | `users#token_destroy` |
| GET | `/users/unsubscribe/:account_delete_token` | `users#token_delete` |
| GET | `/what` | `pages#what` |

## Residuals

- Under-counted, and the residual is now a SHORT list because the reader is a tree-sitter parse rather than a line scan: `root`, `mount`ed engines, route `concern`s, `shallow:` nesting, `direct`/`resolve` helpers and metaprogrammed `draw`s are declarations this rung does not expand. Every one of them is already expanded in `arch-inputs/routes.txt` — the committed output of `bin/rails routes --expanded`, which is the rung above this one and the input that closes them.
- A `root` declaration was found and not expanded (a named residual).
- A `mount`ed Rack application or engine contributes the whole route table of another program and was not expanded (a named residual).
- A verb declaration with no `to:` handler this reader could resolve to a static `controller#action`, and no enclosing resource to infer one from, was not rendered (a named residual).
