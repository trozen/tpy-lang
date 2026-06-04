# Release Notes

## 0.3.0 (2026-06-04)

285 commits since 0.2.0. First stable release published to PyPI as
`tpy-lang` (0.2.0 and earlier were tag-only, pre-rename).

### Language and compiler

- **Generators rebuilt on the resumable frame** (the same abstraction
  that powers async): `yield` now works in arbitrary control flow --
  `if`/`while`/`for`, `try`/`except`/`finally`, `with`, `match` --
  plus generator delegation and a declaration-driven yield ABI
  (borrow vs owned). The simple-generator lambda peephole is kept for
  the common single-loop shape.
- **async/await maturation**: `await` of protocol-param coroutines,
  `return` inside a suspending `finally`, nested suspending `finally`,
  bounded generic classes in resumable methods, narrowing and
  isinstance facts preserved across suspensions.
- **Generic type aliases**, non-recursive and recursive
  (`type Tree[T] = Leaf[T] | Node[T]`); records and protocols can use
  generic recursive aliases; recursive unions unified on the
  reference-type convention.
- **`match` expansion**: expression subjects for polymorphic dispatch,
  `@dynamic`-protocol dispatch, literal field-value conditions in
  class patterns (including `field=None`).
- **isinstance**: per-instantiation lowering on generic type params,
  `isinstance(self, Sub)` polymorphic dispatch.
- **`@dynamic` protocol + wrapper composition**: `Box[P]` / `Rc[P]`
  over abstract `@dynamic` protocols, `Box[P].set` / `Box[P].take`.
- **`*args` overhaul**: `readonly[T]` varargs with auto-readonly
  inference, generic `*args` iteration, `*unpacking` into generic and
  method varargs, `args[1:]` slicing on non-value slots.
- **Macros**: `@function_macro` body-rewriting macros (spike),
  `CallMacroContext.expected_type` for call macros, frontend
  function-macro application.
- `@auto_readonly` usage-dependent receiver const-ness, covariant
  element upcasts in container literals, bound-based `Ptr[U] -> Ptr[B]`
  coercion, inferred type-param bounds validated against substituted
  bounds.

### Library

- **tplib**: `Rc[T]` shared-ownership smart pointer (single-allocation
  cell, bounded `Rc.new[U: T]` factory) and `Weak[T]` non-owning
  companion for cycle-breaking.
- **asyncio**: executor and `run` ported from C++ to TPy, variadic
  `gather(*tasks)`, `gather_list_settled`, `asyncio.Event`.
- **stdlib**: `io` v1, `heapq`, `time` (`perf_counter` / `monotonic` /
  `process_time`), `math.prod` + faster `isqrt`, `random` gaps
  (`choice`, `shuffle`, auto-seed, `getrandbits` k>32),
  `int.bit_length`, `shl_wrap` / `shr_wrap` on fixed-width ints.
- **Runtime panics are now catchable exceptions**; `IndexError` and
  `KeyError` can be caught.

### Tooling and packaging

- Project renamed to **tpy-lang**; published to PyPI with an sdist
  allowlist so stray working-tree files cannot leak into artifacts.
- `tpy --install-agent-docs` bundles the generated API reference for
  AI coding agents; README gains a PyPI-based Quick Start.

Plus many bug fixes across sema, codegen, and the borrow checker.

## 0.2.0 (2026-05-02)

Highlights since 0.1.0 (~189 commits):

- Macro system matures: argparse builder-trace macro, JSON `@model`
  call-macros, dataclass; class/call/builder macro APIs stabilized.
- New stdlib coverage: `json` (recursive-union JsonValue), `argparse`,
  `base64`, `hashlib`, `socket`, `random` additions.
- Sema: extensive readonly/match/narrowing fixes, generic-T over str
  borrow tracking, recursive-union support, value-variant unions,
  literal-seeded local widening, mutation propagation across
  reassignment.
- Codegen: out-of-line method bodies (small inline in .hpp, large in
  .cpp), Python-faithful repr escapes, class-const phases including
  T-independent constants on generics, D24 native qualification.
- Runtime: float `%` Python floor semantics, faster repeat_range,
  constexpr checked arithmetic, macOS portability (pcre2, socket).
- Build: per-variant PCH cache, sign-conversion clean header.

## 0.1.0 (2026-04-13)

Initial tagged release of the Python-to-C++ proof-of-concept
toolchain.
