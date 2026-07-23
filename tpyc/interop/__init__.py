"""CPython `@export` boundary: the interop machinery gathered in one package.

The boundary is validated and emitted in phase-specific places (located
diagnostics need sema, cross-module knowledge needs the compiler
orchestrator, emission needs codegen), but the BODIES all live here so a
check added to one home is visible beside its siblings:

- `export_shape.py` -- shared shape/classifier predicates both validation
  homes and the glue emitter consult (the anti-drift chokepoint).
- `sema_validators.py` -- per-module exposed-class dunder validation and
  boundary-aliasing warnings (called on the analyzer for located
  diagnostics).
- `module_validators.py` -- whole-program ext_module/@export validation
  (called on the compiler after all modules are analyzed).
- `extension.py` -- the glue TU emitter (`PyInit_`, wrappers, type specs).

The phase call sites stay thin hooks; only the logic lives here.
"""
