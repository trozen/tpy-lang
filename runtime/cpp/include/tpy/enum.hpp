/**
 * TurboPython Runtime - Enum Utilities
 *
 * Primary template for EnumUtil<T>. Each TurboPython enum generates
 * an explicit specialization with name(), members, from_value(), and
 * try_parse() in the module's generated code.
 */

#pragma once

namespace tpy {

template<typename T>
struct EnumUtil;

} // namespace tpy
