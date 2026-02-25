/******************************************************************************

    ODIN - Open Disk Imager in a Nutshell

    Copyright (C) 2008

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, version 3 of the License.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more detail

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>

    For more information and the latest version of the source code see
    <http://sourceforge.net/projects/odin-win>

******************************************************************************/
 
/////////////////////////////////////////////////////////////////////////////
//
// Main dialog class for ODIN
//
///////////////////////////////////////////////////////////////////////////// 

#include "stdafx.h"

#include <atldlgs.h>
#include <Dbt.h>
#include <atlctrls.h>
#include <atlctrlx.h>
#include <sstream>
#include <algorithm>
#include <vector>
//#include <atlmisc.h>

#include "UserFeedback.h"
#include "ODINDlg.h"
#include "DriveList.h"
#include "aboutdlg.h"
#include "InternalException.h"
#include "WriteThread.h"
#include "ReadThread.h"
#include "CompressionThread.h"
#include "DecompressionThread.h"
#include "CompressedRunLengthStream.h"
#include "OSException.h"
#include "ImageStream.h"
#include "PartitionInfoMgr.h"
#include "OptionsDlg.h"
#include "Util.h"
#include "FileNameUtil.h"
#include "MultiPartitionHandler.h"

using namespace std;

#ifdef DEBUG
  #define new DEBUG_NEW
  #define malloc DEBUG_MALLOC
#endif // _DEBUG

// section name in .ini file for configuration values
IMPL_SECTION(CODINDlg, L"MainWindow")
/////////////////////////////////////////////////////////////////////////////
//
// class CODINSplitManagerCallback
//

void CODINSplitManagerCallback::GetFileName(unsigned fileNo, std::wstring& fileName)
{
  wchar_t buf[10];
  wsprintf(buf, L"%04u", fileNo);

  size_t lastDotPos = fileName.rfind(L'.');
  if (lastDotPos < 0)
    fileName += buf;
  else {
    wstring ext = fileName.substr(lastDotPos); // get extension
    fileName = fileName.substr(0, lastDotPos);
    fileName += buf;
    fileName += ext;
  }
}

size_t CODINSplitManagerCallback::AskUserForMissingFile(LPCWSTR missingFileName, unsigned fileNo, wstring& newName)
{
  ATL::CString msg;
  ATL::CString filter;
  size_t res;
  msg.FormatMessage(IDS_PROVIDE_FILENAME, fileNo);
  filter.LoadString(IDS_FILTERSTRING);

  const int bufsize = filter.GetLength()+7;
  std::vector<wchar_t> buffer(bufsize);
  wcsncpy_s(buffer.data(), bufsize, filter, filter.GetLength()+1);
  memcpy(buffer.data()+filter.GetLength()+1, L"\0*.*\0\0", 12);

  CFileDialog fileDlg ( true, NULL, missingFileName, OFN_FILEMUSTEXIST,
            buffer.data(), fhWnd );

  if ( res = fileDlg.DoModal() ) {
    if (res == IDOK) {
      newName = fileDlg.m_szFileName;
    }
  }
  return res;
}

/////////////////////////////////////////////////////////////////////////////
//
// class CODINDlg
//

CODINDlg::CODINDlg()
: fSourceSize(0), fWndPosX(L"XPos", -1000000), fWndPosY(L"YPos", -1000000),
  fLastOperationWasBackup(L"LastOperationWasBackup", true),
  fLastDriveIndex(L"LastDriveIndex", 1),
  fLastImageFile(L"LastImageFile", L""),
  fColumn0Width(L"VolumeColumn0Width", 30),
  fColumn1Width(L"VolumeColumn1Width", 220),
  fColumn2Width(L"VolumeColumn2Width", 90),
  fColumn3Width(L"VolumeColumn3Width", 80),
  fColumn4Width(L"VolumeColumn4Width", 80),
  fAutoFlashEnabled(L"AutoFlashEnabled", false),
  fAutoFlashTargetSizeGB(L"AutoFlashTargetSizeGB", 8),
  fAutoFlashWarningShown(L"AutoFlashWarningShown", false),
  fSplitCB(m_hWnd),
  fVerifyRun(false),
  fTimer(0),
  fWasCancelled(false),
  fFeedback(m_hWnd),
  fChecker(fFeedback, fOdinManager)
{
  fMode = fLastOperationWasBackup ? modeBackup : modeRestore;
  fIsAutoFlashOperation = false;
  ResetRunInformation();
}

CODINDlg::~CODINDlg()
{
  fLastOperationWasBackup = fMode == modeBackup;
}

void CODINDlg::Init() 
{
  fSourceSize = 0;
  fMaxTargetSize = 0;
  fBytesProcessed = 0;
  fSpeed = 0;
  fVerifyRun = false;
}

void CODINDlg::InitControls()
{
  //CContainedWindowT<CButton> saveButton, restoreButton;
  CButton saveButton( GetDlgItem(IDC_RADIO_BACKUP) );
  CButton restoreButton( GetDlgItem(IDC_RADIO_RESTORE) );

  if (fLastImageFile().length() > 0)
      fFiles.push_back(fLastImageFile().c_str());

  if (fMode == modeBackup) {
    saveButton.SetCheck(BST_CHECKED);
    restoreButton.SetCheck(BST_UNCHECKED);
  } else {
    saveButton.SetCheck(BST_UNCHECKED);
    restoreButton.SetCheck(BST_CHECKED);
  }

  // set list view style to enable row selection
  // DWORD exStyle = fVolumeList.GetExtendedListViewStyle();
  fVolumeList.SetExtendedListViewStyle(LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);

  // Fill controls with data
  // fill file list box
	CComboBox in(GetDlgItem(IDC_COMBO_FILES));
  ATL::CString strBrowse;
  strBrowse.LoadString(IDS_BROWSE);

  int count = 0;
	in.ResetContent();
	in.AddString(strBrowse);
	list<wstring>::iterator it;

	it = fFiles.begin();
	while(it != fFiles.end())
	{
		in.AddString(it->c_str());
		it++;
    ++count;
	}
  in.SetCurSel(count);

  // init progress bar to percent range
  CProgressBarCtrl progress = GetDlgItem(IDC_PROGRESS_PERCENT);
  progress.SetRange32(0, 100);

  // fill volume list box
  FillDriveList();

  // get template for additional volume info in text box:
  fVolumeInfoTemplate.LoadString(IDS_VOLUME_INFO);
  
  UpdateFileInfoBoxAndResetProgressControls();
  
  ATL::CString dlgTitle;
  dlgTitle.LoadString(IDS_DIALOGTITLE);
  SetWindowText(dlgTitle);

  if (!fStatusBar.IsWindow())
    fStatusBar.Create(this->m_hWnd);
   
}

// fill the control with the list view with all available drives 
void CODINDlg::FillDriveList()
{
  list<wstring>::iterator it;
  int count = 0;
  const size_t BUFSIZE=80;
  wchar_t buffer[BUFSIZE];
  wstring driveTypeString;

	fVolumeList.DeleteAllItems();
	
  count = fOdinManager.GetDriveCount();
  for (int i=0; i<count; i++) {
    CDriveInfo* di = fOdinManager.GetDriveInfo(i);
    fVolumeList.InsertItem(i, di->GetMountPoint().c_str());
    fVolumeList.SetItemText(i, 1, di->GetDisplayName().c_str());
    MakeByteLabel(di->GetBytes(), buffer, BUFSIZE);
    fVolumeList.SetItemText(i, 2, buffer);
    fVolumeList.SetItemText(i, 3, di->GetVolumeName().c_str());
    GetDriveTypeString(di->GetDriveType(), driveTypeString);
    fVolumeList.SetItemText(i, 4, driveTypeString.c_str());
    int imageIndex = di->GetMountPoint().length() > 0 ? GetImageIndexAndUpdateImageList(di->GetMountPoint().c_str()) : -1;
    fVolumeList.SetItem(i, 0, LVIF_IMAGE, NULL, imageIndex, 0, 0, 0);
  }
}

void CODINDlg::RefreshDriveList()
{
  fOdinManager.RefreshDriveList();
  fOdinManager.GetDriveNameList(fDriveNames);
	InitControls();
}

void CODINDlg::GetPartitionFileSystemString(int partType, ATL::CString& fsString)
{
  switch (partType) {
    case PARTITION_FAT_12:
      fsString.LoadString(IDS_PARTITION_FAT12);
      break;
    case PARTITION_FAT_16:
      fsString.LoadString(IDS_PARTITION_FAT16);
      break;
    case PARTITION_FAT32:
    case PARTITION_FAT32_XINT13:
      fsString.LoadString(IDS_PARTITION_FAT32);
      break;
    case PARTITION_IFS:
      fsString.LoadString(IDS_PARTITION_NTFS);
      break;
    default:
      fsString.LoadString(IDS_PARTITION_UNKNOWN);
  }
}


int CODINDlg::GetImageIndexAndUpdateImageList (LPCWSTR drive)
{
  SHFILEINFO sfi = {0};
  HIMAGELIST ilShell;

	ilShell = (HIMAGELIST)SHGetFileInfo( drive, 0, &sfi, sizeof(sfi), SHGFI_SYSICONINDEX | SHGFI_SMALLICON );
	if (!ilShell)
		return -1;

  if (fVolumeList.GetImageList(TVSIL_NORMAL).m_hImageList != ilShell)
	{
		fVolumeList.SetImageList(ilShell, LVSIL_SMALL);
	}

  return sfi.iIcon;
}


void CODINDlg::BrowseFiles(WORD wID)
{
  CComboBox combo(GetDlgItem(wID)); 
	int index = combo.GetCurSel();
  ReadCommentFromDialog();

	if (index == 0)
	{
		BrowseFilesWithFileOpenDialog();
	}
}

void CODINDlg::BrowseFilesWithFileOpenDialog()
{
  CComboBox combo(GetDlgItem(IDC_COMBO_FILES)); 
  LPCTSTR sSelectedFile;
  ATL::CString fileDescr, defaultExt;
  defaultExt.LoadString(IDS_DEFAULTEXT);
  fileDescr.LoadString(IDS_FILEDESCR);
  // replace separator chars from resource string
  int len = fileDescr.GetLength();
  for (int i=0; i<len; i++)
    if (fileDescr[i] == L'|')
      fileDescr.SetAt(i, L'\0');
  // open file dialog
	CFileDialog fileDlg ( fMode == modeRestore, defaultExt, NULL,
    fMode == modeRestore ? OFN_FILEMUSTEXIST|OFN_HIDEREADONLY : OFN_HIDEREADONLY, fileDescr);
	 
  if ( IDOK == fileDlg.DoModal() ) {
	  sSelectedFile = fileDlg.m_szFileName;
	  int index = combo.FindString(0, sSelectedFile);
	  if (CB_ERR == index) {
		  index = combo.AddString(sSelectedFile);
		  fFiles.push_back(sSelectedFile);
	  }
	  combo.SetCurSel(index);
    UpdateFileInfoBoxAndResetProgressControls();
  }
}


void CODINDlg::OnThreadTerminated()
{
}

void CODINDlg::OnFinished()
{
  fBytesProcessed = fOdinManager.GetBytesProcessed();
  UpdateStatus(false);
  bool wasVerifyRun = fVerifyRun; // will be deleted in DeleteProcessingInfo()!
  DeleteProcessingInfo(fWasCancelled); // work is finished
  if (wasVerifyRun) {
    CheckVerifyResult();
    fVerifyRun = false;
  }
}

void CODINDlg::OnAbort()
{
  DeleteProcessingInfo(true);
}

void CODINDlg::OnPartitionChange(int i, int n)
{
  ATL::CString statusText;

  if (fRestoreRun) {
    if (n > 1)
      statusText.FormatMessageW(IDS_STATUS_RESTORE_DISK_PROGRESS, i+1, n);
    else
      statusText.LoadString(IDS_STATUS_RESTORE_PARTITION_PROGRESS);
  } else if (fBackupRun) {
    if (n > 1)
      statusText.FormatMessageW(IDS_STATUS_BACKUP_DISK_PROGRESS, i+1, n);
    else
      statusText.LoadString(IDS_STATUS_BACKUP_PARTITION_PROGRESS);
  }
  fStatusBar.SetWindowTextW(statusText);
  fTimer = SetTimer(cTimerId, 1000); 
}

void CODINDlg::OnPrepareSnapshotBegin()
{
    ATL::CString statusText;
    statusText.LoadString(IDS_STATUS_TAKE_SNAPSHOT);
    fStatusBar.SetWindowTextW(statusText);
}

void CODINDlg::OnPrepareSnapshotReady()
{
    OnPartitionChange(0, 1);
}

void CODINDlg::DeleteProcessingInfo(bool wasCancelled)
{
  wstring msgCopy;
  LPCWSTR msg = fOdinManager.GetErrorMessage();
  ATL::CString statusText;
   
  KillTimer(fTimer);
  fTimer = 0;

  // we have an error message copy it before thread gets destroyed and then display it.
  if (msg != NULL)
    msgCopy = msg;

  if (msg != NULL)
    	AtlMessageBox(m_hWnd, msgCopy.c_str(), IDS_ERROR, MB_ICONEXCLAMATION | MB_OK);

  Init();
  if (wasCancelled)
    statusText.LoadStringW(IDS_STATUS_OPERATION_CANCELLED);
  else
    statusText.LoadString(IDS_STATUS_OPERATION_COMPLETED);
  fStatusBar.SetWindowTextW(statusText);
}

bool CODINDlg::CheckThreadsForErrors()
{
  return fOdinManager.WasError();
}

void CODINDlg::UpdateStatus(bool bInit)
{
  static LARGE_INTEGER startTimer, performanceFrequency;
  static __int64 nnLastTotal;
  LARGE_INTEGER timer;
  __int64 ddTimeDifference;
  unsigned __int64 nnBytesTotal = fOdinManager.GetTotalBytesToProcess();

  if (bInit) {
    QueryPerformanceFrequency(&performanceFrequency);
    QueryPerformanceCounter(&startTimer);
    nnLastTotal = 0;
    SetDlgItemText(IDC_LABEL_STATUS_LINE, L"");
  } else if (nnBytesTotal) {
    int nProgressPercent = (int)((fBytesProcessed * 100 + (nnBytesTotal / 2)) / nnBytesTotal);

    CProgressBarCtrl progress = GetDlgItem(IDC_PROGRESS_PERCENT);
    progress.SetPos(nProgressPercent);

    const size_t BUFSIZE = 80;
    wchar_t done[BUFSIZE], total[BUFSIZE], speed[BUFSIZE];
    MakeByteLabel(fBytesProcessed, done, BUFSIZE);
    MakeByteLabel(nnBytesTotal, total, BUFSIZE);

    QueryPerformanceCounter(&timer);
    ddTimeDifference = timer.QuadPart - startTimer.QuadPart;
    ddTimeDifference = (ddTimeDifference + (performanceFrequency.QuadPart >> 1)) / performanceFrequency.QuadPart;
    DWORD dwElapsed = (DWORD)ddTimeDifference;

    if (ddTimeDifference > 0)
      MakeByteLabel(fBytesProcessed / ddTimeDifference, speed, BUFSIZE);
    else
      wcscpy_s(speed, BUFSIZE, L"--");

    wchar_t etaBuf[32] = L"--:--:--";
    if (fBytesProcessed > 0) {
      __int64 totalTime = (ddTimeDifference * (__int64)nnBytesTotal + (fBytesProcessed >> 1)) / fBytesProcessed;
      __int64 timeLeft = totalTime - ddTimeDifference;
      if (timeLeft >= 0) {
        DWORD dwLeft = (DWORD)timeLeft;
        swprintf_s(etaBuf, L"%02u:%02u:%02u", dwLeft / 3600, dwLeft % 3600 / 60, dwLeft % 60);
      }
    }

    wchar_t line[256];
    swprintf_s(line, L"Processed: %s / %s   Speed: %s/s   Elapsed: %02u:%02u:%02u   ETA: %s",
               done, total, speed,
               dwElapsed / 3600, dwElapsed % 3600 / 60, dwElapsed % 60,
               etaBuf);
    SetDlgItemText(IDC_LABEL_STATUS_LINE, line);
  }
}

void CODINDlg::ResetProgressControls()
{
  CProgressBarCtrl progress = GetDlgItem(IDC_PROGRESS_PERCENT);
  progress.SetPos(0);
  SetDlgItemText(IDC_LABEL_STATUS_LINE, L"");
}



void CODINDlg::UpdateFileInfoBoxAndResetProgressControls()
{
  UpdateFileInfoBox();
  ResetProgressControls();
}

void CODINDlg::UpdateFileInfoBox()
{
  if (fMode == modeRestore)
    ReadImageFileInformation();
  else
    EnterCommentModeForEditBox();
}

void CODINDlg::EnterCommentModeForEditBox()
{
  // CButton verifyButton(GetDlgItem(ID_BT_VERIFY));
  CEdit commentTextField(GetDlgItem(IDC_EDIT_FILE));
  commentTextField.SetReadOnly(FALSE);
  // verifyButton.EnableWindow(FALSE);
  if (fComment.length() == 0) {
    ATL::CString hint;
    hint.LoadString(IDS_ENTERCOMMENT);
    commentTextField.SetWindowText(hint);
  } else
    commentTextField.SetWindowText(fComment.c_str());
    commentTextField.SetSelAll();
}

void CODINDlg::ReadCommentFromDialog()
{
  CEdit commentTextField(GetDlgItem(IDC_EDIT_FILE));
  // save comment if there is one
  ATL::CString hint;
  hint.LoadString(IDS_ENTERCOMMENT);
  ReadWindowText(commentTextField, fComment);
  if (fComment.compare(hint) == 0)
    fComment.empty();
}

void CODINDlg::ReadImageFileInformation()
{
  // get information from file header and update dialog to show this information
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  // CStatic volumeInfoTextField(GetDlgItem(IDC_TEXT_FILE));
  CEdit volumeInfoTextField(GetDlgItem(IDC_EDIT_FILE));
  ATL::CString text;
  CFileImageStream imageStream;
  wstring fileName;
  // CButton verifyButton(GetDlgItem(ID_BT_VERIFY));

  ReadWindowText(comboFiles, fileName);
  try {
    bool isMBRFile = CFileNameUtil::TestIsHardDiskImage(fileName.c_str());
    if (isMBRFile) {
      wstring tmpFileName, volumeDeviceName;
      CFileNameUtil::GenerateDeviceNameForVolume(volumeDeviceName, 0, 1);
      CFileNameUtil::GenerateFileNameForEntireDiskBackup(tmpFileName, fileName.c_str(), volumeDeviceName);
      fileName = tmpFileName;
    }
    imageStream.Open(fileName.c_str(), IImageStream::forReading);
    const size_t BUFSIZE=80;
    wchar_t buffer[BUFSIZE];
    wchar_t creationDateStr[BUFSIZE];
    wchar_t creationTimeStr[BUFSIZE];
    imageStream.ReadImageFileHeader(false);
    // get comment
    LPCWSTR comment = imageStream.GetComment();

    // get get size of contained volume
    const CImageFileHeader& header = imageStream.GetImageFileHeader();
    unsigned __int64 size = header.GetVolumeSize();
    MakeByteLabel(size, buffer, BUFSIZE);

    // get creation date
    CFindFile finder;
    if ( finder.FindFile (fileName.c_str()))
    {
      FILETIME creationDate;
	    SYSTEMTIME stUTC, stLocal;

      BOOL ok = finder.GetCreationTime(&creationDate);
      if (ok) {
	      // Convert the last-write time to local time.
        FileTimeToSystemTime(&creationDate, &stUTC);
        SystemTimeToTzSpecificLocalTime(NULL, &stUTC, &stLocal);
        GetDateFormat(LOCALE_USER_DEFAULT, DATE_SHORTDATE, &stLocal, NULL,  creationDateStr, BUFSIZE);
        GetTimeFormat(LOCALE_USER_DEFAULT, 0, &stLocal, NULL,  creationTimeStr, BUFSIZE);
      }
    }
    finder.Close();
    text.Format(IDS_HEADERTEMPLATE, creationDateStr, creationTimeStr, buffer, comment);
    // verifyButton.EnableWindow(TRUE);
  } catch (EWinException& e) {
    if (e.GetErrorCode() == EWinException::fileOpenError) {
      text.Format(IDS_CANNOTOPENFILE, fileName.c_str());
      // verifyButton.EnableWindow(FALSE);
    } 
  } catch (Exception& e) {
    // verifyButton.EnableWindow(FALSE);
    wstring msg = e.GetMessage();
    ATLTRACE("can not open file: %S, error: %S", fileName.c_str(), msg.c_str());  
  }

  volumeInfoTextField.SetReadOnly(TRUE);
  volumeInfoTextField.SetWindowText(text);

  imageStream.Close();
}

void CODINDlg::CheckVerifyResult()
{
  DWORD verifyCrc32 = fOdinManager.GetVerifiedChecksum();
  if (fCrc32FromFileHeader == verifyCrc32)
    AtlMessageBox(this->m_hWnd,IDS_VERIFY_OK, IDS_VERIFY_TITLE, MB_OK);
  else
    AtlMessageBox(this->m_hWnd,IDS_VERIFY_FAILED, IDS_VERIFY_TITLE, MB_OK|MB_ICONEXCLAMATION);
}

void CODINDlg::DisableControlsWhileProcessing()
{
  CButton saveButton( GetDlgItem(IDC_RADIO_BACKUP) );
  CButton restoreButton( GetDlgItem(IDC_RADIO_RESTORE) );
  CButton okButton(GetDlgItem(IDOK));
  CButton cancelButton(GetDlgItem(IDCANCEL));
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  CListViewCtrl listBoxVolumes(GetDlgItem(IDC_LIST_VOLUMES));
  CEdit commentTextField(GetDlgItem(IDC_EDIT_FILE));
  ATL::CString text;

  saveButton.EnableWindow(FALSE);
  restoreButton.EnableWindow(FALSE);
  okButton.EnableWindow(FALSE);
  ::EnableMenuItem(GetMenu(), ID_BT_VERIFY,  MF_BYCOMMAND | MF_GRAYED);
  ::EnableMenuItem(GetMenu(), ID_BT_OPTIONS, MF_BYCOMMAND | MF_GRAYED);
  comboFiles.EnableWindow(FALSE);
  listBoxVolumes.EnableWindow(FALSE);
  commentTextField.EnableWindow(FALSE);

  text.LoadString(IDS_CANCEL);
  cancelButton.SetWindowText(text);
}

void CODINDlg::EnableControlsAfterProcessingComplete()
{
  CButton saveButton( GetDlgItem(IDC_RADIO_BACKUP) );
  CButton restoreButton( GetDlgItem(IDC_RADIO_RESTORE) );
  CButton okButton(GetDlgItem(IDOK));
  CButton cancelButton(GetDlgItem(IDCANCEL));
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  CListViewCtrl listBoxVolumes(GetDlgItem(IDC_LIST_VOLUMES));
  CEdit commentTextField(GetDlgItem(IDC_EDIT_FILE));
  ATL::CString text;

  saveButton.EnableWindow(TRUE);
  restoreButton.EnableWindow(TRUE);
  okButton.EnableWindow(TRUE);
  ::EnableMenuItem(GetMenu(), ID_BT_VERIFY,  MF_BYCOMMAND | MF_ENABLED);
  ::EnableMenuItem(GetMenu(), ID_BT_OPTIONS, MF_BYCOMMAND | MF_ENABLED);
  comboFiles.EnableWindow(TRUE);
  listBoxVolumes.EnableWindow(TRUE);
  commentTextField.EnableWindow(TRUE);
  text.LoadString(IDS_EXIT);
  cancelButton.SetWindowText(text);
}

void CODINDlg::CleanupPartiallyWrittenFiles()
{
  BOOL ok = TRUE;
  int index = fVolumeList.GetSelectedIndex();
  CDriveInfo* pDriveInfo = fOdinManager.GetDriveList()->GetItem(index);
  bool isHardDisk = pDriveInfo->IsCompleteHardDisk();
  wstring fileName;
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));

  ReadWindowText(comboFiles, fileName);

  if (isHardDisk && fOdinManager.GetSaveOnlyUsedBlocksOption()) {
    wstring mbrFileName, volumeFileName, filePattern;
    mbrFileName = fileName;
    CFileNameUtil::GenerateFileNameForMBRBackupFile(mbrFileName);
    DeleteFile(mbrFileName.c_str());

    int subPartitions = pDriveInfo->GetContainedVolumes();
    std::vector<CDriveInfo*> pContainedVolumes(subPartitions);
    int res = fOdinManager.GetDriveList()->GetVolumes(pDriveInfo, pContainedVolumes.data(), subPartitions);

    // check  if there are file names in conflict with files created during backup
    for (int i=0; i<subPartitions; i++) {
      CFileNameUtil::GenerateFileNameForEntireDiskBackup(volumeFileName, fileName.c_str(), pContainedVolumes[i]->GetDeviceName());
      CFileNameUtil::GetEntireDiskFilePattern(volumeFileName.c_str(), filePattern);
      fChecker.CheckUniqueFileName(volumeFileName.c_str(), filePattern.c_str(), false);
    }
  } else {
    if (fOdinManager.GetSplitSize() > 0) {
      wstring filePattern;
      CFileNameUtil::GetSplitFilePattern(fSplitCB, fileName.c_str(), filePattern);
      fChecker.CheckUniqueFileName(fileName.c_str(), filePattern.c_str(), false);
    } else {
      DeleteFile(fileName.c_str());
    }
  }
}



void CODINDlg::GetNoFilesAndFileSize(LPCWSTR fileName, unsigned& fileCount,  unsigned __int64& fileSize, bool& isEntireDriveImageFile)
{
  wstring splitFileName(fileName);
  fileCount = 0;
  fileSize = 0;

  fSplitCB.GetFileName(0, splitFileName);

  if (!CFileNameUtil::IsFileReadable(fileName) && CFileNameUtil::IsFileReadable(splitFileName.c_str())) {
    CFileImageStream fileStream;
    fileStream.Open(splitFileName.c_str(), IImageStream::forReading);
    fileStream.ReadImageFileHeader(false);
    fileCount = fileStream.GetImageFileHeader().GetFileCount();  
    fileSize = fileStream.GetImageFileHeader().GetFileSize();
    isEntireDriveImageFile = fileStream.GetImageFileHeader().GetVolumeType() == CImageFileHeader::volumeHardDisk;
    fileStream.Close();
  }
}
void CODINDlg::ResetRunInformation()
{
  fVerifyRun = fBackupRun = fRestoreRun = false;
}

//////////////////////////////////////////////////////////////////////////////////////////////////////
// message handler methods

LRESULT CODINDlg::OnInitDialog(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM /*lParam*/, BOOL& bHandled)
{
  POINT ptWin = {fWndPosX, fWndPosY };
  ATL::CString drive, name, size, label, type;
  // DlgResize_Init();
  
  if (MonitorFromPoint(ptWin, MONITOR_DEFAULTTONULL) == NULL) {
    // center the dialog on the screen
	  CenterWindow();
  }
  else { // point is valid
    SetWindowPos(NULL, fWndPosX, fWndPosY, 0, 0, SWP_NOOWNERZORDER | SWP_NOZORDER|SWP_NOSIZE);
  }

	// set icons
	HICON hIcon = (HICON)::LoadImage(_Module.GetResourceInstance(), MAKEINTRESOURCE(IDD_ODIN_MAIN), 
		IMAGE_ICON, ::GetSystemMetrics(SM_CXICON), ::GetSystemMetrics(SM_CYICON), LR_DEFAULTCOLOR);
	SetIcon(hIcon, TRUE);
	HICON hIconSmall = (HICON)::LoadImage(_Module.GetResourceInstance(), MAKEINTRESOURCE(IDD_ODIN_MAIN), 
		IMAGE_ICON, ::GetSystemMetrics(SM_CXSMICON), ::GetSystemMetrics(SM_CYSMICON), LR_DEFAULTCOLOR);
	SetIcon(hIconSmall, FALSE);

  drive.LoadStringW(IDS_DRIVE);
  name.LoadStringW(IDS_NAME);
  size.LoadStringW(IDS_SIZE);
  label.LoadStringW(IDS_VOLNAME);
  type.LoadStringW(IDS_TYPE);
  fVolumeList = GetDlgItem(IDC_LIST_VOLUMES);
  fVolumeList.AddColumn(drive, 0);
  fVolumeList.AddColumn(name, 1);
  fVolumeList.AddColumn(size, 2);
  fVolumeList.AddColumn(label, 3);
  fVolumeList.AddColumn(type, 4);
  fVolumeList.SetColumnWidth(0, fColumn0Width);
  fVolumeList.SetColumnWidth(1, fColumn1Width);
  fVolumeList.SetColumnWidth(2, fColumn2Width);
  fVolumeList.SetColumnWidth(3, fColumn3Width);
  fVolumeList.SetColumnWidth(4, fColumn4Width);

  RefreshDriveList();

  CStatic volumeInfoTextField(GetDlgItem(IDC_TEXT_VOLUME));
  ATL::CString text;
  text.LoadString(IDS_VOLUME_NOSEL);
  volumeInfoTextField.SetWindowText(text);

  // Load and attach main menu
  HMENU hMenu = LoadMenu(_Module.GetResourceInstance(), MAKEINTRESOURCE(IDR_MAINMENU));
  SetMenu(hMenu);
  ::CheckMenuItem(hMenu, ID_SETTINGS_AUTOFLASH_ENABLE,
      MF_BYCOMMAND | (fAutoFlashEnabled ? MF_CHECKED : MF_UNCHECKED));

  ApplyDarkMode(m_hWnd);       // create brushes before first paint
  PostMessage(WM_APP + 100, 0, 0);   // deferred: repaint after window is fully visible

  bHandled = TRUE;
  return 0L;
}

LRESULT CODINDlg::OnDeviceChanged(UINT /*uMsg*/, WPARAM nEventType, LPARAM lParam, BOOL& bHandled)
{
	if (nEventType == DBT_DEVICEARRIVAL || nEventType == DBT_DEVICEREMOVECOMPLETE)
	{
  	DEV_BROADCAST_VOLUME *volume = (DEV_BROADCAST_VOLUME *)lParam;
    if (volume->dbcv_devicetype == DBT_DEVTYP_VOLUME) {
      CStatic textBox;
      ATL::CString msgText;
      EnableWindow(FALSE);
      msgText.LoadStringW(IDS_WAITUPDATEDRIVES);
      CRect rect(130, 150, 350, 210);
      HWND h = textBox.Create(this->m_hWnd, &rect, msgText, WS_VISIBLE| WS_CHILD| WS_BORDER|WS_CLIPCHILDREN|WS_GROUP|WS_TABSTOP|SS_LEFT|SS_SUNKEN, 0);
      textBox.SetWindowPos(this->m_hWnd,0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
      textBox.SetWindowTextW(msgText);
      CWaitCursor waitCursor;
      RefreshDriveList();
      EnableWindow(TRUE);
      textBox.DestroyWindow();

      // Check for auto-flash trigger
      if (nEventType == DBT_DEVICEARRIVAL && fAutoFlashEnabled && fMode == modeRestore) {
        int cfIndex = DetectCFCard();
        if (cfIndex >= 0) {
          TriggerAutoFlash(cfIndex);
        }
      }
    }
  }

  bHandled = TRUE;
  return 0L;
}

LRESULT CODINDlg::OnOK(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& bHandled)
{
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  int index = fVolumeList.GetSelectedIndex();
  wstring fileName;

  bHandled = TRUE;
  ResetRunInformation();
  ReadWindowText(comboFiles, fileName);

  try {

    UpdateStatus(true);
    fTimer = SetTimer(cTimerId, 1000); 
    fWasCancelled = false;

    if (fMode == modeBackup) {
      bool ok = true;
      ReadCommentFromDialog();
      fOdinManager.SetComment(fComment.c_str());
      if ( fOdinManager.GetSplitSize() > 0) {
        wstring fileNameWithNumber(fileName);
        CFileNameUtil::RemoveTrailingNumberFromFileName(fileNameWithNumber);
        fSplitCB.GetFileName(0, fileNameWithNumber);
        comboFiles.SetWindowText(fileNameWithNumber.c_str());
      }
      // check if save possible:
      IUserFeedback::TFeedbackResult res = fChecker.CheckConditionsForSavePartition(fileName, index);
      if (res == IUserFeedback::TOk || res == IUserFeedback::TYes) {
        DisableControlsWhileProcessing();
        fBackupRun=true;
        CMultiPartitionHandler::BackupPartitionOrDisk(index, fileName.c_str(),fOdinManager, &fSplitCB, this, fFeedback );
        EnableControlsAfterProcessingComplete();
      } 
	  } else if (fMode == modeRestore) {
      unsigned noFiles = 0;
      unsigned __int64 totalSize = 0;
      IUserFeedback::TFeedbackResult res = fChecker.CheckConditionsForRestorePartition(fileName, fSplitCB, index, noFiles, totalSize);
      if (res == IUserFeedback::TOk || res == IUserFeedback::TYes) {
        DisableControlsWhileProcessing();
        fRestoreRun=true;
        CMultiPartitionHandler::RestorePartitionOrDisk(index, fileName.c_str(), fOdinManager, &fSplitCB, this);
        EnableControlsAfterProcessingComplete();
      } 
    } else {
      ATLTRACE("Internal Error neither backup nor restore state");
      return 0;
    }
  } catch (Exception& e) {
    wstring msg = e.GetMessage();
    int res = AtlMessageBox(m_hWnd, msg.c_str(), IDS_ERROR, MB_ICONEXCLAMATION | MB_OK);
    DeleteProcessingInfo(true);
    fOdinManager.Terminate();
    if (::IsWindow(m_hWnd))   // may be destroyed if user pressed Cancel button
       EnableControlsAfterProcessingComplete();
  }
  catch (...) {
    // We've encountered an exception sometime before we started the operation, so close down everything
    // as gracefully as we can.
    DeleteProcessingInfo(true);
    fOdinManager.Terminate();
    if (::IsWindow(m_hWnd))    // may be destroyed if user pressed Cancel button
       EnableControlsAfterProcessingComplete();
  }
  fVolumeList.SetFocus();
  return 0;
}

LRESULT CODINDlg::OnCancel(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
  if (fOdinManager.IsRunning()) {
    int res = AtlMessageBox(m_hWnd, IDS_CONFIRMCANCEL, IDS_WARNING, MB_ICONEXCLAMATION | MB_YESNO);
    if (res == IDYES) {
      fWasCancelled = true;
      fOdinManager.CancelOperation();
      if (::IsWindow(m_hWnd))   // may be destroyed if user pressed Cancel button
        EnableControlsAfterProcessingComplete();
    }
  } else {
    DeleteProcessingInfo(true);
    BOOL ok  = EndDialog(wID);
    if (!ok)
      ok = GetLastError();
  }

  return 0L;
}


LRESULT CODINDlg::OnBnClickedRadioBackup(WORD /*wNotifyCode*/, WORD /*wID*/, HWND hWndCtl, BOOL& /*bHandled*/)
{
  CButton saveButton( hWndCtl );
  TOperationMode oldMode = fMode;

  if (saveButton.GetCheck())
    fMode = modeBackup;

  if (fMode != oldMode) {
    UpdateFileInfoBoxAndResetProgressControls();
  }
  return 0;
}

LRESULT CODINDlg::OnBnClickedRadioRestore(WORD /*wNotifyCode*/, WORD /*wID*/, HWND hWndCtl, BOOL& /*bHandled*/)
{
  CButton restoreButton( hWndCtl );
  TOperationMode oldMode = fMode;
  if (restoreButton.GetCheck())
    fMode = modeRestore;
  if (fMode != oldMode) {
    ReadCommentFromDialog();
    UpdateFileInfoBoxAndResetProgressControls();
  }
  return 0;
}

LRESULT CODINDlg::OnCbnSelchangeComboFiles(WORD /*wNotifyCode*/, WORD wID, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
  BrowseFiles(wID);

  return 0;
}

LRESULT CODINDlg::OnLvnItemchangedListVolumes(int /*idCtrl*/, LPNMHDR pNMHDR, BOOL& /*bHandled*/)
{
  // LPNMLISTVIEW pNMLV = reinterpret_cast<LPNMLISTVIEW>(pNMHDR);
  NM_LISTVIEW* pNMListView = (NM_LISTVIEW*)pNMHDR;
  const size_t BUFSIZE=80;
  wchar_t buffer[BUFSIZE];
  CStatic volumeInfoTextField(GetDlgItem(IDC_TEXT_VOLUME));

  if (pNMHDR->idFrom == IDC_LIST_VOLUMES && (pNMListView->uChanged & LVIF_STATE) &&
      (pNMListView->uNewState & LVIS_SELECTED)) {
    ATL::CString text;
    int index = fVolumeList.GetSelectedIndex();
    if (index < 0) {
      text.LoadString(IDS_VOLUME_NOSEL);
      volumeInfoTextField.SetWindowText(text);
    } else {
      ATL::CString fsText;
      CDriveInfo* di = fOdinManager.GetDriveInfo(index);
      MakeByteLabel(di->GetUsedSize(), buffer, BUFSIZE);
      // GetVolumeInformationW returns the actual FS name (NTFS, exFAT, FAT32 …) for mounted
      // drives, correctly handling GPT partitions that have no MBR type byte.
      wchar_t fsName[32] = L"";
      const std::wstring& mp = di->GetMountPoint();
      if (!mp.empty() &&
          GetVolumeInformationW(mp.c_str(), NULL, 0, NULL, NULL, NULL, fsName, 32) &&
          fsName[0] != L'\0') {
        fsText = fsName;
      } else {
        GetPartitionFileSystemString(di->GetPartitionType(), fsText);
      }
      text.Format(fVolumeInfoTemplate, mp.c_str(), buffer, fsText, di->GetClusterSize());
      volumeInfoTextField.SetWindowText(text);
    }
  }

  return 0;
}

LRESULT CODINDlg::OnTimer(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM /*lParam*/, BOOL& /*bHandled*/)
{
  if (CheckThreadsForErrors()) {
    DeleteProcessingInfo(true);
    return 0;
  }

  fBytesProcessed = fOdinManager.GetBytesProcessed();
  UpdateStatus(false);

  return 0;
}

LRESULT CODINDlg::OnAppAbout(WORD /*wNotifyCode*/, WORD /*wID*/, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
	CAboutDlg dlg;
	dlg.DoModal();
	return 0;
}

LRESULT CODINDlg::OnDestroy(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM /*lParam*/, BOOL& /*bHandled*/)
{
  if (fDarkBgBrush)   { ::DeleteObject(fDarkBgBrush);   fDarkBgBrush   = nullptr; }
  if (fDarkEditBrush) { ::DeleteObject(fDarkEditBrush); fDarkEditBrush = nullptr; }

  // Get column widths to persits value
  fColumn0Width = fVolumeList.GetColumnWidth(0);
  fColumn1Width = fVolumeList.GetColumnWidth(1);
  fColumn2Width = fVolumeList.GetColumnWidth(2);
  fColumn3Width = fVolumeList.GetColumnWidth(3);
  fColumn4Width = fVolumeList.GetColumnWidth(4);
  
  // get name of last image file
  wstring fileName;
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  ReadWindowText(comboFiles, fileName);
	int index = comboFiles.GetCurSel();
	if (index != 0) // do not save Browse... label, but save when index is -1
    fLastImageFile = fileName;
  return 0;
}

LRESULT CODINDlg::OnCbnKillfocusComboFiles(WORD /*wNotifyCode*/, WORD /*wID*/, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
  UpdateFileInfoBoxAndResetProgressControls();

  return 0;
}

LRESULT CODINDlg::OnBnClickedBtVerify(WORD /*wNotifyCode*/, WORD /*wID*/, HWND /*hWndCtl*/, BOOL& bHandled)
{
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  wstring fileName;
  ATL::CString statusText;

  ReadWindowText(comboFiles, fileName);
  fCrc32FromFileHeader = 0;

  try {
    UpdateStatus(true);
    DisableControlsWhileProcessing();

    fTimer = SetTimer(cTimerId, 1000); 
    fVerifyRun = true;
    statusText.LoadString(IDS_STATUS_VERIFY_PROGRESS);
    fStatusBar.SetWindowText(statusText);
    bool run = CMultiPartitionHandler::VerifyPartitionOrDisk(fileName.c_str(), fOdinManager, 
                   fCrc32FromFileHeader, &fSplitCB, this, fFeedback);
    if (!run)
      fVerifyRun = false;
    EnableControlsAfterProcessingComplete();

  } catch (Exception& e) {
    wstring msg = e.GetMessage();
    int res = AtlMessageBox(m_hWnd, msg.c_str(), IDS_ERROR, MB_ICONEXCLAMATION | MB_OKCANCEL);
    DeleteProcessingInfo(true);
    if (::IsWindow(m_hWnd))   // may be destroyed if user pressed Cancel button
       EnableControlsAfterProcessingComplete();
  }
  catch (...) {
    // We've encountered an exception sometime before we started the operation, so close down everything
    // as gracefully as we can.
    DeleteProcessingInfo(true);
    if (::IsWindow(m_hWnd))   // may be destroyed if user pressed Cancel button
       EnableControlsAfterProcessingComplete();
    throw;
  }
  return 0;
}

LRESULT CODINDlg::OnBnClickedBtOptions(WORD /*wNotifyCode*/, WORD /*wID*/, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
  COptionsDlg optionsDlg(fOdinManager);
  size_t res = optionsDlg.DoModal();

  return 0;
}

LRESULT CODINDlg::OnBnClickedBtBrowse(WORD /*wNotifyCode*/, WORD /*wID*/, HWND /*hWndCtl*/, BOOL& /*bHandled*/)
{
  BrowseFilesWithFileOpenDialog();
  return 0;
}

LRESULT CODINDlg::OnAutoFlashEnable(WORD, WORD, HWND, BOOL&)
{
  fAutoFlashEnabled = !fAutoFlashEnabled;
  ::CheckMenuItem(GetMenu(), ID_SETTINGS_AUTOFLASH_ENABLE,
      MF_BYCOMMAND | (fAutoFlashEnabled ? MF_CHECKED : MF_UNCHECKED));

  if (fAutoFlashEnabled && !fAutoFlashWarningShown) {
    ATL::CString msg;
    msg.Format(IDS_ERASE_DRIVE, L"detected removable disks");
    int res = AtlMessageBox(m_hWnd, (LPCWSTR)msg, IDS_WARNING, MB_ICONEXCLAMATION | MB_OKCANCEL);
    if (res != IDOK) {
      fAutoFlashEnabled = false;
      ::CheckMenuItem(GetMenu(), ID_SETTINGS_AUTOFLASH_ENABLE,
          MF_BYCOMMAND | MF_UNCHECKED);
    } else {
      fAutoFlashWarningShown = true;
    }
  }
  return 0;
}

LRESULT CODINDlg::OnAutoFlashSize(WORD, WORD, HWND, BOOL&)
{
  wchar_t msg[128];
  swprintf_s(msg, L"Current target size: %d GB\n(Edit ODIN.ini to change AutoFlashTargetSizeGB)",
             (int)fAutoFlashTargetSizeGB);
  AtlMessageBox(m_hWnd, msg, L"Auto-Flash Size", MB_OK | MB_ICONINFORMATION);
  return 0;
}

int CODINDlg::DetectCFCard()
{
  // Detect removable hard disk (entire disk, not partition) with configurable size
  const unsigned __int64 targetSize = (unsigned __int64)fAutoFlashTargetSizeGB * 1024 * 1024 * 1024; // Convert GB to bytes
  const unsigned __int64 tolerance = targetSize / 10; // 10% tolerance
  
  int count = fOdinManager.GetDriveCount();
  for (int i = 0; i < count; i++) {
    CDriveInfo* di = fOdinManager.GetDriveInfo(i);
    
    // Must be a complete hard disk (not a partition)
    if (!di->IsCompleteHardDisk())
      continue;
    
    // Must be removable type
    if (di->GetDriveType() != driveRemovable)
      continue;
    
    // Check size (8GB ± 10%)
    unsigned __int64 driveSize = di->GetBytes();
    if (driveSize < (targetSize - tolerance) || driveSize > (targetSize + tolerance))
      continue;
    
    // Found a matching 8GB disk
    return i;
  }
  
  return -1; // Not found
}

void CODINDlg::TriggerAutoFlash(int driveIndex)
{
  // Verify we have an image file selected
  CComboBox comboFiles(GetDlgItem(IDC_COMBO_FILES));
  wstring fileName;
  ReadWindowText(comboFiles, fileName);
  
  if (fileName.empty() || fileName == L"Browse...") {
    return; // No image file selected, silently return
  }
  
  // Select the CF card in the drive list
  fVolumeList.SelectItem(driveIndex);
  
  // Trigger the restore operation by simulating OK button click
  CWindow okButton = GetDlgItem(IDOK);
  PostMessage(WM_COMMAND, MAKEWPARAM(IDOK, BN_CLICKED), (LPARAM)okButton.m_hWnd);
}

// ── Dark mode ────────────────────────────────────────────────────────────────
// Title bar:  DwmSetWindowAttribute ordinal (loaded at runtime)
// Client area: AllowDarkModeForWindow (uxtheme ordinal 133) + WM_CTLCOLOR* +
//              SetWindowTheme on native controls.
// GetSysColor(COLOR_WINDOW/WINDOWTEXT/BTNFACE/BTNTEXT) returns the correct
// dark-mode values once SetPreferredAppMode(AllowDark) is called at startup.

static bool IsDarkModeEnabled() {
  // ShouldAppsUseDarkMode (uxtheme ordinal 132): only trust a TRUE answer.
  // When SetPreferredAppMode hasn't taken effect it can return FALSE even though
  // the registry says dark, so always fall through to the registry on FALSE.
  typedef BOOL (WINAPI* PFN_ShouldDark)();
  static bool s_tried = false;
  static PFN_ShouldDark s_pfn = nullptr;
  if (!s_tried) {
    s_tried = true;
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) s_pfn = (PFN_ShouldDark)::GetProcAddress(h, MAKEINTRESOURCEA(132));
  }
  if (s_pfn && s_pfn() != FALSE)
    return true;

  // Definitive ground truth: AppsUseLightTheme = 0 means dark.
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

// EnumChildWindows callback: allow dark mode on every child and signal WM_THEMECHANGED.
using PFN_AllowDark = BOOL (WINAPI*)(HWND, BOOL);
struct DarkEnumCtx { PFN_AllowDark fn; BOOL dark; };
static BOOL CALLBACK s_AllowDarkChild(HWND child, LPARAM lp) {
  auto& ctx = *reinterpret_cast<DarkEnumCtx*>(lp);
  if (ctx.fn) ctx.fn(child, ctx.dark);
  ::SendMessage(child, WM_THEMECHANGED, 0, 0);
  return TRUE;
}

// EnumChildWindows callback: apply the correct uxtheme subapp string per control class.
using PFN_Swt = HRESULT (WINAPI*)(HWND, LPCWSTR, LPCWSTR);
struct ThemeEnumCtx { PFN_Swt swt; bool dark; };

static BOOL CALLBACK s_ThemeChild(HWND child, LPARAM lp) {

  auto& c = *reinterpret_cast<ThemeEnumCtx*>(lp);
  wchar_t cls[64] = {};
  ::GetClassNameW(child, cls, _countof(cls));
  const wchar_t* theme = nullptr;

  if (_wcsicmp(cls, L"Button") == 0) {
    LONG_PTR style = ::GetWindowLongPtr(child, GWL_STYLE);
    BYTE type = (BYTE)(style & 0x0FL);
    if (type == BS_GROUPBOX) {
      // Don't theme group boxes - let WM_CTLCOLORSTATIC handle text
      c.swt(child, nullptr, nullptr);
      return TRUE;
    }
    theme = c.dark ? L"DarkMode_Explorer" : nullptr;
  }
  else if (_wcsicmp(cls, L"Edit")          == 0) theme = c.dark ? L"DarkMode_CFD"      : nullptr;
  else if (_wcsicmp(cls, L"ComboBox")      == 0) theme = c.dark ? L"DarkMode_CFD"      : nullptr;
  else if (_wcsicmp(cls, L"SysListView32") == 0) {
    theme = c.dark ? L"DarkMode_Explorer" : nullptr;
    // Theme the header control (child of the ListView) directly — EnumChildWindows
    // on the dialog doesn't recurse into ListView children.
    HWND hHeader = ListView_GetHeader(child);
    if (hHeader) c.swt(hHeader, c.dark ? L"DarkMode_ItemsView" : nullptr, nullptr);
  }
  else if (_wcsicmp(cls, L"SysHeader32")   == 0) theme = c.dark ? L"DarkMode_ItemsView" : nullptr;
  else if (_wcsicmp(cls, L"Static")        == 0) theme = c.dark ? L"DarkMode_Explorer" : nullptr;

  c.swt(child, theme, nullptr);
  return TRUE;
}

// ── WM_UAHDRAWMENU / WM_UAHDRAWMENUITEM ──────────────────────────────────────
// Undocumented messages Windows sends when it wants to draw the menu bar
// background and each top-level item. Intercepting them lets us owner-draw the
// menu bar in dark mode without any uxtheme theme-name tricks.

struct UAHMENU       { HMENU hMenu; HDC hdc; DWORD dwFlags; };
struct UAHDRAWMENUITEM {
  DRAWITEMSTRUCT dis;
  UAHMENU        um;
  DWORD          unk;
};

LRESULT CODINDlg::OnUahDrawMenu(UINT, WPARAM, LPARAM lParam, BOOL& bHandled) {
  if (!IsDarkModeEnabled() || !fDarkBgBrush) { bHandled = FALSE; return 0; }
  auto* p = reinterpret_cast<UAHMENU*>(lParam);

  // Fill the entire menu bar background. Convert screen coords → window DC coords.
  MENUBARINFO mbi = { sizeof(mbi) };
  ::GetMenuBarInfo(m_hWnd, OBJID_MENU, 0, &mbi);
  RECT rcWindow;
  ::GetWindowRect(m_hWnd, &rcWindow);
  RECT rc = mbi.rcBar;
  ::OffsetRect(&rc, -rcWindow.left, -rcWindow.top);
  ::FillRect(p->hdc, &rc, fDarkBgBrush);

  bHandled = TRUE;
  return 1;
}

LRESULT CODINDlg::OnUahDrawMenuItem(UINT, WPARAM, LPARAM lParam, BOOL& bHandled) {
  if (!IsDarkModeEnabled()) { bHandled = FALSE; return 0; }
  auto* p = reinterpret_cast<UAHDRAWMENUITEM*>(lParam);
  DRAWITEMSTRUCT& dis = p->dis;

  // Use the menu bar HDC from the UAHMENU struct — dis.hDC may be null/invalid.
  HDC hdc = p->um.hdc;
  if (!hdc) { bHandled = FALSE; return 0; }

  // Item background — highlight hovered/selected item.
  COLORREF bg = (dis.itemState & (ODS_HOTLIGHT | ODS_SELECTED))
                  ? RGB(62, 62, 64)
                  : RGB(32, 32, 32);
  HBRUSH br = ::CreateSolidBrush(bg);
  ::FillRect(hdc, &dis.rcItem, br);
  ::DeleteObject(br);

  // Item text — find label by matching rect to position.
  wchar_t text[256] = {};
  HMENU hMenu = ::GetMenu(m_hWnd);
  int count = ::GetMenuItemCount(hMenu);
  RECT rcWindow;
  ::GetWindowRect(m_hWnd, &rcWindow);
  for (int i = 0; i < count; i++) {
    RECT rc = {};
    if (::GetMenuItemRect(m_hWnd, hMenu, i, &rc)) {
      ::OffsetRect(&rc, -rcWindow.left, -rcWindow.top);
      if (rc.left == dis.rcItem.left) {
        ::GetMenuStringW(hMenu, i, text, _countof(text), MF_BYPOSITION);
        break;
      }
    }
  }

  ::SetTextColor(hdc, RGB(212, 212, 212));
  ::SetBkMode(hdc, TRANSPARENT);
  ::DrawTextW(hdc, text, -1, &dis.rcItem, DT_CENTER | DT_VCENTER | DT_SINGLELINE);

  bHandled = TRUE;
  return 1;
}

// WM_INITMENUPOPUP — the popup menu window has just been created but not yet shown.
// We can call AllowDarkModeForWindow + SetWindowTheme on its HWND at this point.
// EnumThreadWindows finds the "#32768" popup window that just appeared on this thread.
LRESULT CODINDlg::OnInitMenuPopup(UINT, WPARAM, LPARAM, BOOL& bHandled) {
  bHandled = FALSE;
  if (!IsDarkModeEnabled()) return 0;

  static PFN_AllowDark pfnAllow = nullptr;
  if (!pfnAllow) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) pfnAllow = (PFN_AllowDark)::GetProcAddress(h, MAKEINTRESOURCEA(133));
  }
  static PFN_Swt pfnSwt = nullptr;
  if (!pfnSwt) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) pfnSwt = (PFN_Swt)::GetProcAddress(h, "SetWindowTheme");
  }

  struct Ctx { PFN_AllowDark allow; PFN_Swt swt; };
  Ctx ctx{ pfnAllow, pfnSwt };
  ::EnumThreadWindows(::GetCurrentThreadId(),
    [](HWND hwnd, LPARAM lp) -> BOOL {
      wchar_t cls[32] = {};
      ::GetClassNameW(hwnd, cls, _countof(cls));
      if (::wcscmp(cls, L"#32768") == 0) {
        auto& c = *reinterpret_cast<Ctx*>(lp);
        if (c.allow) c.allow(hwnd, TRUE);
        if (c.swt)   c.swt(hwnd, L"DarkMode_Explorer", nullptr);
        return FALSE; // stop at first match
      }
      return TRUE;
    },
    reinterpret_cast<LPARAM>(&ctx));

  return 0;
}

LRESULT CODINDlg::OnDeferredDarkMode(UINT, WPARAM, LPARAM, BOOL&) {
  ApplyDarkMode(m_hWnd);
  return 0;
}

void CODINDlg::ApplyDarkMode(HWND hwnd) {

  static thread_local bool s_inThemePass = false;
  if (s_inThemePass)
    return;
  s_inThemePass = true;

  // ── title bar (DWM) ──────────────────────────────────────────────────────
  typedef HRESULT (WINAPI *PFN_Dwm)(HWND, DWORD, LPCVOID, DWORD);
  static PFN_Dwm pfnDwm = nullptr;
  if (!pfnDwm) {
    HMODULE h = ::GetModuleHandleW(L"dwmapi.dll");
    if (!h) h = ::LoadLibraryW(L"dwmapi.dll");
    if (h) pfnDwm = (PFN_Dwm)::GetProcAddress(h, "DwmSetWindowAttribute");
  }
  BOOL dark = IsDarkModeEnabled() ? TRUE : FALSE;
  if (pfnDwm) {
    if (FAILED(pfnDwm(hwnd, 20, &dark, sizeof(dark))))
      pfnDwm(hwnd, 19, &dark, sizeof(dark));
  }

  // ── menu bar / non-client dark (SetWindowCompositionAttribute, user32 by name) ──
  // This is more stable than uxtheme ordinals on newer Win11 builds and
  // covers the menu bar which DwmSetWindowAttribute alone does not darken.
  typedef struct { DWORD Attrib; PVOID pvData; SIZE_T cbData; } SWCA_DATA;
  typedef BOOL (WINAPI* PFN_SWCA)(HWND, SWCA_DATA*);
  static PFN_SWCA pfnSwca = nullptr;
  if (!pfnSwca) {
    HMODULE h = ::GetModuleHandleW(L"user32.dll");
    if (h) pfnSwca = (PFN_SWCA)::GetProcAddress(h, "SetWindowCompositionAttribute");
  }
  if (pfnSwca) {
    SWCA_DATA data = { 26 /*WCA_USEDARKMODECOLORS*/, &dark, sizeof(dark) };
    pfnSwca(hwnd, &data);
  }

  // ── client area (AllowDarkModeForWindow, uxtheme ordinal 133) ────────────
static PFN_AllowDark pfnAllow = nullptr;
if (!pfnAllow) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (!h) h = ::LoadLibraryW(L"uxtheme.dll");
    if (h) pfnAllow = (PFN_AllowDark)::GetProcAddress(h, MAKEINTRESOURCEA(133));
}

if (pfnAllow) {
    pfnAllow(hwnd, dark);

    DarkEnumCtx ctx{ pfnAllow, dark };
    ::EnumChildWindows(hwnd, s_AllowDarkChild, reinterpret_cast<LPARAM>(&ctx));

    ::DrawMenuBar(hwnd);
}

// Recreate dark brushes to match current mode
if (fDarkBgBrush)   { ::DeleteObject(fDarkBgBrush);   fDarkBgBrush   = nullptr; }
if (fDarkEditBrush) { ::DeleteObject(fDarkEditBrush); fDarkEditBrush = nullptr; }
if (dark) {
    fDarkBgBrush   = ::CreateSolidBrush(RGB(32, 32, 32));
    fDarkEditBrush = ::CreateSolidBrush(RGB(45, 45, 48));
}

ApplyThemeToControls();
::RedrawWindow(hwnd, nullptr, nullptr,
               RDW_FRAME | RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);

  s_inThemePass = false;
}

void CODINDlg::ApplyThemeToControls() {
  static PFN_Swt pfnSwt = nullptr;
  if (!pfnSwt) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) pfnSwt = (PFN_Swt)::GetProcAddress(h, "SetWindowTheme");
  }
  if (!pfnSwt) return;

  bool dark = IsDarkModeEnabled();

  // Apply the correct uxtheme subapp string to every child control based on its class.
  // This covers buttons, radios, statics, edits, combos, listview, and its header.
  ThemeEnumCtx ctx{ pfnSwt, dark };
  ::EnumChildWindows(m_hWnd, s_ThemeChild, reinterpret_cast<LPARAM>(&ctx));

  // ListView also needs explicit color assignments beyond the theme string.
  HWND hList = GetDlgItem(IDC_LIST_VOLUMES);
  ListView_SetBkColor(hList,     dark ? RGB(32, 32, 32)   : CLR_DEFAULT);
  ListView_SetTextBkColor(hList, dark ? RGB(32, 32, 32)   : CLR_DEFAULT);
  ListView_SetTextColor(hList,   dark ? RGB(212, 212, 212) : CLR_DEFAULT);

  // Explicitly theme and repaint the ListView's header — it's a grandchild of the
  // dialog so EnumChildWindows on the dialog may not reach it.
  HWND hHeader = ListView_GetHeader(hList);
  if (hHeader) {
    pfnSwt(hHeader, dark ? L"DarkMode_ItemsView" : nullptr, nullptr);
    ::InvalidateRect(hHeader, nullptr, TRUE);
  }

  ::InvalidateRect(hList, nullptr, TRUE);

  // Apply dark theme to the main window itself — this is what tells UxTheme to
  // render the menu bar dark. Children get themed via EnumChildWindows above, but
  // the dialog HWND's own non-client area (menu bar) needs its own SetWindowTheme call.
  pfnSwt(m_hWnd, dark ? L"DarkMode_Explorer" : nullptr, nullptr);

  // FlushMenuThemes (uxtheme ordinal 136) forces popup menus to re-evaluate the theme.
  typedef void (WINAPI* PFN_Flush)();
  static PFN_Flush pfnFlush = nullptr;
  if (!pfnFlush) {
    HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
    if (h) pfnFlush = (PFN_Flush)::GetProcAddress(h, MAKEINTRESOURCEA(136));
  }
  if (pfnFlush) pfnFlush();
  ::DrawMenuBar(m_hWnd);
}

// WM_CTLCOLOR* — covers dialog bg (WM_CTLCOLORDLG), static labels, group boxes,
// radio buttons, edit boxes, listbox drop-downs (range 0x0133..0x0138).
// Brushes are created/deleted by ApplyDarkMode; WM_THEMECHANGED recreates them.
LRESULT CODINDlg::OnCtlColor(UINT uMsg, WPARAM wParam, LPARAM /*lParam*/, BOOL& bHandled) {
  if (!IsDarkModeEnabled() || !fDarkBgBrush) { bHandled = FALSE; return 0; }
  HDC hdc = reinterpret_cast<HDC>(wParam);
  if (uMsg == WM_CTLCOLOREDIT || uMsg == WM_CTLCOLORLISTBOX) {
    ::SetTextColor(hdc, RGB(212, 212, 212));
    ::SetBkColor(hdc,   RGB(45,  45,  48));
    bHandled = TRUE;
    return reinterpret_cast<LRESULT>(fDarkEditBrush);
  }
  // WM_CTLCOLORDLG, WM_CTLCOLORSTATIC, WM_CTLCOLORBTN, WM_CTLCOLORSCROLLBAR
  ::SetTextColor(hdc, RGB(212, 212, 212));
  ::SetBkMode(hdc, TRANSPARENT);  // group box labels need transparent bg mode
  ::SetBkColor(hdc,   RGB(32,  32,  32));
  bHandled = TRUE;
  return reinterpret_cast<LRESULT>(fDarkBgBrush);
}

// WM_ERASEBKGND — paint dialog background dark when in dark mode.
LRESULT CODINDlg::OnEraseBkgnd(UINT /*uMsg*/, WPARAM wParam, LPARAM /*lParam*/, BOOL& bHandled) {
  if (!IsDarkModeEnabled() || !fDarkBgBrush) { bHandled = FALSE; return 1; }
  RECT rc;
  GetClientRect(&rc);
  ::FillRect(reinterpret_cast<HDC>(wParam), &rc, fDarkBgBrush);
  return 1;
}

LRESULT CODINDlg::OnSettingChange(UINT /*uMsg*/, WPARAM /*wParam*/, LPARAM lParam, BOOL& /*bHandled*/) {
  if (lParam && wcscmp((LPCWSTR)lParam, L"ImmersiveColorSet") == 0)
    PostMessage(WM_APP + 100);
  return 0;
}

