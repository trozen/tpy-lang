# TurboPython Design Principles

The north star for the language and for how it is built. Every contributor --
and every agent working in this repository -- reads these before designing,
fixing or reviewing anything; rules, skills and designs never contradict them.

**Precedence.** Earlier principles generally take precedence when they conflict. A conflict is a judgement call, and the design names it.

## Language

**1. Valid Python.** Every TPy program is syntactically valid Python, so the Python tooling ecosystem (editors, type checkers, linters, LLMs) works unchanged.

**2. CPython behavior, or a visible difference.** A program TPy accepts behaves as it does under CPython, or the compiler warns where it does not. The program can state the difference in valid Python (a type annotation, a decorator or a helper such as `Own[T]` or `copy()`); that silences the warning, and through the stubs CPython then behaves the same. The compiler never silently produces a different result. Rejecting valid Python is a documented restriction or a tracked gap.

**3. Deliberate defaults, levers both ways.** Defaults lean toward CPython compatibility. Where one departs from it for speed -- an unannotated `x = 1` is an `int32` -- that is a deliberate, documented choice with a lever back (`int`, `--default-int=BigInt`). Opt-ins such as `Ptr` and `@noalloc` go further toward speed. Neither path closes the other.

**4. No hidden costs.** Every copy, allocation and runtime check the compiler introduces shows, as a diagnostic or an explicit spelling. A cost the type mapping does not require is a defect. Opt-ins avoid costs; removing a safety check is an unsafe opt-in (#5).

**5. Safe by default.** Undefined behavior requires an explicitly unsafe construct, and programs are sound under threads. Code can tighten this (e.g. a strict mode with no unsafe constructs) or loosen it locally through a named helper from `tpy.unsafe`. Full safety is the 1.0 goal; gaps until then are tracked.

**6. Performance across domains.** TPy targets real-time systems such as high-frequency trading and game engines, CPython extensions, command-line tools and, later, desktop applications. The real-time ones need C++ speed with predictable latency -- no garbage collection, explicit control over resources; the others need to be far faster than CPython. Neither group pays for the other.

**7. Backend-independent semantics.** The core language is defined in Python terms. C++ is the current backend, not the definition: diagnostics never require C++ knowledge, and native bindings describe their target's contracts.

**8. Explicit interop, both directions.** TPy reaches C and C++ only through declared bindings. TPy code embeds in a host as readily as it runs as a program, and the host owns process-level policy and services: failure handling, logging, I/O.

**9. A small vocabulary.** Everyday TPy is Python plus a few concepts learned once: ownership transfer (`Own[T]`) and fixed-width numbers. Pointers, `readonly` and `unsafe` stay in opt-in code. Every concept added to everyday code halves the audience. Users annotate only what the compiler cannot infer.

## Engineering

**10. One implementation per concept.** A new feature first reuses existing mechanisms, or unifies similar ones, before adding anything. Each rule is implemented once, on a generic path. Parallel implementations and special cases are defects.

**11. TPy first, through a narrow compiler interface.** Whatever can be written in TPy is written in TPy rather than C++: library composition, protocols, and runtime logic above the system boundary. Stdlib types are ordinary types: the compiler knows them through general mechanisms (annotations, protocols, decorators), not by name, and special-cases one only where the language leaves no other way -- such as deducing a list literal's element type.

**12. Evidence before code.** A wrong design costs more than a late one. Changes start from a design checked against probes and measurements, not assumptions. A claim (a reviewer's finding, an agent's report, "this already works") holds only once it is verified.
