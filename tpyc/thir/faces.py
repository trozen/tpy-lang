"""Per-face witness tally; reported by the zero-witness summary at run end.

The committed snapshots pin the C++ each corpus case emits, but say nothing
about a face (a lowering classifier / render) that NO corpus case
reaches -- a latent bug there stays invisible until its first witness
arrives. The test harness folds these counts across cases and xdist workers
and reports registered faces with zero witnesses over the whole corpus run.

Witness semantics differ by face kind (encoded in the registry comment):
lowering faces record at THIR-node construction (the render actually
fired); the `own.*` classifier rows record at lowering admission -- their
render is
the bare arg shared with the pass-through emit, so admission is the only
distinguishing site; `flush.*` record when a flushable
statement position's lowered value actually carries a hoisted arg temp.
The admission-time kind is NOT a small exception: a large minority of the
registry witnesses at a GATE, so "witnessed" there means a row was ADMITTED,
not that its render ran. Read the registry comment before treating a witness
count as render coverage.

CENSUS SCOPE: the harness folds witnesses from every module a case compiles,
its libraries included, so a face only the stdlib reaches still counts. What
it cannot see is a face no case in the corpus reaches at all.

Every kind is journalled per lowering attempt and ROLLED BACK when the body
does not lower (`rollback_witnesses`, driven from reject.py's attempt
boundaries): a rejected body emits nothing, so an arm it merely reached
covers nothing. Without that, an arm witnessing before it
can raise reads as covered when it never lowered -- which is how a dead arm
passed this very check.

The registry is immutable metadata (module-level by design); the mutable
counts live on the active Compiler (`_thir_face_witnesses`), so the helper
is a no-op outside a compilation. Recording is NOT flag-gated: it happens
wherever lowering runs, which is every case of every run. The zero-witness
REPORT is a whole-corpus question, so a `-k`-filtered run names
faces no selected case could reach.
"""

from __future__ import annotations

from ..compilation_context import get_current_compiler

THIR_FACES: frozenset[str] = frozenset({
    # THIRArgTemp arms (lowering; _lower_call_arg / the method-arg row).
    "argtemp.value_union",          # free-call value-union member temp
    "ifexpr.container_comp_arm",    # container ternary with a comp arm:
                                    # rvalue arms, VALUE copy
    "foreach.ifexpr_iterable",      # for-head TERNARY of iterator calls:
                                    # route admission
    "foreach.ifexpr_iterable_lower",  # ... and its dedicated lowering leg
    "argtemp.list_repeat",          # list-repeat rvalue into a container
                                    # ref slot: the is_temporary hoist
    "argtemp.list_repeat_proto",    # ADMISSION of a list-repeat rvalue at
                                    # a STATIC structural-protocol slot;
                                    # the shared structural temp renders it
    "argtemp.cond_eager",           # audited NON-MOVABLE temp in a
                                    # conditional operand: deferral is
                                    # impossible, so the eager statement
                                    # hoist stands (sema warns)
    "argtemp.cond_defer_audited",   # audited movable temp in a
                                    # conditional operand: the emit's
                                    # region banks it
    "argtemp.value_union_method",   # method-call value-union member temp
    "argtemp.recursive_union_literal",  # list/dict literal into a recursive-
                                    # union wrapper slot (json.dumps([...]))
    "argtemp.ru_wrapper_literal",   # scalar/str literal into a
                                    # recursive-union wrapper slot
    "argtemp.ru_wrapper_member",    # member-typed NAME into a wrapper slot
    "argtemp.ru_wrapper_call",      # Own[genrec]-returning free call into
                                    # the same-wrapper slot -> prvalue temp
    "call.genrec_own_ret",          # the Own[genrec] by-value return landing
                                    # bare at a STORAGE sink
    "containerlit.genrec_own_elem", # container literal at an Own[genrec]
                                    # element slot -> the ru-instance render
    "mil.generic_record_move",      # Own-param move into a generic-record
                                    # field _f1_record rejects (Box[Tree[T]])
    "argtemp.ru_wrapper_ctor",      # member-CTOR rvalue into a wrapper slot
                                    # (`Tree __tmp_N = std::move(b);`)
    "argtemp.record_rvalue",        # record-ctor rvalue into a ref slot
    # Protocol-slot arg wrap: the @dynamic Adapter / RefAdapter / concrete
    # materialization, and the structural slot's `auto __tmp_N` rvalue temp.
    "argtemp.protocol",
    "argtemp.marker_protocol_record",  # record rvalue at a structural
                                    # protocol slot on a qualcall: `auto` temp
    "argtemp.iter_proto",           # gen-factory / iter() / dict-view rvalue
                                    # at a structural slot -> un-spelled
                                    # `auto __tmp_N = <rvalue>;` temp
    "argtemp.marker_protocol_literal",  # container literal at a PLAIN module
                                    # callee's structural slot -> the qualcall
                                    # loop's `auto __tmp_N = <self-spelled>` hoist
    "argtemp.genfac_ref_slot",          # gen-factory rvalue at a generator
                                        # callee's ref iterable slot ->
                                        # `auto __tmp_N = count();`
    "argtemp.marker_protocol_range",    # range rvalue at the same slot ->
                                    # `auto __tmp_N = ::tpy::Range<...>(..);`
    "arg.deref_coerce_inline",      # Ptr[T] deref coercion at a record slot
                                    # -> inline `::tpy::deref_check(p)`
    "decl.deref_coerce_alias",      # the same coercion at a borrow local ->
                                    # `Point& p2 = ::tpy::deref_check(ptr);`
    "decl.deref_coerce_addr",       # ... reassigned, so pointer-bound ->
                                    # `Point* copy = &(deref_check(ptr));`
    "reseat.deref_coerce",          # ... and its reseat -> `copy =
                                    # &(::tpy::deref_check(ptr2));`
    "move.opt_own_last_use",        # record name moved bare into an
                                    # Optional[Own[T]] slot (converting ctor)
    "move.own_opt_last_use",        # ... and the reverse-nesting twin: the
                                    # Own[record|None] slot (optional<P>&&)
    "arg.opt_own_record_rvalue",    # ctor/by-value call rvalue at the
                                    # Optional[Own[record]] value-repr slot
    "arg.opt_string_literal",       # str literal bare at the
                                    # Optional[String] value-repr slot
    "arg.own_opt_call_pass",        # Own[P|None]-returning call rvalue binds
                                    # the optional<P>&& slot bare
    "field.opt_record_recv",        # narrowed owned-optional record name
                                    # receiver derefs -- (*r).field
    "field.slice_recv",             # slice-object name: index.start bare
    "field.subscript_recv",         # subscript receiver: std::get<0>(t).f
    "field.storage_opt_recv",       # narrowed storage-opt local receiver
                                    # derefs at the access -- (*item).field
    "arg.storage_opt_whole",        # narrowed storage-opt local at a
                                    # protocol slot passes the whole optional
    "field.opt_check_storage_name", # unproven access off a storage-opt name
                                    # -- deref_optional_check(item).field
    "call.storage_opt_ret",         # Own-optional call result lands bare
                                    # in its storage optional decl slot
    "call.view_inner_opt_ret",      # VIEW-inner value-optional call result
                                    # at a storage sink: bare
    "move.opt_own_ptr_lift",        # ptr-repr Optional name lifts owning
                                    # storage via ptr_to_optional_move
    "arg.native_protocol_value",    # scalar/char/str value at a native
                                    # protocol slot -> bare render (__hash__)
    "arg.native_protocol_open_call",  # open-T CALL rvalue at the same slot
                                    # -> bare (hash(self.get()) in a [T] body)
    "arg.native_union_name",        # union-typed name at a native protocol
                                    # slot -> bare (repr(a) over a variant)
    "arg.optview_identity_coerce",  # Optional view<->str identity coerce over
                                    # a call rvalue at a plain ARG slot ->
                                    # bare pass-through (same C++ repr)
    "arg.readonly_empty_container", # empty [] / list() at a readonly slot
                                    # -> inline typed rvalue (const-ref bind)
    "arg.ru_wrapper_elem_literal",  # scalar literal at a wrapper-union
                                    # element slot (`__arr.push_back(4)`)
    "arg.ru_wrapper_borrow_call",   # borrow-returning wrapper call binds the
                                    # same-wrapper slot bare
    "arg.ru_wrapper_value_call",    # value-returning (already_union) wrapper
                                    # call renders bare at the same slot
    "arg.ru_wrapper_field",         # same-wrapper field read binds the slot bare
    "arg.ru_wrapper_narrowed",      # F6-narrowed member alias passed bare
                                    # into a same-wrapper arg slot
    "expr.lambda_void_print",       # void print-body lambda -> the
                                    # statement-body closure { cout << ...; }
    "argtemp.deref_coerce",         # wrapper `__deref__()` coercion -> the
                                    # slot-typed VALUE copy temp
    "argtemp.ctor_mut_rvalue",      # record rvalue into a MUTATED ctor slot
    "ctor.const_rvalue_arg",        # record rvalue inline into a const ctor slot
    "argtemp.own_copy",             # Own-slot copy+move `__tmp_N` temp
    "argtemp.own_borrow_call",      # its BORROW-returning-call source: the
                                    # `auto __tmp_N = <call>;` + move hoist
    "argtemp.own_proto_container",  # still-live container name at an
                                    # Own[protocol] slot: auto copy temp + move
    "argtemp.own_str",              # Own[str]-slot copy temp: the owned type
                                    # declared with brace init (`std::string
                                    # __tmp_N{<arg>};` -- the view->owned
                                    # conversion), field / coerced-field args
    "argtemp.container_literal",    # list literal into a free-call container
                                    # ref slot -> hoisted `__tmp_N` temp
    "argtemp.generic_container_literal",  # the same hoist at a GENERIC
                                    # callee's substituted container slot
    "argtemp.gen_factory",          # temporary at a generator/coro factory
                                    # METHOD's ref / readonly-ref slot ->
                                    # named scope-local the frame borrows
    "argtemp.frame_temp",           # the uniform rule beside it: ANY temporary
                                    # argument of a generator/coro factory,
                                    # hoisted so the frame never receives one
    "expr.walrus_scalar",           # value-scalar walrus `(n = v)` + named
                                    # pre-decl on the sink's named row
    "expr.walrus_opt_ptr",          # ptr-Optional walrus target: `T* n =
                                    # nullptr;` + borrow-lifted assign
                                    # (optional_to_ptr / nullptr / bare ptr)
    "expr.walrus_btuple_emplace",   # hoisted borrow-tuple walrus with an
                                    # owning-call value: emplace + name tail
    "if.deref_view_narrow",         # isinstance through a Deref wrapper:
                                    # if-init cast of the payload pointer
    "method.deref_view_narrowed",   # branch member call reads (*__b_ptr)
    "expr.walrus_ptr_alias",        # borrow-alias pointer walrus target:
                                    # `[const ]T* n = nullptr;` +
                                    # `(n = &(v), *n)` (bare for a
                                    # pointer-name source)
    "walrus.alias_field_src",       # ... off a record/container FIELD
                                    # source: `(q = &(h.inner), *q)`
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
    "expr.walrus_btuple_mixed_call",  # ... off a MIXED own+borrow tuple
                                    # CALL: binds directly, no lift
    "assign.btuple_elem_field",     # scalar field write through a borrow-
                                    # tuple element (std::get<N>(t)->f = v)
    "assign.value_opt_target",      # registered value-opt NAME target: a
                                    # whole-binding write, bare on both
                                    # paths (the raw-assign macro shape)
    "assign.ptr_none",              # `p = None` at a Ptr[T] slot, raw-
                                    # assign flavor of decl.ptr_none
                                    # (`head = nullptr;`)
    "expr.walrus_btuple_slot",      # reassigned borrow-tuple walrus: owning
                                    # __slot_N.emplace + tuple_to_pointer +
                                    # bare-name tail
    "field.walrus_subscript_recv",  # scalar field off a record-elem
                                    # subscript over a reassigned btuple
                                    # walrus (std::get<N>((t = ..))->f)
    "binop.value_select",           # value-position and/or: the once-
                                    # evaluated-LHS ternary
    "binop.container_select",       # container and/or over lvalue
                                    # operands: the __len__-truthy
                                    # ternary aliasing the chosen side
    "binop.select_bool_dunder",     # __bool__-record select: the
                                    # ::tpy::__bool__ truthy flavor
    "binop.protocol_raw",           # protocol-operand arith in a template
                                    # body: the raw `(a + b)` operator
    "binop.opt_view_narrowed",      # narrowed registered Optional[str] /
                                    # Optional[bytes] binding at its
                                    # family's concat operand: the VIEW
                                    # arm's `(*t)` deref
    "binop.char_concat",            # char-typed str-concat operand: the
                                    # resolved overload's char_to_str
                                    # operand wrapper
    "binop.scalar_raw",             # resolver-less scalar binop (post-sema
                                    # macro synthesis): the raw C++
                                    # operator render
    "binop.tuple_ptr_compare",      # pointer-repr tuple-literal compare
                                    # pair: the deref-aware tuple_eq /
                                    # tuple_lt helper composition
    "binop.tuple_ptr_needle",       # pointer-repr tuple-literal membership
                                    # needle: the tuple_to_storage lift
                                    # over the borrow-form literal
    "subscript.union_elem_tuple",   # `pair[1]` on `tuple[A | B, int32]`:
                                    # the sibling VALUE slot reads bare
    "call.union_elem_tuple_arg",    # ... and the whole tuple at a matching
                                    # param slot (name / borrow literal)
    "binop.bytearray_result",       # `ba + b"cd"` / `ba * 2`: a bytearray
                                    # RESULT takes the bytes_concat /
                                    # bytes_repeat arms, owned literal slot
    "binop.bytearray_operand",      # ... and a bytearray OPERAND reading
                                    # bare into either helper
    "binop.list_concat",            # list + list -> ::tpy::list_concat;
                                    # a literal operand takes the
                                    # typed-brace prefix
    "binop.comprehension_operand",  # ... and a COMPREHENSION operand of the
                                    # same helper: the inline stmt-expr,
                                    # target-typed by its own container
    "ifexpr.container",             # container ternary the reference arm
                                    # does not claim: the bare form-blind
                                    # arm render off the generic tail
    "ifexpr.isin_narrow",           # isinstance-condition ternary: holds
                                    # test + per-arm inline get (else =
                                    # the 2-member complement)
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
    # `*args` call-site pack faces (THIRVarargPack lowering).
    "vararg.empty",                 # `::tpy::varargs<E>()`
    "vararg.pack_value",            # value-element std::array<E, N> temp
    "vararg.pack_ref",              # ref-element std::array<E*, N> temp
    "vararg.star_direct",           # `*span` forwarded direct
    "vararg.star_span",             # `*container` borrowed span
    # The temp-free last-use move (lowering).
    "move.own_last_use",            # `f(std::move(name))`
    "move.own_tuple",               # OWN-element tuple name moved whole
                                    # at the matching rvalue tuple slot
    "move.own_proto_container",     # container conformer name moved into an
                                    # Own[protocol] slot (std::move(nums))
    "move.wrapper_union_elem",      # movable wrapper-union name moved into a
                                    # same-wrapper container element slot
    # Pointer-repr Optional[record] slot faces (lowering).
    "optptr.none",                  # `nullptr`
    "optptr.ctor_rvalue",           # `&(__tmp_N)` addr-of arg temp
    "optptr.adapter_temp",          # structural conformer Adapter/RefAdapter temp
    "optptr.ptr_slot_lift",         # storage-Optional field at a Ptr[T]
                                    # slot -> optional_to_ptr(h.opt)
    "optptr.lift",                  # `::tpy::optional_to_ptr(...)`
    "optptr.pass",                  # already-`T*` binding passes bare
    "optptr.name",                  # `&(name)`
    "optptr.subscript",             # `&(<lvalue record subscript>)`
    "optptr.call_pass",             # borrow-returning call passes bare
    "argtemp.protocol_union_literal",  # container literal at a nullable
                                    # protocol ctor slot: typed temp + addr
    "argtemp.protocol_union_iter",  # dict-view / gen-factory rvalue at the
                                    # same slot: the same typed temp + addr
    # Value-repr Optional slot None arg (lowering): the value-optional twin
    # of `optptr.none` -- `f(std::nullopt)`.
    "call.none_value_opt",
    # Value-repr Optional slot member-typed arg (lowering admission): the
    # generic tail render, the optional's converting ctor absorbing the bare
    # member -- `f(5)`, `f("hi")`, `f(Color.Red)`.
    "call.optval_member",
    # A value-opt-returning CALL rvalue at the same value-opt slot -> the
    # by-value optional prvalue binds bare.
    "call.optval_ret_pass",
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
    "er.bind_alias",                # aliasing result into a hoisted ptr:
                                    # `v = &(::tpy::unwrap_ref(*tmp));`
    "er.alias_first_decl",          # first-decl alias bind: `T* v;` predecl
                                    # + the alias assign (non-const slice)
    "er.bind_slot_wrapper_union",   # first-decl predecl slot spelled as a
                                    # recursive-alias wrapper struct
    "ret.record_ref_call_storage",  # ref-returning call at the Own[record]
                                    # STORAGE slot: bare render, slot copies
    "er.field_target_bind",         # er-assign FIELD target gate: the bind
                                    # line assigns into the rendered lvalue
    "er.bind_field_target",         # emit: `this->p = unwrap_ref_move(*tmp);`
    "call.field_recv_borrow_ret",   # T&-record call under a member read:
                                    # `f(x).n` composes bare, transient
    "call.inst_iter_proto_arg",     # iterator-protocol call rvalue at an
                                    # instantiation arg (`list(iter(ws))`)
    "containerlit.tuple_storage_bare",  # no-pointer-repr-elem tuple slot:
                                    # bare brace, members wrap themselves
    "method.no_fi_member",          # post-sema macro member call, no fi:
                                    # plain `.` member tail (`c.bump()`)
    "foreach.own_iter_name",        # OwnIter-typed NAME iterable: lvalue
                                    # capture + consuming auto&& elem
    "foreach.proto_own_elem_val",   # protocol-param loop, Own[value-T] elem:
                                    # typed copy bind + movable seed
    "foreach.proto_own_elem_ref",   # protocol-param loop, Own[ref-T] elem:
                                    # auto&& bind + movable seed
    "foreach.own_proto_param",      # Own[protocol]-typed param iterable:
                                    # unwrapped into the universal loop
    "foreach.narrowed_value_opt_view",  # proven-narrowed str/bytes|None NAME:
                                    # begin/end loop over `(*b)` deref capture
    "comp.narrowed_value_opt_view",  # ... and its comprehension-source twin
    "comp.global_shadow",           # comp var shadows a pointer-slot global:
                                    # scoped pointer scrub for the walk
    "top_level.global_slot_proto",  # structural-protocol global: static auto
                                    # slot + addr assign / slot-reuse rebind
    "decl.own_copy_iter_slot",      # OwnIter/CopyIter decl slot: `auto`
                                    # spelling, the init render carries type
    "er.discard",                   # expr-stmt `__try_tmp_N` block
    "er.unwrap",                    # `({ ... unwrap_ref_move(*__er_N); })`
    # No corpus witness (every reaching shape needs a borrow-returning
    # fallible callee no committed case has), so the whole-corpus zero-witness
    # census lists it until such a case lands.
    "er.unwrap_ptr",                # `(*({ ... &unwrap_ref(*__er_N); }))`
    # The METHOD-call flavor of the unwrap admission (lowering; the free-call
    # arm's `_er_wrap` mirror -- same THIRErrorReturnUnwrap render).
    "method.er_expr_unwrap",
    "er.try_return",                # the goto-dispatch try emit
    "er.try_binding",               # `std::optional<E> __err_opt_N;` capture
    "er.return_passthrough",        # `return <raw expected call>;` (lowering)
    "try.return_tier",              # a return-tier try lowered (admission)
    # Pointer-variant union-slot lifts (lowering).
    "unionlift.none",               # `pv{std::monostate{}}`
    "unionlift.const_wrap",         # `as_const()` / `to_const_ptr_variant`
    "unionlift.member",             # `pv{&(name)}`
    "unionlift.ctor_temp",          # ctor rvalue temp + `pv{&__tmp_N}`
    "unionlift.bytes_literal_temp", # owned-bytes literal temp + `pv{&__tmp_N}`
    "unionlift.dict_literal_temp",  # dict-literal typed temp + `pv{&__tmp_N}`
    "call.own_tuple_pass",          # owned-movable tuple call rvalue bare at
                                    # the matching && slot
    "binop.mixed_sign_cmp",         # mixed-sign fixed-int compare via
                                    # ::std::cmp_* (target-less slice)
    "binop.tuple_field_compare",    # ptr-repr tuple FIELD pair via
                                    # tuple_eq/tuple_lt over bare members
    "arg.own_opt_container_move",   # ptr-Optional[container] local at an
                                    # Own[Optional[..]] ctor slot: the
                                    # inline move materialization
    "arg.own_bytes_slot",           # view-form bytes at Own[bytes] elem slot
                                    # -> `::tpy::Bytes(x)`
    "call.isinstance_static_value", # tparam isinstance trait disjunction at
                                    # a value position
    "call.isinstance_union_value",  # union-subject holds_alternative chain
                                    # at a value position (no alias)
    # Own-cascade bare rows + the readonly ctor tail (lowering admission).
    "own.scalar_rvalue",            # rvalue scalar into Own[scalar]
    "own.record_rvalue",            # record rvalue call into Own[record]
    "own.native_record_rvalue",     # ... its @native Own-returning residue
    "own.opt_ptr_name_rebuild",     # ptr-repr Optional name into the
                                    # same slot: null-safe move rebuild
    "own.copy_construct",           # copy(x) into an Own[T] slot: T(x)
    "method.protocol_discard",      # discarded protocol-method result in
                                    # statement position -- bare call
    "method.record_discard",        # discarded F1-record method result at
                                    # stmt position: the bare call
    "method.container_discard",     # discarded container method result: same bare call
    "method.union_discard",         # discarded Own[union] method result:
                                    # same bare call
    "ret.own_wrapper_none",         # `return None` at Own[wrapper-union] ->
                                    # `std::monostate{}`
    "ret.own_wrapper_literal",      # container literal at Own[wrapper-union]
                                    # -> the ru spelled render
    "ret.own_wrapper_member",       # scalar literal / member-container name
                                    # at Own[wrapper-union] -> bare
    "ret.own_wrapper_call",         # `return loads(s)` -- a call already
                                    # producing the slot's wrapper, bare
    "ret.wrapper_borrow",           # wrapper borrow return (`Expr&`): bare
                                    # param name / pointer deref source
    "call.wrapper_borrow_ret",      # wrapper-borrow-returning call composes
                                    # bare (`count(passthru(tree))`)
    "call.own_tuple_storage_ret",   # Own[tuple]-declared callee at the
                                    # owning frame-slot emplace sink
    "call.open_value_tuple_ret",    # `tuple[T, int32]` call result bare at
                                    # the storage sink (open element, no lift)
    "call.union_elem_tuple_ret",
    # A REFERENCE-element tuple call result at the tuple-source sink
    # (`std::tuple<Tree<int32_t>&, int32_t>`): bare into the capture.
    "call.wrapper_ref_tuple_ret",    # value tuple with a value-union element
                                    # lands bare at the tuple-source sink
    "call.wrapper_value_ret",       # Own[wrapper]-returning call renders bare
                                    # in a VALUE position (`count(build())`)
    "method.qualcall.record_discard",  # discarded record-family qualcall result
                                       # (asyncio.create_task(...);): the bare call
    "method.qualcall.record_storage",  # record-family qualcall rvalue at a
                                       # storage sink: bare call into the slot
    "method.qualcall.storage_opt_ret",  # Own[record]|None qualcall result at a
                                       # storage sink: bare into optional<T>
    "method.container_iterable",    # container method result as a for-head iterable
    "method.qualcall.container_iterable",  # marker-call container as a for-head iterable
    # marker-call owned-tuple result at an owning Own[tuple] arg slot
    "method.qualcall.own_tuple_slot",
    "method.qualcall.union_discard",  # discarded wrapper-union qualcall
                                    # result: the bare call statement
    "method.qualcall.container_discard",  # discarded marker-call container result
    "method.consuming_move",        # consuming method: std::move(name) receiver wrap
    "call.native_record_arg",       # F1-record call rvalue bare into a native slot
    "call.value_record_arg",        # record rvalue bare into a by-value record slot
    "call.value_opt_record_arg",    # ValueType-record ctor rvalue inline
                                    # at a value-opt record slot
    "call.value_record_name_arg",   # ValueType-record NAME bare at a
                                    # by-value same-record slot
    "call.float_str_fold",          # float("nan"/"inf") -> spelled numeric-limits constant
    "print.record_call",            # F1-record call rvalue streams raw via operator<<
    "print.protocol_call",          # protocol-result call (`print(iter(s))`) streams raw
    "print.protocol_name",          # protocol-typed name streams raw
    "own.union_call_pass",          # same-union Own[A|B]-returning call
                                    # rvalue bare at an Own[union] slot
    "arg.own_union_storage_name",   # Own[union] storage-variant NAME into
                                    # a ptr-variant slot: to_ptr_variant
    "own.union_ctor",               # record-ctor rvalue into Own[union]
    "own.readonly_ctor",            # record-ctor rvalue into readonly slot (sync callee)
    # Self receiver / ctor-call renders (lowering).
    "self.this",                    # `self` name read -> `this`
    "call.self_method",             # `self.helper()` -> `this->helper()`
    "call.imported",                # cross-module callee -> pre-rendered
                                    # `::tpyapp::mod::f` (callee_cpp)
    "call.same_module",             # same-module free callee -> the absolute
                                    # `::tpyapp::mod::f` spelling (ADL-safe)
    "call.literal_mangled",         # same-module literal-specialized callee ->
                                    # the mangling inside that spelling
                                    # (`::tpyapp::mod::f__lit_r__w`)
    "fold.overload_live_chain",     # partially-folded per-stub if-chain ->
                                    # trimmed live `if / else if` render
    "method.literal_mangled",       # literal-overloaded member call -> the
                                    # mangled member spelling (`get__lit_age`)
    "ret.union_owned_str_field",    # owned-str FIELD read at a union return
                                    # -> bare render (`return this->name;`)
    "call.native_free",             # C++ @native free callee -> `::native(args)`
    "call.native_own_scalar_lvalue",  # value-scalar NAME into an Own[..] slot
                                      # of a native/template callee: the bare
                                      # lvalue (inline_template Own arm)
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
    "call.inst_field_arg",          # container FIELD read at the same
                                    # position: the bare member render
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
    "call.generic_rvalue_slot",     # the same temporary where the
                                    # INSTANTIATION is value-typed (the slot
                                    # is `const T&`) -> the rvalue inline
    "ctor.instantiation",           # record-ctor instantiation form
                                    # (`Cell[int32]()` / `Poll[T]()`) ->
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
                                    # (`uint32.trunc(i)`) -> template expansion
    "call.macro_expansion",         # `@call_macro`/getattr/hasattr call ->
                                    # its sema-synthesized replacement expr
    "call.fstr_expansion",          # `@inline` METHOD call -> the substituted
                                    # body expression, rendered in place
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
    # The same template expansion on a plain user-record receiver: an
    # explicitly spelled dunder (`self.__eq__(other)` -> `((*this)) == (other)`)
    # rendered through the operator template sema stamps on every user dunder.
    "method.record_template",
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
    # Tuple-element F1-RECORD subscript receiver (`t[0].get()` -- std::get<N>
    # yields a `T*` off a borrow-form tuple param, `->`; a value element off
    # a storage tuple local, `.` -- the arrow keyed on
    # `_subscript_yields_borrow_ptr`, same as a field read over the element).
    "method.recv.tuple_record_elem",
    # Tuple-element STR subscript receiver (`kv[0].lower()`): std::get<N>
    # feeds the view family's receiver slot positionally.
    "method.recv.tuple_str_elem",
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
    # field's record spells the same way (`_f1_record`: same-module,
    # cross-module, @native, and concrete-arg generic records all qualify), so
    # native / generic field receivers ride the same face as a plain one.
    "method.recv.record_field",
    # Ptr[T]-VALUE field receiver (`self.ptr.__deref__()` ->
    # `::tpy::deref_check(this->ptr)`): the member read composes the
    # Ptr/@cpp_template family like a Ptr NAME receiver.
    "method.recv.ptr_field",
    # Field-CHAIN method receiver (`h.c.item.add(x)` -- every parent link a
    # plain-value F1-record member, innermost link an admitted binding; the
    # chained THIRFieldAccess renders `.field` links like the value-read arm).
    "method.recv.field_chain",
    "method.recv.free_call",        # `make(3).get()` -- a plain F1-record
                                    # free-call result receiver, `.` access
    "method.recv.select_str",       # `(t := "ab").upper()` / `(s if c else t).upper()` -- a
                                    # str/bytes-VALUE ternary or walrus receiver, bare render
    "method.recv.select_record",    # `(a if c else b).area()` -- a plain F1-record ternary /
                                    # walrus receiver, `.` access
    "method.recv.binop",            # `(dt + td).isoformat()` -- a record-
                                    # result dunder-binop receiver (gate)
    "method.recv.binop_str",        # `(a + b).upper()` -- the str/bytes-VALUE
                                    # binop receiver, substituted bare
    "method.recv.str_literal",      # `"a,b,c".split(",")` -- a str-literal
                                    # receiver rendered bare into the resolved
                                    # builtin-method template
    "method.recv.bytes_literal",    # the bytes twin -- the OWNED literal
                                    # receiver substituted into the template
    "method.recv.fstring",          # `f"<p>{n}</p>".encode()` -- an f-string
                                    # receiver, the std::format rvalue
                                    # substituted into the method template
    "method.record_call_rvalue_arg",   # `a.add(mk(x))` -- a record-returning
                                    # free call bound inline by a const-ref
                                    # method slot
    "method.record_method_rvalue_arg",  # `a.add(b.muls(x))` -- the method-call
                                    # source of that same rvalue
    "method.protocol_field_recv",   # protocol method over a one-level field
                                    # receiver (this->factory.make())
    "arg.native_module_var",        # module-variable deref read pinned at a
                                    # native slot (len(os.environ))
    "method.recv.view_field",       # str/bytes field receiver routing the
                                    # view family over the member read
    "method.recv.dyn_view_field",   # str/bytes dyn-getattr receiver: the
                                    # synthesized __getattr__ call composes
                                    # under the view family
    # A CONTAINER-valued property receiver: the borrow-returning
    # getter call is the receiver lvalue (`c.items().push_back(4)`).
    "method.recv.container_property",
    # The protocol-isinstance ASSERT: the concept spelling under
    # THIRAssert's negated-if wrap (the F5 arm's assert flavor).
    "assert.protocol_concept",
    # A same-element-type whole-optional element read at a value-opt
    # setitem slot passes bare (`items[i] = items[0]`).
    "setitem.optval_elem_copy",
    # A move-source same-type container NAME at a nested-container
    # element store moves in whole (`__setitem__(g, "a", std::move(a))`).
    "setitem.container_move",
    # A by-value CALL rvalue at a nested-container element store forwards
    # bare (`__setitem__(d, "k", make())`), like the record element arm.
    "setitem.container_rvalue",
    # A ptr-repr Optional[F1-record] name at a native protocol slot:
    # bare T* un-narrowed, the (*name) deref when proven.
    "arg.native_protocol_optptr",
    # A None literal at a value-opt slot of a NESTED ctor arg
    # (`describe(Dog(None))` -> `std::nullopt`, temp-free).
    "ctor.nested_none_value_opt",
    # A ternary of borrow-returning calls at a pointer reseat
    # (`b = &(((flag) ? (g.itself()) : (h.itself())));`).
    "reseat.borrow_call_ternary",
    # A borrow-form Own-element tuple name at the && slot lifts via
    # the F3 tuple_to_storage (the warned copy).
    "arg.own_tuple_borrow_lift",
    "arg.own_tuple_decay_copy",     # still-live storage Own-tuple name at the
                                    # && slot: `sink(auto(p))`
    "arg.open_value_tuple_name",    # bare NAME at a native `tuple[T, int32]`
                                    # slot -- no lift, the open tuple has none
    "method.recv.bytes_method",     # `srv.recv(32).decode()` -- a bytes-VALUE
                                    # method-call result feeding the outer
                                    # bytes method's receiver slot
    "method.recv.scalar_call",      # scalar-value call result receiver
                                    # (`int(0).bit_length()`)
    "method.recv.str_method",       # `s.strip().lower()` -- a str-VALUE
                                    # method-call/free-call receiver, the inner
                                    # str method's bare nested-call render
    "ctor.call",                    # THIRCtorCall bare ctor expansion
    "ctor.native",                  # native-record (builtin exception) ctor: `::tpy::OSError(...)`
    "ctor.native_plain",            # plain @native record ctor: `::Vec2(...)` / @native_c `::Point{...}`
    "ctor.ptr_null",                # `Ptr[T]()` -> `static_cast<T*>(nullptr)`
    "ctor.container_empty_instantiation",  # zero-arg `Array[int32, 8]()` etc.
                                    # at the ctor path -> `type_cpp()`
    "ctor.inherited_instantiation", # instantiation spelling over an
                                    # inherited param-ful __init__
                                    # (`TypedM[int32](7)`)
    "ctor.cross_module",            # imported-record ctor: the qualified
                                    # `::ns::Name(args)` spelling
    "ctor.str_arg",                 # str-slice arg into a str-family ctor slot
    "ctor.lambda_arg",              # routable lambda into a Callable ctor slot
                                    # -> the inline closure, temp-free
    "method.callable_field",        # callable-field invocation h.cb(3) ->
                                    # the bare member call (std::function)
    "method.opt_callable_field",    # Optional[Callable] field invocation ->
                                    # the `.value()` unwrap member call
    "cfield.container_arg",         # container FIELD arg to a callable-field
                                    # call reads bare into the `T&` param
    "ctor.own_arg",                 # Own-slot ctor arg via the shared cascade
                                    # rows (last-use move / copy+move temp)
    "ctor.container_literal_arg",   # list literal into a ctor's list slot:
                                    # the bare brace-init render in place
    "ctor.value_opt_pass_arg",
    "ctor.opt_own_container_name",  # same-typed container name at an
                                    # Optional[Own[container]] ctor slot      # whole value-opt name into the same
                                    # Optional ctor slot (bare copy)
    "ctor.omit_defaults",           # ctor call omitting trailing default args
                                    # (defaults ride the C++ ctor signature)
    "call.omit_defaults",           # free/method/qualified call omitting
                                    # trailing default args (defaults ride the
                                    # emitted C++ signature)
    # Ctor MIL view-family field inits (lowering; the per-family renders --
    # bare str/StrView source vs the bytes view->owned `Bytes(x)` convert).
    "mil.str_field",                # str/StrView field: bare source render
    "mil.bytes_field",              # bytes field: `Bytes(x)` wrap / owned bare
    # Ctor MIL container-field inits (lowering; the shared container-literal
    # machinery at the target-threaded MIL cell, plus the bare
    # container-param copy / Own-param move name row).
    "mil.container_literal",        # `self.xs = [1, 2]` -> `xs({1, 2})`
    "mil.container_name",           # `self.xs = p` -> `xs(p)` / `xs(std::move(p))`
    "mil.container_repeat",         # `self.xs = [e] * n` -> the threaded
                                    # from_range(repeat_range(..)) prvalue
    "mil.container_comp",           # `self.xs = [f(i) for i in ..]` -> the
                                    # comprehension stmt-expr in the MIL cell
    "with.str_target",              # str/StrView __enter__ as-target
    # Container subscript writes (lowering; THIRSetItem's emit arms plus
    # the owned-str element sink copy and the aug-assign desugar).
    "setitem.slice",                # `c[a:b] = v` / `c[a:b:s] = v` ->
                                    # list_set_slice / list_set_stepped_slice
    "aug.inplace_dunder",           # resolved inplace method (`b += 10` on
                                    # Atomic -> `b.__iadd__(10);`, `s |= {3}`
                                    # -> set_update)
    "aug.record_binop",             # `a += b` on a record with no __iadd__:
                                    # the synthetic `a = (a) + (b);` off the
                                    # Own-returning __add__ fallback
    "aug.container_inplace",        # `c OP= v` -> mutating dunder native
                                    # free-function (list_extend, ...)
    "setitem.checked",              # `::tpy::__setitem__(c, k, v);`
    "setitem.bounds_safe",          # `c[static_cast<std::size_t>(k)] = v;`
    "setitem.aug",                  # `c[k] OP= v` -> the getitem/setitem pair
    "setitem.str_owned_copy",       # view source into a str element: std::string(v)
    "setitem.field_recv",           # write/aug receiver is a field access
    "setitem.user_record",          # `recv[k] = v` on a user record with
                                    # __setitem__ -> ::tpy::__setitem__(recv,k,v)
    "setitem.record_move",          # record element slot: the last use of an
                                    # owned local moves in (std::move(z))
    "setitem.container_value",      # nested-container element: literal value,
                                    # type-prefixed on the checked path
    "setitem.container_comp",       # nested-container element: comprehension
                                    # stmt-expr moved into the slot
    "setitem.btuple_call",          # ptr-Optional-tuple value slot: a
                                    # borrow-tuple call lifts via the
                                    # non-move tuple_to_storage
    "setitem.nested_tuple_literal", # nested-storage tuple value slot: the
                                    # bare spelled literal, lifts inside
    "setitem.nested_tuple_source",  # the same slot from a same-typed source
                                    # expression: the whole tuple stores bare
    "setitem.value_tuple_literal",  # VALUE tuple value slot: the spelled
                                    # brace-init stores directly, each
                                    # element carrying its own view->owned
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
    # `recv.field = [f(x) for x in xs]` at a container field: the
    # comprehension's stmt-expr assigned bare (`this->data = ({ ... });`),
    # the member-init prefix's render one position down.
    "field_write.container_comp",
    # `recv.field = [e] * n` at a container field: the repeat's from_range
    # build, target-typed by the FIELD slot, assigned bare.
    "field_write.container_repeat",
    # `recv.field = data.splitlines()` -- a container-returning method-call
    # RVALUE assigned bare (no move verdict: a prvalue is not a movable name).
    "field_write.container_method_call",
    # ... and its FREE-call sibling (`self.data = make_list(n)`), an
    # `Own[container]` return assigned through the identical bare row.
    "field_write.container_free_call",
    # The same literal into a STORAGE-form `Optional[container]` field
    # (`this->items = std::vector<T>{10, 20};`) -- lowered against the
    # Optional's INNER, the list brace self-describing for the optional ctor.
    "field_write.opt_container_lit",
    # ... and the comprehension into the same STORAGE-form Optional field,
    # lowered against the Optional's INNER.
    "field_write.opt_container_comp",
    # Str-family FIELD write from a name/literal: the bare
    # `recv.field = s;` (operator=(string_view), no view->owned wrap).
    "field_write.str_slice",        # `self.s = x[1:3]` -- a str SLICE value
                                    # (classifier row; shared bare STR emit)
    "field_write.str_ctor",         # `self.s = str()` -- the zero-arg str
                                    # ctor call (classifier row)
    "field_write.str_call",         # str field <- a str-typed call rvalue
                                    # (classifier row; shared bare STR emit)
    "field_write.bytes_narrowed_opt",  # bytes field <- a NAME declared
                                    # `bytes | None`, narrowed here
    "field_write.bytes_slice",      # `self.b = x[1:3]` -- a bytes SLICE value
                                    # (classifier row; shared `Bytes(x)` emit)
    "field_write.bytes_binop",      # `self.b = self.b + c` -- the owned
                                    # concat rvalue (classifier row)
    "field_write.bytes_call",       # `self.b = bytes(...)` -- a bytes-typed
                                    # call rvalue (classifier row)
    "field_write.opt_lift_tparam",  # pointer-repr `Optional[T]` field (T a
                                    # type param) <- borrow `T*` local
    "field_write.str",
    # Owned bytes FIELD write from a name/literal: a view source copies via
    # `::tpy::Bytes(...)`; an owned source lands bare.
    "field_write.bytes",
    # Value-storage Optional[record] FIELD write (`std::optional<inner>`) from
    # a record NAME: the bare copy `recv.opt = p;` (optional::operator=) or
    # `std::move(p)` at a movable name's last use.
    "field_write.optrec_name",
    # The Optional[record] FIELD write from a record RVALUE (ctor / by-value
    # call of the inner type): the bare copy `recv.opt = Inner(args);`.
    "field_write.optrec_rvalue",
    # An `Own[T] | None`-returning CALL into a pointer-repr Optional[T] field:
    # the return IS the field's std::optional<T>, so it assigns bare (the
    # ptr_to_optional lift the field's repr implies would not compile).
    "field_write.owned_opt_call",
    "field_write.union_member_ctor",  # member ctor rvalue -> bare variant store
    # Assign-narrowed same-union NAME source: the whole ptr-variant lifts
    # via to_value_variant into the union field slot.
    "field_write.union_name_lift",
    # The union field sink's CALL twin: a ptr-variant union call result
    # consumed whole by the sink's to_value_variant lift.
    "call.union_value_lift_ret",
    # A container-returning call at a stub method's concrete container
    # slot: the bare call under the cpp_template (STORAGE-threaded).
    "arg.container_call_rvalue",
    # A record-NAME print sink (`file=f`): the bare lvalue under the
    # pinned as_ostream consumer.
    "print.file_name_sink",
    # A literal flush=True kwarg: the `<< std::flush` tail token.
    "print.kw_flush",
    # `return self` at an Optional[Self] ptr-opt return: the bare `this`.
    "ret.ptr_opt_self",
    "ret.ptr_opt_ptr_name",         # a Ptr[T] local at the ptr-opt return
                                    # -> the bare pass (never addr_of)
    # copy() of a ptr-variant union binding: the to_value_variant deep
    # copy (STORAGE FormConvert on the bare name).
    "call.copy_ptr_variant",
    # A storage-form tuple NAME at the Own-storage-tuple return: the
    # bare name (NRVO, no lift).
    "ret.own_storage_tuple_name",
    # A tuple LITERAL at an Own-ELEMENT tuple slot: the spelled
    # brace-init, elements against their Own-peeled by-value slots.
    "arg.own_elem_tuple_literal",
    # An assign-narrowed ptr-variant union NAME method receiver: the
    # inline bare-get read ((*std::get<M*>(c)).m()).
    "method.assign_narrowed_union",
    # An owned-str FIELD read at the value-opt view return: the member
    # lands bare in the optional (converting ctor).
    "ret.value_opt_view_field",
    # A storage-form-tuple-returning call at the ctor MIL slot: bare
    # (no tuple_to_storage lift).
    "mil.tuple_storage_call",
    # A bytes ternary mixing a VIEW arm and a bytes-LITERAL arm: the raw
    # mixed render, whole-ternary BORROW (the sink copies).
    "ifexpr.bytes_view_lit",
    "ifexpr.bytes_owned_elems",     # both arms owned container-element
                                    # subscripts (demoted sink): STORAGE
    "ifexpr.bytes_owned_calls",     # both arms owned-bytes-returning calls:
                                    # STORAGE, the owned sink adds no copy
    # A DISCARDED element-subscript statement: the checked
    # __getitem__(recv, i); evaluated for effect/bounds.
    "expr_stmt.subscript_discard",
    # An Own-element tuple PARAM name at the widened value-tuple return:
    # the bare name (the rvalue-ref binding is already storage form).
    "ret.own_tuple_param",
    # F1-record element/value slot: a record RVALUE (exact or covariant
    # upcast) forwarded bare by the checked `__setitem__`
    # (`::tpy::__setitem__(s._pool, key, Box<Conn>(std::move(conn)));`).
    "setitem.record_rvalue",
    # F1-record element/value slot from `copy(name)`: the copy-construct
    # rvalue (`::tpy::__setitem__(items, 0, Point(p));`).
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
    "setitem.into_any",             # `d[k] = v` into a dict[K, Any] slot from
                                    # an into_any coerce over a bare name or
                                    # str/int literal (make_any element wrap)
    "field_write.any",              # Any FIELD write: the into_any coerce
                                    # (`h.payload = ::tpy::make_any(n);`) or
                                    # an already-Any name copied bare
    "delitem.container",            # del over an element-blind container
    "delitem.subscript_recv",       # `del d[a][b]`: the inner read is the
                                    # receiver lvalue
    "delitem.user_record",          # `del recv[k]` on a user record with
                                    # __delitem__ -> ::tpy::__delitem__(recv, k)
    "field_write.container_narrowed_optptr",  # narrowed ptr-opt param at a
                                    # plain reference field: the deref copy
    "field_write.container_name",   # `Optional[container]` FIELD write from a
                                    # same-family NAME: the shared tail render
    "method.dyn_setattr",           # `obj.x = v` -> the synthesized
                                    # `obj.__setattr__("x", make_any(...))`
    "method.any_ret",               # an Any-returning method call lands
                                    # bare (value type, by-value return) --
                                    # the free-call AnyType row's twin
    "method.dyn_getattr",           # `obj.x` read -> the synthesized
                                    # `obj.__getattr__("x")` method call
    "stmt.del_attr",                # `del obj.attr` -> the synthesized
                                    # `obj.__delattr__("attr");` statement
    "ret.any_subscript",            # `return d[k]` at an Any return slot ->
                                    # bare `::tpy::__getitem__(d, k)`
    "ret.void",                     # `return` / `return None` at a void
                                    # slot (-> None, unannotated, generator)
    "ret.any_name",                 # `return a` -- a bare Any value name
    "ret.any_wrap",                 # non-Any value at an Any return slot ->
                                    # `return ::tpy::make_any(..);`
                                    # (value type, returns bare, no move)
    "ret.dyn_borrow",               # @dynamic protocol borrow return slot:
                                    # bare param name / pointer deref source
    "ret.dyn_own_forward",          # Own[P] return: 'forward' name/call/
                                    # ternary passes the unique_ptr bare
    "ret.dyn_own_wrap",             # Own[P] return: conformer ctor rvalue /
                                    # movable name takes the make_unique /
                                    # make_adapter wrap
    "call.dyn_own_ret",             # an Own[@dynamic P]-returning call lands
                                    # bare at a STORAGE sink (unique_ptr)
    "call.dyn_recv_ret",            # a dyn-protocol call result composing as
                                    # a method receiver / borrow bind
    "method.recv.dyn_call",         # free-call receiver with a dyn-protocol
                                    # result (own arrows, borrow dots)
    "method.protocol_container_ret", # borrow-container protocol result
                                    # feeding the checked-dunder receiver
    "method.protocol_chain_ret",    # protocol-typed method result feeding
                                    # the composing call's receiver slot
    "method.protocol_own_dyn_ret",  # Own[@dynamic P] method result renders
                                    # the bare unique_ptr rvalue
    "decl.dyn_erased_call",         # borrow-returning call as the erased
                                    # dyn decl source (`p = &echo(dog);`)
    "top_level.global_dyn_rebind",  # module-init write of a @dynamic
                                    # protocol global (static adapter slot)
    "expr_stmt.macro_discard",      # void stmt-position macro expansion
                                    # (setattr/delattr builtins) dispatched
                                    # with the DISCARD use
    "expr_stmt.marker_chain",       # void stmt-position @inline/macro marker
                                    # CHAIN peeled to its renderer (DISCARD)
    # A base-init arg beyond the scalar row: str name/literal, None,
    # IntLiteralType digits, record / Optional-ptr / Own param names --
    # all the target-less bare renders of _extract_base_inits.
    "baseinit.nonscalar_arg",
    "baseinit.none_slot_spelling",  # None arg spelled from the base slot ({} / nullopt / nullptr)
    # Ctor member-init-list cells (lowering; the small value families beyond
    # the scalar / record / Optional[record] arms).
    "mil.optional_none",            # `f(std::nullopt)` -- any Optional field,
                                    # inner-independent (incl. value-repr)
    "mil.ptr_none",                 # `p(nullptr)` -- None into a Ptr[T] field
    "mil.genrec_literal",           # container literal into a generic-instance
                                    # wrapper field: the ru-instance spelled render
    "mil.union_none",               # `u(std::monostate{})` -- None into a
                                    # value-variant union field
    "mil.union_lift",               # `u(::tpy::to_value_variant<...>(v))` --
                                    # a borrow ptr-variant name source
    "mil.union_rvalue",             # `u(A(3))` -- a member-record ctor rvalue
                                    # constructs the variant directly
    "mil.value_union",              # `u(u)` / `u(5)` -- value-union bare render
    "mil.tuple_storage",            # `t(::tpy::tuple_to_storage<...>(t))` --
                                    # a borrow pointer-repr tuple param
    "mil.tuple_storage_subscript",  # `pair(::tpy::__getitem__(items, 0))` --
                                    # a storage-tuple element read stores bare
    "mil.tuple_storage_mixed_call", # `t(::tpy::tuple_to_storage<S>(
                                    # make_mixed(b)))` -- non-move lift
    "mil.ptr_tuple_literal",        # `t(::tpy::tuple_to_storage<S>(S{...}))`
                                    # -- a spelled pointer-repr tuple literal
    "mil.value_tuple_name",         # `t(t)` -- value-tuple param bare copy
    "mil.nested_tuple_literal",     # nested-storage tuple literal: bare
                                    # spelled brace-init, lifts inside
    "mil.value_tuple_literal",      # `t(std::tuple<...>{...})` spelled literal
    "mil.optional_value_copy",      # `f(value)` -- value-repr Optional field
                                    # bare-copied from a same-typed opt param
    "mil.optview_shim",             # value-repr Optional[str/bytes] field <-
                                    # borrow optional<view> param (arg-split shim)
    "mil.optional_container_literal",  # `lst(std::vector<int32_t>{1, 2, 3})` --
                                    # a container literal into a value-repr
                                    # Optional[container] field (inner-threaded)
    "mil.optional_str_literal",     # `s("xy")` -- a str literal into a
                                    # value-repr Optional[str] field
    "mil.optional_ptr_lift",        # `f(::tpy::ptr_to_optional(p))` -- a borrow
                                    # `T*` source into a pointer-repr Optional
                                    # field, inner-agnostic
    "mil.unclaimed_family_move",    # own-param move into a field family no
                                    # per-family arm claims (classifier row:
                                    # the render is the shared M3b-move emit)
    "mil.any_coerce",               # `payload(::tpy::make_any(...))` -- an Any
                                    # field from an into_any coerce (classifier
                                    # row; shared bare emit)
    "mil.container_default",       # `items(std::vector<T>())` -- the
                                    # empty-container ctor call in a MIL cell
    "mil.span_copy",              # `items(items)` -- std::span field
                                    # bare-copied from a same-typed param
    "mil.callable_copy",            # `on_event(cb)` -- std::function field
                                    # bare-copied from a same-typed param
    "mil.callable_lambda",          # `action([]() { ... })` -- routable
                                    # lambda into the std::function field
    "mil.record_method_rvalue",     # Own-returning method rvalue constructs
                                    # the record field directly (Rc.new)
    "mil.own_param_copy",           # Own record param at a NON-last use: the
                                    # warned bare `field(param)` copy
    # An own-field init demoted from the MIL to the ctor body (bare non-param
    # name / nested-def name / body-local ref) -- demoted rather than
    # rejecting the whole ctor (lowering verdict; the body machinery renders it).
    "mil.demote_mirror",
    # Container/str subscript read off a FIELD-ACCESS receiver (lowering;
    # `::tpy::__getitem__(this->xs, i)` -- the receiver renders as its own
    # THIRFieldAccess inside the shared subscript emit).
    "subscript.field_recv",
    # Bytes-family FIELD subscript read (lowering; the same field-receiver
    # widening through the bytes dispatch -- `::tpy::bytes_getitem(this->b, i)`).
    "subscript.bytes_field",
    # A value scalar/char/enum/typeparam/Ptr field read off a value F1-record
    # field CHAIN receiver (lowering admission; `o.mid.inner.v` -- `_lower_expr`
    # recurses through the receiver, so every link's render is shared with the
    # single-level field read, and admission is the distinguishing site).
    "field.chain_recv",
    # The same chain whose INNER hop is a `Ptr`-valued field read (lowering
    # admission; `self.s.a.q` -> `::tpy::deref_check(this->s).a.q`, or the
    # `->` spelling where sema proved the pointer non-null).
    "field.chain_ptr_recv",
    # Unproven member access off a STORAGE Optional[F1-record] field lvalue
    # (`h.opt.x` -> `::tpy::deref_optional_check(h.opt).x`; lowering) -- the
    # optional-lvalue sibling of the deref_check (`T*` receiver) arm.
    "field.opt_check_field_recv",
    # The container-subscript sibling (`d["a"].x` ->
    # `::tpy::deref_optional_check(::tpy::__getitem__(d, "a")).x`; lowering).
    "field.opt_check_subscript_recv",
    # A RECORD-result user-dunder binop (`a // b` -> Meters, `td1 + td2`):
    # the injected/native cpp_template render, admitted at consumers that
    # pin the record rvalue (lowering).
    "binop.record_dunder",
    # ...consumed as a PRINT arg (the record-call row's binop twin; gate).
    "print.record_binop",
    "field.opt_check_call_recv",    # unproven field off a ptr-Optional
                                    # CALL -> deref_check(<call>).field
    "method.opt_check_call_recv",   # the method sibling ->
                                    # deref_check(<call>).method(args)
    "print.record_field",           # F1-record FIELD print arg -> RAW stream
                                    # over the bare member read
    # ...consumed as a FIELD-access receiver (`(a // b).v`; gate).
    "field.binop_recv",
    # A field read off a PROPERTY-GETTER receiver returning a record
    # (`h.mid.x` -- the inner read is a getter call in disguise; gate).
    "field.property_call_recv",
    # ...DISCARDED in statement position (`timedelta(seconds=1) / 0;` --
    # evaluated for its raise; statement lowering).
    "expr_stmt.name",               # a bare NAME statement (`x`) -> `x;`
    "expr_stmt.record_binop",
    # A DISCARDED operator / ternary / field read in statement position
    # (`n + 1;`, `-n;`, `0 < n < 5;`, `p.x;`) -> the expression render + `;`.
    "expr_stmt.value_discard",
    # `xs[i] = None` into a pointer-repr Optional[F1-record] element slot
    # -> the STORAGE `std::nullopt`.
    "setitem.optional_none",
    # A scalar source into a value-repr Optional[scalar] element slot
    # (`xs[0] = 5` on `list[int32 | None]`) -> the bare scalar.
    "setitem.optval_scalar",
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
    # (`a.__getitem__(::tpy::BasicSlice{1, 4})`).
    "subscript.record_slice_method",
    # A generator-method ctor-rvalue receiver lifted into a named local
    # (`Counter __tmp_N = Counter(..);` + `__tmp_N.each()`) -- the frame
    # captures the receiver by reference, so the temporary must outlive the
    # call (the is_temporary lift at a method receiver).
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
    # `dict[str, list[int32]]`): the checked dunder's element lvalue, which
    # lands bare in every value sink because the single element emitter
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
    "foreach.hoist_ptr_null",       # hoisted container unpack target ->
                                    # `T* v = nullptr;` predecl
    "foreach.hoist_ptr_target",     # its per-iteration re-point (the
                                    # frame_ptr_elem render, sync twin)
    "foreach.value_opt_elem",
    # A ptr-repr Optional[F1-record] element loop var (`for v in d.values():`
    # over `dict[str, P | None]`): the STORAGE-form binding registers in the
    # storage-opt set -- bare-optional reads, optional_to_ptr at T* slots.
    "foreach.storage_opt_const_elem",  # ... its const-bound twin
                                    # (`const optional<P>&` loop var)
    "foreach.storage_opt_elem",
    # Native auto-consuming for-each iterable (lowering; `for x in items:`
    # where items is consumed at last use -- `::tpy::own_iter(std::move(
    # items))`, rvalue capture, loop var `auto&&` joins movable_locals).
    "foreach.consuming_iter",
    # List-literal for-each iterable (lowering; `for c in [a, b, c]:` -- the
    # owning `auto __obj_N = {a, b, c};` initializer-list capture, elements
    # rendered target-less).
    "foreach.iter_literal",
    # Str-literal for-each iterable (lowering; `for ch in "abc":` -- the
    # owning `auto __obj_N = std::string_view("abc");` capture, char elems).
    "foreach.str_literal",
    # Branch-first-declared value locals used after the loop -> `{cpp} {name};`
    # predecls before the loop (lowering; the branch-decl predecls, shared with
    # the if/try/with hoist family). Includes the loop var when hoisted.
    "foreach.hoist_decl",
    "while.hoist_decl",             # body-declared var read after a while loop -> predecl before it
    # Loop else blocks (lowering; the bare `{...}` past the loop's close
    # brace + its `__after_else_N:;` label -- run on normal completion,
    # jumped past by a break).
    "loop.for_else",                # for/else (range and container routes)
    "loop.while_else",              # while/else
    # `del x` early-destruction move-sink (lowering; one
    # `{ auto __del_sink = std::move([*]name); }` block per sunk name --
    # skip-only dels stay on the no-code THIRNoOpStmt face).
    "stmt.del_var_sink",
    "stmt.del_item_multi",          # multi-target del: one __delitem__ line per target
    "stmt.del_attr_multi",          # multi-target del: one __delattr__ line per target
    # Rebound container-literal local (lowering; the F2d rebind-slot
    # pointer-local with a container-literal init --
    # `std::vector<T>* xs = &__slot_1;`).
    "decl.container_rebind_slot",
    # ... and the comprehension init of the same pointer-local
    # (`std::vector<T> __slot_1 = ({...});`).
    "decl.comp_rebind_slot",
    # Runtime-BigInt `.to_fixed_check<T>()` narrows (lowering; the
    # subscript-index / slice-bound / aug-assign / enum-from_value wraps).
    "narrow.subscript_index",       # `i.to_fixed_check<int32_t>()` (reads + del)
    "narrow.slice_bound",           # same wrap on a str/bytes slice bound
    "narrow.aug_value",             # `({0}).to_fixed_check<T>()` aug-assign value
    "narrow.enum_arg",              # `({0}).to_fixed_check<U>()` E(x) arg
    "narrow.binop_param",           # same wrap at a resolved binop's fixed-int
                                    # param slot (_convert_to_fixed_int_arg)
    "narrow.fstring_arg",           # runtime-BigInt f-string arg -> the
                                    # `({0}).to_string()` row (same declared-
                                    # type key, no to_fixed_check)
    "narrow.wrapper_union",         # F6 isinstance on a recursive-alias wrapper
                                    # union subject -> holds/get via `.value`
    "narrow.folded_isinstance",     # F1 isinstance on an already-narrowed
                                    # subject -> `if (true)/(false)` + shadow
                                    # re-extraction from the ORIGINAL union
    "while.narrow_folded",          # exhaustiveness-folded while-isinstance
                                    # head -> `while (true)` + the body's
                                    # extraction alias
    "while.or_chain",               # isinstance leaf under `||` in a while
                                    # head -> bare membership test, body
                                    # walks un-narrowed
    "if.narrow_folded_else",        # exhaustiveness-folded `if (true)` with
                                    # an EXPLICIT else -> dead arm still
                                    # extracts its excluded member
    "if.narrow_nc_else_fact",       # non-member else fact (remaining
                                    # nullable union) tolerated -- no else
                                    # body, so no consumer exists
    "reseat.narrow_kill",           # rebind of a NARROWED union subject:
                                    # the narrow dies, the write takes the
                                    # ordinary union reseat
    "reseat.union_inline_slot",     # slotless union reseat -> fresh
                                    # __slot_N + to_ptr_variant
    "method.union_property_ret",    # ptr-union property getter call
                                    # at the decl lift's BORROW_BIND
    "method.value_union_property_ret",  # VALUE-union property getter call,
                                    # position-blind (a by-value variant)
    "ret.union_borrow_field",       # Ref(union) property return ->
                                    # the bare self-field read
    "narrow.multi_var",             # multi-var `&&` isinstance compound ->
                                    # composed holds tests + one branch
                                    # alias per subject
    "binop.narrowed_union_operand", # condition-scope-narrowed union NAME
                                    # at a compare slot -> the inline get
    "narrow.poly_tuple",            # tuple-form poly isinstance -> the
                                    # no-init dynamic_cast OR-chain
    "narrow.poly_value",            # value-position poly isinstance -> the
                                    # bare null-check chain (no branch)
    "narrow.dyn_neg_guard",         # `if not isinstance(v, Sub):` on a poly
                                    # subject -> `(!(<null-check>))` condition
    "narrow.dyn_post_if",           # the early-return implicit-else poly
                                    # cast-and-cache alias (persistent)
    "narrow.union_post_if",         # the early-return implicit-else variant
                                    # extraction alias (persistent)
    "narrow.dyn_assert",            # `assert isinstance(v, Sub)` poly narrow
                                    # -> null-check cond + persistent alias
    "subscript.protocol_recv",      # protocol-typed template-param receiver
                                    # -> the shared checked __getitem__
    "subscript.varargs_recv",       # *args view + range-proven index ->
                                    # args[static_cast<std::size_t>(i)]
    "setitem.ru_scalar",            # scalar/str literal into a wrapper-union
                                    # value slot -> bare token (converting ctor)
    "setitem.ru_move",              # ... and a MOVABLE wrapper name there,
                                    # which the insert moves in
    "print.union_narrowed_arg",     # U3-narrowed alias print arg -> the
                                    # union-typed `::tpy::__str__` visitor
    "subscript.ru_narrowed_recv",   # subscript off a narrowed wrapper-union
                                    # receiver -> the fi-fallback bare recv[idx]
    "subscript.narrowed_ptr_opt_recv",  # subscript off a None-narrowed
                                    # ptr-repr Optional[container] name (read
                                    # or write target) -> the `(*recv)` deref
    "subscript.narrowed_opt_nested",  # `rows[i][j]` off a None-narrowed
                                    # ptr-repr Optional[container] name -> the
                                    # nested __getitem__ over the `(*rows)` deref
    "subscript.bytearray_recv",     # `b[i]` off a bytearray name/field ->
                                    # the bytes @native dunder bytes_getitem
    "optptr.container_call_temp",   # container-returning rvalue CALL at an
                                    # Optional[container] slot -> the typed
                                    # `__tmp_N` hoist + `&(__tmp_N)`
    "optptr.record_call_temp",      # its RECORD-pointee sibling: a free call
                                    # returning the slot's pointee (bare or
                                    # Own-wrapped) -> the same hoist + addr-of
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
    "call.borrow_ret_passthrough",  # T&-returning call bare at the
                                    # borrow-record RETURN sink only
    "ret.record_call_storage",      # record rvalue call at the STORAGE
                                    # return -- bare passthrough
    "ret.record_call_borrow",       # T&-returning call at the borrow
                                    # return -- bare passthrough
    "ret.record_op_storage",        # Own-returning user dunder binop/unary
                                    # at the STORAGE return -- bare
    "ret.record_op_value",          # ... at a VALUE-record slot (by-value ret)
    "ret.record_storage",
    "ret.copy_record",              # `return copy(p)` -> `return Point(p);`
    "ret.record_methodcall",        # method-call rvalue at the storage slot
    "ret.record_methodcall_value",  # ... at a VALUE-record slot (by-value ret)
    "ret.record_deref_coerce",      # `return ptr` (Ptr[T] local/param) at the
                                    # borrow return -> `deref_check(ptr)`
    "ret.record_self",              # `return self` -> `return (*this);`
    "ret.self_move",                # `return self` in a consuming method
                                    # -> `return std::move((*this));`
    "ret.record_field",             # `return recv.field` at the borrow slot
    "ret.record_subscript",         # `return c[i]` -- container record element
    "ret.ptr_opt_field",            # `return self.f` (Optional[record] field)
                                    # -> `optional_to_ptr(this->f)`
    "ret.ptr_opt_field_narrowed",   # narrowed storage-Optional field at the
                                    # ptr-opt return -> the same
                                    # optional_to_ptr lift (declared-keyed)
    "ret.opt_field_ref",            # @property getter `return self.f` -> bare
                                    # `this->f` (std::optional<T>& ref return)
    "ret.storage_opt_rvalue",       # `return Coord(0, 0)` -> bare ctor into a
                                    # value-storage / Own Optional slot
    "ret.storage_opt_whole_rvalue",  # `return f(x)` where f already returns
                                    # the slot's own storage optional -- bare
    "ret.storage_opt_own_move",     # `return x` -- an Own[P|None] param
                                    # (std::optional<P>&&) moves out whole
    "decl.opt_own_param_lift",      # `y = x` off an Own[P|None] param: pure
                                    # lift, `P* y = optional_to_ptr(x);`
    "reseat.opt_own_param_lift",    # ... and the slotless reseat twin,
                                    # `y = ::tpy::optional_to_ptr(x);`
    "reseat.opt_storage_call",      # OPT_STORAGE_CALL reseat re-fills the
                                    # decl slot and re-lifts the pointer
    "stmt.tuple_unpack.call_storage_wrap",  # per-element-own call result
                                    # lifts whole via tuple_to_pointer
    "name.func_ref_targs",          # generic fn ref spells the template-args
                                    # suffix (identity<int32_t>)
    "name.async_factory_wrap",      # async-def ref at a Callable slot:
                                    # the make_adapter wrapper lambda
    "ret.str_field",                # `return recv.field` (owned-str member,
                                    # STORAGE) at a str-family return slot
    # The container-only SOURCE shapes of the storage reference return slot
    # (`-> Own[list/dict/set]`). Every source shape the record half also
    # carries witnesses a `ret.record_*` face: one return ladder, so the
    # container rows left here are the ones with no record counterpart.
    "ret.container_literal",
    "ret.container_repeat",         # `return [label] * 3;` -- the repeat
                                    # build target-typed by the slot
    "binop.contains_view_key",      # membership over a VIEW-keyed set/dict:
                                    # str bare / bytes static view spelling
    "arg.bytes_view_literal",       # bytes literal at an Own[BytesView]
                                    # insert slot: the static view spelling
    "call.inst_proto_name_arg",     # structural-protocol param name at an
                                    # instantiation arg: bare in construct<>
    "call.builtin_value_record_ret",   # builtin ValueType record return, bare at storage
    "decl.dyn_own_erased_call",        # already-erased Own[dyn] call decl (unique_ptr spelled)
    "decl.builtin_value_record_slot",  # builtin ValueType record decl, plain spelled copy
    "decl.list_repeat_slot",        # lazy `[v] * n` decl slot: the
                                    # spelled `repeat_range<T>` copy
    "decl.coro_frame_rebind",       # concrete coro handle rebind (emplace / move pair)
    "decl.coro_frame_local",        # async-factory local: the concrete
                                    # frame in optional storage (erasure
                                    # deferred to the Own[dyn] consumer)
    "call.generic_btuple_name",     # borrow-form tuple name at an open
                                    # generic tuple slot: passes bare
    "call.generic_open_slot_field",  # same-T field read at the open T
                                    # slot: bare (`identity<T>(this->val)`)
    # ... and the same read at a COMPOSITE open slot (`Ptr[R]` resolved
    # `Ptr[W]`), which the bare-T row above cannot reach
    "call.generic_open_slot_field_composite",
    "ret.value_opt_view_ctor",      # StrView instantiation at the
                                    # Optional[view] return: the folded src
    "btuple.elem_field",            # F1-record FIELD element in a borrow
                                    # tuple -> `&(<member read>)` lift
    "protoarg.copy_iter",           # copy_iter(..) rvalue at an
                                    # Iterable[Own[T]] slot -> bare inline
                                    # (a CopyIter NAME takes protoarg.bare)
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
    # Value-repr Optional[cheap scalar] return slot (`-> int32 | None`): the
    # None-literal `std::nullopt` arm and the whole-optional bare param pass
    # (deref-on-narrow stripped); other scalar sources ride the generic tail.
    "ret.value_opt_none",
    "ret.value_opt_name",
    # A narrowed RECORD-kind value-opt binding at the Own[record] STORAGE
    # return: `return std::move((*x));` (the Optional[Own[Payload]] param).
    "ret.record_value_opt_name",
    "ret.value_opt_field",
    # Value-repr Optional[value tuple] return slot (`-> tuple[float, int32] |
    # None`): `None` -> `std::nullopt`, a tuple literal spells the inner
    # tuple's brace-init, an un-narrowed value-tuple name passes bare.
    "ret.value_opt_tuple_none",
    "ret.value_opt_tuple_literal",
    "ret.value_opt_tuple_name",
    # Value-repr Optional[view] return (str or bytes): `None` -> `std::nullopt`,
    # a same-family Optional[view] param -> the view->owned arg-split shim
    # (THIROptViewArg), and a str/bytes literal -> bare owned literal.
    "ret.value_opt_view_none",
    "ret.value_opt_view_shim",
    "ret.value_opt_view_literal",
    "ret.value_opt_view_slice",  # a slice source at a VIEW-inner value-opt
                                    # return: the view render lands bare
    "ret.value_opt_view_name",   # a str/bytes NAME whose form already
                                    # matches the slot's inner -> bare
    "ret.value_opt_view_materialize",  # a view->owned materializing coerce at
                                    # an OWNED inner: the coerce owns the
                                    # render, the optional's ctor absorbs it

    # Container-literal element families (lowering; the widened
    # THIRContainerLiteral slots) plus the make_vector/make_ordered_* switch
    # and the per-element last-use move.
    "containerlit.enum_elem",       # `[Color.RED, ...]` / `{Color.RED, ...}`
    "containerlit.optional_elem",   # `[1, None, 3]` -> `{1, std::nullopt, 3}`
    "containerlit.tuple_elem",      # `[(1, 2), ...]` -> spelled std::tuple elems
    "containerlit.container_elem",  # nested list element `[[1, 2], [3]]`
    "containerlit.record_elem",     # `[P(1), p]` -- ctor rvalues / record names
    "containerlit.bytes_elem",      # `[b"a", v]` -- owned render / `Bytes(x)`
    # A list/dict literal at a recursive-union WRAPPER decl slot: the
    # non-generic `AliasRef` form and its generic alias-INSTANCE sibling.
    "decl.ru_wrapper_literal",
    "decl.ru_instance_literal",
    "assign.ru_wrapper_literal",    # the REASSIGN sibling (top-level global
                                    # init `g = [1, [2, 3], 4];`)
    "containerlit.tuple_storage",   # `[(a, P(1)), ...]` -> tuple_to_storage<S>(S{...})
    # The REF-element sibling: the inner is the BORROW tuple
    # (`std::tuple<T*, ..>{&(a), nullptr}`) under the same convert --
    # the tuple literal's has_ref_elements path.
    "containerlit.tuple_borrow_storage",
    "containerlit.make",            # make_vector / make_ordered_map / _set
    "containerlit.move",            # `std::move(name)` element at last use
    "containerlit.copy_record",     # `copy(p)` element: the shared
                                    # copy-construct row at a record slot
    "containerlit.union_name_lift",  # tracked ptr-variant NAME at a value-
                                    # union slot -> `to_value_variant<..>(a)`
    "containerlit.tparam_elem",     # plain declared NAME at a `T` element
                                    # slot -> bare brace init (`return {x};`)
    "containerlit.tparam_copy_elem",  # `copy(x)` of an open-T NAME at a `T`
                                    # element slot -> the `T(x)` rvalue
    "containerlit.tuple_name_storage",  # bare NAME at a non-value tuple
                                    # element slot -> the whole non-move
                                    # tuple_to_storage copy
    "containerlit.tuple_subscript_storage",  # whole storage-tuple element
                                    # read at a non-value tuple slot -> the
                                    # bare `__getitem__` copy (no wrap)
    "containerlit.tuple_mixed_call",  # mixed-own-tuple call element -> the
                                    # non-move tuple_to_storage lift
    "containerlit.union_member_prefix", # nested array literal: typed member ctor
    "containerlit.tuple_union_elem",   # nested tuple literal w/ union elements
    "containerlit.union_narrowed_elem",  # narrowed alias at a value-union
                                    # slot -> bare member (`{__a, ..}`)
    "list_repeat.lazy",             # `[v] * n` kept unmaterialized: the
                                    # bare repeat_range, no from_range wrap
    # A spanlike coerce over an array-literal inner: the helper wraps the
    # make_array-typed brace init (`as_mut_span(std::array<T, N>{...})`).
    "coerce.span_array_literal",
    # A leaf try/FINALLY in a resumable with no return/break/continue
    # crossing it: renders as the plain sync duplicated-body try, so no
    # finally-frame scaffolding is involved.
    "res.leaf_try_finally",
    "res.leaf_deferred_capture",    # a crossing return's deferred pointer
                                    # capture rendered by the hook's recipe
    # A top-level narrowing `assert isinstance` in a resumable flat BB: the
    # alias is appended after the assert and is BB-local (each resume case
    # re-establishes the stamped fact).
    "res.flat_assert_narrow",
    # A CONTAINER field at the RESUMABLE for-head's iterable position: the
    # bare member read is what begin()/end() are taken off
    # (`(__self.nodes).begin()`). The sync for-head has its own arm.
    "field.container_iterable",
    # The NARROWED `Optional[container]` flavor of the row above
    # (`if self.d is not None: for k in self.d:` -> `(*this->d)`): the same
    # bare read, carrying the narrowed-Optional unwrap.
    "field.narrowed_opt_container_iterable",
    # A stored-awaitable F1-record field at the BORROWED suspend operand
    # (`await self.evt` -> `__sub_0 = &(__self.evt);`): the bare member
    # read; the skeleton owns the `&(..)` wrap.
    "field.suspend_borrow",
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
    # is excluded (its render is broken, see BUGS.md).
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
    # (`three_params[int32](10, c=5)` -> `int32_t{}`).
    "call.tparam_default_construct",
    # A tuple LITERAL at a value-tuple-resolved T slot: the inline spelled
    # brace prvalue (`push_t<...>(pq, std::tuple<...>{2, "second"})`).
    "call.generic_tuple_literal",
    # A conformer NAME into a still-open single structural protocol slot
    # (`drive_implicit(t)` at `Awaitable[T]`): binds the template param bare.
    "call.generic_open_proto_name",
    # A lambda at a still-open Fn slot in a generic caller
    # (`map_keys(pairs, lambda p: p[1])`): the lambda renders itself.
    "call.generic_open_slot_lambda",
    # An owned NAME at an Own slot whose payload is still composite in T
    # (`poll_ready(empty)` with `empty: list[T]` at `Own[T]` resolved
    # `Own[list[T]]`): the Own copy+move row against the substituted slot.
    "call.generic_own_composite_slot",
    # A container-element subscript at the still-open slot inside a generic
    # body (`f(xs[i])` at `T`): the element read binds the ref slot bare.
    "call.generic_open_slot_elem",
    # ... and its record-ctor twin (`Bare(src[0])` ->
    # `Bare<T>(::tpy::__getitem__(src, 0))`).
    "ctor.generic_open_slot_elem",
    # A nested value-tuple NAME at a T slot (`less(a, b)` on
    # `((1, 2), "x")`): the recursive value family binds bare.
    "call.generic_nested_tuple_name",
    # A narrowed ptr-repr Optional[wrapper] param at a same-wrapper slot
    # (`leaf_count(t)` on `Tree[int] | None`): the `(*t)` deref binds bare.
    "arg.ru_wrapper_opt_narrowed",
    # A VALUE-yielding generator-factory comp source (`[v for v in
    # wrap(3)]`): the owning `auto __obj_N` capture with begin/end.
    "comp.genfac_source",
    # A container-type ctor call at a VALUE sink (`print(asdict(p))` --
    # the expansion's `dict(...)` prvalue under the printer wrap).
    "call.container_ctor_value",
    # ... and at the for-head's ITERABLE sink (`for it in list(each(xs)):`
    # -- the owning `auto __obj_N =` rvalue capture).
    "call.container_ctor_iterable",
    # A Span-returning call: the value-view prvalue lands bare at any sink
    # (`::tpy::__len__(::tpy::as_span(x))` -- the Array value row's sibling).
    "call.span_value_ret",
    # ... and its for-head consumer (`for v in span(x):` -- the owning
    # `auto __obj_N = ::tpy::as_span(x);` rvalue capture).
    "foreach.span_call",
    # `dict({...})` -- the spelled result type around the dict literal's
    # own ordered_map render (the asdict expansion's shape).
    "call.dict_literal_instantiation",
    "call.dict_tuple_literal_instantiation",
    # A scalar/owned-str FIELD read at a value-union element slot renders
    # bare (`{{"name", p.name}}` -- the converting ctor picks the member).
    "containerlit.union_field_elem",
    # None / nested list/dict literal at a wrapper-union element slot
    # (`list[V]` over a recursive alias): the ru-literal element renders
    # (monostate member / spelled nested container).
    "containerlit.wrapper_elem",
    # A value-tuple needle in a tuple-keyed dict/set membership
    # (`(1, 2) in d`): the spelled tuple render inside contains(...).
    "binop.contains_tuple_needle",
    # An open-T needle at ranges::contains (`key in self._data` on
    # dict[T, int] inside the generic body).
    "binop.contains_tparam_needle",
    # A list comp at a union value slot with a unique list member: the
    # comp lowers against the member (the asdict list recursion).
    "comp.union_member_source",
    # A Callable field read into a callable slot (`apply(handler.cb, 10)`):
    # the bare member read, the std::function converting implicitly.
    "call.callable_field_arg",
    # A Callable-VALUE field read at a value position: the bare member read.
    "field.callable_value",
    # An empty container instantiation into an Own[container] ctor slot
    # (the @dataclass default_factory fill): the spelled default ctor.
    "ctor.own_container_instantiation",
    # ... and its one-source sibling (`list(names)` at the same
    # slot): the construct template, temp-free.
    "ctor.own_container_construct",
    # `copy(src[i])` of an open-T element into an Own[T] ctor slot:
    # the generic copy tail (`T(<element read>)`).
    "ctor.copy_open_elem",
    # ... and its VALUE-TUPLE slot flavor (`Pair(copy(src[0]))` ->
    # `Pair<T, ..>(std::tuple<T, ::tpy::BigInt>(<read>))`).
    "ctor.copy_open_tuple_elem",
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
    "isnone.subscript_storage",      # `d["a"] is not None` -> has_value over
                                     # the storage-form __getitem__ read
    "call.opt_callable_unwrap",      # `f(x)` on a `Callable | None` binding
                                     # -> `f.value()(x)` (declared-type keyed)
    "isnone.property_subject",       # `w.node is None` on a @property read
                                     # -> has_value over the getter call
    "decl.forwarded_alias",          # `xs = it` proto-param alias: no code,
                                     # trivia only
    "name.forwarded_alias",          # a forwarded alias read renders the
                                     # backing param
    "decl.opt_property_lift",        # `n = w.node` on an Optional @property
                                     # -> optional_to_ptr(w.node())
    "binop.contains_field_recv",     # `x in si.tags` on a container field
                                     # -> si.tags.contains(x)
    "binop.iter_membership",         # `x in b` on a user iterable with no
                                     # __contains__ -> the __iter__/__next__
                                     # statement-expression loop
    "isnone.tuple_elem_lift",        # `h.t[0] is None` -> pre-lifted
                                     # `optional_to_ptr(std::get<0>(h.t))
                                     # == nullptr`
    "isnone.tuple_elem_bare",        # `p[1] is None` off a mixed own-borrow
                                     # tuple LOCAL -> the bare
                                     # `std::get<1>(p) != nullptr` (no lift)
    "decl.mixed_elem_ptr",           # `e = p[1]` off a mixed own-borrow
                                     # tuple LOCAL -> `Box* e =
                                     # std::get<1>(p);` (bare, no lift)
    "isnone.generic_opt_trait",      # a generic `T | None` slot -> the
                                     # form-neutral `::tpy::opt_has_value(o)`
    "isnone.union_monostate",        # union-binding `is [not] None` ->
                                     # holds_alternative<std::monostate>
    "isnone.union_wrapper_monostate",  # wrapper-union binding -> the same
                                       # holds test through `.value`
    # Value-tuple slots (`tuple[scalar|str, ...]`): the spelled
    # `std::tuple<...>{...}` literal render at returns / decls, and the bare
    # value-tuple name return.
    "ret.tuple_literal",
    "ret.own_storage_tuple",        # Own[tuple[.., record]] literal return ->
                                    # spelled `std::tuple<..>{rvalues}`
    "ret.wrapper_ref_tuple",        # `return (t, 0)` at tuple[Tree, int32]
                                    # -> the spelled reference-member brace
    "ret.tuple_name",
    "decl.tuple_literal",
    "decl.tuple_literal_wide",      # container/value-union elements at the decl
    # A VALUE-capture record/Own tuple LITERAL decl bound by value (storage
    # form): the local owns its elements, a ref-element type spells `auto`.
    "decl.storage_record_tuple",
    # A @dynamic protocol local (`p: P = Concrete()`): concrete/adapter slot +
    # protocol Base* pointer.
    "decl.dyn_protocol",
    # A @dynamic protocol local RESEAT (`p = Other()`): a fresh hoisted
    # `std::optional<slot>` + emplace + `p = &*slot`.
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
    "btuple.elem_ptr_union",        # ptr-variant UNION elem slot: const
                                    # ptr-variant dst + value-variant src
    "btuple.value_to_borrow",       # rvalue elements via the source-tuple helper
    "btuple.value_arg",             # value-tuple literal call arg
    "btuple.proto_borrow",          # native-protocol slot tuple literal whose
                                    # non-value elements are simple lvalues:
                                    # the per-element `T*` ref capture
    "btuple.decl",                  # sync borrow-tuple local decl (`auto t = ...`)
    "decl.bytearray_alias",         # `std::vector<uint8_t>& y = <name|field>`
    "decl.btuple_alias",            # borrow-tuple local re-aliased from a name
    "decl.storage_tuple_alias_field_subscript",  # `auto&& t = self.store[k]`:
                                    # the storage-tuple alias off a FIELD
                                    # receiver, const derived from the chain
    "decl.btuple_elem_alias",       # T& alias of a borrow-tuple param's ptr element
    "decl.btuple_reassigned",       # reassigned MIXED own-borrow tuple first decl:
                                    # the call render binds directly (no slot/lift)
    "decl.btuple_rebind_slot",      # reassigned btuple decl off an owning call:
                                    # optional slot + emplace + tuple_to_pointer
    "decl.btuple_lift",             # reassigned btuple decl off a storage lvalue
    "decl.btuple_name_copy",        # reassigned btuple decl off a borrow-form
                                    # NAME: the plain pointer-tuple copy
    "decl.btuple_literal",          # reassigned btuple decl off a REF-capture literal
    "call.btuple_slot",             # borrow-tuple call result into an `auto` decl
    "ret.btuple_name",              # already-borrow tuple local returned bare
    "ret.dyn_own_factory",          # return-position async-factory erasure (make_adapter wrap)
    "ret.consuming_self_field",     # consuming method: `return std::move(this->f);`
    "ret.finally_deferred",         # deferred return capture: auto* p before the
                                    # finally chain, move/ptr_to_optional_move after
    "ret.genrec_literal",           # Own[Tree[T]] return of a container literal:
                                    # the ru-instance spelled render
    "ret.genrec_member",            # Own[Tree[T]] return of a scalar member value:
                                    # bare, the converting ctor absorbs it
    "ret.btuple_literal",           # `return (n, p)` -> std::tuple<..,T*>{n, &(p)}
    "ret.btuple_call",              # `return h.get_pair()` -- callee already
                                    # returns borrow form, so the relay is bare
    "ret.btuple_ternary",           # `return h.a if c else h.b` -- ONE
                                    # tuple_to_pointer around the conditional
    "arg.btuple_name",              # already-borrow tuple name passed bare
    "arg.genrec_own_literal",       # container literal into an Own[genrec] slot:
                                    # the ru-instance spelled render, inline
    "arg.required_protocol_union",  # name at a required multi-protocol
                                    # union slot -> plain value render
    "arg.value_opt_callable",       # whole Optional[Callable] name passed
                                    # bare at a matching value-opt slot
    "arg.value_opt_tuple",          # ... and the value-tuple inner twin
    "arg.value_opt_field",          # ... and the FIELD twin of both: a whole
                                    # value-opt member read at a matching slot
    "arg.tuple_literal_value_opt",  # tuple LITERAL at a value-opt tuple slot
                                    # (admission; render is the bare name)
    "method.optview_whole_arg",     # whole Optional[str/bytes] name passed
                                    # bare at a user-record method's matching
                                    # value-opt view slot (target-less loop)
    "arg.container_field",          # container field read bound bare at a
                                    # container ref slot
    "arg.record_field_marker",      # F1-record field read bound bare at a
                                    # marker or record-method callee's
                                    # record ref slot
    "arg.value_tuple_field",        # value-tuple field read bound bare at a
                                    # matching value-tuple ref slot (forms
                                    # coincide, so no lift)
    "arg.record_borrow_ret_marker", # T&-returning record call bound bare at
                                    # a marker callee's record ref slot
    "arg.btuple_literal_marker",    # tuple literal at a marker callee's
                                    # pointer-repr tuple slot (borrow builder)
    "mil.native_ctor",              # ctor MIL field init from a plain @native
                                    # record ctor (`_logger(::ns::H(name))`)
    "arg.own_tparam_call_rvalue",   # T-returning call rvalue bare at the
                                    # same open Own[T] slot
    "call.dyn_getattr_builtin",     # 2-arg getattr(obj, name) delegated to
                                    # the dyn-attr read mirror (result-blind
                                    # bare dunder call)
    "arg.bytes_owned_name",         # owned-form bytes name bound bare at an
                                    # Own[bytes] element slot (the template
                                    # callee binds the lvalue natively)
    "arg.bytes_owned_call",         # owned-bytes call rvalue bound bare at any
                                    # Own[bytes] slot (a prvalue has nothing to
                                    # move from, and owes no view->owned copy)
    "arg.bytes_owned_literal",      # bytes literal at an Own[bytes] element
                                    # slot -> the owned literal render
    "arg.own_enum_elem",            # enum name bare at an Own[enum] container
                                    # element slot (value payload, no temp)
    "arg.own_ptr_value",            # ptr value at an Own[Ptr] element slot
    "arg.own_value_tuple_literal",  # value-tuple literal at an Own[tuple]
                                    # element slot -> spelled value render
    "arg.own_open_t_tuple_literal", # open-T tuple literal at an Own[tuple]
                                    # element slot -> bare-`T` value render
    "arg.own_open_t_tuple_storage_source",
                                    # whole open-T tuple element READ at an
                                    # Own[tuple] element slot -> bare pass
    "arg.ptr_none",                 # None at the (collapsed) Ptr[T] slot ->
                                    # the bare nullptr render
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
    "arg.own_btuple_mixed_call",    # mixed-own-tuple call at the Own elem
                                    # slot -> non-move tuple_to_storage
    "arg.own_tuple_storage_elem",   # storage element read at an Own[tuple]
                                    # param slot -> bare __getitem__ copy
    "arg.wrapper_ref_tuple_elem",   # wrapper elem off a reference-element
                                    # tuple binding -> bare std::get pass
    "arg.own_btuple_call_storage",  # storage-form tuple return (Own[tuple] /
                                    # all-Own per-element synthesis) at the
                                    # same slot -> passes bare
    "call.btuple_pass",             # borrow-tuple call result at a MATCHING
                                    # borrow-form tuple param -> binds bare
    "method.protocol_self_storage_ret",  # Own[Self] rvalue into the `auto`
                                    # decl slot in a template body
    "method.view_self_storage_ret",  # view-receiver stub's structural-protocol
                                    # result into the `auto` decl slot
    "method.protocol_genrec_storage_ret",  # Own[genrec] rvalue off a protocol
                                    # receiver at a STORAGE sink -> bare
    "method.protocol_own_storage_ret",  # Own[record] rvalue off a protocol
                                    # receiver landing bare at a storage sink
    "method.scalar_tuple_ret",      # scalar-receiver stub's value-tuple
                                    # result at a storage/statement sink
    "arg.native_property_container",  # container-returning property read
                                    # bound bare at a native slot (len)
    "method.span_ret",              # Span-view method result renders bare
    "method.ru_wrapper_ret",        # recursive-union WRAPPER borrow method
                                    # result binding a wrapper slot inline
    "method.native_iter_ret",       # SpanIter-family value method result
                                    # landing bare in its storage decl slot
    "method.value_opt_view_ret",    # value-opt owned-view method result
                                    # landing bare in a storage decl slot
    "method.ptr_template_span",     # Ptr[T].span(n) template expansion --
                                    # by-value Span result lands bare
    "method.ptr_template_span_open_t",  # ... and its OPEN-element sibling,
                                    # inside a generic body
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
    "binop.typed_dict_in_total",    # total=True fold: (static_cast<void>(r), true)
    "arg.protocol_union_plain",     # ... the lowering arm that renders it
    "call.view_instantiation",       # Span/Array ctor over a bare source
    "call.view_ctor_value",          # str/bytes-VIEW instantiation at a
                                     # value/arg sink: the fold lands bare
    "call.generic_own_list_literal",  # list literal at a substituted
                                     # Own[list[T]] slot: inline brace
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
    "arg.own_container_comp",       # comprehension stmt-expr inline into an
                                    # Own[container] slot (prvalue moves in)
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
    "decl.storage_opt_name_lift",   # `first = it` off a storage-opt loop
                                    # var: the optional_to_ptr lift
    "decl.opt_name_addr",           # plain record lvalue into a ptr-repr
                                    # Optional slot -> the address-of lift
    "decl.opt_name_copy",
    # Native record-returning free-call local decl (`f = open(path)` ->
    # `::tpy::TextFile f = ::tpy::builtin_open(path);`) -- a plain-value decl,
    # single-assignment rvalue only.
    "decl.native_record_call",
    # The general rvalue-call storage decl (`Animal parent = cast(Animal, a);`,
    # `std::vector<uint8_t> data = r.read();`): an F1-record / owned container
    # result by value, single-assignment only.
    "decl.rvalue_storage_call",
    # The UNARY sibling of the row above (`neg = -v` -> `Vec2 neg = -(v);`):
    # an Own-returning user unary dunder is a fresh value, so the decl is the
    # plain spelled copy.
    "decl.rvalue_storage_unary",
    # Iterator-object local decl (`it = g()` / `it = obj.gen()` -> `auto it
    # = g();`): a generator/iterator factory result feeding the universal
    # __iter__/__next__ loop; single-assignment only.
    "decl.iterator_object",
    # REF_ALIAS from a borrow-record-returning call (lowering; the
    # `T& p = shared(x);` bind of the callee's returned reference).
    "decl.record_borrow_call",
    "decl.container_borrow_call",   # borrow container return binds the T&
                                    # alias (the record row's container twin)
    "decl.dunder_borrow_alias",     # the operator flavor: a borrow-returning
                                    # `__add__`/`__neg__` result binds the
                                    # alias -- `const Acc& c = ((a) + (b));`
    "ret.record_ifexpr",            # lvalue ternary at a record BORROW return
                                    # -> `return ((c) ? ((*this)) : (o));`
    # Open-T local from a T-returning call in a generic body (lowering;
    # the `::tpy::val_or_ref_t<T> item = box.get();` form-neutral bind).
    "decl.tparam_call",
    # `copy(x)` of an open-T source (lowering; the special-builtin arm's
    # general tail, `T(this->value)`).
    "call.copy_tparam",
    # `copy(x)` of a POINTER-FORM open-T name (a frame loop var; the same
    # general tail, whose name read spells the deref: `T((*x))`).
    "call.copy_tparam_ptr",
    # `copy(heap[pos])` of an open-T CONTAINER ELEMENT (lowering; the same
    # general tail over a subscript read, `T(heap[pos])`).
    "call.copy_tparam_elem",
    # `copy(x)` of a concrete container source (`copy(d.get(k, dflt))` ->
    # `std::vector<T>(<src>)`).
    "call.copy_container",
    # `copy(span)` of a Span NAME -> `std::span<T>(span)` (a view copy).
    "call.copy_span",
    # `copy(acc)` of a POINTER-LOCAL record source -> `Tag((*acc))` (the
    # general tail over the indirect read).
    "call.copy_record_ptr",
    # ... and its non-pointer twin, the container arm's record leg: a
    # record source no sink intercepts (a field read, a borrow-returning
    # call, an awaited borrow) -> `Point(h.brec())`.
    "call.copy_record",
    # The result-use GATE for both of those (admission, not render): the
    # `copy()` callee reaching the generic call tail at a storage / value /
    # borrow-bind sink.
    "call.copy_construct_ret",
    # The IMPLICIT copy an owning RETURN slot performs on a borrowed source
    # sema warned about -- the same copy-construct node, at the ladder tail.
    "ret.borrowed_copy",
    # `copy(s)` of a str NAME -> `std::string(s)` (the explicit owned copy).
    "call.copy_str",
    # ... the non-owned-str half of that row: a VIEW-resolved str source or
    # the bytes family, spelled at the family's owned type.
    "call.copy_viewfam",
    # ... and the correction inside it: an owned-resolved bytes source that
    # RENDERS as a span (a `bytes` param) -> `::tpy::Bytes(b)`, since
    # the owned vector has no span ctor.
    "call.copy_bytes_view_source",
    # `copy(big)` of a scalar NAME -> `::tpy::BigInt(big)` (the same
    # type-blind general tail; scalars are value types).
    "call.copy_scalar",
    # `copy((1, b))` of a whole tuple LITERAL with a reference element ->
    # the storage brace `std::tuple<int32_t, Box>{1, b}` (per-element copy).
    "call.copy_tuple_storage",
    # A REAL scalar-cast coerce over a local name at an Own[scalar] slot
    # binds the cast rvalue bare (no copy at the slot); the
    # generic coerce-template render is the whole emit.
    "call.own_coerce_cast",
    # A ptr-variant-BOUND union name at a value-variant Own[union] slot
    # copies the active member out (`to_value_variant<...>(p)`) -- the
    # Own-cascade's union lift, keyed on the binding set.
    "arg.union_value_lift",
    # Ptr[T] value-slot admission (bare passes / field reads share the
    # scalar renders, so the predicate is the only distinguishing site).
    "ptr.value_slot",
    # The @dynamic-protocol pointee arm of the same predicate (the
    # pointee spelling is the shared PtrType.to_cpp, so admission is the
    # only site distinguishing it from the record/scalar pointees).
    "ptr.dyn_proto_pointee",
    # `x = None` at a Ptr[T] value binding (lowering; the `nullptr` render).
    "decl.ptr_none",
    # `x = None` reassign at a value-repr Optional binding (`int | None` param):
    # the storage-form `std::nullopt` render.
    "decl.opt_none",
    # `a: V = None` at a recursive-union WRAPPER slot: the monostate member
    # absorbed by the wrapper's forwarding ctor (`V a = std::monostate{};`).
    "decl.wrapper_none",
    # Slot-hoist pointer-repr Optional local, None init: `T* x = nullptr;`.
    "decl.opt_slot_none",
    # Slot-hoist Optional local, F1-record rvalue init: `T __slot_N = ...;
    # T* x = &__slot_N;`.
    "decl.opt_slot_rvalue",
    # Slot-hoist Optional local, container-LITERAL init:
    # `std::vector<T> __slot_N = std::vector<T>{1, 2}; std::vector<T>* x =
    # &__slot_N;` (the list brace self-describes; dict/set spell their ctor).
    "decl.opt_slot_container_literal",
    "decl.opt_slot_proto_rvalue",   # Optional[dyn P] conformer rvalue slot
    # Owned-optional record slot from a storage-optional-returning call
    # (`std::optional<Rc<T>> upgraded = w.upgrade();`); the name registers
    # for the narrowed `(*name)` deref + has_value None-test reads.
    "decl.opt_record_call",
    "decl.view_inner_opt_call",     # `local = f()` at a VIEW-inner value-opt
                                    # slot: the plain spelled copy
    "decl.optview_shim",           # `local: Optional[str] = s` off a
                                    # borrow-form value-opt view param
    "decl.opt_value_record",        # Optional[value-record] slot: plain spelled copy
    # A registered owned-optional record local's NARROWED read -- the
    # `(*upgraded)` deref consumed as a receiver / member position.
    "name.opt_record_deref",
    # ...and its WHOLE-optional read (the bare name at a None-test).
    "name.opt_record_whole",
    # An Own param that became a plain resumable frame FIELD: body reads are
    # bare member reads, not the sync param's movable last-use render.
    "name.frame_own_field",
    # Escape-hoist PLAIN-record pointer-local, name-reassigned with a record
    # rvalue init: `T __slot_N = init;` + `T* x = &__slot_N;` (the
    # REBIND_SLOT render minus the rebind slot).
    "decl.record_slot_rvalue",
    # Reassigned container-ELEMENT borrow local's first decl: `[const] T* p =
    # &(::tpy::__getitem__(ps, i));` -- the decl twin of `reseat.subscript_elem`.
    "decl.subscript_elem_addr",
    "decl.ptr_name_addr",           # reassigned record-name alias: T* x = &(a)
    "decl.ptr_call_addr",           # reassigned borrow-call: T* x = &(f(a))
    # A CONTAINER-payload ptr-repr Optional decl slot (`std::vector<Tag>* x =
    # optional_to_ptr(fld)`) -- gate admission, same lift render as the
    # record payload.
    "decl.opt_ptr_container",
    "decl.opt_tuple_elem_lift",     # ptr-Optional tuple-field element decl:
                                    # optional_to_ptr(std::get<N>(h.t))
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
    "decl.alias_opt_ptr_deref_src",  # proven Optional-ptr source: T& a = (*p)
    # Reseat of a slot-hoist Optional local to None: `x = nullptr;`.
    "reseat.opt_call_pass",         # borrow-returning ptr-opt call reseat:
                                    # the bare pointer copy, no slot write
    "reseat.opt_none",
    "reseat.opt_inline_rvalue",  # slotless local: in-place plain block slot + later reuse
    "reseat.opt_ptr_copy",       # same-Optional borrow-name source: bare pointer copy
    # Rvalue reseat by sema's storage verdict: `(*x) = <rvalue>;` or
    # `x = &*(__slot_N = <rvalue>);` (THIRAssign's rebind arm).
    "reseat.opt_rvalue",
    # ... with an Own-returning user dunder operator as the rvalue
    # (`v = v + inc` -> `v = &*(__slot_N = (((*v)) + (inc)));`).
    "reseat.rvalue_op",
    # Lvalue reseat of a slotless Optional local: lift a bare record param or an
    # F1-record field source via `x = &(...);`.
    "reseat.opt_lvalue",
    "reseat.storage_opt_name_lift", # storage-opt LOCAL name reseat: the
                                    # optional_to_ptr lift, not a ptr copy
    "reseat.opt_field_lift",        # slotless opt local = optional_to_ptr(field)
    # Ptr-variant union local from a concrete-member rvalue: value-variant
    # `__slot_N` + `to_ptr_variant(__slot_N)`.
    "decl.union_slot_rvalue",
    # Ptr-variant union local from a concrete-member lvalue name:
    # `variant<A*, B*> v{&(name)};`.
    "decl.union_addr",
    # Union rvalue reseat through a slot of the site's own:
    # `__slot_N.emplace(...); v = ::tpy::to_ptr_variant(*__slot_N);`.
    "reseat.union_rvalue",
    # A read of a read-only-seeded same-module value global (lowering; the
    # bare-name render shared with locals, so the seed is what distinguishes).
    "name.global_seeded",
    # A same-module function used as a value (`apply(double, ...)`): the bare
    # escaped-name render on THIRName.cpp (the plain function-ref render).
    "name.func_ref",
    # A nested def's closure local read as a value (`push_back(add_offset)`):
    # the bare local name the nested def bound, not the module spelling.
    "name.closure_local",
    # A read of a read-only-seeded NATIVE-linkage value global (lowering;
    # the pre-rendered `::symbol` spelling on THIRName.cpp).
    "name.global_native",
    # A read of a read-only-seeded IMPORTED value global (lowering; the
    # pre-rendered `::tpyapp::mod::g` / native_cpp_name spelling on
    # THIRName.cpp -- imported_variable_cpp).
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
    # is a distinct overflow / direction shape).
    "range.step_plus_one",          # literal +1 step -> the ascending ++ loop
    "range.step_unit_neg",          # literal -1 step -> the descending -- loop
    "range.step_literal_pos",       # non-unit positive literal step
    "range.step_literal_neg",       # non-unit negative literal step
    "range.step_variable",          # fixed-int-name step (captured `__step_N`)
    # A COMPUTED fixed-int step (binop / call / field / ternary) at the same
    # `__step_N` capture. Split from the name leg so the name leg cannot
    # witness it: only the capture makes an arbitrary expression sound here
    # (Python evaluates the step once; the C++ head would re-read it).
    "range.step_variable_expr",
    # An owning return off a REASSIGNED (rebind-slot pointer) container
    # local, through the shared deref+move indirect-name arm (the record
    # half of the axis witnesses ret.record_ptr_local there).
    "ret.container_ptr_local",
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
    # An always-true walrus condition (`if (r := make(7)):` -- record/enum
    # target): the ALWAYS_TRUE wrap over the inline assign.
    "cond.walrus_always_true",
    # `items[0] += 5` on a user record with getitem/setitem: the operator[]
    # read + the fixed ::tpy::__setitem__ dunder write.
    "setitem.record_aug",
    # `isinstance(x, Animal)` on a bounded-T subject: the compile-time
    # isinstance_static trait, no extraction alias.
    "cond.isinstance_static",
    # Non-identity `_truthy_for_rendered` arms carried by THIRTruthy.
    "truthy.global_slot",           # ptr-repr Optional global: `!(g)`
    "truthy.nonempty",
    "truthy.is_truthy",
    "truthy.to_bool",
    "truthy.record_bool",
    "truthy.record_len",
    "truthy.always_true",
    "truthy.ptr_truthy",            # un-narrowed ptr-repr Optional[record]
                                    # with a dunder: ::tpy::ptr_truthy(x)
    "truthy.storage_opt_bare",      # its storage-form complement (field /
                                    # container element): the bare read
    "subscript.opt_record_truthy",  # the container half of that element read
    # @builtin_type record with a real body and no cpp_formatter (Poll;
    # Waker's formatter-carrying TypeDef stays excluded) admitted as an F1
    # record (lowering admission; the user-record spelling path, so every
    # render is shared).
    "recv.builtin_record",
    # A `Ptr[T]`-returning CALL receiver at a @cpp_template member
    # (lowering admission; the template expands over the receiver render,
    # shared with the NAME/FIELD receiver shapes).
    "method.ptr_template_call_recv",
    # A user-Deref chain receiver that is a NARROWED value-repr
    # `Own[wrapper] | None` NAME (lowering admission; the `(*name)` deref is
    # the shared narrowed-read render, so admission is the only
    # distinguishing site).
    "recv.deref_value_opt_name",
    # Conditional-expression renders (lowering; cond_pos records at local
    # admission -- the condition-position render is shared with the value
    # emit, so admission is the distinguishing site).
    "ifexpr.value",                 # scalar / char / enum result
    "ifexpr.str",                   # str-family result (form-tagged)
    "ifexpr.str_mixed",             # mixed view/owned arms: view-arm wrap
    "ifexpr.bytes",                 # view-result bytes ternary (BORROW span)
    "ifexpr.tuple",                 # tuple result: arms' form propagates
    "ifexpr.cond_pos",              # bool ternary as an if/while condition
    "ifexpr.optptr_call_arm",       # borrow-returning ptr-Optional call arm
                                    # passes its `T*` result bare
    "ifexpr.ptr_opt",               # ptr-Optional result: per-arm `T*`
                                    # normalization (nullptr / bare / lift /
                                    # addr-of), whole ternary BORROW
    "ifexpr.ptr_union",             # WIDE ptr-union result: per-arm
                                    # normalization (bare binding name /
                                    # to_ptr_variant field lift), BORROW
    "ifexpr.record",                # reference-axis lvalue ternary (record
                                    # or container): bare name / borrow-call
                                    # / element arms, a BORROW lvalue
    "ifexpr.record_prvalue",        # F1-record PRVALUE ternary (copy /
                                    # by-value call arms) at the MIL slot
    "decl.opt_ternary",             # OPTIONAL_TO_PTR local off a ternary:
                                    # binds the lowered `T*` ternary bare
    "decl.opt_storage_call",        # Own-declared optional call decl: the
                                    # storage slot + optional_to_ptr lift
    "reseat.opt_storage_field",     # its INLINE-slot reseat twin
    "decl.opt_storage_field",       # ... and its FIELD-of-rvalue sibling
                                    # (`v = make_holder(p).value`)
    "ret.value_ptr_opt_local",      # narrowed wide ptr-opt local at a value
                                    # return: deref + last-use move
    "arg.wide_opt_deref_name",      # narrowed wide ptr-opt name at its
                                    # pointee's borrow slot: deref pass
    "arg.nullable_proto_addr",      # container/record name at nullable
                                    # proto slot: address-of lift
    "arg.nullproto_none",           # typed-null None at nullable proto slot
    "ret.ptr_opt_field_addr",       # pointee-typed field at the ptr-opt
                                    # return: `&(this->_value)` lift
    "ret.generic_tuple_literal",    # sync generic-tuple return literal:
                                    # to_val_or_ptr element wraps
    "ret.generic_tuple_call",       # ... and the call rvalue of the SAME
                                    # generic tuple, returned bare (no wrap)
    "ret.container_borrow_global",  # pointer-slot global derefs into the
                                    # borrow return (no record counterpart:
                                    # a record global takes the ptr-local arm)
    "ret.ptr_opt_subscript",        # container-element subscript at the
                                    # ptr-opt return: `&(__getitem__(..))`
    "method.container_opt_ptr_ret", # dict.get's bare `T*` result (wide
                                    # pointee): consumers own the binding
    "method.container_union_ret",   # union-element rvalue popped into a
                                    # STORAGE sink (the frame-slot emplace)
    "decl.opt_call_passthrough",    # borrow-returning ptr-opt free call
                                    # bound bare at its decl (no slot)
    "print.opt_ptr_name",           # whole ptr-opt name arg:
                                    # ::tpy::print_optional(name)
    "res.return_copy_record",       # `return copy(x)` at an owned-record
                                    # async return: `C __tpy_async_ret =
                                    # C(x);` (the sync sink's copy row)
    "arg.opt_view_own_bare",        # narrowed owned value-opt VIEW local
                                    # at an Own[Optional[StrView]] slot:
                                    # bare whole-optional pass
    "arg.opt_strview_own_shim",     # VIEW-inner source into an owned
                                    # Optional element slot: the `__ov` shim
    "arg.opt_view_own_shim",        # un-narrowed twin: the once-evaluated
                                    # `__ov` statement-expression shim
    "arg.optview_param_own_elem",   # value-opt VIEW param at an owned
                                    # Optional element slot: the moved shim
    "truthy.value_opt_whole",       # narrowed value-opt name truthiness:
                                    # ::tpy::is_truthy on the WHOLE optional
    "ifexpr.value_opt",             # value-repr Optional ternary: both
                                    # arms wrapped in the spelled optional
    "ifexpr.value_opt_scalar_name",  # ... one of those arms a scalar name
    "ifexpr.storage_opt_elem",      # container-ELEMENT Optional ternary: the
                                    # storage wrap even at pointer repr
    "ifexpr.storage_opt_dict_arm",  # ... a dict-literal arm
    "ifexpr.storage_opt_tuple_arm",  # ... a value-tuple-literal arm
    "print.opt_ternary_tuple_arg",   # printed tuple literal with one
                                    # of those ternaries as an element
    "ret.value_opt_view_ternary",   # that ternary at the opt-view return
    "name.storage_opt_whole",       # storage-optional unpack target read
                                    # whole (bare std::optional lvalue)
    "name.opt_btuple_whole",        # nullable borrow-tuple local: the bare
                                    # optional binding (None test)
    "name.opt_btuple_deref",        # ... and the narrowed `(*t)` deref read
    "name.opt_vtuple_whole",        # value-repr Optional[value tuple] local:
                                    # the bare optional (None test / write)
    "name.opt_vtuple_deref",        # ... and the narrowed `(*t)` deref read
    "optptr.storage_name_lift",     # that name at a T* slot:
                                    # ::tpy::optional_to_ptr(p)
    "genexpr.unpack",               # genexpr tuple-unpack head (per-target
                                    # __tup_N binds in the lambda)
    "ret.ptr_opt_ternary",          # ptr-Optional return of a ternary
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
    "fstr.char_arg",                # char arg formats bare (`char` is
                                    # std::formattable; no int8 cast)
    "fstr.spec",                    # constant format spec -> `{:spec}`
                                    # placeholder (lowering, routed args only)
    "fstr.container_arg",           # tuple/list/dict/set arg -> to_str helper
    "fstr.container_call_arg",      # container-returning CALL under that wrap
    "fstr.user_arg",                # user record / bound type param -> __str__
    "fstr.union_arg",               # union arg -> runtime __str__ visitor
    # Sync `with` faces (lowering, per item / per statement).
    "with.manager_borrowed",        # lvalue manager: `auto& __ctx_N = ...`
    "with.manager_borrowed_field",  # field-access lvalue manager: `auto& __ctx_N = <obj>.f;`
    "with.manager_owned",           # rvalue manager: `auto __ctx_N = ...`
    "with.manager_hoist",           # kept owned manager: `__slot_N.emplace(...)` + `auto& __ctx_N = (*__slot_N);`
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
    "with.hoist_ptr_local",         # borrow-only non-value (hoisted with target) -> T* name;
    "with.manager_frame_ctx",       # leaf owned manager frame home: `__with_ctx_K.emplace(...)`
    "with.frame_slot_target",       # leaf frame_slot target: `<name>.emplace(__enter__());`
    "with.frame_field_target",      # leaf plain frame-field target: `<name> = __enter__();`
    "with.frame_btuple_target",     # ... its borrow-form tuple flavor
                                    # (`std::tuple<T*, ...>` frame member)
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
    "try.hoist_const_ptr",          # const borrow-decl hoist -> const T* name;
    "try.body_terminates",          # normal-path finally copy elided
    "try.hoist_borrow_tuple",       # branch-bound ptr-repr tuple predecl at
                                    # the try (the if cascade's sibling row)
    "try.hoist_mixed_own_tuple",    # ... whose sources are MIXED own+borrow
                                    # tuple CALLS: per-element arrow reads
    "try.finally_terminates",       # raise/return-ending finally: no rethrow
    # if/elif/else (lowering, per statement).
    "if.hoist_decl",                # sema-hoisted plain-value branch predecls
    "if.hoist_optional_storage",    # single-bind non-value -> optional<T> name;
    "if.hoist_borrow_tuple",
    # ... whose sources are MIXED own+borrow tuple CALLS: the predecl also
    # registers the per-element arrow the decl arm would have.
    "if.hoist_mixed_own_tuple",        # ptr-repr tuple -> std::tuple<..., T*> name;
    "if.hoist_opt_btuple",          # nullable borrow-tuple branch decl:
                                    # `std::optional<std::tuple<.., T*>> t;`
    "foreach.hoist_borrow_tuple",   # hoisted tuple loop var -> borrow predecl + lift bind
    "subscript.tuple_elem_recv",    # container tuple elem lvalue under std::get
    "setitem.record_method_rvalue", # d[k] = rc.clone() -- bare method rvalue value
    # A MIXED own+borrow tuple-returning CALL reseat: the hybrid render is
    # already the local's form, so it is a plain `p = make_mixed(c);`.
    "btuple.reseat_mixed_call",
    "btuple.reseat_literal",        # borrow-tuple local = REF-capture literal
    "btuple.reseat_lift",           # borrow-tuple local = tuple_to_pointer(lvalue)
    "btuple.reseat_emplace",        # owning-call reseat of a HOISTED btuple:
                                    # t = tuple_to_pointer<..>(__slot_N
                                    # .emplace(make_pair(9)))
    "decl.opt_btuple_none",         # nullable borrow-tuple local: nullopt
    "decl.opt_btuple_lift",         # ... storage source: optional-wrapped
                                    # tuple_to_pointer lift
    "decl.opt_btuple_slot",         # ... owning call: __slot emplace alias
    "reseat.opt_btuple_none",       # reseat rows of the same family
    "reseat.opt_btuple_lift",
    "reseat.opt_btuple_slot",
    "btuple.reseat_emplace_emit",   # the emit half of the same reseat
    "btuple.reseat_name_copy",      # sibling btuple local: bare `u = t;`
    "if.hoist_ptr_local",           # reassigned/borrow non-value -> T* name;
    "if.hoist_const_ptr",           # const borrow-decl -> const T* name;
                                    # (the with/try const_pointer rung)
    "if.hoist_dyn_protocol",        # @dynamic branch decl -> Base* name;
    "reseat.opt_storage",           # plain assign into an OPTIONAL_STORAGE hoist
    "reseat.opt_storage_comp",      # ... its comprehension source (the whole
                                    # stmt-expr assigns into the optional)
    "reseat.branch_rvalue",         # lazy-slot rvalue reseat of a branch hoist
    "reseat.rvalue_comp",           # a comprehension source at any slot
                                    # rebind (`&*(__slot_N = ({...}))`)
    "reseat.storage_name",          # `x = base;` -> `x = &(base);` lvalue lift
    "reseat.param_name",            # `x = b;` over a record param's T& lvalue
    "reseat.borrow_call",           # `x = pick(s);` -> `x = &(pick(s));`
    "reseat.subscript_elem",        # `p = xs[i];` -> `p = &(__getitem__(...));`
    "reseat.opt_storage_lift",      # `alias = p;` off an OPTIONAL_STORAGE local -> `optional_to_ptr(p)`
    "reseat.narrow_alias_addr",     # `form = data;` off a narrowed union ->
                                    # `form = &(__data);`
    "reseat.dunder_borrow",         # `c = a + b;` -> `c = &(((a) + (b)));`,
                                    # the operator flavor of reseat.borrow_call
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
    # `(recv.__contains__(needle))`, the member-call tail.
    "binop.user_membership",
    # native-set membership with no resolved __contains__ member (a
    # `readonly[set]`) -> the `is_native_in` fallback
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
    "binop.container_eq",           # same-type container ==/!= -> the bare
                                    # operator (the @dataclass __eq__ chain)
    "containerlit.open_tparam_elem",  # a `T` PARAM at a `T` element slot ->
                                    # `::tpy::param_to_storage<T>(v)`
    "arg.open_tparam_storage",      # a `T` PARAM at an `Own[T]` element slot
                                    # -> `::tpy::param_to_storage<T>(v)`
    "return.open_tparam_param",     # `return v` of a `T` PARAM at a
                                    # `val_or_ref_t<T>` return ->
                                    # `::tpy::param_to_return<T>(v)`
    "binop.open_tparam_eq",         # `x == v` between two open `T` values ->
                                    # `::tpy::eq`, form-neutral across the
                                    # instantiation's param/storage spellings
    # A both-literal int binop folded in the target-less BigInt context
    # (`2**63 - 1` -> the folded BigInt-targeted literal render).
    "binop.literal_fold",
    "binop.literal_fact_fold",      # ==/!= or &&/|| chain decided by a
                                    # LiteralType flow fact: bare true/false
    "narrow.literal_branch_facts",  # LiteralType branch facts seeded into
                                    # the flow-sensitive literal_facts map
    # sema's optional_safe_eq (value-repr Optional[scalar] ==/!=): optional
    # sides read bare, the plain side opposite an un-narrowed optional is
    # target-typed to its inner (lowering).
    "binop.opt_scalar_eq",
    # A chained comparison with a non-simple intermediate (`a < f() < b`) ->
    # the GCC stmt-expr single-eval form.
    "chained_compare.stmt_expr",
    # Method call on a bare protocol receiver: `p.m(args)`,
    # monomorphized for a structural protocol, a vtable call for a @dynamic
    # one -- one render either way. Args take the FREE-call literal rules
    # (the generic `_args()` fallback loop, not the user-record loop).
    "method.protocol",
    # A zero-arg @cpp_template dunder stub on a protocol value
    # (`it.__next__()` on `Iterator[T]`): the shared template expansion
    # over the same receiver render.
    "method.protocol_template",
    # A pointer-repr Optional slot arg on the protocol ladder (`s.total(d)` at
    # a `dict | None` param -> `&(d)`) -- the record ladder's row.
    "method.protocol_optional_ptr",
    # A `Ptr[T]` RESULT on the protocol ladder (`lock._raw_mutex()`): the
    # by-value pointer renders bare, the ptr-template ladder's return row.
    "method.protocol_ptr_ret",
    # An ENUM arg into a ptr / @native-record template slot
    # (`_raw.compare_exchange(.., MemoryOrder.SEQ_CST)`): one enum_cpp_name
    # spelling, so it interpolates bare like a scalar.
    "method.ptr_template_enum_arg",
    # ... and its OPEN type-param sibling: a bare `T` arg takes no
    # borrow/storage lift, so the slot renders the name.
    "method.ptr_template_tparam_arg",
    # ... and a str-family NAME arg: the binding renders as the bare name
    # (a str LITERAL stays out -- the param_view_t question).
    "method.ptr_template_record_arg",
    "method.ptr_template_str_name_arg",
    # An open-T tuple RESULT of the same family (`tuple[bool, T]` off the
    # @native CAS): borrow and storage coincide, the plain spelled copy.
    "method.ptr_template_open_t_tuple_ret",
    "method.opt_dyn_recv",
    "method.opt_ptr_container_recv",  # narrowed ptr-repr Optional[container]
                                    # receiver dispatches on the payload          # narrowed Optional[dyn P] pointer receiver
    # A scalar value into a bare `T` method slot (a pending deferred
    # generic's raw params): bare render, `val_or_ref_t<T>` binds by value.
    "method.tparam_scalar_arg",
    # ... and its still-OPEN sibling: a param typed as the SAME `T` as the
    # slot forwards bare (`Atomic[T].store` -> `_raw.store(value, order)`).
    "method.tparam_open_pass_arg",
    # A borrow-tuple method result at the `auto` btuple decl slot
    # (`auto p = m.pair(c);`) -- the record-method twin of call.btuple_slot.
    "method.btuple_slot",
    "method.union_subject_ret",     # ptr-variant union return at the match dispatch-local sink
    "call.union_subject_ret",       # the free-call twin
    # A MIXED owned+borrow tuple call result aliased whole at the `auto`
    # decl (`auto x = m.mixed(c);`): owned elements by value, ref elements
    # as pointers; reads pick `.` vs `->` per element.
    "decl.mixed_own_alias",
    "ifexpr.mixed_own_call",        # ternary of two mixed-own-tuple calls:
                                    # both arms bare, mixed render composes

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
    # THIRNestedDef (lowering): a nested `def` -> a lambda emit,
    # capture list spelled from sema's node facts.
    "stmt.nested_def",
    # A nested def with a param DEFAULT: unreachable (sema requires the
    # argument at every call), so the lambda param list carries none.
    "nesteddef.unused_default",
    # (emit) A nested def whose own body produced hoist lines: they drain at
    # the lambda's prologue, not the enclosing body's.
    "stmt.nested_def_hoist",
    # THIRLambda (lowering): a `lambda` expr -> a C++ closure;
    # by-ref capture, non-void or void body.
    "expr.lambda",
    # A pointer-repr tuple lambda return spells the borrow form
    # (to_cpp_return) -- the same-typed bare-call body slice.
    "lambda.btuple_ret",
    # The generic call whose val_or_ptr_t tuple IS that closure's
    # borrow-form return (the lambda_btuple_ret use row).
    "call.lambda_btuple_ret",
    # An F1-record container element streams RAW at a print arg
    # (`<< ::tpy::__getitem__(d, k)`) -- the subscript sibling of
    # print.record_field.
    "print.record_subscript",
    # Standalone `a, b = <name>` unpack of a value-scalar tuple (lowering):
    # `const auto& __tup_N = name;` + per-target scalar decls.
    "stmt.tuple_unpack",
    # A non-name unpack source (lowering): a value-tuple-returning call or a
    # value-tuple field read -> `auto __tup_N = <expr>;` (value capture).
    "stmt.tuple_unpack.rvalue_source",
    # A value-repr Optional[value tuple] NAME read under its None narrow: the
    # holder const-ref-binds the name arm's deref read
    # (`const auto& __tup_N = (*r);`).
    "stmt.tuple_unpack.narrowed_value_opt_source",
    # An Own[F1-record] unpack element moved out of a call-rvalue source
    # (lowering): `Rec a = std::move(std::get<i>(__tup_N));`.
    "stmt.tuple_unpack.own_target",
    # The for-head twin over a STORAGE (own-element) gen-call yield tuple:
    # the owned local moves out of the mutable copy-head.
    "foreach.own_unpack_move",
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
    "stmt.tuple_unpack.field_wrap_source",  # `a, b = h.pair` -- the member
                                    # lifts via tuple_to_pointer
    "stmt.tuple_unpack.call_borrow_source",  # `a, b = both(t1, t2)` -- the
                                    # borrow-form call result captures via
                                    # the plain RVALUE bind, no lift
    # `ref, owned = split(p)` -- a MIXED borrow+Own call result. Admission is
    # the only distinguishing site: the same RVALUE capture serves both slot
    # shapes, so the ref/move target renders are the shared ones.
    "stmt.tuple_unpack.own_ref_mix_source",
    # A pointer-repr Optional[F1-record] unpack target off a borrow-form tuple
    # param: a plain nullable-pointer local `const T* a = std::get<i>(__tup_N);`
    # (const tracks the source param), registered as a pointer-optional local so
    # its None-test / narrowed reads ride the `T | None` param machinery.
    "stmt.tuple_unpack.opt_ptr_target",
    # An `Own[P] | None` STORAGE element target: the plain
    # `std::optional<P> a = std::get<i>(__tup_N);` value copy, registered
    # RECORD-kind so has_value / `(*a)` reads ride the value-opt arms.
    "stmt.tuple_unpack.value_opt_record_target",
    # ... and the owned-inner `Optional[str/bytes]` element target: the
    # same value copy, registered VIEW-kind.
    "stmt.tuple_unpack.value_opt_view_target",
    # Own[A | B] element target: `::tpy::Union<A*, B*> p =
    # ::tpy::to_ptr_variant(std::get<i>(__tup));` -- the per-element lift.
    "stmt.tuple_unpack.ptr_variant_target",
    # A recursive-wrapper element target: `Tree<int32_t>& a =
    # ::tpy::unwrap_ref(std::get<i>(__tup_N));` -- a reference alias.
    "stmt.tuple_unpack.unwrap_ref_target",
    # A reused plain scalar/str target (lowering): `a = std::get<i>(__tup_N);`
    # -- the declared-name assign tail, no decl.
    "stmt.tuple_unpack.assign_target",
    # An Own[F1-record] element landing in a module-level POINTER-SLOT global:
    # `static T __global_slot_N = std::move(std::get<i>(__tup_N));` +
    # `g = &__global_slot_N;` (the pointer-local reassign tail).
    "stmt.tuple_unpack.global_slot_target",
    # THIRComprehension (lowering, the C1+C2 slice).
    "comp.list",                    # list comp -> vector stmt-expr
    "comp.set",                     # set comp -> ordered_set stmt-expr
    "comp.dict",                    # dict comp -> insert_or_assign loop
    "comp.range",                   # 1/2-arg counter loop arm
    "comp.begin_end",               # __obj/__beg/__end container loop arm
    "comp.reserve",                 # sized begin/end list reserve line
    "comp.filter",                  # &&-joined `if (conds)` wrapper
    "comp.filter_walrus_leak",      # PEP 572 filter-walrus target bound
                                    # in the enclosing scope
    "comp.unpack",                  # inline __tup_N tuple-unpack binding
    "comp.range3",                  # 3-arg range: begin/end over the Range object
    "comp.field_iter",              # field-access iterable (recv.items)
    "comp.print_arg",               # comprehension print arg (container printer wrap)
    "comp.container_value",         # dict-comp list/Array VALUE slot: self-typed
                                    # brace-init literal / nested comp value
    "comp.nested",                  # comprehension VALUE inside a dict comp ->
                                    # the recursive stmt-expr render
    "comp.storage_opt_elem",        # ptr-repr Optional[F1] loop var: storage
                                    # binding registered for the body walk
    "comp.combinator_source",       # zip/map/filter/reversed/enumerate/iter
                                    # rvalue source: owning capture, begin/end
    "comp.storage_opt_const_elem",  # ... bound off a CONST source, so the
                                    # lift-decl twin spells `const P*`
    "argtemp.comprehension",        # slot-typed comp ArgTemp at a plain
                                    # container ref slot, free call or ctor
                                    # (accept([x for ..]), Flat([x for ..]))
    "arg.borrow_tuple_field",       # storage F3-tuple field wrapped
                                    # tuple_to_pointer at a borrow-tuple slot
    "arg.borrow_tuple_subscript",   # the container-element twin: a checked
                                    # element read through the same wrap
    "arg.btuple_storage_name",      # storage-form tuple LOCAL (loop var /
                                    # storage-bound local) through the wrap
    "arg.btuple_mixed_name",        # mixed-own-tuple local at a borrow-
                                    # tuple param slot -> tuple_to_pointer
    "subscript.value_record_elem",  # ValueType-record element read copied
                                    # into a by-value storage slot
    "subscript.value_tuple_source",  # value-tuple element as an unpack source
    "subscript.open_t_tuple_source",  # its open-T flavor inside a generic
                                    # body -- same self-contained element
    "subscript.value_union_elem",   # value-variant union element read
                                    # bare into its same-union sink
    "subscript.value_tuple_elem",   # value-TUPLE container element read
                                    # whole (the union row's sibling)
    "call.readonly_scalar_ret",     # readonly[scalar] call result -- a
                                    # const value copy, scalar everywhere
    "call.container_recv_ret",      # container call result consumed as
                                    # the next receiver (free-call twin)
    "subscript.call_recv",          # container-returning CALL receiver ->
                                    # the checked dunder over the inline
                                    # call render (READ-only)
    "binop.value_select_bytes",     # bytes-family and/or result -- the str
                                    # row's twin (.empty() truthiness, the
                                    # view-vs-owned form split)
    "binop.value_select_span",      # Span[T] and/or result: a value view
                                    # copied like a scalar (__len__ test)
    "binop.record_dunder_operand",  # record compared against a NON-record
                                    # operand its own dunder declares
                                    # (`__eq__(self, other: str)`)
    "subscript.ternary_recv",       # container TERNARY receiver of bare
                                    # NAME arms -- the call row's sibling
                                    # (READ-only, lvalue arms only)
    "subscript.borrow_tuple_elem",
    # An OPEN value-tuple container element at a borrow-bind position
    # (`copy(src[0])` on `list[tuple[T, int]]`): the same bare read.
    "subscript.open_tuple_elem",  # borrow-form tuple element at a
                                    # BORROW_BIND sink (the arg wrap)
    "subscript.record_elem_borrow", # checked F1-record element lvalue at a
                                    # BORROW_BIND sink (record ref-slot arg)
    "subscript.recv_field_chain",   # `self.scene.objects[i]` -- a multi-level
                                    # plain-record field chain as the
                                    # container receiver
    "subscript.recv_ptr_field",     # `sector.flags[i]` -- a container member
                                    # off an explicit Ptr[record] binding
    "deref.ptr_field_recv",         # `hit.material.bounce(x)` -- a Deref
                                    # wrapper read through a Ptr[record]
                                    # field hop
    "arg.record_deref_field",       # `base.mul(b.material.color)` -- a record
                                    # field read through a Deref wrapper hop,
                                    # bound bare by a record ref slot
    "ifexpr.record_prvalue_name_arm",  # `V3(0) if e is None else e` at a ctor
                                    # member-init: a record NAME arm beside a
                                    # prvalue arm, the ?: still a prvalue
    "own.container_call_rvalue",    # `table.append(make_row(1.0))` -- a
                                    # container-returning FREE call bound
                                    # bare by an Own[container] slot
    "arg.record_borrow_call",       # T&-returning call bound inline at a
                                    # record ref slot (bump(find_first(..)))
    "arg.record_elem_subscript",    # checked element lvalue bound inline at
                                    # a record ref slot, every call family
                                    # (Player(things[0]), add_a(a.bs[0]))
    "arg.recursive_union_borrow_call",  # the wrapper-slot twin
                                    # (show(v.inner.get()) at `const Value&`)
    "arg.native_protocol_tuple_literal",  # tuple literal at a native
                                    # protocol slot: its own storage type
    "arg.pending_str_slot",         # unresolved view-var str slot admitted
                                    # through the resolver, not the spelling
    "arg.protocol_record_field",    # concrete F1-record field at a still-
                                    # protocol template slot -> bare member
    "arg.native_protocol_open_field",  # open-T field at an unsubstituted
                                    # protocol slot: the bare member read
    "arg.native_protocol_field",    # bare optional/record field read at a
                                    # native protocol slot (repr_of(this->f))
    "arg.type_ctor_protocol_field",
    # An INT-kind type-param arg at a scalar type-ctor slot (`int32(N)` under
    # `[N: int]`): the non-type template parameter passes bare.
    "arg.type_ctor_int_tparam",  # the same bare member read on the
                                    # TYPE-CTOR arg loop (str(p.name))
    "arg.native_value_tuple_field",  # value-tuple field passed whole at a
                                    # native tuple slot (tuple_to_str(f))
    "arg.native_slice_subscript",   # list/Span slice subscript inline at a
                                    # native slot (__len__(list_slice(..)))
    "field.value_tuple",            # value-tuple field read consumed whole:
                                    # bare member, forms coincide
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
    "if.nullproto_guard",           # is-not-None constexpr swap on
                                    # nullable proto param
    "fold.overload_block",          # per-@overload-stub dead-branch fold splice
                                    # (if-chain flatten / folded match arm)
    "fold.overload_bind",           # folded match arm's capture binding
                                    # (`auto`/`auto&` name = subject.field)
    "fn.param_copy_viewfam",        # view-family reassigned param: the
                                    # owned-copy init respell
    "vararg.view_elem_copy",        # view-source str vararg element: the
                                    # owned-copy wrap into the std::array
    "vararg.bytes_elem",            # bytes vararg element: bare (the
                                    # bytesview_to_bytes coerce IS the copy)
    "fn.param_copy",                # reassigned const-ref param -> the mutable
                                    # owned-copy prologue (THIRParamCopy)
    "fn.macro_staticmethod",        # macro-authored staticmethod: record-owned
                                    # via self_type alone, is_method clear
    "fn.overload_template_stub",    # per-stub emission over a set with a
                                    # protocol-param (template-header) stub
    "argtemp.genexpr_proto",        # genexpr at a structural protocol slot:
                                    # the un-spelled auto make_generator temp
    "optptr.container_temp",        # container literal at an Optional
                                    # [container] slot: typed temp + &(...)
    "optptr.scalar_temp",           # scalar rvalue at an Optional[scalar]
                                    # slot: typed temp + &(...)
    "method.array_value_ret",       # VALUE Array method result lands bare
                                    # (std::array prvalue, span-like)
    "call.array_value_ret",         # ... and the free-call twin
    "reseat.opt_frame_slot",        # resumable rvalue reseat via the
                                    # prescanned frame-field slot (&*(f=..))
    "reseat.opt_frame_in_place",   # resumable Optional-ptr rvalue reseat
                                    # assigning through the pointer (IN_PLACE)
    "reseat.opt_frame_storage_call",  # ... and the Own-opt-call sibling:
                                    # field fill + optional_to_ptr re-lift
    "genexpr.range",                # the range-source counter lambda
                                    # (1/2/3-arg, step checks on 3-arg)
    "genexpr.filter",               # &&-joined filter conditions wrapping
                                    # the yield inside the lambda
    "genexpr.native_iterable",      # genexpr into a native Iterable consumer ->
                                    # the make_generator IIFE (all/any/sum arg)
    "print.none_literal",           # print(None): the bare "None" string
                                    # literal
    "print.optval",                 # un-narrowed value-repr Optional[scalar/str]
                                    # print arg -> bare `::tpy::print_optional_val`
    "print.optval_fmt",             # ... with an explicit Formatter template
                                    # (bytes / tuple / Array / Span inner)
    "print.wrap_arg",               # container / value-tuple / F1-record NAME
                                    # print arg -> its kind-keyed printer wrap
    "print.tuple_record_elem",      # std::get<i>(t) record element at a
                                    # print sink; deref iff borrow-form
    "print.tuple_literal_storage_arg",  # all-RVALUE-element tuple literal at
                                    # the print sink: the storage spelling,
                                    # not the sibling arm's borrow one
    "foreach.range_object",       # zero-literal-step range: the generic
                                  # begin/end loop over the Range object
    "foreach.copy_iter_call",     # copy_iter(...) call iterable: the owning
                                  # begin/end capture of the iterator object
    "foreach.self_iterable",      # `for x in self:` on a user-iterator
                                  # record: the (*this) capture
    "foreach.open_t_param",       # open-T PARAM iterable (Iterable-bound
                                  # T): the universal loop over the bare
                                  # param lvalue
    "foreach.narrowed_opt_dict",  # proven-narrowed ptr-opt dict param
                                  # iterable: the (*d) capture into the
                                  # universal key loop
    "foreach.narrowed_opt_listset",  # the list/set families of the same
                                     # capture (the loop render is
                                     # family-blind)
    # Proven-narrowed ptr-opt container NAME under a dict-view call
    # (`for v in items.values():` at `dict | None`): the bare `(*items)`
    # deref feeding the same view render as an unwrapped receiver.
    "iter.narrowed_opt_container_view",
    # `d.keys()` on a dict whose VALUE family the row below does not name
    # (`dict[str, JsonValue]`): the view render and the key the consumer
    # binds are both value-blind, so only the key slice decides.
    "iter.dict_keys_value_blind",
    "foreach.genexpr_iterable",   # genexpr iterable: the make_generator
                                  # lambda's rvalue owning capture
    "foreach.list_repeat_local",  # lazy repeat local iterable: the
                                  # universal loop over the lvalue capture
    "print.opt_ptr_call",         # ptr-repr Optional-returning call print
                                  # arg: print_optional over the bare T*
                                  # call result
    "print.record_name",          # F1-record NAME print arg: the raw
                                  # operator<< stream, pointer bindings
                                  # deref
    "print.self_arg",             # `print(self)` -> the `(*this)` receiver
                                    # streamed raw via operator<<
    "print.wrap_field_arg",         # container FIELD read print arg -> its
                                    # kind-keyed printer wrap (ListPrinter(m.f))
    "print.bytes_field",            # bytes-family field read print arg -> bare
                                    # `.field` inside a BytesPrinter wrap
    "print.tuple_subscript_arg",    # value-tuple subscript read print arg ->
                                    # TuplePrinter(std::get<N>(t))
    "print.container_literal_arg",  # list/Array LITERAL print arg: the
                                    # typed brace-init inside ListPrinter
    "print.tuple_literal_arg",      # pointer-repr tuple LITERAL print arg:
                                    # the borrow-form render inside
                                    # TuplePrinter
    "print.container_slice_arg",    # list/Array/Span slice read print arg ->
                                    # ListPrinter(list_slice/list_stepped_slice)
    "print.file_ternary_sink",      # `file=` sink is a ternary of two
                                    # admitted sink reads
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
    "field.opt_deref_check_read",   # unproven opt-scalar field operand unwrap
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
    "setitem.optview_whole",        # whole Optional[str/bytes] value store
    "setitem.record_tuple_call",    # record-tuple call value: tuple_to_storage
    "setitem.bytes_owned_copy",     # view-form bytes source -> Bytes(...)
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
    "match.scalar_rvalue_subject",  # switch-tier non-lvalue subject: `auto` copy
    "match.hoist_ptr_local",        # capture hoist: `T* name;` borrow-only form
    "match.hoist_ptr_const",        # nested-reuse const rung: the shared
                                    # capture slot spells `const T*`
    "match.bind_reuse_ptr",         # a nested match re-seats an enclosing
                                    # match's `T*` capture hoist
    "match.hoist_ptr_slot",         # rvalue-reassigned hoist: T* + rebind slot
    "match.hoist_opt_ptr_local",    # ptr-repr Optional capture hoist: `T* name;`
    "match.hoist_opt_ptr_frame",    # resumable twin: the P* frame member is
                                    # the binding; no decl line
    "match.field_bind_opt_ptr_frame",  # the capture assign lifts through
                                       # optional_to_ptr into that member
    "match.hoist_optional_storage",  # capture hoist: `std::optional<T> name;` slot
    "match.if_elif_guarded",        # standalone-if + goto __match_end_N (M3b)
    "match.guard_isinstance",       # a bare isinstance guard -> the holds
                                    # test (the if-condition render)
    "match.guard_const_fold",       # sema decided the guard statically; it
                                    # renders as a bare `true`/`false`
    "match.guard_arm",              # a guarded arm's inner `if (guard)`
    "match.switch_guard_chain",     # in-switch guard chain (grouped entries)
    "match.default_goto",           # all-guarded group -> goto __match_default_N
    "match.switch_union",           # switch (subject.index()) over variant tags
    "match.union_wrapper_value",    # wrapper subject: switch/get over `.value`
    "match.guarded_union_wrapper",  # the guarded tier's wrapper subject:
                                    # the same `.value` switch/get respell
    "match.union_alias",            # `auto& __case_i = [*]std::get<idx>(...)`
    "match.union_none_arm",         # `case None:` -> the monostate index
    "match.union_default",          # wildcard/capture -> `default:` in place
    "match.or_wildcard_default",    # `case 1 | _:` -- an or-group holding a
                                    # wildcard/capture IS the always-match arm
                                    # (the union/switch `default:` block, the
                                    # chain's `} else {`, the str tier's
                                    # trailing arm)
    "match.guarded_union",          # per-index guard groups + goto end (M4b)
    "match.if_elif_record",         # record-subject unguarded chain
    "match.guarded_record",         # record standalone-if + goto tier
    "match.record_or",              # or-pattern of condition-only class alts
    "match.field_cond",             # literal field condition (`==` compare)
    "match.field_cond_as",          # `f=<lit> as x`: the condition plus the
                                    # `as` name aliasing the tested field
    "match.field_none",             # field=None -> has_value/monostate check
    "match.field_bind",             # field capture: `{base}.{f}` rhs binding
    "match.field_bind_assign_addr",  # ptr-hoisted field capture: the
                                    # address-of lift `name = &({base}.{f});`
    "match.field_bind_opt",         # VALUE-repr Optional field capture:
                                    # the `auto&` alias registered as a
                                    # value-opt binding (scalar/view kind)
    "arg.literal_scalar_slot",      # resolved scalar at a Literal[...] slot
    "ctor.own_scalar_peel",         # nested ctor tail: Own[scalar] no-op peel
    "ctor.own_str_literal",         # str literal bare into an Own[str] slot
    "ctor.ru_wrapper_own_literal",  # scalar/str literal bare into an
                                    # Own[wrapper] ctor slot (V&& prvalue)
    "ctor.own_bytes_literal",       # bytes literal bare into an Own[bytes]
                                    # slot (the owned literal spelling)
    "ctor.bytes_literal_value_opt",  # ... and into a value-repr
                                    # Optional[bytes] slot
    "method.bytes_literal_value_opt",  # ... the record-method twin of that
                                    # row (`h.store(b"abc")`)
    "gentuple.field_elem",          # `self.<field>` at an open-T generic
                                    # tuple-literal element slot
    "decl.open_t_tuple_slot",       # `tuple[T, int32]` decl slot inside a
                                    # generic body: the plain spelled copy
    "method.protocol_open_t_tuple_ret",  # open-T tuple RESULT of a
                                    # protocol method (`s.pair()`)
    "arg.open_t_tuple_literal",     # tuple literal at an open-T tuple arg
                                    # slot -> the generic val_or_ptr builder
    "ctor.tuple_literal_value_opt",  # tuple literal (spelled brace-init)
                                    # into a value-repr Optional[tuple] slot
    "ctor.bytearray_rvalue",        # bytearray-returning call bare into a
                                    # plain bytearray ctor slot (no temp)
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
    "match.bind_as_capture",        # `case x as y:` -> two binding lines
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
    "res.frame_opt_none",           # `v = None` at a value-repr Optional
                                    # FRAME field -> storage-form nullopt
    "res.frame_union_call",         # same-union value-union call lands bare
                                    # in the frame field (a = remake())
    "res.yield_value_opt_none",     # `yield None` at a value-repr Optional
                                    # yield slot -> std::nullopt
    "res.branch_cond",              # Branch terminator condition render
    "res.await_args",               # sub-coro emplace argument renders
    "res.return_value",             # ReturnT value render for _make_async_return
    "res.nested_return",            # return in a leaf compound (skeleton hook)
    "res.postif_narrow",            # early-return narrowing leaf if (post-if alias)
    "res.finally_stop",             # generator helper return (__finally_stop pair)
    "res.branch_frame_write",       # branch-nested plain frame-field decl
    "res.branch_btuple_write",      # branch-nested borrow-tuple frame decl
    "res.branch_frame_slot_write",  # branch-nested frame_slot emplace decl
    "res.branch_block_local",       # branch-nested decl of a non-frame local
    "res.leaf_try_except",          # except-only leaf try (sync tiers mid-state)
    "res.leaf_match_sync",          # non-suspending leaf match (sync tiers)
    "res.yield_container_borrow",   # container yield of a frame_slot /
                                    # pointer-form / alias name (*buf)
    "res.yield_container_param",    # container yield of a PARAM name, bare
    "res.decl_no_init",             # annotation-only frame-field decl, no code
    "res.yield_container_ternary",  # ternary of frame-slot containers hands
                                    # out the branch-picked deref borrow
    "res.yield_container_field",    # `yield self.a` at a container slot reads
                                    # the storage member bare
    "res.yield_record_field",       # `yield self.a` at a record slot reads
                                    # the storage member bare
    "res.yield_record_ternary",     # ternary of frame-slot records hands out
                                    # the branch-picked deref borrow
    "res.yield_own_ctor",           # ctor call at an OWN record yield slot:
                                    # the storage render (`return Node(i);`)
    "res.yield_own_rvalue",         # call DECLARING `-> Own[T]` at an OWN
                                    # yield slot: the same storage render
    "res.btuple_yield_generic",     # generic tuple literal yield -- spelled
                                    # val_or_ptr_t brace-init + to_val_or_ptr
    "res.btuple_yield_elem_lift",   # container-element source at the btuple
                                    # yield slot: tuple_to_pointer over the
                                    # checked element read
    "res.btuple_yield_storage_name",  # owning frame_slot tuple NAME at its
                                    # own STORAGE slot: the deref'd read,
                                    # still live after the yield
    "res.btuple_yield_storage_name_move",  # ... dead after the yield: the
                                    # slot moves out instead of copying
    "res.btuple_yield_storage_name_lift",  # ptr-to-storage tuple loop var at
                                    # a POINTER-REPR slot: tuple_to_pointer
                                    # over the deref'd read
    "res.frame_unpack",             # frame-target tuple unpack (rvalue source)
    "res.unpack_union_elem",        # value-tuple call source with a value-
                                    # union element at the frame unpack
    "res.unpack_opt_ptr",           # optional_to_ptr unpack target bind
    "res.unpack_oneshot",           # await-lift one-shot unpack (auto&& move-out)
    "res.frame_tuple_literal",      # value-tuple literal at a bare frame field
    "res.frame_own_tuple_literal",  # literal at a fully-owned frame slot
    "res.frame_mixed_tuple_literal",  # literal at a MIXED-own frame slot
    "res.alias_bind",               # pointer-alias frame bind (= &(<lvalue>)
                                    # or the bare alias-of-alias pointer copy)
    "res.nested_def_member",        # frame nested def -> the marker-line stmt
    "res.nested_def_body",          # frame nested def MEMBER body lowered
    "res.return_self_borrow",       # `return self` at a Poll<T*> slot: &(__self)
    "res.return_tuple_literal",     # value-tuple literal at the async return slot
    "res.return_generic_tuple",     # generic tuple literal at the async return slot
    "res.loop_ptr_bind",            # pointer-form loop var admitted (T* reads)
    "res.loop_slot_bind",           # frame_slot loop var admitted ((*x) reads)
    "binop.poly_inline_narrow",     # inline poly-isinstance under && (spelled static_cast RHS)
    "truthy.optional_field_whole",  # truthy Optional field condition (is_truthy over raw storage)
    "mil.demote_probe",             # dynamic MIL demote (the probe registers a temp)
    "res.return_opt_record_none",   # storage Optional[record] async return of None (nullopt)
    "res.return_ptr_opt_field",     # ptr-repr Optional field lift at the BORROW async return
    "res.poly_cond",                # poly isinstance Branch cond (no-alias dynamic_cast check)
    "res.for_narrowed_opt_src",     # narrowed value-opt iterable, bare leaf
                                    # (skeleton unwraps)
    "res.for_narrowed_opt_ptr_src", # narrowed PTR-repr container local at
                                    # the for head: the leaf carries (*h)
    # Same, for a narrowed Optional FIELD iterable: the leaf hands over the
    # member read with its own narrowed unwrap stripped.
    "res.for_narrowed_opt_field_src",
    "res.loop_tuple_bind",          # value-tuple holder loop admitted
    "res.loop_btuple_bind",         # proxy-ref borrow-tuple loop admitted
    "res.loop_value_tuple_bind",    # whole all-value tuple loop var admitted
    "res.yield_record_borrow",      # record yield of a routed loop-var name
    "res.yield_record_param",       # record yield of a PARAM name, bare
    "res.yield_value",              # generator yield-value render
    "res.frame_slot_write",         # frame_slot local `.emplace()` write (R1c)
    "res.rebind_ptr_own",           # REBIND_PTR frame local write into its
                                    # site field (OWN / first write)
    "res.rebind_ptr_in_place",      # REBIND_PTR frame local write through
                                    # the pointer (IN_PLACE)
    "res.frame_comp_write",         # a comp init emplaces its stmt-expr:
                                    # rows.emplace(({ ... }))
    "res.coro_handle_write",        # concrete-coro handle factory-call bind
    "res.coro_handle_move",         # NAME source: emplace(move(*c)); c.reset()
    "res.erased_handle_write",      # owned-erased dyn handle own-arg bind
    "res.await_prebuilt",           # bound-handle await routed (poll-in-place)
    "res.match_dispatch",           # MatchDispatch routed through the tiers
    "res.narrow_scope",             # BB leaves lowered under a narrowed scope
    "res.narrow_kill",              # a top-level rebind of a narrowed name
                                    # mid-BB pops the alias at the leaf
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
    # The universal __iter__/__next__ protocol foreach (generator-call /
    # iterator-returning-call / user-iterator-name iterables -- the
    # direct-next loop over an explicit iterator).
    "foreach.iter_proto",
    # A NativeIterable[T]/Spannable[T] protocol PARAM iterable: the
    # NativeIterable peephole (plain begin/end range-for over the deduced
    # template-param lvalue), not the universal loop.
    "foreach.narrowed_proto_src",  # narrowed-alias iterable -> the
                                    # universal __iter__/__next__ loop
    "foreach.native_proto_param",
    "foreach.native_bound_field",   # open-T field with a NativeIterable/
                                    # Spannable bound: begin/end member loop
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
    # The same explicit `Ptr[record]` receiver at the STORAGE-form field
    # access (the assign-target / storage-sink arm) -- the twin of
    # `field.ptr_value`, which fires at the value read.
    "field.ptr_value_storage",
    # `.field` auto-dereffed through a USER Deref wrapper -> `r.__deref__().x`
    # (N = deref_depth). Bare `.` receiver; read and scalar-write target alike.
    "field.user_deref_chain",
    # A BORROW-returning ptr-repr Optional call result at the
    # `Optional[record]` FIELD-write sink, lifted via `ptr_to_optional`.
    "call.ptr_opt_lift",
    # The same borrowed result as the bare `::tpy::ptr_truthy(..)` operand.
    "call.ptr_truthy_operand",
    # A tuple LITERAL at a tuple field: the spelled value brace-init, plus
    # the `tuple_to_storage` wrap at an F3 (non-value-element) slot.
    "field_write.tuple_literal",
    "field_write.tuple_storage_copy",  # F3 tuple field from a storage-form
                                    # source (subscript / field / storage
                                    # name) -- the bare copy, no wrap
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
    # `t.w = e.make(4);` -- a record-returning METHOD call RVALUE copies bare
    # into the field. The borrow-returning twin rejects.
    "field_write.method_rvalue_copy",
    "field_write.ptr_local_copy",   # `this->r = (*saved);` -- pointer-local
                                    # record source copies through the deref
    # A plain F1-record FIELD write whose TARGET auto-derefs through a user
    # `__deref__` wrapper (`this->_state.__deref__()._recv_waker = waker;`).
    # Target-position only: the value rows above decide copy-vs-move.
    "field_write.record_user_deref",
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
    # `self.on_event = cb;` -- an Optional[Callable] field's operator=
    # absorbs the bare callable-name render.
    "field_write.opt_callable_name",
    # Module-init (`__tpy_init`) statements. `global_slot` is a non-value
    # global's initializing write (`static T __global_slot_N = init;` +
    # `g = &__global_slot_N;`); `import_init` an import's `__tpy_init()`
    # chain (or its comment-only empty render); `final_skip` a `Final`
    # global, whose definition lives at namespace scope.
    "top_level.native_global_skip", # `native_global(..)` decl: emits nothing
    "ret.record_ptr_opt_local",     # `return std::move((*p));`
    "ret.record_ptr_local",         # plain F1 pointer-local: `return (*best);` (+ move at last use)
    "ret.tparam_ptr_local",         # open-T `T*` local -> `return (*p);`
    "top_level.global_opt_passthrough",  # `g = <ptr-opt call>;`
    "call.ptr_opt_whole",           # ptr-opt borrow call result at a
                                    # whole-optional consumer: the bare T*
    "call.ptr_opt_passthrough",     # free call at that write
    "method.ptr_opt_passthrough",   # method call at that write
    "call.container_borrow_ret",    # borrow container return at the T&
                                    # alias-decl sink
    "call.container_rebind_ret",    # ... and the OWNING one filling a
                                    # reassigned local's rebind slot
    "call.recv_borrow_ret",         # borrow-returning record call under a
                                    # RECEIVER position's own `&(...)` lift
    "call.er_ref_bind",             # ref-returning @error_return callee at
                                    # the raw er-bind's alias-bind arm
    "call.native_record_recv",      # native record-rvalue call under a
                                    # postfix member read (deref(p).f)
    "call.native_own_record_value",  # Own-returning native record call at
                                    # the plain VALUE position
    "decl.none_unit_slot",          # None-annotated decl: monostate copy
    "decl.bytearray_view_copy",     # bytearray slot from a coerced view:
                                    # the materialize `Bytes(x)`
    "field.none_unit_write",        # NoneType field write: bare assign
    "mil.none_unit",                # ctor MIL None field: slot(monostate{})
    "top_level.global_no_init",     # annotation-only global: emits nothing
    "top_level.global_slot",
    "top_level.global_slot_comp",   # comp init renders its stmt-expr inside
                                    # the static slot line
    "top_level.global_slot_reuse",  # `g = &(__global_slot_N = init);`
    # A HOISTED global's initializing write: the slot is an optional on
    # the hoist lines (`g = &*(__global_slot_N = init);`).
    "top_level.global_hoist_slot",
    "top_level.tuple_storage_global",  # F3 tuple global write: the borrow
                                    # literal under the tuple_to_storage lift
    # The same global written from a MIXED-own-tuple CALL: the call renders
    # bare under the same non-move lift.
    "top_level.tuple_global_mixed_call",
    "top_level.global_null",        # `g = nullptr;`
    # A BORROW-returning method call at a global slot: the slot points AT
    # the callee-owned storage (`pt = &(points->load(0));`), no slot alloc.
    "top_level.global_addr_call",
    "top_level.global_ptr_copy",    # `g = other;` (pointer-slot source)
    "top_level.global_addr_local",  # address-of a plain LOCAL lvalue (the
                                    # desugared unpack alias) into the slot
    # Address-of a FIELD lvalue (`ys = h.xs`): the owner holds the storage.
    "top_level.global_addr_field",
    # A SUBCLASS rvalue into an annotated base slot: the sema-warned upcast
    # slices into the base-spelled slot.
    "top_level.global_slot_upcast",
    # A VALUE-variant global slot: the rvalue converts at its member type.
    "top_level.global_slot_union",
    # An Optional[record] FIELD source: the storage member lifts to the slot
    # pointer (`g = optional_to_ptr(h->value);`).
    "top_level.global_opt_field_lift",
    # A branch whose COMPOUND condition sema narrowed: the body carries the
    # branch-entry extraction alias.
    "if.cond_facts_alias",
    # The while twin, at loop entry.
    "while.cond_facts_alias",
    # The same for a NEGATED or-chain head, whose fact survives the negation.
    "while.or_chain_narrowed",
    "top_level.import_init",
    "top_level.final_skip",
    # Walrus whose target is a resumable-frame FIELD -- one row per
    # frame-layout verdict (lowering; the write renders, nothing declares).
    "expr.walrus_frame_slot",       # owning slot: `xs.emplace(v)`
    "expr.walrus_frame_opt_ptr",    # `T*` Optional field: `(m = v)`, with
                                    # the optional_to_ptr lift on a field src
    "expr.walrus_frame_alias",      # `T*` alias field: `(row = &(v), *row)`
    "expr.walrus_frame_alias_slot", # `T*` alias field off a frame_slot NAME:
                                    # `(x = &((*buf)), *x)`
    "expr.walrus_frame_btuple",     # borrow tuple field: `(bt = v, bt)`
    "expr.walrus_frame_field",      # plain field: `(n = v)`
    "ifexpr.isin_narrow_str",       # isinstance-narrowed select at a str
                                    # result: the shared view/owned verdict
    "ifexpr.value_opt_scalar_expr",  # value-opt ternary arm: a scalar-valued
                                    # expression under the optional wrap
    "ifexpr.record_elem_arm",       # F1-record ternary arm: a container
                                    # ELEMENT subscript lvalue
    "ifexpr.optptr_elem_addr",      # ptr-Optional ternary arm: `&(elem)` off
                                    # a plain record container element
    "ifexpr.optptr_elem_lift",      # ... optional_to_ptr off an Optional
                                    # container element
    "walrus.optptr_ternary_src",    # ptr-Optional walrus source rung: a
                                    # ternary whose arms are already `T*`
    "walrus.optptr_subscript_src",  # ptr-Optional walrus source rung: a
                                    # storage-Optional container element
                                    # lifted by optional_to_ptr
    "walrus.optptr_call_src",       # ptr-Optional walrus source rung: a
                                    # BORROWING call's `T*` lands bare
    "res.yield_container_walrus",   # container yield slot walrus delegates
                                    # to the frame-walrus dispatch
    # ... and its record-slot twin. No corpus witness: the walrus dispatch
    # has no leg for a record-pointer alias yet (`expr.walrus`), so the
    # whole-corpus zero-witness census lists it until one lands.
    "res.yield_record_walrus",
    "yield.own_tuple_literal",      # Own-record-element tuple literal at
                                    # the resumable tuple yield slot:
                                    # the spelled storage brace-init
    "decl.type_param_slot",         # bare open type-param decl slot
                                    # (`T newitem = ...;`): plain spelled copy
    "ret.value_opt_view_result",    # call/method-call/subscript result at a
                                    # value-repr Optional[view] return slot
    "decl.value_opt_tuple_slot",    # value-repr Optional[value tuple] decl
                                    # slot: plain spelled copy
    "if.narrow_elif_else_fact",     # else fact with no consumer tolerated on a
                                    # narrowing if whose else body is an elif
                                    # link -- the link seeds its own facts and
                                    # no extraction runs at this level
    "ret.own_wrapper_ptr_opt_local",  # hoisted-optional local of the slot's
                                    # own wrapper union at an Own[union]
                                    # return -> `return std::move((*v));`
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
    `reject.begin_attempt`, so the journal shares the attempt's boundaries)."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_face_journal = {}


def commit_witnesses() -> None:
    """Close the journal on a body that ROUTED, so witnesses recorded outside
    a lowering attempt -- emit-time ones especially, since `thir/emit.py`
    records faces too -- are never journalled and so can never be rolled back.

    Closing is not what makes the tally correct: `begin_attempt` precedes
    every reject and RESETS the journal, so a rollback already drains
    only its own body (measured -- neutering this call corpus-wide leaves the
    zero-witness list unchanged). What it buys is that the window has a
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

    A body that rejects emits NOTHING, so an arm that merely ran during the
    attempt contributed no emitted C++ and is
    not covered by anything. Counting it defeats the detector: that is exactly
    how a dead arm passed the zero-witness check once already. Applies to the
    `own.*` admission rows too -- admission stays the distinguishing site for a
    body that ROUTES, which is the case their semantics were written for."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    assert compiler._thir_face_journal is not None, (
        "a reject with no journal open -- every attempt seam must be "
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
