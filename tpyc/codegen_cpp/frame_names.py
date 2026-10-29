"""Frame-struct naming: the C++ names of the generator / coroutine frame
structs, one grammar for both shapes and for every call site that embeds
another body's frame (an inline await's sub-future, a delegated generator
source, an `async with` / `async for` dunder's sub-coro).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse.nodes import GENEXPR_FUNC_PREFIX
from ..resumable_shape import ResumableShape
from .context import (CodeGenError, FrameSourceUnnameable, escape_cpp_name,
                      module_to_cpp_namespace,
                      resumable_struct_name)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..typesys import NominalType, TpyType


# -- Frame-struct naming.
#
# One grammar for both shapes: the `__coro_` struct an inline await embeds
# and the `__gen_` struct a delegating `for` embeds are the same kind of
# name over the same kind of callee, so a caller that spells one by hand
# drifts from the other (that drift is what kept cross-module generator
# delegation rejected while cross-module await worked).

_SHAPE_PREFIX = {ResumableShape.ASYNC: "__coro_",
                 ResumableShape.GENERATOR: "__gen_"}


def frame_struct_name(name: str, owner_record: 'str | None' = None,
                      shape: ResumableShape = ResumableShape.ASYNC) -> str:
    """Resumable-frame struct name from raw strings: `<prefix><name>` for
    free functions, `<prefix><Owner>_<name>` for methods, where the prefix
    is `__coro_` for the async shape and `__gen_` for the generator one. A
    generator expression's frame is `<name>_frame` instead.

    Single source of truth for the frame a function emits for itself, for
    the sub-coroutine struct of a statically-resolved await (always
    `__coro_`, since only an `async def` can be awaited), for the delegated
    generator a `__for_src` field embeds, and for the async-with prescan
    (which only has the context manager's `NominalType.name`).
    """
    # A generator expression's function is compiler-named, so the name already
    # stands apart from a `def` generator's; the frame only needs telling from
    # its factory.
    if owner_record is None and name.startswith(GENEXPR_FUNC_PREFIX):
        return f"{escape_cpp_name(name)}_frame"
    # The spelling itself (incl. the collision-free form for a nested
    # owner like `Outer.Inner`) is the context helper's; this only picks
    # the prefix from the shape.
    return resumable_struct_name(name, owner_record, _SHAPE_PREFIX[shape])


def frame_struct_qualname(
        owner: 'NominalType | None', method: str,
        inferred_type_args: 'tuple[TpyType, ...] | None' = None,
        *, analyzer=None,
        type_to_cpp: 'Callable[[TpyType], str] | None' = None,
        module_qual: str | None = None,
        extra_template_args: 'list[str] | None' = None,
        shape: ResumableShape = ResumableShape.ASYNC) -> str:
    """The C++ name of the frame struct a call site embeds, qualified so it
    is spellable from the module doing the embedding. `analyzer` and the
    type renderer `type_to_cpp` are needed only for an owner or inferred
    type arguments.

    Free function, same module: `<prefix><name>[<inferred_args>]`.
    Free function, cross-module (via `module_qual`):
        `<callee_ns>::<prefix><name>[<inferred_args>]`.
    Method on a non-generic class: `<ns>::<prefix><Record>_<name>
        [<inferred_args>]`, the namespace read off the owner's rendered
        C++ name so an imported owner qualifies itself.
    Method on a generic class: `<ns>::<prefix><Record>_<name>
        <owner_type_args>`. (The class-generic + method-generic case is
        rejected at sema, so the two arg lists are not composed today.)

    `extra_template_args` appends the deduced C++ type for each
    static-protocol param on the callee -- the `T_<pname>` template args
    declared on its struct (see `_extra_template_args_for_await`).
    """
    ns_qual = ""
    owner_name = None
    owner_args_suffix = ""
    if owner is not None:
        # Resolve the record rather than reading the owner's rendered C++
        # name: a nested owner (`Outer.Inner`) renders inside its outer
        # class scope, but its frame lives beside the record at module
        # scope under the record's dotted name.
        record = analyzer.registry.get_record_for_type(owner)
        owner_name = record.name if record is not None else owner.name
        if record is not None:
            owner_module = record.defining_module or record.module
            if owner_module and owner_module != analyzer.ctx.module_name:
                ns_qual = f"::{module_to_cpp_namespace(owner_module)}::"
        if owner.type_args:
            inner_cpps = [type_to_cpp(ta) for ta in owner.type_args]
            owner_args_suffix = "<" + ", ".join(inner_cpps) + ">"
    elif module_qual is not None:
        # Cross-module free function: qualify with the callee module's
        # C++ namespace.
        ns_qual = f"::{module_to_cpp_namespace(module_qual)}::"
    bare = frame_struct_name(method, owner_name, shape)
    # Combined template-arg list: callee's explicit `[T1, ...]` from
    # the call's inferred substitution, followed by any
    # `T_<pname>` extras deduced from static-protocol args.
    all_args: list[str] = []
    if inferred_type_args and not (owner is not None and owner.type_args):
        all_args.extend(type_to_cpp(ta) for ta in inferred_type_args)
    if extra_template_args:
        all_args.extend(extra_template_args)
    suffix = ("<" + ", ".join(all_args) + ">") if all_args else ""
    return f"{ns_qual}{bare}{owner_args_suffix}{suffix}"


def recursive_delegation_error(name: str, loc=None) -> CodeGenError:
    """The diagnostic for a generator delegation cycle. Raised both by the
    within-module emit-order sort (which detects the cycle by failing to
    order the units) and by the field-type decision for a callee in a
    module that is a cycle peer of this one -- neither module's header is
    complete for the other, and the frames are mutually infinite-size.

    Its type says the frame can name no slot for that source, so a loop that
    does not need one can decline it instead of failing."""
    return FrameSourceUnnameable(
        f"recursive generator delegation involving '{name}' is not "
        "supported: the delegated generator source is stored by value in "
        "the consumer's frame, so the cycle would be infinite-size. Break "
        "the recursion (e.g. materialize the inner elements with "
        "`list(...)`).", loc=loc)


def sub_struct_qualname(
        owner: 'NominalType | None', method: str,
        inferred_type_args: 'tuple[TpyType, ...] | None' = None,
        *, analyzer=None,
        type_to_cpp: 'Callable[[TpyType], str] | None' = None,
        module_qual: str | None = None,
        extra_template_args: 'list[str] | None' = None,
        loc=None) -> str:
    """The sub-coro struct name for a statically-resolved await -- the
    async face of `frame_struct_qualname` (which owns the naming
    grammar; see its docstring).
    """
    return frame_struct_qualname(
        owner, method, inferred_type_args,
        analyzer=analyzer, type_to_cpp=type_to_cpp,
        module_qual=module_qual, extra_template_args=extra_template_args,
        shape=ResumableShape.ASYNC)
