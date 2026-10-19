# Python engineering reference

Verify version-specific statements against the release notes of the interpreter the project actually uses (`python --version`, `python -VV`).

## Measure before tuning
1. Reproduce with a benchmark (`pyperf`, `timeit`) on realistic data. 2. Profile: `cProfile` or `py-spy` (sampling, attaches to a running process), `scalene` (CPU, memory), `tracemalloc` (allocations), `line_profiler`. 3. Change one thing, re-measure, keep the numbers.
Typical wins in order: better algorithm or data structure; fewer calls in hot loops; vectorize with NumPy; bounded caching (`functools.lru_cache(maxsize=...)`); `__slots__` / `dataclass(slots=True)`; generators for large streams; move the hot function to Cython, mypyc, Numba or a Rust extension (PyO3) only after the profile points at it.

## Concurrency model by version
- **Any CPython with the GIL (the default build up to and including 3.13):** only one thread runs Python bytecode at a time. Threads help I/O-bound work and code that releases the GIL (NumPy, many C extensions). CPU-bound Python needs processes (`multiprocessing`, `ProcessPoolExecutor`), native extensions, or a different design. `asyncio` is for many concurrent I/O waits in one thread; never block the loop (use `asyncio.to_thread` or an executor for blocking calls).
- **3.13:** experimental free-threaded build (separate `python3.13t` binary, no GIL) and an experimental JIT (build-time option). Both are for experiments, not production.
- **3.14:** the free-threaded build is officially supported but optional, and single-thread speed is lower than the default build, so benchmark. Multiple interpreters in one process are available through `concurrent.interpreters` and `InterpreterPoolExecutor` (each interpreter has its own GIL, data is passed by copy or shareable objects, not shared freely). The JIT is still experimental. Also new: template strings (`t"..."`), deferred evaluation of annotations, `compression.zstd`.
- Choose: I/O-bound -> asyncio or threads; CPU-bound on old Python -> processes; CPU-bound on 3.14 -> try interpreters or free-threaded and measure against processes; shared mutable state across threads on a free-threaded build -> assume nothing is atomic, use locks or queues, and check every dependency declares free-threaded support.

## Style and tooling
Type hints everywhere public; `ruff` (lint and format), `mypy` or `pyright` in strict mode for new code, `pytest` with fixtures and parametrization, `hypothesis` for property tests, `uv` or `pip-tools` for locked dependencies, `pre-commit`. Exceptions: raise specific types, chain with `from`, never bare `except:`. Pass dependencies in (no hidden globals). Prefer `pathlib`, `dataclasses`, `enum`, context managers for resources, `logging` with structured fields (no `print`).

## gRPC
- Define contracts in `.proto`; lint and check breaking changes with `buf`. Generate code in CI, do not hand-edit.
- Always set a deadline on every client call; propagate the caller's deadline downstream. Reuse channels; do not create one per request.
- Use `grpc.aio` for async servers and clients; sync servers need `max_workers` sized to the blocking work. Configure keepalive, max message sizes and compression deliberately.
- Retries only for idempotent methods, via the service config, with backoff. Return proper status codes (`INVALID_ARGUMENT`, `DEADLINE_EXCEEDED`, `UNAVAILABLE`); do not put errors in a success payload.
- Add interceptors for auth, metrics and tracing (OpenTelemetry). Implement the health-check service. Graceful shutdown: `server.stop(grace)`.
- Streaming: respect backpressure and cancel on client disconnect.

## Messaging and cache
- **Kafka** (`confluent-kafka`, `aiokafka`): choose the partition key for ordering; consumer groups; commit offsets after processing succeeds; idempotent producer; handle rebalances; a dead-letter topic for poison messages; a schema registry for contracts.
- **RabbitMQ** (`aio-pika`, `pika`): explicit acks, sensible `prefetch`, publisher confirms, dead-letter exchanges, durable queues; one channel per thread.
- **NATS JetStream, Redis Streams, SQS:** know the delivery guarantee (usually at-least-once) and make consumers idempotent with an idempotency key. Use the outbox pattern when a database write and a message must succeed together.
- **Task queues** (Celery, Dramatiq, arq): set time limits, retries with backoff, and keep tasks idempotent.
- **Redis cache** (`redis-py`): connection pool, timeouts on every call, TTL with random jitter, protect against stampede (single-flight or a short lock), version cache keys, never cache secrets, decide what happens when the cache is down (fall back, do not fail the request).
- Always: timeouts, bounded queues, retries with jitter, metrics for lag and error rate.
