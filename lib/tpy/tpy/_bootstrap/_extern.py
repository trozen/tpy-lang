__tpy__ = {"mod": "native",
           "cpp_namespace": "tpystd::tpy"}
# Extern decorator stubs. builtin_decorator is the bootstrap primitive
# (auto-resolved within this module via _EXTERN_KEYWORDS). All other
# decorators are defined using @builtin_decorator.

@builtin_decorator("tpy.extern.builtin_decorator")
def builtin_decorator(key: str): ...

@builtin_decorator("tpy.extern.builtin_type")
def builtin_type(key: str): ...

@builtin_decorator("tpy.extern.builtin_function")
def builtin_function(key: str): ...

# Declared here rather than in tpy._typing (which imports this module and
# re-exports it) so a decorator stub below can annotate a keyword that
# names a function by reference.
@builtin_type("typing.Callable")
class Callable: ...

# Class-level `borrowing_view=True`: every value of the (value) type is a
# borrow handle into storage it does not own, even a copy of it, so the
# compiler lifetime-checks it at returns and yields. An unannotated native
# value type is treated as owning its data.
# `_iter_yields_ref_tuple_proxies=True` is an INTERNAL stopgap, not API: it
# marks dict_items, whose iterator yields a tuple of references BY VALUE, so a
# generator/async frame's for-loop binds the element instead of taking its
# address. It goes away once the runtime picks that binding from the
# iterator's reference type (TODO.md: "Remove `_iter_yields_ref_tuple_proxies`").
# Class-level `elements=True`: a value owns the values of its type arguments
# as elements in storage of its own. The members are the type parameters its
# readonly `__iter__` yields and its `__getitem__` returns; a borrow can point
# into them, and a mutating method may move or free them.
# Method-level `element_access=True` / `element_access=<twin>` (here and on
# @cpp_template), only on `__getitem__` / `__setitem__` / `__delitem__`: the
# variant performs the container's own operation on the slot the key names
# and nothing else (docs/NATIVE_INTEROP.md). A twin, named by reference so it
# must be defined above the variant, is a @cpp_template method of the same
# class doing the same without the bounds check, valid only where the index
# is proven in `0 <= i < len(self)`. The `Callable` annotation is how the
# schema spells "a method named by reference". Each use is an audit of the
# binding.
# Function-level `transient=True` (here and on @cpp_template): the bound C++
# reads or writes only its arguments (as their declared mutability allows),
# retains nothing after return or raise, reaches no other TPy storage and
# runs no user code. @pure implies it. Each use is an audit of the binding.
# Function-level `checks_signals=True` (here and on @cpp_template): the bound
# C++ is a Ctrl-C check point (it calls `::tpy::check_signals()` or raises
# KeyboardInterrupt itself), so a cleanup body calling it needs deferral.
# `result={...}` / `args={...}` (here and on @cpp_template): the binding's
# contract, keyed by parameter NAME (`"self"` for a method's receiver); a
# dict of str to str is how the schema spells it. Each use is an audit of
# the binding.
# `result=` says what the result holds of each argument: "borrow" -- it IS
# the argument (or lives inside its storage); "element" -- it is handed out
# by ITERATING the argument, so a container lends its elements and an
# iterator never does (its step is valid only until the next); "adopts"
# (free functions only) -- the bound C++ has an rvalue overload that moves a
# FRESH argument into the result, so the call keeps nothing of its storage
# and the caller holds no temporary for it (a named one is lent as the
# signature says); "none" -- nothing. A map with a "borrow" / "element"
# names every parameter that can lend storage; "none" only there. "borrow" also
# admits an owned leaf the C++ passes by reference (`int`, `String`) when
# the result is an owned value: `std::min` over `const BigInt&` hands back
# one of its arguments.
# The call's result binds as a borrow only where every "borrow" /
# "element" argument lends; elsewhere it is a value, and a binding that
# holds it warns. Operator methods and property getters refuse a borrow.
# Without `result=` a free function's result is a fresh value (a native
# method's keeps the receiver borrow its signature implies).
# `args=` (methods only) says what the method does with an argument.
# "writes" on `self`: it replaces elements in place and moves none, so
# iterators and references to the other elements stay valid. A method with
# a receiver that is neither @readonly, @pure nor @auto_readonly may say
# it. The default for a mutating method is the wider claim: it may replace
# elements AND move or free every one of them (reallocate, erase), so it
# invalidates everything inside the receiver.
# "stored" / "compared" on an argument whose type is one of the container's
# type parameters (its element, key or value) or a container of them: the
# method holds it from then on, or only compares it with what it holds or
# hands it back -- one or the other for every such argument of the method.
# A container literal whose numbers are not decided yet widens by a stored
# value and requires a compared one to fit; without either, such a call
# decides its numbers first. The stdlib `list` / `dict` / `set` / `Span` /
# `Array` stubs declare them.
@builtin_decorator("tpy.extern.native")
def native(name: str = "", function: bool = False, binding: str = "",
           cpp_return_type: type | None = None, indirecting: bool = False,
           borrowing_view: bool = False, transient: bool = False,
           checks_signals: bool = False,
           elements: bool = False,
           element_access: bool | Callable[[], None] = False,
           _iter_yields_ref_tuple_proxies: bool = False, *,
           result: dict[str, str], args: dict[str, str]): ...

@builtin_decorator("tpy.extern.export")
def export(name: str = "", binding: str = ""): ...

# Prefer @native over @cpp_template -- use only when @native can't express the
# call (e.g. wrapping in a constructor, type cast, or non-trivial expression).
@builtin_decorator("tpy.extern.cpp_template")
def cpp_template(template: str, transient: bool = False,
                 checks_signals: bool = False,
                 element_access: bool | Callable[[], None] = False, *,
                 result: dict[str, str], args: dict[str, str]): ...

@builtin_decorator("tpy.extern.value_ptr_coercion")
def value_ptr_coercion(): ...

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

# The configured --default-int type: as an annotation (an 8/16-bit operator's
# result) and as the @type_param_default sentinel.
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
