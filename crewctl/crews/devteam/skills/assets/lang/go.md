# Go engineering reference

Check the Go version in `go.mod` and the release notes for version-specific behaviour. Every performance claim needs a benchmark or profile from this code.

## Clean code
- Package names are short and lower-case; no `util` or `common`. Small interfaces defined where they are used; accept interfaces, return concrete types.
- `context.Context` is the first parameter of anything that blocks; never store it in a struct. Every goroutine has an owner and a way to stop (`errgroup`, context cancellation, closed channel); no goroutine leaks.
- Errors are values: wrap with `fmt.Errorf("doing x: %w", err)`, test with `errors.Is` / `errors.As`, handle an error once (log or return, not both). No `panic` for expected failures.
- Zero values should be useful. Avoid package-level mutable state and `init()` side effects. Use functional options for constructors with many settings.
- Tooling in CI: `gofmt`/`goimports`, `go vet`, `staticcheck` or `golangci-lint`, `go test -race`, `govulncheck`. Table-driven tests, fuzz tests for parsers, `testdata/` for fixtures.

## Performance workflow
1. Benchmark: `go test -bench . -benchmem -count=10`, compare with `benchstat`. 2. Profile: `pprof` (CPU, heap, allocs, block, mutex) or runtime trace. 3. Read the escape analysis: `go build -gcflags='-m'`. 4. Change one thing and re-measure.
Common wins: fewer allocations (preallocate slices and maps with capacity, reuse buffers, `strings.Builder`, avoid `interface{}` boxing), `sync.Pool` only for measured hot short-lived objects, avoid copying large structs in loops, batch syscalls and I/O with `bufio`, avoid lock contention (shard, or use `atomic`), profile-guided optimization (`default.pgo` in the main package).
Memory: `GOGC` and `GOMEMLIMIT` tune the garbage collector; in containers check that `GOMAXPROCS` matches the CPU quota (recent releases consider cgroup limits; otherwise set it, for example with `automaxprocs`).

## CPU architecture
- Build targets: `GOOS`, `GOARCH`, `GOAMD64=v1..v4` (v3 assumes AVX2, BMI), `GOARM64`. Raising the level makes binaries fail on older CPUs, so match the fleet.
- Runtime feature detection with `golang.org/x/sys/cpu` (`cpu.X86.HasAVX2`, `cpu.ARM64.HasSVE`...). Hot loops that need SIMD: write assembly (Go assembler or `avo`) behind a build tag, always with a pure-Go fallback and a test that compares both.
- Cache: cache lines are typically 64 bytes (some Arm cores use 128). Pack hot fields together, order struct fields to cut padding (`fieldalignment`), and pad per-CPU counters to avoid false sharing. Prefer arrays of structs of arrays for sequential scans.
- Memory model: x86 is strongly ordered, Arm is weaker. Code that "works on x86" with a data race can fail on Arm. Use `sync` or `sync/atomic`, and run `-race`.
- NUMA: pin the process with `numactl`/`taskset` and keep memory local; measure cross-socket cost before restructuring.

## Linux kernel interaction
Go's runtime uses epoll for network I/O. Costs worth knowing: syscalls (batch them; count with `strace -c`), context switches, scheduler latency, CFS CPU-quota throttling in containers (shows as latency spikes while CPU use looks low), page cache behaviour, `SO_REUSEPORT` for multi-accept, `TCP_NODELAY`, huge pages, `madvise`. Tools: `perf`, `bpftrace`, `pidstat`, `/proc/<pid>/status`. For extreme I/O consider `io_uring` through a library, only after epoll is shown to be the limit.

## Review checklist for every Go change
Goroutine ownership; context passed; errors wrapped once; no data race (`-race` run); allocation hot spots measured; public API minimal and documented; tests for failure paths; benchmarks attached when performance is the goal.
