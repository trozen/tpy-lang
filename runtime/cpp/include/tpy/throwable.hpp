/**
 * TurboPython Runtime - Throwable ABI
 *
 * Abstract base for the TPy exception hierarchy. Phase 20 (E9) promotes
 * `Throwable` from a markerless `@dynamic` protocol to a real one with
 * two virtuals so user code can store polymorphic exceptions in
 * `Box[Throwable]` and rethrow them without slicing.
 *
 *   - clone()    -- polymorphic copy on the heap (used by Box(e.clone())).
 *   - __raise__() -- throws *this as the dynamic type, preserving the
 *                    concrete subclass through C++ unwinding.
 *
 * what() stays as the std::exception virtual; the BaseException
 * implementation returns the inherited `message` field.
 *
 * Stage 2a (this file) ships the abstract base and a per-class macro
 * that emits the two overrides for every native exception subclass in
 * core.hpp / async.hpp. Stage 4 deletes this macro when codegen auto-
 * emits the same overrides on every TPy-defined Throwable subclass.
 */

#pragma once

#include <exception>
#include <memory>

#include "type_traits.hpp"

namespace tpy {

struct Throwable : std::exception {
    [[nodiscard]] virtual std::unique_ptr<Throwable> clone() const = 0;
    [[noreturn]] virtual void __raise__() const = 0;
    ~Throwable() override = default;
};

}  // namespace tpy

// Marks ::tpy::Throwable as a @dynamic-protocol base for the polymorphism
// predicate `is_polymorphic_class_type`. Normally codegen emits this
// specialization alongside its codegen-emitted abstract base; for
// `@native + @dynamic` protocols the codegen path is suppressed and the
// runtime supplies it.
template<> struct tpy::is_dyn_protocol_base<::tpy::Throwable> : std::true_type {};

// Per-class boilerplate for native exception subclasses. Stage 4 of Phase 20
// retires this macro in favor of codegen auto-emission on every TPy-defined
// Throwable subclass; until then, every concrete native subclass needs both
// overrides explicitly (inheriting them from BaseException would slice the
// dynamic type at clone() / __raise__() sites).
#define TPY_THROWABLE_VIRTUALS(ThisClass) \
    [[nodiscard]] std::unique_ptr<::tpy::Throwable> clone() const override { \
        return std::make_unique<ThisClass>(*this); \
    } \
    [[noreturn]] void __raise__() const override { throw *this; }
