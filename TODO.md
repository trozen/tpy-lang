# TODO

## Next
- Union return copies non-value members: `f() -> A | B` returns `std::variant<A, B>` by value, copying record members. Python returns by reference. `std::variant<A&, B&>` is not valid C++. Params are fine (`&` for non-value unions). See `docs/UNION_TYPES_DESIGN.md` Known Semantic Gaps.
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?

## Bugs

## Fuzzy Testing Findings (2026-03-12)

### Silent divergences (TPy compiles and runs, output differs from CPython)
- **[HIGH]** Ternary generates copy instead of reference for mutable types (`list`/`dict`/`set`/records): `or`/`and` was fixed (uses references). Ternary remains a known gap -- see Known Limitations.

### Compilation errors (valid Python that fails to build)
- **[HIGH]** `StrView` -> `str` coercion missing in variable initializer: when a local is deduced as `std::string` (e.g. because it is later mutated with `+=`) but its initializing expression has type `StrView` (str param, ternary of params, `a or b` of params), no `std::string(...)` conversion is emitted and the C++ build fails. Same gap affects `return expr` when return type is `str` and expr is `StrView`. Fix: in codegen, wrap `StrView` expressions in `std::string(...)` when the target slot is `std::string`.
- **[MED]** `list += [literal]` fails C++ build: `b += [4, 5]` generates `tpy::list_extend(b, {4, 5})` where the brace-init list cannot deduce the element type (e.g. `BigInt`). Fix: emit a typed vector literal (`std::vector<BigInt>{4, 5}`) instead of a raw brace-init list.
- **[LOW]** Nested `dict[K, dict[...]]` printing fails: `DictPrinter` has no `operator<<` for `ordered_map` as a value type, so printing a dict whose values are themselves dicts fails at C++ build time. Fix: add recursive printing support in `dict_ops.hpp`/`printing.hpp`.

### Safety (latent UB, no diagnostic emitted)
- **[HIGH]** Generic method returning `T` copies instead of binding a reference: `p = box.get()` where `get(self) -> T` generates `Point p = box.get()` (copy) instead of `Point& p = box.get()` (reference). For non-value types `val_or_ref_t<T>` = `T&`, so the method returns a reference but the caller discards it by copying. `p.mutate()` silently fails to affect the original -- wrong Python semantics. Same class of problem as the subscript case below; fix requires both generating `T&` variable declarations for non-value returns and extending borrow tracking to cover them.
- **[HIGH]** Iterator invalidation via container subscript: `p = items[0]` generates `T& p = tpy::__getitem__(items, 0)` -- a reference into the vector's storage. Any subsequent mutation of `items` that causes reallocation (e.g. `items.append(...)`) silently invalidates `p`, causing UB with no warning. This is the same class of problem as `Span` borrow tracking -- `__getitem__` on a container is an implicit borrow of the container and should be treated as one. Fix falls under extending borrow tracking (see Safety section above) to cover container subscript references, not just explicit `Span`/`Ptr` view types.

### Quality / optimization (correct output, but suboptimal codegen)
- **[LOW]** None-seeded variable assigned in all branches stays `Optional[T]`: when `x = None` is followed by assignment in both the `if` and `else` branches (so every path guarantees a value), `x` is still typed `std::optional<T>` after the if/else block. Post-dominance analysis could demote it to `T` and skip the optional wrapper. Not a correctness issue -- output is identical -- but adds unnecessary runtime cost and less readable C++.

## C++ Codegen Review Findings (2026-03-12)

### Bugs (generated code does not compile or is incorrect)
- **[HIGH]** Branched pointer-local slot scoping: when a pointer-local variable (e.g. `Own[Point]`) is first assigned inside one branch of an `if/else` and rebound in the sibling branch, the codegen emits the slot variables (`__slot_1`, `__slot_2`, and the pointer `p`) inside the first branch's scope, making them invisible to the `else` branch. The generated C++ does not compile. The pointer-local mechanism should not trigger at all in this pattern -- both branches immediately return, so each can simply declare and return its own named local (two independent NRVO opportunities).

### Systematic suboptimalities
- **[HIGH]** Container/record parameters always emitted as `T&` (mutable ref) regardless of whether the function mutates them: `list`, `dict`, `set`, and record params should be `const T&` when the function only reads them. `str` already gets this right (`std::string_view`). Fix: mutation analysis in codegen param-type selection (likely `codegen_cpp/functions.py` or `types.py`) -- check whether sema recorded any write to the parameter before deciding `T&` vs `const T&`.
- **[MED]** User-defined methods not auto-inferred as `const`: methods that never write to `self` are not emitted as `const` member functions. Requires explicit `@readonly` decorator. This means `const T&` method calls fail at C++ level, and the optimizer has less information. Fix: infer `const` automatically when sema finds no assignments to `self` fields or mutating method calls on `self` in the method body; `@readonly` could become optional/redundant for pure readers.
- **[MED]** `str +=` doesn't reuse the buffer: `x += y` (and `x = x + y`) on strings is lowered to `x = tpy::str_concat(x, y)` which allocates a fresh `std::string` each time, discarding `x`'s existing buffer. In a loop this is O(n^2) allocations. Fix: detect the `x = x + y` pattern (and `x += y`) on `str`/`String` and emit `x += std::string_view(y)` instead, using `std::string::operator+=` for in-place buffer reuse.

### Missed optimizations
- **[MED]** `return (*x)` on `Optional[non-trivial T]` copies instead of moves: liveness/auto-move analysis tracks `TpyName` last-uses but does not extend through optional-dereference expressions. When `x: Optional[str]` or `Optional[Record]` is returned at its last use, `return (*x)` copies the inner value. Should emit `return std::move(*x)`.
- **[LOW]** String concat chain produces N-1 intermediate allocations: `a + b + c + d` emits left-associative nested `str_concat` calls, each allocating a temporary `std::string`. A codegen optimization detecting a chain of `+` on string-view operands could emit a single `reserve` + N `append` calls.
- **[LOW]** Method returning a `str` field copies the string: a getter like `def get_name(self) -> str: return self.name` emits `return this->name` which copies the `std::string` field. The string-view deduction system handles `str` parameters but not field reads in return position. Should return `std::string_view` pointing into the field for read-only getters.

## Safety
- `Span[str]` subscript view: `SpanType.subscript_borrows()` is intentionally not overridden because `v = s[0]` registers `s` as the str-borrow source, but mutations to the backing container (`arr[0] = "x"` where `s = Span[str](arr)`) call `mark_str_borrowers_mutated("arr")` -- missing `s`. Fix requires `mark_str_borrowers_mutated` to chase the borrow tracker's alias chain so backing-container mutations also invalidate views borrowed through spans.
- View type borrow tracking for user types: currently only built-in view types (Span, Ptr) are tracked as borrows. Likely needed when designing tpy stdlib types. See escape analysis design doc (Future Extensions) for field-level vs class-level annotation tradeoffs.
- `own()` builtin: explicit `T -> Own[T]` conversion (analogous to `span()` -> `Span[T]`); near-term bridge until `__iter__(self: Own[Self])` auto-dispatch is implemented. See `docs/CONSUMING_ITERATION_DESIGN.md`.
- Consuming iteration: `__iter__(self: Own[Self]) -> Iterator[Own[T]]` overload for zero-copy element moves; `OwnIter[T]` runtime type for list drain; user-defined drain iterators (e.g. `ArrayListDrainIter`) as view types with borrow tracking. See `docs/CONSUMING_ITERATION_DESIGN.md`.

## Examples

## Investigate
- Readonly type unification: consider replacing `ReadOnlySpan[T]`/`ReadOnlyPtr[T]`/`ReadOnlySpanLike[T]` with parameterized `Span[readonly[T]]`/`Ptr[readonly[T]]`/`SpanLike[readonly[T]]`. Would simplify the type system (fewer distinct types), make readonly composable, and align naming. Design questions: how does `readonly[T]` interact with generic type params, protocol conformance, and coercion rules? Does `Span[readonly[T]]` mean the span itself is const or the elements are? (Rust distinguishes `&[T]` vs `&mut [T]` at the reference level, not the element level.)
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn
- Distinct types (newtypes): `class Meters(Distinct[float]): pass` -- zero-cost wrapper that creates a nominally distinct type. Prevents mixing incompatible values of the same underlying type (units, IDs, currencies). C++ codegen: thin struct wrapper or strong typedef. Inspired by Nim's `distinct` keyword.
- `@constexpr` decorator: mark pure functions as compile-time evaluable, emit C++ `constexpr`. Useful for lookup tables, constants, config. Low implementation cost -- lean on C++ compiler. `TABLE: Final[Int32] = factorial(10)` computed at compile time.
- Inline iterators: `@inline` on generator functions to unroll the loop body at the call site instead of creating a state machine. Zero overhead, important for hot paths. Generalizes what `range()` already does implicitly to user-defined iterators. Inspired by Nim's inline vs closure iterator distinction.

## Builtins
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- ptr() function? Auto-select Ptr vs ReadOnlyPtr based on binding mutability. Needs sema-level magic (mutability not in type, it's in binding context).

## Hard Problems
(see FEATURE_ROADMAP.md for tracked hard problems)

## Python features
- Any
- dynamic attributes
- list/container slicing (Phase 3: step)
- list slice assignment: `a[1:3] = [10, 20]` -- done. RHS must be `list[T]`; span RHS is a future extension.
- lambda expression
- properties
- with statement/context manager
- yield/generator function
- walrus operator :=
- Protocol isinstance in ternary expressions: `x = a.foo() if isinstance(a, P1) else a.bar()` generates a runtime `?:` but both branches must be valid C++ at template instantiation time. Fix: generate an IIFE with `if constexpr` inside, e.g. `[&]() -> T { if constexpr (P1<T_a>) { return a.foo(); } else { return a.bar(); } }()`. This also enables single-line field init in `__init__` (goes into the C++ member initializer list instead of requiring unconditional pre-assignment + reassignment in branches).
- Allow `@runtime_checkable` decorator on protocols (no-op in tpyc, enables CPython compatibility for isinstance checks on user-defined protocols)
- `del x` (variable unbinding): complex in compiled context -- needs lifetime/scope analysis. Low priority.
- allow type annotation to use "" (forward decl)
- for-each: preserve loop variable after loop exit (if used after the loop)
- C-style for loop: reassigning loop variable affects iteration (differs from Python)

## Documentation
- language restriction documentation

## Refactor
- Inline `and`/`or` chains for side-effect-free operands: `a or b or c` currently emits an intermediate temp (`auto&& __tmp = (a ? a : b); result = (__tmp ? __tmp : c)`) to avoid double-evaluation. When all operands are provably side-effect-free (variables, literals), the temp is unnecessary and the chain can be emitted as a single nested ternary (`!a.empty() ? a : !b.empty() ? b : c`), which is more readable.
- Unify rvalue materialization: two overlapping mechanisms exist for extending rvalue lifetimes -- the slot system (`std::optional<T>` slots in `_gen_pointer_local_init`, tied to var decl infrastructure) and TempState (`auto&&` temps, expression-level). Both solve the same problem at different abstraction levels. Consider exposing a lower-level "materialize this rvalue" API that both var decls and expression-level temps (e.g. `and`/`or` ternaries) can share.

## Random items
- AddressSanitizer test mode: add `--asan` flag to compile exec tests with `-fsanitize=address` to detect memory leaks, double-free, and use-after-free. Important before serious usage of `__copy__` + `__del__` patterns (e.g. CopyableBox[T]).
- Large value-type copy warning: estimate record sizes from fields and warn when Own[T] passes a large type by value (e.g. >128 bytes). For inline data there's no cheap move -- the bits are the object. Suggest Own[Box[T]] for cheap ownership transfer (moves a pointer) or Ptr[T]/ReadOnlyPtr[T] for borrowing. Stricter threshold in @noalloc contexts.
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char type location: currently in `builtins` module but feels like a tpy type. Python doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- Fixed-int division semantics: Python `//` uses floor division (round toward -inf), C++ `/` uses truncation (round toward zero). They differ for negative operands (`-7 // 2` = `-4` in Python, `-3` in C++). Current codegen emits `div_floor`/`mod_floor` which adds a branch per operation. Options: (A) configurable flag like `--int-div=floor|trunc` (similar to `--default-int`), (B) default to truncation for fixed-width types (users opting into Int32 already accept C++ semantics), BigInt always floor, (C) rely on range tracking to emit raw `/` when both operands are provably non-negative (no semantic change, but only helps when compiler can prove it)
- c++ generation profiles: utf8 strings vs char strings
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- extract built-in function defintions to separate files (len, print)
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- better class operator<< tests (but missing str formatting/concatenation)
- formatting/linting like in genweb
- existing C++ interoperability: when we want to call existing C++ we need to declare types/functions in TPy files, but without generation, only annotating how to use them in code
- auto-declare fields from `__init__`: extend to `self.f = expr` and `self.f = Constructor()` (Phase 1 param-only is done, see `docs/CONSTRUCTOR_DESIGN.md`)
- `__int__` equivalent for Int32 etc types (e.g. `__int32__` etc or prefixed: `__tpy_int32__`)
- hoisted variable slots: keep them at the lowest scope that satisfies lifetime, instead of always hoisting to function scope
- runtime `using` declarations in global namespace (`tpy.hpp`): generated code should use `tpy::` prefix instead of relying on `using tpy::BigInt` etc.
- refactor: consider merging `gen_module_init()` body generation into `gen_body()` helper (functions and methods already use it, but module init has too many special cases currently)
- panic show line number?
- decorator qualified syntax: @tpy.extern.native_c (3-level attribute access not yet supported)
- "did you mean" import hints for unresolved decorators (needs library resolution)
- @extern_c/@extern_cpp functions/classes etc
- `# tpy: range-check=off`
- extend int type configuration to AddressType/SizeType/PtrDiff (e.g. UInt32, Int32, Int32)
- diagnostics: trace "float spill" origin across assignments/expressions (e.g. accidental `/` instead of `//`) and surface root cause in downstream type mismatch errors
- extract c++ compiler interface
- analysis: when an object is passed to a function by reference but then copied, should we suggest passing as Own[]?
- Own[T] -- warn when type is large (always? or only in hot-path? noalloc?)
- sizeof() function
- logging, stream object printing, to log instead of __str__

## Standard Library

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)
- make a doc with TPy vs Python differences

## Code Review Items (2026-01-27)

## Refactoring
- `slice` type resolution: `slice` is special-cased in `_resolve_primitive_type` (parser.py) alongside `int`/`float`/`bool`/`str` because the fallback path in `_resolve_registered_type` uses `name[0].isupper()` to optimistically create NamedType for unresolved names. Lowercase builtin types like `slice` don't pass this heuristic, even though `is_known_type("slice")` returns True (the builtins module registers it). The deeper issue is that `_resolve_registered_type` returns `NamedType("slice")` instead of `SliceType`, causing type identity mismatches in union isinstance checks. Builtin types should resolve through the same path as user types instead of needing parser special cases.

## Low Priority
- `= default` semantic gap: records with required `__init__` params currently emit `ClassName() = default;` if their C++ fields are all trivially constructible, bypassing the Python-level construction contract. Should emit `= delete` (or nothing) instead -- `std::optional<T>` and containers don't require default-constructibility, so the impact is limited to direct `T t;` / `T arr[N]` patterns which tpyc doesn't generate anyway. Fix: check `init_params` required count in `_fld_type_cpp_default_constructible` (same as `_is_default_constructible`). See `docs/CONSTRUCTOR_DESIGN.md` open question 4.
- Module-level tuple unpack: missing `Final` reassignment guard (`x, y = ...` where `x` is already `Final` should error)
- Module-level tuple unpack: missing `narrowing.update_after_write()` (stale narrowing facts not cleared after overwrite)
- Module-level tuple unpack: missing `declared_var_types` tracking (`# tpyc: type()` test annotations won't validate on unpack lines)

## Known Limitations
- Pointer-local slot reuse: reassigned T* pointer-locals allocate a fresh `std::optional<T>` slot per assignment. The initial slot could be reused after reassignment instead of allocating a new one.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)
- Inherited constructor forwarding: multi-level inheritance (`Child -> Mid -> Base`) where intermediate classes have no `__init__` doesn't forward the base constructor. C++ generates `Child() = default;` only, so `Child(args)` fails. Workaround: add explicit `__init__` + `super().__init__()` at each level.
- Functions don't currently support INT type params (only TYPE)
- `readonly[T]` field mutation: sema enforces readonly on assignment targets, but codegen may not emit `const` for `readonly[str]` fields. Verify codegen emits `const` qualifier.
- Union isinstance narrowing in ternary: `x if isinstance(x, str) else ...` where `x: str | int` would need `std::get<T>()` extraction, which requires statement-level codegen (variable declaration for the extracted value).
- Ternary value copy for containers: `x = a if cond else b` where `a`/`b` are `list`/`dict`/`set` produces a C++ value copy, not a reference binding like CPython. `or`/`and` correctly bind by reference; ternary does not because `is_rvalue_source` in `codegen_cpp/context.py` has no `TpyIfExpr` handling and defaults to `True`. (see also: Fuzzy Testing Findings)

## ShedSkin examples
- score4
- mandelbrot
- nbody
