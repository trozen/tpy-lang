# Brief: post-cutover health review of THIR lowering / emit (one layer per agent)

## Context

TurboPython (repo root /home/tommy/dev/turbo-python/tpy-m1) compiles a typed Python
subset to C++. Two codegen paths exist for function bodies: the legacy AST emitter
(`tpyc/codegen_cpp/expressions.py`, `statements.py`, `match.py`, `builtins.py`) and
THIR (`tpyc/thir/nodes.py` = the IR, `tpyc/thir/lower/` = AST+sema -> THIR,
`tpyc/thir/emit.py` = THIR -> C++). THIR was built under one hard constraint: emit
C++ BYTE-IDENTICAL to the AST path, so that a corpus byte-diff could verify it.
That constraint shaped everything: lowering re-derives AST-emitter decisions,
nodes carry pre-rendered C++ strings, docstrings specify themselves by naming AST
emitter functions ("mirrors `_gen_method_call`'s ... arm").

The AST emitter is about to be DELETED. After that, byte-identity is no longer a
requirement: the committed snapshots become the oracle, and any change to
generated C++ is just ordinary snapshot churn that exec + CPython-parity tests
verify. The question for this review is: **what should this code look like as the
PERMANENT and ONLY codegen path**, and what is the cheapest safe order of steps to
get there. A second IR stage (MIR: basic blocks, places, borrow checking; see
`docs/IR_DESIGN.md` "MIR Design") may come later, so the target must not push
C++-specific decisions DEEPER into THIR than they are today; note where a MIR
boundary would naturally sit.

This is a READ-ONLY review. Do not edit repository files. Do not run pytest. Do
not use git to change anything. Write scratch files only under the scratchpad
directory given in your task message.

## What to look for (the rubric)

For your layer, catalogue with `file:line` anchors and honest size estimates:

1. **Monoliths and dispatch shape.** Functions over ~200 lines, isinstance /
   if-elif ladders that could be tables. `tpyc/thir/lower/field_write.py` uses a
   (classify, lower) plan-table pattern; say where that pattern would apply and
   where it would not.
2. **Duplicated or near-duplicate classifiers.** Predicates answering the same
   question with different spellings (the codebase itself has noted pairs like
   `value_opt_name_pass` vs `value_opt_pass_through`). Give the pairs.
3. **Decisions made in the wrong layer.** Lowering that pre-renders C++ text the
   emitter could render from typed facts (a `*_cpp: str` field composed from
   pieces); emit that re-inspects types or re-derives a fact lowering already
   decided; lowering that consults `codegen_cpp` helpers (imports from
   `..codegen_cpp` / `...codegen_cpp`) -- list every such import and say whether
   the helper is a type-rendering primitive (fine, that layer survives) or a
   body-emitter decision (a dependency the cutover must cut or absorb).
4. **Byte-identity warts.** Code whose only reason is to reproduce an AST
   accident: counter-draw ordering ("the AST draws the counter here"), comment
   trivia reproduction, `paren_wrap`-style flags, emit ordering constraints. Each
   is a candidate to delete WITH snapshot churn. Estimate how many snapshot
   files each would touch if you can (grep the emitted pattern in
   `tests/cases/*/*/expected/` -- only when cheap).
5. **Reject gates that are migration debt, not language semantics.** A
   `ThirUnsupported` raise whose reason is "the AST path does X and we did not
   mirror it yet" versus one that reflects a real TPy restriction. Do NOT
   classify every raise site (another review does that); characterise the
   population in your layer and give representative examples of each kind.
6. **Docstring debt.** Count lines in your layer that name an AST emitter symbol
   (`_gen_*`, `gen_*`, `_emit_*` of the AST, `ExpressionGenerator`,
   `StatementGenerator`, "mirrors the AST") -- these become dangling pointers at
   deletion. Propose the rewrite policy (state the invariant instead).
7. **Stringly-typed enums and flag zoos.** `kind: str = "list"`, `mode: str`,
   `strategy: str`, and clusters of mutually exclusive booleans guarded by
   `__post_init__` asserts. The IR already has good examples of the fix
   (`TupleSourceBind`, `WithTargetArm`, `PtrSlotKind`, `PrintForm` in `nodes.py`).
8. **Where the MIR boundary would sit.** Which facts in your layer are semantic
   (ownership, aliasing, borrow vs storage form, last-use moves, narrowing) and
   which are C++ representation choices (slot hoisting, `frame_slot`, pointer
   locals, rebind slots, statement-expression temps). The former belong to THIR
   or MIR; the latter belong to a late lowering / the printer.

## Reading aids

- `docs/IR_DESIGN.md` lines 239-760 (THIR design + "Form as a First-Class THIR
  Fact") -- the stated intent.
- `tpyc/thir/nodes.py` -- the IR. Read the nodes your layer constructs.
- `tpyc/thir/lower/__init__.py` -- the layer order and public surface.
- `tpyc/thir/lower/field_write.py` -- the plan-table pattern.
- `TODO.md` has `[thir]` entries recording known debt (grep `\[thir\]`); cite an
  entry if a finding is already recorded there, do not re-derive it.

## Output

1. A JSON file at the path given in your task message: a list of findings,
   each `{"rubric": 1-8, "file": ..., "line": ..., "title": "<one line>",
   "detail": "<2-5 sentences>", "size": "S|M|L|XL", "churn": "zero|output-changing|unknown",
   "confidence": "high|medium|low"}`. Size: S = under a day, M = days, L = a week
   or two, XL = more. Churn: would fixing it change emitted C++ (snapshots)?
2. Your reply: a report UNDER 500 WORDS. Lead with the three findings that most
   change the picture, then a one-paragraph proposed target shape for your layer,
   then the MIR-boundary observation. No code in the report; anchors as
   `file:line`.
