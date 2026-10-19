# Rust engineering reference

Check `rust-version` in `Cargo.toml` and the edition. Every performance claim needs a benchmark or profile from this code.

## Clean code
- Model states with types: enums for variants, newtypes for units and ids, `Option` instead of sentinel values, builders for many parameters. Make illegal states unrepresentable.
- Errors: a typed error per library crate (`thiserror`), `anyhow` or `eyre` in binaries; add context; no `unwrap()`/`expect()` on user-controlled paths (document the invariant when you do use `expect`).
- Borrow before you own: take `&str`/`&[T]`, return owned data only when needed; use `Cow` where both occur. Avoid needless `clone()`. Prefer iterators over index loops. Keep lifetimes simple; restructure data rather than fight the borrow checker with `Rc<RefCell<_>>`.
- `unsafe`: keep it small, wrap it in a safe API, put a `// SAFETY:` comment on each block stating the invariant, and use `#![forbid(unsafe_code)]` in crates that do not need it. Test unsafe code with `miri`.
- Tooling in CI: `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`, `cargo deny` / `cargo audit`, `cargo doc` with `#![warn(missing_docs)]` for libraries; pin the toolchain in `rust-toolchain.toml`.
- Async (tokio): never block inside async code (use `spawn_blocking`), use bounded channels, make cancellation safe (a future can be dropped at any `.await`), set timeouts, and avoid holding a lock across `.await`.

## Performance workflow
1. Benchmark: `criterion` or `divan` (release mode, realistic input). 2. Profile: `perf`, `cargo flamegraph`, `samply`; memory with `dhat`, `heaptrack`; cache behaviour with `cachegrind`. 3. Inspect generated code when it matters (`cargo asm`, Compiler Explorer). 4. Change one thing, re-measure.
Common wins: remove allocations (preallocate with `with_capacity`, reuse buffers, `SmallVec`, arenas), better data layout (struct of arrays), avoid dynamic dispatch in hot paths, iterate in a way the compiler can vectorize, avoid bounds checks with slices/chunks/iterators, choose a faster hasher for non-adversarial maps (`FxHash`, `ahash`), a different allocator (`mimalloc`, `jemalloc`).

## Release profile and CPU targets
```toml
[profile.release]
opt-level = 3
lto = "fat"          # or "thin" for faster builds
codegen-units = 1
panic = "abort"      # only if unwinding is not needed
debug = "line-tables-only"   # keep symbols for profiling
```
- `RUSTFLAGS="-C target-cpu=native"` is fine for a binary built and run on the same machine. For binaries you ship, target a baseline (for example `x86-64-v2`/`v3`, or a specific Arm level) and use runtime detection for faster paths: `is_x86_feature_detected!`, `std::arch` intrinsics, or the `multiversion` crate; keep a portable fallback and test both.
- Profile-guided optimization with `cargo-pgo`; post-link optimization with BOLT for large binaries. Measure; gains vary.

## CPU and kernel awareness
Cache lines are about 64 bytes (some Arm cores 128): use `#[repr(align(64))]` to separate hot counters used by different threads (false sharing); `#[repr(C)]` only when layout must be fixed, otherwise let the compiler reorder fields. Atomics: pick the weakest `Ordering` you can justify and test with `loom`; Arm is weaker than x86, so a missing ordering can pass on x86 and fail on Arm. NUMA: pin threads and keep memory local. Linux: batch syscalls, use `mmap` and huge pages deliberately, `io_uring` through `tokio-uring`, `glommio` or `monoio` only after epoll is shown to be the limit. Tools: `perf`, `strace -c`, `bpftrace`.

## Review checklist for every Rust change
Public API minimal; errors typed with context; no panics on user input; each `unsafe` justified and tested with miri; `clippy` clean; tests include failure paths and property tests where logic is intricate; benchmarks attached when performance is the goal.
