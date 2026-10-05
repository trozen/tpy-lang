# Narrowing-kill acceptance matrix

The acceptance set for the fact-kill rules of sema (`tpyc/prescan.py`
`InPlaceWrites`, `tpyc/sema/narrowing.py`, `tpyc/sema/init_tracker.py`): one
small program per aliasing shape, each with CPython's truth and the verdict
the compiler gives. A kill rule is changed against three gates, and this
matrix is the instrument for all three:

1. **must kill**: every row whose truth is `None-reaches` renders the subject
   read CHECKED (a `std::optional` local / a checked subscript / a checked
   deref), unless its `ACCEPTED` header names a filed defect;
2. **must stay silent**: every row whose truth is `stays-value` renders it
   UNCHECKED -- a kill there is a runtime check or, for a non-arithmetic
   Optional, a compile ERROR on valid code;
3. **must still compile**: a row that compiled on the baseline tree and
   rejects on the branch is a regression (`no longer compiles` in the report),
   whatever its verdict would have been.

A manual tool, never part of `pytest`: a full run compiles every row twice
(TPy, then CPython for the truth) and the cost rows alone take a few minutes.

## Running

```
uv run python scripts/alias_kill_matrix/run_matrix.py --label master          # this checkout -> results_master.json
uv run python scripts/alias_kill_matrix/run_matrix.py --label branch --repo /path/to/worktree
uv run python scripts/alias_kill_matrix/run_matrix.py --label branch --only inl_ternary,ptr_rc --no-cost
uv run python scripts/alias_kill_matrix/run_matrix.py --check scripts/alias_kill_matrix/build/shapes/inl_ternary.py
uv run python scripts/alias_kill_matrix/run_matrix.py --report                # RESULTS.md from every results_*.json
uv run python scripts/alias_kill_matrix/gen_shapes.py [dir]                # write the rows for reading (a run generates them itself)
uv run python scripts/alias_kill_matrix/gen_cost.py [dir]                  # the cost rows likewise
```

The compiler of `--repo` runs as `python -m tpyc.cli` with `PYTHONPATH=<repo>`
inside the uv environment of `--env-repo` (default: `<repo>` when it has a
`.venv`, else this checkout), so `tpyc` and `lib/tpy` both come from the
measured tree. Compiles are sequential with a 60 s timeout in a temporary
`build/` directory that is removed afterwards. Rows not re-measured by a
partial run (`--only`, `--no-cost`) keep their previous result in the JSON.

`results_master.json` is committed: the verdicts of `master` at the commit
the JSON records -- the commit of the tree it was measured on, which for a
baseline re-measured on a branch is the branch head before its squash becomes
`master` -- each row stamped with its shape's content hash (the report
flags a row whose shape changed since). A branch adds a column with its own
label; `RESULTS.md` then lists, per branch, the rows that differ from the
expected/accepted verdict, the rows that changed against `master`, and the
rows that no longer compile. When the branch merges, re-measure `master` and
commit the new JSON and report; branch JSONs are not committed.

## Rows

The rows live in the generators, `gen_shapes.py` and `gen_cost.py`, and are
written under `build/` at every run; nothing generated is committed. To read
a row, run the generator and open `build/shapes/<name>.py`. Every shape
carries a header:

```
# <one-line description>
# GROUP: inline | pointer | unmodelled | meet | everyday | view | cost
# EXPECTED: CHECKED | UNCHECKED | VIEW | COMPILES | <=2x   what a complete analysis gives
# ACCEPTED: CHECKED | UNCHECKED | REJECT -- <why>   the shipped rule's verdict, when it differs
# VERDICT: local (default) | bounds | deref | view | compiles   how the subject read is classified
# NOTE: free text (may repeat)
```

`ACCEPTED` names either a filed defect (`BUGS.md#<slug>`; the pointer rows
are `pointer-structure-aliases-unmodelled`, the analysis sema does not do) or
a design decision (the bare-bind kill). The gate compares a tree against
`ACCEPTED` where present, else `EXPECTED`; a row with neither matching is a
finding, not noise. An `ACCEPTED: REJECT` row is a `compiles` row whose
rejection is the shipped rule's on purpose: it never counts as `no longer
compiles`, and it shows under `differs` once it compiles.

The subject read is the one line ending `# SUBJECT`, spelled `y = <read>`;
the module declares no other `y`. The verdict reads the generated C++: a
`std::optional<...>` declaration of `y`, or an initializer through
`deref_optional_check`, is CHECKED; an initializer `(*...)` is UNCHECKED.
`VERDICT: bounds` classifies `y = xs[i]` by whether the subscript goes through
a checking helper; `VERDICT: deref` by `deref_check` against a raw `->`;
`VERDICT: view` reads the declared C++ type of `y` as VIEW (`std::string_view`,
a `View` type) or OWNED (`std::string`, `Bytes`), the view-rule rows;
`VERDICT: compiles` has no subject verdict: the row exists for the third gate
(a kill there would be a compile error on valid code). A row
that does not compile is REJECT (or CRASH on a traceback), kept apart from
the verdicts.

Truth is the shape run under CPython with `lib/cpy` on the path: every
`print("Y", y)` line is collected; any `Y None`, or a `TypeError` /
`AttributeError` / `IndexError`, is `None-reaches`, else `stays-value`. The
exception is accepted wherever it is raised, so a row must not raise before
its subject.

Cost rows (`GROUP: cost`) measure front-end seconds of
`tpyc file.py -o out --no-bundle-runtime` against a trivial program compiled
three times (the minimum is the baseline); expected within 2x.

## Caveats worth knowing before reading the table

- **CPython and TPy differ on an overwritten inline slot.** Replacing
  `t.inner = none_m()` does not change an alias of the OLD object in CPython,
  but in TPy the alias denotes the overwritten storage. `inl_replace_slot_alias_fact`
  and `inl_replace_ancestor` therefore show `stays-value` in CPython while TPy
  reads None there; their expected CHECKED is right for TPy. Rows whose fact is
  spelled by the full path agree in both.
- `b or a` with plain records always yields `b` in CPython (objects are
  truthy); `inl_or` gives `N` a `__bool__` so the `a` arm is reachable.
- `ptr_box`: `Box` is unique ownership, so no Box chain leads back to `head`
  (truth `stays-value`); the complete analysis still kills, conservatively.
- `ptr_tree_descent`: the truth graph has a back edge (`c1.l = root`); a
  tree-shaped type does not prove acyclicity.
- **Spellings that reject today** were replaced by stepwise ones and kept as
  `*_lit` / `*_onechain` / `*_fullpath` / `*_decl` / `*_loopwalk` rows, so the
  report shows when they start to lower: a literal `None` store through a
  `Ptr` local or a two-level path (`assign.field_write_shape`); a 2+-field
  chain in one expression (`decl.slot_type`, `if.cond_binop.is not`);
  `node: RO | None = head` (`decl.slot_type`); a `while True` reseat from
  `head`; `while na is not None and na.next is not None:` over a `Ptr`.
- **Cost attribution** (cProfile): `cost_alias_chain_*` is ~90% in
  `sema/context.py all_storage_through_borrows` (once per bind, quadratic in
  the names), the same before the relation existed -- not the may-hold
  relation (`BUGS.md#alias-chain-compile-time-quadratic`); `cost_field_chain_*`
  is `flow_facts.merge_binding_provenance` / `alias_rebind._join` (one merge
  per `if`, each over every name) plus the relation's closure on top.
