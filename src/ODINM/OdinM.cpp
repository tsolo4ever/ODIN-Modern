/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    OdinM.cpp - Application entry point

******************************************************************************/

#include "stdafx.h"
#include "resource.h"
#include "OdinMDlg.h"

CAppModule _Module;

int WINAPI _tWinMain(HINSTANCE hInstance, HINSTANCE /*hPrevInstance*/,
                     LPTSTR /*lpCmdLine*/, int /*nCmdShow*/)
{
    HRESULT hRes = ::CoInitialize(NULL);
    ATLASSERT(SUCCEEDED(hRes));

    // Ensure the common controls are initialized
    ::DefWindowProc(NULL, 0, 0, 0L);
    AtlInitCommonControls(ICC_BAR_CLASSES | ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES);

    hRes = _Module.Init(NULL, hInstance);
    ATLASSERT(SUCCEEDED(hRes));

    int nRet = 0;
    {
        COdinMDlg dlgMain;
        nRet = (int)dlgMain.DoModal();
    }

    _Module.Term();
    ::CoUninitialize();

    return nRet;
}
