"""Message-family classifier for lifetime/borrow/loan/escape diagnostics."""
import re

FAMILIES: list[tuple[str, re.Pattern]] = [(n, re.compile(p, re.I)) for n, p in [
    ("iter_invalidation", r"Mutation of '.*' (while iterating over it|may hit the element being iterated)"),
    ("borrow_invalidation", r"Mutation of '.*' (while borrowed|may hit a borrowed element)"),
    ("borrowed_container_arg", r"Passing borrowed container"),
    ("genexpr_capture_mutation", r"is mutated by a generator expression that captures it"),
    ("match_binding_dangle", r"pattern bindings borrow its storage"),
    ("loop_frame_hold", r"cannot keep '.*' open across passes of this loop|after the loop it is bound in"),
    ("with_block_frame", r"after the '.*' block it is bound in|is not readable after the '.*' block|open past the 'with' block"),
    ("yield_dangle", r"Cannot yield .*(local|temporary|dangle)"),
    ("generator_element_escape", r"borrows an element of a generator"),
    ("global_rebind_borrow", r"borrow of module variable|module-level unpack borrowed"),
    ("return_dangle", r"Cannot return .*(local|temporary|dangle)|Cannot return this tuple|returned pointer.* would dangle|Cannot return pointer to local"),
    ("outlive", r"may outlive its storage"),
    ("temporary_borrow", r"borrows from temporary|temporary is destroyed|into a temporary would dangle|Cannot store a temporary|mutable pointer to read-only or temporary|lends_from_temporary|StrView \(result would dangle\)"),
    ("closure_capture", r"Escaping closure|in escaping closure|nested function '.*' defined in (an async function|a generator) cannot escape|captures str parameter"),
    ("suspension_borrow", r"across a suspension|until the next yield or await|outlives the loop iteration|stable lvalue|borrow-returning coroutine|borrows its receiver by reference|dangle across a suspension"),
    ("coroutine_handle_borrow", r"coroutine handle|borrowed coroutine reference"),
    ("finally_return_borrow", r"still borrows it \(the value is materialized"),
    ("borrow_into_own", r"borrow as owned|borrowed source|Own\[.*\] from a borrow"),
    ("readonly_ref", r"non-readonly method '.*' on readonly|Cannot mutate readonly|readonly source"),
]]
CATCH = re.compile(r"borrow|dangl|outliv|escap|lifetime|while iterating", re.I)
# Messages that mention the keywords but are not lifetime checks.
EXCLUDE = re.compile(r"not yet supported by C\+\+ code generation|CPython boundary|returns a copy of|Cyclic import|is not Send|is not Sync|^@export|@native\(borrowing_view|Own\[T\] is redundant|is only supported at a parameter position|is not yet supported: an Own-Optional|nested borrow slot is miscompiled|falls back to '__add__'|iterating a conditional whose arms", re.I)


def family(msg: str) -> str | None:
    if EXCLUDE.search(msg):
        return None
    for name, rx in FAMILIES:
        if rx.search(msg):
            return name
    if CATCH.search(msg):
        return "other_lifetime"
    return None
