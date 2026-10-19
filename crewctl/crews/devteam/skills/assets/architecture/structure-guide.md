# Choosing a structure that stays reusable and maintainable

## Pick the shape from the facts
- Small team, one deployable, domain still changing: a modular monolith with enforced module boundaries. Split into services only for a measured reason (independent scaling, different release cadence, team ownership, fault isolation).
- Many teams, clear bounded contexts, independent release needs: services per bounded context, each owning its data.
- Event-driven needs (fan-out, async work, audit trail): add a broker, and decide delivery semantics first (at-least-once with idempotent consumers is the usual safe default).
- Bursty or rare workloads: consider functions or queues with workers. Always-on steady load: containers are usually cheaper.

## Layout rules that keep code reusable
1. Organize by domain first (`orders/`, `billing/`), and by technical layer inside each domain. Not by layer at the top (`controllers/`, `models/`), which spreads one feature over the whole tree.
2. Dependencies point inward: transport (HTTP, gRPC, CLI) -> application services -> domain -> nothing. Infrastructure (database, cache, queue clients) implements interfaces the application defines.
3. One responsibility per module; a module exports a small public surface and hides the rest (private packages, `internal/`, `_private`, crate visibility).
4. Shared code lives in a `shared`/`platform` module only after two real users need it. No `utils` dumping ground; name modules for what they do.
5. Configuration is read once at the edge and passed in. No global state; no reading the environment deep in the code.
6. Every external call has a timeout, a retry policy that matches its idempotency, and an interface so it can be faked in tests.
7. Tests mirror the tree. Unit tests beside the code or under `tests/<same path>`; integration tests in their own folder with their dependencies in containers.
8. Contracts first: API schemas (OpenAPI, protobuf) live in their own folder and generate code; breaking changes need a version.

## Example tree (adapt, do not copy blindly)
```
service/
  cmd/ or app/            entry points only: parse config, wire dependencies, start
  internal/ or src/
    <domain>/
      domain/             entities, rules; no I/O
      service/            use cases; depends on interfaces
      adapters/           database, cache, queue, http clients implementing the interfaces
      transport/          HTTP/gRPC handlers; map requests to use cases
    platform/             logging, config, metrics, tracing helpers shared by all domains
  api/                    OpenAPI / protobuf contracts and generated code
  migrations/
  deploy/                 Dockerfile, Helm/Kustomize, pipeline files
  docs/adr/               decision records
  tests/                  integration and end-to-end
```

## Coding style to state in the design
Formatter and linter (pinned versions), naming rules, error handling (wrap with context; one place logs), logging fields, API versioning, dependency policy (pinned, reviewed, scanned), branching and release rules, definition of done.

## Technology choice rules
For each choice record: requirement it serves, two alternatives, the deciding fact, operational cost (who runs it, how it fails), team familiarity, license, and the exit cost if it must be replaced. Prefer boring, well-understood technology unless a measured requirement rules it out.
