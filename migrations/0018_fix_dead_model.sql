-- 0018_fix_dead_model.sql — OpenRouter retired anthropic/claude-sonnet-4 (404). Repoint the route.
UPDATE provider_routes SET model = 'anthropic/claude-sonnet-4.6'
 WHERE task_type = 'draft.strong' AND model = 'anthropic/claude-sonnet-4';
