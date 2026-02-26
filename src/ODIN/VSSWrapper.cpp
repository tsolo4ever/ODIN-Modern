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
 
#pragma once
/////////////////////////////////////////////////////////////////////////////
//
// wrapper class to encapsulate the details of Volume Shadow Copy Service
//
///////////////////////////////////////////////////////////////////////////// 

#include "stdafx.h"
#include "VSSWrapper.h"
#include "VSSException.h"
#include <vector>
#include <string>

using namespace std;

#define CHECK_HRESULT(hr, msg) CHECK_COM_ERROR((hr), EVSSException::vssError, (msg));
#define THROW_VSS_ERROR(msg) (GetLastError(), EVSSException::vssError, (msg));

#ifdef DEBUG
  #define new DEBUG_NEW
  #define malloc DEBUG_MALLOC
#endif // _DEBUG

//=======================================================================================================
class CWriterComponent
{
private:
    wstring _logicalPath;
    bool _logicalPathParsed; 
    wstring _name; 
    CWriterComponent* _pParent; 
//    vector<wstring> _pathComponents;
    bool _selectableForBackup; 
    VSS_COMPONENT_TYPE _type; 
    int _writer; 

public:
    CWriterComponent::CWriterComponent()
    {
        _pParent = NULL; 
    }

    bool get_HasSelectableAncestor(void)
    {
        if (_pParent == NULL)
        {
            return false; 
        }

        if (_pParent->get_SelectableForBackup())
        {
            return true; 
        }

        return _pParent->get_HasSelectableAncestor(); 
    }

    wstring get_LogicalPath(void)
    {
        return _logicalPath; 
    }

    void set_LogicalPath(wstring logicalPath)
    {
        _logicalPath = logicalPath; 
        _logicalPathParsed = false; 
    }

    wstring get_Name(void)
    {
        return _name; 
    }

    void set_Name(wstring value)
    {
        _name = value; 
    }

    CWriterComponent* get_Parent(void)
    {
        return _pParent; 
    }

    void set_Parent(CWriterComponent* value)
    {
        _pParent = value; 
    }

    bool get_SelectableForBackup(void)
    {
        return _selectableForBackup; 
    }

    void set_SelectableForBackup(bool selectableForBackup)
    {
        _selectableForBackup = selectableForBackup; 
    }

    VSS_COMPONENT_TYPE get_Type(void)
    {
        return _type; 
    }

    void set_Type(VSS_COMPONENT_TYPE value)
    {
        _type = value; 
    }

    int get_Writer(void)
    {
        return _writer; 
    }

    void set_Writer(int writer)
    {
        _writer = writer; 
    }
/*
    bool IsAncestorOf(CWriterComponent& potentialDescendant)
    {
        ParseLogicalPath(); 
        potentialDescendant.ParseLogicalPath(); 
        
        if (_pathComponents.size() >= potentialDescendant._pathComponents.size())
        {
            return false; 
        }

        for (unsigned int iPathComponent = 0; 
            iPathComponent < _pathComponents.size(); 
            ++iPathComponent)
        {
            if (potentialDescendant._pathComponents[iPathComponent].Compare(_pathComponents[iPathComponent]) != 0)
            {
                return false; 
            }
        }

        return true; 
    }
*/
    bool IsParentOf(CWriterComponent& potentialParent)
    {
        // The other component is our parent if our logical path is equal to 
        // their logical path plus their name. 
        wstring pathToCompare = potentialParent.get_LogicalPath(); 

        if (pathToCompare.length() > 0)
        {
            pathToCompare.append(TEXT("\\")); 
        }

        pathToCompare.append(potentialParent.get_Name()); 

        return get_LogicalPath().compare(pathToCompare) == 0; 
    }

};


class CWriter
{
private:
    vector<CWriterComponent> _components;
    // vector<CWriterComponent> _componentTree; 
    vector <bool> _addedComponent;
    GUID _instanceId;
    wstring _name; 
    GUID _writerId; 
    
public:
    vector<CWriterComponent>& get_Components(void)
    {
        return _components; 
    }

    GUID get_InstanceId(void)
    {
        return _instanceId; 
    }

    void set_InstanceId(GUID& value)
    {
        _instanceId = value;
    }

    wstring& get_Name(void)
    {
        return _name; 
    }

    void set_Name(wstring name)
    {
        _name = name; 
    }

    GUID get_WriterId(void)
    {
        return _writerId;
    }

    void set_WriterId(GUID& value)
    {
        _writerId = value; 
    }

    void setAddedComponent(unsigned int index, bool isAdded)
    {
      _addedComponent[index] = isAdded;
    }

    bool isAddedComponent(unsigned int index)
    {
      return _addedComponent[index];
    }

    void ComputeComponentTree(void)
    {
        _addedComponent.resize(_components.size(), false);

        for (unsigned int iComponent = 0; iComponent < _components.size(); ++iComponent)
        {
            CWriterComponent& current = _components[iComponent];

            for (unsigned int iComponentParent = 0; iComponentParent < _components.size(); ++iComponentParent)
            {
                if (iComponentParent == iComponent)
                {
                    continue; 
                }

                CWriterComponent& potentialParent = _components[iComponentParent]; 
                if (potentialParent.IsParentOf(current))
                {
                    current.set_Parent(&potentialParent); 
                    break; 
                }
            }
        }
    }
};

//====================================================================================
// class CVssWrapper
//====================================================================================

// Note: For error reporting from VSS with FormatMessage() see:
// http://msdn.microsoft.com/en-us/library/aa819772(VS.85).aspx
// Article: What's New in VSS in Windows Vista
// This seems to work only from Vista on, so we use our own error descriptions



// return true if VSS service is supported on this platform or false if not
bool CVssWrapper::VSSIsSupported() {
  // check if we run or XP or higher
  // check if not run on WIndows PE
  /*
  Basically, the logic can be this:
  -	If the key MiniNT is found in HKLM\System\ControlSet001\Control we are
  running on WinPE
  */
  OSVERSIONINFOEX osInfo;
  HKEY hKey = NULL;
  LONG lRet;
  bool xpOrHigher, winPE;

  // check win pe
  lRet = RegOpenKeyEx( HKEY_LOCAL_MACHINE, L"SYSTEM\\CurrentControlSet\\Control\\MiniNT", 0, KEY_QUERY_VALUE, &hKey );
  if (lRet == ERROR_SUCCESS && hKey != NULL) {
    winPE = true;
    RegCloseKey(hKey);
  } else {
    winPE = false;
  }

  // check version number
  ZeroMemory(&osInfo, sizeof(osInfo));
  osInfo.dwOSVersionInfoSize = sizeof(osInfo);
  BOOL ok = GetVersionEx((LPOSVERSIONINFO)&osInfo);
  if (!ok)
    return false;

  xpOrHigher = osInfo.dwMajorVersion > 5 || (osInfo.dwMajorVersion == 5 && osInfo.dwMinorVersion >= 1);
  bool is32BitRunnninOn64 = Is32BitProcessRunningInWow64();
  return xpOrHigher && !winPE && !is32BitRunnninOn64;

}

bool CVssWrapper::Is32BitProcessRunningInWow64() {
  typedef BOOL (WINAPI *LPFN_ISWOW64PROCESS) (HANDLE, PBOOL);
  LPFN_ISWOW64PROCESS fnIsWow64Process;
  BOOL bIsWow64 = FALSE;

  fnIsWow64Process = (LPFN_ISWOW64PROCESS)GetProcAddress(GetModuleHandle(L"kernel32"),"IsWow64Process");
  if (NULL != fnIsWow64Process)
  {
      BOOL ok = fnIsWow64Process(GetCurrentProcess(), &bIsWow64);
      if (!ok) {
        // todo: log error
      }
  }
  // else kernel function is missing and we are not in an 64Bit emulator
  return bIsWow64 != 0;
}

CVssWrapper::CVssWrapper() {
  fBackupComponents =  NULL; 
  fSnapshotSetId = GUID_NULL; 
  fVssDLL = NULL;
  fSnapshotCreated = false; 
  fAbnormalAbort = true; 
  fSnapshotIds = NULL;
  fSnapshotCount = 0;
  fSnapshotDeviceNames = 0;
  fSnapshotIds = 0;
  Init();
}

CVssWrapper::~CVssWrapper() {
  // COM pointer must be released before the DLL is freed.
  fBackupComponents = NULL;
  if (fVssDLL)
    FreeLibrary(fVssDLL);
  fVssDLL = NULL;
  fSnapshotCount = 0;
  delete [] fSnapshotDeviceNames;
  delete [] fSnapshotIds;
}

void  CVssWrapper::Init()
{
  fAbortFromException = false;

  //load vssapi.dll
  fVssDLL = LoadLibrary(L"vssapi.dll");  
  if (!fVssDLL)
    THROW_VSS_ERROR(L"LoadLibrary() for vssapi.dll failes"); 
}

void CVssWrapper::PrepareSnapshot(const wchar_t** volumeNames, int volumeCount)
{
    int fileCount = 0; 
    LONGLONG byteCount = 0; 
    int directoryCount = 0; 
    int skipCount = 0; 
    VSS_SNAPSHOT_PROP snapshotProperties; 

    try
    {
        const char CreateVssBackupComponentVista[] = "CreateVssBackupComponentsInternal";
        typedef HRESULT (__stdcall *CreateVssBackupComponents)(IVssBackupComponents**);
        CComPtr<IVssAsync> pWriterMetadataStatus;
        HRESULT hrGatherStatus;
//        vector<CWriter> writers;
        UINT cWriters;
        GUID snapshotId;
        WCHAR wszVolumePathName[MAX_PATH];
        CreateVssBackupComponents CreateVssBackupComponentsPtr = NULL;
        HRESULT hrPrepareForBackupResults;
        CComPtr<IVssAsync> pDoSnapshotSetResults;
        HRESULT hrDoSnapshotSetResults;

        CreateVssBackupComponentsPtr = (CreateVssBackupComponents)::GetProcAddress(fVssDLL, CreateVssBackupComponentVista);
        if (!CreateVssBackupComponentsPtr) {
          THROW_VSS_ERROR("Function CreateVssBackupComponents not found in vssapi.dll");
        }

        CHECK_HRESULT(CreateVssBackupComponentsPtr(&fBackupComponents), L"CreateVssBackupComponents");
        CHECK_HRESULT(fBackupComponents->InitializeForBackup(), L"InitializeForBackup");
        CHECK_HRESULT(fBackupComponents->GatherWriterMetadata(&pWriterMetadataStatus), L"GatherWriterMetadata");
        CHECK_HRESULT(pWriterMetadataStatus->Wait(), L"Wait()");
        CHECK_HRESULT(pWriterMetadataStatus->QueryStatus(&hrGatherStatus, NULL), L"QueryStatus");
        if (hrGatherStatus == VSS_S_ASYNC_CANCELLED)
        {
            if (SUCCEEDED(hrGatherStatus))
              hrGatherStatus = E_FAIL;  // throw unspecified error
            CHECK_HRESULT(hrGatherStatus, L"GatherWriterMetadata was cancelled.");
        }
        CHECK_HRESULT(fBackupComponents->GetWriterMetadataCount(&cWriters), L"GetWriterMetadataCount");

        for (UINT iWriter = 0; iWriter < cWriters; ++iWriter)
        {
            CWriter writer; 
            CComPtr<IVssExamineWriterMetadata> pExamineWriterMetadata;
            GUID id;
            GUID idInstance; 
            GUID idWriter; 
            BSTR bstrWriterName;
            VSS_USAGE_TYPE usage; 
            VSS_SOURCE_TYPE source; 
            UINT cIncludeFiles;
            UINT cExcludeFiles; 
            UINT cComponents; 

            CHECK_HRESULT(fBackupComponents->GetWriterMetadata(iWriter, &id, &pExamineWriterMetadata), L"GetWriterMetadata");
            CHECK_HRESULT(pExamineWriterMetadata->GetIdentity(&idInstance, &idWriter, &bstrWriterName, &usage, &source), L"GetIdentity");
            writer.set_InstanceId(idInstance); 
            writer.set_Name(bstrWriterName); 
            writer.set_WriterId(idWriter); 

            CComBSTR writerName(bstrWriterName); 
            //wstring message; 
            //message.AppendFormat(TEXT("Writer %d named %s"), iWriter, (LPCTSTR) writerName); 
            // OutputWriter::WriteLine(message); 

            CHECK_HRESULT(pExamineWriterMetadata->GetFileCounts(&cIncludeFiles, &cExcludeFiles, &cComponents), L"GetFileCounts");

            //message.Empty(); 
            //message.AppendFormat(TEXT("Writer has %d components"), cComponents); 
            // OutputWriter::WriteLine(message); 

            for (UINT iComponent = 0; iComponent < cComponents; ++iComponent)
            {
                CWriterComponent component; 
                CComPtr<IVssWMComponent> pComponent; 
                PVSSCOMPONENTINFO pComponentInfo; 

                CHECK_HRESULT(pExamineWriterMetadata->GetComponent(iComponent, &pComponent), L"GetComponent");

                CHECK_HRESULT(pComponent->GetComponentInfo(&pComponentInfo), L"GetComponentInfo"); 

                //wstring message; 
                /*
                message.AppendFormat(TEXT("Component %d is named %s, has a path of %s, and is %sselectable for backup. %d files, %d databases, %d log files."), 
                    iComponent,
                    pComponentInfo->bstrComponentName, 
                    pComponentInfo->bstrLogicalPath, 
                    pComponentInfo->bSelectable ? TEXT("") : TEXT("not "), 
                    pComponentInfo->cFileCount, 
                    pComponentInfo->cDatabases,
                    pComponentInfo->cLogFiles); 
                    */
                // OutputWriter::WriteLine(message); 

                if (pComponentInfo->bstrLogicalPath)
                  component.set_LogicalPath(pComponentInfo->bstrLogicalPath); 
                component.set_SelectableForBackup(pComponentInfo->bSelectable); 
                component.set_Writer(iWriter); 
                if (pComponentInfo->bstrComponentName)
                  component.set_Name(pComponentInfo->bstrComponentName);
                if (pComponentInfo->type)
                  component.set_Type(pComponentInfo->type);
/*
                for (UINT iFile = 0; iFile < pComponentInfo->cFileCount; ++iFile)
                {
                    CComPtr<IVssWMFiledesc> pFileDesc; 
                    CHECK_HRESULT(pComponent->GetFile(iFile, &pFileDesc)); 

                    CComBSTR bstrPath; 
                    CHECK_HRESULT(pFileDesc->GetPath(&bstrPath)); 

                    CComBSTR bstrFileSpec; 
                    CHECK_HRESULT(pFileDesc->GetFilespec(&bstrFileSpec)); 

                    wstring message; 
                    message.AppendFormat(TEXT("File %d has path %s\\%s"), iFile, bstrPath, bstrFileSpec); 
                    // OutputWriter::WriteLine(message); 
                }

                for (UINT iDatabase = 0; iDatabase < pComponentInfo->cDatabases; ++iDatabase)
                {
                    CComPtr<IVssWMFiledesc> pFileDesc; 
                    CHECK_HRESULT(pComponent->GetDatabaseFile(iDatabase, &pFileDesc)); 

                    CComBSTR bstrPath; 
                    CHECK_HRESULT(pFileDesc->GetPath(&bstrPath)); 

                    CComBSTR bstrFileSpec; 
                    CHECK_HRESULT(pFileDesc->GetFilespec(&bstrFileSpec)); 

                    wstring message; 
                    message.AppendFormat(TEXT("Database file %d has path %s\\%s"), iDatabase, bstrPath, bstrFileSpec); 
                    // OutputWriter::WriteLine(message); 
                }

                for (UINT iDatabaseLogFile = 0; iDatabaseLogFile < pComponentInfo->cLogFiles; ++iDatabaseLogFile)
                {
                    CComPtr<IVssWMFiledesc> pFileDesc; 
                    CHECK_HRESULT(pComponent->GetDatabaseLogFile(iDatabaseLogFile, &pFileDesc)); 

                    CComBSTR bstrPath; 
                    CHECK_HRESULT(pFileDesc->GetPath(&bstrPath)); 

                    CComBSTR bstrFileSpec; 
                    CHECK_HRESULT(pFileDesc->GetFilespec(&bstrFileSpec)); 

                    wstring message; 
                    message.AppendFormat(TEXT("Database log file %d has path %s\\%s"), iDatabaseLogFile, bstrPath, bstrFileSpec); 
                    // OutputWriter::WriteLine(message); 
                }
*/
                CHECK_HRESULT(pComponent->FreeComponentInfo(pComponentInfo), L"FreeComponentInfo"); 

                writer.get_Components().push_back(component); 

            }

            writer.ComputeComponentTree(); 

            for (unsigned int iComponent = 0; iComponent < writer.get_Components().size(); ++iComponent)
            {
                CWriterComponent& component = writer.get_Components()[iComponent]; 
                /*
                wstring message; 
                message.AppendFormat(TEXT("Component %d has name %s, path %s, is %sselectable for backup, and has parent %s"), 
                    iComponent, 
                    component.get_Name(), 
                    component.get_LogicalPath(), 
                    component.get_SelectableForBackup() ? TEXT("") : TEXT("not "), 
                    component.get_Parent() == NULL ? TEXT("(no parent)") : component.get_Parent()->get_Name()); 
                */
                // OutputWriter::WriteLine(message); 
            }

            fWriters.push_back(writer); 
        }

        CHECK_HRESULT(fBackupComponents->StartSnapshotSet(&fSnapshotSetId), L"StartSnapshotSet");

        fSnapshotIds = new GUID[volumeCount];
        fSnapshotDeviceNames = new wstring[volumeCount];
        fSnapshotCount = volumeCount;
        for (int i=0; i < volumeCount; i++) {
          if (volumeNames[i] != NULL ) {
            // mounted volume
            BOOL bWorked = ::GetVolumePathName(volumeNames[i], wszVolumePathName, MAX_PATH); 

            CHECK_HRESULT(fBackupComponents->AddToSnapshotSet(wszVolumePathName, GUID_NULL, &snapshotId), L"AddToSnapshotSet");
            fSnapshotIds[i] = snapshotId;  
          } else {
            // unmounted volume
            fSnapshotIds[i] = GUID_NULL;
          }
        }

        for (unsigned int iWriter = 0; iWriter < fWriters.size(); ++iWriter)
        {
            CWriter writer = fWriters[iWriter];

            //wstring message; 
            //message.AppendFormat(TEXT("Adding components to snapshot set for writer %s"), writer.get_Name()); 
            // OutputWriter::WriteLine(message); 
            for (unsigned int iComponent = 0; iComponent < writer.get_Components().size(); ++iComponent)
            {
                CWriterComponent& component = writer.get_Components()[iComponent];

                bool addedComponent = ShouldAddComponent(component);
                writer.setAddedComponent(iComponent, addedComponent);
                if (addedComponent)
                {
                   /* 
                   wstring message; 
                    message.AppendFormat(TEXT("Adding component %s (%s) from writer %s"), 
                        component.get_Name(), 
                        component.get_LogicalPath(), 
                        writer.get_Name()); 
                    */
                    // OutputWriter::WriteLine(message); 
                    CHECK_HRESULT(fBackupComponents->AddComponent(writer.get_InstanceId(), writer.get_WriterId(),
                      component.get_Type(), component.get_LogicalPath().c_str(), component.get_Name().c_str()),
                      L"AddComponent");
                }
                else
                {
                    //wstring message; 
                    //  //message.AppendFormat(TEXT("Not adding component %s from writer %s."), 
                    //    component.get_Name(), writer.get_Name()); 
                    // OutputWriter::WriteLine(message); 
                }
            }
        }

        {
          CComPtr<IVssAsync> pPrepareForBackupResults;
          CHECK_HRESULT(fBackupComponents->SetBackupState(FALSE, TRUE, VSS_BT_COPY, FALSE), L"SetBackupState");
          CHECK_HRESULT(fBackupComponents->PrepareForBackup(&pPrepareForBackupResults), L"PrepareForBackup");
          CHECK_HRESULT(pPrepareForBackupResults->Wait(), L"Wait");
          CHECK_HRESULT(pPrepareForBackupResults->QueryStatus(&hrPrepareForBackupResults, NULL), L"QueryStatus");
        }

        if (hrPrepareForBackupResults != VSS_S_ASYNC_FINISHED)
        {
          if (SUCCEEDED(hrPrepareForBackupResults))  
            hrPrepareForBackupResults = E_FAIL;  // throw unspecified error
          CHECK_HRESULT(hrPrepareForBackupResults, L"Prepare for backup failed."); 
        }

        CHECK_HRESULT(fBackupComponents->DoSnapshotSet(&pDoSnapshotSetResults), L"DoSnapshotSet");
        CHECK_HRESULT(pDoSnapshotSetResults->Wait(), L"Wait");

        fSnapshotCreated = true; 

        CHECK_HRESULT(pDoSnapshotSetResults->QueryStatus(&hrDoSnapshotSetResults, NULL), L"QueryStatus");

        if (hrDoSnapshotSetResults != VSS_S_ASYNC_FINISHED)
        {
          if (SUCCEEDED(hrDoSnapshotSetResults))  
            hrDoSnapshotSetResults = E_FAIL;  // throw unspecified error

          CHECK_HRESULT(hrDoSnapshotSetResults, L"DoSnapshotSet failed.");
        }

        for (int i=0; i < volumeCount; i++) {
          if (fSnapshotIds[i] == GUID_NULL) {
            fSnapshotDeviceNames[i].clear();
          } else {
            CHECK_HRESULT(fBackupComponents->GetSnapshotProperties(fSnapshotIds[i], &snapshotProperties), L"GetSnapshotProperties");
            fSnapshotDeviceNames[i] = snapshotProperties.m_pwszSnapshotDeviceObject;
            }
        }

    }
    catch (EVSSException& e)
    {
        fAbortFromException = true;
        Cleanup();
        throw e; 
    }
}

const wchar_t* CVssWrapper::GetSnapshotDeviceName(int driveIndex) 
{
  return fSnapshotDeviceNames[driveIndex].empty() ? NULL : fSnapshotDeviceNames[driveIndex].c_str() + 14;
     // remove prefix \\?\GLOBALROOT
}


void CVssWrapper::ReleaseSnapshot(bool wasAborted)
{
  try {
        CComPtr<IVssAsync> pBackupCompleteResults;
        HRESULT hrBackupCompleteResults = VSS_S_ASYNC_PENDING;

        if (fBackupComponents == NULL)
        {
            return;
        }
        ReportBackupResultsToWriters(!wasAborted);

        if (fSnapshotCreated)
        {
          if (fBackupComponents) {
            CHECK_HRESULT(fBackupComponents->BackupComplete(&pBackupCompleteResults), L"BackupComplete");
          }
          if (pBackupCompleteResults) {
            if (wasAborted) {
              CHECK_HRESULT(pBackupCompleteResults->Cancel(), L"Cancel");
            }
            CHECK_HRESULT(pBackupCompleteResults->Wait(), L"Wait");
            CHECK_HRESULT(pBackupCompleteResults->QueryStatus(&hrBackupCompleteResults, NULL), L"QueryStatus");
          }
        }

        if (hrBackupCompleteResults != VSS_S_ASYNC_FINISHED)
        {
            CHECK_HRESULT(hrBackupCompleteResults, L"Completion of backup failed."); 
        }
        fAbnormalAbort = false;

  }
  catch (EVSSException& e) {
        fAbortFromException = true;
        Cleanup();
        throw e; 
  }
  Cleanup();
}

void CVssWrapper::ReportBackupResultsToWriters(bool successful) 
{
  for (unsigned int iWriter = 0; iWriter < fWriters.size(); ++iWriter)
  {
    CWriter& writer = fWriters[iWriter];

    for (unsigned int iComponent = 0; iComponent < writer.get_Components().size(); ++iComponent)
    {
      if (writer.isAddedComponent(iComponent)) 
      {
        CWriterComponent& component = writer.get_Components()[iComponent];

        CHECK_HRESULT(fBackupComponents->SetBackupSucceeded(writer.get_InstanceId(), writer.get_WriterId(),
          component.get_Type(), component.get_LogicalPath().c_str(), component.get_Name().c_str(), successful),
          L"SetBackupSucceeded");
      }
    }
  }
}

void CVssWrapper::CalculateSourcePath(LPCTSTR wszSnapshotDevice, LPCTSTR wszBackupSource, LPCTSTR wszMountPoint, wstring& output)
{
    wstring backupSource(wszBackupSource); 
    wstring mountPoint(wszMountPoint); 

    wstring subdirectory = backupSource.substr(mountPoint.length()); 

    CombinePath(wszSnapshotDevice, subdirectory.c_str(), output); 
}

void CVssWrapper::Cleanup()
{
    if (fBackupComponents == NULL)
    {
        return;
    }

    try {
      if (fAbnormalAbort)
      {
          fBackupComponents->AbortBackup();
          if (fSnapshotCreated)
          {
              LONG cDeletedSnapshots;
              GUID nonDeletedSnapshotId;
              fBackupComponents->DeleteSnapshots(fSnapshotSetId, VSS_OBJECT_SNAPSHOT_SET, TRUE,
                  &cDeletedSnapshots, &nonDeletedSnapshotId);// omit CHECK_HRESULT(, L"DeleteSnapshots");
          }
      }
    }
    catch (EVSSException& e) {
      // if cleanup was called from raising an exception than it is probable that the cleanup actions
      // cause another exception that we simply ignore then
      if (!fAbortFromException) {
        fAbnormalAbort = true;
        fSnapshotCreated = false;
        fBackupComponents = NULL;
        throw e;
      }
    }
    fAbnormalAbort = true;
    fSnapshotCreated = false;
    fBackupComponents = NULL;
}


bool CVssWrapper::ShouldAddComponent(CWriterComponent& component)
{
    // Component should not be added if
    // 1) It is not selectable for backup and 
    // 2) It has a selectable ancestor
    // Otherwise, add it. 

    if (component.get_SelectableForBackup())
    {
        return true; 
    }

    return !component.get_HasSelectableAncestor();

}

bool CVssWrapper::EndsWith(LPCTSTR wsz, size_t maxLength, TCHAR wchar)
{
    wstring s(wsz); 

    size_t length = s.length(); 

    if (length == 0)
    {
        return false; 
    }

    if (s[length - 1] == wchar)
    {
        return true; 
    }

    return false; 
}

void CVssWrapper::CombinePath(LPCTSTR wszPath1, LPCTSTR wszPath2, wstring& output)
{
    output.clear(); 
    output.append(wszPath1); 

    if (output.length() > 0)
    {
        if (!EndsWith(wszPath1, MAX_PATH, TEXT('\\')))
        {
            output.append(TEXT("\\")); 
        }
    }

    output.append(wszPath2); 
}
