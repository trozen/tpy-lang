# TODO

## Next
- make Box[T] example great again
- example: StaticList, using UninitArrayStorage
- fix generic `unsafe_store` ownership inference: `unsafe_store(arr: Ptr[T], ..., val: T)` currently fails (`No matching overload ... Own[T]`) unless caller passes `Own[T]` or `copy(val)`; should accept `T` and preserve explicit/diagnosable copy semantics
- `self.inner = inner` in `__init__` warns about field copy even when `inner` is `Own[T]` at last use -- should auto-move
- `list.append(copy(x))` doesn't fire unnecessary-copy warning -- builtin method args bypass CallAnalyzer's copy check
- move-through for lvalue assignment at last use: `alias = h` at last use of `h` could move instead of creating `T&` ref; would enable `@nocopy` return-through-alias patterns
- Type params in generic function bodies: `list[T]` as local variable annotation fails ("Type parameter 'T' used outside of generic context"), and `return []` can't infer element type from return type when it contains a type param
- dynamic protocols and dynamic dispatch
- bi-directional contextual type inference (Phase 1b: coercion-aware matching, Phase 3: overload filtering by return type): docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md
- how to mark turbo-python files? using .tp.py is not good since it breaks python packages; maybe add an `# tpy` or `# tpy: options...` comment at the top? (differentiate between .py and .tp.py files - .tp.py files are for TurboPython dialect, may or may not run with regular CPython, or some behaviour may be different. TurboPython should make effort to run any .py file, but should warn/error if some features are not supported or behave differently)
- class field instantiation design: should we explicitely create class members in constructor (e.g. `self.obj = Obj()`) or are class member type annotations enough (e.g. `obj: Obj`)? should we store inline by default OR should we use `Own[Obj]` to define inline members?
- `# tpy:` directives handling (including per-module `# tpy: default-int=...`)
- constant global variables (see `Final` in Language Features Roadmap)
- "@native_c, @native, @extern_c, @readonly, @noalloc are all hard-coded parser keywords" -- should be handled like normal functions eventually (maybe in tpy.extern package?)
- move native_c_global and native_global to tpy.extern package? (or tpy.native?)
- better local/global variable type deduction (e.g. if multiple assignment but first is literal, it should be postponed to look at next etc)
- flow-sensitive None narrowing: broaden current narrowing coverage where needed (e.g. more complex expression forms)
- Ptr narrowing: after `p is not None`, skip `deref_check()` and use direct `->` access (same idea as Optional narrowing but for raw pointers)
- Ptr null-provenance warning: consider warning when accessing through a Ptr with unknown provenance (similar to Optional access warnings). Design question: warn on all unknown-provenance access (noisy for function params) vs only when provenance is lost (was non-null, then reassigned from unknown source)?
- list literal contextual typing: when LHS has explicit annotation like `list[Int32 | None]`, allow compatible literals (`[]`, `[Int32(1)]`, `[None]`) via contextual element-type widening instead of strict inferred-list mismatch
- type containing an allocated object (e.g. `Box[T]`)
- ~~`tpy::__len__()`~~ PARTIAL: `__len__`, `__getitem__`, `__setitem__` are now free functions in `dunder.hpp`. Consider also generating `__len__()` as `size()` member function for STL compatibility.
- copy/nocopy propagation: classes containing nocopy fields (e.g. `UninitHeapStorage`) should automatically become nocopy; extend to module-defined types (currently `@nocopy` only works on user-defined records)
- `DeRef` protocol?
- `ValueType` protocol bound — `T: ValueType` would suppress copy warnings for generic fields, since value types copy silently
- proper string handling (STRING_HANDLING.md)
- Deduce generic type args from field annotation: `self.data = Array()` → `Array[T, N]()` when `data: Array[T, N]`
- keyword arguments
- REPL: arr=[1,2,3]; arr[-4]
- better C++ code formatting? 4 space indentation (or tab?)
- argument default values
- ConstPtr[T] vs Ptr[readonly[T]] vs ReadOnlyPtr[T]?
- coerce int32 -> uint32?

## Language Features Roadmap

Discussed 2026-02-19. Grouped by suggested implementation phase.

### Near-term (low effort, high value)

- **Type alias**: `type MessagePayload = HelloMessage | ChatMessage` or `MessagePayload = TypeAlias[...]`. Maps to C++ `using`. Prerequisite for ergonomic union types. [Low effort]
- **Enums**: `class Color(Enum)` -> `enum class Color : int32_t`. Int-based enums first; design questions: string enums, flag enums, interaction with match/case. [Medium effort]
- **`Final` for constants**: `GLOBAL_CONST: Final = Int32(123)` -> `constexpr int32_t`. Semantically equivalent to `readonly` in TPy (both mean full immutability). Use `Final` for declarations (variables, globals), `readonly[T]` for type positions (parameters, fields). [Low effort, extends existing readonly infrastructure]
- **Function overload** (`@overload`): already tracked in Hard Problems; sema infrastructure exists, needs user-facing wiring. [Medium effort]
- **Function hiding/override warnings**: warn when child method hides parent method without virtual dispatch. Important correctness warning -- TPy/C++ uses static dispatch, Python uses dynamic dispatch, so `base_ref.method()` behaves differently. Ties into virtual dispatch (Polymorphism section). [Low effort]
- **`with` statement** (context managers): `with open(f) as fh:` -> C++ RAII scoped blocks. Natural fit for C++ (destructors, scope guards). Maps to `__enter__`/`__exit__` protocol. [Medium effort]
- **Tuple unpacking**: `a, b = func()` and `for k, v in items:`. Requires tuple type support first (`tuple` or `std::tuple`). Also needed for multiple return values. [Medium effort]
- **f-strings**: `f"x={x}, y={y}"` -> `std::format` (C++20) or `fmt::format`. Extremely common Python pattern. Ties into macro discussion (compile-time format string parsing). [Medium effort]
- **List comprehensions**: `[x*2 for x in items if x > 0]` -> loop + push_back or range pipeline. Very Pythonic, high-frequency usage. [Medium effort]
- **Dataclasses** (`@dataclass`): auto-generate `__init__`, `__eq__`, `__repr__` from field annotations. TPy classes already have partial overlap; explicit `@dataclass` support would be more Pythonic. [Medium effort]

### Medium-term (foundational features)

- **Union types** (`A | B` with isinstance): tagged unions via `std::variant`. `isinstance(x, A)` -> `std::holds_alternative<A>`. Needs narrowing support (after isinstance check, type is known). Foundational for match/case. [High effort]
- **Match/case with patterns**: Python 3.10+ `match`/`case` -> C++ `switch` on `variant.index()` (not `std::visit` -- avoids indirect dispatch overhead). Class patterns (`case ChatMessage():`) narrow the subject variable for the block body. Start with literal + type patterns (v1), structural patterns later. Depends on union types + enums. [Medium-high effort]
- **Exception handling** (`try`/`except`/`raise`): fundamental for real-world code. Design question: C++ exceptions, error codes, or `std::expected`. Could offer policy-based approach (exceptions by default, `@noexcept` for hot paths). [High effort]
- **`del x` explicit destruction**: `del x` destructs object and marks variable as de-initialized; compiler errors on subsequent use. Leverages existing init_tracker. [Medium effort]
- **String literal types** (`Literal["r", "w", "rb"]`): compile-time checked string sets for API compat (e.g. `open()` mode). Could map to enum class internally while accepting string syntax at TPy level. [Medium effort]
- **Closures / nested functions**: `def inner():` capturing outer variables. Needs capture semantics design (by value vs by reference, lifetime of captures). Prerequisite for lambda and callbacks. [Medium-high effort]

### Longer-term (design-heavy)

- **Thread safety design**: Rust-inspired ideas (Send/Sync traits, ownership-based thread safety, immutable-by-default sharing). TPy targets multi-threaded applications unlike Python. Needs early design to avoid painting into a corner. Ties into existing investigation item (borrowing/lifetimes). [Design phase first]
- **Macro system** (compile-time codegen): function-like macros (`print(...)` expands to optimized formatting code, like Rust's `println!`) and decorator macros (`@parse_json class X` generates methods from class fields). Partially designed in LANGUAGE_FEATURES.md (`@compile_time` / `__generate__` hooks). [High effort]
- **Self-interpret** (TPy eval in tpyc): run/interpret TPy code during compilation for macro expansion. Also improves REPL (current approach is awkward). Two approaches: embedded interpreter or compile-and-exec during compilation (like Zig comptime). [High effort, needs design exploration]
- **Cyclic dependency handling**: already tracked in Hard Problems. TPy can do better than Python (which silently partially-initializes modules) since we have the full dep graph at compile time. Options: clear error on cycles, or resolve via C++ forward declarations where possible. [High effort]
- **Lambda**: `lambda x: x + 1` -> C++ lambda. Depends on closures/nested functions for capture semantics. [Medium effort, lower priority]

## Bugs
- None ;-)

## Investigate
- investigate rust like feature (borrowing, lifetimes etc) to make the language safe; however these should be softer restrictions than in rust
- zig language: what it is, how is it different from C, what useful patterns can we learn
- Go: channels is a nice concept (for our needs fixed size channels would be great)

## Builtins
- type(); (in future `T = type(x); z = T()`)
- tpy.ctypes.CInt32
- deref()
- ptr() function?

## Hard Problems
- auto-detect readonly from method body analysis (bottom-up inference) -- currently dunders in IMPLICIT_READONLY_METHODS are implicitly readonly, but regular methods need explicit `@readonly`; auto-inference could remove the need for annotations in most cases
- handling cyclic imports (see Language Features Roadmap -- Longer-term)
- user-defined function/method overloads via `@overload` decorator (from `typing`). Each `@overload` body is the real implementation (unlike CPython where bodies are stubs). Maps directly to C++ overloads. Infrastructure already exists (overload resolution, type-checked params). (See Language Features Roadmap -- Near-term)

## Polymorphism
- Implicit upcasting: `parent: Animal = Dog()` (child instance to parent type)
- Polymorphic coercion: `Dog` → `Ptr[Animal]` (child to parent pointer)
- Virtual dispatch (requires C++ `virtual` methods) - currently `self.method()` in parent uses static dispatch
- Protocol-typed local variables: allow protocol types as variable types (e.g. `seq: Sequence[Int32] = items`)

## Python features
- dict full support
- str full support
- tuple, multiple returns (mandelbrot TODOs)
- list slicing (`items[1:3]`)
- make sure docstrings work in every context
- list/StaticList operator (+=, *, +, in), sort
- bytes type
- list(str)
- allow type annotation to use "" (forward decl)
- for-each: preserve loop variable after loop exit (if used after the loop)
- add `__bool__()` and a proper protocol (bool() function should have overload consuming that protocol)

## Random items
Random items that may or may not be implemented in the future, but putting them here so that they don't get lost:
- handling user object copy via __copy__ (e.g. heap allocated)
- language restriction documentation
- use this as source of examples: https://github.com/shedskin/shedskin/tree/master/examples (at some point we would like to make them all work)
- Char type location: currently in `builtins` module but feels like a tpy type. Python doesn't have `Char`. Decide: keep in builtins, move to tpy, or remove? Affects `from tpy import Char` which currently fails.
- ultimate goal: make tpyc compile with tpyc
- game of life benchmark TPy vs CPy (two version: idiomatic python, optimized TPy types)
- option to change divide semantics (negative): Python vs C++
- do not stop at first error, generate source with special Invalid() type, that would be ignored in further lines, so that we get all errors from compilation
- better handling of tpy_panic -- exceptions in first version (later generation policy)
- c++ generation profiles: utf8 strings vs char strings
- static_cast<char> -- should rather use checked cast (policy based)
- `Own[T]` for argument passing: callee takes ownership (how to pass an object from pointer? require explicit copy?)
- support more dunder methods: `__bool__`, `__hash__`, etc.
- extract built-in function defintions to separate files (len, print)
- update char semantics (e.g. passing str to a function accepting Char should throw if len != 1)
- ability to define `__str__` method (currently works as explicit call `obj.__str__()`, but `str(obj)` doesn't dispatch to it)
- better class operator<< tests (but missing str formatting/concatenation)
- formatting/linting like in genweb
- properties with getter/setter
- list[Ptr[Point]] not supported, but it should be, eventually
- `Span(list([1,2,3]))` not supported
- existing C++ interoperability: when we want to call existing C++ we need to declare types/functions in TPy files, but without generation, only annotating how to use them in code
- implicitely define class members by assigning in constructor (in @noalloc mode should warn about deducing int)
- `__int__` equivalent for Int32 etc types (e.g. `__int32__` etc or prefixed: `__tpy_int32__`)
- hoisted variable slots: keep them at the lowest scope that satisfies lifetime, instead of always hoisting to function scope
- eliminate trivial temps for value-type constructor args: `wrap(Int32(10))` generates `int32_t __tmp = 10; wrap(__tmp)` instead of `wrap(10)`
- runtime `using` declarations in global namespace (`tpy.hpp`): generated code should use `tpy::` prefix instead of relying on `using tpy::BigInt` etc.
- refactor: consider merging `gen_module_init()` body generation into `gen_body()` helper (functions and methods already use it, but module init has too many special cases currently)
- C++ header ordering: inline method bodies in structs (constructors, methods) can't call free functions declared later in the header. Affects cases like `self.value = func()` when func is defined before the class in Python but its C++ forward declaration is emitted after the struct. Fix: emit function forward declarations before struct definitions, or move method bodies out-of-line.
- investigate other backends than c++
- panic show line number?
- properly import annotations from tpy module (readonly, noalloc etc); should not be accessible without it; also, should support @tpy.readonly
- @extern_c/@extern_cpp functions/classes etc
- `# tpy: range-check=off`
- warn on mutable globals
- dead code detection
- extend int type configuration to AddressType/SizeType/PtrDiff (e.g. UInt32, Int32, Int32)
- diagnostics: trace "float spill" origin across assignments/expressions (e.g. accidental `/` instead of `//`) and surface root cause in downstream type mismatch errors
- extract c++ compiler interface
- analysis: when an object is passed to a function by reference but then copied, should we suggest passing as Own[]?

## Other
- Char → str coercion: only literals work (`c: Char = "x"`), variables can't convert to str
- Docstrings: silently skipped in codegen (harmless, but no introspection support)
- make a doc with TPy vs Python differences

## Code Review Items (2026-01-27)
- Comparisons accept any types: `record == record` passes sema but may fail C++ if no operator==
- Unknown record types not rejected: `bar: UnknownType` passes sema, fails at C++ compile
- Unary `not` not type-checked: `not items` (container) passes sema, fails C++ compile
- `list.extend` lacks type validation: element type mismatch not checked
- `and`/`or` return `bool` not operand: `1 and 2` returns `1` (bool), Python returns `2`

## Known Limitations
- `str(numeric)` returns `std::string` but `str` type maps to `std::string_view` - storing result in variable creates dangling reference (UAF). Safe for inline use only (e.g., `print(str(42))`). Proper fix requires ownership tracking in type system.
- Pointer-local slot reuse: reassigned T* pointer-locals allocate a fresh `std::optional<T>` slot per assignment. The initial slot could be reused after reassignment instead of allocating a new one.
- Top-level block scoping differs from Python: Variables declared inside `if`/`while`/`for` at module level are visible outside the block in Python but block-scoped in C++. Example: `if cond: x = 1` followed by `print(x)` works in Python but `x` is out of scope in generated C++. Fix requires hoisting declarations to module scope. (Note: `if`/`else` in functions is fixed — branch-declared vars are pre-declared before the if-statement.)
- Generic Optional codegen mismatch: for generic records, `T | None` generates `T*`/`nullptr` instead of `std::optional<T>`. This breaks C++ concepts that expect `std::optional<ElemT>` (e.g., `tpy::OptIterator`). Value-type Optional (`Int32 | None` → `std::optional<int32_t>`) works fine.
- Inherited constructor forwarding: multi-level inheritance (`Child -> Mid -> Base`) where intermediate classes have no `__init__` doesn't forward the base constructor. C++ generates `Child() = default;` only, so `Child(args)` fails. Workaround: add explicit `__init__` + `super().__init__()` at each level.
- No `Iterable[T]` protocol: `__iter__` support is structural (detected by `get_iter_element_type()`), not protocol-based. Can't write `def f(it: Iterable[T])` as a parameter type. Adding it requires return-type conformance checking in the protocol system — currently protocol conformance only checks type equality on method signatures, not whether a return type *conforms to* another protocol (e.g., `Counter` conforming to `OptIterator[T]`).

## ShedSkin examples
- score4
- mandelbrot
- nbody
