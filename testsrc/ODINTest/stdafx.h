// stdafx.h : include file for standard system include files,
// or project specific include files that are used frequently, but
// are changed infrequently
//

#pragma once

#include <stdio.h>
#include <tchar.h>

// Change these values to use different versions
#define WINVER		0x0500
#define _WIN32_WINNT 0x0502
#define _WIN32_IE	0x0502
#define _RICHEDIT_VER	0x0200

// Modern VS2026 with WTL 10 - no ATL3 workarounds needed
#define _CRT_NON_CONFORMING_SWPRINTFS
#define _CRT_SECURE_NO_DEPRECATE

#include <atlbase.h>

#include <atlapp.h>

extern CAppModule _Module;

#include <atlwin.h>

// to track memory allocations:
#include "..\..\src\ODIN\DebugMem.h"

