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

} // namespace tpy
