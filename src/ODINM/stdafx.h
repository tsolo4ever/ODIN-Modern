/******************************************************************************

    OdinM - Multi-Drive Clone Tool

    Copyright (C) 2026

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, version 3 of the License.

******************************************************************************/

#pragma once

// Modify the following defines if you have to target a platform prior to the ones specified below.
#ifndef WINVER
#define WINVER 0x0601		// Windows 7 or later
#endif

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601	// Windows 7 or later
#endif

#ifndef _WIN32_IE
#define _WIN32_IE 0x0700	// IE 7.0 or later
#endif

#define _WTL_NO_CSTRING
#define _ATL_APARTMENT_THREADED
#define _ATL_NO_AUTOMATIC_NAMESPACE
#define _ATL_CSTRING_EXPLICIT_CONSTRUCTORS	// some CString constructors will be explicit
#define _ATL_ALL_WARNINGS

#include <atlbase.h>
#include <atlapp.h>

extern CAppModule _Module;

#include <atlwin.h>
#include <atlframe.h>
#include <atlctrls.h>
#include <atldlgs.h>
#include <atlctrlx.h>
#include <atlmisc.h>

// Standard C++ headers
#include <string>
#include <vector>
#include <map>
#include <memory>
#include <algorithm>

// Windows headers
#include <windows.h>
#include <winioctl.h>
#include <wincrypt.h>
#include <shlwapi.h>

#if defined _M_IX86
  #pragma comment(linker, "/manifestdependency:\"type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='x86' publicKeyToken='6595b64144ccf1df' language='*'\"")
#elif defined _M_IA64
  #pragma comment(linker, "/manifestdependency:\"type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='ia64' publicKeyToken='6595b64144ccf1df' language='*'\"")
#elif defined _M_X64
  #pragma comment(linker, "/manifestdependency:\"type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='amd64' publicKeyToken='6595b64144ccf1df' language='*'\"")
#else
  #pragma comment(linker, "/manifestdependency:\"type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'\"")
#endif

// Link required libraries
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "version.lib")
