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
// aboutdlg.h : interface of the CAboutDlg class
//
/////////////////////////////////////////////////////////////////////////////

#pragma once

#include <string>
#include "resource.h"
#include <atlctrls.h>
#include <atlctrlx.h>


class CAboutDlg : public CDialogImpl<CAboutDlg>
{
public:
	enum { IDD = IDD_ABOUTBOX };

	BEGIN_MSG_MAP(CAboutDlg)
		MESSAGE_HANDLER(WM_INITDIALOG, OnInitDialog)
		COMMAND_ID_HANDLER(IDOK, OnCloseCmd)
		COMMAND_ID_HANDLER(IDCANCEL, OnCloseCmd)
    COMMAND_HANDLER(IDC_BUTTON_LICENSE, BN_CLICKED, OnLicenseCmd)

    MESSAGE_HANDLER(WM_SETTINGCHANGE, OnSettingChange)
    MESSAGE_HANDLER(WM_THEMECHANGED, OnThemeChanged)
    MESSAGE_HANDLER(WM_APP + 100, OnDeferredDarkMode)

    MESSAGE_HANDLER(WM_ERASEBKGND, OnEraseBkgnd)
    MESSAGE_HANDLER(WM_CTLCOLORDLG, OnCtlColor)
    MESSAGE_RANGE_HANDLER(WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC, OnCtlColor)
    MESSAGE_HANDLER(WM_DESTROY, OnDestroy)
  END_MSG_MAP()

	LRESULT OnInitDialog(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM /*lParam*/, BOOL& /*bHandled*/);
	LRESULT OnCloseCmd(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/);
	LRESULT OnLicenseCmd(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/);

  LRESULT OnThemeChanged(UINT, WPARAM, LPARAM, BOOL&);
  LRESULT OnDeferredDarkMode(UINT, WPARAM, LPARAM, BOOL&);
  LRESULT OnSettingChange(UINT, WPARAM, LPARAM, BOOL&);
  LRESULT OnCtlColor(UINT uMsg, WPARAM wParam, LPARAM lParam, BOOL& bHandled);
  LRESULT OnEraseBkgnd(UINT, WPARAM wParam, LPARAM, BOOL& bHandled);
  LRESULT OnDestroy(UINT, WPARAM, LPARAM, BOOL&);
  void    ApplyDarkMode(HWND hwnd);

private:
  std::wstring versionNumber;
  CHyperLink fHyperHomepage;
  HBRUSH m_darkBgBrush   = nullptr;
  HBRUSH m_darkEditBrush = nullptr;

  void GetVersionFromResource();
};
