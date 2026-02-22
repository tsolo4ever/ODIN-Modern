// stdafx.h : include file for standard system include files,
// or project specific include files that are used frequently, but
// are changed infrequently
//

#pragma once

#include <stdio.h>
#include <tchar.h>
#include <memory>

// Change these values to use different versions
// WTL 10 requires WINVER >= 0x0501 (Windows XP or higher)
// WTL 10 requires _RICHEDIT_VER >= 0x0300
#define WINVER		0x0501
#define _WIN32_WINNT	0x0501
#define _WIN32_IE	0x0600
#define _RICHEDIT_VER	0x0300

// Modern VS2026 with WTL 10 - no ATL3 workarounds needed
#define _CRT_NON_CONFORMING_SWPRINTFS
#define _CRT_SECURE_NO_DEPRECATE

#include <atlbase.h>
#include <atlstr.h>  // ATL::CString

#include <atlapp.h>

extern CAppModule _Module;

#include <atlwin.h>

// to track memory allocations:
#include "..\..\src\ODIN\DebugMem.h"

