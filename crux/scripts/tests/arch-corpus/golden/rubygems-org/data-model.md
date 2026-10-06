# Data model

_Derived from `db/schema.rb` (ActiveRecord's committed schema dump)._

## Entities (56 tables)

| table | column | type | null | default | fk |
|---|---|---|---|---|---|
| admin_github_users | `avatar_url` | string | — | — | — |
| admin_github_users | `created_at` | datetime | no | — | — |
| admin_github_users | `github_id` | string | — | — | — |
| admin_github_users | `info_data` | json | — | — | — |
| admin_github_users | `is_admin` | boolean | — | — | — |
| admin_github_users | `login` | string | — | — | — |
| admin_github_users | `oauth_token` | string | — | — | — |
| admin_github_users | `updated_at` | datetime | no | — | — |
| api_key_rubygem_scopes | `api_key_id` | bigint | no | — | api_keys |
| api_key_rubygem_scopes | `created_at` | datetime | no | — | — |
| api_key_rubygem_scopes | `ownership_id` | bigint | no | — | — |
| api_key_rubygem_scopes | `updated_at` | datetime | no | — | — |
| api_keys | `created_at` | datetime | no | — | — |
| api_keys | `expires_at` | datetime | — | — | — |
| api_keys | `hashed_key` | string | no | — | — |
| api_keys | `last_accessed_at` | datetime | — | — | — |
| api_keys | `mfa` | boolean | no | false | — |
| api_keys | `name` | string | no | — | — |
| api_keys | `owner_id` | bigint | — | — | — |
| api_keys | `owner_id IS NOT NULL` | check_constraint | — | — | — |
| api_keys | `owner_type` | string | — | — | — |
| api_keys | `owner_type IS NOT NULL` | check_constraint | — | — | — |
| api_keys | `scopes` | string | — | — | — |
| api_keys | `scopes IS NOT NULL` | check_constraint | — | — | — |
| api_keys | `soft_deleted_at` | datetime | — | — | — |
| api_keys | `soft_deleted_rubygem_name` | string | — | — | — |
| api_keys | `updated_at` | datetime | no | — | — |
| attestations | `body` | jsonb | — | — | — |
| attestations | `created_at` | datetime | no | — | — |
| attestations | `media_type` | string | — | — | — |
| attestations | `updated_at` | datetime | no | — | — |
| attestations | `version_id` | bigint | no | — | versions |
| audits | `action` | string | no | — | — |
| audits | `admin_github_user_id` | bigint | no | — | admin_github_users |
| audits | `auditable_id` | bigint | no | — | — |
| audits | `auditable_type` | string | no | — | — |
| audits | `audited_changes` | text | — | — | — |
| audits | `comment` | string | — | — | — |
| audits | `created_at` | datetime | no | — | — |
| audits | `updated_at` | datetime | no | — | — |
| blazer_audits | `created_at` | datetime | — | — | — |
| blazer_audits | `data_source` | string | — | — | — |
| blazer_audits | `query_id` | bigint | — | — | — |
| blazer_audits | `statement` | text | — | — | — |
| blazer_audits | `user_id` | bigint | — | — | — |
| blazer_checks | `check_type` | string | — | — | — |
| blazer_checks | `created_at` | datetime | no | — | — |
| blazer_checks | `creator_id` | bigint | — | — | — |
| blazer_checks | `emails` | text | — | — | — |
| blazer_checks | `last_run_at` | datetime | — | — | — |
| blazer_checks | `message` | text | — | — | — |
| blazer_checks | `query_id` | bigint | — | — | — |
| blazer_checks | `schedule` | string | — | — | — |
| blazer_checks | `slack_channels` | text | — | — | — |
| blazer_checks | `state` | string | — | — | — |
| blazer_checks | `updated_at` | datetime | no | — | — |
| blazer_dashboard_queries | `created_at` | datetime | no | — | — |
| blazer_dashboard_queries | `dashboard_id` | bigint | — | — | — |
| blazer_dashboard_queries | `position` | integer | — | — | — |
| blazer_dashboard_queries | `query_id` | bigint | — | — | — |
| blazer_dashboard_queries | `updated_at` | datetime | no | — | — |
| blazer_dashboards | `created_at` | datetime | no | — | — |
| blazer_dashboards | `creator_id` | bigint | — | — | — |
| blazer_dashboards | `name` | string | — | — | — |
| blazer_dashboards | `updated_at` | datetime | no | — | — |
| blazer_queries | `created_at` | datetime | no | — | — |
| blazer_queries | `creator_id` | bigint | — | — | — |
| blazer_queries | `data_source` | string | — | — | — |
| blazer_queries | `description` | text | — | — | — |
| blazer_queries | `name` | string | — | — | — |
| blazer_queries | `statement` | text | — | — | — |
| blazer_queries | `status` | string | — | — | — |
| blazer_queries | `updated_at` | datetime | no | — | — |
| blocked_email_domains | `created_at` | datetime | no | — | — |
| blocked_email_domains | `domain` | string | no | — | — |
| blocked_email_domains | `notes` | text | — | — | — |
| blocked_email_domains | `source` | integer | no | 0 | — |
| blocked_email_domains | `updated_at` | datetime | no | — | — |
| deletions | `created_at` | datetime | no | — | — |
| deletions | `number` | string | — | — | — |
| deletions | `platform` | string | — | — | — |
| deletions | `ruby_abi` | string | — | — | — |
| deletions | `rubygem` | string | — | — | — |
| deletions | `updated_at` | datetime | no | — | — |
| deletions | `user_id` | integer | — | — | — |
| deletions | `version_id` | bigint | — | — | — |
| dependencies | `created_at` | datetime | — | — | — |
| dependencies | `requirements` | string | — | — | — |
| dependencies | `rubygem_id` | integer | — | — | — |
| dependencies | `scope` | string | — | — | — |
| dependencies | `unresolved_name` | string | — | — | — |
| dependencies | `updated_at` | datetime | — | — | — |
| dependencies | `version_id` | integer | — | — | — |
| email_domain_allowlists | `created_at` | datetime | no | — | — |
| email_domain_allowlists | `domain` | string | no | — | — |
| email_domain_allowlists | `notes` | text | — | — | — |
| email_domain_allowlists | `updated_at` | datetime | no | — | — |
| events_organization_events | `additional` | jsonb | — | — | — |
| events_organization_events | `created_at` | datetime | no | — | — |
| events_organization_events | `geoip_info_id` | bigint | — | — | geoip_infos |
| events_organization_events | `ip_address_id` | bigint | — | — | — |
| events_organization_events | `organization_id` | bigint | no | — | organizations |
| events_organization_events | `tag` | string | no | — | — |
| events_organization_events | `trace_id` | string | — | — | — |
| events_organization_events | `updated_at` | datetime | no | — | — |
| events_rubygem_events | `additional` | jsonb | — | — | — |
| events_rubygem_events | `created_at` | datetime | no | — | — |
| events_rubygem_events | `geoip_info_id` | bigint | — | — | geoip_infos |
| events_rubygem_events | `ip_address_id` | bigint | — | — | — |
| events_rubygem_events | `rubygem_id` | bigint | no | — | rubygems |
| events_rubygem_events | `tag` | string | no | — | — |
| events_rubygem_events | `trace_id` | string | — | — | — |
| events_rubygem_events | `updated_at` | datetime | no | — | — |
| events_user_events | `additional` | jsonb | — | — | — |
| events_user_events | `created_at` | datetime | no | — | — |
| events_user_events | `geoip_info_id` | bigint | — | — | geoip_infos |
| events_user_events | `ip_address_id` | bigint | — | — | — |
| events_user_events | `tag` | string | no | — | — |
| events_user_events | `trace_id` | string | — | — | — |
| events_user_events | `updated_at` | datetime | no | — | — |
| events_user_events | `user_id` | bigint | no | — | users |
| flipper_features | `created_at` | datetime | no | — | — |
| flipper_features | `key` | string | no | — | — |
| flipper_features | `updated_at` | datetime | no | — | — |
| flipper_gates | `created_at` | datetime | no | — | — |
| flipper_gates | `feature_key` | string | no | — | — |
| flipper_gates | `key` | string | no | — | — |
| flipper_gates | `updated_at` | datetime | no | — | — |
| flipper_gates | `value` | text | — | — | — |
| gem_downloads | `count` | bigint | — | — | — |
| gem_downloads | `rubygem_id` | integer | no | — | — |
| gem_downloads | `version_id` | integer | no | — | — |
| gem_name_reservations | `created_at` | datetime | no | — | — |
| gem_name_reservations | `name` | string | no | — | — |
| gem_name_reservations | `updated_at` | datetime | no | — | — |
| gem_typo_exceptions | `created_at` | datetime | no | — | — |
| gem_typo_exceptions | `info` | text | — | — | — |
| gem_typo_exceptions | `name` | string | — | — | — |
| gem_typo_exceptions | `updated_at` | datetime | no | — | — |
| geoip_infos | `city` | string | — | — | — |
| geoip_infos | `continent_code` | string | — | — | — |
| geoip_infos | `country_code` | string | — | — | — |
| geoip_infos | `country_code3` | string | — | — | — |
| geoip_infos | `country_name` | string | — | — | — |
| geoip_infos | `created_at` | datetime | no | — | — |
| geoip_infos | `region` | string | — | — | — |
| geoip_infos | `updated_at` | datetime | no | — | — |
| good_job_batches | `callback_priority` | integer | — | — | — |
| good_job_batches | `callback_queue_name` | text | — | — | — |
| good_job_batches | `created_at` | datetime | no | — | — |
| good_job_batches | `description` | text | — | — | — |
| good_job_batches | `discarded_at` | datetime | — | — | — |
| good_job_batches | `enqueued_at` | datetime | — | — | — |
| good_job_batches | `finished_at` | datetime | — | — | — |
| good_job_batches | `on_discard` | text | — | — | — |
| good_job_batches | `on_finish` | text | — | — | — |
| good_job_batches | `on_success` | text | — | — | — |
| good_job_batches | `serialized_properties` | jsonb | — | — | — |
| good_job_batches | `updated_at` | datetime | no | — | — |
| good_job_executions | `active_job_id` | uuid | no | — | — |
| good_job_executions | `created_at` | datetime | no | — | — |
| good_job_executions | `duration` | interval | — | — | — |
| good_job_executions | `error` | text | — | — | — |
| good_job_executions | `error_backtrace` | text | — | — | — |
| good_job_executions | `error_event` | integer | — | — | — |
| good_job_executions | `finished_at` | datetime | — | — | — |
| good_job_executions | `job_class` | text | — | — | — |
| good_job_executions | `process_id` | uuid | — | — | — |
| good_job_executions | `queue_name` | text | — | — | — |
| good_job_executions | `scheduled_at` | datetime | — | — | — |
| good_job_executions | `serialized_params` | jsonb | — | — | — |
| good_job_executions | `updated_at` | datetime | no | — | — |
| good_job_processes | `created_at` | datetime | no | — | — |
| good_job_processes | `lock_type` | integer | — | — | — |
| good_job_processes | `state` | jsonb | — | — | — |
| good_job_processes | `updated_at` | datetime | no | — | — |
| good_job_settings | `created_at` | datetime | no | — | — |
| good_job_settings | `key` | text | — | — | — |
| good_job_settings | `updated_at` | datetime | no | — | — |
| good_job_settings | `value` | jsonb | — | — | — |
| good_jobs | `active_job_id` | uuid | — | — | — |
| good_jobs | `batch_callback_id` | uuid | — | — | — |
| good_jobs | `batch_id` | uuid | — | — | — |
| good_jobs | `concurrency_key` | text | — | — | — |
| good_jobs | `created_at` | datetime | no | — | — |
| good_jobs | `cron_at` | datetime | — | — | — |
| good_jobs | `cron_key` | text | — | — | — |
| good_jobs | `error` | text | — | — | — |
| good_jobs | `error_event` | integer | — | — | — |
| good_jobs | `executions_count` | integer | — | — | — |
| good_jobs | `finished_at` | datetime | — | — | — |
| good_jobs | `is_discrete` | boolean | — | — | — |
| good_jobs | `job_class` | text | — | — | — |
| good_jobs | `labels` | text | — | — | — |
| good_jobs | `locked_at` | datetime | — | — | — |
| good_jobs | `locked_by_id` | uuid | — | — | — |
| good_jobs | `performed_at` | datetime | — | — | — |
| good_jobs | `priority` | integer | — | — | — |
| good_jobs | `queue_name` | text | — | — | — |
| good_jobs | `retried_good_job_id` | uuid | — | — | — |
| good_jobs | `scheduled_at` | datetime | — | — | — |
| good_jobs | `serialized_params` | jsonb | — | — | — |
| good_jobs | `updated_at` | datetime | no | — | — |
| ip_addresses | `created_at` | datetime | no | — | — |
| ip_addresses | `geoip_info_id` | bigint | — | — | geoip_infos |
| ip_addresses | `hashed_ip_address` | text | no | — | — |
| ip_addresses | `ip_address` | inet | no | — | — |
| ip_addresses | `updated_at` | datetime | no | — | — |
| link_verifications | `created_at` | datetime | no | — | — |
| link_verifications | `failures_since_last_verification` | integer | — | 0 | — |
| link_verifications | `last_failure_at` | datetime | — | — | — |
| link_verifications | `last_verified_at` | datetime | — | — | — |
| link_verifications | `linkable_id` | bigint | no | — | — |
| link_verifications | `linkable_type` | string | no | — | — |
| link_verifications | `updated_at` | datetime | no | — | — |
| link_verifications | `uri` | string | no | — | — |
| linksets | `bugs` | string | — | — | — |
| linksets | `code` | string | — | — | — |
| linksets | `created_at` | datetime | — | — | — |
| linksets | `docs` | string | — | — | — |
| linksets | `home` | string | — | — | — |
| linksets | `mail` | string | — | — | — |
| linksets | `rubygem_id` | integer | — | — | rubygems |
| linksets | `updated_at` | datetime | — | — | — |
| linksets | `wiki` | string | — | — | — |
| log_tickets | `backend` | integer | — | 0 | — |
| log_tickets | `created_at` | datetime | no | — | — |
| log_tickets | `directory` | string | — | — | — |
| log_tickets | `key` | string | — | — | — |
| log_tickets | `processed_count` | integer | — | — | — |
| log_tickets | `status` | string | — | — | — |
| log_tickets | `updated_at` | datetime | no | — | — |
| maintenance_tasks_runs | `arguments` | text | — | — | — |
| maintenance_tasks_runs | `backtrace` | text | — | — | — |
| maintenance_tasks_runs | `created_at` | datetime | no | — | — |
| maintenance_tasks_runs | `cursor` | string | — | — | — |
| maintenance_tasks_runs | `ended_at` | datetime | — | — | — |
| maintenance_tasks_runs | `error_class` | string | — | — | — |
| maintenance_tasks_runs | `error_message` | string | — | — | — |
| maintenance_tasks_runs | `job_id` | string | — | — | — |
| maintenance_tasks_runs | `lock_version` | integer | no | 0 | — |
| maintenance_tasks_runs | `metadata` | text | — | — | — |
| maintenance_tasks_runs | `started_at` | datetime | — | — | — |
| maintenance_tasks_runs | `status` | string | no | enqueued | — |
| maintenance_tasks_runs | `task_name` | string | no | — | — |
| maintenance_tasks_runs | `tick_count` | bigint | no | 0 | — |
| maintenance_tasks_runs | `tick_total` | bigint | — | — | — |
| maintenance_tasks_runs | `time_running` | float | no | 0.0 | — |
| maintenance_tasks_runs | `updated_at` | datetime | no | — | — |
| memberships | `confirmed_at` | datetime | — | — | — |
| memberships | `created_at` | datetime | no | — | — |
| memberships | `invitation_expires_at` | datetime | — | — | — |
| memberships | `invited_by_id` | bigint | — | — | — |
| memberships | `organization_id` | bigint | no | — | organizations |
| memberships | `role` | integer | no | 50 | — |
| memberships | `updated_at` | datetime | no | — | — |
| memberships | `user_id` | bigint | no | — | users |
| oidc_api_key_roles | `access_policy` | jsonb | no | — | — |
| oidc_api_key_roles | `api_key_permissions` | jsonb | no | — | — |
| oidc_api_key_roles | `created_at` | datetime | no | — | — |
| oidc_api_key_roles | `deleted_at` | datetime | — | — | — |
| oidc_api_key_roles | `name` | string | no | — | — |
| oidc_api_key_roles | `oidc_provider_id` | bigint | no | — | oidc_providers |
| oidc_api_key_roles | `token` | string | no | — | — |
| oidc_api_key_roles | `updated_at` | datetime | no | — | — |
| oidc_api_key_roles | `user_id` | bigint | no | — | users |
| oidc_id_tokens | `api_key_id` | bigint | — | — | api_keys |
| oidc_id_tokens | `created_at` | datetime | no | — | — |
| oidc_id_tokens | `jwt` | jsonb | no | — | — |
| oidc_id_tokens | `oidc_api_key_role_id` | bigint | no | — | oidc_api_key_roles |
| oidc_id_tokens | `updated_at` | datetime | no | — | — |
| oidc_pending_trusted_publishers | `created_at` | datetime | no | — | — |
| oidc_pending_trusted_publishers | `expires_at` | datetime | no | — | — |
| oidc_pending_trusted_publishers | `rubygem_name` | string | — | — | — |
| oidc_pending_trusted_publishers | `trusted_publisher_id` | bigint | no | — | — |
| oidc_pending_trusted_publishers | `trusted_publisher_type` | string | no | — | — |
| oidc_pending_trusted_publishers | `updated_at` | datetime | no | — | — |
| oidc_pending_trusted_publishers | `user_id` | bigint | no | — | users |
| oidc_providers | `configuration` | jsonb | — | — | — |
| oidc_providers | `configuration_updated_at` | datetime | — | — | — |
| oidc_providers | `created_at` | datetime | no | — | — |
| oidc_providers | `issuer` | text | — | — | — |
| oidc_providers | `jwks` | jsonb | — | — | — |
| oidc_providers | `updated_at` | datetime | no | — | — |
| oidc_rubygem_trusted_publishers | `created_at` | datetime | no | — | — |
| oidc_rubygem_trusted_publishers | `rubygem_id` | bigint | no | — | rubygems |
| oidc_rubygem_trusted_publishers | `trusted_publisher_id` | bigint | no | — | — |
| oidc_rubygem_trusted_publishers | `trusted_publisher_type` | string | no | — | — |
| oidc_rubygem_trusted_publishers | `updated_at` | datetime | no | — | — |
| oidc_trusted_publisher_github_actions | `created_at` | datetime | no | — | — |
| oidc_trusted_publisher_github_actions | `environment` | string | — | — | — |
| oidc_trusted_publisher_github_actions | `repository_name` | string | no | — | — |
| oidc_trusted_publisher_github_actions | `repository_owner` | string | no | — | — |
| oidc_trusted_publisher_github_actions | `repository_owner_id` | string | no | — | — |
| oidc_trusted_publisher_github_actions | `updated_at` | datetime | no | — | — |
| oidc_trusted_publisher_github_actions | `workflow_filename` | string | no | — | — |
| oidc_trusted_publisher_github_actions | `workflow_repository_name` | string | — | — | — |
| oidc_trusted_publisher_github_actions | `workflow_repository_owner` | string | — | — | — |
| organization_invites | `created_at` | datetime | no | — | — |
| organization_invites | `invitable_id` | bigint | no | — | — |
| organization_invites | `invitable_type` | string | no | — | — |
| organization_invites | `role` | string | — | — | — |
| organization_invites | `updated_at` | datetime | no | — | — |
| organization_invites | `user_id` | bigint | no | — | users |
| organization_onboarding_invites | `created_at` | datetime | no | — | — |
| organization_onboarding_invites | `organization_onboarding_id` | bigint | no | — | organization_onboardings |
| organization_onboarding_invites | `role` | string | — | — | — |
| organization_onboarding_invites | `updated_at` | datetime | no | — | — |
| organization_onboarding_invites | `user_id` | bigint | no | — | users |
| organization_onboardings | `created_at` | datetime | no | — | — |
| organization_onboardings | `created_by_id` | integer | no | — | — |
| organization_onboardings | `error` | text | — | — | — |
| organization_onboardings | `onboarded_at` | datetime | — | — | — |
| organization_onboardings | `onboarded_organization_id` | integer | — | — | — |
| organization_onboardings | `organization_handle` | string | no | — | — |
| organization_onboardings | `organization_name` | string | no | — | — |
| organization_onboardings | `rubygems` | integer | — | [] | — |
| organization_onboardings | `status` | string | no | — | — |
| organization_onboardings | `updated_at` | datetime | no | — | — |
| organizations | `created_at` | datetime | no | — | — |
| organizations | `deleted_at` | datetime | — | — | — |
| organizations | `handle` | string | — | — | — |
| organizations | `name` | string | — | — | — |
| organizations | `updated_at` | datetime | no | — | — |
| ownership_calls | `created_at` | datetime | no | — | — |
| ownership_calls | `note` | text | — | — | — |
| ownership_calls | `rubygem_id` | bigint | — | — | rubygems |
| ownership_calls | `status` | boolean | no | true | — |
| ownership_calls | `updated_at` | datetime | no | — | — |
| ownership_calls | `user_id` | bigint | — | — | users |
| ownership_requests | `approver_id` | integer | — | — | users |
| ownership_requests | `created_at` | datetime | no | — | — |
| ownership_requests | `note` | text | — | — | — |
| ownership_requests | `ownership_call_id` | bigint | — | — | ownership_calls |
| ownership_requests | `rubygem_id` | bigint | — | — | rubygems |
| ownership_requests | `status` | integer | no | 0 | — |
| ownership_requests | `updated_at` | datetime | no | — | — |
| ownership_requests | `user_id` | bigint | — | — | users |
| ownerships | `authorizer_id` | integer | — | — | — |
| ownerships | `confirmed_at` | datetime | — | — | — |
| ownerships | `created_at` | datetime | — | — | — |
| ownerships | `owner_notifier` | boolean | no | true | — |
| ownerships | `ownership_request_notifier` | boolean | no | true | — |
| ownerships | `push_notifier` | boolean | no | true | — |
| ownerships | `role` | integer | no | 70 | — |
| ownerships | `rubygem_id` | integer | — | — | — |
| ownerships | `token` | string | — | — | — |
| ownerships | `token_expires_at` | datetime | — | — | — |
| ownerships | `updated_at` | datetime | — | — | — |
| ownerships | `user_id` | integer | — | — | users |
| rubygem_transfers | `completed_at` | datetime | — | — | — |
| rubygem_transfers | `created_at` | datetime | no | — | — |
| rubygem_transfers | `created_by_id` | bigint | no | — | users |
| rubygem_transfers | `error` | text | — | — | — |
| rubygem_transfers | `organization_id` | bigint | — | — | organizations |
| rubygem_transfers | `rubygems` | integer | — | [] | — |
| rubygem_transfers | `status` | string | no | pending | — |
| rubygem_transfers | `updated_at` | datetime | no | — | — |
| rubygems | `created_at` | datetime | — | — | — |
| rubygems | `indexed` | boolean | no | false | — |
| rubygems | `name` | string | — | — | — |
| rubygems | `organization_id` | bigint | — | — | organizations |
| rubygems | `updated_at` | datetime | — | — | — |
| sendgrid_events | `created_at` | datetime | no | — | — |
| sendgrid_events | `email` | string | — | — | — |
| sendgrid_events | `event_type` | string | — | — | — |
| sendgrid_events | `occurred_at` | datetime | — | — | — |
| sendgrid_events | `payload` | jsonb | no | — | — |
| sendgrid_events | `sendgrid_id` | string | no | — | — |
| sendgrid_events | `status` | string | no | — | — |
| sendgrid_events | `updated_at` | datetime | no | — | — |
| subscriptions | `created_at` | datetime | — | — | — |
| subscriptions | `rubygem_id` | integer | — | — | — |
| subscriptions | `updated_at` | datetime | — | — | — |
| subscriptions | `user_id` | integer | — | — | — |
| users | `api_key` | string | — | — | — |
| users | `blocked_email` | string | — | — | — |
| users | `confirmation_token` | string | — | — | — |
| users | `created_at` | datetime | — | — | — |
| users | `deleted_at` | datetime | — | — | — |
| users | `email` | string | — | — | — |
| users | `email_confirmed` | boolean | no | false | — |
| users | `email_reset` | boolean | — | — | — |
| users | `encrypted_password` | string | — | — | — |
| users | `full_name` | string | — | — | — |
| users | `handle` | string | — | — | — |
| users | `hide_email` | boolean | — | true | — |
| users | `mail_fails` | integer | — | 0 | — |
| users | `mfa_hashed_recovery_codes` | string | — | [] | — |
| users | `mfa_level` | integer | — | 0 | — |
| users | `password_reset_token_digest` | string | — | — | — |
| users | `password_reset_token_expires_at` | datetime | — | — | — |
| users | `policies_acknowledged_at` | datetime | — | — | — |
| users | `public_email` | boolean | no | false | — |
| users | `remember_token` | string | — | — | — |
| users | `remember_token_expires_at` | datetime | — | — | — |
| users | `salt` | string | — | — | — |
| users | `token` | string | — | — | — |
| users | `token_expires_at` | datetime | — | — | — |
| users | `totp_seed` | string | — | — | — |
| users | `twitter_username` | string | — | — | — |
| users | `unconfirmed_email` | string | — | — | — |
| users | `updated_at` | datetime | — | — | — |
| users | `webauthn_id` | string | — | — | — |
| versions | `authors` | text | — | — | — |
| versions | `built_at` | datetime | — | — | — |
| versions | `canonical_number` | string | — | — | — |
| versions | `cert_chain` | text | — | — | — |
| versions | `content_address` | string | — | — | — |
| versions | `created_at` | datetime | — | — | — |
| versions | `description` | text | — | — | — |
| versions | `full_name` | string | — | — | — |
| versions | `gem_full_name` | string | — | — | — |
| versions | `gem_platform` | string | — | — | — |
| versions | `indexed` | boolean | — | true | — |
| versions | `info_checksum_v2` | string | — | — | — |
| versions | `latest` | boolean | — | — | — |
| versions | `licenses` | string | — | — | — |
| versions | `metadata` | hstore | no | {} | — |
| versions | `number` | string | — | — | — |
| versions | `platform` | string | — | — | — |
| versions | `position` | integer | — | — | — |
| versions | `prerelease` | boolean | — | — | — |
| versions | `pusher_api_key_id` | bigint | — | — | api_keys |
| versions | `pusher_id` | bigint | — | — | — |
| versions | `required_ruby_version` | string | — | — | — |
| versions | `required_rubygems_version` | string | — | — | — |
| versions | `requirements` | text | — | — | — |
| versions | `ruby_abi` | string | — | — | — |
| versions | `rubygem_id` | integer | — | — | rubygems |
| versions | `sha256` | string | — | — | — |
| versions | `size` | integer | — | — | — |
| versions | `spec_sha256` | string | — | — | — |
| versions | `summary` | text | — | — | — |
| versions | `updated_at` | datetime | — | — | — |
| versions | `yanked_at` | datetime | — | — | — |
| versions | `yanked_info_checksum_v2` | string | — | — | — |
| web_hooks | `created_at` | datetime | — | — | — |
| web_hooks | `disabled_at` | datetime | — | — | — |
| web_hooks | `disabled_reason` | text | — | — | — |
| web_hooks | `failure_count` | integer | — | 0 | — |
| web_hooks | `failures_since_last_success` | integer | — | 0 | — |
| web_hooks | `last_failure` | datetime | — | — | — |
| web_hooks | `last_success` | datetime | — | — | — |
| web_hooks | `rubygem_id` | integer | — | — | — |
| web_hooks | `successes_since_last_failure` | integer | — | 0 | — |
| web_hooks | `updated_at` | datetime | — | — | — |
| web_hooks | `url` | string | — | — | — |
| web_hooks | `user_id` | integer | — | — | users |
| webauthn_credentials | `created_at` | datetime | no | — | — |
| webauthn_credentials | `external_id` | string | no | — | — |
| webauthn_credentials | `nickname` | string | no | — | — |
| webauthn_credentials | `public_key` | string | no | — | — |
| webauthn_credentials | `sign_count` | bigint | no | 0 | — |
| webauthn_credentials | `updated_at` | datetime | no | — | — |
| webauthn_credentials | `user_id` | bigint | no | — | users |
| webauthn_verifications | `created_at` | datetime | no | — | — |
| webauthn_verifications | `otp` | string | — | — | — |
| webauthn_verifications | `otp_expires_at` | datetime | — | — | — |
| webauthn_verifications | `path_token` | string | — | — | — |
| webauthn_verifications | `path_token_expires_at` | datetime | — | — | — |
| webauthn_verifications | `updated_at` | datetime | no | — | — |
| webauthn_verifications | `user_id` | bigint | no | — | users |

## Indexes

- `admin_github_users`: `index_admin_github_users_on_github_id` (github_id, unique)
- `api_key_rubygem_scopes`: `index_api_key_rubygem_scopes_on_api_key_id` (api_key_id)
- `api_keys`: `index_api_keys_on_hashed_key` (hashed_key, unique); `index_api_keys_on_name_trigram_for_users` (name); `index_api_keys_on_owner` (owner_type, owner_id)
- `attestations`: `index_attestations_on_version_id` (version_id)
- `audits`: `index_audits_on_admin_github_user_id` (admin_github_user_id); `index_audits_on_auditable` (auditable_type, auditable_id)
- `blazer_audits`: `index_blazer_audits_on_query_id` (query_id); `index_blazer_audits_on_user_id` (user_id)
- `blazer_checks`: `index_blazer_checks_on_creator_id` (creator_id); `index_blazer_checks_on_query_id` (query_id)
- `blazer_dashboard_queries`: `index_blazer_dashboard_queries_on_dashboard_id` (dashboard_id); `index_blazer_dashboard_queries_on_query_id` (query_id)
- `blazer_dashboards`: `index_blazer_dashboards_on_creator_id` (creator_id)
- `blazer_queries`: `index_blazer_queries_on_creator_id` (creator_id)
- `blocked_email_domains`: `index_blocked_email_domains_on_domain` (domain, unique); `index_blocked_email_domains_on_source` (source)
- `deletions`: `index_deletions_on_user_id` (user_id); `index_deletions_on_version_id` (version_id)
- `dependencies`: `index_dependencies_on_rubygem_id` (rubygem_id); `index_dependencies_on_unresolved_name` (unresolved_name); `index_dependencies_on_version_id` (version_id)
- `email_domain_allowlists`: `index_email_domain_allowlists_on_domain` (domain, unique)
- `events_organization_events`: `index_events_organization_events_on_geoip_info_id` (geoip_info_id); `index_events_organization_events_on_ip_address_id` (ip_address_id); `index_events_organization_events_on_organization_id` (organization_id); `index_events_organization_events_on_tag` (tag)
- `events_rubygem_events`: `index_events_rubygem_events_on_geoip_info_id` (geoip_info_id); `index_events_rubygem_events_on_ip_address_id` (ip_address_id); `index_events_rubygem_events_on_rubygem_id` (rubygem_id); `index_events_rubygem_events_on_tag` (tag)
- `events_user_events`: `index_events_user_events_on_geoip_info_id` (geoip_info_id); `index_events_user_events_on_ip_address_id` (ip_address_id); `index_events_user_events_on_tag` (tag); `index_events_user_events_on_user_id` (user_id)
- `flipper_features`: `index_flipper_features_on_key` (key, unique)
- `flipper_gates`: `index_flipper_gates_on_feature_key_and_key_and_value` (feature_key, key, value, unique)
- `gem_downloads`: `index_gem_downloads_on_count` (count); `index_gem_downloads_on_rubygem_id_and_version_id` (rubygem_id, version_id, unique); `index_gem_downloads_on_version_id_and_rubygem_id_and_count` (version_id, rubygem_id, count)
- `gem_name_reservations`: `index_gem_name_reservations_on_name` (name, unique)
- `geoip_infos`: `index_geoip_infos_on_fields` (continent_code, country_code, country_code3, country_name, region, city, unique)
- `good_job_executions`: `index_good_job_executions_on_active_job_id_and_created_at` (active_job_id, created_at); `index_good_job_executions_on_process_id_and_created_at` (process_id, created_at)
- `good_job_settings`: `index_good_job_settings_on_key` (key, unique)
- `good_jobs`: `index_good_jobs_on_active_job_id_and_created_at` (active_job_id, created_at); `index_good_jobs_on_batch_callback_id` (batch_callback_id); `index_good_jobs_on_batch_id` (batch_id); `index_good_jobs_on_concurrency_key_when_unfinished` (concurrency_key); `index_good_jobs_on_cron_key_and_created_at_cond` (cron_key, created_at); `index_good_jobs_on_cron_key_and_cron_at_cond` (cron_key, cron_at, unique); `index_good_jobs_jobs_on_finished_at` (finished_at); `index_good_jobs_on_labels` (labels); `index_good_jobs_on_locked_by_id` (locked_by_id); `index_good_job_jobs_for_candidate_lookup` (priority, created_at); `index_good_jobs_jobs_on_priority_created_at_when_unfinished` (priority, created_at); `index_good_jobs_on_priority_scheduled_at_unfinished_unlocked` (priority, scheduled_at); `index_good_jobs_on_queue_dequeue_ordered` (queue_name, priority, created_at); `index_good_jobs_on_queue_name_and_scheduled_at` (queue_name, scheduled_at); `index_good_jobs_on_scheduled_at` (scheduled_at)
- `ip_addresses`: `index_ip_addresses_on_geoip_info_id` (geoip_info_id); `index_ip_addresses_on_hashed_ip_address` (hashed_ip_address, unique); `index_ip_addresses_on_ip_address` (ip_address, unique)
- `link_verifications`: `index_link_verifications_on_linkable_and_uri` (linkable_id, linkable_type, uri); `index_link_verifications_on_linkable` (linkable_type, linkable_id)
- `linksets`: `index_linksets_on_rubygem_id` (rubygem_id)
- `log_tickets`: `index_log_tickets_on_directory_and_key` (directory, key, unique)
- `maintenance_tasks_runs`: `index_maintenance_tasks_runs` (task_name, status, created_at)
- `memberships`: `index_memberships_on_invited_by_id` (invited_by_id); `index_memberships_on_organization_id` (organization_id); `index_memberships_on_user_id_and_organization_id` (user_id, organization_id, unique); `index_memberships_on_user_id` (user_id)
- `oidc_api_key_roles`: `index_oidc_api_key_roles_on_oidc_provider_id` (oidc_provider_id); `index_oidc_api_key_roles_on_token` (token, unique); `index_oidc_api_key_roles_on_user_id` (user_id)
- `oidc_id_tokens`: `index_oidc_id_tokens_on_api_key_id` (api_key_id); `index_oidc_id_tokens_on_oidc_api_key_role_id` (oidc_api_key_role_id)
- `oidc_pending_trusted_publishers`: `index_oidc_pending_trusted_publishers_on_trusted_publisher` (trusted_publisher_type, trusted_publisher_id); `index_oidc_pending_trusted_publishers_on_user_id` (user_id)
- `oidc_providers`: `index_oidc_providers_on_issuer` (issuer, unique)
- `oidc_rubygem_trusted_publishers`: `index_oidc_rubygem_trusted_publishers_unique` (rubygem_id, trusted_publisher_id, trusted_publisher_type, unique); `index_oidc_rubygem_trusted_publishers_on_trusted_publisher` (trusted_publisher_type, trusted_publisher_id)
- `oidc_trusted_publisher_github_actions`: `index_oidc_trusted_publisher_github_actions_claims` (repository_owner, repository_name, repository_owner_id, workflow_filename, environment, workflow_repository_owner, workflow_repository_name, unique)
- `organization_invites`: `index_organization_invites_on_invitable` (invitable_type, invitable_id); `index_organization_invites_on_invitable_type_and_invitable_id` (invitable_type, invitable_id); `index_organization_invites_on_user_id` (user_id)
- `organization_onboarding_invites`: `idx_on_organization_onboarding_id_e5b08868fb` (organization_onboarding_id); `index_organization_onboarding_invites_on_user_id` (user_id)
- `organizations`: `index_organizations_on_lower_handle` (unique)
- `ownership_calls`: `index_ownership_calls_on_rubygem_id` (rubygem_id); `index_ownership_calls_on_user_id` (user_id)
- `ownership_requests`: `index_ownership_requests_on_ownership_call_id` (ownership_call_id); `index_ownership_requests_on_rubygem_id` (rubygem_id); `index_ownership_requests_on_user_id` (user_id)
- `ownerships`: `index_ownerships_on_rubygem_id` (rubygem_id); `index_ownerships_on_user_id_and_rubygem_id` (user_id, rubygem_id, unique)
- `rubygem_transfers`: `index_rubygem_transfers_on_created_by_id` (created_by_id); `index_rubygem_transfers_on_organization_id` (organization_id)
- `rubygems`: `dashunderscore_typos_idx`; `index_rubygems_upcase`; `index_rubygems_on_indexed` (indexed); `index_rubygems_on_name` (name, unique); `index_rubygems_on_organization_id` (organization_id)
- `sendgrid_events`: `index_sendgrid_events_on_email` (email); `index_sendgrid_events_on_sendgrid_id` (sendgrid_id, unique)
- `subscriptions`: `index_subscriptions_on_rubygem_id` (rubygem_id); `index_subscriptions_on_user_id` (user_id)
- `users`: `index_users_on_lower_email`; `index_users_on_blocked_email_trigram` (blocked_email); `index_users_on_email` (email); `index_users_on_email_trigram` (email); `index_users_on_handle` (handle); `index_users_on_handle_trigram` (handle); `index_users_on_id_and_confirmation_token` (id, confirmation_token); `index_users_on_id_and_token` (id, token); `index_users_on_policies_not_acknowledged` (id); `index_users_on_password_reset_token_digest` (password_reset_token_digest, unique); `index_users_on_remember_token` (remember_token); `index_users_on_token` (token); `index_users_on_webauthn_id` (webauthn_id, unique)
- `versions`: `index_versions_on_lower_full_name`; `index_versions_on_lower_gem_full_name`; `index_versions_on_built_at` (built_at); `index_versions_canonical_platform_abi` (canonical_number, rubygem_id, platform, ruby_abi, unique); `index_versions_canonical_platform` (canonical_number, rubygem_id, platform); `index_versions_canonical_platform_no_abi` (canonical_number, rubygem_id, platform, unique); `index_versions_on_created_at` (created_at); `index_versions_on_full_name` (full_name); `index_versions_on_indexed_and_yanked_at` (indexed, yanked_at); `index_versions_on_number` (number); `index_versions_on_position_and_rubygem_id` (position, rubygem_id); `index_versions_on_prerelease` (prerelease); `index_versions_on_pusher_api_key_id` (pusher_api_key_id); `index_versions_on_pusher_id` (pusher_id); `index_versions_number_content_address` (rubygem_id, number, content_address, unique); `index_versions_number_platform_abi` (rubygem_id, number, platform, ruby_abi, unique); `index_versions_number_platform` (rubygem_id, number, platform); `index_versions_number_platform_no_abi` (rubygem_id, number, platform, unique); `index_versions_on_rubygem_id_and_position_and_created_at` (rubygem_id, position, created_at)
- `web_hooks`: `index_web_hooks_on_user_id_and_rubygem_id` (user_id, rubygem_id)
- `webauthn_credentials`: `index_webauthn_credentials_on_external_id` (external_id, unique); `index_webauthn_credentials_on_user_id` (user_id)
- `webauthn_verifications`: `index_webauthn_verifications_on_user_id` (user_id, unique)

## Residuals

- An `add_foreign_key` without an explicit `column:` infers the referencing column as `<referenced_singular>_id`, which mis-attributes a non-conventional FK column (a named residual).
