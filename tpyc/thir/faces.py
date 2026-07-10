"""Per-face witness tally; reported by the --thir-codegen zero-witness summary.

The corpus byte-diff proves routed bodies emit byte-identical C++, but says
nothing about a face (a gate arm / a lowering render) that NO corpus case
reaches -- a latent bug there stays invisible until its first witness
arrives. The test harness folds these counts across cases and xdist workers
(like the routed-body tally) and reports registered faces with zero
witnesses over the whole corpus run.

Witness semantics differ by face kind (encoded in the registry comment):
lowering faces record at THIR-node construction (the render actually
fired); the `own.*` gate rows record at gate ADMISSION -- their render is
the bare arg shared with the pass-through emit, so admission is the only
distinguishing site (an admit in a body later rejected elsewhere still
counts, a deliberate over-approximation); `flush.*` record when a flushable
statement position's lowered value actually carries a hoisted arg temp.

The registry is immutable metadata (module-level by design); the mutable
counts live on the active Compiler (`_thir_face_witnesses`), so the helper
is a no-op outside a compilation. Recording is NOT flag-gated: it happens
wherever lowering and gating run, which is every case of every run with
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
    "argtemp.ctor_mut_rvalue",      # record rvalue into a MUTATED ctor slot
    "ctor.const_rvalue_arg",        # record rvalue inline into a const ctor slot
    "argtemp.own_copy",             # Own-slot copy+move `__tmp_N` temp
    # The temp-free last-use move (lowering).
    "move.own_last_use",            # `f(std::move(name))`
    # Pointer-repr Optional[record] slot faces (lowering).
    "optptr.none",                  # `nullptr`
    "optptr.ctor_rvalue",           # `&(__tmp_N)` addr-of arg temp
    "optptr.lift",                  # `::tpy::optional_to_ptr(...)`
    "optptr.pass",                  # already-`T*` binding passes bare
    "optptr.name",                  # `&(name)`
    # Value-repr Optional slot None arg (lowering): the value-optional twin
    # of `optptr.none` -- `f(std::nullopt)`.
    "call.none_value_opt",
    # Pointer-variant union-slot lifts (lowering).
    "unionlift.none",               # `pv{std::monostate{}}`
    "unionlift.const_wrap",         # `ptr_variant_to_const(...)`
    "unionlift.member",             # `pv{&(name)}`
    # Own-cascade bare rows + the readonly ctor tail (gate admission).
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
    "call.module_native",           # bare-@native module callee `m.f(x)`
                                    # -> `::native(args)`
    "call.static_template",         # positional-only @cpp_template static
                                    # (`UInt32.trunc(i)`) -> template expansion
    "call.macro_expansion",         # `@call_macro`/getattr/hasattr call ->
                                    # its sema-synthesized replacement expr
    "call.cast_passthrough",        # `typing.cast(T, x)` non-Any -> bare `x`
    # Ptr[T]-receiver Deref method calls (lowering; the THIRMethodCall
    # is_arrow / deref_check renders over a pointer-VALUE receiver).
    "method.ptr_arrow",             # proven non-null: `p->m(args)`
    "method.ptr_checked",           # `::tpy::deref_check(p).m(args)`
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
    # Value-record field method receiver (`self.field.m()` -> the user-record
    # arm over a bare `this->field` / `p->field` THIRFieldAccess receiver). The
    # field's record spells byte-identically (`_f1_record`: same-module,
    # cross-module, @native, and concrete-arg generic records all qualify), so
    # native / generic field receivers ride the same face as a plain one.
    "method.recv.record_field",
    "ctor.call",                    # THIRCtorCall bare ctor expansion
    "ctor.str_arg",                 # str-slice arg into a str-family ctor slot
    "ctor.omit_defaults",           # ctor call omitting trailing default args
                                    # (defaults ride the C++ ctor signature)
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
    "setitem.checked",              # `::tpy::__setitem__(c, k, v);`
    "setitem.bounds_safe",          # `c[static_cast<std::size_t>(k)] = v;`
    "setitem.aug",                  # `c[k] OP= v` -> the getitem/setitem pair
    "setitem.str_owned_copy",       # view source into a str element: std::string(v)
    "setitem.field_recv",           # write/aug receiver is a field access
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
    # Value-storage Optional[record] FIELD write (`std::optional<inner>`) from
    # a record NAME: the bare copy `recv.opt = p;` (optional::operator=) or
    # `std::move(p)` at a movable name's last use.
    "field_write.optrec_name",
    # The Optional[record] FIELD write from a record RVALUE (ctor / by-value
    # call of the inner type): the bare copy `recv.opt = Inner(args);`.
    "field_write.optrec_rvalue",
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
    "mil.value_tuple_name",         # `t(t)` -- value-tuple param bare copy
    "mil.value_tuple_literal",      # `t(std::tuple<...>{...})` spelled literal
    # An own-field init the AST demotes to the ctor body (bare non-param name /
    # nested-def name / body-local ref) -- THIR demotes identically instead of
    # rejecting the whole ctor (gate verdict; the body machinery renders it).
    "mil.demote_mirror",
    # Container/str subscript read off a FIELD-ACCESS receiver (lowering;
    # `::tpy::__getitem__(this->xs, i)` -- the receiver renders as its own
    # THIRFieldAccess inside the shared subscript emit).
    "subscript.field_recv",
    # Bytes-family FIELD subscript read (lowering; the same field-receiver
    # widening through the bytes dispatch -- `::tpy::bytes_getitem(this->b, i)`).
    "subscript.bytes_field",
    # A value scalar/Char/enum/typeparam/Ptr field read off a value F1-record
    # field CHAIN receiver (gate admission; `o.mid.inner.v` -- `_lower_expr`
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
    # `len(recv.field)` -- a container/str/bytes field arg to the builtin len
    # (lowering; `::tpy::__len__(this->xs)`, the field renders as its own
    # THIRFieldAccess inside the shared native-call emit).
    "len.field_recv",
    # Container-FIELD for-each iterable (lowering; `for x in self.xs:` -- the
    # field renders inside the same lvalue `auto& __obj_N =` capture a name
    # takes; str/bytes fields ride the older viewfam admission).
    "foreach.container_field",
    # A str method returning `Own[list[str]]` as a for-each iterable (lowering;
    # `for w in s.split():` -- the owning `auto __obj_N = ::tpy::str_split...(s)`
    # rvalue capture, iterated like any list[str]).
    "foreach.str_list_method",
    # Value-tuple element for-each loop var (lowering; a `list[tuple[...]]`
    # element or a `d.items()` key/value pair -- binds the whole tuple as
    # `auto&& t = *__beg_N;`, subscript reads render `std::get<i>(t)` bare).
    "foreach.value_tuple_elem",
    # Runtime-BigInt `.to_fixed_check<T>()` narrows (lowering; the AST's
    # gen_index_expr / _gen_slice_bound / aug-assign / enum-from_value wraps).
    "narrow.subscript_index",       # `i.to_fixed_check<int32_t>()` (reads + del)
    "narrow.slice_bound",           # same wrap on a str/bytes slice bound
    "narrow.aug_value",             # `({0}).to_fixed_check<T>()` aug-assign value
    "narrow.enum_arg",              # `({0}).to_fixed_check<U>()` E(x) arg
    # Record return slots (gate admission; the renders -- bare name / bare
    # ctor expansion -- are shared with the pass-through emits, so admission
    # is the only distinguishing site).
    "ret.record_borrow",
    "ret.record_storage",
    "ret.record_self",              # `return self` -> `return (*this);`
    "ret.record_field",             # `return recv.field` at the borrow slot
    "ret.str_field",                # `return recv.field` (owned-str member,
                                    # STORAGE) at a str-family return slot
    # Storage container return slot (`-> Own[list/dict/set]`; the renders --
    # bare owned name / the decl-init literal emits -- are shared, so
    # admission is the distinguishing site).
    "ret.container_name",
    "ret.container_literal",
    "ret.container_call",           # `return make_list(n);` -- bare call source
    "ret.tuple_call",               # `return make_pair(n);` -- bare call source
    # Value-repr Optional[cheap scalar] return slot (`-> Int32 | None`): the
    # None-literal `std::nullopt` arm and the whole-optional bare param pass
    # (deref-on-narrow stripped); other scalar sources ride the generic tail.
    "ret.value_opt_none",
    "ret.value_opt_name",
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
    # Value-tuple slots (`tuple[scalar|str, ...]`): the spelled
    # `std::tuple<...>{...}` literal render at returns / decls, and the bare
    # value-tuple name return.
    "ret.tuple_literal",
    "ret.tuple_name",
    "decl.tuple_literal",
    # Widened value-tuple RETURN elements: a NESTED value-tuple element (spelled
    # recursively) and a value-`Optional[scalar]` element (`None`->`std::nullopt`
    # / a scalar value bare). Return-slot only.
    "ret.tuple_nested_elem",
    "ret.tuple_opt_elem",
    # A value-`Optional[str]` RETURN element: a str-view source wraps
    # `std::string(view)` through the Optional slot; `None`->`std::nullopt`, a
    # str literal bare.
    "ret.tuple_opt_str_elem",
    # Owned record local decl (lowering; the `{cpp_type} {name} = <rvalue>;`
    # plain-value render).
    "decl.owned_record",
    # Owned record local decl from a method-call rvalue source (`Rec r =
    # b.build();`) -- the method sibling of the free-call `decl.owned_record`.
    "decl.owned_record_method",
    # Storage-call local decl (gate admission; a container/tuple/union-
    # returning call init -- the bare `T x = f(...);` / plain reassign,
    # rendered by the shared generic decl tail).
    "decl.storage_call",
    # REF_ALIAS from a borrow-record-returning call (lowering; the
    # `T& p = shared(x);` bind of the callee's returned reference).
    "decl.record_borrow_call",
    # Ptr[T] value-slot admission (gate; bare passes / field reads share the
    # scalar renders, so the predicate is the only distinguishing site).
    "ptr.value_slot",
    # The @dynamic-protocol pointee arm of the same predicate (gate; the
    # pointee spelling is the shared PtrType.to_cpp on both paths, so
    # admission distinguishes it from the record/scalar pointees).
    "ptr.dyn_proto_pointee",
    # `x = None` at a Ptr[T] value binding (lowering; the `nullptr` render).
    "decl.ptr_none",
    # A read of a read-only-seeded same-module value global (lowering; the
    # bare-name render shared with locals, so the seed is what distinguishes).
    "name.global_seeded",
    # A read of a read-only-seeded NATIVE-linkage value global (lowering;
    # the pre-rendered `::symbol` spelling on THIRName.cpp).
    "name.global_native",
    # A read of a read-only-seeded IMPORTED value global (lowering; the
    # pre-rendered `::tpyapp::mod::g` / native_cpp_name spelling on
    # THIRName.cpp -- imported_variable_cpp, shared with the AST render).
    "name.global_imported",
    # BigInt-counter range loop (gate admission; the render difference is
    # the `::tpy::BigInt` cpp_elem + literal-bound retype, shared with the
    # fixed-int emit).
    "range.bigint_counter",
    # 3-arg stepped range loop, by step arm (gate admission; each arm's emit
    # is a distinct overflow / direction shape mirroring _gen_range_counter_loop).
    "range.step_plus_one",          # literal +1 step -> the ascending ++ loop
    "range.step_unit_neg",          # literal -1 step -> the descending -- loop
    "range.step_literal_pos",       # non-unit positive literal step
    "range.step_literal_neg",       # non-unit negative literal step
    "range.step_variable",          # fixed-int-name step (captured `__step_N`)
    # Bool-field truthiness condition (gate admission; `if self.closed:` --
    # a bool value's truthiness render IS its value render, so the admitted
    # field-read emit carries the condition unchanged).
    "cond.bool_field",
    # Bool-method-call truthiness condition (gate admission; `if g.is_open():`
    # -- the same bare-render property as cond.bool_field, over the method
    # call's value-position admission).
    "cond.bool_method",
    # @builtin_type record with a real body and no cpp_formatter (Poll;
    # Waker's formatter-carrying TypeDef stays excluded) admitted as an F1
    # record (gate admission; the user-record spelling path, so every
    # render is shared).
    "recv.builtin_record",
    # Conditional-expression renders (lowering, except cond_pos at gate
    # admission -- the condition-position render is shared with the value
    # emit, so admission is the distinguishing site).
    "ifexpr.value",                 # scalar / Char / enum result
    "ifexpr.str",                   # str-family result (form-tagged)
    "ifexpr.str_mixed",             # mixed view/owned arms: view-arm wrap
    "ifexpr.cond_pos",              # bool ternary as an if/while condition
    # Enum value-binding renders (lowering).
    "enum.truthy_plain",            # plain-enum truthiness -> literal `true`
    "enum.truthy_int",              # IntEnum truthiness `(static_cast<U>(x) != 0)`
    "enum.neg",                     # IntEnum `-x` -> `(-static_cast<U>(x))`
    "enum.value",                   # `.value` -> `static_cast<U>(x)`
    "enum.name",                    # `.name` -> `EnumUtil<E>::name(x)` (BORROW)
    "enum.repr_print",              # @native enum print arg -> `::tpy::__repr__`
    "enum.nested_from_value",       # `Outer.Kind(v)` EnumUtil from_value
    # F-string per-arg rows (the wrap table; witnessed at gate probe and
    # again at lowering -- non-vacuity only needs a nonzero count).
    "fstr.conv_repr",               # `!r` -> `::tpy::repr_of({0})` wrap
    "fstr.conv_str",                # `!s` no-op passthrough (non-user types)
    "fstr.str_field",               # owned-str field arg formats bare
    "fstr.char_arg",                # Char arg formats bare (`char` is
                                    # std::formattable; no int8 cast)
    "fstr.spec",                    # constant format spec -> `{:spec}`
                                    # placeholder (lowering, routed args only)
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
    # dict/set membership (`needle in c` -> `(c.contains(needle))`, the
    # resolved_contains arm; witnessed at gate admission and again at
    # lowering -- non-vacuity only needs a nonzero count).
    "binop.membership",
    # A fixed-int bitwise op (`a & b`, `a << b`, ...) admitted at the scalar
    # arm -- same resolved-binop template emit as arithmetic (gate admission).
    "binop.bitwise",
    # A compositional container param (`list`/`dict`/`set`/`Array`/`Span` of any
    # fully-concrete element) admitted so its subscript / len / iteration /
    # membership route (gate admission; the by-ref signature is AST-emitted and
    # element-type-neutral, so admission is the distinguishing site for the
    # newly-reachable bodies -- each element USE is gated by the body walk).
    "param.container",
    # Trivia (lowering): docstring / `pass` -> THIRNoOpStmt, body-wide.
    "stmt.trivia",
    # Standalone `a, b = <name>` unpack of a value-scalar tuple (lowering):
    # `const auto& __tup_N = name;` + per-target scalar decls.
    "stmt.tuple_unpack",
    # A non-name unpack source (lowering): a value-tuple-returning call or a
    # value-tuple field read -> `auto __tup_N = <expr>;` (value capture).
    "stmt.tuple_unpack.rvalue_source",
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
    "print.optval",                 # un-narrowed value-repr Optional[scalar/str]
                                    # print arg -> bare `::tpy::print_optional_val`
    "print.wrap_arg",               # container / value-tuple / F1-record NAME
                                    # print arg -> its kind-keyed printer wrap
    "comp.array_range",             # Array demotion: array_from_index range lambda
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
    "match.optional_partition",     # Optional-ptr subject: None/has-value split
    "match.optional_none_arm",      # `case None:` prefix -> the nullptr block
    "match.optional_value_only",    # no None arm -> bare `if (s != nullptr)`
    "match.optional_inner_bind",    # capture/as vs the __match_inner_N alias
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
    "res.yield_value",              # generator yield-value render
    "res.frame_slot_write",         # frame_slot local `.emplace()` write (R1c)
    "res.suspend_expr",             # ERASED/BORROWED operand + bound receiver (R5)
})


def witness(face: str) -> bool:
    """Record one hit of `face` on the active compiler; no-op (but still
    True) when no compilation is in flight. Returns True so gate arms can
    tack it onto their admission conjunction (`... and witness("own.x")`)
    without restructuring."""
    assert face in THIR_FACES, f"unregistered THIR face: {face}"
    compiler = get_current_compiler()
    if compiler is not None:
        w = compiler._thir_face_witnesses
        w[face] = w.get(face, 0) + 1
    return True
