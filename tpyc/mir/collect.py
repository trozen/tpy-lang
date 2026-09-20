"""Debug adapter from emitted THIR caches to bounded MIR bodies."""

from ..codegen_cpp.context import CodeGenContext
from ..identity_map import IdentityMap
from ..parse import TpyFunction, TpyModule
from ..sema.analyzer import SemanticAnalyzer
from ..thir.lower import iter_module_callables, iter_module_constructors
from ..thir.reject import is_bodyless_binding
from .definitions import MIRDefinitions
from .dependencies import analyze_dependencies, dump_dependencies
from .dump import dump_function
from .lower import lower_constructor, lower_function
from .liveness import analyze_liveness, dump_liveness
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered
from .storage import analyze_storage, dump_storage
from .retention import analyze_retention, dump_retention
from .payload_lifetime import dump_payload_ends, dump_payload_inspection, inspect_payload_lifetimes
from .scope_lifetime import analyze_scope_ends, dump_scope_ends


def dump_codegen_mir(module: TpyModule, analyzer: SemanticAnalyzer,
                     ctx: CodeGenContext, module_name: str,
                     definitions: MIRDefinitions,
                     reasons: IdentityMap) -> str:
    """Inspect emitted bodies without making MIR an emission prerequisite."""
    lines: list[str] = []
    identities: dict[str, int] = {}

    def identity(name: str, func: TpyFunction | None = None) -> MIRBodyId:
        if func is not None and func.loc is not None:
            name += f"@{func.loc.line}:{func.loc.column}"
        count = identities.get(name, 0) + 1
        identities[name] = count
        return MIRBodyId(module_name, name if count == 1 else f"{name}#{count}")

    def unavailable(body: MIRBodyId, reason: str) -> None:
        lines.append(f"fn {body.module}::{body.declaration}: <{reason}>\n")

    def missing(body: MIRBodyId, func: TpyFunction) -> None:
        if is_bodyless_binding(func) or func.is_overload_stub:
            unavailable(body, "no body to lower")
        elif (why := reasons.get(func)) is not None:
            unavailable(body, f"THIR rejected: {why}")
        else:
            unavailable(body, "THIR not attempted: an earlier reject ended emission")

    def rendered(result: MIRFunction | MIRNotCovered) -> None:
        match result:
            case MIRNotCovered():
                unavailable(result.body, f"MIR not covered: {result.reason}")
            case MIRFunction():
                lines.append(dump_function(result).rstrip("\n") + "\n")
                liveness = analyze_liveness(result)
                lines.append(dump_liveness(liveness))
                dependencies = analyze_dependencies(result, liveness)
                events = analyze_storage(result)
                lines.append(dump_scope_ends(analyze_scope_ends(result)))
                lines.append(dump_dependencies(dependencies))
                lines.append(dump_storage(events))
                lines.append(dump_retention(analyze_retention(result, liveness, dependencies, events)))
                inspection = inspect_payload_lifetimes(result)
                lines.append(dump_payload_ends(inspection.ends))
                lines.append(dump_payload_inspection(inspection))

    if module.top_level_stmts:
        body = identity("__tpy_init")
        if ctx.thir_top_level is not None:
            unavailable(body, "MIR not covered: module initialization")
        elif (why := reasons.get(module)) is not None:
            unavailable(body, f"THIR rejected: {why}")
        else:
            unavailable(body, "THIR not attempted: an earlier reject ended emission")

    for func, owner in iter_module_callables(module, analyzer):
        name = f"{owner.name}.{func.name}" if owner is not None else func.name
        body = identity(name, func)
        if is_bodyless_binding(func) or func.is_overload_stub:
            missing(body, func)
        elif ctx.thir_resumables.get(func) is not None:
            unavailable(body, "MIR not covered: resumable body")
        elif func in ctx.thir_functions or func in ctx.thir_overload_functions:
            if func.type_params or (owner is not None and owner.type_args):
                unavailable(body, "MIR not covered: generic body")
            elif analyzer.overload_groups.get(func):
                unavailable(body, "MIR not covered: overloaded callable")
            else:
                fn = ctx.thir_functions.get(func)
                if fn is None:
                    missing(body, func)
                else:
                    kind = MIRBodyKind.METHOD if owner is not None else MIRBodyKind.FREE_FUNCTION
                    rendered(lower_function(fn, body, kind=kind, definitions=definitions))
        else:
            missing(body, func)

    for record, ctor, _owner in iter_module_constructors(module, analyzer):
        body = identity(f"{record.name}.__init__", ctor)
        if ctor not in ctx.thir_constructors:
            missing(body, ctor)
        elif record.type_params:
            unavailable(body, "MIR not covered: generic constructor")
        else:
            rendered(lower_constructor(ctx.thir_constructors[ctor], body,
                                       definitions=definitions))
    return "\n".join(lines) if lines else "(no emitted bodies in this module)\n"
