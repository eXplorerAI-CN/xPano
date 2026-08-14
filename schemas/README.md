# xPano Contracts

The JSON Schema documents in this directory are the persisted and event-wire contract source of truth.

- `xpano_project_v3.schema.json` describes `xpano_project.json`.
- `xpano_job_event_v1.schema.json` describes durable task events.
- `xpano_execution_plan_v1.schema.json` describes backend-generated reconstruction plans.
- `fixtures/` contains portable examples used by Rust, Python, and TypeScript build checks.

Generated project artifacts must use project-relative paths. External source media may use absolute paths and must carry a source fingerprint.
