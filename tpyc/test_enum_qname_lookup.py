"""Unit tests for TypeRegistry.find_enum_by_qname.

Guards the enum qname resolver against the bare-name collision class: an
enum imported under an alias whose canonical name matches an unrelated
LOCAL enum. The bare `registry.enums` slot is won by the local enum, so a
canonical-name lookup for the FOREIGN enum must route through the declaring
module's own `ModuleInfo.enums` dict instead of trusting the bare slot.
"""

from tpyc.typesys import TypeRegistry, ModuleInfo, NominalType


def _enum(qname: str) -> NominalType:
    short = qname.rsplit(".", 1)[1]
    return NominalType(name=short, type_args=(), _module_qname=qname)


def test_foreign_enum_resolves_despite_local_bareslot_collision():
    reg = TypeRegistry()
    local = _enum("__main__.Color")
    foreign = _enum("colors.Color")
    # Local declaration wins its own bare slot (mirrors sub-phase ordering).
    reg.enums["Color"] = local
    reg.register_module(ModuleInfo(name="colors", enums={"Color": foreign}))

    # Canonical-name lookup for the foreign enum must NOT return the local
    # enum that occupies the bare slot.
    assert reg.find_enum_by_qname("colors.Color") is foreign
    # And the current-module lookup still resolves the local enum via the
    # short-name fallback (its ModuleInfo is absent during its own analyze).
    assert reg.find_enum_by_qname("__main__.Color") is local


def test_bare_name_fallback_when_module_absent():
    reg = TypeRegistry()
    local = _enum("__main__.Color")
    reg.enums["Color"] = local
    # No ModuleInfo for the qualified module -> fall back to the bare slot,
    # matching the prior get_enum(short) behavior (never a regression).
    assert reg.find_enum_by_qname("nowhere.Color") is local
    assert reg.find_enum_by_qname("Color") is local
    assert reg.find_enum_by_qname("Missing") is None
