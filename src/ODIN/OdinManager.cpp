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
 
/////////////////////////////////////////////////////////////////////////////////////////////////////////////////
// OdinManager a class managing the overall process for saving and resotring the disk images
/////////////////////////////////////////////////////////////////////////////////////////////////////////////////

#include "stdafx.h"
#include "OdinThread.h"
#include "WriteThread.h"
#include "ReadThread.h"
#include "CompressionThread.h"
#include "DecompressionThread.h"
#include "BufferQueue.h"
#include "ImageStream.h"
#include "OdinManager.h"
#include "DriveList.h"
#include "InternalException.h"
#include "SplitManager.h"
#include "VSSWrapper.h"

#ifdef DEBUG
  #define new DEBUG_NEW
  #define malloc DEBUG_MALLOC
#endif // _DEBUG

using namespace std;

static const int kDoCopyBufferCount = 8;

// section name in .ini file for configuration values
IMPL_SECTION(COdinManager, L"Options")

COdinManager::COdinManager()
  :fCompressionMode(L"CompressionMode", noCompression),
   fSaveAllBlocks(L"SaveAllBlocks", false),
   fSplitFiles(L"SplitFiles", false),
   fSplitFileSize(L"SplitFileSize", 0),
   fReadBlockSize(L"ReadWriteBlockSize", 1048576), // 1MB
   fTakeVSSSnapshot(L"TakeVSSSnaphot", false)
{
  fVerifyCrc32 = 0;
  fWasCancelled = false;
  Init();
}

COdinManager::~COdinManager()
{
  Reset();
}

void COdinManager::Init()
{
  fReadThread.reset();
  fWriteThread.reset();
  fCompDecompThread.reset();
  fSourceImage.reset();
  fTargetImage.reset();
  fEmptyReaderQueue.reset();
  fFilledReaderQueue.reset();
  fEmptyCompDecompQueue.reset();
  fFilledCompDecompQueue.reset();
  fSplitCallback.reset();
  fIsSaving = false;
  fIsRestoring = false;
  fVSS.reset();
  fMultiVolumeMode = false;
  fMultiVolumeIndex = 0;
  if (!CVssWrapper::VSSIsSupported()) {
    fTakeVSSSnapshot = false;
  }
}
  
void COdinManager::RefreshDriveList()
{
  fDriveList = std::make_unique<CDriveList>(false);
}

  // Terminates all worker threads, bCancelled indicates if this is 
  // becuse a running operation was aborted.
void COdinManager::Terminate()
{
  if (fReadThread) {
    fReadThread->Terminate();
  }
  if (fWriteThread) {
    fWriteThread->Terminate();
  }
  if (fCompDecompThread) {
    fCompDecompThread->Terminate();
  }
  if (fVSS && !fMultiVolumeMode) {
    fVSS->ReleaseSnapshot(fWasCancelled);
    fVSS.reset();
  }
  Reset();
}

void COdinManager::Reset()
{
  fReadThread.reset();
  fWriteThread.reset();
  fCompDecompThread.reset();
  fSourceImage.reset();
  fTargetImage.reset();
  fEmptyReaderQueue.reset();
  fFilledReaderQueue.reset();
  fEmptyCompDecompQueue.reset();
  fFilledCompDecompQueue.reset();
  fSplitCallback.reset();
  if (fMultiVolumeMode) {
    fIsSaving = false;
    fIsRestoring = false;
  } else {
    fVSS.reset();
    Init();
  }
}

CDriveInfo* COdinManager::GetDriveInfo(int index) {
  return fDriveList->GetItem(index);
}

unsigned COdinManager::GetDriveCount() {
    return (unsigned) fDriveList->GetCount();
  }

void COdinManager::SavePartition(int driveIndex, LPCWSTR fileName, ISplitManagerCallback* cb, IWaitCallback* wcb)
{
  DoCopy(isBackup, fileName, driveIndex, 0, 0, cb, wcb);
}

void COdinManager::RestorePartition(LPCWSTR fileName, int driveIndex, unsigned noFiles, unsigned __int64 totalSize, ISplitManagerCallback* cb, IWaitCallback* wcb)
{
  DoCopy(isRestore, fileName, driveIndex, noFiles, totalSize, cb, wcb);
}

void COdinManager::VerifyPartition(LPCWSTR fileName, int driveIndex, unsigned noFiles, unsigned __int64 totalSize, ISplitManagerCallback* cb, IWaitCallback* wcb)
{
  DoCopy(isVerify, fileName, -1, noFiles, totalSize, cb, wcb);
}


void COdinManager::MakeSnapshot(int driveIndex, IWaitCallback* wcb) {
  if (!fMultiVolumeMode)
    return; // ignore 

  if (fTakeVSSSnapshot) {
    wcb->OnPrepareSnapshotBegin();
    CDriveInfo* pDriveInfo = driveIndex < 0 ? NULL : fDriveList->GetItem(driveIndex);
    int subPartitions = pDriveInfo ? pDriveInfo->GetContainedVolumes() : 0;
    bool isHardDisk = pDriveInfo ? pDriveInfo->IsCompleteHardDisk() : false;
    std::vector<LPCWSTR> mountPoints;

    if (isHardDisk) {
      std::vector<CDriveInfo*> containedVolumes(subPartitions);
      mountPoints.resize(subPartitions);
      int res = fDriveList->GetVolumes(pDriveInfo, containedVolumes.data(), subPartitions);
      for (int i = 0; i < res; i++) {
        ATLTRACE(L"Found sub-partition: %s\n", containedVolumes[i]->GetDisplayName().c_str());
        mountPoints[i] = containedVolumes[i]->GetMountPoint().length() > 0
          ? containedVolumes[i]->GetMountPoint().c_str()
          : NULL;
      }
    } else {
      subPartitions = 1;
      mountPoints.resize(1);
      mountPoints[0] = (pDriveInfo && pDriveInfo->GetMountPoint().length() > 0)
        ? pDriveInfo->GetMountPoint().c_str()
        : NULL;
    }

    fVSS = std::make_unique<CVssWrapper>();
    fVSS->PrepareSnapshot(mountPoints.data(), subPartitions);
    wcb->OnPrepareSnapshotReady();
  }
}

void COdinManager::ReleaseSnapshot(bool bCancelled) {
  if (!fMultiVolumeMode)
    return; // ignore 

  fVSS->ReleaseSnapshot(bCancelled);

}

void COdinManager::GetDriveNameList(std::list<std::wstring>& driveNames)
{
 	for (int i=0; i<(int)fDriveList->GetCount(); i++)
		driveNames.push_back(fDriveList->GetItem(i)->GetDisplayName());
}

void COdinManager::DoCopy(TOdinOperation operation, LPCWSTR fileName, int driveIndex, unsigned noFiles,
                          unsigned __int64 totalSize, ISplitManagerCallback* cb,  IWaitCallback* wcb)
{
  int nBufferCount = kDoCopyBufferCount;
  TCompressionFormat decompressionFormat = noCompression;
  bool bSaveAllBlocks = fSaveAllBlocks;
  fVerifyCrc32 = 0;
  CDriveInfo*	pDriveInfo = driveIndex<0 ? NULL : fDriveList->GetItem(driveIndex);
  bool isHardDisk = pDriveInfo ? pDriveInfo->IsCompleteHardDisk() : false;
  LPCWSTR mountPoint = NULL;
  LPCWSTR deviceName = pDriveInfo ? pDriveInfo->GetDeviceName().c_str() : NULL;
  unsigned bytesPerCluster = pDriveInfo ? pDriveInfo->GetClusterSize() : 0;

  if (isHardDisk && !fSaveAllBlocks) {
    ATLASSERT(fMultiVolumeMode);
  } else {
    mountPoint = pDriveInfo ? pDriveInfo->GetMountPoint().c_str() : NULL;
  }

  bool verifyOnly = operation == isVerify;

  // Create the output image store
  if (operation == isBackup) {
      // setup target file
      fTargetImage = std::make_unique<CFileImageStream>();
      if (fSplitFileSize > 0) {
        fTargetImage->Open(NULL, IImageStream::forWriting);
        fSplitCallback = std::make_unique<CSplitManager>(fileName, fSplitFileSize, static_cast<CFileImageStream*>(fTargetImage.get()), cb);
        static_cast<CFileImageStream*>(fTargetImage.get())->RegisterCallback(fSplitCallback.get());
      } else {
        fTargetImage->Open(fileName, IImageStream::forWriting);
      }
      // setup source device
      const wchar_t* vssVolume;
      if (fTakeVSSSnapshot && !verifyOnly && !pDriveInfo->GetMountPoint().empty()) {
        if (!fMultiVolumeMode) {
          wcb->OnPrepareSnapshotBegin();
          fVSS = std::make_unique<CVssWrapper>();
          LPCWSTR* mountPointPtr = &mountPoint;
          fVSS->PrepareSnapshot(mountPointPtr, 1);
          wcb->OnPrepareSnapshotReady();
          fMultiVolumeIndex = 0;
        }
        vssVolume = fVSS->GetSnapshotDeviceName(fMultiVolumeIndex);
          if (vssVolume != NULL && *vssVolume != L'\0') // can be 0 if not mounted
            deviceName  = vssVolume;
      }
      fSourceImage = std::make_unique<CDiskImageStream>();
      fSourceImage->Open(deviceName, IImageStream::forReading);
  } else if (operation == isRestore || operation == isVerify) {
      if (operation == isRestore) {
        // setup target
        fTargetImage = std::make_unique<CDiskImageStream>();
        int subPartitions = pDriveInfo ? pDriveInfo->GetContainedVolumes() : 0;
        static_cast<CDiskImageStream*>(fTargetImage.get())->SetContainedSubPartitionsCount(subPartitions);
        fTargetImage->Open(deviceName, IImageStream::forWriting);
      }
      fSourceImage = std::make_unique<CFileImageStream>();
      if (noFiles > 0) {
         fSourceImage->Open(NULL, IImageStream::forReading);
         fSplitCallback = std::make_unique<CSplitManager>(fileName, static_cast<CFileImageStream*>(fSourceImage.get()), totalSize, cb);
         static_cast<CFileImageStream*>(fSourceImage.get())->RegisterCallback(fSplitCallback.get());
      } else {
         fSourceImage->Open(fileName, IImageStream::forReading);
      }
  } else {
      THROW_INT_EXC(EInternalException::inputTypeNotSet);
  }  
  fWasCancelled = false;
  // Determine the block sizes we'll be using
  fEmptyReaderQueue = std::make_unique<CImageBuffer>(fReadBlockSize, nBufferCount, L"fEmptyReaderQueue");
  fFilledReaderQueue = std::make_unique<CImageBuffer>(L"fFilledReaderQueue");

  CImageBuffer *writerInQueue = nullptr;
  CImageBuffer *writerOutQueue = nullptr;
  if (operation == isRestore || operation == isVerify) {
    CFileImageStream *fileStream = static_cast<CFileImageStream*>(fSourceImage.get());
    fileStream->ReadImageFileHeader(true);
    decompressionFormat = fileStream->GetImageFileHeader().GetCompressionFormat();
  }

  if (fCompressionMode != noCompression || decompressionFormat != noCompression) {
    fEmptyCompDecompQueue = std::make_unique<CImageBuffer>(fReadBlockSize, nBufferCount, L"fEmptyCompDecompQueue");
    fFilledCompDecompQueue = std::make_unique<CImageBuffer>(L"fFilledCompDecompQueue");
    writerInQueue = fFilledCompDecompQueue.get();
    writerOutQueue = fEmptyCompDecompQueue.get();
  } else {
    writerInQueue = fFilledReaderQueue.get();
    writerOutQueue = fEmptyReaderQueue.get();
  }

  unsigned __int64 volumeBitmapOffset, volumeBitmapLength;
  fReadThread = std::make_unique<CReadThread>(fSourceImage.get(), fEmptyReaderQueue.get(), fFilledReaderQueue.get(), verifyOnly);
  fWriteThread = std::make_unique<CWriteThread>(fTargetImage.get(), writerInQueue, writerOutQueue, verifyOnly);
  if (operation == isRestore || operation == isVerify) {
      CFileImageStream *fileStream = static_cast<CFileImageStream*>(fSourceImage.get());
      unsigned __int64 dataOffset;
      LPCWSTR comment = fileStream->GetComment();
      ATLTRACE("Restoring disk image with comment: %S\n", comment ? comment : L"None");
      ATLTRACE("Restoring disk image with CRC32: %x\n", fileStream->GetCrc32Checksum());

      if (!verifyOnly) {
        static_cast<CDiskImageStream*>(fTargetImage.get())->SetBytesPerCluster(bytesPerCluster);
        fileStream->GetImageFileHeader().GetClusterBitmapOffsetAndLength(volumeBitmapOffset, volumeBitmapLength);
        fWriteThread->SetAllocationMapReaderInfo(fSourceImage->GetRunLengthStreamReader(), fileStream->GetImageFileHeader().GetClusterSize());
      }
      dataOffset = fileStream->GetImageFileHeader().GetVolumeDataOffset();
      fReadThread->SetVolumeDataOffset(dataOffset);
      if (decompressionFormat != noCompression) {
        fCompDecompThread = std::make_unique<CDecompressionThread>(decompressionFormat, fFilledReaderQueue.get(),
                              fEmptyReaderQueue.get(), fEmptyCompDecompQueue.get(), writerInQueue);
      } 
      fIsRestoring = true;
  } else if (operation == isBackup) {
      CFileImageStream *fileStream = static_cast<CFileImageStream*>(fTargetImage.get());
      fileStream->SetComment(fComment.c_str());
      fileStream->SetCompressionFormat(GetCompressionMode());
      fileStream->SetVolumeFormat(isHardDisk ? CImageFileHeader::volumeHardDisk : CImageFileHeader::volumePartition);
      if (bytesPerCluster == 0) {
        // drive info could not detect cluster size so we just take the one from GetDiskFreeEx()
        // happens if we do not get a mount name like d:
        bytesPerCluster = static_cast<CDiskImageStream*>(fSourceImage.get())->GetBytesPerClusterFromBootSector();
      }

      static_cast<CDiskImageStream*>(fSourceImage.get())->SetBytesPerCluster(bytesPerCluster);
      
      if (bSaveAllBlocks || isHardDisk || !static_cast<CDiskImageStream*>(fSourceImage.get())->IsMounted() || bytesPerCluster == 0) 
        bSaveAllBlocks = true;
      if (!bSaveAllBlocks) {
        fileStream->WriteImageFileHeaderAndAllocationMap(static_cast<CDiskImageStream*>(fSourceImage.get()));
        fReadThread->SetAllocationMapReaderInfo(fSourceImage->GetRunLengthStreamReader(), fileStream->GetImageFileHeader().GetClusterSize());
        if ( fSplitFileSize > 0 && fileStream->GetPosition() > fSplitFileSize)
          THROW_INT_EXC(EInternalException::chunkSizeTooSmall); 
      } else {
        fileStream->WriteImageFileHeaderForSaveAllBlocks(fSourceImage->GetSize(), 
          static_cast<CDiskImageStream*>(fSourceImage.get())->GetBytesPerCluster());
      }
      if (fCompressionMode != noCompression) {
        fCompDecompThread = std::make_unique<CCompressionThread>(GetCompressionMode(), fFilledReaderQueue.get(),
                                  fEmptyReaderQueue.get(), fEmptyCompDecompQueue.get(), writerInQueue);
      }  
      fIsSaving = true;
  }

  if (fReadThread)
    fReadThread->Resume();
  if (fWriteThread)
    fWriteThread->Resume();
  if (fCompDecompThread)
    fCompDecompThread->Resume();
}

void COdinManager::CancelOperation()
{
  fWasCancelled = true;
  if (fReadThread) {
    fReadThread->CancelThread();
  }
  if (fWriteThread) {
    fWriteThread->CancelThread();
  }
  if (fCompDecompThread) {
    fCompDecompThread->CancelThread();
  }
}

void COdinManager::WaitToCompleteOperation(IWaitCallback* callback) 
{
    // wait until threads are completed without blocking the user interface
  unsigned threadCount = GetThreadCount();
  
  // Use smart pointer to prevent memory leak in exception paths
  std::unique_ptr<HANDLE[]> threadHandleArray(new HANDLE[threadCount]);
  
  bool ok = GetThreadHandles(threadHandleArray.get(), threadCount);
  if (!ok)
    return;
  ATLTRACE("WaitToCompleteOperation() entered.\n");

  while (TRUE) {
    DWORD result = MsgWaitForMultipleObjects(threadCount, threadHandleArray.get(), FALSE, INFINITE, QS_ALLEVENTS);
    if (result >= WAIT_OBJECT_0 && result < (DWORD)threadCount) {
      ATLTRACE("event arrived: %d, thread id: %x\n", result, threadHandleArray[result]);
      callback->OnThreadTerminated();
      if (--threadCount == 0)  {       
        ATLTRACE(" All worker threads are terminated now\n");
        if (fReadThread) 
          fVerifyCrc32 = fReadThread->GetCrc32();
        callback->OnFinished();
        Terminate(); // work is finished
        break;
      }
      // setup new array with the remaining threads:
      for (unsigned i=result; i<threadCount; i++)
        threadHandleArray[i] = threadHandleArray[i+1];
    }
    else if (result  == WAIT_OBJECT_0 + threadCount)
    {
      // process windows messages
      MSG msg ;
      while(::PeekMessage(&msg, NULL, 0, 0, PM_REMOVE))
        ::DispatchMessage(&msg) ;
    }
    else if ( WAIT_FAILED) {
      ATLTRACE("MsgWaitForMultipleObjects failed, last error is: %d\n", GetLastError());
      callback->OnAbort();
      break;
    }
    else
      ATLTRACE("unusual return code from MsgWaitForMultipleObjects: %d\n", result);
  }
  // Smart pointer automatically cleans up - no manual delete needed
  ATLTRACE("WaitToCompleteOperation() exited.\n");
}

unsigned COdinManager::GetThreadCount()
{
  int count;

  if (fReadThread && fWriteThread)
    count = 2;
  else
    count = 0;
  if (fCompDecompThread)
    ++count;
  return count;
}

bool COdinManager::GetThreadHandles(HANDLE* handles, unsigned size)
{
  int i=0;

  if (size<GetThreadCount())
    return false;
  memset(handles, 0, size*sizeof(HANDLE));
  
  if (fReadThread)
    handles[i++] = fReadThread->GetHandle();

  if (fWriteThread)
    handles[i++] = fWriteThread->GetHandle();

  if (fCompDecompThread)
    handles[i++] = fCompDecompThread->GetHandle();
  
  return true;
}

LPCWSTR COdinManager::GetErrorMessage()
{
  LPCWSTR msg = NULL;

  if (fReadThread && fReadThread->GetErrorFlag())
    msg = fReadThread->GetErrorMessage();

  if (msg==NULL && fWriteThread && fWriteThread->GetErrorFlag())
    msg = fWriteThread->GetErrorMessage();

  if (msg==NULL && fCompDecompThread && fCompDecompThread->GetErrorFlag())
    msg = fCompDecompThread->GetErrorMessage();
  
  return msg;
}

bool COdinManager::WasError()
{
  return (fReadThread && fReadThread->GetErrorFlag()) ||
         (fWriteThread && fWriteThread->GetErrorFlag()) ||
         (fCompDecompThread && fCompDecompThread->GetErrorFlag());
}

unsigned __int64 COdinManager::GetTotalBytesToProcess()
{
  // TODO: 
  // The total number of bytes to process is used as reference for the progress bar.
  // For backup use:
  // allocated bytes of source image if used block are saved
  // size of source partition if all blocks are saved
  // For restore use:
  // used blocks of image file header if image file contains only used blocks
  // all bytes of destination partition if image file contains all blocks
  // Note that image file can be compressed heavily so that sizes of image file may lead to inaccurate results
  
  unsigned __int64 res;

  if (fIsSaving || (fIsRestoring && !fTargetImage) ) {
  //               ^ verify mode! (rhs of or condition)                   
    if (fSourceImage) {
      if (fSaveAllBlocks)
        res = fSourceImage->GetSize();
      else {
        res = fSourceImage->GetAllocatedBytes();
        if (res == 0) // happens for raw disks
          res = fSourceImage->GetSize();
      }
    } else {
      res = 0;
    }
  } else if (fIsRestoring) {
    if (fTargetImage) {
      if (fSaveAllBlocks)
        res = fTargetImage->GetSize();
      else {
        res = fSourceImage->GetAllocatedBytes();
        if (res == 0) // happens for raw disks
          res = fSourceImage->GetSize();
      }
    } else {
      // we are in verify mode
      res = 0;
    }
  } else {
    res =0;
  }
  return res;
}

unsigned __int64 COdinManager::GetBytesProcessed()
{
  // TODO: This value is used to calculate progress bar. Use always the number of processed bytes
  // from the write thread, because if image is restored and compressed heavily the number of the 
  // image file may be very inaccurate.

  if (fIsRestoring && fWriteThread)
    return fWriteThread->GetBytesProcessed();
  else if (fIsSaving && fReadThread)
    return fReadThread->GetBytesProcessed();
  else
    return 0;
}

bool COdinManager::IsFileReadable(LPCWSTR fileName)
{
  HANDLE h = CreateFile(fileName, GENERIC_READ, FILE_SHARE_READ|FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL); 
  if (h != INVALID_HANDLE_VALUE)
  {
	  CloseHandle(h);
  }
 
  return h != INVALID_HANDLE_VALUE;
}
