# PatientPlus Backend Documentation

This folder explains the current backend structure as it exists in the repository. It is intended as a quick orientation layer for engineers who need to understand how requests move through the app, where important helpers live, and how runtime configuration and Azure Key Vault secrets are used.

## Documents

- [architecture.md](architecture.md) - high-level backend architecture, startup sequence, package layout, and main runtime flows.
- [configuration-and-secrets.md](configuration-and-secrets.md) - environment settings, Azure Key Vault behavior, secret naming, and where secrets are consumed.
- [api-and-domain-map.md](api-and-domain-map.md) - discovered API route structure, domain model groups, and how route modules map to database builders.
- [deployment-and-operations.md](deployment-and-operations.md) - Docker, AKS, Azure DevOps pipeline, health probes, and operational notes.

## Core Component Guides

- [core-components/application-startup-and-routing.md](core-components/application-startup-and-routing.md) - FastAPI app construction, lifespan startup, probe routes, middleware order, CORS, and dynamic router loading.
- [core-components/authentication-and-sessions.md](core-components/authentication-and-sessions.md) - OAuth verification, silent SSO, auth middleware, token refresh, cookies, and session persistence.
- [core-components/data-access-and-domain-models.md](core-components/data-access-and-domain-models.md) - SQLAlchemy engine/session handling, model groups, builder classes, audit fields, and table ownership.
- [core-components/prompts-and-chat-history.md](core-components/prompts-and-chat-history.md) - prompt template files, startup prompt sync, prompt lookup API, chat history, search, and status endpoints.
- [core-components/shared-utilities-and-infrastructure.md](core-components/shared-utilities-and-infrastructure.md) - Key Vault helper, settings/setup helpers, cache, date utilities, security helpers, and deployment support files.

## Fast Orientation

The backend is a FastAPI service rooted at [main.py](../main.py). It registers public health endpoints, applies authentication middleware, dynamically discovers API routers under [app/api](../app/api), and connects to SQL Server through SQLAlchemy/pyodbc. Sensitive values are resolved through [app/utils/vault.py](../app/utils/vault.py), which reads Azure Key Vault first and falls back to environment variables for local development.

The most important folders are:

```text
app/api/            FastAPI route modules, discovered automatically
app/middlewares/    HTTP authentication middleware
app/models/         SQLAlchemy models plus builder-style data access helpers
app/utils/          shared helpers for vault, security, cache, dates, setup
prompts/            prompt templates synced into the database at startup
scripts/            maintenance/startup scripts, currently prompt sync
linked_services/    Azure DevOps and Kubernetes deployment assets
```

## Current Code Notes

Some comments and README sections refer to encrypted access_token cookies. In the current route and middleware implementation, token encryption/decryption is commented out; the OAuth access token itself is set in the `access_token` HttpOnly cookie.

The current `VaultHandler` attempts `DefaultAzureCredential` first and falls back to `AzureCliCredential` when needed. This supports AKS Workload Identity in deployed environments and `az login` for local development.