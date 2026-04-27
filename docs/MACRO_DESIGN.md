# Macro System Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Class macros: `@class_macro` decorator, `ClassInfo`/`FieldInfo`/`TypeInfo` API, `# tpy: macro_module` directive, `@dataclass` replacement | Done |
| 1b | Macro kwargs validation: typed macro signatures with automatic validation via `inspect.signature` | Done |
| 1c | Generic `field()` handling: `Field` descriptor + `field()` function in macro module, `call_macro_field_function` infrastructure | Done |
| 2 | Call-site macros: `@call_macro` functions expand at call site, receive `MacroArg` (expr + type), return replacement `TpyExpr`. First use cases: `asdict`/`astuple` | Done |
| 3+4 | Source-based macro authoring: `quote()` / `add_method_from_source` for writing macro output as TPy source strings instead of AST builder calls | Done |
| 5 | CPython compatibility: `lib/cpy/tpyc/macro_api.py` backend targeting Python `ast` module | Dropped |
| 6 | TpyMini VM: tree-walking interpreter for self-hosted compiler | Not started |
| 7 | Builder-trace macros: `@builder_macro` / `@builder_method` / `@builder_returns` / `@builder_terminal`, `BuilderContext`, sema sub-pass. First use case: `argparse` | Done |

### Macro System Future Work

Compiler-side macro infrastructure. Per-macro-module future work
(e.g. argparse's v2 list) lives with that macro's use-case section
below.

| Feature | Notes |
|---------|-------|
| Hygiene | Macro-generated names get unique internal names to avoid shadowing. Opt-out via `unhygienic(name)` |
| `--expand-macros` flag | CLI flag to dump macro expansions for debugging |
| Macro expansion trace in errors | Currently errors from macros point at the decorator site (user's class), not the macro source file + line. Diagnostics should include a trace: `in macro 'builder' at builder.py:6` so both the macro author and the user can locate the problem. Also applies to quote/add_method_from_source errors. |
| `static_read()` | Compile-time file I/O (e.g. reading `.proto` schemas). Needs caching/rebuild triggers |
| `macro_note()` | Informational diagnostic hint (shown with related errors) |
| Macro ordering / composition | Multiple macros on one class, inner-to-outer application order |
| FieldInfo.metadata | Typed metadata for macro-specific field annotations (e.g. `proto.Field`) |
| API tightening | `set_match_args()` is boilerplate -- auto-derive from init params. More broadly, reduce exposed compiler internals; common patterns (field management, method generation, match support) should be automatic |
| `TpyBlockExpr` | Block expression: sequence of statements + result expression. Codegen hoists statements to enclosing scope |
| Generic mapping detection in `asdict`/`astuple` | Currently only built-in `dict[K, V]` is recursed. A `CallMacroContext.get_mapping_key_value_types()` method could detect any type with `.items() -> Iterable[tuple[K, V]]`, enabling recursion into user-defined mapping types |
| AST splicing in `quote()` | Embed computed `Expr`/`Stmt` objects into quoted source via `${expr}` syntax. Requires custom parse pass. Enables mixing static method shapes with dynamic AST fragments (e.g., computed comparison chains). Deferred -- f-string interpolation covers common cases |
| Top-level companion records / functions | Lift the module-level emission methods (``emit_record``, ``emit_function``, ``replace_call``, ``fresh_module_name``) from ``BuilderContext`` into a shared ``ModuleEmitter`` helper held by ``ClassInfo`` and ``CallMacroContext`` too. Lets a class macro generate sibling records and free functions next to the decorated class (e.g. a key enum for JSON field dispatch, or a factory helper). Implementable today -- the BuilderTraceExpander prototype already does the work; only the home of the API would change. Caveats: the emission ordering caveats in ``emit_record``'s docstring (skipped inheritance / value-type validation) apply here too |
| Nested companion types | ``cls.add_companion_type(name, ...)`` -- generate types nested *inside* the decorated class (rather than as siblings). Requires nested-class support in parser/sema/codegen first; the top-level companion item above is the simpler precursor |
| CPython macro compat | CPython backend for macro API was dropped -- maintaining parity between compiler AST and CPython `exec`-based codegen (frozen fields, factory defaults, `super()` in exec'd code) was not worth the effort. Could be revisited if CPython test coverage of macro-generated code becomes important |
| Eval Final-typed kwargs | `BuilderContext.eval_literal_or_final()` advertises Final-constant support but the resolver returns `_UNSET` for any name today. Threading the module's Final-init expressions through to the expander would let macro authors accept `Final[str]` etc. as kwarg values |
| `macro_deps` for builder-trace macros | Today `macro_deps()` only fires for macro modules used via class macros (records with `pending_macros`); builder-trace macros bypass that path. Wiring it would let `argparse` import `tpy` (for `copy()`) and `sys` (for `sys.argv`) into the user's module namespace, unblocking the two argparse divergences below |
| Branch-aware terminal proof | Builder-trace v1 forbids tracked symbols inside conditionals/loops; a structural check that every reachable path contains exactly one terminal would relax this |
| Cross-function builder helpers | Allow factored helpers (`def add_common(parser): parser.add_argument(...)`) via a decorator that marks the parameter as a tracked builder symbol the macro can dispatch into |
| Builder state caching | Cache top-level builder-trace state by binding name so e.g. `ArgumentParser(parents=[base])` can replay `base`'s registrations -- prerequisite for `argparse` `parents=` support |

---

## Goals

- Enable library-level code generation without compiler changes
- All macro definitions must be valid Python (runnable in CPython)
- Three macro kinds: class macros (Phase 1), call-site macros (Phase 2), builder-trace macros (Phase 7)

## Architecture (Phase 1)

### Key Files

| File | Purpose |
|------|---------|
| `tpyc/macro_api.py` | Public API: metadata (`ClassInfo`, `FieldInfo`, `TypeInfo`, `BuilderContext`), builders (`ast`, `types`), type aliases (`Expr`, `Stmt`, `Function`, `Type`), decorators (`class_macro`, `call_macro`, `builder_macro`, `builder_method`, `builder_returns`, `builder_terminal`) |
| `tpyc/macro_loader.py` | `MacroRegistry`, `validate_and_call_macro`, `call_macro_field_function`, `expand_call_macro`, `validate_builder_macro`, `expand_builder_method`, `expand_builder_terminal` |
| `tpyc/sema/builder_trace.py` | `BuilderTraceExpander` -- pre-body sub-pass that detects builder constructor calls, dispatches method calls to handlers, and splices synthesized records and parse functions back into the body (Phase 7) |
| `lib/tpy/_macro_helpers.py` | Shared macro helpers: `build_init`, `build_eq`, `build_repr`, `build_hash`, `build_order` |
| `lib/tpy/dataclasses.py` | `@dataclass` class macro, `Field`/`field()`, `asdict`/`astuple` call macros |
| `lib/tpy/enum.py` | `Enum`, `IntEnum`, `auto()` -- resolved via import tracking, not yet macro-driven |
| `lib/tpy/argparse.py` | `ArgumentParser` builder-trace macro (Phase 7); first user of the `@builder_macro` mechanism |

### How It Works

1. Parser sees `from dataclasses import dataclass` -- treats it as a regular file import
2. Compiler discovers `lib/tpy/dataclasses.py` via normal module resolution
3. Before parsing, checks for `# tpy: macro_module` -- detects it
4. Loads it via CPython into `MacroRegistry` (not compiled to C++)
5. During sema registration, `_apply_class_macros()` looks up the macro and invokes it
6. Field defaults that are calls to macro-module functions (e.g. `field()`) are
   called via `call_macro_field_function`, returning descriptor objects (e.g. `Field`)
   stored on `FieldInfo.default_obj`
7. The macro receives a `ClassInfo` wrapper, inspects `default_obj` on fields,
   adds methods, sets flags
8. `ClassInfo.apply_to_record()` writes mutations back to the `TpyRecord`
9. Registration and codegen proceed normally

### How Call-Site Macros Work (Phase 2)

1. Parser sees `asdict(p)` or `dataclasses.asdict(p)` -- parses as normal call
2. Sema resolves the function via namespace lookup (`IMPORTED_NAME` binding)
3. Checks `macro_registry.get_call_macro(module, name)` -- found
4. Analyzes argument expressions first to get their types
5. Wraps each as `MacroArg(expr, TypeInfo)`. Kwargs annotated with simple types
   (`str`, `int`, `bool`) are auto-extracted from literals.
6. Calls the macro function via `expand_call_macro()`
7. Macro returns a replacement `TpyExpr` (e.g. `TpyDictLiteral`)
8. Stored on `expr.macro_expansion`; sema analyzes the expansion
9. Codegen checks `macro_expansion` and emits it instead of the original call

### How Builder-Trace Macros Work (Phase 7)

1. Parser sees `parser = ArgumentParser(...)` -- parses as a normal call + assignment
2. `BuilderTraceExpander` runs as a pre-body sub-pass inside `_analyze_function()`
   and `_analyze_record_methods()`, before `_prescan_and_analyze_body()` (and once
   for `module.top_level_stmts`)
3. The expander scans body statements for assignments whose RHS is a constructor
   call to a class registered as `@builder_macro` in the macro registry
4. Each match instantiates the macro state class and binds the LHS name as a
   tracked symbol. `__init__(self, ctx: BuilderContext, args: list[MacroArg])`
   stores macro-evaluated kwargs in plain Python attributes
5. Subsequent statements are walked linearly. Method calls on tracked symbols
   dispatch to the corresponding handler:
   - `@builder_method` -- void; mutates state
   - `@builder_returns(ChildClass)` -- returns a sub-builder; LHS becomes a
     new tracked symbol bound to a fresh `ChildClass` instance
   - `@builder_terminal` -- closes the trace, emits synthesized declarations
     (records and module-scope functions), and replaces the call site
6. Any other use of a tracked symbol (passed to a function, returned, indexed,
   used in a conditional, reassigned) is a hard error with a precise diagnostic.
   v1 also forbids the symbol appearing inside conditionals/loops/`try`
7. Synthesized records and functions are registered through the existing
   registrar (`registrar.register_record`, `registrar.register_function`) on
   terminal expansion, before normal sema continues -- this is what lets
   `args = parser.parse_args()` type-check with `args` as the synthesized
   record type
8. The original builder-call statements are deleted from the body; the rewritten
   body proceeds through normal `_prescan_and_analyze_body()`
9. Phase 2 (call-graph fixpoint) sees the synthesized parse function as
   ordinary; no special-casing in mutation propagation or codegen

### Macro API

Macros receive type info through thin public wrappers over compiler internals:

```python
from tpyc.macro_api import ClassInfo, FieldInfo, TypeInfo, class_macro

class TypeInfo:
    name: str                      # "Int32", "list", "str"
    type_args: list[TypeInfo]
    is_optional: bool
    is_value_type: bool
    is_record: bool
    _tpy_type: TpyType             # escape hatch for round-tripping

class FieldInfo:
    name: str
    type: TypeInfo
    has_default: bool
    default_expr: TpyExpr | None   # raw AST for programmatic use
    is_factory_default: bool
    loc: SourceLocation | None

class ClassInfo:
    name: str
    fields: list[FieldInfo]        # own fields only
    type_params: list[str]
    parent: TypeInfo | None
    is_frozen: bool                # settable

    def add_method(self, func: TpyFunction) -> None: ...
    def has_method(self, name: str) -> bool: ...
    def get_parent_fields(self) -> list[FieldInfo]: ...
    def set_match_args(self, names: list[str]) -> None: ...
    def warning(self, msg: str, loc=None) -> None: ...
    def error(self, msg: str, loc=None) -> NoReturn: ...
```

Call-site macros receive a `CallMacroContext` with call-site context and type
introspection:

```python
class CallMacroContext:
    # Call site context
    in_method: bool                                     # inside a method body?
    self_type: TypeInfo | None                          # current class (methods)
    first_param: tuple[str, TypeInfo] | None            # self for methods, first arg for free functions

    # Type introspection
    def get_field_type(type_info, name) -> TypeInfo | None: ...        # field type (incl. inherited)
    def get_method_return_type(type_info, name) -> TypeInfo | None: ...  # method return type (incl. inherited)
    def get_iterable_element_type(type_info) -> TypeInfo | None: ...
    def qualified_name(type_info) -> str: ...            # "module.TypeName"
    def get_record_fields(name: str) -> list[FieldInfo] | None: ...    # for match_args records

    # AST helpers
    def self_field(name) -> Expr: ...                    # AST for self.<name>

    # Diagnostics
    def warning(msg, loc=None) -> None: ...
    def error(msg, loc=None) -> NoReturn: ...
```

`add_method` injects a `TpyFunction` AST node (power user API). For common patterns,
use the shared builder functions from `_macro_helpers` (`build_init`, `build_eq`,
`build_repr`, `build_hash`, `build_order`) to generate complete method bodies.

Builder-trace macros (Phase 7) receive a `BuilderContext` carrying macro-time
literal evaluators, code emission helpers, and diagnostics:

```python
class BuilderContext:
    # Diagnostics
    def warning(msg, loc=None) -> None: ...
    def error(msg, loc=None) -> NoReturn: ...
    @property
    def call_loc(self) -> Loc: ...
    @property
    def function_being_traced(self) -> str: ...

    # Macro-time literal evaluators (the primitives)
    def eval_literal_or_final(expr) -> object: ...
    def eval_sequence_of_literal_or_final(expr) -> list[object]: ...

    # Typed kwarg / positional extractors (over the primitives above)
    def positional_strs(args) -> list[str]: ...
    def positional_str(args, i) -> str: ...
    def kwarg_str(args, name, default=None) -> str | None: ...
    def kwarg_int(args, name, default=None) -> int | None: ...
    def kwarg_bool(args, name, default=False) -> bool: ...
    def kwarg_str_or_int(args, name, default=None) -> str | int | None: ...
    def kwarg_type(args, name, default=None) -> TypeInfo | None: ...
    def kwarg_list_literal(args, name, default=None) -> list[MacroArg] | None: ...
    def kwarg_macroarg(args, name, default=None) -> MacroArg | None: ...

    # Code emission
    def fresh_module_name(hint: str) -> str: ...
    def emit_record(name, fields, methods=None) -> TypeInfo: ...
    def emit_function(name, params, return_type, body) -> str: ...
    def replace_call(fn_name, args) -> None: ...
```

`AstBuilder` (`ast`) and `TypeBuilder` (`types`) are reused unchanged; emission
helpers thread synthesized records and functions through the existing registrar
so codegen sees them as ordinary declarations.

### Macro Module Format

```python
# tpy: macro_module
from tpyc.macro_api import (
    ClassInfo, FieldInfo, TypeInfo, MacroError,
    class_macro, ast, types, Expr, Stmt, Function,
)
from _macro_helpers import build_init, build_eq, build_repr, build_hash, build_order

@class_macro
def my_decorator(cls: ClassInfo, *, option: bool = False) -> None:
    # inspect cls.fields, cls.type_params, etc.
    # build methods via ast.function(), ast.assign(), ast.call(), etc.
    # add methods via cls.add_method()
    # set flags via cls.is_frozen, etc.
    # emit diagnostics via cls.warning() or cls.error()
    pass
```

Macro modules use `# tpy: macro_module` directive. They are executed via CPython
during compilation and never compiled to C++. Import restriction enforces that
macros import only from `tpyc.macro_api` and other macro modules (files with
`# tpy: macro_module` found in lib search paths). Dangerous builtins (`open`,
`exec`, `eval`, etc.) are also blocked.

## Use Cases

### 1. Pydantic-like model types

```python
@model
class Order:
    symbol: FixStr[8]
    price: Float64
    quantity: Int32
```

The `@model` macro inspects fields+types, generates `__init__`, `validate()`,
`to_json()`, `from_json()`, `__eq__`, `__repr__`. Validation and serialization
compile to efficient C++ with no runtime reflection.

### 2. Protobuf serialization (`tplib.protobuf`)

Compile-time protobuf code generation via class macros. Messages are defined as
Python classes with type annotations and `field(N)` descriptors. The macro
generates `serialize()` and `parse()` methods that compile to efficient C++ with
no runtime reflection.

**Prior art**: betterproto (dataclass-based, generated from .proto files),
pure-protobuf (pure Python, `Annotated[T, Field(N)]`), proto-plus (Google wrapper).
Our API is closest to betterproto's ergonomics but with types from annotations
and `field(N)` reusing the existing descriptor infrastructure.

#### API

```python
from tplib.protobuf import Message, field

class Point(Message):
    x: Float64 = field(1)
    y: Float64 = field(2)

class Trade(Message):
    id: Int64 = field(1)
    symbol: str = field(2)
    price: Float64 = field(3)
    tags: list[str] = field(4)
    origin: Optional[Point] = field(5)

# Serialize
buf = t.encode()           # -> bytes / bytearray

# Parse
t2 = Trade.decode(buf)          # -> Trade
```

#### Wire type mapping

TPy types map to protobuf wire types:

| TPy type | Proto wire type | Encoding |
|----------|----------------|----------|
| `bool` | varint | 0/1 |
| `Int32`, `Int64` | varint | signed varint (zigzag) |
| `UInt32`, `UInt64` | varint | unsigned varint |
| `Float32` | fixed32 | IEEE 754 single |
| `Float64` | fixed64 | IEEE 754 double |
| `str` | length-delimited | UTF-8 bytes |
| `bytes` | length-delimited | raw bytes |
| `list[T]` | length-delimited (packed) | repeated, packed for scalars |
| `Optional[T]` | same as T | presence tracked via field bitmask |
| `Message` subclass | length-delimited | nested message |
| `IntEnum` | varint | enum value |

#### What the macro generates

The `Message` class macro:
- Validates field numbers (unique, positive)
- Validates field types (must map to a wire type)
- Generates `__init__` (via `build_init`, same as `@dataclass`)
- Generates `__eq__` (via `build_eq`)
- Generates `encode(self) -> bytearray` method (field-by-field serialization)
- Generates `decode(cls, data: bytes) -> Self` classmethod (field-by-field parsing)
- Calls `set_match_args` so `asdict`/`astuple` work

#### C++ runtime support

Small runtime in `runtime/cpp/include/tpy/proto.hpp`:
- `proto::encode_varint(buf, value)` / `proto::decode_varint(data, pos) -> (value, new_pos)`
- `proto::encode_zigzag(value)` / `proto::decode_zigzag(encoded)`
- `proto::encode_tag(field_number, wire_type)` / `proto::decode_tag(data, pos)`
- `proto::encode_length_delimited(buf, bytes)` / `proto::decode_length_delimited(data, pos)`
- `proto::encode_fixed32/64(buf, value)` / `proto::decode_fixed32/64(data, pos)`

The macro generates method bodies that call these primitives directly -- no
virtual dispatch, no reflection, no field table lookups at runtime.

#### Implementation phases

1. **Scalar fields**: `Int32`, `Int64`, `UInt32`, `UInt64`, `Float32`, `Float64`,
   `bool`, `str` -- covers the core wire types
2. **Nested messages**: `Message` subclass fields, recursive encode/decode
3. **Repeated fields**: `list[T]` with packed encoding for scalars
4. **Optional fields**: `Optional[T]` with presence bitmask
5. **Enums**: `IntEnum` fields as varint
6. **Maps**: `dict[K, V]` as repeated key-value pair messages (proto3 convention)
7. **Oneof**: union fields (maps to TPy `A | B` union types)

#### Infrastructure needed

- `add_method_from_source` (Phase 4) or manual AST construction for
  `encode()`/`decode()` bodies -- these are complex method bodies with loops
  and conditional logic
- `bytes` / `bytearray` type support in TPy (or `Span[UInt8]` as the buffer type)
- C++ runtime header for protobuf encoding primitives

#### Open questions

- **Buffer type**: `bytearray` (Pythonic) vs `Span[UInt8]` (zero-copy, existing
  TPy type) vs `list[UInt8]` (simple but slow). Probably `bytearray` as the
  API type, backed by `std::vector<uint8_t>` in C++.
- **Streaming**: should `encode()` accept an output buffer/writer for zero-copy
  serialization, or always return a new buffer? Could offer both:
  `encode() -> bytearray` and `encode_into(buf: Span[UInt8]) -> Int32` (returns
  bytes written).
- **Compatibility**: should we support reading proto2 messages (required fields,
  groups)? Probably proto3-only for simplicity.
- **`.proto` file import**: future extension via `static_read()` macro -- read
  and parse `.proto` files at compile time to generate message classes
  automatically.

### 3. Zero-alloc logging (call-site macro, Phase 2)

```python
log(INFO, "user {} traded {} @ {}", user_id, symbol, price)
```

Expands at compile time to type-aware writes into a fixed buffer.

### 4. Derive-style method generation

```python
@derive(Eq, Hash, Repr)
class Point:
    x: Int32
    y: Int32
```

### 5. Builder pattern (class-macro form)

```python
@builder
class Config:
    host: str
    port: Int32 = 8080
    timeout: Float64 = 30.0
```

A class macro that emits fluent `set_*` methods. Distinct from builder-trace
macros (use case 7), which trace a builder pattern at the call site rather
than annotating the class.

### 6. Compile-time regex validation (call-site macro, Phase 2)

```python
r = regex("[a-z]+")       # ok
r2 = regex("[invalid(")   # compile error: malformed regex pattern
```

### 7. CLI parsing (builder-trace macro, Phase 7)

`argparse` mirrors CPython's surface as a builder-trace macro:

```python
from argparse import ArgumentParser

def main(argv: list[str]) -> Int32:
    parser = ArgumentParser(description="Frobnicate")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--count", type=int, default=1)
    parser.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    if args.verbose:
        print(args.count, args.files)
    return 0
```

The macro synthesizes a per-call-site record (`verbose: bool`, `count: int`,
`files: list[str]`) and a parse function. `args` is statically typed; IDEs see
the fields; no runtime reflection.

v1 surface (done): `add_argument` with `store` / `store_true` /
`store_false` / `count` / `append` / `extend` / `store_const` actions;
`nargs` in `?` / `*` / `+` / integer; `type=` from `int` / `float` /
`str`; `default=` / `const=` / `choices=` / `required=` / `help=` /
`dest=`. Optional scalar flags without `default=` produce `Optional[T]`
fields. `help=` is accepted and stored at macro time but no `--help`
printer is emitted yet.

#### argparse Future Work

Priority-ordered. STDLIB_ROADMAP.md mirrors this as the per-feature
status table for the `argparse` module.

| Tier | Feature | Notes |
|------|---------|-------|
| 1 | `--help` / `-h` auto-generation | Synthesize a help-printer that walks the registered specs to produce a usage line + per-argument help text and exits when `-h` / `--help` appears in argv. `help=` data is already collected at macro time |
| 1 | `type=` for fixed-width ints / `Float32` | `Int32` / `Int64` / `UInt8` / ... / `Float32`. Same shape as the v1 `int` / `float` / `str` cases, just more entries in the allowed-types set with matching field types and conversion calls |
| 1 | Subparsers | `add_subparsers()` + `add_parser(name)`. Tagged-union codegen: each sub-parser builds its own per-name record; the top-level result is `Union[NameA, NameB, ...]` discriminated by the chosen subcommand. Sema sees the union; `match`/`case` narrows |
| 2 | `metavar=` | Display name in the synthesized help text; pairs with `--help` |
| 2 | `prog=` / `usage=` / `epilog=` | `ArgumentParser`-level help-string customization; reuses the help-printer infrastructure |
| 2 | `add_mutually_exclusive_group()` | At-most-one constraint across a set of flags; fail at parse time if more than one is seen |
| 2 | Custom `type=` via `ArgType[T]` | Protocol with `from_arg(s: str) -> T`. Lets users plug `Path`, `datetime`, custom records without macro changes |
| 2 | List-literal defaults | `default=[1, 2, 3]` -- extend `eval_literal_or_final` to recurse into list / tuple literals at macro time |
| 3 | `parents=` | Compose parsers by replaying a base parser's registrations. Needs the **Builder state caching** macro-system feature |
| 3 | `BooleanOptionalAction` | Python 3.9+: paired `--foo` / `--no-foo` from a single `add_argument`. Synthesis is straightforward; defer until users ask |
| 3 | `add_argument_group()` | Help-formatting feature; hooks into the `--help` printer once it lands |
| 3 | `allow_abbrev` | Long-flag prefix matching (`--ver` matches `--verbose`); non-trivial collision rules |
| 3 | `fromfile_prefix_chars` | Read additional args from a file prefixed with `@`. Niche |
| 3 | Custom formatter classes | `RawDescriptionHelpFormatter` etc.; depends on the help printer being pluggable |
| 3 | `action=<callable>` | CPython's escape hatch for arbitrary action classes. Hard to model under builder-trace because the action object is opaque to the macro |

Known v1 divergences from CPython argparse (all blocked on macro-
system future work, mostly the `macro_deps` for builder-trace macros
item):

| Divergence | TPy v1 | CPython | Unblock |
|------------|--------|---------|---------|
| Absent optional list-typed args (`action='append'`/`'extend'` or `store` with `nargs=N/*/+`) | field is `[]` | field is `None` | macro_deps so synthesized code can call `tpy.copy()` to move a typed accumulator into an `Optional[list[T]]` field without an implicit-copy warning |
| `parser.parse_args()` with no args | macro-time error | uses `sys.argv[1:]` | macro_deps so synthesized code can reference `sys.argv` |
| Parse-time errors (unrecognized arg, missing required, wrong nargs count, invalid choice, type-conversion failure) | raises `ValueError` -- propagates as TPy panic if uncaught | prints usage to stderr and `sys.exit(2)` (raises `SystemExit`) | macro_deps so synthesized code can call `sys.exit`; plus the Tier-1 ``--help`` printer to format the usage line. Until both land, every panic test against parse-time errors needs to live in a `panic_*` case (which auto-skips the cpy phase) |

## Three Macro Kinds

### Class macros (decorator macros) -- Phase 1, done

Applied as decorators on class definitions. The compiler loads the macro module
via CPython, invokes the function with a `ClassInfo` wrapper, and applies the
mutations back to the class AST before sema registration.

```python
@class_macro
def model(cls: ClassInfo) -> None:
    for field in cls.fields:
        ...  # inspect field.name, field.type
    cls.add_method(...)
```

### Call-site macros (expression macros) -- Phase 2, done

Look like function calls but expand inline at the call site. The macro receives
`MacroArg` wrappers (AST expr + resolved type) and returns a replacement `TpyExpr`.

```python
@call_macro
def asdict(ctx: CallMacroContext, obj: MacroArg) -> TpyExpr:
    fields = ctx.get_record_fields(obj.type.name)
    ...
    return TpyDictLiteral(keys=keys, values=values)
```

Kwargs annotated with simple types (`str`, `int`, `bool`) are auto-extracted
from literals. Macros raise `MacroError` for compile errors.

### Builder-trace macros (call-chain macros) -- Phase 7, done

Applied to a class whose constructor opens a *trace*: the compiler tracks the
bound symbol through the enclosing function body and dispatches method calls
on it to handlers on a Python state class. A `@builder_terminal` method
closes the trace and synthesizes module-level declarations (a typed record +
a parse function), splicing the result back into the body.

```python
@builder_macro
class ArgumentParser:
    def __init__(self, ctx: BuilderContext, args: list[MacroArg]) -> None:
        self.description = ctx.kwarg_str(args, "description")
        self.arguments: list[ArgSpec] = []

    @builder_method
    def add_argument(self, ctx, args) -> None:
        self.arguments.append(_parse_arg_spec(ctx, args))

    @builder_terminal
    def parse_args(self, ctx, args) -> TypeInfo:
        ns = ctx.emit_record(
            ctx.fresh_module_name("args"),
            [(s.dest, s.field_type) for s in self.arguments],
        )
        fn = ctx.emit_function(
            ctx.fresh_module_name("parse"),
            [("argv", types.list(types.str))],
            ns,
            _emit_parse_body(self),
        )
        ctx.replace_call(fn, args)
        return ns
```

v1 trace rules (hard-error otherwise):

- Tracked symbol may only appear in method calls handled by registered handlers
- No reassignment, no escape, no use inside conditionals/loops/`try`
- Exactly one `@builder_terminal` reached on the linear path through the body
- Macro-time kwargs (`default=`, `type=`, `choices=`, ...) must be literals or
  `Final` constants; non-literal expressions are a hard error

Use cases beyond argparse: any builder pattern with statically-knowable
configuration (logging setup, route registration, schema declaration).

## Execution Model

### Current (compiler in Python)

Macros execute as regular Python functions inside the compiler process.
The "macro VM" is just CPython.

### Self-hosted (compiler in TPy) -- Phase 6

**TpyMini VM** (preferred): A tree-walking interpreter embedded in the compiler
that supports a restricted subset of TPy:

Supported:
- Basic types: `int`, `str`, `bool`, `list`, `dict`
- Control flow: `if`/`elif`/`else`, `for`, `while`
- Functions (no generics, no overloads)
- String operations (essential for codegen)
- The AST/type introspection API

Not supported:
- Generics, type parameters (macros don't need to be generic)
- Ptr/Own/Span and the memory model
- C++ interop

## Custom Diagnostics

Macros emit their own compile errors and warnings via `ClassInfo`:

```python
@class_macro
def model(cls: ClassInfo) -> None:
    for field in cls.fields:
        if field.type.name == "str" and not field.has_default:
            cls.error("str fields in @model must have a default", loc=field.loc)
        if field.type.name == "list":
            cls.warning("consider using Array[T, N] for fixed-size data", loc=field.loc)
```

## CPython Compatibility (Phase 5)

**Goal**: Same macro source code works under both tpyc and CPython. Macro authors
write one implementation; user macros get CPython compatibility for free.

**Approach**: `lib/cpy/tpyc/macro_api.py` provides a CPython backend that
implements the same public API (`ast`, `types`, `ClassInfo`, `TypeInfo`, etc.)
but targets Python runtime objects instead of compiler AST nodes.

### How it works

Under **tpyc**: `@class_macro` runs at compile time. `ast.*` builders produce
TpyAST nodes. Sema analyzes them, codegen emits C++.

Under **CPython**: `@class_macro` runs at class definition time (as a decorator).
`ast.*` builders produce Python AST nodes via the `ast` stdlib module, which are
`compile()`'d into function objects and attached to the class.

```
Macro source (model.py)
    |
    +-- tpyc path: ast.* -> TpyAST -> sema -> C++
    |
    +-- CPython path: ast.* -> Python ast -> compile() -> function objects
```

The macro logic (field discovery, validation, type dispatch, method structure)
is identical. Only the AST backend differs.

### CPython macro_api components

**`lib/cpy/tpyc/macro_api.py`** must provide:

| Component | tpyc behavior | CPython behavior |
|-----------|--------------|------------------|
| `@class_macro` | Registers macro for compile-time invocation | Returns a decorator that runs at class definition time |
| `ClassInfo` | Wraps `TpyRecord` | Wraps Python class + `__annotations__` |
| `TypeInfo` | Wraps `TpyType` | Wraps Python type annotations (`int`, `str`, `list[X]`, etc.) |
| `FieldInfo` | Wraps compiler `FieldInfo` | Wraps `(name, annotation, default)` tuples |
| `ast.*` builders | Produce `TpyExpr`/`TpyStmt` nodes | Produce Python `ast` module nodes |
| `types.*` properties | Return `TpyType` singletons | Return Python type objects or markers |
| `ast.function()` | Returns `TpyFunction` | Returns compiled Python function via `compile()` + `exec()` |
| `macro_deps()` | Registers compile-time module deps | No-op (CPython resolves imports normally) |

### TypeInfo under CPython

TypeInfo predicates inspect Python type annotations:

```python
# CPython TypeInfo wraps a Python type annotation
TypeInfo.from_python_type(int)        # is_int = False (Python int = BigInt)
TypeInfo.from_python_type(Int32)      # is_int = True (from lib/cpy/tpy/)
TypeInfo.from_python_type(str)        # is_str = True
TypeInfo.from_python_type(list[str])  # is_list = True, type_args = [TypeInfo(str)]
```

Uses `typing.get_type_hints()` and `typing.get_args()`/`typing.get_origin()`
for generic types.

### ClassInfo under CPython

```python
@class_macro
def model(cls: ClassInfo, *, frozen: bool = False) -> None:
    # cls.fields -- from __annotations__ + defaults
    # cls.add_method(func) -- func is a compiled Python function
    # cls.set_match_args(all_fields)
    ...
```

`ClassInfo` wraps a Python class. `add_method()` attaches compiled functions.
`apply_to_record()` applies mutations back to the class.

### AST builders under CPython

The key challenge. `ast.*` builders must produce Python `ast` module nodes that
can be compiled into executable functions.

Example -- `ast.method_call(reader, "read_str")` produces:

```python
# tpyc: TpyMethodCall(obj=reader, method="read_str", args=[])
# CPython: ast.Call(func=ast.Attribute(value=reader, attr="read_str"), args=[])
```

`ast.function()` collects all body statements, wraps them in an `ast.FunctionDef`,
calls `compile()`, and returns the function object.

### Runtime dependencies

Macro-generated methods may reference runtime types (e.g., `JsonReader`).
Under CPython, these must exist as Python classes:

- `lib/cpy/tplib/json/parser.py` -- `JsonReader` backed by Python's `json` module
- `lib/cpy/tplib/json/writer.py` -- `JsonWriter` backed by Python's `json` module

These are NOT parallel implementations of the macro logic -- they're runtime
support classes that the generated code calls. The macro logic is shared.

### Implementation phases

1. **CPython `ClassInfo`/`TypeInfo`/`FieldInfo`** -- wrap Python classes and
   annotations. Support `set_match_args`, `add_method`, field discovery.
2. **CPython `ast.*` expression builders** -- produce `ast.Name`, `ast.Call`,
   `ast.BinOp`, `ast.Attribute`, literals, subscript, comprehensions.
3. **CPython `ast.*` statement builders** -- `ast.Assign`, `ast.Return`,
   `ast.If`, `ast.While`, `ast.For`, `ast.Raise`, `ast.Try`, `ast.Match`.
4. **CPython `ast.function()`** -- assemble body into `ast.FunctionDef`,
   `compile()`, `exec()` into function object.
5. **CPython `types.*`** -- return Python type objects or marker classes.
6. **`@class_macro` decorator** -- intercept class definition, construct
   `ClassInfo`, invoke macro, apply results.
7. **Runtime stubs** -- `JsonReader`/`JsonWriter` CPython implementations.
8. **Remove `no_cpython.txt`** from `@model` tests, verify the CPython compatibility phase in `test_case` passes.

### Design constraints

- **No parallel logic**: The macro module (`model.py`) is the single source of
  truth. The CPython backend implements the `ast.*`/`types.*`/`ClassInfo` API,
  not the serialization logic.
- **Forward-compatible with TpyMini VM** (Phase 6): The CPython backend is a
  prototype of the macro VM. The API surface it implements is what TpyMini must
  support.
- **Minimal runtime surface**: Only `tpyc.macro_api` is importable in macros
  (enforced by import restriction). The CPython backend must provide exactly
  this surface.

## Source-based Macro Authoring (Phase 3+4)

**Goal**: Let macro authors write generated code as TPy source strings instead of
AST builder calls. The AST builder remains the power-user API for core modules
(`dataclasses.py`, `model.py`) where full control matters. Source-based authoring
is for user macros where readability and ease of writing matter more.

### Motivation

The AST builder API is verbose for methods with static structure. A `@builder`
macro generating setter methods requires ~8 builder calls per field for what's
naturally a 3-line function:

```python
# AST builder (current)
for field in cls.fields:
    body = [
        ast.assign(ast.field_access(ast.name("self"), field.name), ast.name("value")),
        ast.return_(ast.name("self")),
    ]
    cls.add_method(ast.function(
        f"set_{field.name}", [("value", field.type.raw_type)],
        self_type, body, is_method=True,
    ))

# Source-based (proposed)
for field in cls.fields:
    cls.add_method_from_source(f"""
def set_{field.name}(self, value: {field.type.name}) -> {cls.name}:
    self.{field.name} = value
    return self
""")
```

### Performance

Macro expansion is <2% of total compile time (benchmarked: 15 `@model` classes
with ~50 fields total = 3.5ms out of 200ms). Adding `ast.parse()` calls for
small method bodies adds microseconds -- negligible vs sema, codegen, and C++
compilation.

### API

Three `quote` methods on `AstBuilder`, plus a convenience on `ClassInfo`:

**`ast.quote(source: str) -> list[Stmt]`** -- parse TPy statements:

```python
stmts = ast.quote(f"""
x = self.{field.name}
if x is None:
    raise ValueError
""")
```

**`ast.quote_expr(source: str) -> Expr`** -- parse a single expression:

```python
default = ast.quote_expr(f"{field.type.name}()")
```

**`ast.quote_fun(source: str) -> Function`** -- parse a complete function
definition:

```python
func = ast.quote_fun(f"""
def validate(self) -> bool:
    return self.{field.name} > 0
""")
cls.add_method(func)
```

**`cls.add_method_from_source(source: str)`** -- convenience, equivalent to
`cls.add_method(ast.quote_fun(source))`:

```python
@class_macro
def builder(cls: ClassInfo) -> None:
    for field in cls.fields:
        cls.add_method_from_source(f"""
def set_{field.name}(self, value: {field.type.name}) -> {cls.name}:
    self.{field.name} = value
    return self
""")
```

### Interpolation model

**Phase 3+4**: f-string interpolation only. Macro authors splice names, type
names, and literals as strings. The source string must be syntactically valid
Python after interpolation.

```python
# Works: splicing names and type names
f"def get_{field.name}(self) -> {field.type.name}:"

# Works: splicing literal values
f"x = {default_value}"

# Does NOT work: splicing AST node objects
f"x = {some_expr_node}"  # produces "<TpyName object ...>"
```

**Future extension -- AST splicing**: Embed computed `Expr`/`Stmt` nodes into
quoted code via an `$unquote()` or `${expr}` mechanism. This enables patterns
like building a chain of comparisons for `__eq__` from a dynamic field list:

```python
# Future: splice computed AST nodes
eq_body = ast.quote(f"""
return ${{comparisons_expr}}
""")
```

This requires a custom parser pass (not just f-string + `ast.parse()`) and is
deferred to a later phase. The current f-string approach covers the common case
of static method shapes with varying names/types.

### Implementation

Both `quote` and `add_method_from_source` use the same underlying primitive:

```python
# In tpyc/parse/parser.py
@classmethod
def parse_fragment(cls, source: str, kind: str = "function") -> ...:
    """Parse a TPy source fragment without full module context.
    kind: "function" | "statements" | "expression"
    """
```

Creates a throwaway `TpyParser` with minimal state. Type annotations in the
fragment are left as unresolved names -- sema resolves them later, same as
AST builder nodes like `ast.name("Int32")`.

The CPython backend (`lib/cpy/tpyc/macro_api.py`) implements the same methods
using Python's `ast.parse()`, consistent with how `ast.function()` already
works there.

### What this doesn't replace

The AST builder remains necessary when:

- Method structure depends on runtime macro logic (e.g., `build_eq` chains N
  comparisons based on field count)
- You need to build AST nodes conditionally per field type (e.g., `model.py`
  type-dispatching read/write logic)
- Core library macros where the builder's explicitness is preferred

The recommended pattern: use `quote`/`add_method_from_source` for the overall
method shape, and the AST builder for computed fragments within it. Full
integration of both (via AST splicing) is a future extension.

## Limitations

Macros explicitly can NOT:

- **Create new syntax** -- macro calls and decorators must be valid Python
- **Modify code outside their scope** -- a class macro can only modify the
  decorated class, not other classes or module-level code
- **Run at runtime** -- macros are strictly compile-time
- **Depend on runtime values** -- macro inputs must be statically known

## Open Questions

1. **Macro ordering**: Can macros compose? If a class has both `@model` and
   `@proto.message`, what's the application order? (Probably: inner-to-outer,
   like Python decorators.)

2. **Compile-time file I/O**: Should macros be able to read files (e.g. a
   `.proto` schema)? Useful but needs caching/rebuild triggers.

3. **Performance budget**: Should there be limits (max execution time, max AST
   size) to prevent runaway macros?

## Prior Art

- **Rust proc_macro**: Three kinds (derive, attribute, function-like). Operate
  on token streams. Separate crate requirement.
- **Nim macros/templates**: Written in Nim itself, operate on typed AST.
  Executed by an embedded VM (nimvm).
- **Zig comptime**: No separate macro language -- same Zig code runs at compile
  time. Types are first-class values at comptime.
- **Swift macros** (5.9+): Freestanding and attached macros. Operate on
  SwiftSyntax trees. Written in Swift, run as compiler plugins.
- **C++26 reflection** (P2996): `consteval` functions that inspect types via
  `std::meta::info`. Can iterate struct members, generate code.
