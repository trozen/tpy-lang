# Nested-def with plain (non-Unpack) typed `**kwargs` is parser-rejected
# at `_parse_unpack_annotation` (parser.py): TPy requires
# `**kwargs: Unpack[TypedDict]` and refuses bare type annotations. The
# reject fires before `_finalize_function_refs` runs, so this case
# does NOT exercise the kwarg_type-leak fix; the companion case
# `error_kwargs_nested_unpack` covers that. This case locks in the
# parser-side reject so the rule itself doesn't regress.
def outer() -> None:
    def inner(**kw: int) -> None:  # tpyc: error(/Unpack\[TypedDict\]/)
        print("x")
    inner(a=1)


outer()
