"""Per-face witness tally; reported by the --thir-codegen zero-witness summary.

The corpus byte-diff proves routed bodies emit byte-identical C++, but says
nothing about a face (a lowering classifier / render) that NO corpus case
reaches -- a latent bug there stays invisible until its first witness
arrives. The test harness folds these counts across cases and xdist workers
(like the routed-body tally) and reports registered faces with zero
witnesses over the whole corpus run.

Witness semantics differ by face kind (encoded in the registry comment):
lowering faces record at THIR-node construction (the render actually
fired); the `own.*` classifier rows record at lowering admission -- their
render is
the bare arg shared with the pass-through emit, so admission is the only
distinguishing site (an admit in a body later rejected elsewhere still
counts, a deliberate over-approximation); `flush.*` record when a flushable
statement position's lowered value actually carries a hoisted arg temp.

The registry is immutable metadata (module-level by design); the mutable
counts live on the active Compiler (`_thir_face_witnesses`), so the helper
is a no-op outside a compilation. Recording is NOT flag-gated: it happens
wherever lowering runs, which is every case of every run with
THIR on. Only the zero-witness REPORT is behind the marker-ignoring metrics
flags -- it is a whole-corpus question, so a `-k`-filtered run would name
faces no selected case could reach.
"""

from __future__ import annotations

from ..compilation_context import get_current_compiler

THIR_FACES: frozenset[str] = frozenset({
    # THIRArgTemp arms (lowering; _lower_call_arg / the method-arg row).
    "argtemp.value_union",          # free-call value-union member temp
    "argtemp.value_union_method",   # method-call value-union member temp
    "argtemp.record_rvalue",        # record-ctor rvalue into a ref slot
    # Protocol-slot arg wrap: the @dynamic Adapter / RefAdapter / concrete
    # materialization, and the structural slot's `auto __tmp_N` rvalue temp.
    "argtemp.protocol",
    "argtemp.ctor_mut_rvalue",      # record rvalue into a MUTATED ctor slot
    "ctor.const_rvalue_arg",        # record rvalue inline into a const ctor slot
    "argtemp.own_copy",             # Own-slot copy+move `__tmp_N` temp
    "argtemp.container_literal",    # list literal into a free-call container
                                    # ref slot -> hoisted `__tmp_N` temp
    # `*args` call-site pack faces (THIRVarargPack lowering / _gen_vararg_pack).
    "vararg.empty",                 # `::tpy::varargs<E>()`
    "vararg.pack_value",            # value-element std::array<E, N> temp
    "vararg.pack_ref",              # ref-element std::array<E*, N> temp
    "vararg.star_direct",           # `*span` forwarded direct
    "vararg.star_span",             # `*container` borrowed span
    # The temp-free last-use move (lowering).
    "move.own_last_use",            # `f(std::move(name))`
    # Pointer-repr Optional[record] slot faces (lowering).
    "optptr.none",                  # `nullptr`
    "optptr.ctor_rvalue",           # `&(__tmp_N)` addr-of arg temp
    "optptr.lift",                  # `::tpy::optional_to_ptr(...)`
    "optptr.pass",                  # already-`T*` binding passes bare
    "optptr.name",                  # `&(name)`
    "optptr.subscript",             # `&(<lvalue record subscript>)`
    # Value-repr Optional slot None arg (lowering): the value-optional twin
    # of `optptr.none` -- `f(std::nullopt)`.
    "call.none_value_opt",
    # Value-repr Optional slot member-typed arg (lowering admission): the
    # generic tail render, the optional's converting ctor absorbing the bare
    # member -- `f(5)`, `f("hi")`, `f(Color.Red)`.
    "call.optval_member",
    # An owned value-repr Optional[str/bytes] LOCAL into a matching owned
    # Optional slot -> bare whole-optional pass (no view->owned shim).
    "call.optview_local_whole",
    # @error_return faces (emit unless noted): the function-body renders
    # (bare-return `{}`, the void success tail, the return-tier raise), the
    # caller renders (the statement bind/discard blocks, the expression
    # unwrap in value and pointer form), and the return-tier try dispatch.
    "er.bare_return",               # bare `return` -> `return {};`
    "er.void_tail",                 # trailing `return {};` success
    "er.raise",                     # `return make_unexpected(E{})`
    "er.raise_args",                # `return make_unexpected(E(args))`
    "er.reraise",                   # bare `raise` in a return-tier handler
    "er.bind",                      # var-decl/assign `__try_tmp_N` block
    "er.discard",                   # expr-stmt `__try_tmp_N` block
    "er.unwrap",                    # `({ ... unwrap_ref_move(*__er_N); })`
    # No corpus witness (every reaching shape needs a borrow-returning
    # fallible callee no committed case has); pinned byte-identically by
    # test_thir_error_return.py's pointer-form unit.
    "er.unwrap_ptr",                # `(*({ ... &unwrap_ref(*__er_N); }))`
    "er.try_return",                # the goto-dispatch try emit
    "er.try_binding",               # `std::optional<E> __err_opt_N;` capture
    "er.return_passthrough",        # `return <raw expected call>;` (lowering)
    "try.return_tier",              # a return-tier try lowered (admission)
    # Pointer-variant union-slot lifts (lowering).
    "unionlift.none",               # `pv{std::monostate{}}`
    "unionlift.const_wrap",         # `ptr_variant_to_const(...)`
    "unionlift.member",             # `pv{&(name)}`
    "unionlift.ctor_temp",          # ctor rvalue temp + `pv{&__tmp_N}`
    # Own-cascade bare rows + the readonly ctor tail (lowering admission).
    "own.scalar_rvalue",            # rvalue scalar into Own[scalar]
    "own.record_rvalue",            # record rvalue call into Own[record]
    "own.union_ctor",               # record-ctor rvalue into Own[union]
    "own.readonly_ctor",            # record-ctor rvalue into readonly slot
    # Self receiver / ctor-call renders (lowering).
    "self.this",                    # `self` name read -> `this`
    "call.self_method",             # `self.helper()` -> `this->helper()`
    "call.imported",                # cross-module callee -> pre-rendered
                                    # `::tpyapp::mod::f` (callee_cpp)
    "call.native_free",             # C++ @native free callee -> `::native(args)`
    "call.template_free",           # positional-only @cpp_template free callee
    "call.instantiation_template",  # generic-type instantiation `list(it)` ->
                                    # sema-substituted ctor template expansion
    "call.instantiation_empty",     # empty `set()`/`list()`/`dict()` -> the
                                    # spelled default ctor `T()`
    "call.viewfam_instantiation",   # str-family VALUE instantiation
                                    # `StrView("x")`/`String("x")` -> the ctor
                                    # @cpp_template over inline args

    "call.inst_ctor_arg",           # `dict(PairIter(3))` -> a user-iterator
                                    # ctor rvalue into the construct template
    "call.generic_free",            # plain TPy generic callee -> explicit
                                    # `f<T1, T2>(args)` template-arg spelling
    "call.generic_qualified",       # module-qualified generic call ->
                                    # `::tpyapp::m::gf<T>(args)`
    "call.generic_static",          # same-module generic static ->
                                    # `Cls<CA>::template m<MA>(args)`
    "argtemp.generic_ref_slot",     # literal temporary into a TypeParamRef
                                    # ref slot -> `<resolved> __tmp_N = <lit>;`
    "ctor.instantiation",           # record-ctor instantiation form
                                    # (`Cell[Int32]()` / `Poll[T]()`) ->
                                    # rendered `type_to_cpp(call_type)(args)`
    "call.marker_qualified",        # module-qualified `m.f(x)` / static
                                    # `Rec.m(x)` -> pre-rendered callee_cpp
    "call.coro_factory_adapter",    # async-def factory call into an
                                    # Own[@dynamic P] slot ->
                                    # `::tpy::make_adapter<Base>(f(args))`
    "call.coro_handle_adapter",     # bound-handle NAME into that slot ->
                                    # `make_adapter<Base>(std::move(*(c)))`
    "call.module_native",           # bare-@native module callee `m.f(x)`
                                    # -> `::native(args)`
    "call.static_template",         # positional-only @cpp_template static
                                    # (`UInt32.trunc(i)`) -> template expansion
    "call.macro_expansion",         # `@call_macro`/getattr/hasattr call ->
                                    # its sema-synthesized replacement expr
    "call.dunder_call",             # `obj(args)` with a __call__ method ->
                                    # the synthetic `obj.__call__(args)`
    "call.ord_fold",                # `ord("X")` single-char literal ->
                                    # the constant ordinal int
    "call.range_object",            # `range(...)` in object position ->
                                    # `::tpy::Range<T>(...)`
    "ctor.nested_record",           # `Outer.Inner(args)` -> `Outer::Inner(args)`
    "call.cast_passthrough",        # `typing.cast(T, x)` non-Any -> bare `x`
    "call.cast_any",                # `typing.cast(T, x)` from Any ->
                                    # `::tpy::any_cast_or_panic<T>(x)`
    # Ptr[T]-receiver Deref method calls (lowering; the THIRMethodCall
    # is_arrow / deref_check renders over a pointer-VALUE receiver).
    "method.ptr_arrow",             # proven non-null: `p->m(args)`
    "method.ptr_checked",           # `::tpy::deref_check(p).m(args)`
    "method.user_deref_chain",      # `r.__deref__()...m(args)` user Deref proxy
    "method.ptr_template",          # explicit `@cpp_template` Ptr method
                                    # (`p.__deref__()` -> `::tpy::deref_check(p)`)
    # Container-field method receiver (`self.buf.append(x)` -> the container arm
    # over a bare `this->buf` THIRFieldAccess receiver, same emit as a bare-name
    # container receiver).
    "method.recv.container_field",
    # Container-element-record subscript method receiver (`xs[i].m()` -> the
    # user-record arm over a `::tpy::__getitem__(xs, i)` borrow lvalue, `.`
    # access -- never `->`, mirroring the field-access-off-subscript receiver).
    "method.recv.subscript",
    # Method-call method receiver (`a.b().c()` -> the user-record arm over an
    # inner-call receiver whose result is a plain non-pointer record, `.`
    # access; the inner call renders via the shared method lowering).
    "method.recv.method",
    "method.recv.protocol",         # inner call yields a protocol borrow
                                    # (`box.get()` -> `Pet&`) -> `.` outer call
    # Value-record field method receiver (`self.field.m()` -> the user-record
    # arm over a bare `this->field` / `p->field` THIRFieldAccess receiver). The
    # field's record spells byte-identically (`_f1_record`: same-module,
    # cross-module, @native, and concrete-arg generic records all qualify), so
    # native / generic field receivers ride the same face as a plain one.
    "method.recv.record_field",
    "method.recv.free_call",        # `make(3).get()` -- a plain F1-record
                                    # free-call result receiver, `.` access
    "method.recv.str_literal",      # `"a,b,c".split(",")` -- a str-literal
                                    # receiver rendered bare into the resolved
                                    # builtin-method template
    "method.recv.str_method",       # `s.strip().lower()` -- a str-VALUE
                                    # method-call/free-call receiver, the inner
                                    # str method's bare nested-call render
    "ctor.call",                    # THIRCtorCall bare ctor expansion
    "ctor.native",                  # native-record (builtin exception) ctor: `::tpy::OSError(...)`
    "ctor.ptr_null",                # `Ptr[T]()` -> `static_cast<T*>(nullptr)`
    "ctor.cross_module",            # imported-record ctor: the qualified
                                    # `::ns::Name(args)` spelling
    "ctor.str_arg",                 # str-slice arg into a str-family ctor slot
    "ctor.own_arg",                 # Own-slot ctor arg via the shared cascade
                                    # rows (last-use move / copy+move temp)
    "ctor.container_literal_arg",   # list literal into a ctor's list slot:
                                    # the bare brace-init render in place
    "ctor.omit_defaults",           # ctor call omitting trailing default args
                                    # (defaults ride the C++ ctor signature)
    "call.omit_defaults",           # free/method/qualified call omitting
                                    # trailing default args (defaults ride the
                                    # emitted C++ signature)
    # Ctor MIL view-family field inits (lowering; the per-family renders --
    # bare str/StrView source vs the bytes view->owned bytes_copy convert).
    "mil.str_field",                # str/StrView field: bare source render
    "mil.bytes_field",              # bytes field: bytes_copy wrap / owned bare
    # Ctor MIL container-field inits (lowering; the shared container-literal
    # machinery at the target-threaded MIL cell, plus the bare
    # container-param copy / Own-param move name row).
    "mil.container_literal",        # `self.xs = [1, 2]` -> `xs({1, 2})`
    "mil.container_name",           # `self.xs = p` -> `xs(p)` / `xs(std::move(p))`
    "with.str_target",              # str/StrView __enter__ as-target
    # Container subscript writes (lowering; THIRSetItem's emit arms plus
    # the owned-str element sink copy and the aug-assign desugar).
    "setitem.slice",                # `c[a:b] = v` / `c[a:b:s] = v` ->
                                    # list_set_slice / list_set_stepped_slice
    "aug.container_inplace",        # `c OP= v` -> mutating dunder native
                                    # free-function (list_extend, ...)
    "setitem.checked",              # `::tpy::__setitem__(c, k, v);`
    "setitem.bounds_safe",          # `c[static_cast<std::size_t>(k)] = v;`
    "setitem.aug",                  # `c[k] OP= v` -> the getitem/setitem pair
    "setitem.str_owned_copy",       # view source into a str element: std::string(v)
    "setitem.field_recv",           # write/aug receiver is a field access
    "setitem.user_record",          # `recv[k] = v` on a user record with
                                    # __setitem__ -> ::tpy::__setitem__(recv,k,v)
    "setitem.container_value",      # nested-container element: literal value,
                                    # type-prefixed on the checked path
    "setitem.borrow_lift",          # Optional/union element: borrow NAME lifts
                                    # via ptr_to_optional / to_value_variant
                                    # (narrowed member names store bare)
                                    # (`::tpy::__setitem__(this->xs, i, v);`)
    # Plain F1-record FIELD write from a record rvalue (a ctor STORAGE / a
    # by-value record-returning call VALUE): a bare copy `recv.field =
    # Inner(args);`, no borrow<->storage lift.
    "field_write.record_rvalue",
    # Plain F1-record FIELD write from a record NAME: the bare copy
    # `recv.field = p;` or `std::move(p)` at a movable name's last use.
    "field_write.record_name",
    # Container-literal FIELD write: the decl-init literal render assigned
    # into the field lvalue (`this->xs = {n};` / the ordered_map ctor form).
    "field_write.container_lit",
    # Str-family FIELD write from a name/literal: the bare
    # `recv.field = s;` (operator=(string_view), no view->owned wrap).
    "field_write.str",
    # Owned bytes FIELD write from a name/literal: a view source copies via
    # `::tpy::bytes_copy(...)`; an owned source lands bare.
    "field_write.bytes",
    # Value-storage Optional[record] FIELD write (`std::optional<inner>`) from
    # a record NAME: the bare copy `recv.opt = p;` (optional::operator=) or
    # `std::move(p)` at a movable name's last use.
    "field_write.optrec_name",
    # The Optional[record] FIELD write from a record RVALUE (ctor / by-value
    # call of the inner type): the bare copy `recv.opt = Inner(args);`.
    "field_write.optrec_rvalue",
    # `recv.opt = None` at any Optional FIELD: the storage-form `std::nullopt`,
    # keyed on the declared field type (a narrowed write site still stores it).
    "field_write.opt_none",
    # Dynamic-attrs (D16) family faces.
    "setitem.any_value",            # `d[k] = v` into a dict[K, Any] slot from
                                    # an Any-typed name (bare, no make_any)
    "delitem.any_value",            # `del d[k]` on a dict[K, Any] receiver
    "delitem.user_record",          # `del recv[k]` on a user record with
                                    # __delitem__ -> ::tpy::__delitem__(recv, k)
    "field_write.container_name",   # container FIELD write from a same-family
                                    # NAME: bare copy or std::move at last use
    "method.dyn_setattr",           # `obj.x = v` -> the synthesized
                                    # `obj.__setattr__("x", make_any(...))`
    "method.dyn_getattr",           # `obj.x` read -> the synthesized
                                    # `obj.__getattr__("x")` method call
    "stmt.del_attr",                # `del obj.attr` -> the synthesized
                                    # `obj.__delattr__("attr");` statement
    "ret.any_subscript",            # `return d[k]` at an Any return slot ->
                                    # bare `::tpy::__getitem__(d, k)`
    "ret.any_name",                 # `return a` -- a bare Any value name
                                    # (value type, returns bare, no move)
    "expr_stmt.macro_discard",      # void stmt-position macro expansion
                                    # (setattr/delattr builtins) dispatched
                                    # with the DISCARD use
    # A base-init arg beyond the scalar row: str name/literal, None,
    # IntLiteralType digits, record / Optional-ptr / Own param names --
    # all the target-less bare renders of _extract_base_inits.
    "baseinit.nonscalar_arg",
    # Ctor member-init-list cells (lowering; the small value families beyond
    # the scalar / record / Optional[record] arms).
    "mil.optional_none",            # `f(std::nullopt)` -- any Optional field,
                                    # inner-independent (incl. value-repr)
    "mil.ptr_none",                 # `p(nullptr)` -- None into a Ptr[T] field
    "mil.union_none",               # `u(std::monostate{})` -- None into a
                                    # value-variant union field
    "mil.union_lift",               # `u(::tpy::to_value_variant<...>(v))` --
                                    # a borrow ptr-variant name source
    "mil.union_rvalue",             # `u(A(3))` -- a member-record ctor rvalue
                                    # constructs the variant directly
    "mil.value_union",              # `u(u)` / `u(5)` -- value-union bare render
    "mil.tuple_storage",            # `t(::tpy::tuple_to_storage<...>(t))` --
                                    # a borrow pointer-repr tuple param
    "mil.ptr_tuple_literal",        # `t(::tpy::tuple_to_storage<S>(S{...}))`
                                    # -- a spelled pointer-repr tuple literal
    "mil.value_tuple_name",         # `t(t)` -- value-tuple param bare copy
    "mil.value_tuple_literal",      # `t(std::tuple<...>{...})` spelled literal
    "mil.optional_value_copy",      # `f(value)` -- value-repr Optional field
                                    # bare-copied from a same-typed opt param
    "mil.optview_shim",             # value-repr Optional[str/bytes] field <-
                                    # borrow optional<view> param (arg-split shim)
    "mil.callable_copy",            # `on_event(cb)` -- std::function field
                                    # bare-copied from a same-typed param
    # An own-field init the AST demotes to the ctor body (bare non-param name /
    # nested-def name / body-local ref) -- THIR demotes identically instead of
    # rejecting the whole ctor (lowering verdict; the body machinery renders it).
    "mil.demote_mirror",
    # Container/str subscript read off a FIELD-ACCESS receiver (lowering;
    # `::tpy::__getitem__(this->xs, i)` -- the receiver renders as its own
    # THIRFieldAccess inside the shared subscript emit).
    "subscript.field_recv",
    # Bytes-family FIELD subscript read (lowering; the same field-receiver
    # widening through the bytes dispatch -- `::tpy::bytes_getitem(this->b, i)`).
    "subscript.bytes_field",
    # A value scalar/Char/enum/typeparam/Ptr field read off a value F1-record
    # field CHAIN receiver (lowering admission; `o.mid.inner.v` -- `_lower_expr`
    # recurses through the receiver, so every link's render is shared with the
    # single-level field read, and admission is the distinguishing site).
    "field.chain_recv",
    # Owned-BYTES element read off a list[bytes]/dict-value container
    # (lowering; STORAGE form -- owned sinks copy implicitly, view bindings
    # / span args convert implicitly, so every admitted sink lands it bare).
    "subscript.bytes_elem",
    # F1-record element read (`ps[i]` -> `T&` BORROW; lowering) -- consumed
    # as a field-access receiver (`ps[i].x`, read/write/aug) or a REF_ALIAS
    # borrow-local source (`p = ps[i]` -> `P& p = ...`).
    "subscript.record_elem",
    # A user-record subscript `recv[index]` -> the record's bare operator[]
    # (generated from __getitem__); value-scalar/char/str/etc results.
    "subscript.record_getitem",
    # A list/Array/Span slice read (`items[a:b:c]`) -> owned list via the
    # `list_slice`/`list_stepped_slice` @cpp_template (STORAGE result).
    "subscript.container_slice",
    # `Color[name]` enum name lookup -> `::tpy::EnumUtil<E>::from_name(name)`
    # (lowering; a static lookup panicking KeyError on miss).
    "subscript.enum_from_name",
    # `len(recv.field)` -- a container/str/bytes field arg to the builtin len
    # (lowering; `::tpy::__len__(this->xs)`, the field renders as its own
    # THIRFieldAccess inside the shared native-call emit).
    "len.field_recv",
    # Container-FIELD for-each iterable (lowering; `for x in self.xs:` -- the
    # field renders inside the same lvalue `auto& __obj_N =` capture a name
    # takes; str/bytes fields ride the older viewfam admission).
    "foreach.container_field",
    # Enum-type for-each iterable (lowering; `for c in Color:` -- ranges over
    # the fixed `::tpy::EnumUtil<E>::members` static array as an `auto&` lvalue).
    "foreach.enum",
    # A str method returning `Own[list[str]]` as a for-each iterable (lowering;
    # `for w in s.split():` -- the owning `auto __obj_N = ::tpy::str_split...(s)`
    # rvalue capture, iterated like any list[str]).
    "foreach.str_list_method",
    # Value-tuple element for-each loop var (lowering; a `list[tuple[...]]`
    # element or a `d.items()` key/value pair -- binds the whole tuple as
    # `auto&& t = *__beg_N;`, subscript reads render `std::get<i>(t)` bare).
    "foreach.value_tuple_elem",
    # Value-repr Optional[scalar] for-each loop var (lowering; binds the
    # typed `std::optional<T>` copy and registers the name so its body reads
    # ride the value-opt binding arms -- deref-on-narrow, the whole-optional
    # None-test / truthiness / arg renders).
    "foreach.value_opt_elem",
    # Native auto-consuming for-each iterable (lowering; `for x in items:`
    # where items is consumed at last use -- `::tpy::own_iter(std::move(
    # items))`, rvalue capture, loop var `auto&&` joins movable_locals).
    "foreach.consuming_iter",
    # List-literal for-each iterable (lowering; `for c in [a, b, c]:` -- the
    # owning `auto __obj_N = {a, b, c};` initializer-list capture, elements
    # rendered target-less like the AST's untargeted gen_expr_deref).
    "foreach.iter_literal",
    # Str-literal for-each iterable (lowering; `for ch in "abc":` -- the
    # owning `auto __obj_N = std::string_view("abc");` capture, Char elems).
    "foreach.str_literal",
    # Branch-first-declared value locals used after the loop -> `{cpp} {name};`
    # predecls before the loop (lowering; mirrors _emit_branch_decls, shared with
    # the if/try/with hoist family). Includes the loop var when hoisted.
    "foreach.hoist_decl",
    # Loop else blocks (lowering; the bare `{...}` past the loop's close
    # brace + its `__after_else_N:;` label -- run on normal completion,
    # jumped past by a break).
    "loop.for_else",                # for/else (range and container routes)
    "loop.while_else",              # while/else
    # `del x` early-destruction move-sink (lowering; one
    # `{ auto __del_sink = std::move([*]name); }` block per sunk name --
    # skip-only dels stay on the no-code THIRNoOpStmt face).
    "stmt.del_var_sink",
    # Rebound container-literal local (lowering; the F2d two-slot machinery
    # with a container-literal init/reseat -- `std::vector<T>* xs = &__slot_1;
    # ... xs = &*(__slot_2 = {...});`).
    "decl.container_rebind_slot",
    # Runtime-BigInt `.to_fixed_check<T>()` narrows (lowering; the AST's
    # gen_index_expr / _gen_slice_bound / aug-assign / enum-from_value wraps).
    "narrow.subscript_index",       # `i.to_fixed_check<int32_t>()` (reads + del)
    "narrow.slice_bound",           # same wrap on a str/bytes slice bound
    "narrow.aug_value",             # `({0}).to_fixed_check<T>()` aug-assign value
    "narrow.enum_arg",              # `({0}).to_fixed_check<U>()` E(x) arg
    "narrow.opt_field_test",        # `self.f is [not] None` -> the storage-form
                                    # `.has_value()` compare over the bare member
    "narrow.ptr_field_test",        # `s.p is [not] None` on a `Ptr[T]` field ->
                                    # the `(s.p == nullptr)` raw-pointer compare
    "field.narrowed_deref",         # sema-narrowed Optional field value read ->
                                    # the unconditional `(*recv.field)` unwrap
    # Record return slots (lowering admission; the renders -- bare name / bare
    # ctor expansion -- are shared with the pass-through emits, so admission
    # is the only distinguishing site).
    "ret.record_borrow",
    "ret.record_storage",
    "ret.record_self",              # `return self` -> `return (*this);`
    "ret.record_field",             # `return recv.field` at the borrow slot
    "ret.record_subscript",         # `return c[i]` -- container record element
    "ret.ptr_opt_field",            # `return self.f` (Optional[record] field)
                                    # -> `optional_to_ptr(this->f)`
    "ret.opt_field_ref",            # @property getter `return self.f` -> bare
                                    # `this->f` (std::optional<T>& ref return)
    "ret.storage_opt_rvalue",       # `return Coord(0, 0)` -> bare ctor into a
                                    # value-storage / Own Optional slot
    "ret.str_field",                # `return recv.field` (owned-str member,
                                    # STORAGE) at a str-family return slot
    # Storage container return slot (`-> Own[list/dict/set]`; the renders --
    # bare owned name / the decl-init literal emits -- are shared, so
    # admission is the distinguishing site).
    "ret.container_name",
    "ret.container_literal",
    "ret.container_call",           # `return make_list(n);` -- bare call source
    "ret.container_comp",           # `return {x for ...}` -- the decl-init
                                    # stmt-expr render at the return slot
    "ret.closure_name",             # `return add;` -- a closure local's bare
                                    # name at a Callable return slot
    "ret.closure_lambda",           # `return lambda x: ...;` -- a direct
                                    # escaping closure at a Callable return slot
    "ret.closure_ref",              # `return double;` -- a bare func-ref name
                                    # at a Callable return slot
    "ret.tuple_call",               # `return make_pair(n);` -- bare call source
    # Value-repr Optional[cheap scalar] return slot (`-> Int32 | None`): the
    # None-literal `std::nullopt` arm and the whole-optional bare param pass
    # (deref-on-narrow stripped); other scalar sources ride the generic tail.
    "ret.value_opt_none",
    "ret.value_opt_name",
    "ret.value_opt_field",
    # Value-repr Optional[view] return (str or bytes): `None` -> `std::nullopt`,
    # a same-family Optional[view] param -> the view->owned arg-split shim
    # (THIROptViewArg), and a str/bytes literal -> bare owned literal.
    "ret.value_opt_view_none",
    "ret.value_opt_view_shim",
    "ret.value_opt_view_literal",

    # Container-literal element families (lowering; the widened
    # THIRContainerLiteral slots) plus the make_vector/make_ordered_* switch
    # and the per-element last-use move.
    "containerlit.enum_elem",       # `[Color.RED, ...]` / `{Color.RED, ...}`
    "containerlit.optional_elem",   # `[1, None, 3]` -> `{1, std::nullopt, 3}`
    "containerlit.tuple_elem",      # `[(1, 2), ...]` -> spelled std::tuple elems
    "containerlit.container_elem",  # nested list element `[[1, 2], [3]]`
    "containerlit.record_elem",     # `[P(1), p]` -- ctor rvalues / record names
    "containerlit.bytes_elem",      # `[b"a", v]` -- owned render / bytes_copy
    "containerlit.make",            # make_vector / make_ordered_map / _set
    "containerlit.move",            # `std::move(name)` element at last use
    # A spanlike coerce over an array-literal inner: the helper wraps the
    # make_array-typed brace init (`as_mut_span(std::array<T, N>{...})`).
    "coerce.span_array_literal",
    # `into_any` coercion: a scalar / str / bytes / None value wrapped into a
    # `tpy::Any` cell via `make_any` (`x: Any = 42` / `Any(v)`).
    "coerce.into_any",
    # `from_any` auto-coerce: runtime-checked `any_cast_or_panic<T>` extraction
    # at a concrete slot (`n: int = a` / `return a`).
    "coerce.from_any",
    # A raw `Any` value in print position: `std::cout << a` via Any's
    # operator<< (PrintForm.RAW).
    "print.any",
    # A raw `Any` f-string arg: bare into std::format (Any formatter).
    "fstr.any_arg",
    # `Any is [not] None`: the D15 typeid probe against std::monostate.
    "isnone.any_typeid",
    # Value-tuple slots (`tuple[scalar|str, ...]`): the spelled
    # `std::tuple<...>{...}` literal render at returns / decls, and the bare
    # value-tuple name return.
    "ret.tuple_literal",
    "ret.tuple_name",
    "decl.tuple_literal",
    # A VALUE-capture record/Own tuple LITERAL decl bound by value (storage
    # form): the local owns its elements, a ref-element type spells `auto`.
    "decl.storage_record_tuple",
    # A @dynamic protocol local (`p: P = Concrete()`): concrete/adapter slot +
    # protocol Base* pointer (the AST's _gen_dynamic_protocol_init).
    "decl.dyn_protocol",
    # A @dynamic protocol local RESEAT (`p = Other()`): a fresh hoisted
    # `std::optional<slot>` + emplace + `p = &*slot` (_gen_dynamic_protocol_rebind).
    "reseat.dyn_protocol",
    # An already-erased @dynamic assign (`p2: P = p1` / `p2 = p1`): alias the
    # same object, `Base* p2 = &(*p1);` / `p2 = &(*p1);` (no slot).
    "decl.dyn_protocol_erased",
    "reseat.dyn_protocol_erased",
    # Widened value-tuple RETURN elements: a NESTED value-tuple element (spelled
    # recursively) and a value-`Optional[scalar]` element (`None`->`std::nullopt`
    # / a scalar value bare). Return-slot only.
    "ret.tuple_nested_elem",
    "btuple.literal",               # borrow-slot tuple literal (spelled + lifts)
    "btuple.value_to_borrow",       # rvalue elements via the source-tuple helper
    "btuple.value_arg",             # value-tuple literal call arg
    "btuple.decl",                  # sync borrow-tuple local decl (`auto t = ...`)
    "res.btuple_write",             # resumable borrow-tuple frame-field write
    "res.btuple_yield",             # borrow-tuple yield (literal or lifted source)
    "ret.tuple_opt_elem",
    # A value-`Optional[str]` RETURN element: a str-view source wraps
    # `std::string(view)` through the Optional slot; `None`->`std::nullopt`, a
    # str literal bare.
    "ret.tuple_opt_str_elem",
    # An `Own[F1-record]` RETURN element (a by-value `T` slot in the spelled
    # tuple): a ctor rvalue lands bare, an owned name moves at last use.
    "ret.tuple_own_elem",
    # An `Own[A | B]` record-member union return slot: a member ctor rvalue
    # returns bare into the by-value storage variant.
    "ret.own_union_ctor",
    # Owned record local decl (lowering; the `{cpp_type} {name} = <rvalue>;`
    # plain-value render).
    "decl.owned_record",
    # Owned record local decl from a method-call rvalue source (`Rec r =
    # b.build();`) -- the method sibling of the free-call `decl.owned_record`.
    "decl.owned_record_method",
    # Move-through owned record local (`Handle a = std::move(h);`): a NAME
    # source consumed at its last use, target flagged in `move_through`.
    "decl.move_through_record",
    # `copy(a)` of a plain F1-record source into an owned record local
    # (`T b = T(a);`, the copy-construct rvalue).
    "decl.copy_record",
    # Storage-call local decl (lowering admission; a container/tuple/union-
    # returning call init -- the bare `T x = f(...);` / plain reassign,
    # rendered by the shared generic decl tail).
    "decl.storage_call",
    # Native record-returning free-call local decl (`f = open(path)` ->
    # `::tpy::TextFile f = ::tpy::builtin_open(path);`) -- a plain-value decl,
    # single-assignment rvalue only.
    "decl.native_record_call",
    # REF_ALIAS from a borrow-record-returning call (lowering; the
    # `T& p = shared(x);` bind of the callee's returned reference).
    "decl.record_borrow_call",
    # Open-T local from a T-returning call in a generic body (lowering;
    # the `::tpy::val_or_ref_t<T> item = box.get();` form-neutral bind).
    "decl.tparam_call",
    # `copy(x)` of an open-T source (lowering; the special-builtin arm's
    # general tail, `T(this->value)`).
    "call.copy_tparam",
    # `copy(x)` of a concrete container source (`copy(d.get(k, dflt))` ->
    # `std::vector<T>(<src>)`).
    "call.copy_container",
    # Ptr[T] value-slot admission (bare passes / field reads share the
    # scalar renders, so the predicate is the only distinguishing site).
    "ptr.value_slot",
    # The @dynamic-protocol pointee arm of the same predicate (the
    # pointee spelling is the shared PtrType.to_cpp on both paths, so
    # admission distinguishes it from the record/scalar pointees).
    "ptr.dyn_proto_pointee",
    # `x = None` at a Ptr[T] value binding (lowering; the `nullptr` render).
    "decl.ptr_none",
    # `x = None` reassign at a value-repr Optional binding (`int | None` param):
    # the storage-form `std::nullopt` render.
    "decl.opt_none",
    # Slot-hoist pointer-repr Optional local, None init: `T* x = nullptr;`
    # plus the `std::optional<T>` rebind-slot pre-decl when rvalue-reassigned.
    "decl.opt_slot_none",
    # Slot-hoist Optional local, F1-record rvalue init: `T __slot_N = ...;
    # T* x = &__slot_N;` (+ the rebind-slot pre-decl).
    "decl.opt_slot_rvalue",
    # Reseat of a slot-hoist Optional local to None: `x = nullptr;`.
    "reseat.opt_none",
    # Rvalue reseat through the pre-declared rebind slot:
    # `x = &*(__slot_N = <rvalue>);` (THIRAssign's rebind-slot arm).
    "reseat.opt_rvalue",
    # Lvalue reseat of a slotless Optional local: lift a bare record param or an
    # F1-record field source via `x = &(...);`.
    "reseat.opt_lvalue",
    # Ptr-variant union local from a concrete-member rvalue: value-variant
    # `__slot_N` + `to_ptr_variant(__slot_N)` (+ the rebind-slot pre-decl).
    "decl.union_slot_rvalue",
    # Ptr-variant union local from a concrete-member lvalue name:
    # `variant<A*, B*> v{&(name)};`.
    "decl.union_addr",
    # Union rvalue reseat through the pre-declared rebind slot:
    # `__slot_N.emplace(...); v = ::tpy::to_ptr_variant(*__slot_N);`.
    "reseat.union_rvalue",
    # A read of a read-only-seeded same-module value global (lowering; the
    # bare-name render shared with locals, so the seed is what distinguishes).
    "name.global_seeded",
    # A same-module function used as a value (`apply(double, ...)`): the bare
    # escaped-name render on THIRName.cpp (_function_ref_name's plain arm).
    "name.func_ref",
    # A read of a read-only-seeded NATIVE-linkage value global (lowering;
    # the pre-rendered `::symbol` spelling on THIRName.cpp).
    "name.global_native",
    # A read of a read-only-seeded IMPORTED value global (lowering; the
    # pre-rendered `::tpyapp::mod::g` / native_cpp_name spelling on
    # THIRName.cpp -- imported_variable_cpp, shared with the AST render).
    "name.global_imported",
    # BigInt-counter range loop (lowering admission; the render difference is
    # the `::tpy::BigInt` cpp_elem + literal-bound retype, shared with the
    # fixed-int emit).
    "range.bigint_counter",
    # 3-arg stepped range loop, by step arm (lowering admission; each arm's emit
    # is a distinct overflow / direction shape mirroring _gen_range_counter_loop).
    "range.step_plus_one",          # literal +1 step -> the ascending ++ loop
    "range.step_unit_neg",          # literal -1 step -> the descending -- loop
    "range.step_literal_pos",       # non-unit positive literal step
    "range.step_literal_neg",       # non-unit negative literal step
    "range.step_variable",          # fixed-int-name step (captured `__step_N`)
    # Bool-field truthiness condition (lowering admission; `if self.closed:` --
    # a bool value's truthiness render IS its value render, so the admitted
    # field-read emit carries the condition unchanged).
    "cond.bool_field",
    # Bool-method-call truthiness condition (lowering admission;
    # `if g.is_open():` -- the same bare-render property as cond.bool_field, over the method
    # call's value-position admission).
    "cond.bool_method",
    # Bool free-call truthiness condition (lowering admission; `if f(x):` --
    # the free-call twin of cond.bool_method).
    "cond.bool_call",
    # Non-identity `_truthy_for_rendered` arms carried by THIRTruthy.
    "truthy.nonempty",
    "truthy.is_truthy",
    "truthy.to_bool",
    "truthy.record_bool",
    "truthy.record_len",
    "truthy.always_true",
    # @builtin_type record with a real body and no cpp_formatter (Poll;
    # Waker's formatter-carrying TypeDef stays excluded) admitted as an F1
    # record (lowering admission; the user-record spelling path, so every
    # render is shared).
    "recv.builtin_record",
    # Conditional-expression renders (lowering; cond_pos records at local
    # admission -- the condition-position render is shared with the value
    # emit, so admission is the distinguishing site).
    "ifexpr.value",                 # scalar / Char / enum result
    "ifexpr.str",                   # str-family result (form-tagged)
    "ifexpr.str_mixed",             # mixed view/owned arms: view-arm wrap
    "ifexpr.bytes",                 # view-result bytes ternary (BORROW span)
    "ifexpr.cond_pos",              # bool ternary as an if/while condition
    # Enum value-binding renders (lowering).
    "enum.truthy_plain",            # plain-enum truthiness -> literal `true`
    "enum.truthy_int",              # IntEnum truthiness `(static_cast<U>(x) != 0)`
    "enum.neg",                     # IntEnum `-x` -> `(-static_cast<U>(x))`
    "enum.value",                   # `.value` -> `static_cast<U>(x)`
    "enum.name",                    # `.name` -> `EnumUtil<E>::name(x)` (BORROW)
    "enum.repr_print",              # @native enum print arg -> `::tpy::__repr__`
    "enum.nested_from_value",       # `Outer.Kind(v)` EnumUtil from_value
    # F-string per-arg rows (the wrap table; witnessed during local
    # classification and again at lowering -- non-vacuity only needs a
    # nonzero count).
    "fstr.conv_repr",               # `!r` -> `::tpy::repr_of({0})` wrap
    "fstr.conv_str",                # `!s` no-op passthrough (non-user types)
    "fstr.str_field",               # owned-str field arg formats bare
    "fstr.char_arg",                # Char arg formats bare (`char` is
                                    # std::formattable; no int8 cast)
    "fstr.spec",                    # constant format spec -> `{:spec}`
                                    # placeholder (lowering, routed args only)
    "fstr.container_arg",           # tuple/list/dict/set arg -> to_str helper
    "fstr.user_arg",                # user record / bound type param -> __str__
    "fstr.union_arg",               # union arg -> runtime __str__ visitor
    # Sync `with` faces (lowering, per item / per statement).
    "with.manager_borrowed",        # lvalue manager: `auto& __ctx_N = ...`
    "with.manager_owned",           # rvalue manager: `auto __ctx_N = ...`
    "with.manager_deref",           # pointer-local manager: `*(...)` deref
    "with.as_value",                # `auto <name> = __enter__();`
    "with.as_ref",                  # `auto& <name> = __enter__();`
    "with.ptr_target",              # reassigned target: `T* <name> = &(...)`
    "with.ptr_target_reuse",        # later with, same name: `<name> = &(...)`
    "with.no_target",               # bare `__ctx_N.__enter__();`
    "with.suppress",                # bool __exit__: `if (!...) throw;` catch
    "with.exc_val",                 # `&__exc_N` passed to __exit__
    "with.cleanup_only",            # elided BaseException catch (common shape)
    "with.multi",                   # multiple managers in one statement
    "with.hoist_decl",              # sema-hoisted plain-value predecls
    # Sync `try` faces (lowering, per statement).
    "try.finally_only",             # the unified try/catch(...)/finally shape
    "try.throw_tier",               # C++ try/catch over the handler arms
    "try.multi_handler",            # 2+ catch arms
    "try.bare_except",              # `except:` -> `catch (...)`
    "try.binding",                  # `as e` -> the catch parameter
    "try.else",                     # goto __after_else_N past the handlers
    "try.except_finally",           # throw tier wrapped in the finally frame
    "try.hoist_decl",               # sema-hoisted plain-value predecls
    "try.body_terminates",          # normal-path finally copy elided
    "try.finally_terminates",       # raise/return-ending finally: no rethrow
    # if/elif/else (lowering, per statement).
    "if.hoist_decl",                # sema-hoisted plain-value branch predecls
    # Raise statements (lowering).
    "raise.ctor",                   # `raise X(args)` -> `throw <cpp>(...)`
    "raise.bare",                   # bare re-raise -> `throw;`
    "raise.expr",                   # `raise <expr>` -> `<expr>.__raise__();`
    # dict/set membership (`needle in c` -> `(c.contains(needle))`, the
    # resolved_contains arm; witnessed at lowering admission and again at
    # lowering -- non-vacuity only needs a nonzero count).
    "binop.membership",
    # native-set membership with no resolved __contains__ member (a
    # `readonly[set]`) -> the AST's `is_native_in` fallback
    # `[!]std::ranges::contains(s, x)`.
    "binop.set_ranges_membership",
    # bytes/BytesView membership (`needle in b` -> the native free-function
    # `::tpy::bytes_contains[_sub](b, needle)`, single-byte vs substring form).
    "binop.bytes_membership",
    # str-family membership (`needle in s` -> the `.find()` arm,
    # `(s.find(needle) != std::string::npos)`; `== npos` for `not in`).
    "binop.str_membership",
    # tuple-literal membership (`x in (a, b, ...)` -> the `==` OR-chain, the
    # statement-expression temp form when the needle is non-trivial).
    "binop.tuple_membership",
    # A fixed-int bitwise op (`a & b`, `a << b`, ...) admitted at the scalar
    # arm -- same resolved-binop template emit as arithmetic (lowering admission).
    "binop.bitwise",
    # sema's optional_safe_eq (value-repr Optional[scalar] ==/!=): optional
    # sides read bare, the plain side opposite an un-narrowed optional is
    # target-typed to its inner (lowering).
    "binop.opt_scalar_eq",
    # A chained comparison with a non-simple intermediate (`a < f() < b`) ->
    # the GCC stmt-expr single-eval form (_gen_chained_compare_lambda).
    "chained_compare.stmt_expr",
    # Method call on a bare protocol receiver: `p.m(args)`,
    # monomorphized for a structural protocol, a vtable call for a @dynamic
    # one -- one render either way. Args take the FREE-call literal rules
    # (`_gen_method_call`'s `_args()` fallback loop, not the user-record loop).
    "method.protocol",
    # Protocol-slot arg that renders BARE (a structural-slot lvalue, an
    # inheritance-conformer lvalue, or an already-protocol name forwarded on).
    "protoarg.bare",
    # `len(p)` on a protocol binding -- the same `::tpy::__len__(p)` a
    # container emits (a protocol binding is a reference, never a pointer).
    "len.protocol",
    # Trivia (lowering): docstring / `pass` -> THIRNoOpStmt, body-wide.
    "stmt.trivia",
    "stmt.super_del",               # `super().__del__()` in a destructor ->
                                    # elided (base dtor runs automatically)
    # THIRNestedDef (lowering): a nested `def` -> the AST's lambda emit,
    # capture list spelled from sema's node facts.
    "stmt.nested_def",
    # THIRLambda (lowering): a `lambda` expr -> _gen_lambda's C++ closure;
    # by-ref capture, non-void or void body.
    "expr.lambda",
    # Standalone `a, b = <name>` unpack of a value-scalar tuple (lowering):
    # `const auto& __tup_N = name;` + per-target scalar decls.
    "stmt.tuple_unpack",
    # A non-name unpack source (lowering): a value-tuple-returning call or a
    # value-tuple field read -> `auto __tup_N = <expr>;` (value capture).
    "stmt.tuple_unpack.rvalue_source",
    # An Own[F1-record] unpack element moved out of a call-rvalue source
    # (lowering): `Rec a = std::move(std::get<i>(__tup_N));`.
    "stmt.tuple_unpack.own_target",
    # An expensive-copy value target bound zero-copy (lowering, sema's
    # is_const_ref): `const T& a = std::get<i>(__tup_N);`.
    "stmt.tuple_unpack.cref_target",
    # A reused plain scalar/str target (lowering): `a = std::get<i>(__tup_N);`
    # -- the AST's declared-name assign tail, no decl.
    "stmt.tuple_unpack.assign_target",
    # THIRComprehension (lowering, the C1+C2 slice).
    "comp.list",                    # list comp -> vector stmt-expr
    "comp.set",                     # set comp -> ordered_set stmt-expr
    "comp.dict",                    # dict comp -> insert_or_assign loop
    "comp.range",                   # 1/2-arg counter loop arm
    "comp.begin_end",               # __obj/__beg/__end container loop arm
    "comp.reserve",                 # sized begin/end list reserve line
    "comp.filter",                  # &&-joined `if (conds)` wrapper
    "comp.unpack",                  # inline __tup_N tuple-unpack binding
    "comp.range3",                  # 3-arg range: begin/end over the Range object
    "comp.field_iter",              # field-access iterable (recv.items)
    "comp.print_arg",               # comprehension print arg (container printer wrap)
    "comp.container_value",         # dict-comp list/Array VALUE slot: self-typed
                                    # brace-init literal / nested comp value
    "comp.nested",                  # comprehension VALUE inside a dict comp ->
                                    # the recursive stmt-expr render
    "fn.param_copy",                # reassigned const-ref param -> the mutable
                                    # owned-copy prologue (THIRParamCopy)
    "genexpr.native_iterable",      # genexpr into a native Iterable consumer ->
                                    # the make_generator IIFE (all/any/sum arg)
    "print.optval",                 # un-narrowed value-repr Optional[scalar/str]
                                    # print arg -> bare `::tpy::print_optional_val`
    "print.wrap_arg",               # container / value-tuple / F1-record NAME
                                    # print arg -> its kind-keyed printer wrap
    "print.wrap_field_arg",         # container FIELD read print arg -> its
                                    # kind-keyed printer wrap (ListPrinter(m.f))
    "print.bytes_field",            # bytes-family field read print arg -> bare
                                    # `.field` inside a BytesPrinter wrap
    "print.tuple_subscript_arg",    # value-tuple subscript read print arg ->
                                    # TuplePrinter(std::get<N>(t))
    "print.container_slice_arg",    # list/Array/Span slice read print arg ->
                                    # ListPrinter(list_slice/list_stepped_slice)
    "print.kw_sep_end",             # sep=/end= kwarg (str literal or resolved
                                    # str-value name) -> the chain-token render
    "print.file_sink",              # file= kwarg -> ::tpy::as_ostream(<sink>)
                                    # sink lowered in value position (deref)
    "comp.array_range",             # Array demotion: array_from_index range lambda
    "comp.array_source",            # Array demotion: array_from_index over an Array source
    # THIRMatch M1 -- the unguarded scalar switch tiers (lowering).
    "match.switch_enum",            # switch over enum-member case labels
    "match.switch_primitive",       # switch over int-literal case labels
    "match.if_elif",                # unguarded `==` chain (M2)
    "match.if_elif_else",           # the chain's wildcard `} else {` arm
    "match.bind_copy",              # capture/as: `auto name = subject;`
    "match.bind_ref",               # capture/as: `auto& name = subject;`
    "match.bind_assign",            # pre-declared/hoisted: `name = subject;`
    "match.if_elif_guarded",        # standalone-if + goto __match_end_N (M3b)
    "match.guard_arm",              # a guarded arm's inner `if (guard)`
    "match.switch_guard_chain",     # in-switch guard chain (grouped entries)
    "match.default_goto",           # all-guarded group -> goto __match_default_N
    "match.switch_union",           # switch (subject.index()) over variant tags
    "match.union_alias",            # `auto& __case_i = [*]std::get<idx>(...)`
    "match.union_none_arm",         # `case None:` -> the monostate index
    "match.union_default",          # wildcard/capture -> `default:` in place
    "match.guarded_union",          # per-index guard groups + goto end (M4b)
    "match.if_elif_record",         # record-subject unguarded chain
    "match.guarded_record",         # record standalone-if + goto tier
    "match.record_or",              # or-pattern of condition-only class alts
    "match.field_cond",             # literal field condition (`==` compare)
    "match.field_none",             # field=None -> has_value/monostate check
    "match.field_bind",             # field capture: `{base}.{f}` rhs binding
    "match.union_field_cond",       # guarded-union entry with field conds
    "match.or_labels",              # or-pattern -> stacked case labels
    "match.union_or_bind",          # binding or-arm -> one block per alt
    "match.optional_partition",     # Optional-ptr subject: None/has-value split
    "match.optional_value_dispatch",  # value-repr subject: multi-arm inner tier
    "match.optional_none_arm",      # `case None:` prefix -> the nullptr block
    "match.optional_value_only",    # no None arm -> bare `if (s != nullptr)`
    "match.optional_inner_bind",    # capture/as vs the __match_inner_N alias
    "match.optional_inner_record",  # record inner: if/elif chain on the alias
    "match.if_elif_optional",       # non-partitioned Optional: unguarded chain
    "match.if_elif_optional_guarded",  # its standalone-if + goto sibling
    "match.optional_chain_or",      # or-pattern cond groups (None | lit | ...)
    "match.optional_full_bind",     # capture/as binding the full Optional
    "match.switch_str",             # str discriminator switch (size/char_at)
    "match.str_guard_prefix",       # guarded literal arm before the switch
    "match.str_trailing_arm",       # wildcard/capture arm after the switch
    "match.wildcard_default",       # `case _:` -> the `default:` block
    "match.synthetic_default",      # non-exhaustive: `default: break;`
    "match.unreachable_tail",       # exhaustive + terminating arms tail
    "match.hoist_decl",             # sema-hoisted plain-value predecls
    # Emit-side finally-frame walks (recorded at emission -- the chain is
    # structural, so lowering never sees it; a no-op outside a compilation).
    # The with/try prefix keys on the walked segment's frame arms, so a
    # mixed stack witnesses both.
    "with.finally_return",          # return in body: inline __exit__ chain
    "with.finally_loop_exit",       # break/continue in body: partial chain
    "try.finally_return",           # return in body: finally body re-emitted
    "try.finally_loop_exit",        # break/continue in body: partial chain
    "try.chain_terminated",         # terminating finally suppressed the exit
    "match.loop_break_goto",        # break escaping a switch: goto __loop_break_N
    "loop.break_else_goto",         # break out of an else-loop: goto __after_else_N
    # The five flushable statement positions, counted only when the
    # position's value actually hoists an arg temp.
    "flush.vardecl",
    "flush.assign",
    "flush.field_write",
    "flush.return",
    "flush.expr_stmt",
    # Resumable (async) leaf routing -- the gen_async seam. One face per
    # leaf-render kind the skeleton delegates, plus the routed-body tally.
    "res.body",                     # one routed resumable body
    "res.decl_assign",              # hoisted frame-field decl -> assignment
    "res.branch_cond",              # Branch terminator condition render
    "res.await_args",               # sub-coro emplace argument renders
    "res.return_value",             # ReturnT value render for _make_async_return
    "res.nested_return",            # return in a leaf compound (skeleton hook)
    "res.postif_narrow",            # early-return narrowing leaf if (post-if alias)
    "res.finally_stop",             # generator helper return (__finally_stop pair)
    "res.branch_frame_write",       # branch-nested plain frame-field decl
    "res.leaf_try_except",          # except-only leaf try (sync tiers mid-state)
    "res.yield_container_borrow",   # container yield of a frame_slot name (*buf)
    "res.frame_unpack",             # frame-target tuple unpack (rvalue source)
    "res.loop_ptr_bind",            # pointer-form loop var admitted (T* reads)
    "res.loop_slot_bind",           # frame_slot loop var admitted ((*x) reads)
    "res.loop_tuple_bind",          # value-tuple holder loop admitted
    "res.yield_record_borrow",      # record yield of a routed loop-var name
    "res.yield_value",              # generator yield-value render
    "res.frame_slot_write",         # frame_slot local `.emplace()` write (R1c)
    "res.coro_handle_write",        # concrete-coro handle factory-call bind
    "res.await_prebuilt",           # bound-handle await routed (poll-in-place)
    "res.match_dispatch",           # MatchDispatch routed through the tiers
    "res.narrow_scope",             # BB leaves lowered under a narrowed scope
    "res.suspend_expr",             # ERASED/BORROWED operand + bound receiver (R5)
    "res.suspend_operand",          # ERASED/BORROWED whole-operand render
    "res.try_region",               # body routed with a try/except region (R6)
    "res.with_region",              # body routed with a with region (R6)
    "res.with_ctx",                 # with-region manager expression render
    "res.finally_helper",           # helper-based finally body routed (R6)
    "res.for_iter_setup",           # sync for-loop iterable/range render (R3)
    "res.sync_loop",                # body routed with a sync for-loop (R3)
    "res.async_loop",               # body routed with an async for-loop (R3)
    "res.async_with",               # body routed with an async with (R5)
    # Simple-generator (lambda peephole) leaf routing -- the gen_generators
    # seam. One face per leaf-render kind the skeleton delegates, plus the
    # routed-body tally.
    "sgen.body",                    # one routed simple-generator body
    "sgen.while_cond",              # while-branch condition render
    "sgen.yield_value",             # yield-value render
    "sgen.iterable",                # for-branch iterable render
    "sgen.range_arg",               # for-range bound renders
    # The universal __iter__/__next__ protocol foreach (generator-call /
    # iterator-returning-call / user-iterator-name iterables --
    # _gen_direct_next_loop_with_iter).
    "foreach.iter_proto",
    # Tuple-unpack head over the universal loop (`for a, b in zip(..)` /
    # a tuple-yield generator call -- the same head decls as the container
    # tuple-unpack, THIRForIterProto instead of begin/end).
    "foreach.tuple_unpack_iter",
    # Class-constant read -> the bare qualified static (lowering;
    # `C::LIMIT`, `::tpyapp::m::Limits::MAX`, `C<int32_t>::X` -- the
    # receiver_eval-None shapes of _class_constant_access_parts).
    "field.class_const",
    # Module-variable read -> the fixed registered spelling (lowering;
    # `mod.X` off a MODULE binding or the dotted `pkg.sub.X` marker --
    # native_cpp_name / (*slot) / qualified cpp_expr).
    "field.module_var",
    # Class-constant / classvar write -> the bare qualified lvalue
    # (lowering; `C::X = v;` -- gen_class_constant_lvalue's assign arm).
    "field_write.class_const",
    # Class-constant aug-assign -> the binop substitution over the
    # qualified lvalue (lowering; `C::X = add_check<int32_t>(C::X, v);`).
    "aug.class_const",
    # Class-constant write receiver evals, split off as a leading statement
    # (lowering): the unproven-Optional pointer-name check
    # (`::tpy::deref_check(c);`) and the effectful receiver discard
    # (`static_cast<void>(make_c());`). Shared by the assign and aug arms.
    "field_write.cc_recv_check",
    "field_write.cc_recv_effect",
    # Same-module global-record receiver field read (lowering; the `T*`
    # pointer-slot name with an arrow -- `time->x`).
    "field.global_record_recv",
    # F1-record-returning call/method-call receiver field read (admission;
    # the bare postfix member over the call render -- `f().x`,
    # `h.boxed.get().x` -- the inner call lowers through its own arms).
    "field.call_recv",
    # `.field` through an explicit `Ptr[record]` VALUE receiver -> `p->field`
    # (proven non-null) or `::tpy::deref_check(p).field` (unproven), picked from
    # sema's `ptr_non_null`. Read and write target alike.
    "field.ptr_value",
    # `.field` auto-dereffed through a USER Deref wrapper -> `r.__deref__().x`
    # (N = deref_depth). Bare `.` receiver; read and scalar-write target alike.
    "field.user_deref_chain",
})


def witness(face: str) -> bool:
    """Record one hit of `face` on the active compiler; no-op (but still
    True) when no compilation is in flight. Returns True so classifiers can
    tack it onto their local conjunction (`... and witness("own.x")`)
    without restructuring."""
    assert face in THIR_FACES, f"unregistered THIR face: {face}"
    compiler = get_current_compiler()
    if compiler is not None:
        w = compiler._thir_face_witnesses
        w[face] = w.get(face, 0) + 1
    return True
