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
distinguishing site; `flush.*` record when a flushable
statement position's lowered value actually carries a hoisted arg temp.

Every kind is journalled per lowering attempt and ROLLED BACK when the body
falls back (`rollback_witnesses`, driven from fallback.py's attempt
boundaries): a fallback emits its whole tree through the AST path, so an arm
it merely reached covers nothing. Without that, an arm witnessing before it
can raise reads as covered when it never lowered -- which is how a dead arm
passed this very check.

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
    "argtemp.recursive_union_literal",  # list/dict literal into a recursive-
                                    # union wrapper slot (json.dumps([...]))
    "argtemp.ru_wrapper_literal",   # scalar/str literal into a
                                    # recursive-union wrapper slot
    "argtemp.ru_wrapper_member",    # member-typed NAME into a wrapper slot
                                    # (`Tree __tmp_N = std::move(b);`)
    "argtemp.record_rvalue",        # record-ctor rvalue into a ref slot
    # Protocol-slot arg wrap: the @dynamic Adapter / RefAdapter / concrete
    # materialization, and the structural slot's `auto __tmp_N` rvalue temp.
    "argtemp.protocol",
    "argtemp.iter_proto",           # gen-factory / iter() / dict-view rvalue
                                    # at a structural slot -> un-spelled
                                    # `auto __tmp_N = <rvalue>;` temp
    "argtemp.marker_protocol_literal",  # container literal at a PLAIN module
                                    # callee's structural slot -> the qualcall
                                    # loop's `auto __tmp_N = <self-spelled>` hoist
    "argtemp.marker_protocol_range",    # range rvalue at the same slot ->
                                    # `auto __tmp_N = ::tpy::Range<...>(..);`
    "arg.deref_coerce_inline",      # Ptr[T] deref coercion at a record slot
                                    # -> inline `::tpy::deref_check(p)`
    "move.opt_own_last_use",        # record name moved bare into an
                                    # Optional[Own[T]] slot (converting ctor)
    "arg.native_protocol_value",    # scalar/Char/str value at a native
                                    # protocol slot -> bare render (__hash__)
    "arg.readonly_empty_container", # empty [] / list() at a readonly slot
                                    # -> inline typed rvalue (const-ref bind)
    "arg.ru_wrapper_narrowed",      # F6-narrowed member alias passed bare
                                    # into a same-wrapper arg slot
    "expr.lambda_void_print",       # void print-body lambda -> the
                                    # statement-body closure { cout << ...; }
    "argtemp.deref_coerce",         # wrapper `__deref__()` coercion -> the
                                    # slot-typed VALUE copy temp
    "argtemp.ctor_mut_rvalue",      # record rvalue into a MUTATED ctor slot
    "ctor.const_rvalue_arg",        # record rvalue inline into a const ctor slot
    "argtemp.own_copy",             # Own-slot copy+move `__tmp_N` temp
    "argtemp.own_str",              # Own[str]-slot copy temp: the owned type
                                    # declared with brace init (`std::string
                                    # __tmp_N{<arg>};` -- the view->owned
                                    # conversion), field / coerced-field args
    "argtemp.container_literal",    # list literal into a free-call container
                                    # ref slot -> hoisted `__tmp_N` temp
    "argtemp.generic_container_literal",  # the same hoist at a GENERIC
                                    # callee's substituted container slot
    "expr.walrus_scalar",           # value-scalar walrus `(n = v)` + named
                                    # pre-decl on the sink's named row
    "expr.walrus_opt_ptr",          # ptr-Optional walrus target: `T* n =
                                    # nullptr;` + borrow-lifted assign
                                    # (optional_to_ptr / nullptr / bare ptr)
    "expr.walrus_ptr_alias",        # borrow-alias pointer walrus target:
                                    # `[const ]T* n = nullptr;` +
                                    # `(n = &(v), *n)` (bare for a
                                    # pointer-name source)
    "field.walrus_recv",            # field read off a walrus receiver
                                    # (`(q = &(b), *q).v`, dot access)
    "expr.walrus_value_opt",        # value-opt scalar walrus reassign:
                                    # `(x = std::nullopt)` / converting
                                    # scalar assign
    "expr.walrus_owned_viewfam",    # owned str/bytes walrus reassign:
                                    # `(s = str_concat(...))` in place
    "expr.walrus_owned_slot",       # owned non-value walrus off an Own
                                    # call: `std::optional<T> n;` +
                                    # `(n = make(), *n)`, `(*n)` dot reads
    "expr.walrus_btuple",           # non-reassigned borrow-tuple walrus:
                                    # `std::tuple<..., T*> t;` + borrow
                                    # literal / tuple_to_pointer lift
    "assign.btuple_elem_field",     # scalar field write through a borrow-
                                    # tuple element (std::get<N>(t)->f = v)
    "expr.walrus_btuple_slot",      # reassigned borrow-tuple walrus: owning
                                    # __slot_N.emplace + tuple_to_pointer +
                                    # bare-name tail
    "field.walrus_subscript_recv",  # scalar field off a record-elem
                                    # subscript over a reassigned btuple
                                    # walrus (std::get<N>((t = ..))->f)
    "binop.value_select",           # value-position and/or: the once-
                                    # evaluated-LHS ternary
                                    # (_gen_logical_value's value slice)
    "binop.container_select",       # container and/or over lvalue
                                    # operands: the __len__-truthy
                                    # ternary aliasing the chosen side
    "binop.protocol_raw",           # protocol-operand arith in a template
                                    # body: the raw `(a + b)` operator
    "ifexpr.container",             # container ternary: the bare
                                    # form-blind arm render
    "stmt.compile_time_assert",     # assert_send/assert_sync statement:
                                    # sema-checked, zero emission (NoOp)
    "ctor.union_pass_arg",          # already-union NAME bare into a
                                    # same-union record-ctor slot
    "method.struct_proto_union_arg",  # NAME into a structural-protocol union slot
    "method.union_pass_arg",        # already-union NAME bare into a non-
                                    # dcbp pointer-variant method slot
    "dynown.make_unique",           # inheritance conformer into Own[dyn P]:
                                    # std::make_unique<U>(x)
    "dynown.adapter_conformer",     # structural conformer into Own[dyn P]:
                                    # ::tpy::make_adapter<Base>(x)
    "argtemp.covariant",            # covariant-upcast typed temp:
                                    # `Box<Shape> __tmp_N = std::move(bc);`
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
    "er.bind_ptr_rebind",           # bind line reseats `&*(__slot_N = ..)`
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
    "own.opt_ptr_name_rebuild",     # ptr-repr Optional name into the
                                    # same slot: null-safe move rebuild
    "own.record_copy",              # copy(name) into Own[record]: T(x)
    "method.protocol_discard",      # discarded protocol-method result in
                                    # statement position -- bare call
    "method.record_discard",        # discarded F1-record method result at
                                    # stmt position: the bare call
    "method.container_discard",     # discarded container method result: same bare call
    "method.qualcall.record_discard",  # discarded record-family qualcall result
                                       # (asyncio.create_task(...);): the bare call
    "method.qualcall.record_storage",  # record-family qualcall rvalue at a
                                       # storage sink: bare call into the slot
    "method.container_iterable",    # container method result as a for-head iterable
    "method.qualcall.container_iterable",  # marker-call container as a for-head iterable
    "method.qualcall.container_discard",  # discarded marker-call container result
    "method.consuming_move",        # consuming method: std::move(name) receiver wrap
    "call.native_record_arg",       # F1-record call rvalue bare into a native slot
    "call.value_record_arg",        # record rvalue bare into a by-value record slot
    "call.float_str_fold",          # float("nan"/"inf") -> spelled numeric-limits constant
    "print.record_call",            # F1-record call rvalue streams raw via operator<<
    "print.protocol_call",          # protocol-result call (`print(iter(s))`) streams raw
    "print.protocol_name",          # protocol-typed name streams raw
    "own.union_ctor",               # record-ctor rvalue into Own[union]
    "own.readonly_ctor",            # record-ctor rvalue into readonly slot (sync callee)
    # Self receiver / ctor-call renders (lowering).
    "self.this",                    # `self` name read -> `this`
    "call.self_method",             # `self.helper()` -> `this->helper()`
    "call.imported",                # cross-module callee -> pre-rendered
                                    # `::tpyapp::mod::f` (callee_cpp)
    "call.native_free",             # C++ @native free callee -> `::native(args)`
    "call.template_arg_dropped",    # @cpp_template never spells {i}: the
                                    # arg render cannot reach the expansion
    "call.expr_callee",             # computed callable: (callee)(args)
    "call.strlit_overload_pin",     # str literal pinned to its overloaded slot
    "call.native_c_free",           # C-linkage @native free callee -> raw `sym(args)`
    "call.own_iter_arg",            # movable last-use container into an Iterable slot -> ::tpy::own_iter(std::move(x))
    "call.own_iter_explicit",       # explicit own_iter(x) -> ::tpy::own_iter(std::move(x))
    "call.copy_iter_explicit",      # copy_iter(it) -> ::tpy::copy_iter<Elem>(<it>)
    "call.try_parse",               # try_parse(Enum, s) -> EnumUtil<E>::
                                    # try_parse(s)
    "call.native_ret_cast",         # @native cpp_return_type: static_cast<
                                    # declared>(::sym(args))
    "call.template_free",           # positional-only @cpp_template free callee
    "call.instantiation_template",  # generic-type instantiation `list(it)` ->
                                    # sema-substituted ctor template expansion
    "call.instantiation_empty",     # empty `set()`/`list()`/`dict()` -> the
                                    # spelled default ctor `T()`
    "call.viewfam_instantiation",   # str-family VALUE instantiation
                                    # `StrView("x")`/`String("x")` -> the ctor
                                    # @cpp_template over inline args

    "call.inst_bare_name_arg",      # non-movable name at a container
                                    # instantiation: the bare render
    "call.inst_call_rvalue_arg",    # `set(make_nodes())` -> an owning call
                                    # rvalue inline in the construct template
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
    "ctor.typed_dict",              # TypedDict ctor: init_params spelling, no fi
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
    "call.dyn_hasattr",             # runtime hasattr probe -> the try/catch
                                    # stmt-expr over `obj.__getattr__(name)`
    "call.dyn_getattr_default",     # getattr(obj, name, default) -> the
                                    # optional-deferred try/catch stmt-expr
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
    "method.overload_set_call",     # genuine (non-mangled) method stub set call
    "method.ptr_arrow",             # proven non-null: `p->m(args)`
    "method.ptr_checked",           # `::tpy::deref_check(p).m(args)`
    "method.user_deref_chain",      # `r.__deref__()...m(args)` user Deref proxy
    "method.user_deref_stub",       # container MEMBER stub through the Deref chain (push_back)
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
    "method.recv.tuple_elem_subscript",  # `xs[i][j].append(v)` -- std::get<j>
                                    # over a container-element borrow, the
                                    # container-method arm over a `.` receiver
    # Method-call method receiver (`a.b().c()` -> the user-record arm over an
    # inner-call receiver whose result is a plain non-pointer record, `.`
    # access; the inner call renders via the shared method lowering).
    "method.recv.method",
    "method.recv.protocol",         # inner call yields a protocol borrow
                                    # (`box.get()` -> `Pet&`) -> `.` outer call
    # Inner call yields a CONTAINER borrow (`b1.take().append(4)`,
    # `groups.setdefault("a", []).append(1)`) -> the stub method composes
    # onto the bare call render with `.`.
    "method.recv.container_method",
    # Value-record field method receiver (`self.field.m()` -> the user-record
    # arm over a bare `this->field` / `p->field` THIRFieldAccess receiver). The
    # field's record spells byte-identically (`_f1_record`: same-module,
    # cross-module, @native, and concrete-arg generic records all qualify), so
    # native / generic field receivers ride the same face as a plain one.
    "method.recv.record_field",
    "method.recv.free_call",        # `make(3).get()` -- a plain F1-record
                                    # free-call result receiver, `.` access
    "method.recv.binop",            # `(dt + td).isoformat()` -- a record-
                                    # result dunder-binop receiver (gate)
    "method.recv.str_literal",      # `"a,b,c".split(",")` -- a str-literal
                                    # receiver rendered bare into the resolved
                                    # builtin-method template
    "method.recv.bytes_literal",    # the bytes twin -- the OWNED literal
                                    # receiver substituted into the template
    "method.protocol_field_recv",   # protocol method over a one-level field
                                    # receiver (this->factory.make())
    "arg.native_module_var",        # module-variable deref read pinned at a
                                    # native slot (len(os.environ))
    "method.recv.view_field",       # str/bytes field receiver routing the
                                    # view family over the member read
    "method.recv.bytes_method",     # `srv.recv(32).decode()` -- a bytes-VALUE
                                    # method-call result feeding the outer
                                    # bytes method's receiver slot
    "method.recv.str_method",       # `s.strip().lower()` -- a str-VALUE
                                    # method-call/free-call receiver, the inner
                                    # str method's bare nested-call render
    "ctor.call",                    # THIRCtorCall bare ctor expansion
    "ctor.native",                  # native-record (builtin exception) ctor: `::tpy::OSError(...)`
    "ctor.native_plain",            # plain @native record ctor: `::Vec2(...)` / @native_c `::Point{...}`
    "ctor.ptr_null",                # `Ptr[T]()` -> `static_cast<T*>(nullptr)`
    "ctor.cross_module",            # imported-record ctor: the qualified
                                    # `::ns::Name(args)` spelling
    "ctor.str_arg",                 # str-slice arg into a str-family ctor slot
    "ctor.lambda_arg",              # routable lambda into a Callable ctor slot
                                    # -> the inline closure, temp-free
    "method.callable_field",        # callable-field invocation h.cb(3) ->
                                    # the bare member call (std::function)
    "cfield.container_arg",         # container FIELD arg to a callable-field
                                    # call reads bare into the `T&` param
    "ctor.own_arg",                 # Own-slot ctor arg via the shared cascade
                                    # rows (last-use move / copy+move temp)
    "ctor.container_literal_arg",   # list literal into a ctor's list slot:
                                    # the bare brace-init render in place
    "ctor.value_opt_pass_arg",      # whole value-opt name into the same
                                    # Optional ctor slot (bare copy)
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
    "aug.inplace_dunder",           # resolved inplace method (`b += 10` on
                                    # Atomic -> `b.__iadd__(10);`, `s |= {3}`
                                    # -> set_update) via gen_call_from_fi
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
    "setitem.btuple_call",          # ptr-Optional-tuple value slot: a
                                    # borrow-tuple call lifts via the
                                    # non-move tuple_to_storage
    "setitem.btuple_literal",       # ... a tuple LITERAL value: the borrow
                                    # tuple with plain lifts, same non-move
    "setitem.btuple_elem_pass",     # ... a same-tuple element read passes
                                    # bare (`d2[k] = d[k]`)
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
    "field_write.union_member_ctor",  # member ctor rvalue -> bare variant store
    # F1-record element/value slot: a record RVALUE (exact or covariant
    # upcast) forwarded bare by the checked `__setitem__`
    # (`::tpy::__setitem__(s._pool, key, Box<Conn>(std::move(conn)));`).
    "setitem.record_rvalue",
    # F1-record element/value slot from `copy(name)`: the copy-construct
    # rvalue (`::tpy::__setitem__(items, 0, Point(p));`).
    "field_write.container_copy",   # `self.items = copy(data)` ->
                                    # `std::vector<T>(data)`
    "setitem.record_copy",
    # F1-record element/value slot from a plain record NAME: the bare copy
    # (`::tpy::__setitem__(items, 0, p);`) or `std::move(p)` at a movable
    # name's last use.
    "setitem.record_name",
    # `recv.opt = None` at any Optional FIELD: the storage-form `std::nullopt`,
    # keyed on the declared field type (a narrowed write site still stores it).
    "field_write.opt_none",
    # Dynamic-attrs (D16) family faces.
    "setitem.any_value",            # `d[k] = v` into a dict[K, Any] slot from
                                    # an Any-typed name (bare, no make_any)
    "delitem.container",            # del over an element-blind container
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
    "ret.any_wrap",                 # non-Any value at an Any return slot ->
                                    # `return ::tpy::make_any(..);`
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
    "mil.container_default",       # `items(std::vector<T>())` -- the
                                    # empty-container ctor call in a MIL cell
    "mil.span_copy",              # `items(items)` -- std::span field
                                    # bare-copied from a same-typed param
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
    # Unproven member access off a STORAGE Optional[F1-record] field lvalue
    # (`h.opt.x` -> `::tpy::deref_optional_check(h.opt).x`; lowering) -- the
    # optional-lvalue sibling of the deref_check (`T*` receiver) arm.
    "field.opt_check_field_recv",
    # A RECORD-result user-dunder binop (`a // b` -> Meters, `td1 + td2`):
    # the injected/native cpp_template render, admitted at consumers that
    # pin the record rvalue (lowering).
    "binop.record_dunder",
    # ...consumed as a PRINT arg (the record-call row's binop twin; gate).
    "print.record_binop",
    # ...consumed as a FIELD-access receiver (`(a // b).v`; gate).
    "field.binop_recv",
    # A field read off a PROPERTY-GETTER receiver returning a record
    # (`h.mid.x` -- the inner read is a getter call in disguise; gate).
    "field.property_call_recv",
    # ...DISCARDED in statement position (`timedelta(seconds=1) / 0;` --
    # evaluated for its raise; statement lowering).
    "expr_stmt.record_binop",
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
    # A user record's own slice `__getitem__` overload rendered as the plain
    # member call over the BasicSlice initializer
    # (`a.__getitem__(::tpy::BasicSlice{1, 4})` -- gen_call_from_fi's tail).
    "subscript.record_slice_method",
    # A generator-method ctor-rvalue receiver lifted into a named local
    # (`Counter __tmp_N = Counter(..);` + `__tmp_N.each()`) -- the frame
    # captures the receiver by reference, so the temporary must outlive the
    # call (_gen_method_call's is_temporary lift).
    "method.gen_recv_temp",
    # A plain @native member (renamed method / property getter) on a
    # Ptr[record] receiver, deref-check face only
    # (`::tpy::deref_check(s).outer()` -- the first hop of a native field
    # chain).
    "method.ptr_native_member",
    # `Color[name]` enum name lookup -> `::tpy::EnumUtil<E>::from_name(name)`
    # (lowering; a static lookup panicking KeyError on miss).
    "subscript.enum_from_name",
    "subscript.typed_dict",         # d["key"] -> d.key (TypedDict field read)
    "subscript.typed_dict_check",   # total=False read -> ::tpy::typed_dict_field_check(d.key)
    "setitem.typed_dict_field",     # d["key"] = v / OP= v -> the plain field lvalue write
    # `len(recv.field)` -- a container/str/bytes field arg to the builtin len
    # (lowering; `::tpy::__len__(this->xs)`, the field renders as its own
    # THIRFieldAccess inside the shared native-call emit).
    "len.field_recv",
    # A NESTED-CONTAINER element read in a value position (`groups["a"]` off
    # `dict[str, list[Int32]]`): the checked dunder's element lvalue, which
    # lands bare in every value sink because the AST's single element emitter
    # is consumer-blind. Replaced the per-sink `subscript_prechecked` bypasses
    # at len/print, so the gate's receiver checks now apply there too.
    "subscript.container_elem",
    # `for x in self.items:` on an open-T field whose bound is a structural
    # iterable protocol (lowering; the bare member capture + the universal
    # `::tpy::__iter__` loop, resolved through the bound).
    "foreach.open_t_field",
    # `self` read inside an `isinstance(self, Sub)` branch (lowering; the
    # pre-bound cast pointer's deref `(*__self_ptr)`, not `this`).
    "self.poly_narrowed",
    # A non-value tuple element inside a RESUMABLE frame-emplace container
    # literal (lowering; `std::tuple<int32_t, Box>{1, Box(5)}` bare -- the
    # emplaced brace is already storage-typed, so no tuple_to_storage wrap).
    "containerlit.tuple_frame_elem",
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
    "narrow.wrapper_union",         # F6 isinstance on a recursive-alias wrapper
                                    # union subject -> holds/get via `.value`
    "narrow.folded_isinstance",     # F1 isinstance on an already-narrowed
                                    # subject -> `if (true)/(false)` + shadow
                                    # re-extraction from the ORIGINAL union
    "narrow.poly_tuple",            # tuple-form poly isinstance -> the
                                    # no-init dynamic_cast OR-chain
    "narrow.poly_value",            # value-position poly isinstance -> the
                                    # bare null-check chain (no branch)
    "subscript.protocol_recv",      # protocol-typed template-param receiver
                                    # -> the shared checked __getitem__
    "subscript.varargs_recv",       # *args view + range-proven index ->
                                    # args[static_cast<std::size_t>(i)]
    "setitem.ru_scalar",            # scalar/str literal into a wrapper-union
                                    # value slot -> bare token (converting ctor)
    "print.union_narrowed_arg",     # U3-narrowed alias print arg -> the
                                    # union-typed `::tpy::__str__` visitor
    "subscript.ru_narrowed_recv",   # subscript off a narrowed wrapper-union
                                    # receiver -> the fi-fallback bare recv[idx]
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
    "ret.copy_record",              # `return copy(p)` -> `return Point(p);`
    "ret.record_methodcall",        # method-call rvalue at the storage slot
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
    "ret.container_borrow",         # borrow-slot name/field returns bare
    "ret.container_literal",
    "ret.container_call",           # `return make_list(n);` -- bare call source
    "ret.container_comp",           # `return {x for ...}` -- the decl-init
                                    # stmt-expr render at the return slot
    "ret.closure_name",             # `return add;` -- a closure local's bare
                                    # name at a Callable return slot
    "ret.callable_name",            # `return f;` -- a std::function-typed
                                    # param/local returned bare
    # `return pet;` on an isinstance-narrowed ptr-variant union: the
    # extraction alias returned by address (`return &(__pet);`).
    "ret.narrowed_union_addr",
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
    # A list/dict literal at a recursive-union WRAPPER decl slot: the
    # non-generic `AliasRef` form and its generic alias-INSTANCE sibling.
    "decl.ru_wrapper_literal",
    "decl.ru_instance_literal",
    "containerlit.tuple_storage",   # `[(a, P(1)), ...]` -> tuple_to_storage<S>(S{...})
    # The REF-element sibling: the inner is the BORROW tuple
    # (`std::tuple<T*, ..>{&(a), nullptr}`) under the same convert --
    # _gen_tuple_literal's has_ref_elements path.
    "containerlit.tuple_borrow_storage",
    "containerlit.make",            # make_vector / make_ordered_map / _set
    "containerlit.move",            # `std::move(name)` element at last use
    "containerlit.copy_record",     # `copy(p)` element: the shared
                                    # copy-construct row at a record slot
    "containerlit.union_name_lift",  # tracked ptr-variant NAME at a value-
                                    # union slot -> `to_value_variant<..>(a)`
    "containerlit.tparam_elem",     # plain declared NAME at a `T` element
                                    # slot -> bare brace init (`return {x};`)
    "containerlit.tuple_name_storage",  # bare NAME at a non-value tuple
                                    # element slot -> the whole non-move
                                    # tuple_to_storage copy
    "containerlit.union_narrowed_elem",  # narrowed alias at a value-union
                                    # slot -> bare member (`{__a, ..}`)
    # A spanlike coerce over an array-literal inner: the helper wraps the
    # make_array-typed brace init (`as_mut_span(std::array<T, N>{...})`).
    "coerce.span_array_literal",
    # A leaf try/FINALLY in a resumable with no return/break/continue
    # crossing it: renders as the plain sync duplicated-body try, so no
    # finally-frame scaffolding is involved.
    "res.leaf_try_finally",
    # A top-level narrowing `assert isinstance` in a resumable flat BB: the
    # alias is appended after the assert and is BB-local (each resume case
    # re-establishes the stamped fact).
    "res.flat_assert_narrow",
    # A CONTAINER field at the RESUMABLE for-head's iterable position: the
    # bare member read is what begin()/end() are taken off
    # (`(__self.nodes).begin()`). The sync for-head has its own arm.
    "field.container_iterable",
    # A CONTAINER field as the `std::ranges::contains` haystack
    # (`item in self.xs` -> `std::ranges::contains(this->xs, item)`).
    "binop.membership_container_field",
    # A str-family FIELD source at a RESUMABLE str return slot: the bare
    # member read feeds the `<ret_cpp> __tpy_async_ret = <value>;` decl.
    "res.return_str_field",
    # A str-family FIELD at a str YIELD slot: the bare member read is the
    # yielded expression (`return __case_0.name;`).
    "res.yield_str_field",
    # A str-family FIELD read under a view-TARGET coerce (`return self.s` at a
    # StrView slot): the coerce renders its inner bare, so the member read is
    # the emitted form. Declared-type keyed -- a narrowed `str | None` field
    # stays unrouted (its AST render is broken, see BUGS.md).
    "coerce.str_field_view",
    # An open-T result at a marker/qualcall slot (`val_or_cref_t<T>`): the
    # form-neutral slot renders the bare call.
    "method.qualcall.ret_tparam",
    # A bare FIELD read into a ctor REF slot of the field's own declared type
    # (`IntListIter(this->items)` / `Pair<B, A>(p.second, p.first)`): the
    # member read binds the `const T&` slot directly.
    "ctor.field_read_ref_arg",
    # `bytearray()` -- the type-ctor template expands to the plain
    # `std::vector<uint8_t>()` construction. Arg-less only; `bytearray(n)`
    # resolves as a plain call and never reaches this arm.
    "call.type_ctor.bytearray",
    # A `*args` pack at a GENERIC callee's vararg slot -- the same
    # `std::array` temp + `::tpy::varargs<..>` render the plain path uses.
    "call.generic_vararg_pack",
    # A NAME bound to the still-unsubstituted slot type at a generic callee
    # (`first(items)` at `list[T]`): binds the ref template param bare.
    "call.generic_open_slot_name",
    # A `T()` default filling an omitted generic param
    # (`three_params[Int32](10, c=5)` -> `int32_t{}`).
    "call.tparam_default_construct",
    # A conformer NAME into a still-open single structural protocol slot
    # (`drive_implicit(t)` at `Awaitable[T]`): binds the template param bare.
    "call.generic_open_proto_name",
    # A Callable field read into a callable slot (`apply(handler.cb, 10)`):
    # the bare member read, the std::function converting implicitly.
    "call.callable_field_arg",
    # A Callable-VALUE field read at a value position: the bare member read.
    "field.callable_value",
    # An empty container instantiation into an Own[container] ctor slot
    # (the @dataclass default_factory fill): the spelled default ctor.
    "ctor.own_container_instantiation",
    # `copy(src[i])` of an open-T element into an Own[T] ctor slot:
    # the generic copy tail (`T(<element read>)`).
    "ctor.copy_open_elem",
    # A @cpp_template free call returning a by-value F1 record at a STORAGE
    # sink (`make_default[Point]()` -> `Point p = Point{};`).
    "call.template_record_rvalue",
    # A DISCARDED @native record-rvalue call: the bare call statement.
    "call.discard_native_record",
    # A `None` literal into a unit slot (`Own[None]` / bare None-resolved T):
    # the bare `std::monostate{}` value.
    "call.none_unit",
    # A str-slice arg into an `Own[str]` slot: the bare literal / view->owned
    # convert render (the copy+move temp row's temp-free faces).
    "arg.own_str_slot",
    # An @error_return record-rvalue callee at the field-receiver position
    # (composes on the er-unwrap stmt-expr) or the raw statement bind
    # (the try/er `__try_tmp_N` block).
    "call.er_record_rvalue",
    # `into_any` coercion: a scalar / str / bytes / None value wrapped into a
    # `tpy::Any` cell via `make_any` (`x: Any = 42` / `Any(v)`).
    "coerce.into_any",
    # A redundant `copy()` dropped at the into_any coerce -- make_any already
    # copy-constructs into the cell (ptr-variant unions excluded).
    "coerce.into_any_copy_peel",
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
    "isnone.global_slot",            # `g == nullptr` on the raw slot pointer
    "isnone.union_monostate",        # union-binding `is [not] None` ->
                                     # holds_alternative<std::monostate>
    # Value-tuple slots (`tuple[scalar|str, ...]`): the spelled
    # `std::tuple<...>{...}` literal render at returns / decls, and the bare
    # value-tuple name return.
    "ret.tuple_literal",
    "ret.own_storage_tuple",        # Own[tuple[.., record]] literal return ->
                                    # spelled `std::tuple<..>{rvalues}`
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
    "expr.value_tuple_self_typed",  # a slot-less value-tuple literal spelled
                                    # from its own sema type
    "ret.tuple_nested_elem",
    "btuple.literal",               # borrow-slot tuple literal (spelled + lifts)
    "btuple.elem_btuple_subscript",  # element read off a borrow-form tuple:
                                    # already a T*, no address-of
    "btuple.elem_optptr",           # pointer-repr Optional elem slot: None ->
                                    # nullptr, plain lvalue name -> &(name)
    "gentuple.literal",             # generic-slot tuple literal (to_val_or_ptr)
    "btuple.value_to_borrow",       # rvalue elements via the source-tuple helper
    "btuple.value_arg",             # value-tuple literal call arg
    "btuple.decl",                  # sync borrow-tuple local decl (`auto t = ...`)
    "decl.btuple_alias",            # borrow-tuple local re-aliased from a name
    "decl.btuple_elem_alias",       # T& alias of a borrow-tuple param's ptr element
    "decl.btuple_rebind_slot",      # reassigned btuple decl off an owning call:
                                    # optional slot + emplace + tuple_to_pointer
    "decl.btuple_lift",             # reassigned btuple decl off a storage lvalue
    "decl.btuple_literal",          # reassigned btuple decl off a REF-capture literal
    "call.btuple_slot",             # borrow-tuple call result into an `auto` decl
    "ret.btuple_name",              # already-borrow tuple local returned bare
    "ret.consuming_self_field",     # consuming method: `return std::move(this->f);`
    "ret.btuple_literal",           # `return (n, p)` -> std::tuple<..,T*>{n, &(p)}
    "arg.btuple_name",              # already-borrow tuple name passed bare
    "arg.required_protocol_union",  # name at a required multi-protocol
                                    # union slot -> plain value render
    "arg.value_opt_callable",       # whole Optional[Callable] name passed
                                    # bare at a matching value-opt slot
                                    # (admission; render is the bare name)
    "method.optview_whole_arg",     # whole Optional[str/bytes] name passed
                                    # bare at a user-record method's matching
                                    # value-opt view slot (target-less loop)
    "arg.container_field_marker",   # container field read bound bare at a
                                    # marker callee's container ref slot
    "arg.own_tparam_method_rvalue", # Own[T] method rvalue bare at the same
                                    # open Own[T] method slot
    "call.dyn_getattr_builtin",     # 2-arg getattr(obj, name) delegated to
                                    # the dyn-attr read mirror (result-blind
                                    # bare dunder call)
    "arg.bytes_owned_literal",      # bytes literal at an Own[bytes] element
                                    # slot -> the owned literal render
    "arg.own_value_tuple_literal",  # value-tuple literal at an Own[tuple]
                                    # element slot -> spelled value render
    "arg.ptr_addr_of_elem",         # record-element lvalue at a Ptr elem
                                    # slot: the &(...) lift
    "arg.own_tuple_call_rvalue",    # owning call whose result IS the
                                    # Own[tuple] element slot: bare insert
    "arg.own_btuple_literal",       # ref-element tuple literal at an
                                    # Own[tuple[T|None, ..]] element slot ->
                                    # tuple_to_storage_move<S>(borrow tuple
                                    # with per-element moves)
    "arg.own_btuple_call",          # borrow-tuple-returning call at the same
                                    # slot -> non-move tuple_to_storage lift
    "call.btuple_pass",             # borrow-tuple call result at a MATCHING
                                    # borrow-form tuple param -> binds bare
    "method.protocol_self_storage_ret",  # Own[Self] rvalue into the `auto`
                                    # decl slot in a template body
    "method.protocol_own_storage_ret",  # Own[record] rvalue off a protocol
                                    # receiver landing bare at a storage sink
    "method.scalar_tuple_ret",      # scalar-receiver stub's value-tuple
                                    # result at a storage/statement sink
    "arg.native_property_container",  # container-returning property read
                                    # bound bare at a native slot (len)
    "method.span_ret",              # Span-view method result renders bare
    "method.value_opt_view_ret",    # value-opt owned-view method result
                                    # landing bare in a storage decl slot
    "method.ptr_template_span",     # Ptr[T].span(n) template expansion --
                                    # by-value Span result lands bare
    "method.native_ret_cast",       # declared cpp_return_type's
                                    # static_cast wrap over the member call
    "call.super_generic",           # generic super()/unbound-self call --
                                    # this->Base<T>::template m<U>(args)
    "arg.same_tparam_name",         # name bound to the slot's own bare T
                                    # passes bare at a marker call
    "method.typed_dict_get",        # TypedDict kwargs.get -> the value_or /
                                    # make_optional / bare-field composition
    "binop.typed_dict_in",          # TypedDict membership -> the
                                    # .field.has_value() presence check
    "arg.protocol_union_plain",     # ... the lowering arm that renders it
    "call.view_instantiation",       # Span/Array ctor over a bare source
    "call.array_literal_instantiation",  # `Array[T, N]([..])` -> the spelled
                                    # array type over the literal's braces
    "call.native_iter_instantiation",  # `SpanIter(rs)` -> the ctor's own
                                    # pre-substituted {cpp} template
    "method.native_function_form",  # `b.__iter__()` -> the qualified
                                    # native symbol over the receiver
    "call.span_instantiation",      # `Span(p, n)` -> the ctor's own template
                                    # expanded over inline args
    "call.container_literal_instantiation",  # `set([..])` -> the spelled
                                    # container type over the literal's braces
    "arg.native_comprehension",     # comprehension stmt-expr inline at a
                                    # native/template Iterable slot
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
    "decl.move_through_array",      # Array last-use alias: spelled decl + std::move
    # `copy(a)` of a plain F1-record source into an owned record local
    # (`T b = T(a);`, the copy-construct rvalue).
    "decl.copy_record",
    # Storage-call local decl (lowering admission; a container/tuple/union-
    # returning call init -- the bare `T x = f(...);` / plain reassign,
    # rendered by the shared generic decl tail).
    "decl.storage_call",
    # An owning pointer-repr-element tuple call result declared storage-form
    # (`auto t = make_pair(5);` + storage element reads).
    "decl.storage_call_tuple",
    # A same-repr pointer-Optional name-copy decl (`const Point* q = a;`).
    "decl.opt_name_copy",
    # Native record-returning free-call local decl (`f = open(path)` ->
    # `::tpy::TextFile f = ::tpy::builtin_open(path);`) -- a plain-value decl,
    # single-assignment rvalue only.
    "decl.native_record_call",
    # The general rvalue-call storage decl (`Animal parent = cast(Animal, a);`,
    # `std::vector<uint8_t> data = r.read();`): an F1-record / owned container
    # result by value, single-assignment only.
    "decl.rvalue_storage_call",
    # Iterator-object local decl (`it = g()` / `it = obj.gen()` -> `auto it
    # = g();`): a generator/iterator factory result feeding the universal
    # __iter__/__next__ loop; single-assignment only.
    "decl.iterator_object",
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
    # `copy(span)` of a Span NAME -> `std::span<T>(span)` (a view copy).
    "call.copy_span",
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
    # Owned-optional record slot from a storage-optional-returning call
    # (`std::optional<Rc<T>> upgraded = w.upgrade();`); the name registers
    # for the narrowed `(*name)` deref + has_value None-test reads.
    "decl.opt_record_call",
    "decl.opt_value_record",        # Optional[value-record] slot: plain spelled copy
    # A registered owned-optional record local's NARROWED read -- the
    # `(*upgraded)` deref consumed as a receiver / member position.
    "name.opt_record_deref",
    # ...and its WHOLE-optional read (the bare name at a None-test).
    "name.opt_record_whole",
    # Escape-hoist PLAIN-record pointer-local, name-reassigned with a record
    # rvalue init: `T __slot_N = init;` + `T* x = &__slot_N;` (the
    # REBIND_SLOT render minus the rebind slot).
    "decl.record_slot_rvalue",
    # Reassigned container-ELEMENT borrow local's first decl: `[const] T* p =
    # &(::tpy::__getitem__(ps, i));` -- the decl twin of `reseat.subscript_elem`.
    "decl.subscript_elem_addr",
    # NAME-reassigned container-LITERAL pointer-local: `std::vector<T>
    # __slot_N = {..};` + `std::vector<T>* xs = &__slot_N;` (the container
    # flavor of the same render).
    "decl.container_slot_rvalue",
    # HOISTED record pointer-local decl inside a loop/branch: function-top
    # `std::optional<T> __slot_N;` + `T* x = &*(__slot_N = init);`.
    "decl.record_slot_hoisted",
    # Pointer-name copy reseat between two `T*` locals: `saved = p;` (bare,
    # no address-of).
    "reseat.ptr_copy",
    # REF_ALIAS decl off a PLAIN pointer-local source: `Point& alias =
    # (*p);` (the name lowering's deref render at the alias init).
    "decl.alias_ptr_deref_src",
    # Reseat of a slot-hoist Optional local to None: `x = nullptr;`.
    "reseat.opt_none",
    "reseat.opt_inline_rvalue",  # slotless local: in-place plain block slot + later reuse
    "reseat.opt_ptr_copy",       # same-Optional borrow-name source: bare pointer copy
    # Rvalue reseat through the pre-declared rebind slot:
    # `x = &*(__slot_N = <rvalue>);` (THIRAssign's rebind-slot arm).
    "reseat.opt_rvalue",
    # Lvalue reseat of a slotless Optional local: lift a bare record param or an
    # F1-record field source via `x = &(...);`.
    "reseat.opt_lvalue",
    "reseat.opt_field_lift",        # slotless opt local = optional_to_ptr(field)
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
    # A nested def's closure local read as a value (`push_back(add_offset)`):
    # the bare local name the nested def bound, not the module spelling.
    "name.closure_local",
    # A read of a read-only-seeded NATIVE-linkage value global (lowering;
    # the pre-rendered `::symbol` spelling on THIRName.cpp).
    "name.global_native",
    # A read of a read-only-seeded IMPORTED value global (lowering; the
    # pre-rendered `::tpyapp::mod::g` / native_cpp_name spelling on
    # THIRName.cpp -- imported_variable_cpp, shared with the AST render).
    "name.global_imported",
    # A read of a read-only-seeded POINTER-SLOT global (non-value record/
    # container `T* g{};` -- rides the pointer-local arms via lc.pointers:
    # `(*g)` value derefs, `->` receivers, the addr-coerce `&(*g)`, the
    # `T& q = (*g);` alias bind).
    "name.global_slot",
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
    "truthy.global_slot",           # ptr-repr Optional global: `!(g)`
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
    "ifexpr.tuple",                 # tuple result: arms' form propagates
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
    "with.hoist_optional_storage",  # single-bind non-value -> optional<T> name;
    "with.opt_slot_target",         # hoist-predeclared target -> name = __enter__();
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
    "if.hoist_optional_storage",    # single-bind non-value -> optional<T> name;
    "if.hoist_borrow_tuple",        # ptr-repr tuple -> std::tuple<..., T*> name;
    "foreach.hoist_borrow_tuple",   # hoisted tuple loop var -> borrow predecl + lift bind
    "subscript.tuple_elem_recv",    # container tuple elem lvalue under std::get
    "setitem.record_method_rvalue", # d[k] = rc.clone() -- bare method rvalue value
    "btuple.reseat_literal",        # borrow-tuple local = REF-capture literal
    "btuple.reseat_lift",           # borrow-tuple local = tuple_to_pointer(lvalue)
    "if.hoist_ptr_local",           # reassigned/borrow non-value -> T* name;
    "if.hoist_dyn_protocol",        # @dynamic branch decl -> Base* name;
    "reseat.opt_storage",           # plain assign into an OPTIONAL_STORAGE hoist
    "reseat.branch_rvalue",         # lazy-slot rvalue reseat of a branch hoist
    "reseat.storage_name",          # `x = base;` -> `x = &(base);` lvalue lift
    "reseat.subscript_elem",        # `p = xs[i];` -> `p = &(__getitem__(...));`
    "print.hoisted_container_arg",  # ListPrinter((*items)) over a hoisted ptr
    # Raise statements (lowering).
    "raise.ctor",                   # `raise X(args)` -> `throw <cpp>(...)`
    "raise.bare",                   # bare re-raise -> `throw;`
    "raise.expr",                   # `raise <expr>` -> `<expr>.__raise__();`
    # dict/set membership (`needle in c` -> `(c.contains(needle))`, the
    # resolved_contains arm; witnessed at lowering admission and again at
    # lowering -- non-vacuity only needs a nonzero count).
    "binop.membership",
    # user-record membership (`needle in jar` over a record whose
    # `__contains__` is a plain user method) -> the member call
    # `(recv.__contains__(needle))`, gen_call_from_fi's member tail.
    "binop.user_membership",
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
    # A both-literal int binop folded in the target-less BigInt context
    # (`2**63 - 1` -> the folded BigInt-targeted literal render).
    "binop.literal_fold",
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
    # A borrow (is_ref) F1-record target aliasing a storage-form tuple element
    # via the tuple_to_pointer source wrap + unwrap_ref/tuple_elem_ref bind.
    "stmt.tuple_unpack.ref_target",
    # For-head ref unpack over an ITER-PROTO source (`for i, p in
    # enumerate(ps):`): the element is already a borrow-form tuple, so the
    # head binds `auto& __tup_N` (no lift) and ref targets alias via
    # unwrap_ref/tuple_elem_ref (statement lowering).
    "stmt.tuple_unpack.ref_target_iter",
    # Standalone `a, b = p` where `p` is an already-borrow-form tuple PARAM
    # (`tuple[T, ...]` passes as `const std::tuple<T*, ...>&`): the source binds
    # `auto& __tup_N = p` (NAME_REF, no tuple_to_pointer lift) and ref targets
    # alias via unwrap_ref/tuple_elem_ref.
    "stmt.tuple_unpack.ref_param_source",
    "stmt.tuple_unpack.subscript_wrap_source",  # `a0, b0 = pairs[0]` -- the
                                    # whole storage element lifts via
                                    # tuple_to_pointer off __getitem__
    "stmt.tuple_unpack.call_borrow_source",  # `a, b = both(t1, t2)` -- the
                                    # borrow-form call result captures via
                                    # the plain RVALUE bind, no lift
    # A pointer-repr Optional[F1-record] unpack target off a borrow-form tuple
    # param: a plain nullable-pointer local `const T* a = std::get<i>(__tup_N);`
    # (const tracks the source param), registered as a pointer-optional local so
    # its None-test / narrowed reads ride the `T | None` param machinery.
    "stmt.tuple_unpack.opt_ptr_target",
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
    "argtemp.comprehension",        # slot-typed comp ArgTemp at a plain
                                    # container ref slot (accept([x for ..]))
    "arg.borrow_tuple_field",       # storage F3-tuple field wrapped
                                    # tuple_to_pointer at a borrow-tuple slot
    "arg.borrow_tuple_subscript",   # the container-element twin: a checked
                                    # element read through the same wrap
    "arg.btuple_storage_name",      # storage-form tuple LOCAL (loop var /
                                    # storage-bound local) through the wrap
    "subscript.value_tuple_source",  # value-tuple element as an unpack source
    "subscript.borrow_tuple_elem",  # borrow-form tuple element at a
                                    # BORROW_BIND sink (the arg wrap)
    "subscript.record_elem_borrow", # checked F1-record element lvalue at a
                                    # BORROW_BIND sink (record ref-slot arg)
    "arg.record_borrow_call",       # T&-returning call bound inline at a
                                    # record ref slot (bump(find_first(..)))
    "arg.recursive_union_borrow_call",  # the wrapper-slot twin
                                    # (show(v.inner.get()) at `const Value&`)
    "arg.native_protocol_tuple_literal",  # tuple literal at a native
                                    # protocol slot: its own storage type
    "arg.pending_str_slot",         # unresolved view-var str slot admitted
                                    # through the resolver, not the spelling
    "arg.native_protocol_open_field",  # open-T field at an unsubstituted
                                    # protocol slot: the bare member read
    "arg.native_protocol_field",    # bare optional/record field read at a
                                    # native protocol slot (repr_of(this->f))
    "call.native_range_arg",        # range(...) rvalue bare at a native
                                    # builtin's Iterable slot (zip(range..))
    "call.native_iter_call_arg",    # nested combinator / gen-factory rvalue
                                    # at a native builtin's Iterable slot
    "call.inst_iter_arg",           # combinator rvalue in a container
                                    # instantiation (list(map(f, xs)))
    "call.inst_genexpr_arg",        # genexpr rvalue in a container
                                    # instantiation (list(x for ...))
    "if.constexpr_concept",         # protocol-isinstance -> if constexpr
                                    # over the concept test
    "fold.overload_block",          # per-@overload-stub dead-branch fold splice
                                    # (if-chain flatten / folded match arm)
    "fold.overload_bind",           # folded match arm's capture binding
                                    # (`auto`/`auto&` name = subject.field)
    "fn.param_copy",                # reassigned const-ref param -> the mutable
                                    # owned-copy prologue (THIRParamCopy)
    "genexpr.native_iterable",      # genexpr into a native Iterable consumer ->
                                    # the make_generator IIFE (all/any/sum arg)
    "print.optval",                 # un-narrowed value-repr Optional[scalar/str]
                                    # print arg -> bare `::tpy::print_optional_val`
    "print.wrap_arg",               # container / value-tuple / F1-record NAME
                                    # print arg -> its kind-keyed printer wrap
    "print.tuple_record_elem",      # std::get<i>(t) record element at a
                                    # print sink; deref iff borrow-form
    "print.self_arg",             # `print(self)` -> the `(*this)` receiver
                                    # streamed raw via operator<<
    "print.wrap_field_arg",         # container FIELD read print arg -> its
                                    # kind-keyed printer wrap (ListPrinter(m.f))
    "print.bytes_field",            # bytes-family field read print arg -> bare
                                    # `.field` inside a BytesPrinter wrap
    "print.tuple_subscript_arg",    # value-tuple subscript read print arg ->
                                    # TuplePrinter(std::get<N>(t))
    "print.container_slice_arg",    # list/Array/Span slice read print arg ->
                                    # ListPrinter(list_slice/list_stepped_slice)
    "print.walrus_arg",             # container walrus print arg -> the
                                    # kind-keyed wrap over the walrus render
    "print.container_call_arg",     # container-returning CALL print arg -> its
                                    # kind-keyed printer wrap around the call
    "call.inst_slice_arg",          # container-slice rvalue into a list/set/
                                    # dict instantiation (`list(argv[i:])`)
    "call.inst_gen_arg",            # generator-factory rvalue into a list/set/
                                    # dict instantiation (`list(gen())`)
    "call.inst_view_arg",           # dict-view rvalue instantiation arg
                                    # (dict(m.items()) / list(d.keys()))
    "argtemp.optptr_container_literal",  # container literal into a ptr-repr
                                    # Optional[container] slot: typed __tmp_N
                                    # + &(__tmp_N)
    "argtemp.container_call",       # container-returning rvalue call into a
                                    # plain call's container ref param -> the
                                    # hoisted `__tmp_N` ArgTemp
    "field.whole_optional",         # WHOLE value-repr Optional field read into
                                    # an optional sink -> bare member copy
    "field.assign_narrowed_union",  # .field off an assign-narrowed ptr-variant
                                    # union name -> (*std::get<T*>(v)).field
    "decl.no_init_value",           # annotation-only value decl (`x: str`) ->
                                    # the default-construct `std::string x;`
    "call.record_field_arg",        # F1-record FIELD read into a record ref
                                    # slot -> the bare aliasing member read
    "setitem.optval_none",          # None into a value-repr Optional[scalar]
                                    # element -> the nullopt STORAGE store
    "setitem.unit_none",            # `d[k] = None` at a unit value slot
                                    # -> the monostate STORAGE literal
    "ctor.protocol_union_arg",      # record/container NAME into an
                                    # all-protocols union ctor slot -> bare
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
    "match.bind_assign_addr",       # hoisted ptr-local: `name = &(subject);`
    "match.bind_assign_move",       # hoisted opt slot: `name = std::move(subject);`
    "match.subject_rvalue",         # call rvalue subject: owned `auto` dispatch-local
    "match.hoist_ptr_local",        # capture hoist: `T* name;` borrow-only form
    "match.hoist_opt_ptr_local",    # ptr-repr Optional capture hoist: `T* name;`
    "match.hoist_optional_storage",  # capture hoist: `std::optional<T> name;` slot
    "match.if_elif_guarded",        # standalone-if + goto __match_end_N (M3b)
    "match.guard_arm",              # a guarded arm's inner `if (guard)`
    "match.switch_guard_chain",     # in-switch guard chain (grouped entries)
    "match.default_goto",           # all-guarded group -> goto __match_default_N
    "match.switch_union",           # switch (subject.index()) over variant tags
    "match.union_wrapper_value",    # wrapper subject: switch/get over `.value`
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
    "arg.literal_scalar_slot",      # resolved scalar at a Literal[...] slot
    "ctor.own_scalar_peel",         # nested ctor tail: Own[scalar] no-op peel
    "ctor.own_str_literal",         # str literal bare into an Own[str] slot
    "match.literal_facts",          # Literal-subject per-arm fact scope
    "match.field_nested",           # nested class sub-pattern recursion
    "match.field_union_guard",      # holds_alternative<T> union-field guard
    "match.field_guard_as",         # `as` bind of the (extracted) field value
    "match.field_alias",            # __field_{base}_{f} extraction temp
    "match.union_field_cond",       # guarded-union entry with field conds
    "match.or_labels",              # or-pattern -> stacked case labels
    "match.union_or_bind",          # binding or-arm -> one block per alt
    "match.poly_if_elif",           # dynamic_cast if-init chain (P1)
    "match.poly_guarded",           # poly standalone-if + goto end tier
    "match.poly_or_arm",            # or-pattern -> ||-joined null tests
    "match.optional_partition",     # Optional-ptr subject: None/has-value split
    "match.optional_value_dispatch",  # value-repr subject: multi-arm inner tier
    "match.optional_none_arm",      # `case None:` prefix -> the nullptr block
    "match.optional_subject_lift",  # storage-form field source -> optional_to_ptr
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
    "match.hoist_value_frame",      # resumable hook mode: value hoist is a
                                    # frame field, no decl at the match site
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
    "res.branch_btuple_write",      # branch-nested borrow-tuple frame decl
    "res.branch_frame_slot_write",  # branch-nested frame_slot emplace decl
    "res.leaf_try_except",          # except-only leaf try (sync tiers mid-state)
    "res.leaf_match_sync",          # non-suspending leaf match (sync tiers)
    "res.yield_container_borrow",   # container yield of a frame_slot name (*buf)
    "res.frame_unpack",             # frame-target tuple unpack (rvalue source)
    "res.unpack_oneshot",           # await-lift one-shot unpack (auto&& move-out)
    "res.frame_tuple_literal",      # value-tuple literal at a bare frame field
    "res.alias_bind",               # pointer-alias frame bind (= &(<lvalue>)
                                    # or the bare alias-of-alias pointer copy)
    "res.nested_def_member",        # frame nested def -> the marker-line stmt
    "res.return_self_borrow",       # `return self` at a Poll<T*> slot: &(__self)
    "res.return_tuple_literal",     # value-tuple literal at the async return slot
    "res.return_generic_tuple",     # generic tuple literal at the async return slot
    "res.loop_ptr_bind",            # pointer-form loop var admitted (T* reads)
    "res.loop_slot_bind",           # frame_slot loop var admitted ((*x) reads)
    "res.loop_tuple_bind",          # value-tuple holder loop admitted
    "res.loop_btuple_bind",         # proxy-ref borrow-tuple loop admitted
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
    "sgen.tuple_yield",             # tuple yield slot: the resumable tuple
                                    # arm's mirror (borrow/value builders)
    "sgen.yield_copy_record",       # `yield copy(p)`: the shared
                                    # copy-construct row at the __val slot
    "sgen.iterable",                # for-branch iterable render
    "sgen.range_arg",               # for-range bound renders
    # The universal __iter__/__next__ protocol foreach (generator-call /
    # iterator-returning-call / user-iterator-name iterables --
    # _gen_direct_next_loop_with_iter).
    "foreach.iter_proto",
    # A NativeIterable[T]/Spannable[T] protocol PARAM iterable: the AST's
    # NativeIterable peephole (plain begin/end range-for over the deduced
    # template-param lvalue), not the universal loop.
    "foreach.narrowed_proto_src",  # narrowed-alias iterable -> the
                                    # universal __iter__/__next__ loop
    "foreach.native_proto_param",
    # Tuple-unpack head over the universal loop (`for a, b in zip(..)` /
    # a tuple-yield generator call -- the same head decls as the container
    # tuple-unpack, THIRForIterProto instead of begin/end).
    "foreach.tuple_unpack_iter",
    # Class-constant read -> the bare qualified static (lowering;
    # `C::LIMIT`, `::tpyapp::m::Limits::MAX`, `C<int32_t>::X` -- the
    # receiver_eval-None shapes of _class_constant_access_parts).
    "field.class_const",
    # Class-constant read whose UNPROVEN Optional-ptr name receiver keeps its
    # runtime check (`({ ::tpy::deref_check(c); C::LIMIT; })`; lowering).
    "field.class_const_recv_check",
    # Class-constant read whose instance receiver has observable cost --
    # evaluated and discarded (`({ static_cast<void>(make_c()); C::LIMIT; })`;
    # lowering).
    "field.class_const_recv_effect",
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
    # Unbound-self base-class field access -> `this->BaseN::field` (lowering;
    # the base qualifier rides `field_cpp`, the class-name receiver is never
    # lowered). Read and write target alike.
    "field.unbound_self",
    # `.field` through an explicit `Ptr[record]` VALUE receiver -> `p->field`
    # (proven non-null) or `::tpy::deref_check(p).field` (unproven), picked from
    # sema's `ptr_non_null`. Read and write target alike.
    "field.ptr_value",
    # `.field` auto-dereffed through a USER Deref wrapper -> `r.__deref__().x`
    # (N = deref_depth). Bare `.` receiver; read and scalar-write target alike.
    "field.user_deref_chain",
    # A BORROW-returning ptr-repr Optional call result at the
    # `Optional[record]` FIELD-write sink, lifted via `ptr_to_optional`.
    "call.ptr_opt_lift",
    # A tuple LITERAL at a tuple field: the spelled value brace-init, plus
    # the `tuple_to_storage` wrap at an F3 (non-value-element) slot.
    "field_write.tuple_literal",
    "call.field_copy_borrow_ret",   # the T&-returning call admitted at the
                                    # field-write COPY sink (renders bare)
    "setitem.borrow_call_copy",     # the T&-returning call value copies
                                    # into the checked setitem's V param
    "field_write.field_copy",       # `h.p = h2.p;` -- the field-read
                                    # reference source copies bare
    "field_write.subscript_copy",   # `h.p = pts[0];` -- the record-elem
                                    # subscript reference source copies bare
    "field_write.borrow_call_copy",  # `h.p = identity(pt);` -- the T&-
                                     # returning call copies bare on assign
    "field_write.ptr_local_copy",   # `this->r = (*saved);` -- pointer-local
                                    # record source copies through the deref
    # `copy()` sources at a record / Optional[record] FIELD write: the
    # copy-CONSTRUCT rvalue (`field = T(x);`) and the constructor-argument
    # peel (`copy(T(...))` renders as the bare `T(...)` prvalue).
    "field_write.record_copy",
    "field_write.record_copy_ctor",
    "field_write.optrec_copy",
    "field_write.optrec_copy_ctor",
    # A VALUE-repr `Optional[scalar]` field write (lowering; `s.count = 42;`
    # -- the scalar converts implicitly into `std::optional<int32_t>`, so no
    # `ptr_to_optional` lift, which is the POINTER-repr sibling's).
    "field_write.value_opt_scalar",
    # Module-init (`__tpy_init`) statements. `global_slot` is a non-value
    # global's initializing write (`static T __global_slot_N = init;` +
    # `g = &__global_slot_N;`); `import_init` an import's `__tpy_init()`
    # chain (or its comment-only empty render); `final_skip` a `Final`
    # global, whose definition lives at namespace scope.
    "top_level.native_global_skip", # `native_global(..)` decl: emits nothing
    "ret.record_ptr_opt_local",     # `return std::move((*p));`
    "ret.tparam_ptr_local",         # open-T `T*` local -> `return (*p);`
    "top_level.global_opt_passthrough",  # `g = <ptr-opt call>;`
    "call.ptr_opt_passthrough",     # free call at that write
    "method.ptr_opt_passthrough",   # method call at that write
    "top_level.global_no_init",     # annotation-only global: emits nothing
    "top_level.global_slot",
    "top_level.global_slot_reuse",  # `g = &(__global_slot_N = init);`
    "top_level.global_null",        # `g = nullptr;`
    # A BORROW-returning method call at a global slot: the slot points AT
    # the callee-owned storage (`pt = &(points->load(0));`), no slot alloc.
    "top_level.global_addr_call",
    "top_level.global_ptr_copy",    # `g = other;` (pointer-slot source)
    "top_level.import_init",
    "top_level.final_skip",
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
        j = compiler._thir_face_journal
        if j is not None:
            j[face] = j.get(face, 0) + 1
    return True


def begin_witness_journal() -> None:
    """Open the journal for one body's lowering attempt (called from
    `fallback.begin_attempt`, so the two tallies share their boundaries)."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_face_journal = {}


def commit_witnesses() -> None:
    """Close the journal on a body that ROUTED, so witnesses recorded outside
    a lowering attempt -- emit-time ones especially, since `thir/emit.py`
    records faces too -- are never journalled and so can never be rolled back.

    Closing is not what makes the tally correct: `begin_attempt` precedes
    every `fold_attempt` and RESETS the journal, so a rollback already drains
    only its own body (measured -- neutering this call corpus-wide leaves the
    zero-witness list byte-identical). What it buys is that the window has a
    definite end, which is what lets `rollback_witnesses` assert it was
    opened. That assert is the enforcement the 6-site convention would
    otherwise lack.

    THE WINDOW IS ONE FLAT SLOT, NOT A STACK: attempts must not nest. An
    inner commit would close the outer body's window and silently disable its
    rollback. Holds today because every attempt completes before the next
    begins (function and ctor attempts finish in the seeding loop, top-level
    closes before `gen_module_init`, and a resumable attempt is
    begin/lower/close with no other journal open). A lowering that straddled
    emit would break it -- make the journal a stack before allowing that."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_face_journal = None


def rollback_witnesses() -> None:
    """Undo every witness recorded since the journal opened, and close it.

    A body that falls back emits its WHOLE tree through the AST path, so an
    arm that merely ran during the attempt contributed no emitted C++ and is
    not covered by anything. Counting it defeats the detector: that is exactly
    how a dead arm passed the zero-witness check once already. Applies to the
    `own.*` admission rows too -- admission stays the distinguishing site for a
    body that ROUTES, which is the case their semantics were written for."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    assert compiler._thir_face_journal is not None, (
        "fold_attempt with no journal open -- every fallback seam must be "
        "preceded by begin_attempt, or the rolled-back witnesses belong to "
        "whatever ran last instead of to this body")
    w = compiler._thir_face_witnesses
    for face, n in compiler._thir_face_journal.items():
        left = w.get(face, 0) - n
        if left > 0:
            w[face] = left
        else:
            w.pop(face, None)
    compiler._thir_face_journal = None
