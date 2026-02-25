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
 
/////////////////////////////////////////////////////////////////////////////
// aboutdlg.cpp : interface of the CAboutDlg class
//
/////////////////////////////////////////////////////////////////////////////

#pragma once

#include "stdafx.h"
#include <atlctrls.h>
#include <atlctrlx.h>
#include <atlmisc.h>
#include <string>
#include <sstream>
#include "AboutDlg.h"
#include "resource.h"

using namespace std;

// ── dark mode helpers ────────────────────────────────────────────────────────
static bool AboutDlg_IsDark()
{
  typedef BOOL (WINAPI* PFN_ShouldDark)();
  static bool s_tried = false;
  static PFN_ShouldDark s_pfn = nullptr;
  if (!s_tried) {
    s_tried = true;
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) s_pfn = (PFN_ShouldDark)::GetProcAddress(h, MAKEINTRESOURCEA(132));
  }
  if (s_pfn && s_pfn() != FALSE)
    return true;   // only trust TRUE from ordinal 132
  // Always fall through to registry (reliable on all Win10/11 builds)
  DWORD value = 1, size = sizeof(value);
  HKEY hKey = NULL;
  if (RegOpenKeyExW(HKEY_CURRENT_USER,
        L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
        0, KEY_QUERY_VALUE, &hKey) == ERROR_SUCCESS) {
    RegQueryValueExW(hKey, L"AppsUseLightTheme", nullptr, nullptr,
                     reinterpret_cast<LPBYTE>(&value), &size);
    RegCloseKey(hKey);
  }
  return value == 0;
}

void CAboutDlg::ApplyDarkMode(HWND hwnd)
{
    static thread_local bool s_inThemePass = false;
    if (s_inThemePass)
        return;
    s_inThemePass = true;

  // ── title bar (DWM) ──────────────────────────────────────────────────────
  typedef HRESULT (WINAPI* PFN_DWM)(HWND, DWORD, LPCVOID, DWORD);
  static PFN_DWM pfnDwm = nullptr;
  
  typedef HRESULT (WINAPI* PFN_SWT)(HWND, LPCWSTR, LPCWSTR);
  static PFN_SWT pfnSwt = nullptr;

  const bool dark = AboutDlg_IsDark();
  BOOL d = dark ? TRUE : FALSE;

  if (!pfnDwm) {
    HMODULE h = ::GetModuleHandleW(L"dwmapi.dll");
    if (!h) h = ::LoadLibraryW(L"dwmapi.dll");
    if (h) pfnDwm = (PFN_DWM)::GetProcAddress(h, "DwmSetWindowAttribute");
  }
  if (pfnDwm) {
    if (FAILED(pfnDwm(hwnd, 20, &d, sizeof(d))))
      pfnDwm(hwnd, 19, &d, sizeof(d));
  }

  // ── client-area brushes ──────────────────────────────────────────────────
  if (m_darkBgBrush)   { ::DeleteObject(m_darkBgBrush);   m_darkBgBrush   = nullptr; }
  if (m_darkEditBrush) { ::DeleteObject(m_darkEditBrush); m_darkEditBrush = nullptr; }
  if (dark) {
    m_darkBgBrush   = ::CreateSolidBrush(RGB(32, 32, 32));
    m_darkEditBrush = ::CreateSolidBrush(RGB(45, 45, 48));
  }
  
  // ── child window themes ──────────────────────────────────────────────────
  typedef HRESULT (WINAPI* PFN_SWT)(HWND, LPCWSTR, LPCWSTR);
  static PFN_SWT pfnSwt = nullptr;
  if (!pfnSwt) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) pfnSwt = (PFN_SWT)::GetProcAddress(h, "SetWindowTheme");
  }
  if (pfnSwt) {
    struct Ctx { PFN_SWT swt; bool dark; } c = { pfnSwt, dark };
    ::EnumChildWindows(hwnd, [](HWND child, LPARAM lp) -> BOOL {
      auto& c = *reinterpret_cast<Ctx*>(lp);
      wchar_t cls[64] = {};
      ::GetClassNameW(child, cls, _countof(cls));
      LPCWSTR theme = nullptr;

      if (_wcsicmp(cls, L"Button") == 0) {
        LONG_PTR style = ::GetWindowLongPtr(child, GWL_STYLE);
        BYTE type = (BYTE)(style & 0x0FL);
        if (type == BS_GROUPBOX) { c.swt(child, nullptr, nullptr); return TRUE; }
        theme = c.dark ? L"DarkMode_Explorer" : nullptr;
      }
      else if (_wcsicmp(cls, L"Edit") == 0 || _wcsicmp(cls, L"ComboBox") == 0) {
        theme = c.dark ? L"DarkMode_CFD" : nullptr;
      }
      else if (_wcsicmp(cls, L"SysListView32") == 0) {
        theme = c.dark ? L"DarkMode_Explorer" : nullptr;
      }

      c.swt(child, theme, nullptr);
      return TRUE;
    }, reinterpret_cast<LPARAM>(&c));
  }

  HWND hList = GetDlgItem(IDC_LIST_VOLUMES);
  
  if (hList) {
      // Text + background colors
      ListView_SetTextColor(hList, RGB(212, 212, 212));
      ListView_SetTextBkColor(hList, RGB(32, 32, 32));
      ListView_SetBkColor(hList, RGB(32, 32, 32));
  
      // Apply dark theme
      if (pfnSwt)
          pfnSwt(hList, dark ? L"DarkMode_Explorer" : nullptr, nullptr);
  }

  ::InvalidateRect(hwnd, nullptr, TRUE);

  s_inThemePass = false;
}

LRESULT CAboutDlg::OnDeferredDarkMode(UINT, WPARAM, LPARAM, BOOL&)
{
  ApplyDarkMode(m_hWnd);
  return 0;
}

LRESULT CAboutDlg::OnSettingChange(UINT, WPARAM, LPARAM lParam, BOOL&)
{
  if (lParam && wcscmp(reinterpret_cast<LPCWSTR>(lParam), L"ImmersiveColorSet") == 0)
    ApplyDarkMode(m_hWnd);
  return 0;
}

LRESULT CAboutDlg::OnCtlColor(UINT uMsg, WPARAM wParam, LPARAM /*lParam*/, BOOL& bHandled)
{
    if (!m_darkBgBrush) {
        bHandled = FALSE;
        return 0;
    }

    HDC hdc = reinterpret_cast<HDC>(wParam);

    // Edit controls + listboxes
    if (uMsg == WM_CTLCOLOREDIT || uMsg == WM_CTLCOLORLISTBOX) {
        ::SetTextColor(hdc, RGB(212, 212, 212));
        ::SetBkColor(hdc,   RGB(45, 45, 48));
        bHandled = TRUE;
        return reinterpret_cast<LRESULT>(m_darkEditBrush);
    }

    // Everything else: static, button, group box, dialog, scrollbar
    ::SetTextColor(hdc, RGB(212, 212, 212));
    ::SetBkMode(hdc, TRANSPARENT);
    ::SetBkColor(hdc, RGB(32, 32, 32));
    bHandled = TRUE;
    return reinterpret_cast<LRESULT>(m_darkBgBrush);
}

LRESULT CAboutDlg::OnEraseBkgnd(UINT, WPARAM wParam, LPARAM, BOOL& bHandled)
{
  if (!m_darkBgBrush) { bHandled = FALSE; return 0; }
  RECT rc; ::GetClientRect(m_hWnd, &rc);
  ::FillRect(reinterpret_cast<HDC>(wParam), &rc, m_darkBgBrush);
  bHandled = TRUE;
  return 1;
}

LRESULT CAboutDlg::OnDestroy(UINT, WPARAM, LPARAM, BOOL&)
{
  if (m_darkBgBrush)   { ::DeleteObject(m_darkBgBrush);   m_darkBgBrush   = nullptr; }
  if (m_darkEditBrush) { ::DeleteObject(m_darkEditBrush); m_darkEditBrush = nullptr; }
  return 0;
}
// ── end dark mode helpers ────────────────────────────────────────────────────

const wchar_t creditsText[] = L"The zlib data compression library written by Mark Adler and Jean-loup Gailly \
http://www.zlib.net/.\n\
\n\
The bzip2 data compression library written by Julian Seward (Copyright � 1996-2007\
Julian Seward) http://www.bzip.org/.\n\
\n\
Some ideas, design aspects code fragments taken from Selfimage \
http://selfimage.excelcia.org/";

LRESULT CAboutDlg::OnInitDialog(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM /*lParam*/, BOOL& /*bHandled*/)
{
	CenterWindow(GetParent());
  CStatic versionBox(GetDlgItem(IDC_VERNO));
  CStatic platformBox(GetDlgItem(IDC_PLATFORM));
  ATL::CString platform;
  
  GetVersionFromResource();
  versionBox.SetWindowTextW(versionNumber.c_str());

  if (sizeof(void*)==8)
    platform.LoadString(IDS_VERSION_64_BIT);
  else if (sizeof(void*)==4)
    platform.LoadString(IDS_VERSION_32_BIT);
  platformBox.SetWindowTextW(platform);

  CStatic creditsBox(GetDlgItem(IDC_CREDITS));
  /* Not painted correctly if multiline*/
  /*
  fHyperCtrl.SubclassWindow(creditsBox.m_hWnd);
  fHyperCtrl.SetHyperLinkExtendedStyle ( HLINK_USETAGS);
  fHyperCtrl.SetLabel(creditsText);
  fHyperCtrl.SetHyperLink ( creditsUlr );
  */
  creditsBox.SetWindowText(creditsText);

  CStatic homepageBox(GetDlgItem(IDC_HYPER));
  fHyperHomepage.SubclassWindow(homepageBox.m_hWnd);

  ApplyDarkMode(m_hWnd);      // create brushes before first paint
  PostMessage(WM_APP + 100, 0, 0);  // deferred repaint after window fully visible
	return TRUE;
}

LRESULT CAboutDlg::OnCloseCmd(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
	EndDialog(wID);
	return 0;
}

LRESULT CAboutDlg::OnThemeChanged(UINT, WPARAM, LPARAM, BOOL&)
{
    PostMessage(WM_APP + 100);
    return 0;
}
  
LRESULT CAboutDlg::OnLicenseCmd(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
	wchar_t licenseFile[] = L"\\license.html";
  wchar_t dir[MAX_PATH];
  wchar_t fullPath[MAX_PATH];
  DWORD res = GetModuleFileName(NULL, dir, MAX_PATH);
  if (res > 0 && res < MAX_PATH) {
    res = PathRemoveFileSpec(dir);
    if (res) {
      wcscpy_s(fullPath, MAX_PATH, dir);
      wcscat_s(fullPath, MAX_PATH, licenseFile);
      HINSTANCE h = ShellExecute(this->m_hWnd, L"open", fullPath, NULL, dir, SW_SHOW);
      if (h>(HINSTANCE)32) {
        return 0;
      }
    }
  } 
  AtlMessageBox(this->m_hWnd, IDS_LICENSEFILEMISSING, IDS_ERROR, MB_ICONEXCLAMATION | MB_OK);
  return 0;
}

void CAboutDlg::GetVersionFromResource() 
{
  LPWSTR fileEntry = L"\\StringFileInfo\\040904B0\\FileVersion"; // L"FILEVERSION"
  int i1, i2, i3, i4;

#ifdef _WTL_SUPPORT_SDK_ATL3
  HMODULE hRes =_Module.GetResourceInstance();
#else
  HMODULE hRes = ATL::_AtlBaseModule.GetResourceInstance();
#endif
  HRSRC hVersion = FindResource(hRes, MAKEINTRESOURCE(VS_VERSION_INFO), RT_VERSION);
  if (hVersion != NULL)
  {
    HGLOBAL hGlobal = LoadResource( hRes, hVersion ); 
    if ( hGlobal != NULL)  
    {  
  
      LPVOID versionInfo  = LockResource(hGlobal);  
      if (versionInfo != NULL)
      {
        DWORD i, vLen;
        wostringstream versionStringTmp;
        void* retbuf=NULL;
        LPCWSTR tmp;
        if (VerQueryValue(versionInfo,fileEntry,&retbuf,(UINT *)&vLen)) {
          versionNumber = (LPCWSTR)retbuf;
          tmp = (LPCWSTR) retbuf;
          i1 = _wtoi(tmp);
          i=0;
          while (i < vLen && *++tmp != L',')
            i++;
          ++tmp;
          i2 = _wtoi(tmp);
          while (i < vLen && *++tmp != L',')
            i++;
          ++tmp;
          i3 = _wtoi(tmp);
          while (i < vLen && *++tmp != L',')
            i++;
          ++tmp;
          i4 = _wtoi(tmp);
          versionStringTmp << i1 << L"." << i2 << L"." << i3 << L"." << i4;
          versionNumber = versionStringTmp.str();
        }
      }
      UnlockResource( hGlobal );  
      FreeResource( hGlobal );
    }
  }
}

