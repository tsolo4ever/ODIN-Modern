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
 
#include "stdafx.h"
#include <string>
#include "BufferQueue.h"
#include "DecompressionThread.h"
#include "../zlib-1.3.2/zlib.h"
#include "../bzip2-1.0.5/bzlib.h"
#include "lz4frame.h"
#include "zstd.h"
#include "CompressionException.h"
#include "InternalException.h"

#ifdef DEBUG
  #define new DEBUG_NEW
  #define malloc DEBUG_MALLOC
#endif // _DEBUG

using namespace std;
//---------------------------------------------------------------------------


//---------------------------------------------------------------------------
// Constructor
//
 CDecompressionThread::CDecompressionThread(TCompressionFormat compressionFormat, 
      CImageBuffer *sourceQueueCompressed, 
      CImageBuffer *targetQueueCompressed,
      CImageBuffer *sourceQueueDecompressed, 
      CImageBuffer *targetQueueDecompressed
      ) : COdinThread(CREATE_SUSPENDED)
{
  fSourceQueueCompressed = sourceQueueCompressed;
  fTargetQueueCompressed = targetQueueCompressed;
  fSourceQueueDecompressed = sourceQueueDecompressed;
  fTargetQueueDecompressed = targetQueueDecompressed;
  this->fCompressionFormat = compressionFormat;
}
//---------------------------------------------------------------------------


DWORD  CDecompressionThread::Execute()
{
  ATLTRACE("CDecompressionThread created,  thread: %d, name: Decompression-Thread\n", GetCurrentThreadId());
  SetName("DecompressionThread");
  try {
    switch (fCompressionFormat) {
      case compressionGZip:
        DecompressLoopZlib();
        break;
      case compressionBZIP2:
        DecompressLoopLibz2();
        break;
      case compressionLZ4:
      case compressionLZ4HC:
        DecompressLoopLZ4();
        break;
      case compressionZSTD:
        DecompressLoopZSTD();
        break;
      default:
        break;
    }

    fFinished = true;
  } catch (Exception &e) {
    fErrorFlag = true;
    fErrorMessage = L"Decompression thread encountered exception: \"";
  	fErrorMessage += e.GetMessage();
	  fErrorMessage += L"\"";
    fFinished = true;
	  return E_FAIL;
  } catch (std::exception &e) {
    fErrorFlag = true;
    fErrorMessage = L"Decompression thread encountered standard exception: \"";
    fErrorMessage += CA2W(e.what());
    fErrorMessage += L"\"";
    fFinished = true;
    return E_FAIL;
  } catch (...) {
    fErrorFlag = true;
    fErrorMessage = L"Decompression thread encountered unknown exception";
    fFinished = true;
    return E_FAIL;
  }
  return 0;
}

//---------------------------------------------------------------------------

void CDecompressionThread::DecompressLoopZlib()
{
  bool bEOF = false;
  z_stream  zsStream;
  int ret = Z_OK;
  CBufferChunk *readChunk = NULL;
  CBufferChunk *decompressChunk = NULL;

  memset(&zsStream, 0, sizeof(zsStream));
  ret = inflateInit(&zsStream);
  if (ret != Z_OK)
     THROWEX(EZLibCompressionException, ret);

  while (ret != Z_STREAM_END) {
    if (zsStream.avail_in == 0) {
      if (readChunk)
        fTargetQueueCompressed->ReleaseChunk(readChunk);
      readChunk = fSourceQueueCompressed->GetChunk(); // may block
      if (!readChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bEOF = readChunk->IsEOF();
      zsStream.next_in = (BYTE*)readChunk->GetData();
      zsStream.avail_in = readChunk->GetSize();
    }

    if (zsStream.avail_out == 0) {
      if (decompressChunk) {
        decompressChunk->SetSize(decompressChunk->GetMaxSize());// (zsStream.total_out);
        fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
      }
      decompressChunk = fSourceQueueDecompressed->GetChunk();
      if (!decompressChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      zsStream.next_out = (BYTE*)decompressChunk->GetData();
      zsStream.avail_out = decompressChunk->GetMaxSize();
    }

    ret = inflate(&zsStream, Z_NO_FLUSH); 
    if (ret < 0) {
      ATLTRACE("Error in gzip compressing data, error code: %d\n", ret);
      THROWEX(EZLibCompressionException, ret);
    }

    if (fCancel) {
      if (decompressChunk)
        fTargetQueueCompressed->ReleaseChunk(decompressChunk);
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      inflateEnd(&zsStream);
      Terminate(-1);  // terminate thread after releasing buffer and before acquiring next one
    }
  } 
  
  ret = inflateEnd(&zsStream);
  if (ret < 0) {
    ATLTRACE("Error in termination of gzip compression, error code: %d\n", ret);
    THROWEX(EZLibCompressionException, ret);
  }

  if (readChunk != NULL)
    fTargetQueueCompressed->ReleaseChunk(readChunk);
  if (decompressChunk != NULL) {
    decompressChunk->SetSize (decompressChunk->GetMaxSize() - zsStream.avail_out); // zsStream.total_out);
    decompressChunk->SetEOF(true);
    fTargetQueueDecompressed->ReleaseChunk(decompressChunk); 
  }
}

void CDecompressionThread::DecompressLoopLibz2()
{
  bool bEOF = false;
  bz_stream  bzStream;
  int ret = Z_OK;
  CBufferChunk *readChunk = NULL;
  CBufferChunk *decompressChunk = NULL;

  memset(&bzStream, 0, sizeof(bzStream));
  ret = BZ2_bzDecompressInit(&bzStream, 0, 0);
  if (ret != BZ_OK)
    THROWEX(EBZip2CompressionException, ret);

  while (ret != BZ_STREAM_END ) {
    if (bzStream.avail_in == 0) {
      if (readChunk)
        fTargetQueueCompressed->ReleaseChunk(readChunk);
      readChunk = fSourceQueueCompressed->GetChunk(); // may block
      if (!readChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bEOF = readChunk->IsEOF();
      bzStream.next_in = (char*)readChunk->GetData();
      bzStream.avail_in = readChunk->GetSize();
    }

    if (bzStream.avail_out == 0) {
      if (decompressChunk) {
        decompressChunk->SetSize(decompressChunk->GetMaxSize());// (bzStream.total_out);
        fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
      }
      decompressChunk = fSourceQueueDecompressed->GetChunk();
      if (!decompressChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bzStream.next_out = (char*)decompressChunk->GetData();
      bzStream.avail_out = decompressChunk->GetMaxSize();
    }

   ret = BZ2_bzDecompress(&bzStream); 
   if (ret < 0)
     THROWEX(EBZip2CompressionException, ret);

    if (fCancel) {
      BZ2_bzDecompressEnd(&bzStream);
      if (decompressChunk)
        fTargetQueueCompressed->ReleaseChunk(decompressChunk);
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      Terminate(-1);  // terminate thread after releasing buffer and before acquiring next one
    }
  } 
  
  ret = BZ2_bzDecompressEnd(&bzStream);
  if (ret < 0) {
    ATLTRACE("Error in termination of bzip2 decompression, error code: %d\n", ret);
    THROWEX(EBZip2CompressionException, ret);
  }

  if (readChunk != NULL)
    fTargetQueueCompressed->ReleaseChunk(readChunk);
  if (decompressChunk != NULL) {
    decompressChunk->SetSize (decompressChunk->GetMaxSize() - bzStream.avail_out); // bzStream.total_out);
    decompressChunk->SetEOF(true);
    fTargetQueueDecompressed->ReleaseChunk(decompressChunk); 
  }
}

//---------------------------------------------------------------------------
// LZ4 Frame decompression loop.
// Works for both compressionLZ4 and compressionLZ4HC — the frame format
// is identical; only the compression level differs and the decompressor
// does not need to know which was used.
//
void CDecompressionThread::DecompressLoopLZ4()
{
  LZ4F_dctx* ctx = NULL;
  CBufferChunk *readChunk      = NULL;
  CBufferChunk *decompressChunk = NULL;

  LZ4F_errorCode_t err = LZ4F_createDecompressionContext(&ctx, LZ4F_VERSION);
  if (LZ4F_isError(err))
    THROW_INT_EXC(EInternalException::lz4CompressError);

  // Acquire first output chunk.
  decompressChunk = fSourceQueueDecompressed->GetChunk();
  if (!decompressChunk) {
    LZ4F_freeDecompressionContext(ctx);
    THROW_INT_EXC(EInternalException::getChunkError);
  }

  const BYTE* srcPtr      = NULL;
  size_t      srcRemaining = 0;
  size_t      dstUsed     = 0;
  size_t      ret         = 1; // non-zero: frame not yet complete

  // LZ4F_decompress returns 0 when the entire frame has been decoded.
  while (ret != 0) {
    // Refill compressed input when the current chunk is exhausted.
    if (srcRemaining == 0) {
      if (readChunk)
        fTargetQueueCompressed->ReleaseChunk(readChunk);
      readChunk = fSourceQueueCompressed->GetChunk(); // may block
      if (!readChunk) {
        LZ4F_freeDecompressionContext(ctx);
        THROW_INT_EXC(EInternalException::getChunkError);
      }
      srcPtr      = (const BYTE*)readChunk->GetData();
      srcRemaining = readChunk->GetSize();
    }

    // Get a fresh output chunk if the current one is full.
    size_t dstAvail = decompressChunk->GetMaxSize() - dstUsed;
    if (dstAvail == 0) {
      decompressChunk->SetSize(decompressChunk->GetMaxSize());
      fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
      decompressChunk = fSourceQueueDecompressed->GetChunk();
      if (!decompressChunk) {
        LZ4F_freeDecompressionContext(ctx);
        THROW_INT_EXC(EInternalException::getChunkError);
      }
      dstUsed  = 0;
      dstAvail = decompressChunk->GetMaxSize();
    }

    size_t srcConsumed = srcRemaining;
    size_t dstWritten  = dstAvail;
    ret = LZ4F_decompress(ctx,
                          (BYTE*)decompressChunk->GetData() + dstUsed, &dstWritten,
                          srcPtr, &srcConsumed,
                          NULL);
    if (LZ4F_isError(ret)) {
      LZ4F_freeDecompressionContext(ctx);
      THROW_INT_EXC(EInternalException::lz4CompressError);
    }
    srcPtr       += srcConsumed;
    srcRemaining -= srcConsumed;
    dstUsed      += dstWritten;

    if (fCancel) {
      LZ4F_freeDecompressionContext(ctx);
      if (decompressChunk) fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
      if (readChunk)       fTargetQueueCompressed->ReleaseChunk(readChunk);
      Terminate(-1);
    }
  }

  LZ4F_freeDecompressionContext(ctx);

  if (readChunk != NULL)
    fTargetQueueCompressed->ReleaseChunk(readChunk);
  if (decompressChunk != NULL) {
    decompressChunk->SetSize(dstUsed);
    decompressChunk->SetEOF(true);
    fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
  }
}

//---------------------------------------------------------------------------
// Zstandard streaming decompression loop.
//
void CDecompressionThread::DecompressLoopZSTD()
{
  ZSTD_DStream* stream = ZSTD_createDStream();
  if (!stream)
    THROW_INT_EXC(EInternalException::zstdCompressError);

  size_t initRet = ZSTD_initDStream(stream);
  if (ZSTD_isError(initRet)) {
    ZSTD_freeDStream(stream);
    THROW_INT_EXC(EInternalException::zstdCompressError);
  }

  CBufferChunk *readChunk      = NULL;
  CBufferChunk *decompressChunk = NULL;
  bool bEOF          = false;
  bool frameComplete = false;

  decompressChunk = fSourceQueueDecompressed->GetChunk();
  if (!decompressChunk) {
    ZSTD_freeDStream(stream);
    THROW_INT_EXC(EInternalException::getChunkError);
  }
  ZSTD_outBuffer outBuf;
  outBuf.dst  = decompressChunk->GetData();
  outBuf.size = decompressChunk->GetMaxSize();
  outBuf.pos  = 0;

  // Outer loop: consume one compressed chunk per iteration.
  while (!frameComplete) {
    if (readChunk)
      fTargetQueueCompressed->ReleaseChunk(readChunk);
    readChunk = fSourceQueueCompressed->GetChunk(); // may block
    if (!readChunk) {
      ZSTD_freeDStream(stream);
      THROW_INT_EXC(EInternalException::getChunkError);
    }
    bEOF = readChunk->IsEOF();

    ZSTD_inBuffer inBuf;
    inBuf.src  = readChunk->GetData();
    inBuf.size = readChunk->GetSize();
    inBuf.pos  = 0;

    // Inner loop: decompress all of inBuf or until frame ends.
    while (inBuf.pos < inBuf.size) {
      size_t ret = ZSTD_decompressStream(stream, &outBuf, &inBuf);
      if (ZSTD_isError(ret)) {
        ZSTD_freeDStream(stream);
        THROW_INT_EXC(EInternalException::zstdCompressError);
      }
      if (ret == 0) {
        // Frame is fully decoded.
        frameComplete = true;
        break;
      }
      // If the output buffer is full, flush it and get a new chunk.
      if (outBuf.pos == outBuf.size) {
        decompressChunk->SetSize(decompressChunk->GetMaxSize());
        fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
        decompressChunk = fSourceQueueDecompressed->GetChunk();
        if (!decompressChunk) {
          ZSTD_freeDStream(stream);
          THROW_INT_EXC(EInternalException::getChunkError);
        }
        outBuf.dst  = decompressChunk->GetData();
        outBuf.size = decompressChunk->GetMaxSize();
        outBuf.pos  = 0;
      }

      if (fCancel) {
        ZSTD_freeDStream(stream);
        if (decompressChunk) fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
        if (readChunk)       fTargetQueueCompressed->ReleaseChunk(readChunk);
        Terminate(-1);
      }
    }

    // If we processed the EOF-marked chunk and the frame is still not
    // signalled complete, stop anyway to avoid blocking on an empty queue.
    if (bEOF && !frameComplete)
      break;
  }

  ZSTD_freeDStream(stream);

  if (readChunk != NULL)
    fTargetQueueCompressed->ReleaseChunk(readChunk);
  if (decompressChunk != NULL) {
    decompressChunk->SetSize(outBuf.pos);
    decompressChunk->SetEOF(true);
    fTargetQueueDecompressed->ReleaseChunk(decompressChunk);
  }
}


