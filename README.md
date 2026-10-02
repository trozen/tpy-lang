# TurboPython: a Python-to-C++ compiler

**TurboPython** (TPy, package `tpy-lang`) compiles statically typed Python
through C++ to a native binary. The result has no interpreter, no garbage
collector, no automatic reference counting and no GIL; an ownership model
provides deterministic, compile-checked memory management. Source files
stay valid Python, so existing editors and linters read them, but
TurboPython is not a drop-in replacement for CPython.

[Website](https://tpy-lang.org) |
[Documentation](https://tpy-lang.org/docs/) |
[Examples](https://github.com/trozen/tpy-examples) |
[Release notes](https://github.com/trozen/tpy-lang/blob/master/RELEASE_NOTES.md)

> **Status: early development.** The core language compiles and runs real
> programs, but some ordinary Python constructs are still rejected, the
> standard library is a subset, and known bugs can produce wrong results
> silently. See [what works](https://tpy-lang.org/docs/compatibility/)
> before relying on it.

## Example

The source is Python with type annotations and two TurboPython types,
`int32` and `Own`:

```python
from dataclasses import dataclass
from tpy import Own, int32

@dataclass
class Stats:
    count: int32
    total: int32
    longest: int32

def measure(words: list[str]) -> Own[Stats]:
    total, longest = 0, 0
    for w in words:
        total += len(w)
        longest = max(longest, len(w))
    return Stats(len(words), total, longest)

s = measure("the quick brown fox jumps over the lazy dog".split())
print(s.count, s.total, s.longest)
```

`tpy` compiles and runs it; `tpyc -b` leaves a standalone binary:

```console
$ tpy stats.py
9 35 5
$ tpyc -b stats.py
$ ./__tpyc__/stats.d/release/stats
9 35 5
```

`int32` is a fixed-width integer. Integer literals and `len()` are `int32`
by default, so `total` and `longest` need no annotation, and arithmetic on
them is checked: an overflow stops the program with an error instead of
wrapping. Plain `int` stays arbitrary-precision, as in Python. `Stats`
becomes a plain struct of three 32-bit integers and the loop runs over the
list's storage directly: nothing is heap-allocated per object,
reference-counted or garbage-collected. `Own[Stats]` says the function
hands its result to the caller; see
[Ownership](https://tpy-lang.org/docs/guide/ownership/).

## Installation

Requirements: **Python 3.12+** and a **C++23 compiler** on the PATH
(g++ 13+ or clang++ 19+). Linux and macOS are supported; on Windows, WSL is
the recommended setup.

```console
$ pip install tpy-lang
$ pip install "tpy-lang[bundled]"   # also installs zig as a bundled C++ compiler
```

Or as an isolated tool: `uv tool install tpy-lang` or `pipx install tpy-lang`.

Two commands are installed: `tpy` runs programs and drops to a REPL with no
arguments; `tpyc` compiles only and emits `.hpp`/`.cpp` files or a binary.

## Usage

```console
$ tpy                        # interactive REPL
$ tpy -c "print(1 + 2)"      # run one line
$ tpy --debug app.py         # unoptimized build with debug info (-g -O0)
$ tpy --dump-code app.py     # print the generated C++
$ tpyc -o out/ app.py        # emit .hpp/.cpp into out/
```

[Building & running](https://tpy-lang.org/docs/guide/building/)
covers every command and flag, and the
[getting-started guide](https://tpy-lang.org/docs/getting-started/) walks
through a first program.

## How it differs from Python

- Function parameters and return types are annotated (a missing return
  annotation means `-> None`), and class fields are declared with
  annotations; local variables are inferred.
- Integer literals and `len()` are `int32`, with checked overflow; `int`
  stays arbitrary-precision.
- Every value has exactly one owner: a function frame, a field or a
  container. Storing an object that stays in use afterwards copies it, and
  the compiler warns where CPython would have shared a reference. `Own[T]`
  moves a value instead.
- No GIL: `spawn` runs real OS threads and checks `Send`/`Sync` at compile
  time. A task that is not `Send`, or state shared through `Arc` that is not
  `Sync`, is rejected; shared mutable state goes through `Arc[Mutex[T]]`,
  `RwLock`, atomics or channels.
- No CPython packages: everything a program imports is TurboPython source,
  compiled with it. Dependencies are TurboPython packages, either the
  standard-library subset and `tplib` that ship with the compiler, or others
  written for it.
- Running a file under CPython, or type-checking its `tpy` imports, needs
  the `tpy` stub package from the source tree (`lib/cpy`) on the path; it is
  not yet part of the installed package.

The ownership rule is the one deep difference. A list owns its elements, so
appending an object that is still in use afterwards stores a copy, and the
compiler says so:

```python
from dataclasses import dataclass

@dataclass
class Reading:
    sensor: str

def main() -> None:
    log: list[Reading] = []
    r = Reading("boiler-3")
    log.append(r)            # r is used below, so the list gets a copy
    r.sensor = "renamed"
    print(log[0].sensor)     # boiler-3 -- not renamed

main()
```

```
warning: copies Reading into owned storage; use copy() to make this explicit
```

Under CPython this program prints `renamed`. The program still compiles, and
the warning makes the difference visible at compile time; writing `copy(r)`
(`from tpy import copy`) states the intent and silences it. Unlike Cython, mypyc or Nuitka, the
output does not use the CPython runtime; unlike Codon and Shed Skin, memory
is managed by ownership instead of a garbage collector.
[How TPy differs](https://tpy-lang.org/docs/guide/differences/) lists every
difference with the compiler's actual diagnostics, and
[Ownership](https://tpy-lang.org/docs/guide/ownership/) teaches the model.

## Status

TurboPython is pre-1.0 and changes between releases; read the
[release notes](https://github.com/trozen/tpy-lang/blob/master/RELEASE_NOTES.md)
before upgrading.

- **What works** is listed per feature and per standard-library module in
  [Compatibility](https://tpy-lang.org/docs/compatibility/).
- **What is broken** is tracked in the open, in
  [`BUGS.md`](https://github.com/trozen/tpy-lang/blob/master/BUGS.md). It
  is long. Most entries are valid Python the compiler still rejects with
  an error; the ones that matter most are the silent miscompiles, and
  those are fixed first.
- **Memory safety is partial.** The compiler rejects a reference that would
  outlive its owner, but it does not enforce Rust's aliasing rules: mutating
  a container while a reference into it is live draws a warning, not an
  error. Known holes in the escape check are tracked in the Safety section
  of `BUGS.md`.
- **How it is checked**: more than 6,500 test programs. Each pins the
  compiler's diagnostics and the generated C++; the ones that run are
  built, executed, and -- wherever the program is also valid CPython --
  byte-compared against CPython's output.
- **Bug reports are welcome**, especially a program that compiles and
  prints something CPython does not:
  [open an issue](https://github.com/trozen/tpy-lang/issues).

## Design goals

1. **Efficient native code** -- no interpreter, garbage collector or
   automatic reference counting, and opt-in constraints such as `@noalloc`
   for hot paths. Where efficiency and CPython compatibility conflict,
   efficiency wins.
2. **Explicit divergence** -- a construct that behaves differently from
   CPython is rejected or warned about, with a diagnostic that names the fix.
3. **Valid Python source** -- existing editors, linters and coding agents
   read TurboPython code without plugins.
4. **C/C++ interop** -- generated code links with existing C and C++ code,
   and calls go in both directions: TurboPython calls native functions and
   types through `@native`, and native code calls exported TurboPython
   functions.
5. **Checked concurrency** -- no GIL; what crosses a thread boundary is
   checked at compile time.

## Using the generated C++

`tpy --dump-code` prints the C++ the compiler writes, and `tpyc` emits it
as `.hpp`/`.cpp` files for use in a C++ project. The `Stats` class and the
`measure` function from the example above come out as:

```cpp
namespace tpyapp::stats {

struct Stats {
    int32_t count;
    int32_t total;
    int32_t longest;

    Stats() = default;
    explicit Stats(int32_t count, int32_t total, int32_t longest);
    // ... __eq__, __repr__, operator== and a class-name constant
};

Stats measure(const std::vector<std::string>& words) {
    int32_t total = 0;
    int32_t longest = 0;
    auto& __obj_0 = words;
    auto __beg_0 = __obj_0.begin();
    auto __end_0 = __obj_0.end();
    for (; __beg_0 != __end_0; ++__beg_0) {
        std::string_view w = *__beg_0;
        total = ::tpy::add_check<int32_t>(total, ::tpy::__len__(w));
        longest = ::std::max(longest, ::tpy::__len__(w));
    }
    return Stats(::tpy::__len__(words), total, longest);
}

} // namespace tpyapp::stats
```

`add_check` is the overflow-checked add; `words` is borrowed as a `const&`
and each `w` is a `std::string_view` into it, with no copies.

With an explicit output directory (`tpyc -o out/ myapp.py`), a
`sources.cmake` and the runtime headers and sources are written next to the
generated code, so `out/` is self-contained and can be committed or copied
to another machine:

```cmake
include(path/to/out/sources.cmake)
add_executable(myapp ${TPYC_SOURCES})
target_include_directories(myapp PRIVATE ${TPYC_INCLUDE_DIRS})
target_link_libraries(myapp PRIVATE ${TPYC_LIBRARIES})
target_compile_definitions(myapp PRIVATE ${TPYC_COMPILE_DEFINITIONS})
set_target_properties(myapp PROPERTIES CXX_STANDARD ${TPYC_CXX_STANDARD})
```

`TPYC_COMPILE_DEFINITIONS` is empty unless a build option fills it:
`--no-signals` puts `TPY_NO_SIGNALS` there, which compiles the Ctrl-C layer
out of the runtime for code embedded in a host (no SIGINT handler, no
`KeyboardInterrupt` from a signal, no check points; the generated code is
the same).

The generated code needs C++23 and the GCC statement-expression extension:
g++ 13+, clang++ 19+ or another LLVM-based compiler. MSVC is not supported.
Existing C and C++ code is reached through `@native` declarations, a
TurboPython function gets C linkage with `@export(binding="C")`, and a
module marked `# tpy: ext_module` builds into a regular CPython extension;
see [Emit and integrate C++](https://tpy-lang.org/docs/guide/building/#emit-and-integrate-c)
and [Native interop](https://github.com/trozen/tpy-lang/blob/master/docs/NATIVE_INTEROP.md).

## Coding with an AI agent

TurboPython source is valid Python, so coding agents work without a plugin.
What they lack is where TurboPython departs from Python.
`tpy --install-agent-docs docs/` writes four reference files into a project
(a Python-to-TPy bootstrap, the language reference, standard-library
coverage and the exact API of the installed version) and prints a snippet
to add to `AGENTS.md` or `CLAUDE.md`. Re-running it after an upgrade
refreshes them.
[Details](https://tpy-lang.org/docs/getting-started/#coding-with-an-ai-agent).

## Development

From a source checkout: `uv sync`, then `uv run tpy app.py` and
`uv run pytest`. `docs/ARCHITECTURE.md` describes the compiler.

## License

Apache License 2.0 with the LLVM Exceptions -- see
[`LICENSE`](https://github.com/trozen/tpy-lang/blob/master/LICENSE). The
exception covers the runtime and library code that is compiled into every
program: a binary built with `tpy` can be distributed without carrying the
license text or a notice. The bundled third-party libraries keep their own
licenses, listed in
[`NOTICE`](https://github.com/trozen/tpy-lang/blob/master/NOTICE).
