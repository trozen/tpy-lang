# Python Stdlib Coverage

Tracks TPy's coverage of the CPython standard library. This is a living
document -- update it whenever a stdlib module or item gains/loses support.

**Scope**: only modules present in CPython's `stdlib`. TPy-native extras
(`tplib.Box`, `tplib.ArrayList`, `tplib.FixStr`, `tplib.json`) and builtins
(len, print, list, dict, ...) are tracked elsewhere (`LANGUAGE_FEATURES.md`).

Status legend:
- **Done** -- matches CPython semantics for the items listed below
- **Partial** -- usable; some items missing or differ from CPython
- **Stub** -- minimal scaffolding, most items missing
- **Missing** -- not started
- **Blocked** -- needs a language/runtime feature before meaningful work

Priority reflects impact for typical Python programs (P0 = essential for
most real programs; P3 = rarely needed / specialized).

Approach legend: **native** (thin `@native` bindings to libc / OS / existing
C++ libraries), **pure** (pure TPy), **macro** (compile-time macro module),
**mixed** (combination).

---

## Implementation Policy

Stdlib modules should be written in **pure TPy (.py)** by default. C++ code
is introduced only where it genuinely must be:

1. **OS / libc / syscalls** -- wall-clock time, filesystem, sockets, process
   spawning, memory maps. There is no TPy-level equivalent.
2. **Existing C++ library bindings** -- regex engines (std::regex, PCRE2),
   crypto (OpenSSL), compression (zlib), etc. We wrap, we don't rewrite.
3. **Performance-critical inner loops where TPy generation is demonstrably
   worse.** Must be justified with a benchmark; not a default assumption.

Everything else is pure TPy. If the language is missing something that blocks
a clean pure-TPy implementation -- closures, generators, a specific dunder,
module-level mutable state, `*args`/`**kwargs`, runtime type info -- the
**right fix is to extend the language**, not to drop into C++. A stdlib that
hits language gaps is the best possible driver for language work, because
each gap has a concrete user-visible payoff.

For modules that do need native code, follow the **thin-binding pattern**:
import raw symbols via `@native` into a minimal module (e.g.
`tplib.cppstd.re`, `tpy._os.libc`), then build the CPython-compatible surface
as pure-TPy wrappers on top. The native layer should expose the underlying
primitive faithfully -- no Python semantics baked in -- and the pure-TPy
layer translates to Python semantics (error handling, optional args,
defaults, naming, iteration protocols). Benefits:

- One place to swap backends (e.g. std::regex -> PCRE2) without touching
  user-visible code.
- CPython stubs in `lib/cpy/` can mirror the pure-TPy wrappers directly --
  only the bottom layer differs.
- Type checker, IDE, and LLMs see real Python code, not opaque C++ symbols.
- Macro-based modules can inspect the pure-TPy layer.

The `approach` column in the overview table below reflects the **public-facing**
strategy. Internally nearly every module ends up "mixed" if it touches the OS --
the distinction is whether the Python-visible logic lives in .py or C++.

Examples of the policy in action:

- `bisect` -- fully pure TPy over the `Comparable` protocol. No native code.
- `math` -- thin `@native` bindings to `std::log`, `std::sqrt`, etc. (libc
  math is the primitive); no TPy-visible C++ logic beyond the bindings.
  `log(x, base)`, `radians`, `degrees` are pure-TPy overloads/wrappers.
- `random` -- currently thin binds to `std::rand`; long-term should be a
  pure-TPy Mersenne Twister (matches CPython), with only `os.urandom`-style
  entropy as the native primitive.
- `re` -- thin `tplib.cppstd.re` / `tplib.pcre2.re` binding (the regex engine
  is the primitive), with a pure-TPy facade for the Python surface and a
  pure-TPy syntax translator.
- `pathlib` -- pure TPy over thin filesystem-syscall bindings.
- `json` (stdlib-compat) -- pure TPy on top of `tplib.json`'s parser/writer.

---

## Module Overview

| Module | Priority | Status | % | Approach | Blockers / Notes |
|---|---|---|---|---|---|
| [`math`](#math) | P0 | Partial | ~50% | mixed | Thin libc bindings + pure TPy wrappers. Missing fmod/isnan/isinf/gamma family; trig inverses partial |
| [`time`](#time) | P0 | Stub | ~10% | mixed | Thin clock/sleep syscalls + pure TPy. Missing perf_counter/monotonic/struct_time/strftime |
| [`sys`](#sys) | P0 | Stub | ~5% | mixed | Thin syscall bindings + pure TPy. Only `argv`; needs stdout/stderr/exit/path/version_info |
| [`os`](#os) | P0 | Missing | 0% | -- | Needs filesystem wrapper + path handling |
| [`os.path`](#ospath) | P0 | Missing | 0% | -- | Independent of `os`; candidate for pure TPy over C++ `<filesystem>` |
| [`pathlib`](#pathlib) | P0 | Missing | 0% | -- | Class-heavy; depends on filesystem bindings |
| [`io`](#io) | P0 | Missing | 0% | -- | Protocol design needed; unlocks json/csv/pickle/configparser |
| [`json`](#json) | P0 | Missing | 0% | -- | tplib.json exists (TPy-native). Stdlib-compat wrapper would need `io` |
| [`re`](#re) | P0 | Missing | 0% | mixed | Staged: `tplib.cppstd.re` (std::regex) + syntax translator -> `tplib.pcre2.re` later. Backend selection needs F8 |
| [`collections`](#collections) | P0 | Missing | 0% | -- | OrderedDict trivial (have ordered_map); deque needs C++ struct; Counter/defaultdict/namedtuple need macros |
| [`itertools`](#itertools) | P0 | Missing | 0% | -- | C++ primitives exist in `runtime/itertools.hpp`; needs Python-surface module |
| [`functools`](#functools) | P0 | Missing | 0% | -- | partial/reduce/lru_cache need closure + macro support |
| [`random`](#random) | P1 | Stub | ~15% | mixed | Should be pure-TPy Mersenne Twister over a thin entropy binding. Currently thin `std::rand` wrappers |
| [`struct`](#struct) | P1 | Partial | ~60% | macro | unpack/calcsize only; `pack` needs statement-expr or buffer builder |
| [`bisect`](#bisect) | P1 | Done | 100% | pure | All four functions implemented generically over `Comparable` |
| [`enum`](#enum) | P1 | Partial | ~50% | macro | Enum/IntEnum/auto; missing functional API, lookup by name/value, iteration |
| [`dataclasses`](#dataclasses) | P1 | Partial | ~75% | macro | frozen/order/inheritance/asdict/astuple; missing InitVar, __post_init__, replace(), metadata |
| [`typing`](#typing) | P1 | Partial | ~60% | native | Protocols/Sized/Iterator/TypedDict/Unpack; missing Generic, TypeVar, ParamSpec, ClassVar |
| [`datetime`](#datetime) | P1 | Missing | 0% | -- | Class-heavy; needs timedelta arithmetic and timezone handling |
| [`csv`](#csv) | P1 | Missing | 0% | -- | Depends on `io` |
| [`base64`](#base64) | P1 | Missing | 0% | -- | Candidate for pure TPy; bytes support is ready |
| [`hashlib`](#hashlib) | P1 | Missing | 0% | pure | Pure TPy (md5/sha1/sha256/sha512 are modest loops); optional thin OpenSSL binding later for speed |
| [`argparse`](#argparse) | P1 | Missing | 0% | -- | Dynamic-type heavy; may need macro approach |
| [`logging`](#logging) | P2 | Missing | 0% | -- | Module-level state + handler architecture |
| [`configparser`](#configparser) | P2 | Missing | 0% | -- | Depends on `io` |
| [`urllib.parse`](#urllibparse) | P2 | Missing | 0% | -- | Pure-TPy candidate; no network dependency |
| [`heapq`](#heapq) | P2 | Missing | 0% | -- | Candidate for pure TPy over `list[T]` |
| [`copy`](#copy) | P2 | Missing | 0% | -- | `copy()` deep semantics need intrinsic support |
| [`textwrap`](#textwrap) | P2 | Missing | 0% | -- | Pure TPy candidate |
| [`decimal`](#decimal) | P2 | Missing | 0% | -- | Large surface; candidate for BigInt-based pure impl or native lib |
| [`fractions`](#fractions) | P3 | Missing | 0% | -- | Pure TPy over BigInt |
| [`statistics`](#statistics) | P2 | Missing | 0% | -- | Pure TPy candidate |
| [`pickle`](#pickle) | P2 | Blocked | 0% | -- | Needs dynamic type info + `io` |
| [`shelve`](#shelve) | P3 | Blocked | 0% | -- | Needs pickle |
| [`inspect`](#inspect) | P2 | Blocked | 0% | -- | Needs runtime type/func introspection |
| [`asyncio`](#asyncio) | P1 | Blocked | 0% | -- | Needs async/await (G1 in roadmap) |
| [`threading`](#threading) | P1 | Blocked | 0% | -- | Needs threading primitives |
| [`multiprocessing`](#multiprocessing) | P2 | Blocked | 0% | -- | Needs process spawning + IPC |
| [`subprocess`](#subprocess) | P1 | Blocked | 0% | -- | Needs process spawning |
| [`socket`](#socket) | P1 | Blocked | 0% | -- | Needs network primitives |
| [`http.client`](#httpclient) | P2 | Blocked | 0% | -- | Needs socket + regex |
| [`urllib.request`](#urllibrequest) | P2 | Blocked | 0% | -- | Needs http |

---

## Cross-cutting Language / Runtime Gaps

These unlock multiple stdlib modules. Listed with the modules each would
unblock.

| Gap | Unblocks | Rough effort |
|---|---|---|
| `io.IOBase` protocol + text/binary wrappers (StringIO, BytesIO, TextIOWrapper) | io, json (stdlib), csv, configparser, pickle, shelve | M |
| Regex engine (std::regex phase 1, PCRE2 phase 2, SRE if needed) | re, argparse quality, urllib | M phase 1, L phase 2 |
| Compile-time conditional compilation / build profiles (F8) | re backend selection, allocator choice, embedded variants, debug/release | M |
| Filesystem bindings (wrap C++ `<filesystem>`) | os.path, pathlib, os, shutil | M |
| async/await + event loop | asyncio, aiohttp, async generators | XL |
| Threading primitives (Thread, Lock, Event, Queue) | threading, multiprocessing.dummy, concurrent.futures | XL |
| Process spawning (fork/exec or std::process) | subprocess, multiprocessing | M-L |
| Socket primitives | socket, http.client, smtplib, ftplib, urllib.request | L |
| Runtime type info / reflection for `get_type_hints`, `type(x)`, `isinstance` on concrete | typing runtime, inspect, pickle | L |
| Closure capture for `partial`/`lru_cache` | functools | S-M (may already work via Callable) |

### Existing TODO.md bugs that gate pure-TPy stdlib work

Under the implementation policy (pure TPy over thin native bindings), several
known compiler bugs block clean stdlib modules. These were lower-priority as
isolated issues but become **stdlib prerequisites** when stdlib development
ramps up.

| TODO.md bug | Effect on stdlib | Blocks |
|---|---|---|
| Variable re-exports through `native_module` facades don't produce usable `VARIABLE` bindings downstream (bug in Bugs section) | Any stdlib module that exposes module-level constants through a facade (re-export via `__init__.py`) can't be used via the idiomatic `from module import CONST` | `sys` (stdout/stderr as objects), `math` constants, `time` constants, any singleton instance |
| Init chain doesn't propagate transitively through `native_module` facades | Non-trivial module-level init (seeding a Mersenne Twister, building a lookup table, opening stdout/stderr) is silently not run when reached via a facade import | `random` (MT state), `logging` (root logger), `locale`, `sys.stdout` wrapping |
| `import pkg.sub` followed by attribute access (`pkg.sub.X`) not supported | Forces `from pkg.sub import X` everywhere. Any stdlib module that users canonically access as `os.path.join(...)` or `logging.info(...)` is painful | `os.path`, `logging.*` module-level functions, `http.client`, `urllib.parse`, any stdlib with sub-packages |
| Module-level mutable state across compilation units | No single source of truth for per-process state | `logging` (handlers registry), `random` (shared RNG instance), `sys.path`, `warnings` |
| Non-native functions in builtin modules can't be called from user code (bug: codegen emits unqualified names) | Can't mix pure-TPy functions into a native-facade module. Forces workaround through `@cpp_template` / `@native` shims | `itertools` (would want to re-export C++ generators as pure-TPy functions), any facade that mixes thin bindings + pure helpers |

Recommendation: bundle these as a **"stdlib enablement"** workstream and fix
them before doing meaningful `random` / `logging` / `os.path` work. Each is
individually S-M effort; together they unblock a large fraction of the pure-
TPy stdlib surface.

---

## Module Detail

Item status legend (per-row): **Done** / **Partial** / **Missing** / **Blocked**.

### math

Current: `lib/tpy/math.py` -- native C++ wrappers. Sufficient for numerics-heavy code.

| Item | Status | Notes |
|---|---|---|
| `pi`, `e`, `inf` | Done | Constants as `Final[float]` |
| `nan` | Missing | Trivial add |
| `tau` | Missing | Trivial add |
| `log`, `log10`, `log2` | Done | `log(x, base)` is pure-TPy overload |
| `sqrt`, `pow`, `exp` | Done | |
| `floor`, `ceil`, `trunc` | Done | Return `int` (BigInt) / generic `T` |
| `sin`, `cos`, `tan` | Done | |
| `asin`, `acos`, `atan2` | Done | |
| `atan` | Missing | Add `@native("std::atan")` |
| `sinh`, `cosh`, `tanh`, `asinh`, `acosh`, `atanh` | Missing | Trivial natives |
| `fabs` | Done | |
| `hypot` | Done | Binary only; variadic form needs `*args` |
| `radians`, `degrees` | Done | Pure-TPy |
| `isnan`, `isinf`, `isfinite` | Missing | Trivial natives (`std::isnan` etc.) |
| `copysign` | Missing | Trivial native |
| `fmod`, `remainder` | Missing | Trivial native |
| `gcd`, `lcm` | Missing | Pure TPy over BigInt |
| `factorial` | Missing | Pure TPy over BigInt |
| `gamma`, `lgamma`, `erf`, `erfc` | Missing | Trivial natives |
| `modf`, `frexp`, `ldexp` | Missing | Need tuple-return from native |

Tests: `math_module`, `math_extended`, `math_log_base` in `tests/cases/builtins/`.

### time

Current: `lib/tpy/time.py` -- native_module. Very thin.

| Item | Status | Notes |
|---|---|---|
| `time()` | Done | Seconds since epoch as float |
| `sleep(s)` | Done | |
| `perf_counter()` | Missing | `std::chrono::steady_clock` |
| `monotonic()` | Missing | Same |
| `time_ns()`, `perf_counter_ns()`, `monotonic_ns()` | Missing | Return `int` (BigInt) or Int64 |
| `process_time()` | Missing | `std::clock` |
| `struct_time` | Missing | Needs named-tuple-like or @dataclass |
| `gmtime`, `localtime` | Missing | Depends on struct_time |
| `strftime`, `strptime` | Missing | Formatting strings; depends on struct_time |
| `mktime` | Missing | Depends on struct_time |
| `asctime`, `ctime` | Missing | Depends on struct_time |
| `timezone`, `altzone`, `tzname` | Missing | Module-level constants |

Tests: `time_module`, `time_sleep`, `time_import`.

### sys

Current: `lib/tpy/sys.py` -- native; only `argv`.

| Item | Status | Notes |
|---|---|---|
| `argv` | Done | List populated at runtime init |
| `exit(code)` | Missing | Native wrapper for `std::exit` |
| `stdout`, `stderr`, `stdin` | Missing | Needs `io` protocol |
| `platform` | Missing | Compile-time constant |
| `version`, `version_info` | Missing | Already in `tpy.version`; could re-export |
| `path` | Missing | List; relates to import machinery (TPy resolves at compile time, so semantics differ) |
| `modules` | Missing | Not meaningful under static compilation |
| `maxsize` | Missing | `PTRDIFF_MAX` constant |
| `byteorder` | Missing | Compile-time constant |
| `getsizeof` | Missing | Hard: sizes differ from CPython (inline fields vs boxed) |
| `executable` | Missing | `argv[0]` / `/proc/self/exe` |

Tests: `sys_argv`.

### os

**Missing.** Depends on filesystem + process bindings. When attacking, likely in order:

| Item | Status | Notes |
|---|---|---|
| `getcwd`, `chdir` | Missing | `std::filesystem::current_path` |
| `listdir`, `scandir` | Missing | `std::filesystem::directory_iterator` |
| `mkdir`, `makedirs`, `rmdir`, `removedirs` | Missing | `std::filesystem::create_directory` etc. |
| `remove`, `rename`, `replace` | Missing | `std::filesystem::remove` / `rename` |
| `stat`, `lstat` | Missing | Needs `stat_result` struct |
| `environ`, `getenv`, `putenv` | Missing | `std::getenv` |
| `path` | Missing | See [os.path](#ospath) |
| `walk` | Missing | Pure TPy over `scandir` |
| `fork`, `exec*`, `spawnv*`, `system` | Blocked | Process spawning |

### os.path

**Missing.** Independent of `os` -- pure TPy over `std::filesystem::path`
would cover most of it.

| Item | Status |
|---|---|
| `join`, `split`, `splitext`, `basename`, `dirname` | Missing |
| `exists`, `isfile`, `isdir`, `islink` | Missing |
| `abspath`, `realpath`, `normpath`, `relpath` | Missing |
| `expanduser`, `expandvars` | Missing |
| `getsize`, `getmtime`, `getatime`, `getctime` | Missing |

### pathlib

**Missing.** Class-based wrapper around `os.path`; depends on filesystem bindings.

| Item | Status |
|---|---|
| `Path`, `PurePath` | Missing |
| Path operators (`/`), `.parent`, `.name`, `.suffix`, `.stem` | Missing |
| `.read_text`, `.write_text`, `.read_bytes`, `.write_bytes` | Missing |
| `.glob`, `.rglob`, `.iterdir` | Missing |
| `.exists`, `.is_file`, `.is_dir`, `.stat`, `.unlink`, `.mkdir` | Missing |

### io

**Missing.** Foundational -- unlocks json (stdlib), csv, configparser, pickle, logging handlers.

Design question: can we reuse the existing `TextIO`/`BinaryIO` from `tpy._builtins._io`
as the protocol backbone, or do we need `IOBase`-style abstract bases with
`readable()`, `writable()`, `seekable()`?

| Item | Status | Notes |
|---|---|---|
| `IOBase` protocol | Missing | Needs protocol with optional methods |
| `RawIOBase`, `BufferedIOBase`, `TextIOBase` | Missing | Protocol hierarchy |
| `StringIO` | Missing | In-memory text buffer (wrap `std::stringstream` or `std::string` + cursor) |
| `BytesIO` | Missing | In-memory bytes buffer |
| `TextIOWrapper` | Missing | Wraps binary stream with encoding |
| `open()` | Done | In `builtins` (not in `io`); exposes `TextIO`/`BinaryIO` |

### json

**Missing** as a stdlib-compat module. TPy has `tplib.json` (macro-based, typed
deserialization via `@model`). A `json` shim that mirrors CPython's API
(`loads`, `dumps`, `load`, `dump`) would need an untyped JSON value type and
`io` support.

Options:
- (a) Build `json` as a thin wrapper over `tplib.json` with a `dict[str, Any]`-like value type.
- (b) Keep `tplib.json` as the primary path and document it as the replacement.

| Item | Status |
|---|---|
| `loads`, `dumps` | Missing |
| `load(fp)`, `dump(obj, fp)` | Missing (needs `io`) |
| `JSONEncoder`, `JSONDecoder` | Missing |

### re

**Missing -- staged plan.** Biggest single missing module by impact.

Engine options considered: `std::regex` (zero deps, slow, ECMAScript syntax), **RE2**
(fast, linear-time, no backrefs/lookaround), **PCRE2** (closest semantics to CPython's
SRE, MIT-licensed, widely packaged), vendored SRE (~20k LOC, full compat).
CPython uses its own engine (SRE); no existing library is a drop-in match for
Python `re` semantics. For precedent: Codon uses RE2, RustPython uses the Rust
`regex` crate, GraalPy has its own TRegex. None literally ship SRE.

**Staged plan:**

- **Phase 1 (now):** Add `tplib.cppstd.re` -- a thin binding over `std::regex`.
  Ship `re` as a Python-facing facade on top. Includes:
  - a **syntax translator** (macro-driven, or pure function invoked from the
    facade) that rewrites Python regex syntax to ECMAScript where they differ:
    `(?P<name>...)` -> `(?<name>...)`, `(?P=name)` -> `\k<name>`, etc.
  - compile-time diagnostics for unsupported constructs: lookbehind,
    `\p{...}` Unicode categories, verbose mode inline flags, Unicode `\d\w\s`
    semantics that don't match ASCII.
  - clear documentation of the compat gap.
  Trade-off: std::regex is known-slow, but it's zero deps and unblocks the
  surface. Establishes a `tplib.cppstd.*` convention for other C++ stdlib
  bindings (`cppstd.chrono`, `cppstd.filesystem`, ...).
- **Phase 2:** Add `tplib.pcre2.re` -- PCRE2 binding. Make it the default
  backend for `re`; keep cppstd as a fallback for deps-free builds.
  Selection via the feature-flag system (see FEATURE_ROADMAP.md F8).
- **Phase 3 (if needed):** Vendor SRE for perfect CPython compat.
  Only if PCRE2's residual divergences actually bite users.

| Item | Status | Notes |
|---|---|---|
| `compile`, `match`, `search`, `findall`, `finditer`, `fullmatch` | Missing | Phase 1 target |
| `sub`, `subn`, `split` | Missing | Phase 1 target |
| `Match`, `Pattern` types | Missing | Phase 1 target |
| Flags (`IGNORECASE`, `MULTILINE`, `DOTALL`, ...) | Missing | Map to std::regex::flag_type in phase 1 |
| Named groups `(?P<name>...)` | Missing | Syntax translator in phase 1 |
| Backreferences `(?P=name)` | Missing | Syntax translator in phase 1 |
| Lookahead | Missing | std::regex supports it; phase 1 |
| Lookbehind | Missing | Not supported by std::regex or RE2; needs PCRE2 (phase 2) |
| `\p{...}` Unicode categories | Missing | Needs PCRE2 (phase 2) |
| Verbose mode `(?x)` | Missing | Translator in phase 1 (strip whitespace/comments) |

### collections

**Missing** as a module. Some building blocks already exist.

| Item | Status | Notes |
|---|---|---|
| `OrderedDict` | Missing | TPy already uses `tpy::ordered_map` for `dict[K,V]`; this would be a thin alias or subclass |
| `defaultdict` | Missing | Macro-friendly: store factory, synthesize `__getitem__` |
| `Counter` | Missing | Pure TPy over `dict[T, int]` |
| `deque` | Missing | Needs C++ backing (std::deque) with Python-like API |
| `namedtuple` | Missing | Would be a class macro; could desugar to @dataclass(frozen=True) |
| `ChainMap` | Missing | Pure TPy over list of dicts |
| `abc.*` (Sequence, Mapping, ...) | Partial | Some in `typing`; deeper introspection absent |

### itertools

**Missing** as a Python-surface module, but most primitives exist in
`runtime/cpp/include/tpy/itertools.hpp`: `chain`, `zip_longest`, `islice`,
`repeat`, `cycle`, `product`, `combinations`, `permutations`, `groupby`.

Existing builtins `enumerate`, `zip`, `reversed`, `map`, `filter` live in
`itertools.hpp` too (as builtins, not `itertools.*`).

| Item | Status | Notes |
|---|---|---|
| `count`, `cycle`, `repeat` | Missing | Need Python-visible wrappers |
| `chain`, `chain.from_iterable` | Missing | Wrapper |
| `compress`, `dropwhile`, `takewhile`, `filterfalse` | Missing | Pure TPy or wrapper |
| `islice` | Missing | Wrapper |
| `starmap`, `tee` | Missing | `tee` tricky (needs buffering) |
| `zip_longest` | Missing | Wrapper |
| `product`, `permutations`, `combinations`, `combinations_with_replacement` | Missing | Wrapper |
| `groupby`, `accumulate`, `pairwise`, `batched` | Missing | Wrapper / pure |

Key question: whether the module is pure TPy re-exporting C++ generators
(requires fixing "non-native functions in builtin modules can't be called
from user code" -- see TODO.md bugs) or `@native` thin shims.

### functools

| Item | Status | Notes |
|---|---|---|
| `reduce` | Missing | Pure TPy |
| `partial` | Missing | Closure support; may need a macro for typed partial |
| `partialmethod` | Missing | Descriptor-protocol heavy |
| `lru_cache`, `cache` | Missing | Cache keyed by arg tuple; needs hashable-tuple |
| `wraps`, `update_wrapper` | Missing | Metadata transfer; probably a no-op macro |
| `cmp_to_key` | Missing | Pure TPy |
| `singledispatch` | Missing | Runtime dispatch; use `@overload` instead |
| `total_ordering` | Missing | Class macro |
| `cached_property` | Missing | Needs descriptor support |

### random

Current: `lib/tpy/random.py` -- uses `std::rand/srand`. Non-reproducible
cross-platform; marked TODO for Mersenne Twister.

Target under the policy: the Mersenne Twister state machine itself is **pure
TPy** (same as CPython's `_randommodule.c` logic, but in .py). The only native
primitive is an OS entropy source for seeding when no explicit seed is given
(e.g. `os.urandom` via thin syscall binding). Everything else -- `randint`,
`choice`, `shuffle`, `sample`, `gauss`, etc. -- is pure TPy over the MT core.

Blockers for the pure-TPy MT: needs module-level mutable state (the RNG
state vector) that actually works across compilation units (see TODO.md bugs
around `native_module` facades and init-chain propagation). These are
language bugs worth fixing before doing the random module port.

| Item | Status | Notes |
|---|---|---|
| `random()` | Done | Uniform [0, 1) |
| `seed(n)` | Done | |
| `randint(a, b)`, `randrange` | Missing | Easy add |
| `choice(seq)`, `choices`, `sample` | Missing | Easy add |
| `shuffle(seq)` | Missing | Needs mutable-Span iteration |
| `uniform(a, b)`, `gauss`, `normalvariate` | Missing | Easy adds |
| `expovariate`, `betavariate`, `gammavariate`, `lognormvariate` | Missing | |
| `Random` class (per-instance state) | Missing | Needs object-held RNG state |

Tests: `random_basic`.

### struct

Current: `lib/tpy/struct.py` -- macro module. Format string must be literal.

| Item | Status | Notes |
|---|---|---|
| `unpack(fmt, data)`, `unpack_from` | Done | Little-endian only; big-endian emits MacroError |
| `calcsize(fmt)` | Done | Compile-time constant |
| `pack(fmt, *values)`, `pack_into` | Missing | Needs statement-expr or buffer-builder pattern (see module docstring) |
| `iter_unpack` | Missing | |
| `Struct` class | Missing | Would need per-class macro |
| Big-endian byte order (`>`, `!`) | Missing | Need `std::byteswap` wrappers in unsafe |
| Format codes `e` (f16), `P` (ptr), `n`/`N` (ssize_t/size_t) | Missing | |

Tests: `struct_unpack`.

### bisect

**Done.** `lib/tpy/bisect.py` is pure TPy generic over `Comparable`.

| Item | Status |
|---|---|
| `bisect_left`, `bisect_right`, `insort_left`, `insort_right` | Done |
| `bisect` (alias) | Missing | Trivial alias to `bisect_right` |

Tests: `stdlib_bisect`.

### enum

Current: `lib/tpy/enum.py` -- macro module.

| Item | Status | Notes |
|---|---|---|
| `Enum` base class | Done | |
| `IntEnum` | Done | |
| `auto()` | Done | |
| `StrEnum` | Missing | Python 3.11+ |
| `Flag`, `IntFlag` | Missing | Bitwise semantics |
| Member iteration (`for m in E`) | Partial | Works at runtime; check exhaustiveness |
| Lookup by value (`E(1)`) | Missing | |
| Lookup by name (`E["FOO"]`) | Missing | |
| `.name`, `.value` attributes | Done | |
| Functional API (`E = Enum("E", "A B C")`) | Missing | Rarely used |
| `@unique`, `@verify` decorators | Missing | |

Tests: integrated in json_model and enum test group.

### dataclasses

Current: `lib/tpy/dataclasses.py` -- macro module.

| Item | Status | Notes |
|---|---|---|
| `@dataclass(frozen, order)` | Done | |
| `field(default, default_factory)` | Done | |
| `asdict()`, `astuple()` | Done | Recurse into nested dataclasses, lists, dicts, tuples |
| `@dataclass(slots)` | N/A | All TPy records use inline storage |
| `@dataclass(eq=False, repr=False, init=False)` | Missing | Opt-outs |
| `@dataclass(kw_only)` | Missing | |
| `__post_init__` | Missing | |
| `InitVar[T]` | Missing | |
| `replace(obj, **kw)` | Missing | Would be a call macro |
| `fields(cls)`, `is_dataclass` | Missing | Needs compile-time or runtime reflection |
| `field(metadata=...)` | Missing | Currently ignored |
| `MISSING` sentinel | Missing | |

Tests: multiple `tplib/json_model_*` cases exercise @dataclass.

### typing

Current: `lib/tpy/typing.py` -- re-export from `tpy._typing`.

| Item | Status | Notes |
|---|---|---|
| `Protocol`, `runtime_checkable` | Partial | Protocols via `Protocol`; `@runtime_checkable` N/A |
| `Self` | Done | |
| `overload`, `override` | Done | |
| `Sized`, `Iterable`, `Iterator`, `Sequence`, `MutableSequence` | Done | |
| `Optional`, `Final` | Done | |
| `Callable` | Done | |
| `Literal` | Done | |
| `TypedDict`, `Unpack` | Done | |
| `Union`, `Annotated` | Missing | TPy uses `A \| B` syntax |
| `Any` | Missing | Type-system gap; would need dynamic dispatch |
| `TypeVar`, `Generic`, `ParamSpec`, `TypeVarTuple` | Missing | TPy uses PEP 695 `[T]` syntax |
| `ClassVar` | Missing | |
| `NewType` | Missing | Could be macro |
| `cast` | Missing | Open question: explicit upcast syntax |
| `get_type_hints`, `get_origin`, `get_args` | Missing | Runtime reflection |

### datetime

**Missing.** Class-heavy; natural fit for @dataclass-style TPy records +
native conversion helpers. Blocked by nothing architectural; medium effort.

| Item | Status |
|---|---|
| `date`, `time`, `datetime`, `timedelta`, `tzinfo`, `timezone` | Missing |
| `date.today`, `datetime.now`, `datetime.utcnow` | Missing |
| `strftime`, `strptime`, `isoformat`, `fromisoformat` | Missing |
| `timedelta` arithmetic | Missing |

### csv

**Missing.** Depends on `io` for `reader`/`writer` accepting file-like objects.

### base64

**Missing.** Pure-TPy candidate; `bytes` support is ready.

| Item | Status |
|---|---|
| `b64encode`, `b64decode` | Missing |
| `urlsafe_b64encode`, `urlsafe_b64decode` | Missing |
| `b16/b32` variants | Missing |

### hashlib

**Missing.** Under the policy: pure TPy first. MD5/SHA-1/SHA-2 are a few
hundred lines of bit-twiddling each and have well-known reference
implementations. A thin OpenSSL binding could be added later as an optional
backend (selected via F8) for throughput, but should not be a dependency.

Blockers for the pure-TPy impl: efficient fixed-width integer operations
(already present), `bytes`/`bytearray` support (already present), no other
known gaps.

| Item | Status | Notes |
|---|---|---|
| `md5`, `sha1`, `sha256`, `sha512` | Missing | Pure TPy; standard reference loops |
| `blake2b`, `blake2s` | Missing | Pure TPy; Python has its own impl too |
| `sha3_*`, `shake_*` | Missing | Later |
| `new(name)`, `algorithms_available` | Missing | |
| `.update`, `.digest`, `.hexdigest`, `.copy` | Missing | |

### argparse

**Missing.** Dynamic-type heavy; may benefit from a macro-driven redesign.

### logging

**Missing.** Needs module-level mutable state + handler/formatter architecture.

### configparser

**Missing.** Depends on `io`.

### urllib.parse

**Missing.** Pure-TPy candidate (no network dependency).

### heapq

**Missing.** Pure-TPy candidate over `list[T]`.

### copy

**Missing.** `copy`/`deepcopy` need generic copy intrinsic. TPy has `copy()` builtin
for value types; dataclass deep-copy would need macro-driven recursion similar to asdict.

### textwrap

**Missing.** Pure-TPy candidate.

### decimal

**Missing.** Large surface. Either bind `mpdecimal` or build pure TPy atop BigInt.

### fractions

**Missing.** Pure TPy over BigInt; small surface.

### statistics

**Missing.** Pure TPy (mean/median/mode/stdev/variance).

### pickle

**Blocked.** Needs runtime type info + `io`.

### shelve

**Blocked.** Needs pickle + dbm.

### inspect

**Blocked.** Needs runtime type/function introspection.

### asyncio

**Blocked** on async/await (roadmap G1).

### threading

**Blocked** on threading primitives.

### multiprocessing

**Blocked** on process spawning.

### subprocess

**Blocked** on process spawning.

### socket

**Blocked** on socket primitives.

### http.client

**Blocked** on socket + regex.

### urllib.request

**Blocked** on http.

---

## Adjacent: NumPy / Numeric Computing (parked)

Not CPython stdlib, but tracked here to keep it in sight. Not on the near-term
plan. A separate design doc (`NUMERIC_COMPUTING.md`) will spin off when this
becomes active.

**Key realization**: numpy itself is not wrappable. The Python object layer
(`np.array`, `arr.shape`, methods) and the C API (`PyArray_*`) are hard-coupled
to CPython's refcounting + GIL + PyObject machinery. A natively-compiled TPy
runtime can't host that.

**However, the low-level pieces people assume are "numpy" mostly aren't**:

| Layer | Wrappable from TPy? | What it actually is |
|---|---|---|
| Python object layer | No | Numpy's own, CPython-coupled |
| C API (`PyArray_*`) | No | Numpy's own, CPython-coupled |
| Ufunc dispatch / `NpyIter` broadcasting | Theoretically; not packaged as a standalone lib | Numpy's own |
| Element-wise kernel loops | Theoretically; template-generated, not shipped as reusable | Numpy's own |
| **BLAS / LAPACK / OpenBLAS / MKL** (linear algebra) | **Yes** | Upstream Fortran/C libs; ABI-stable; numpy just wraps them |
| **pocketfft** (FFT) | **Yes** | ~2k LOC C++, MIT, header-only, upstream repo exists; numpy vendors it |
| **sleef** (SIMD transcendentals) | **Yes** | Upstream C lib |

So the "wrap numpy's low-level building blocks" idea collapses into **wrap the
things numpy itself wraps** -- which are CPython-independent upstream libs we
can bind directly via the thin-binding pattern. Gets us the raw compute for
free.

**The container + broadcasting layer is ours to build** regardless. NumPy's
view/refcount model doesn't fit TPy's ownership/borrow model; a
reimplementation is mandatory.

### Candidate backends for the ndarray container

| Option | What it is | License | Fit |
|---|---|---|---|
| **xtensor** | Header-only C++ "numpy for C++, Python-free". `xt::xarray<T>`, broadcasting, lazy expressions, ufuncs, BLAS/LAPACK bindings | BSD-3 | **Strongest candidate** -- explicitly designed for this use case; matches numpy's API style; years of optimization work already done |
| **Eigen** | Header-only C++ linear algebra (matrices, decompositions) | MPL2 | Good for linalg specifically; less numpy-shaped than xtensor |
| **ArrayFire** | GPU-capable numeric library | BSD-3 | Relevant if GPU is ever in scope |
| **blitz++** | Older C++ numeric library with expression templates | Artistic/LGPL | Historical; less active than xtensor |
| **Pure TPy + `Span[T]` / `Array[T, N]`** | Build our own | -- | Maximum control; maximum work. Pressure-tests language features (multi-dim arrays, broadcasting via operator overloads, SIMD intrinsics, bounds-check elision) |

Most likely path: prototype **xtensor-backed** and **pure-TPy** options side-
by-side for the same small API subset (1D/2D dense, element-wise, reductions,
slicing), benchmark, pick.

### Strategy sketch

- **Phase 0 (now)**: parked. Keep this section up to date as thinking
  evolves; don't build.
- **Phase 1**: `tplib.ndarray` -- numpy-*style*, not numpy-compat. 1D/2D
  dense arrays, element-wise ops, reductions, basic slicing. Backend: TBD
  (xtensor vs own). Bind BLAS for linalg separately.
- **Phase 2**: N-D, broadcasting, fancy indexing, dtype system, FFT
  (pocketfft binding).
- **Phase 3 (maybe never)**: a `numpy`-named CPython-compat shim over
  `tplib.ndarray`. Document gaps honestly. Only justified if the shim gets
  close enough to real numpy that users can flip a switch.

### Other angles worth remembering

- **At compile time, macros run under CPython** -- so a macro like
  `@np_table` can freely use real numpy to compute lookup tables, validate
  shapes, or generate specialized kernels that get embedded in the binary.
  Narrow but real use case, and it's available today without any runtime
  numpy support.
- **Nobody has pulled off full numpy compat in a compiled Python.** Codon
  ships a partial numpy-like lib under its own namespace. PyPy's
  `micronumpy` is partial and most users fall back to CPython numpy via
  `cpyext`. Cython/Nuitka/mypyc don't reimplement -- they use real numpy
  because they run on CPython. The honest precedent is "numpy-style
  subset", not "numpy-compat".
