/******************************************************************************

    ODIN - Open Disk Imager in a Nutshell

    Copyright (C) 2008

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, version 3 of the License.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>

    For more information and the latest version of the source code see
    <http://sourceforge.net/projects/odin-win>

******************************************************************************/

//
// compressioncompat.cpp
//
// MinGW/GCC-compiled static libraries (liblz4_static.lib, libzstd_static.lib)
// contain references to ___chkstk_ms, which is a MinGW compiler helper that
// probes stack pages to avoid guard-page violations on large stack allocations.
//
// When linking those GCC-built libs with MSVC, the linker cannot resolve
// ___chkstk_ms because MSVC uses its own mechanism (_chkstk) inserted by the
// compiler itself.  The two mechanisms are equivalent in effect: MSVC already
// probes every page between the current stack pointer and the new allocation,
// so providing an empty body here is safe — the MSVC-compiled callers have
// already committed the stack before handing control to the MinGW library code.
//
// This stub is compiled only for x64; Win32 builds do not have the MinGW libs.
//

#include "stdafx.h"

#ifdef _WIN64
extern "C" void ___chkstk_ms(void) {}
#endif
