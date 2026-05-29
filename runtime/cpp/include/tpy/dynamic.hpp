/**
 * TurboPython Runtime - Dynamic Protocol Adapters
 *
 * Primary templates for Adapter<Base, T> and RefAdapter<Base, T>.
 * Each @dynamic protocol generates partial specializations with
 * virtual method overrides in the module's generated code.
 */

#pragma once

namespace tpy {

template<typename Base, typename T>
struct Adapter;

template<typename Base, typename T>
struct RefAdapter;

// Narrowing cast for `isinstance` against a STRUCTURAL conformer of a @dynamic
// protocol. A structural `Cat` behind a `Pet*` is physically an
// `Adapter<Pet, Cat>` (owning, from an rvalue source) or a
// `RefAdapter<Pet, Cat>` (from an lvalue source) -- never a `Cat` that IS-A
// `Pet` -- so `dynamic_cast<Cat*>` would always fail. Try both adapter shapes
// and project the contained `.inner`. Returns nullptr when neither matches
// (the object behind the pointer is a different conformer, or null).
template<typename Base, typename T>
T* dyn_adapter_cast(Base* p) {
    if (auto* a = dynamic_cast<Adapter<Base, T>*>(p)) return &a->inner;
    if (auto* r = dynamic_cast<RefAdapter<Base, T>*>(p)) return &r->inner;
    return nullptr;
}

template<typename Base, typename T>
const T* dyn_adapter_cast(const Base* p) {
    if (auto* a = dynamic_cast<const Adapter<Base, T>*>(p)) return &a->inner;
    // RefAdapter's `T& inner` is a reference member: it is not const-propagated
    // through a const adapter, so const-qualify explicitly to honor the
    // const-input overload's `const T*` contract.
    if (auto* r = dynamic_cast<const RefAdapter<Base, T>*>(p))
        return &static_cast<const T&>(r->inner);
    return nullptr;
}

} // namespace tpy
