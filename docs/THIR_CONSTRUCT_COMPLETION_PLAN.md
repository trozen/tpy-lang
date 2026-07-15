# THIR Construct-Completion Plan

*Built 2026-07-15 on the landed wave-4 collector (`thir-routing-wave4` @
`0b8681954`, dial 1177). Reorients the migration from opportunistic
case-flipping to construct-completion, steered toward AST-emit-code
DELETION. Data from `$THIR_ARM_RESIDUAL_JSON` / `$THIR_FALLBACK_JSON` on
the landed state.*

## The metric shift

**Completion is gated on deleting the AST emit code, not on migrating
cases.** Two finite conditions: (a) every AST emit ARM has a THIR
equivalent (ledger closed), (b) every real body routes (fallback = 0
across user + in-scope library). Neither cases, bodies, faces, nor shapes
measures this well (all measure the corpus incidentally; shape % is an
asymptotic combinatorial diagnostic).

**Headline completion metric going forward: AST-emit-arm RESIDENCY -> 0**
-- how many AST emit-dispatch arms in `tpyc/codegen_cpp/` still fire under
forced-THIR (measured by the A-minus-U coverage subtraction; harness at
`scripts/thir_migration/thir_ast_arm_residency.py`). Secondary: **# constructs with any
fallback body -> 0** (the arm-residual dump enumerates these). Shapes/cases
demoted to visible-progress proxies.

## Three steering views (from the landed dumps)

**A. First-reject BLOCKERS (biggest first) -- what to route to clear body
mass + move the shape needle.** These are the constructs that actually
BLOCK bodies (first-reject):
- body (4174): `expr.call` 529, `decl.slot_type` 494, `expr.method_call`
  335, `return.slot_type` 142, `match` 100, `fstring` 91,
  `container_literal` 88, `assign.field_write_shape` 85, `if:cond.call`
  65, `print.arg.scalar_bin_op` 63, `print.arg.scalar_field_access` 59,
  `return:field.result_type` 53, +324 more kinds (the long tail).
- ctor (172): `mil_field.optional.name` 53, `field_write_shape` 15,
  `genrec_concrete.name` 11, ...
- resumable (418): `res.param_type` 100, `await_param_type` 46,
  `method_call` 43, `yield_type` 41, `return_type` 36, `local_storage`
  27, `generic` 23, `generic_record` 11, `unpack` 11, `leaf_return` 8, ...

**B. Arm-residual (SMALLEST first) -- cheap arm-KILLS (few bodies keep the
AST arm alive; zero them and that construct is fully THIR-covered).** Note:
the few bodies may be interlocked on a big construct, so "N bodies" is a
floor, not the effort. Cheapest arms:
`type_param_construct` 1, `value_pattern` 2, `del_var` 3, `or_pattern` 4,
`set_comprehension` 5, `continue` 6, `capture_pattern` 10,
`generator_expression` 10, `nested_def` 11, `function` 11,
`dict_comprehension` 12, `del_item` 13, `list_repeat` 16, `global` 18,
`star_unpack` 24, `pass_stmt` 29, `set_literal` 30, `named_expr` 32,
`list_comprehension` 48, `as_pattern`/`literal_pattern` ~52, `break` 54,
`raise` 59, `lambda` 60, `assert` 63, `if_expr` 71, `slice` 90, `with` 103,
`class_pattern` 111, `f_string` 120, ...

**C. Common-construct tail (biggest arm-residual) -- clears LAST, as a
consequence, not a target.** `method_call` 1711, `coerce` 1687,
`str_literal` 1677, `bin_op` 1389, `subscript` 879, `if` 799, `assign`
626, `for_each` 588 -- these appear in almost every fallback body because
they are ubiquitous, NOT because they are un-ported. They zero only when
nearly everything else routes. Do NOT target them directly.

## The sequenced program

The interlock that makes case-flips lag becomes a TAILWIND: as blockers
clear, bodies flip in bulk and the ubiquitous-construct residuals (view C)
collapse on their own. Sequence:

**Track 1 -- the general BLOCKER head (biggest shape-movers, unblock the
most interlock).** One construct per wave, driven to zero first-reject
corpus-wide, ratchet-locked:
1. `expr.call` (529) -- the call-arg/callee shape tail. Also dissolves
   most of `fstring` (91: its args are `repr()`/`str()`/method calls) and
   part of `if:cond.call`.
2. `expr.method_call` (335) -- receiver/return/arg shapes (native-method
   rungs already partly done in wave 4; continue: chained calls, socket
   owned/optional/error-return, cpp_template).
3. `decl.slot_type` (494) -- the reference-type-local grab-bag (record/
   container/Box/Rc/protocol-adapter/generic/storage-call returns; wave-4
   E did the alias sub-slice; continue the rest).
4. `return.slot_type` (142) + `return:field.result_type` (53).
5. `assign.field_write_shape` (85, body + 15 ctor).

**Track 2 -- cheap arm-KILLS (momentum + shrink the remaining-arm count),
interleaved.** Pick off small-residual arms (view B) whose few bodies are
NOT interlocked on a Track-1 blocker: `del_var`/`del_item`, `continue`/
`pass_stmt`/`break`, `set_comprehension`/`dict_comprehension`/
`list_comprehension`, `or_pattern`/`value_pattern`/`capture_pattern`
(match sub-arms), `star_unpack`, `global`, `named_expr`, `lambda`,
`assert`, `slice`. Each fully-covered arm drops the "# constructs with
fallback" completion metric by one -- visible, cheap wins.

**Track 3 -- the GENERICS FOUNDATION (parallel, its own session/branch off
post-wave-4 master).** Gate on the generics/protocol/resumable-template
mass (~180+ bodies) AND zeroes `type_param_construct` (1) and the
generic-fn/record residue. Partially built (TYPE-kind fns + generic record
methods route); residue threads TypeParamRef/bounds through the SAME
decl/call/method/coerce/return arms + int-kind + static-protocol template
frames + explicit-targs. Brief: `docs/THIR_GENERICS_FOUNDATION_BRIEF.md`.

**Track 4 -- the resumable tail (after Track 3 lands its base + its own
cells).** `res.param_type` 100 (Own-non-record/protocol -> needs generics),
`await_param_type` 46, `yield_type` 41, `return_type` 36 (str/bytes async
returns -- view-source threading), `local_storage` 27 (borrow-tuple
frame-local, the deferred Part 2(ii)), `leaf_return` 8. Internally serial
(one executor: resumable.py + gen_async.py).

**Track 5 -- the long tail (match, container_literal, print.arg, if:cond
isinstance, etc.).** Many clear as Track-1 blockers dissolve; `match` (100,
match.py -- ptr-union + capture-alias) and `container_literal` (88,
storage-form element conversion) are their own bounded ARCH items;
`if:cond.call` (65) is ~90% isinstance-narrowing (partly generics-gated).

## Cadence per wave
Pick 1 Track-1 blocker (drive to zero first-reject) + 2-4 Track-2 cheap
arm-kills, plus the standing Track-3 generics work in parallel. After each
wave: re-run the AST-arm-residency harness (headline: arms-left-to-kill)
+ the arm-residual dump (# constructs with fallback). Steer by residency
dropping, not by the case dial.

## Completion
When AST-arm residency = 0 (no emit arm fires under forced-THIR) AND the
corpus covers every emit arm (coverage-completeness) AND the ledger is
closed: flip THIR mandatory, delete the AST body/form codegen in one
change. That is the deletion the whole migration exists to reach.

## Open item
AST-arm-residency headline number (arms-left-to-kill on the landed state)
being measured now via the A-minus-U coverage subtraction; fold in as the
plan's tracking baseline once available.
