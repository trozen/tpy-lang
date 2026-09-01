# Dual-path asymmetry check

Answers one question, over `match` statements: **is there a program the AST
codegen REFUSES and THIR emits?**

    uv run python scripts/thir_migration/asym/check_asym.py \
        --width Int32 --width Int64 --width BigInt

`--width` defaults to Int32 alone, and a sibling sweep found the only known
option-gated AST/THIR divergence at BigInt -- so pass all three when the answer
matters. Two other scope notes, because the headline number invites a wider
reading than it earns: `matrix.py` generates only `match` programs, and roughly
550 of the ~670 never reach either emitter (sema refuses them first), so the
population the question is actually asked of is the ~120 that emit.

That is the only direction in which deleting the AST body emitters is unsafe.
Delete a raise site whose shape THIR also refuses and a diagnostic merely
changes; delete one whose shape THIR can lower and the rejection was the defect
being fixed. It is dangerous only where a refusal disappears and the program
compiles to output nobody reviewed.

Framing this as an asymmetry rather than as "is raise site X reachable" is what
makes it tractable: reachability needs a witness per site, the asymmetry needs
none. A fallback cannot fake the result either -- a body THIR cannot lower is
re-emitted by the AST, so if the AST raises the fallback raises too, and THIR
succeeding where the AST raises means THIR routed.

The probes are GENERATED (`matrix.py`), not committed, because the inputs are a
cross product: a generator is smaller than the corpus and is the only form in
which "every crossing" is checkable rather than asserted.

**The control is not optional and runs automatically.** Five committed cases
known to refuse at codegen must each come back `BOTH_REFUSE`.

**Exit status**: non-zero on a control failure, on any ASYMMETRY / DIVERGE /
THIR_ONLY_REFUSES, or when more probes fall back than the ratchet in
`check_asym.py` records. The known fallbacks are tracked in `TODO.md` and do
NOT fail the run -- failing on them would make the status a constant and unable
to tell a broken instrument from the documented backlog. A zero from a detector is evidence only once the detector
has been shown to fire, and this one's dominant outcome is a bucket that
already looks like a clean result -- so a silent misread and a clean tree
would otherwise be indistinguishable.

**What the control establishes directly is that an AST refusal is DETECTED.**
Both error texts are recorded, so the run also shows whether THIR refused for
its own reason -- it does for all five, each raising from inside
`tpyc/thir/lower/`. But the authoritative test of that property is the corpus
error-path gate (`tests/conftest.py`), which re-emits through THIR whenever the
AST run raises and demands the SAME diagnostic; this control only demands that
both refuse. Cite the gate for it, not this.

Expect most probes to land in `FRONTEND`. That is the result, not a failure to
probe: sema refuses most tier/pattern crossings before either emitter runs,
which is why the codegen raise sites behind it have no witnesses.

Needs both emit paths, so it does not survive the AST body-emitter deletion --
and needs no successor, since what it asks stops being a question when one
emitter remains.
