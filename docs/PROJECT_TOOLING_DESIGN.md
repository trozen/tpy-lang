# Project / Build / Dependency Tooling -- Design

**Status: exploratory.** Captures a design discussion (incl. a Codex
brainstorm cross-check), not a committed spec. No code exists yet. The goal
is to agree the shape before any `/tpy-add-feature` pass.

This doc is about the *user-facing* project layer -- how someone builds,
tests, and manages dependencies for a TurboPython **application or
library**. It is distinct from `BUILD_PIPELINE.md` (the compiler's internal
source -> C++ -> binary pipeline), which this layer drives.

## Goals

- **Single familiar command surface**, cargo-like: sync deps, build, test,
  run an app from one tool. A Python developer should feel at home.
- **Public-usable.** TurboPython is already on pypi.org and is meant for
  everyone, not just internal use. Nothing in the design may *require*
  internal infrastructure; internal indexes/workspaces are additive.
- **Reproducible builds** as far as we control them: pin what produces the
  output, fetch it on demand, no mandatory global install. Be honest about
  the boundary between what we can pin and what we cannot.
- **Reuse the Python ecosystem** where it already solves the problem
  (dependency resolution, locking, virtualenvs) rather than reinventing it.

## Non-goals (initially)

- Building our own dependency resolver / registry on day one (reuse uv;
  see "Migration").
- Building a native C/C++ package ecosystem (defer to existing managers;
  see "Native dependency plane").
- A workspace/multi-package model as the *only* supported shape (it is the
  recommended starter, not a requirement).
- Inventing a rich package-manager grammar inside `[tool.tpy]` (see "The
  stable contract" -- `[tool.tpy]` describes how to *compile and link*, not
  how to *resolve Python packages*).

## Guiding principle: Rust semantics + tooling, Python syntax + ergonomics

The north star for TPy is **a Rust-variant with Python syntax and
ergonomics**. TPy already is Rust-semantics-with-Python-syntax at the
language level (`Own[T]`, borrow/`readonly`, move semantics, `Box`/`Rc`/
`Weak`, and the two-tier error model -- exceptions for throw/catch vs
`@error_return` ~= Rust `Result`/`?`). This layer follows suit:

- **Default to Rust's tooling vocabulary** (cargo/rustup) so the mental
  model is consistent -- it settles most naming decisions by itself.
- **Keep Python idioms where Python's model is the better fit** -- notably
  the recoverable/exception channel (`try`/`except`, `@raises`).

Concrete Rust <-> TPy alignment:

| Rust | TPy (this design) |
|---|---|
| `cargo new/init/build/run/test/add/clean` | `tpx new/init/build/run/test/add/clean` |
| `cargo build --release` | `tpx build --release` |
| `[profile.dev]` / `[profile.release]` | `[tool.tpy.profiles.debug|release]` (kept `debug` -- universal term -- with cargo's `--release` CLI) |
| `[[bin]]`, workspaces | `[[tool.tpy.bin]]`, workspaces |
| `rust-toolchain.toml` (rustup pin) | `[tool.tpy] compiler = "..."` |
| `#[test]` / `#[should_panic]` | `@test` / `@should_panic` |
| `#[ignore]` / `#[bench]` | `@ignore` / `@bench` |
| `assert_eq!` / `assert_ne!` | `assert_eq` / `assert_ne` |
| `cargo test <substr>` (positional filter) | `tpx test <substr>` (positional; `-k` as alias) |
| `cargo test -- --nocapture` | `tpx test --nocapture` |
| (no clean attribute for expected `Err`) | `@raises(ExcType)` -- Python ergonomic for the exception channel |

## The stable contract: three files, three concerns

The durable user-facing contract is three separate artifacts, each owning
exactly one concern. **Do not overload one with another's reality** (the
Python lock must not carry C++ toolchain facts).

| File | Concern | Authored / generated | Owner |
|---|---|---|---|
| `pyproject.toml` | human intent: metadata, deps, `[tool.tpy]` build/link config | authored | PEP 621 + us |
| `pylock.toml` (PEP 751) | Python/TPy dependency resolution | generated | resolver backend (uv now) |
| `tpy-build.lock` | native deps + resolved toolchain identity + build-plan provenance | generated | porcelain |

`uv.lock` may exist as an interim implementation cache while uv is the
backend, but **PEP 751 `pylock.toml` is the portable contract** (uv's own
lock is uv-specific and exportable to `pylock.toml`, not portable by
itself). The third file, `tpy-build.lock`, is what makes "verified" builds
possible (see Toolchain).

Everything else -- uv, vcpkg, Conan, pkg-config, CMake/Ninja, compiler
discovery -- is a **replaceable backend behind this contract**.

## Tool topology

Three commands, mirroring rustc / cargo / rustup rather than overloading
one binary:

| Command | Role | Analogy |
|---|---|---|
| `tpyc` | the compiler (source -> C++ -> binary) | `rustc` |
| `tpy` | runner / REPL (`tpy file.py`, bare `tpy` = REPL) | `python` |
| **porcelain** (leaning `tpx`; alts `tpm`/`tpym`/`tupy`) | project/build/dep manager | `cargo` (+ a little `rustup`) |

The porcelain is a **separate command**, not `tpy build` subcommands -- this
keeps `tpy file.py` unambiguous and matches cargo-vs-rustc expectations.
"`tpy` growing package-manager subcommands" is on the list of mistakes that
are painful to undo.

Indicative subcommands:

```
tpx init | new        # scaffold pyproject + [tool.tpy]
tpx add | remove      # edit deps (delegates to the resolver backend)
tpx sync              # resolve + install into the project env
tpx build [--release] # native build (auto-syncs first)
tpx run               # build + run
tpx test              # build + run the native test harness
tpx toolchain doctor  # diagnose compiler/ABI/linker/cache
tpx clean
```

Conceptually there are **three independent versions**: the launcher (thin,
stable, installed once), the `tpyc` compiler (pinned per project, fetched),
and the C++ toolchain (constrained/external). Keep the launcher dumb and
forward-compatible; push determinism into the pinned compiler.

## Configuration layers

Three layers, each with a distinct role; they do not overlap:

1. **Source `# tpy:` directives** -- per-module, code-coupled facts that
   travel with the code: `link(...)`, `include(...)`, `native_module`,
   `macro_module`, `cpp_namespace(...)`. Parsed in `parse/parser.py`.
   *Unchanged by this design.*
2. **`pyproject.toml` `[tool.tpy]`** -- project-wide, stable build/link
   policy. **Scope: how to compile and link -- NOT how to resolve Python
   packages.** The Python plane is carried by standard mechanisms (PEP 621
   `[project.dependencies]`, dependency-groups, uv workspace metadata,
   `pylock.toml`), not by re-namespaced `[tool.tpy]` keys. (This narrows an
   earlier draft that proposed `[tool.tpy.sources]`/`[tool.tpy.workspace]`
   -- dropped, to avoid reinventing resolver grammar and to keep the schema
   small.)
3. **CLI flags** -- per-invocation overrides and ephemeral options (`-v`,
   `--dump-code`, one-off `--pcre2=system`).

**Precedence:** CLI flag > `[tool.tpy]` > built-in default. Directives are
orthogonal (module facts, not build policy).

The manifest is a **standard PEP 621 `pyproject.toml`** (read with stdlib
`tomllib`; no new dependency). Indicative `[tool.tpy]` (illustrative):

```toml
[project]
name = "hello"
version = "0.1.0"
dependencies = ["regex-tpy>=1"]      # Python/TPy plane -- standard PEP 621

[tool.tpy]
compiler = "turbopython==0.8.4"      # compiler pin as a PEP 508 string

[tool.tpy.toolchain]                 # deferred; see "C++ toolchain"
source = "system"                    # system | hermetic
verify = "warn"                      # off | warn | strict

[[tool.tpy.bin]]                     # targets -- canonical form (see "Targets")
name = "hello"                       # src/hello/main.py is auto-discovered too
path = "src/hello/main.py"

[tool.tpy.profiles.debug]            # profiles are named DATA from day one
opt-level = 0
debug = true

[tool.tpy.profiles.release]
opt-level = 3
debug = false

[tool.tpy.native]                    # native plane -- compile/link only
sources    = ["native/*.cpp"]
system-libs = ["zlib"]

[tool.tpy.native.vcpkg]              # tier-4 delegation
manifest = "vcpkg.json"
```

Pinning the compiler with a **PEP 508 requirement string** reuses
version-spec semantics instead of a bespoke field. Profiles are **named
data**, not flags baked into code paths -- this is hard to retrofit, so
model it from the start.

## Targets and workspace (fork #7 -- resolved)

Cargo's target model, minus one table. Target kinds a TPy package has:

| Kind | How declared |
|---|---|
| **bin** (executable) | convention `src/<pkg>/main.py` (default bin), or explicit `[[tool.tpy.bin]]` |
| **lib** (importable modules) | *implicit* -- no `[lib]` table; lib-ness comes from the `tpy.libraries` marker (#5) + importable modules. A package may be both lib and bins. |
| **test** | auto from `tests/` + `@test` (#3); not a `[[bin]]` |
| **ext** (CPython-importable extension) | future; see "Forward-looking" -- a `.so`/`.pyd` built via a PEP 517 backend, importable from normal CPython |
| example / bench | later (cargo-style) |

The not-needing-a-`[lib]`-table is the one simplification over cargo.

**Entry form (`[[tool.tpy.bin]]` canonical):** `name` + **`path`** (a module
file whose top level / `main()` is the entry -- matches how `tpy file.py`
runs a file today and cargo's `src/main.rs`). Optionally `entry =
"pkg.module:func"` for the function-entry case. We do **not** reuse standard
`[project.scripts]` -- that means "install a Python console-script wrapper,"
a different thing from building a native binary.

```toml
[[tool.tpy.bin]]
name = "atlas"
path = "src/atlas/main.py"

[[tool.tpy.bin]]
name = "report"
path = "src/atlas/report.py"
```

**Auto-discovery (MVP):** only the default `src/<pkg>/main.py` -> a bin named
after the package. A cargo-style `src/bin/*.py` auto-multi-bin convention is
deferred; use explicit `[[bin]]` for extra binaries for now.

**Workspace:** resolution rides the uv workspace
(`[tool.uv.workspace] members`, Python plane). Build/run selection mirrors
cargo:

```
tpx build                 # all bins in the package (all members at ws root)
tpx build --bin atlas     # one bin
tpx build -p regex        # one workspace member (--package)
tpx run --bin atlas       # run a specific bin
```

**Layout convention:**

```
atlas/
  pyproject.toml
  src/atlas/__init__.py     # library modules (importable; lib target)
  src/atlas/main.py         # default bin "atlas" (auto)
  src/atlas/report.py       # extra bin via [[bin]]
  tests/test_*.py           # test target (auto)
```

## Dependency model -- two planes, separate namespaces

Persistent rule: the Python/TPy plane and the native C/C++ plane have
**separate namespaces and resolution** -- do not express a C library as a
Python dependency, and do not hand-maintain a dependency's native link
metadata in the root app. They are *not* fully disjoint, though: they
**merge at build-plan time** (plane 1 feeds plane 2 a native worklist; see
"How the planes interact"), with a **defined precedence** (app-level native
config overrides a dependency's preference). "Separate planes" means
separate namespaces + separate resolvers, not "never interact."

### Plane 1 -- TPy / Python dependencies (resolver backend, standards-based)

A TPy library is valid Python source, so it ships as an ordinary Python
source distribution and the ecosystem solves the hard parts:

- **Declaration:** PEP 621 `[project.dependencies]` + dependency-groups.
  The consuming app does **not** categorize its deps with any TPy-specific
  grammar (fork #5 = compile/link-only `[tool.tpy]`).
- **Resolution + lock + fetch + venv:** a **resolver backend** (uv today),
  reached only through a small internal interface so it can be swapped:

  ```
  resolve(manifest, groups, indexes) -> python_lock     # pylock.toml
  sync(lock, environment, groups)    -> environment
  run_in_env(environment, argv)
  ```

  User-facing files must not mention uv except optional passthrough config.
- **Compilation needs only source**, not a wheel: the installed `.py` lands
  in the venv `site-packages`; `modules/resolver.py` adds that to its search
  path and builds from source.

#### Differentiating TPy packages from ordinary Python packages (fork #5)

The consuming app stays vanilla PEP 621; **each package self-declares what
it is**, and the porcelain/compiler discovers it. The build distinguishes
three kinds, each known without the app categorizing anything:

| Kind | Used | Known via |
|---|---|---|
| Runtime TPy lib | compiled into the binary | compiler follows the import graph and compiles the source it finds |
| Compile-time tool (macro / plugin) | runs under CPython *during* compilation | the package's own `# tpy: macro_module` / `native_module` directives |
| Dev tool (test harness, formatter) | not in the build | lives in `[dependency-groups].dev`, never `[project.dependencies]` |

A TPy library marks itself with **standard Python self-declaration
mechanisms** (the marker, not the name, does the differentiation):

```toml
# a TPy library's own pyproject.toml
[project]
name = "regex-tpy"
classifiers = ["Framework :: TurboPython"]   # human-facing tag (a la Framework :: Django)

[project.entry-points."tpy.libraries"]       # machine-queryable from installed metadata
regex = "regex"                              # dist name -> import package (handles name mismatch)
```

- The `tpy.libraries` **entry-point group** is the primary marker: present
  in installed dist metadata (queryable via `importlib.metadata`, the same
  way pytest/flake8 plugins self-register) and doubles as the
  **dist-name -> import-name map** the compiler needs.
- The `Framework :: TurboPython` **classifier** is the secondary,
  human-facing tag (PyPI filtering); costs nothing.

**Non-TPy package in `[project.dependencies]`:** warn by default (it may be
a dep of a compile-time tool that legitimately runs under CPython); **hard
error only when reached on the runtime import graph.**

#### Distribution / package sources (fork #8 -- start simple)

Decision: keep distribution dead-simple early; grow into an index only when
the ecosystem demands it. Three routes, in order of expected early use:

1. **Path / git-submodule (day-one, must work from the start).** Early
   adopters vendor a lib's source into the tree (`git submodule` under e.g.
   `vendor/`) and point the compiler at it via `[tool.tpy] lib-paths`
   (equivalent to `-L`, and legitimately compile/link config, not package
   resolution -- consistent with fork #5):

   ```toml
   [tool.tpy]
   lib-paths = ["vendor/regex", "vendor/json"]
   ```

   No index, no uv ceremony, no packaging metadata required -- just source
   on a search path. The **submodule commit SHA is the pin** (reproducible
   by construction). Cost: transitive deps are vendored manually; when that
   hurts, graduate to a uv path/workspace dep (the same source, now with
   metadata) or an index. **Lock caveat:** these deps live *outside*
   `pylock.toml` -- to keep the three-contract story honest, `tpy-build.lock`
   must record each `lib-path` source root + its submodule SHA (or the build
   must explicitly declare submodule deps as outside its reproducibility
   guarantee). Do not silently leave them unrecorded.
2. **Public PyPI.** Uploading a TPy lib is fully supported; **suggest the
   `-tpy` suffix** convention to avoid name collisions on the shared
   namespace (normal Python practice -- `pytest-*`, `django-*`). Consumed
   via standard `[project.dependencies]`.
3. **Dedicated TPy index (e.g. `pi.tpy-lang.org`) -- deferred.** Solves the
   suffix/collision problem (clean names like `regex`) and enables curation,
   but it makes us a registry (uptime + supply-chain ops). Revisit when
   submodules + PyPI stop scaling. Architecturally it is **just index
   configuration** -- a config change behind the unchanged resolver-backend
   interface, not a redesign. If/when added: a **public** TPy index is fine
   to default to (does not violate "usable by everyone"); only **private**
   company indexes (e.g. `pypi.mkmc.pl`) must stay strictly additive. With
   multiple indexes in play, pin the TPy index **explicitly** to avoid
   dependency-confusion shadowing.

The compiler (`turbopython`) stays on **public PyPI** regardless, so
`uvx turbopython` bootstrap works for any stranger.

### Plane 2 -- Native C/C++ dependencies (compiler registry + external managers)

Tiers; commit to the easy ones first:

1. **Managed vendored libs** -- the pcre2 pattern (vendored sources +
   facade header + `link(managed=True)`, backend mode in
   `[tool.tpy.native]`). The compiler already does this.
2. **System libs** -- `link("foo")` -> `-lfoo`, discovered via `pkg-config`.
3. **User C/C++ sources in the project** -- the `native_types.cpp`
   companion pattern, compiled and linked with the build.
4. **Fetch-and-build external native libs** -- **delegate early to
   vcpkg/Conan** (require a manifest/profile, e.g.
   `[tool.tpy.native.vcpkg] manifest=`), do not invent recipes.

A native dependency has its **own identity, separate from the Python
package name**:

```
python package   regex-tpy
native lib id     pcre2
mode              bundled | system | vcpkg | conan | none
link surface      include dirs, libs, compile defs, source files
```

### How the planes interact at build time

Explicit order (the porcelain drives this):

1. Resolve/sync the Python/TPy environment (plane 1).
2. Locate the pinned `tpyc`.
3. Discover TPy source deps from the environment.
4. **Aggregate native requirements** declared by those deps' own
   `[tool.tpy.native]` + `link(...)` directives (plane 1 *feeds* plane 2 a
   worklist).
5. Resolve native deps (plane 2).
6. Generate C++.
7. Compile user + generated + runtime + native sources; link.

**Conflict policy:** if two TPy packages request incompatible native modes
or versions of the same lib, **fail at sync/build-plan time, before
codegen** -- never let it surface as a linker error. App-level config
overrides a dependency's preference.

**ABI rule + trust tiers (one-way hazard).** The aspiration: build native
code from source with the **same toolchain as the app** (libstdc++ vs
libc++, the C++11 ABI flag, the target triple must match). But this cannot
hold uniformly -- **tier-2 system libs (`-lfoo`/pkg-config) are prebuilt
with unknown compiler/stdlib/flags/ABI.** So model native deps as **trust
tiers**, not one rule:

- tiers 1 + 3 (vendored / user sources): built from source with the app's
  toolchain -- ABI-safe, recordable in `tpy-build.lock`.
- tier 2 (system libs): ABI is environment-trusted. In `verify=strict` /
  hermetic mode they must be **allowlisted with recorded ABI metadata**, or
  rejected -- otherwise `tpy-build.lock` looks more authoritative than it is.
- tier 4 (vcpkg/Conan): ABI per the external manager's triplet/profile;
  record it.

Source-only is the safe initial commitment; a binary-artifact cache is a
*later, additive* acceleration keyed by toolchain. Allowing prebuilt binary
*distribution* of TPy libs early would inherit ABI hell permanently.

## Toolchain pinning and bootstrap

Two toolchains affect the output; they pin very differently.

### TPy compiler -- pinnable and fetchable (strong story)

`tpyc` is a PyPI package, so a project pins its own compiler version
(`[tool.tpy] compiler = "turbopython==X"`) and the porcelain fetches that
exact version into a project-local / cache-managed env and **re-execs the
pinned version** if needed (Gradle-wrapper / rustup analogy). Reproducible,
no global install, multiple versions coexist per machine.

**Apps pin exact; libraries declare a compatibility *range* (fork #13).** An
exact pin is right for an *application* (it owns the lock), but wrong as the
*only* mechanism for a *library*: if every lib exact-pins, workspaces
conflict; if libs say nothing, builds fail late at compile. Mirror Rust's
split -- `rust-toolchain.toml` (app pin) vs `rust-version`/`edition`
(library compatibility): a library declares a **minimum compiler / language
version (and eventually an edition)**, and the *app* resolves those
constraints down to one exact pin. The exact-`==` form is the app shape; a
range/min form is the library shape.

### C++ toolchain -- reproducibility (cannot be pip-pinned)

**Status: options recorded, decision deferred.** This is further out than
the MVP; capturing the thinking so the model is ready when it matters
(notably the HFT repeatable-binary case). We do not ship or `pip install`
GCC/Clang, so we cannot "pin" it the way we pin `tpyc`. Be precise about
*what guarantee* applies.

The honest promise either way: **deterministic generated C++ from pinned
`tpyc`; binary repeatability only when the external toolchain identity is
fixed.** The toolchain identity also feeds the object-cache / ccache key, so
recording it makes cache invalidation explicit.

**Two orthogonal axes (the cleaner model).** The earlier three "modes"
(`portable` / `verified` / `hermetic`) conflated two separate questions.
They are better expressed as:

```toml
[tool.tpy.toolchain]
source = "system"   # system | hermetic   -- where the compiler comes from (provisioning)
verify = "warn"     # off | warn | strict  -- drift enforcement vs tpy-build.lock
```

- `source = hermetic` *provisions* a pinned toolchain (container / Nix /
  pixi / vcpkg binary cache / Conan profile). Caveat: `FROM ubuntu + apt
  install clang` is **not** hermetic -- pin by digest *and* package version,
  or it floats.
- `verify` *proves* the build used the recorded toolchain; it does not make
  any toolchain available (if absent, it just fails).

| Use case | source | verify | (old name) |
|---|---|---|---|
| Local dev | system | warn | -- |
| Casual reproducibility | system | strict | `verified` |
| HFT release / CI | hermetic | strict | `hermetic`+`verified` |

**Recommendation (when we get here):** keep dev at `system`+`warn`;
recommend **`hermetic`+`strict`** for HFT release (provision via **pixi** or
a **digest-pinned container**; Nix only if already in use). Default to
**behavioral/performance reproducibility** -- bit-identical is a further,
separate effort (`__DATE__`/`__TIME__`, `-ffile-prefix-map` for build paths,
LTO non-determinism, build IDs) offered as a best-effort "reproducible-build
flags" bundle. For HFT, perf+audit repro is usually what matters;
bit-identical is the regulatory/incident nice-to-have.

`tpy-build.lock` is the **build closure** -- it ties the three contracts
together and is what `verify` compares against:

```toml
[meta]
lock_version = 1
generated_by = "tpx 0.3.1"

[tpy]
compiler = "turbopython==0.8.4"; resolved = "0.8.4"
runtime_hash = "sha256:..."; default_int = "Int64"

[python]
pylock = "pylock.toml"; pylock_hash = "sha256:..."   # correlate, don't duplicate

[toolchain]
source = "hermetic"; provisioner = "pixi:env-hash=..."   # or container digest / nix flake ref
kind = "clang"; version = "18.1.8"; target = "x86_64-unknown-linux-gnu"
stdlib = "libstdc++"; stdlib_version = "13.2"   # ABI-critical
linker = "lld"; cxx_standard = "23"
flags = ["-O3", "-ffile-prefix-map=..."]

[native.pcre2]
mode = "bundled"; version = "10.44"; source_hash = "sha256:..."
[native.zlib]
mode = "pkg-config"; version = "1.3.1"

[outputs]
generated_cxx_hash = "sha256:..."   # proves codegen determinism
binary_hash = "sha256:..."          # only meaningful under bit-identical flags
```

**Sub-decisions to settle later (recorded, not decided):**

- *Verify granularity:* exact (`18.1.8 != 18.1.9` fails) is brittle (patch
  bumps break every build); recommend recording exact but comparing at
  `kind + major.minor + stdlib-major` by default, with `verify=strict`
  tightening to exact. HFT audit may want exact-fail regardless.
- *Commit policy:* commit `tpy-build.lock` for **app/binary targets**
  (provenance + drift guard); **not** for pure **libraries** (the consuming
  app pins the toolchain).
- *Target tier:* is **bit-identical** an actual requirement, or is
  **behavioral/perf + audit** repro enough? Sizes the reproducible-flags
  effort.

`tpx toolchain doctor` is first-class: it reports which compiler was
found, which was required, the active ABI/linker/pkg-config paths, and how
to override -- to cut "works on my machine" support load.

### Bootstrap ("build without installing tpy first")

Primary, portable story:

```
uvx tpx@latest run
```

`tpx` then reads `[tool.tpy].compiler`, provisions that exact version, and
re-execs. The prerequisite collapses to essentially *"have uv."* A
committed thin wrapper (Gradle-wrapper style, with a download **checksum**)
is a **secondary** convenience considered later, not the primary mechanism.

**Failure modes to handle explicitly:**

- The first `tpx` is only a launcher -- it must read old manifests and
  fetch the pinned compiler; **never silently use the launcher's embedded
  compiler** when the manifest pins a version.
- `uvx tpx` (no version) uses uv's tool-cache semantics, so the **project
  pin must win** over whatever launcher started.
- No network + pinned compiler not cached -> concrete cache/download error.
- Private indexes passed through explicitly and visibly; PyPI stays default.
- **No C++ compiler present:** the TPy half is zero-install, but the C++
  half needs a system toolchain. A fresh machine with no clang fails at the
  C++ step -- give a crisp diagnostic and offer `--emit-cxx-only` (works
  with no compiler).
- **Windows/MSVC unsupported** (no GCC statement-expressions) -- error
  clearly and steer to clang/MinGW, do not fail cryptically at the C++ step.

## Build profiles

Cargo-style, mapping to C++ flags; profiles are named data in
`[tool.tpy.profiles.*]`:

- `tpx build` -> debug (`-O0 -g`); `--release` -> release (`-O2`/`-O3`).
- Per-profile artifact dirs under a `target/`-like root, namespaced by
  profile + toolchain id; existing `__tpyc__/`, the content-addressed
  stdlib object cache, and ccache live underneath.

Whether a profile may change *codegen/semantics* (e.g. elide runtime safety
checks in release) is **deferred and must stay explicit**: `--release` =
perf/debug flags only; any safety-check elision is a *separate, loud, named*
opt-in, never implied by the profile. Silent semantic change is very hard to
walk back.

## Test harness (fork #3 -- resolved)

No CPython-parity requirement for user tests, and **no pytest emulation**
(fixtures/plugins/introspection fight a static compiler). Compile-time
discovered, cargo-`#[test]`-style.

**Two failure channels, two decorators.** TPy has two runtime failure
channels, and they line up with the two languages' idioms (see the guiding
principle) -- so each channel uses the term of the language that owns it:

| TPy channel | Maps to | Decorator | Isolation |
|---|---|---|---|
| Catchable exception (`raise`/`except`; `assert` raises `AssertionError`) | Python / Rust `Result::Err` | `@raises(IndexError)` | in-process catch |
| `tpy_panic` (abort, unrecoverable) | Rust `panic!` | `@should_panic` / `@should_panic(expected="...")` | `--isolate` (child proc) |

The two decorators are not competing vocabularies -- they target different
channels. `@should_panic` matches Rust exactly (incl. the `expected=`
form); `@raises` gives a Python ergonomic for the exception channel, where
Rust has no clean attribute analog.

```python
from tpy.testing import test, raises, assert_eq

@test
def test_add():
    assert add(2, 3) == 5
    assert_eq(add(0, 0), 0)        # nicer failure message than bare assert

@test
@raises(IndexError)                # recoverable exception -> caught in-process
def test_oob():
    xs = [1]
    _ = xs[3]
```

**Model:**
- **In-process by default:** the runner wraps each `@test` in a catch,
  records pass/fail, **continues after failures** (the exception channel
  unwinds, so this is cheap -- the Rust unwinding-panic model). `--isolate`
  (process-per-test) is opt-in for abort-level `tpy_panic`/segfaults.
- **Binary granularity: one test binary per package** (cargo's per-crate
  model; the workspace unit). Integration tests under `tests/` may each
  become their own binary later.
- **Discovery: decorator-only** (`@test`), no `test_*` naming magic.
- **Mechanism (lean):** build-time collection -- the whole-program compiler
  gathers decorated functions and synthesizes the runner `main` (vs static
  self-registration). The decorators are **compiler-recognized markers from
  `tpy.testing`**, not arbitrary user decorators -- *validate this against
  the macro/decorator system before committing to the exact API.*
- Provides filtering, captured stdout/stderr (shown only on failure),
  expected-exception/panic, `@ignore`, `@bench` later. No fixtures/plugins.
  (In-process *parallelism* waits on threading, v3+; MVP shards per binary.)

## Migration: uv now, our own resolver later

Safe because uv is an *implementation detail* behind the resolver backend
interface and the user-facing artifacts are standards-based:

- Manifest = PEP 621 `pyproject.toml` + `[tool.tpy]`.
- Lock = PEP 751 `pylock.toml` as the contract (interim `uv.lock` cache).
- The backend calls `uv lock`/`uv sync`/`uv run`/`uv export` today; the
  same interface later calls our resolver, changing nothing a user sees.
- Coupling cost: depend on uv (a pip-installable wheel), shell out to the
  CLI, parse only machine-readable output, isolate every uv call in one
  module (the single swap point), pin uv's version.

Bad boundaries to avoid: reading uv's private lock schema for semantic
decisions; requiring `[tool.uv]` for normal behavior; encoding TPy-specific
meaning in Python extras unless those extras are meaningful to Python
tooling too.

## Reproducibility model (summary)

| Layer | How pinned | Reproducible? |
|---|---|---|
| TPy deps | PEP 621 + `pylock.toml` | yes |
| `tpyc` compiler | `[tool.tpy] compiler=` (PEP 508) + fetch from PyPI | yes |
| Generated C++ | deterministic from pinned `tpyc` + settings | yes |
| C++ toolchain | `source` (system/hermetic) x `verify` (off/warn/strict) -- *deferred* | best-effort (system) / fixed (hermetic) |
| Native libs | managed (1), system (2), user src (3), external mgr (4) | tiers 1+3 yes; 2+4 environment-dependent |

## Forward-looking: extension points the tooling must not preclude

Each has its own deep design (out of scope here); the point is to reserve
the tooling hooks now so the design does not block them.

### CPython interop -- an `ext` target + a build-backend mode

"A pure-Python project includes TPy modules compiled to native, callable
from normal CPython" is the **Python packaging flow**, not the executable
flow. Hooks:

- A target kind **`ext`** -- a CPython-importable extension (`.so`/`.pyd`).
- TPy as a **PEP 517 build backend** (or hatch/setuptools plugin):
  `[build-system] build-backend = "turbopython.build"`; the project marks
  which modules are TPy; **`uv build`/`pip wheel` compile them into the
  wheel**. Downstream CPython users `pip install` + `import` -- no TPy
  install required by consumers.

So there are **two complementary tooling modes**, both reusing the Python
ecosystem:

- **app mode** -- `tpx build` -> native executable (porcelain-owned);
- **extension/lib mode** -- TPy as a build backend -> wheels with compiled
  `.so` via standard `uv build` (porcelain optional).

Implication: do **not** assume the porcelain is the only entry point;
`uv build` must work for `ext`/`lib` packages. The interop *semantics*
(signatures exposed to CPython, GIL, refcounting, marshalling) are designed
in [`docs/CPYTHON_INTEROP.md`](CPYTHON_INTEROP.md); this distribution form
also means a TPy package may ship a
compiled `.so` (consumed by CPython) vs TPy source (compiled from source by
downstream TPy) -- two valid distribution shapes.

### Pluggable codegen backend (C++ now, native later)

The C++ stage is the *current* backend, not an invariant.

- `[tool.tpy] backend = "cpp" | "native"`; the two-phase "generate C++ ->
  build binary" generalizes to **"emit -> link"** (`--emit-cxx-only`
  becomes `--emit = cpp | obj | bin`).
- The toolchain/provenance model (#2) is **backend-relative**: cpp ->
  C++ compiler + stdlib; native -> LLVM/codegen + linker. `tpy-build.lock`
  records `backend` + *its* toolchain, not "clang" unconditionally.

Implication: phrase the toolchain and build phases backend-neutrally; never
hardcode a C++ stage in the porcelain.

### Additional frontends via manifest config

The frontend-plugin system already exists (`FRONTEND_PLUGIN_DESIGN.md`;
`--dsl-plugin`/`--dsl-opt`; the test harness's `options.json`
`plugin`/`dsl_opts`). Promote it to manifest config:

```toml
[tool.tpy.frontend]
plugin = "pascal-tpy"            # a normal Python-plane dependency, resolved via uv
dsl_opts = { dialect = "fpc" }   # mirrors options.json dsl_opts
```

The plugin is itself a **Python-plane dependency** (pulled via the resolver
backend, referenced by name) -- ties the two planes cleanly, and is
compile/link config, so it fits `[tool.tpy]`'s scope (no #5 violation).
This one is near-term and low-risk.

## Possible extensions (out of scope, enabled by the design)

- **CMake / build-system export** as the native/link surface (cli.py
  already generates CMake sources): lets users' existing C++ build/IDE drive
  the native build and is the natural vcpkg/Conan integration point.
- **Generated-C++ as a distributable artifact** for non-TPy C++ consumers
  (TPy as a C++-library authoring tool) -- the decoupled generate/build
  phase enables it.

## Open questions / forks

- **#5 RESOLVED.** `[tool.tpy]` stays compile/link-only; deps ride standard
  PEP 621; packages self-declare TPy-ness via the `tpy.libraries`
  entry-point + `Framework :: TurboPython` classifier. See "Differentiating
  TPy packages" above.
- **#8 RESOLVED (start simple).** Day-one: path / git-submodule via
  `[tool.tpy] lib-paths`. PyPI uploads supported with a suggested `-tpy`
  suffix. Dedicated index (`pi.tpy-lang.org`) deferred (just index config
  when needed). See "Distribution / package sources" above.
- **#3 RESOLVED.** Test harness: two-channel model (`@should_panic` for
  `tpy_panic` aborts, `@raises(Exc)` for the exception channel), one binary
  per package, in-process by default + `--isolate` opt-in, decorator-only
  discovery via build-time collection. See "Test harness". Aligned to the
  Rust tooling vocabulary (guiding principle).
- **#7 RESOLVED.** Targets = cargo model minus an explicit `[lib]` (the
  marker covers lib-ness); `[[tool.tpy.bin]]` with `name`+`path` canonical
  (`module:func` optional), default-main auto-discovery only, uv workspace
  for resolution, cargo-style `--bin`/`-p` selection. See "Targets and
  workspace".

Still open:

1. **#1 LEANING `tpx`** (alts `tpm` / `tpym` / `tupy` -- all TurboPython
   initialisms or a turbo+python portmanteau; `tpx` echoes `pipx`/`uvx` and
   is collision-free locally). Not locked; verify PyPI/GitHub availability
   before committing.
2. **DEFERRED (options recorded).** C++ toolchain reproducibility: the
   2-axis `source` x `verify` model, recommended `hermetic`+`strict` for HFT
   release, and the `tpy-build.lock` schema are written up under "C++
   toolchain". Sub-decisions (verify granularity, commit policy,
   bit-identical vs behavioral tier) left open until this is on the roadmap.
4. **DEFERRED (recommended).** Lock-format timing. Recommendation:
   `pylock.toml` (PEP 751) is the *stated contract* regardless; ride
   `uv.lock` as an interim implementation cache and have the resolver
   backend *export* `pylock.toml` until PEP 751 generate/consume tooling is
   mature, then make it primary. Open: exact switchover timing (tracks
   ecosystem maturity, not our schedule).
6. **DEFERRED (recommended).** Native tier-4 (fetch-and-build external C/C++
   libs). Recommendation: lean **vcpkg** as the first integration target
   (manifest mode, CMake-centric, good cross-platform story), with **Conan**
   as the documented alternative; `pkg-config` stays tier-2 only (system
   libs, not fetch-and-build). Both sit behind the native-plane interface so
   the choice is swappable. Open: which to implement first when tier-4 is on
   the roadmap.

Forward-looking (hooks reserved; own deep designs -- see "Forward-looking"):

9.  CPython interop: `ext` target kind + TPy as a PEP 517 build backend
    (`uv build` -> wheel with compiled `.so`). Interop semantics designed in
    [`docs/CPYTHON_INTEROP.md`](CPYTHON_INTEROP.md); the `.so` build-output
    mode + abi3 wheel is the tooling half this doc owns.
10. Pluggable codegen backend (`cpp` now, `native` later): keep toolchain
    and build phases backend-neutral; record `backend` in manifest + lock.
11. Frontend plugins configured in `[tool.tpy.frontend]` (plugin = a
    Python-plane dependency). Near-term, low-risk.

Raised by review (need a first-class answer before publishing conventions):

12. **Dependency roles.** PEP 621 does not separate cargo's runtime / build
    (host) / dev / target roles. A compile-time macro/plugin runs on *host*
    CPython; a runtime TPy lib compiles for the *target*; native deps are
    target/toolchain-specific. The current single-env + "warn on non-TPy
    dep" model is too coarse (a plugin's ordinary Python deps are legitimate
    non-TPy host deps, not a warning). Needs a real role model, and it is a
    prerequisite for cross-compilation.
13. **Library compiler-compat range** (vs app exact pin) -- see "TPy
    compiler". Libraries declare min compiler/language version + eventual
    edition; apps resolve to an exact pin.

## Known weaknesses / gaps (from skeptical review)

Recorded so they are not mistaken for "covered." Beyond forks #12/#13:

- **Cargo-UX surface under-specified.** Must be pinned down at MVP:
  `target/` artifact layout + cache boundaries (project target dir vs the
  compiler env vs uv env vs native build dir vs ccache/object cache);
  `tpx clean` semantics (what it deletes); `tpx run -- <args>` passthrough;
  env vars; diagnostics verbosity; C++/linker error mapping back to TPy
  source.
- **Feature flags / conditional compilation.** No `cfg`-equivalent exists;
  cargo leans on features heavily. Open whether TPy needs optional deps /
  conditional compilation and how they'd be expressed.
- **Wheel-may-not-ship-source.** "Compile from source" assumes the installed
  distribution *contains* the `.py`; a source-less or partially-compiled
  wheel breaks it. The dist->import + wheel-contents coupling is part of the
  TPy build contract (a Python-specific problem cargo never has).
- **Test mechanics** beyond the resolved model: how test modules are
  discovered/imported, test-only deps (dependency-groups), native fixtures,
  and workspace package selection.

**Riskiest single assumption (name it to watch it):** *"a TPy library is
just an ordinary Python package installed into a uv-managed venv, plus light
self-identification metadata."* That one bet silently carries dependency
resolution, source discovery, macro/plugin execution, reproducibility,
publishing, and cross-compilation. If it proves too thin, we retrofit a real
TPy package contract -- ideally before users publish packages, not after.

## Suggested phasing

1. **Conventions on uv (no new tool):** document `pyproject.toml` +
   `[tool.tpy]`, `uv sync` + `uv run tpyc`. Validates the manifest shape
   with zero build effort.
2. **Porcelain MVP:** `new`/`build`/`run` over `[[tool.tpy.bin]]`, debug +
   release, resolver backend = uv, `portable` mode. **Path / git-submodule
   deps via `[tool.tpy] lib-paths` work from here** (no index required).
3. **Test harness:** `test` + the `tpy.testing` convention.
4. **Toolchain pinning + bootstrap:** per-project `tpyc` version, `uvx`
   primary, `tpy-build.lock` + `verified` mode + `toolchain doctor`.
5. **Dependency shapes (PyPI side):** `[project.dependencies]` from PyPI
   (`-tpy` suffix convention), then workspace / private index / git path
   deps via the resolver backend.
6. **Native plane:** drive tiers 1-3; design tier-4 (vcpkg/Conan)
   delegation; aggregate deps' native requirements with conflict-at-plan.
7. **(Later) own resolver / `hermetic` mode / binary cache / CMake export**
   behind the unchanged manifest/lock contract.
