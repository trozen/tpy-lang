"""Every FlowFacts family is classified for the comprehension exit's scoped
sweep (`init_tracker._scoped_to_fork`), so a new family cannot skip it."""

from dataclasses import fields

from .flow_facts import FlowFacts
from .init_tracker import NAME_KEYED_FAMILIES, UNKEYED_FAMILIES


def test_every_flow_family_is_classified() -> None:
    families = [f.name for f in fields(FlowFacts)]
    classified = list(NAME_KEYED_FAMILIES) + list(UNKEYED_FAMILIES)
    assert sorted(classified) == sorted(families)
