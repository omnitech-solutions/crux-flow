# Data model

_Derived from `db/schema.rb` (ActiveRecord's committed schema dump)._

## Entities (12 tables)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| data_dumps | `created_at` | datetime | — | — | — |
| data_dumps | `data` | text | — | — | — |
| data_dumps | `updated_at` | datetime | — | — | — |
| doc_assignments | `clicked` | boolean | — | false | — |
| doc_assignments | `created_at` | datetime | — | — | — |
| doc_assignments | `doc_class_id` | bigint | — | — | — |
| doc_assignments | `doc_method_id` | bigint | — | — | — |
| doc_assignments | `repo_id` | bigint | — | — | — |
| doc_assignments | `repo_subscription_id` | bigint | — | — | — |
| doc_assignments | `updated_at` | datetime | — | — | — |
| doc_classes | `created_at` | datetime | no | — | — |
| doc_classes | `doc_comments_count` | integer | no | 0 | — |
| doc_classes | `file` | string | — | — | — |
| doc_classes | `line` | integer | — | — | — |
| doc_classes | `name` | string | — | — | — |
| doc_classes | `path` | string | — | — | — |
| doc_classes | `repo_id` | bigint | — | — | — |
| doc_classes | `updated_at` | datetime | no | — | — |
| doc_comments | `comment` | text | — | — | — |
| doc_comments | `created_at` | datetime | — | — | — |
| doc_comments | `doc_class_id` | bigint | — | — | — |
| doc_comments | `doc_method_id` | bigint | — | — | — |
| doc_comments | `updated_at` | datetime | — | — | — |
| doc_methods | `active` | boolean | — | true | — |
| doc_methods | `comment` | text | — | — | — |
| doc_methods | `created_at` | datetime | no | — | — |
| doc_methods | `doc_comments_count` | integer | no | 0 | — |
| doc_methods | `file` | string | — | — | — |
| doc_methods | `has_comment` | boolean | — | — | — |
| doc_methods | `line` | integer | — | — | — |
| doc_methods | `name` | string | — | — | — |
| doc_methods | `path` | string | — | — | — |
| doc_methods | `repo_id` | bigint | — | — | — |
| doc_methods | `skip_read` | boolean | — | false | — |
| doc_methods | `skip_write` | boolean | — | false | — |
| doc_methods | `updated_at` | datetime | no | — | — |
| issue_assignments | `clicked` | boolean | — | false | — |
| issue_assignments | `created_at` | datetime | no | — | — |
| issue_assignments | `delivered` | boolean | — | false | — |
| issue_assignments | `issue_id` | bigint | — | — | — |
| issue_assignments | `repo_subscription_id` | bigint | — | — | — |
| issue_assignments | `updated_at` | datetime | no | — | — |
| issues | `comment_count` | integer | — | — | — |
| issues | `created_at` | datetime | no | — | — |
| issues | `html_url` | string | — | — | — |
| issues | `last_touched_at` | datetime | — | — | — |
| issues | `number` | integer | — | — | — |
| issues | `pr_attached` | boolean | — | false | — |
| issues | `repo_id` | bigint | — | — | — |
| issues | `repo_name` | string | — | — | — |
| issues | `state` | string | — | — | — |
| issues | `title` | text | — | — | — |
| issues | `updated_at` | datetime | no | — | — |
| issues | `url` | string | — | — | — |
| issues | `user_name` | string | — | — | — |
| labels | `created_at` | datetime | no | — | — |
| labels | `name` | string | no | — | — |
| labels | `updated_at` | datetime | no | — | — |
| repo_labels | `created_at` | datetime | no | — | — |
| repo_labels | `label_id` | bigint | no | — | labels |
| repo_labels | `repo_id` | bigint | no | — | repos |
| repo_labels | `updated_at` | datetime | no | — | — |
| repo_subscriptions | `created_at` | datetime | no | — | — |
| repo_subscriptions | `email_limit` | integer | — | 1 | — |
| repo_subscriptions | `last_sent_at` | datetime | — | — | — |
| repo_subscriptions | `read` | boolean | — | false | — |
| repo_subscriptions | `read_limit` | integer | — | — | — |
| repo_subscriptions | `repo_id` | bigint | — | — | repos |
| repo_subscriptions | `updated_at` | datetime | no | — | — |
| repo_subscriptions | `user_id` | bigint | — | — | users |
| repo_subscriptions | `write` | boolean | — | false | — |
| repo_subscriptions | `write_limit` | integer | — | — | — |
| repos | `archived` | boolean | — | false | — |
| repos | `commit_sha` | string | — | — | — |
| repos | `created_at` | datetime | no | — | — |
| repos | `description` | string | — | — | — |
| repos | `docs_subscriber_count` | integer | — | 0 | — |
| repos | `full_name` | string | — | — | — |
| repos | `github_error_msg` | text | — | — | — |
| repos | `issues_count` | integer | no | 0 | — |
| repos | `language` | string | — | — | — |
| repos | `name` | string | — | — | — |
| repos | `notes` | text | — | — | — |
| repos | `removed_from_github` | boolean | — | false | — |
| repos | `stars_count` | integer | — | 0 | — |
| repos | `subscribers_count` | integer | — | 0 | — |
| repos | `updated_at` | datetime | no | — | — |
| repos | `user_name` | string | — | — | — |
| users | `account_delete_token` | string | — | — | — |
| users | `admin` | boolean | — | — | — |
| users | `avatar_url` | string | — | http://gravatar.com/avatar/default | — |
| users | `created_at` | datetime | no | — | — |
| users | `current_sign_in_at` | datetime | — | — | — |
| users | `current_sign_in_ip` | string | — | — | — |
| users | `daily_issue_limit` | integer | — | 50 | — |
| users | `email` | string | no |  | — |
| users | `email_frequency` | string | — | daily | — |
| users | `email_time_of_day` | time | — | — | — |
| users | `encrypted_password` | string | no |  | — |
| users | `favorite_languages` | string | — | — | — |
| users | `github` | string | — | — | — |
| users | `github_access_token` | string | — | — | — |
| users | `htos_contributor_bought` | boolean | no | false | — |
| users | `htos_contributor_unsubscribe` | boolean | no | false | — |
| users | `last_clicked_at` | datetime | — | — | — |
| users | `last_email_at` | datetime | — | — | — |
| users | `last_sign_in_at` | datetime | — | — | — |
| users | `last_sign_in_ip` | string | — | — | — |
| users | `name` | string | — | — | — |
| users | `old_token` | string | — | — | — |
| users | `phone_number` | string | — | — | — |
| users | `private` | boolean | — | false | — |
| users | `raw_emails_since_click` | integer | — | 0 | — |
| users | `raw_streak_count` | integer | — | 0 | — |
| users | `remember_created_at` | datetime | — | — | — |
| users | `reset_password_sent_at` | datetime | — | — | — |
| users | `reset_password_token` | string | — | — | — |
| users | `sign_in_count` | integer | — | 0 | — |
| users | `skip_issues_with_pr` | boolean | — | false | — |
| users | `twitter` | boolean | — | — | — |
| users | `updated_at` | datetime | no | — | — |
| users | `zip` | string | — | — | — |

## Indexes

- `doc_assignments`: `index_doc_assignments_on_repo_id` (repo_id); `index_doc_assignments_on_repo_subscription_id_and_doc_method_id` (repo_subscription_id, doc_method_id)
- `doc_classes`: `index_doc_classes_on_repo_id_and_doc_comments_count` (repo_id, doc_comments_count)
- `doc_comments`: `index_doc_comments_on_doc_class_id` (doc_class_id); `index_doc_comments_on_doc_method_id` (doc_method_id)
- `doc_methods`: `index_doc_methods_on_repo_id_and_doc_comments_count` (repo_id, doc_comments_count); `index_doc_methods_on_repo_id_and_id` (repo_id, id); `index_doc_methods_on_repo_id_and_name_and_path` (repo_id, name, path, unique)
- `issue_assignments`: `index_issue_assignments_on_repo_subscription_id_and_delivered` (repo_subscription_id, delivered)
- `issues`: `index_issues_on_number_and_repo_id` (number, repo_id, unique); `index_issues_on_repo_id_and_id` (repo_id, id); `index_issues_on_repo_id_and_number` (repo_id, number); `index_issues_on_repo_id_and_state` (repo_id, state); `index_issues_on_updated_at` (updated_at)
- `repo_labels`: `index_repo_labels_on_label_id` (label_id); `index_repo_labels_on_repo_id_and_label_id` (repo_id, label_id, unique); `index_repo_labels_on_repo_id` (repo_id)
- `repo_subscriptions`: `index_repo_subscriptions_on_read` (read); `index_repo_subscriptions_on_repo_id_and_user_id` (repo_id, user_id); `index_repo_subscriptions_on_repo_id` (repo_id); `index_repo_subscriptions_on_user_id_and_last_sent_at` (user_id, last_sent_at); `index_repo_subscriptions_on_write` (write)
- `repos`: `index_repos_on_archived` (archived); `index_repos_on_full_name` (full_name); `index_repos_on_issues_count` (issues_count); `index_repos_on_language` (language); `index_repos_on_name_and_user_name` (name, user_name, unique); `index_repos_on_subscribers_count` (subscribers_count); `index_repos_on_user_name` (user_name)
- `users`: `index_users_on_account_delete_token` (account_delete_token); `index_users_on_email` (email, unique); `index_users_on_github` (github, unique); `index_users_on_github_access_token` (github_access_token); `index_users_on_private_and_id_and_created_at` (private, id, created_at); `index_users_on_reset_password_token` (reset_password_token, unique)

## Residuals

- An `add_foreign_key` without an explicit `column:` infers the referencing column as `<referenced_singular>_id`, which mis-attributes a non-conventional FK column (a named residual).
