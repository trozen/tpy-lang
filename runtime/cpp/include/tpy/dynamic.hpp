/**
 * TurboPython Runtime - Dynamic Protocol Adapters
 *
 * Primary templates for Adapter<Base, T> and RefAdapter<Base, T>.
 * Each @dynamic protocol generates partial specializations with
 * virtual method overrides in the module's generated code.
 */

#pragma once

#include <type_traits>
#include <memory>
#include <utility>

namespace tpy {

// Compile-time `isinstance(x, C)` for a generic type-parameter subject. Sema
// restricts this lowering to a non-polymorphic class-bounded `T`, where the
// static instantiation type IS the dynamic type, so a same-or-derived test is
// exact per instantiation. Polymorphic (vtable), union, and `Any` subjects do
// NOT reach here -- they keep their dynamic_cast / holds_alternative / typeid
// lowerings. Normalizes ref/pointer/cv on both sides before the trait test.
template<typename C, typename X>
constexpr bool isinstance_static() {
    using CT = std::remove_cv_t<std::remove_pointer_t<std::remove_reference_t<C>>>;
    using XT = std::remove_cv_t<std::remove_pointer_t<std::remove_reference_t<X>>>;
    // A polymorphic instantiation breaks the static premise: the runtime
    // object behind an `XT&` may be a subclass, so a compile-time test would
    // silently miss it. Sema rejects a polymorphic *bound*, but a polymorphic
    // subclass of a non-polymorphic bound slips through (BUGS.md); fail loud
    // here rather than fold to a wrong answer. Temporary -- drop once the
    // call-site substituted-type revalidation / dispatcher lands.
    static_assert(!std::is_polymorphic_v<XT>,
        "isinstance() on a generic type parameter instantiated with a "
        "polymorphic type is not supported: the compile-time check cannot see "
        "the runtime dynamic type. Use a @dynamic protocol parameter for "
        "runtime dispatch.");
    return std::is_base_of_v<CT, XT> || std::is_same_v<CT, XT>;
}

template<typename Base, typename T>
struct Adapter;

template<typename Base, typename T>
struct RefAdapter;

// Deduces Concrete from the argument so a call site whose concrete type is not
// sema-visible need not spell it via `decltype` (which would force the argument
// expression to appear twice).
template<typename Base, typename Concrete>
std::unique_ptr<Base> make_adapter(Concrete&& c) {
    return std::make_unique<Adapter<Base, std::remove_cvref_t<Concrete>>>(
        std::forward<Concrete>(c));
}

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
