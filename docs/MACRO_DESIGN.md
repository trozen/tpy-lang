# Macro System Design (Draft)

Status: early design, not yet planned for implementation.

## Goals

- Enable library-level code generation without compiler changes
- All macro definitions must be valid Python (runnable in CPython)
- Hygienic by default (macro-generated names don't leak into caller scope)
- Two macro kinds: class macros and call-site macros

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

`proto.Field()` carries metadata (field number, wire type hints,
`optional`/`repeated` flags). The macro accesses it via `field.metadata`.
Namespaced under `proto` to avoid collision with model's `Field()` and
to allow related types like `proto.Enum`, `proto.OneOf`.

Generates `serialize() -> bytes` and `deserialize(data: bytes) -> TradeMessage`
with wire-format encoding. The macro reads field types + metadata and emits
type-specific encode/decode calls.

### 3. Zero-alloc logging

```python
log(INFO, "user {} traded {} @ {}", user_id, symbol, price)
```

Expands at compile time to type-aware writes into a fixed buffer:

```python
# generated:
buf = FixedBuffer(256)
buf.write_str("user ")
buf.write_int(user_id)
buf.write_str(" traded ")
buf.write_fixstr(symbol)
buf.write_str(" @ ")
buf.write_float(price)
logger.submit(buf)
```

No intermediate string allocation. Suitable for `@noalloc` hot paths.

### 4. Format strings (like Rust's `format!`)

```python
s = fmt("{} + {} = {}", a, b, a + b)
```

Parses format string at compile time, generates type-checked formatting calls.
No `str()` boxing of each argument.

### 5. Derive-style method generation

```python
@derive(Eq, Hash, Repr)
class Point:
    x: Int32
    y: Int32
```

Generalizes what `@dataclass` does as a hardcoded compiler feature into a
user-extensible system.

### 6. Builder pattern

```python
@builder
class Config:
    host: str
    port: Int32 = 8080
    timeout: Float64 = 30.0
    max_retries: Int32 = 3

# usage:
cfg = Config.builder().host("localhost").timeout(5.0).build()
```

Generates a fluent builder API for objects with many optional fields.
The macro creates a `ConfigBuilder` class with setter methods that return
`self`, and a `build()` that validates required fields and constructs
the final object.

### 7. Compile-time regex validation

```python
r = regex("[a-z]+")       # ok
r2 = regex("[invalid(")   # compile error: malformed regex pattern
```

Call-site macro that parses the regex pattern at compile time. If the
pattern is malformed, it's a compile error (not a runtime crash).
Can also generate an optimized matcher instead of interpreting the
pattern at runtime.

### 8. Test mocks

```python
@mock
class MockDatabase(DatabaseProtocol):
    pass

# generates stub methods matching DatabaseProtocol:
#   query() -> records calls, returns default
#   insert() -> records calls, returns default
# plus: mock.assert_called("query", times=2)
```

Inspects a protocol's method signatures and generates stub
implementations that record calls for assertion. Useful for unit testing
without manual mock boilerplate.

### 9. Compile-time lookup tables

```python
@constexpr
def crc32_table() -> Array[UInt32, 256]:
    table = [UInt32(0)] * 256
    for i in range(256):
        crc = UInt32(i)
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ UInt32(0xEDB88320)
            else:
                crc >>= 1
        table[i] = crc
    return table

TABLE: Final = crc32_table()  # embedded in binary, zero runtime cost
```

Overlaps with the `@constexpr` feature (see TODO.md). Macros could
provide the more complex cases where C++ `constexpr` alone isn't enough.

## Two Macro Kinds

### Class macros (decorator macros)

Applied as decorators on class or function definitions.

```python
@macro
def model(cls: ClassInfo) -> ClassInfo:
    for field in cls.fields:
        ...  # inspect field.name, field.type
    cls.add_method(...)
    return cls
```

The compiler:
1. Resolves the class fields and types (post-sema)
2. Sees the decorator was defined with `@macro`
3. Invokes the macro function, passing class metadata
4. Incorporates returned methods/modifications into codegen

### Call-site macros (expression/statement macros)

Look like function calls but expand inline at the call site.

```python
@macro
def log(level: Expr, fmt_str: Expr, *args: Expr) -> Stmt:
    # parse fmt_str (must be a string literal)
    # for each {} placeholder, emit a type-aware write
    ...
```

The compiler:
1. Sees a call to a `@macro`-defined function
2. Passes argument AST nodes (not evaluated values) to the macro
3. Replaces the call with the returned AST

The call site looks like normal Python -- no special syntax needed.

## Execution Model

### Current (compiler in Python)

Macros execute as regular Python functions inside the compiler process.
The "macro VM" is just CPython. Trivial to implement.

### Self-hosted (compiler in TPy)

Macros need a compile-time execution environment. Options:

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
- Imports beyond a blessed set of compile-time modules

The VM doesn't need to support generics itself -- it only needs to
*inspect* generic types in the target code.

Unsupported features produce clear errors:
"generics not supported in compile-time macros"

## Macro API

### Type introspection

Macros receive type info through a high-level API, not raw AST:

```python
class FieldInfo:
    name: str
    type: TypeInfo
    default: Expr | None
    has_default: bool

class TypeInfo:
    name: str              # "Int32", "list", "Optional", etc.
    type_args: list[TypeInfo]  # e.g. list[Int32] -> [TypeInfo("Int32")]
    is_optional: bool
    is_generic: bool
    is_record: bool

class ClassInfo:
    name: str
    fields: list[FieldInfo]
    methods: list[MethodInfo]
    base_classes: list[TypeInfo]
    # mutation:
    def add_method(self, method: MethodDef) -> None: ...
    def add_field(self, field: FieldInfo) -> None: ...
```

### AST builder (Phase 1)

Explicit builder calls. Verbose but unambiguous and valid Python:

```python
@macro
def assert_eq(a: Expr, b: Expr) -> Stmt:
    msg = f"Expected {a.source()} == {b.source()}"
    return ast.If(
        test=ast.NotEq(a, b),
        body=[ast.Call("panic", [ast.Str(msg)])]
    )
```

### Quote templates (Phase 2 -- syntactic sugar)

A `quote()` helper that parses a template string with `$` splicing:

```python
@macro
def assert_eq(a: Expr, b: Expr) -> Stmt:
    msg = f"Expected {a.source()} == {b.source()}"
    return quote("if $a != $b: panic($msg)", a=a, b=b, msg=ast.Str(msg))
```

`quote()` parses the template at macro definition time and substitutes
splice points. This is purely syntactic sugar over the AST builder --
no new language constructs needed.

## Hygiene

Macros are hygienic by default:
- Variables introduced by the macro get unique internal names
- They don't shadow or conflict with variables at the expansion site
- The macro author can opt out with `unhygienic(name)` for intentional
  name injection (e.g. a macro that defines `self.field_name`)

## CPython Compatibility

Same source, two execution paths:

```python
class Model:
    # CPython path: __init_subclass__ runs at class definition time
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        fields = get_fields(cls)
        cls.to_json = make_to_json(fields)
        cls.from_json = classmethod(make_from_json(fields))

    # tpyc path: @macro runs during compilation
    @macro
    @classmethod
    def __generate__(cls, info: ClassInfo) -> ClassInfo:
        for field in info.fields:
            ...
        return info
```

The `@macro` decorator is a no-op in CPython (just returns the function
unchanged). The `__init_subclass__` path is ignored by tpyc (it sees the
`@macro` hook instead).

## Phasing

### Phase 1: Class macros with AST builder
- `@macro` on class decorators
- ClassInfo/FieldInfo/TypeInfo API
- Explicit AST builder
- Runs in CPython (compiler is Python)
- Sufficient for: @model, @protobuf, @derive

### Phase 2: Call-site macros
- `@macro` on function definitions
- Expr/Stmt AST node arguments
- Sufficient for: log(), fmt(), static_assert()

### Phase 3: Quote templates
- `quote()` syntactic sugar
- Makes macro authoring less verbose

### Phase 4: TpyMini VM (for self-hosting)
- Tree-walking interpreter for macro execution
- Restricted TPy subset
- Only needed if/when compiler is self-hosted

## Custom Diagnostics

Macros need a way to emit their own compile errors and warnings. Without
this, mistakes surface as cryptic failures in generated code.

```python
@macro
def model(cls: ClassInfo) -> ClassInfo:
    for field in cls.fields:
        if field.type.name == "str" and not field.has_default:
            macro_error(field, "str fields in @model must have a default")
        if field.type.name == "list":
            macro_warning(field, "consider using Array[T, N] for fixed-size data")
    ...
```

API:
- `macro_error(node, message)` -- abort compilation with error at the node's location
- `macro_warning(node, message)` -- emit warning, continue compilation
- `macro_note(node, message)` -- informational hint (shown with related errors)

The `node` parameter provides source location so the diagnostic points at
the user's code, not the macro internals.

## Interaction with Other TPy Features

### @noalloc

Macro-generated methods can carry effect annotations:

```python
@macro
def model(cls: ClassInfo) -> ClassInfo:
    # generated serialize() is noalloc-safe if all fields are fixed-size
    if all(is_fixed_size(f.type) for f in cls.fields):
        cls.add_method(serialize_method, decorators=["noalloc"])
    else:
        cls.add_method(serialize_method)
    ...
```

The compiler validates `@noalloc` on macro-generated methods the same way
as on hand-written ones. No special treatment.

### Generics

Macros can generate code that uses generic types, but the macro function
itself doesn't need to be generic. The macro inspects `TypeInfo.type_args`
and emits the appropriate specialized code:

```python
# user writes:
@model
class Pair:
    items: list[Int32]

# macro sees: field.type.name == "list", field.type.type_args == [TypeInfo("Int32")]
# macro emits: serialize code that iterates and writes Int32 elements
```

### Protocols

A macro can add protocol conformance to a class by generating the
required methods:

```python
@codable  # generates encode()/decode(), adds Codable conformance
class User:
    name: str
    age: Int32
```

The macro generates methods that satisfy the `Codable` protocol. The
compiler's normal protocol checking validates correctness after expansion.

## Debugging and Introspection

Seeing what a macro generated is critical for adoption and debugging.

### --expand-macros flag

```bash
# dump all macro expansions
uv run tpyc --expand-macros src/main.py

# filter by macro name
uv run tpyc --expand-macros=model src/main.py

# combine with --dump-code to see final C++
uv run tpyc --expand-macros --dump-code src/main.py
```

Output shows the class before and after macro expansion, with generated
methods clearly marked:

```
@model class Order:
  + generated __init__(self, symbol: FixStr[8], price: Float64, quantity: Int32)
  + generated to_json(self) -> str
  + generated from_json(data: JsonValue) -> Order
  + generated __eq__(self, other: Order) -> bool
```

### Macro expansion trace in errors

When a macro generates invalid code, the diagnostic includes a trace:

```
error: type 'FixStr[8]' has no method 'to_string'
  --> src/main.py:3:5
   |
3  |     symbol: FixStr[8]
   |     ^^^^^^
   = in method 'to_json' generated by @model (macros/model.py:42)
```

## Limitations

Macros explicitly can NOT:

- **Create new syntax** -- macro calls and decorators must be valid Python
- **Modify code outside their scope** -- a class macro can only modify the
  decorated class, not other classes or module-level code
- **Run at runtime** -- macros are strictly compile-time; the `@macro`
  decorator is a no-op in the generated binary
- **Access the filesystem** (Phase 1) -- no file I/O during compilation;
  may be relaxed in later phases with `static_read()` (see open questions)
- **Perform I/O or network calls** -- macros are pure code transformations
- **Depend on runtime values** -- macro inputs must be statically known
  (types, literals, AST structure); they can't branch on a variable's
  runtime value

## Open Questions

1. **Macro discovery**: How does the compiler find macro definitions? Import-based
   (must import the module that defines the macro)? Or scan for `@macro`?
   Import-based is simpler and more explicit.

2. **Macro ordering**: Can macros compose? If a class has both `@model` and
   `@protobuf`, what's the application order? (Probably: inner-to-outer,
   like Python decorators.)

3. **Error reporting**: When a macro generates invalid code, how do we trace
   the error back to the macro source? Need a "macro expansion trace" in
   diagnostics (see Debugging section above for proposed design).

4. **Compile-time file I/O**: Should macros be able to read files (e.g. a
   `.proto` schema)? Nim has `staticRead`. This is useful but opens a can
   of worms (caching, rebuild triggers).

5. **Macro testing**: How do users test their macros? Probably: write a class
   that uses the macro, compile it, verify the output. The `--expand-macros`
   flag helps, but a dedicated macro test harness (assert on generated AST)
   would be better.

6. **Performance budget**: Macros run during compilation. Should there be
   limits (max execution time, max AST size) to prevent runaway macros?

7. **FieldInfo.metadata shape**: What's the type of `field.metadata`? A generic
   `dict[str, Any]`? Or macro-specific typed metadata (e.g. `proto.Field`
   returns a `ProtoFieldMeta`)? Typed metadata is safer but requires the
   macro API to understand metadata types.

## Prior Art

- **Rust proc_macro**: Three kinds (derive, attribute, function-like). Operate
  on token streams. Separate crate requirement. Hygienic for `macro_rules!`,
  unhygienic for proc macros. [Reference](https://doc.rust-lang.org/reference/procedural-macros.html)

- **Nim macros/templates**: Written in Nim itself, operate on typed AST.
  Templates are simple substitution, macros are full AST rewriting. Executed
  by an embedded VM (nimvm). Unhygienic by default with opt-in `genSym`.
  [Tutorial](https://nim-lang.org/docs/tut3.html)

- **Zig comptime**: No separate macro language -- same Zig code runs at compile
  time. Types are first-class values at comptime. Can generate structs, lookup
  tables, validate inputs. Limited: no allocation, no I/O, no recursion depth
  beyond limit. [Guide](https://zig.guide/language-basics/comptime/)

- **Swift macros** (5.9+): Freestanding and attached macros. Operate on
  SwiftSyntax trees (structured AST, not tokens). Written in Swift, run as
  compiler plugins. Type-checked expansions. [Docs](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/macros/)

- **C++26 reflection** (P2996): Not macros per se, but `consteval` functions
  that inspect types via `std::meta::info`. Can iterate struct members, generate
  code. Will likely reduce the need for macro-based codegen in C++.
  [Proposal](https://isocpp.org/files/papers/P2996R4.html)
