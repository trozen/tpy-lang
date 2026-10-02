# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Extern decorator stubs. builtin_decorator is the bootstrap primitive
# (auto-resolved within this module via _EXTERN_KEYWORDS). All other
# decorators are defined using @builtin_decorator.

@builtin_decorator("tpy.extern.builtin_decorator")
def builtin_decorator(key: str): ...

@builtin_decorator("tpy.extern.builtin_type")
def builtin_type(key: str): ...

@builtin_decorator("tpy.extern.builtin_function")
def builtin_function(key: str): ...

# Class-level `borrowing_view=True`: every value of the (value) type is a
# borrow handle into storage it does not own, even a copy of it, so the
# compiler lifetime-checks it at returns and yields. An unannotated native
# value type is treated as owning its data.
# `_iter_yields_ref_tuple_proxies=True` is an INTERNAL stopgap, not API: it
# marks dict_items, whose iterator yields a tuple of references BY VALUE, so a
# generator/async frame's for-loop binds the element instead of taking its
# address. It goes away once the runtime picks that binding from the
# iterator's reference type (TODO.md: "Remove `_iter_yields_ref_tuple_proxies`").
# Function-level `transient=True` (here and on @cpp_template): the bound C++
# reads or writes only its arguments (as their declared mutability allows),
# retains nothing after return or raise, reaches no other TPy storage and
# runs no user code. @pure implies it. Each use is an audit of the binding.
# Function-level `checks_signals=True` (here and on @cpp_template): the bound
# C++ is a Ctrl-C check point (it calls `::tpy::check_signals()` or raises
# KeyboardInterrupt itself), so a cleanup body calling it needs deferral.
@builtin_decorator("tpy.extern.native")
def native(name: str = "", function: bool = False, binding: str = "",
           cpp_return_type: type | None = None, indirecting: bool = False,
           borrowing_view: bool = False, transient: bool = False,
           checks_signals: bool = False,
           _iter_yields_ref_tuple_proxies: bool = False): ...

@builtin_decorator("tpy.extern.export")
def export(name: str = "", binding: str = ""): ...

# Prefer @native over @cpp_template -- use only when @native can't express the
# call (e.g. wrapping in a constructor, type cast, or non-trivial expression).
@builtin_decorator("tpy.extern.cpp_template")
def cpp_template(template: str, transient: bool = False,
                 checks_signals: bool = False): ...

@builtin_decorator("tpy.extern.value_ptr_coercion")
def value_ptr_coercion(): ...

@builtin_decorator("tpy.extern.native_preserves_refs")
def native_preserves_refs(): ...

# Marks an accessor whose Own[V] result is a copy where the method's CPython
# namesake aliases -- so mutating the result is a silent no-op. sema warns at
# such call sites (escape: copy(), or an aliasing accessor). Library-declared
# so the compiler holds no per-type knowledge.
@builtin_decorator("tpy.extern.copy_returns_warn")
def copy_returns_warn(): ...

# Marks a native class whose hand-written C++ __raise__ is NOT equivalent to
# a fresh `throw ClassName(args)` (it dispatches -- e.g. OSError's ctor-time
# errno -> subclass mapping), so codegen must route `raise ClassName(args)`
# through __raise__ instead of the fresh-construction throw peephole.
# Library-declared so the compiler holds no per-type knowledge. Applies to
# the decorated class only, never inherited: subclasses' own __raise__
# overrides are plain `throw *this`.
@builtin_decorator("tpy.extern.virtual_raise")
def virtual_raise(): ...

# Workaround for Python 3.12 -- Python 3.13+ has native type param defaults
# (PEP 696): def round[T = int](x: float) -> T: ...
@builtin_decorator("tpy.extern.type_param_default")
def type_param_default(): ...

# Sentinel for @type_param_default: resolves to the configured --default-int type.
# TODO: make this a real type alias (e.g. `type DefaultInt = ...`) with compiler
# support, so it can be used in type annotations beyond @type_param_default.
@builtin_type("tpy.extern.DefaultInt")
class DefaultInt: pass


# Native global variable declarations for C/C++ interop.
# TODO: design a macro function mechanism so these set variable properties
# (linkage, extern name) through the macro system instead of special handling.
@builtin_function("tpy.extern.native_global")
def native_global(name: str = "", binding: str = "", array: bool = False): ...

# Per-field C++ rename on @native classes. Used in the default-value slot of
# an annotated field on an @native class; the compiler extracts the rename
# string and drops the expression (no initializer is emitted for native fields).
@builtin_function("tpy.extern.native_field")
def native_field(name: str): ...

# Per-member C++ rename on @native enums. Used in the value slot of an enum
# member; the compiler extracts the rename string and treats the value as
# unspecified (the C++ side is the source of truth for the actual value).
# Useful when the C++ enumerator name clashes with a Python keyword
# (`None`, `True`, `False`) or follows a different naming convention.
@builtin_function("tpy.extern.native_member")
def native_member(name: str): ...
