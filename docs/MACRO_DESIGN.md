# Macro System Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Class macros: `@class_macro` decorator, `ClassInfo`/`FieldInfo`/`TypeInfo` API, `# tpy: macro_module` directive, `@dataclass` replacement | Done |
| 1b | Macro kwargs validation: typed macro signatures with automatic validation via `inspect.signature` | Done |
| 1c | Generic `field()` handling: `Field` descriptor + `field()` function in macro module, `call_macro_field_function` infrastructure | Done |
| 2 | Call-site macros: `@call_macro` functions expand at call site, receive `MacroArg` (expr + type), return replacement `TpyExpr`. First use cases: `asdict`/`astuple` | Done |
| 3 | Quote templates: `quote()` syntactic sugar for less verbose macro authoring | Not started |
| 4 | String-based method generation: `add_method_from_source` (parse TPy source strings) | Not started |
| 5 | CPython compatibility: dual `__init_subclass__` / `@class_macro` path | Not started |
| 6 | TpyMini VM: tree-walking interpreter for self-hosted compiler | Not started |

### Future Extensions

| Feature | Notes |
|---------|-------|
| Hygiene | Macro-generated names get unique internal names to avoid shadowing. Opt-out via `unhygienic(name)` |
| `--expand-macros` flag | CLI flag to dump macro expansions for debugging |
| Macro expansion trace in errors | Diagnostics include trace pointing to the macro that generated invalid code |
| `static_read()` | Compile-time file I/O (e.g. reading `.proto` schemas). Needs caching/rebuild triggers |
| `macro_note()` | Informational diagnostic hint (shown with related errors) |
| Macro ordering / composition | Multiple macros on one class, inner-to-outer application order |
| FieldInfo.metadata | Typed metadata for macro-specific field annotations (e.g. `proto.Field`) |
| Replace codegen special cases | Macros generate full `__repr__`/`__hash__`/ordering bodies as AST, not stubs. Requires `repr()` builtin that codegen maps to type-aware formatting |
| `TpyBlockExpr` | Block expression: sequence of statements + result expression. Codegen hoists statements to enclosing scope. Enables `asdict` with mixed-type fields (typed dict creation + subscript assigns) |

---

## Goals

- Enable library-level code generation without compiler changes
- All macro definitions must be valid Python (runnable in CPython)
- Two macro kinds: class macros (Phase 1) and call-site macros (Phase 2)

## Architecture (Phase 1)

### Key Files

| File | Purpose |
|------|---------|
| `tpyc/macro_api.py` | Public API: `ClassInfo`, `FieldInfo`, `TypeInfo`, `MacroArg`, `CallMacroContext`, `MacroError`, `class_macro`, `call_macro` |
| `tpyc/macro_loader.py` | `MacroRegistry`, `validate_and_call_macro`, `call_macro_field_function`, `expand_call_macro` |
| `lib/tpy/dataclasses.py` | `@dataclass` class macro, `Field`/`field()`, `asdict`/`astuple` call macros |
| `lib/tpy/enum.py` | `Enum`, `IntEnum`, `auto()` -- resolved via import tracking, not yet macro-driven |

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
    is_dataclass: bool             # settable
    is_frozen: bool                # settable
    is_ordered: bool               # settable

    def add_method(self, func: TpyFunction) -> None: ...
    def add_method_stub(self, name, params, return_type, is_readonly=False) -> None: ...
    def has_method(self, name: str) -> bool: ...
    def get_parent_fields(self) -> list[FieldInfo]: ...
    def set_dataclass_fields(self, fields: list[FieldInfo]) -> None: ...
    def warning(self, msg: str, loc=None) -> None: ...
    def error(self, msg: str, loc=None) -> NoReturn: ...
```

`add_method` injects a `TpyFunction` AST node (power user API). `add_method_stub`
registers a `FunctionInfo` signature without a body -- codegen generates the C++
(used for `__repr__`, `__hash__`, ordering where codegen has type-aware formatting).

### Macro Module Format

```python
# tpy: macro_module
from tpyc.macro_api import ClassInfo, class_macro, build_init, build_eq

@class_macro
def my_decorator(cls: ClassInfo, *, option: bool = False) -> None:
    # inspect cls.fields, cls.type_params, etc.
    # add methods via cls.add_method() or cls.add_method_stub()
    # set flags via cls.is_dataclass, cls.is_frozen, etc.
    # emit diagnostics via cls.warning() or cls.error()
    pass
```

Macro modules use `# tpy: macro_module` directive. They are executed via CPython
during compilation and never compiled to C++. They can import from `tpyc.macro_api`
and Python stdlib only.

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

### 2. Protocol serializers (protobuf, FlatBuffers)

```python
from tpy import proto

@proto.message
class TradeMessage:
    id: Int64 = proto.Field(1)
    symbol: FixStr[8] = proto.Field(2)
    price: Float64 = proto.Field(3, optional=True)
```

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

### 5. Builder pattern

```python
@builder
class Config:
    host: str
    port: Int32 = 8080
    timeout: Float64 = 30.0
```

### 6. Compile-time regex validation (call-site macro, Phase 2)

```python
r = regex("[a-z]+")       # ok
r2 = regex("[invalid(")   # compile error: malformed regex pattern
```

## Two Macro Kinds

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

Same source, two execution paths:

```python
class Model:
    # CPython path: __init_subclass__ runs at class definition time
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        fields = get_fields(cls)
        cls.to_json = make_to_json(fields)

    # tpyc path: @class_macro runs during compilation
    @class_macro
    @classmethod
    def __generate__(cls, info: ClassInfo) -> None:
        for field in info.fields:
            ...
```

The `@class_macro` decorator is a no-op in CPython (just returns the function
unchanged). The `__init_subclass__` path is ignored by tpyc.

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
