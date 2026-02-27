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
#include "../zlib-1.3.2/zlib.h"
#include "../bzip2-1.0.5/bzlib.h"
#include "lz4frame.h"
#include "zstd.h"
#include "Compression.h"
#include <string>
#include "BufferQueue.h"
#include "CompressionThread.h"
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
 CCompressionThread::CCompressionThread(TCompressionFormat compressionFormat, 
   CImageBuffer *sourceQueueDecompressed, 
   CImageBuffer *targetQueueDecompressed,
   CImageBuffer *sourceQueueCompressed, 
   CImageBuffer *targetQueueCompressed
   ) : COdinThread(CREATE_SUSPENDED)      
{
  wstring ResourceName;

  this->fCompressionFormat = compressionFormat;
  fSourceQueueDecompressed = sourceQueueDecompressed;
  fTargetQueueDecompressed = targetQueueDecompressed;
  fSourceQueueCompressed = sourceQueueCompressed;
  fTargetQueueCompressed = targetQueueCompressed;
  fCompressionLevel = 6;
}
//---------------------------------------------------------------------------

//---------------------------------------------------------------------------
// The thread's main execution loop - keep this as simple as possible
//
DWORD CCompressionThread::Execute()
{
  SetName("CompressionThread");

  try {
    switch (fCompressionFormat) {
      case compressionGZip:
        CompressLoopZlib();
        break;
      case compressionBZIP2:
        CommpressLoopLibz2();
        break;
      case compressionLZ4:
        CompressLoopLZ4(false);
        break;
      case compressionLZ4HC:
        CompressLoopLZ4(true);
        break;
      case compressionZSTD:
        CompressLoopZSTD();
        break;
      default:
        break;
    }

    fFinished = true;
  } catch (Exception &e) {
    fErrorFlag = true;
    fErrorMessage = L"Compression thread encountered exception: \"";
  	fErrorMessage += e.GetMessage();
	  fErrorMessage += L"\"";
    fFinished = true;
	  return E_FAIL;
  } catch (std::exception &e) {
    fErrorFlag = true;
    fErrorMessage = L"Compression thread encountered standard exception: \"";
    fErrorMessage += CA2W(e.what());
    fErrorMessage += L"\"";
    fFinished = true;
    return E_FAIL;
  } catch (...) {
    fErrorFlag = true;
    fErrorMessage = L"Compression thread encountered unknown exception";
    fFinished = true;
    return E_FAIL;
  }
  return 0;
}  

//---------------------------------------------------------------------------

void  CCompressionThread::CompressLoopZlib()
{
  bool bEOF = false;
  z_stream  zStream;
  int flush, ret = Z_OK;
  CBufferChunk *readChunk = NULL;
  CBufferChunk *compressChunk = NULL;

  memset(&zStream, 0, sizeof(zStream));
  ret = deflateInit(&zStream, Z_DEFAULT_COMPRESSION);
  if (ret != BZ_OK) {
    THROWEX(EZLibCompressionException, ret);
  }

  while (ret != Z_STREAM_END) {
    if (zStream.avail_in == 0 && !bEOF) {
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      readChunk = fSourceQueueDecompressed->GetChunk(); // may block
      if (!readChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bEOF = readChunk->IsEOF();
      zStream.next_in = (BYTE*)readChunk->GetData();
      zStream.avail_in = readChunk->GetSize();
    }

    if (zStream.avail_out == 0) {
      if (compressChunk) {
        compressChunk->SetSize(compressChunk->GetMaxSize());
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
      }
      compressChunk = fSourceQueueCompressed->GetChunk();
      if (!compressChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      zStream.next_out = (BYTE*)compressChunk->GetData();
      zStream.avail_out = compressChunk->GetMaxSize();
    }

    flush = bEOF ? Z_FINISH : Z_NO_FLUSH;
    if (zStream.next_in > 0) {
      ret = deflate(&zStream, flush); 
      if (ret < 0 ) {
        ATLTRACE("Error in gzip compressing data, error code: %d\n", ret);
        THROWEX(EZLibCompressionException, ret);
      }
    }
    if (fCancel) {
      if (compressChunk)
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      deflateEnd(&zStream);
      Terminate(-1);  // terminate thread after releasing buffer and before acquiring next one
    }
  } 
  
  ret = deflateEnd(&zStream);
  if (ret < 0) {
    ATLTRACE("Error in termination of gzip compression, error code: %d\n", ret);
    THROWEX(EZLibCompressionException, ret);
  }

  if (readChunk != NULL)
    fTargetQueueDecompressed->ReleaseChunk(readChunk);
  if (compressChunk != NULL) {
    compressChunk->SetSize (compressChunk->GetMaxSize() - zStream.avail_out); 
    compressChunk->SetEOF(true);
    fTargetQueueCompressed->ReleaseChunk(compressChunk); 
  }
}

void CCompressionThread::CommpressLoopLibz2()
{
  bool bEOF = false;
  bz_stream  bzsStream;
  int action, ret = Z_OK;
  CBufferChunk *readChunk = NULL;
  CBufferChunk *compressChunk = NULL;

  memset(&bzsStream, 0, sizeof(bzsStream));
  ret = BZ2_bzCompressInit(&bzsStream, 9, 0, 0);
  if (ret != BZ_OK)
     THROWEX(EBZip2CompressionException, ret);

  while (ret != BZ_STREAM_END ) {
    if (bzsStream.avail_in == 0 && !bEOF) {
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      readChunk = fSourceQueueDecompressed->GetChunk(); // may block
      if (!readChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bEOF = readChunk->IsEOF();
      bzsStream.next_in = (char*)readChunk->GetData();
      bzsStream.avail_in = readChunk->GetSize();
    }

    if (bzsStream.avail_out == 0) {
      if (compressChunk) {
        compressChunk->SetSize(compressChunk->GetMaxSize());// (zsStream.total_out);
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
      }
      compressChunk = fSourceQueueCompressed->GetChunk();
      if (!compressChunk)
        THROW_INT_EXC(EInternalException::getChunkError);
      bzsStream.next_out = (char*)compressChunk->GetData();
      bzsStream.avail_out = compressChunk->GetMaxSize();
    }

    action = bEOF ? BZ_FINISH : BZ_RUN;
    if (bzsStream.next_in > 0) {
      ret = BZ2_bzCompress(&bzsStream, action);
      if (ret < 0) {
        ATLTRACE("Error in bzip2 compressing data, error code: %d\n", ret);
        THROWEX(EBZip2CompressionException, ret);
      }
    }

    if (fCancel) {
      BZ2_bzCompressEnd(&bzsStream);
      if (compressChunk)
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
      if (readChunk)
        fTargetQueueDecompressed->ReleaseChunk(readChunk);
      Terminate(-1);  // terminate thread after releasing buffer and before acquiring next one
    }
  } 
  
  ret = BZ2_bzCompressEnd(&bzsStream);
  if (ret < 0) {
    ATLTRACE("Error in termination of bzip2 compression, error code: %d\n", ret);
    THROWEX(EBZip2CompressionException, ret);
  }

  if (readChunk != NULL)
    fTargetQueueDecompressed->ReleaseChunk(readChunk);
  if (compressChunk != NULL) {
    compressChunk->SetSize (compressChunk->GetMaxSize() - bzsStream.avail_out);
    compressChunk->SetEOF(true);
    fTargetQueueCompressed->ReleaseChunk(compressChunk); 
  }
}

//---------------------------------------------------------------------------
// LZ4 Frame compression loop.
// useHC=false → fast LZ4 (level 0), useHC=true → LZ4-HC (level 9).
// Input is consumed in 64 KB sub-blocks so that LZ4F_compressBound() is
// always satisfied without needing to know the exact chunk size in advance.
//
void CCompressionThread::CompressLoopLZ4(bool useHC)
{
  static const size_t BLOCK_SIZE = 65536; // LZ4F_max64KB

  LZ4F_cctx* ctx = NULL;
  LZ4F_preferences_t prefs;
  CBufferChunk *readChunk   = NULL;
  CBufferChunk *compressChunk = NULL;
  bool bEOF = false;

  memset(&prefs, 0, sizeof(prefs));
  prefs.frameInfo.blockSizeID        = LZ4F_max64KB;
  prefs.frameInfo.blockMode          = LZ4F_blockIndependent;
  prefs.frameInfo.contentChecksumFlag = LZ4F_contentChecksumEnabled;
  prefs.autoFlush                    = 1;
  prefs.compressionLevel             = useHC ? 9 : 0;

  LZ4F_errorCode_t err = LZ4F_createCompressionContext(&ctx, LZ4F_VERSION);
  if (LZ4F_isError(err))
    THROW_INT_EXC(EInternalException::lz4CompressError);

  // Pre-compute worst-case compressed size for one BLOCK_SIZE input block.
  // With autoFlush=1 this is what a single compressUpdate call may produce.
  const size_t maxCompressedBlock = LZ4F_compressBound(BLOCK_SIZE, &prefs);

  // Acquire first output chunk and write the LZ4 frame header.
  compressChunk = fSourceQueueCompressed->GetChunk();
  if (!compressChunk) {
    LZ4F_freeCompressionContext(ctx);
    THROW_INT_EXC(EInternalException::getChunkError);
  }
  BYTE*  dst         = (BYTE*)compressChunk->GetData();
  size_t dstCapacity = compressChunk->GetMaxSize();
  size_t dstUsed     = 0;

  size_t headerSize = LZ4F_compressBegin(ctx, dst, dstCapacity, &prefs);
  if (LZ4F_isError(headerSize)) {
    LZ4F_freeCompressionContext(ctx);
    fTargetQueueCompressed->ReleaseChunk(compressChunk);
    THROW_INT_EXC(EInternalException::lz4CompressError);
  }
  dstUsed += headerSize;

  // Main compression loop — process each input chunk in BLOCK_SIZE pieces.
  while (!bEOF) {
    if (readChunk)
      fTargetQueueDecompressed->ReleaseChunk(readChunk);
    readChunk = fSourceQueueDecompressed->GetChunk(); // may block
    if (!readChunk) {
      LZ4F_freeCompressionContext(ctx);
      THROW_INT_EXC(EInternalException::getChunkError);
    }
    bEOF = readChunk->IsEOF();

    const BYTE* src       = (const BYTE*)readChunk->GetData();
    size_t      srcSize   = readChunk->GetSize();
    size_t      srcOffset = 0;

    while (srcOffset < srcSize) {
      size_t blockSize = srcSize - srcOffset;
      if (blockSize > BLOCK_SIZE) blockSize = BLOCK_SIZE;

      // Ensure there is room for the worst-case compressed block.
      if (dstCapacity - dstUsed < maxCompressedBlock) {
        compressChunk->SetSize(dstUsed);
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
        compressChunk = fSourceQueueCompressed->GetChunk();
        if (!compressChunk) {
          LZ4F_freeCompressionContext(ctx);
          THROW_INT_EXC(EInternalException::getChunkError);
        }
        dst         = (BYTE*)compressChunk->GetData();
        dstCapacity = compressChunk->GetMaxSize();
        dstUsed     = 0;
      }

      size_t written = LZ4F_compressUpdate(ctx,
                                           dst + dstUsed, dstCapacity - dstUsed,
                                           src + srcOffset, blockSize,
                                           NULL);
      if (LZ4F_isError(written)) {
        LZ4F_freeCompressionContext(ctx);
        THROW_INT_EXC(EInternalException::lz4CompressError);
      }
      dstUsed   += written;
      srcOffset += blockSize;

      if (fCancel) {
        LZ4F_freeCompressionContext(ctx);
        if (compressChunk) fTargetQueueCompressed->ReleaseChunk(compressChunk);
        if (readChunk)     fTargetQueueDecompressed->ReleaseChunk(readChunk);
        Terminate(-1);
      }
    }
  }

  // Finalize: write the LZ4 frame end-mark + content checksum.
  // LZ4F_compressBound(0, ...) gives the exact space needed for flush+end.
  const size_t endBound = LZ4F_compressBound(0, &prefs);
  if (dstCapacity - dstUsed < endBound) {
    compressChunk->SetSize(dstUsed);
    fTargetQueueCompressed->ReleaseChunk(compressChunk);
    compressChunk = fSourceQueueCompressed->GetChunk();
    if (!compressChunk) {
      LZ4F_freeCompressionContext(ctx);
      THROW_INT_EXC(EInternalException::getChunkError);
    }
    dst         = (BYTE*)compressChunk->GetData();
    dstCapacity = compressChunk->GetMaxSize();
    dstUsed     = 0;
  }

  size_t endSize = LZ4F_compressEnd(ctx, dst + dstUsed, dstCapacity - dstUsed, NULL);
  if (LZ4F_isError(endSize)) {
    LZ4F_freeCompressionContext(ctx);
    THROW_INT_EXC(EInternalException::lz4CompressError);
  }
  dstUsed += endSize;

  LZ4F_freeCompressionContext(ctx);

  if (readChunk != NULL)
    fTargetQueueDecompressed->ReleaseChunk(readChunk);
  if (compressChunk != NULL) {
    compressChunk->SetSize(dstUsed);
    compressChunk->SetEOF(true);
    fTargetQueueCompressed->ReleaseChunk(compressChunk);
  }
}

//---------------------------------------------------------------------------
// Zstandard streaming compression loop.
// Uses fCompressionLevel (default 6); ZSTD accepts levels 1–22.
//
void CCompressionThread::CompressLoopZSTD()
{
  ZSTD_CStream* stream = ZSTD_createCStream();
  if (!stream)
    THROW_INT_EXC(EInternalException::zstdCompressError);

  int level = (fCompressionLevel > 0) ? fCompressionLevel : ZSTD_CLEVEL_DEFAULT;
  size_t initRet = ZSTD_initCStream(stream, level);
  if (ZSTD_isError(initRet)) {
    ZSTD_freeCStream(stream);
    THROW_INT_EXC(EInternalException::zstdCompressError);
  }

  CBufferChunk *readChunk     = NULL;
  CBufferChunk *compressChunk = NULL;
  bool bEOF = false;

  compressChunk = fSourceQueueCompressed->GetChunk();
  if (!compressChunk) {
    ZSTD_freeCStream(stream);
    THROW_INT_EXC(EInternalException::getChunkError);
  }
  ZSTD_outBuffer outBuf;
  outBuf.dst  = compressChunk->GetData();
  outBuf.size = compressChunk->GetMaxSize();
  outBuf.pos  = 0;

  // Main compression loop.
  while (!bEOF) {
    if (readChunk)
      fTargetQueueDecompressed->ReleaseChunk(readChunk);
    readChunk = fSourceQueueDecompressed->GetChunk(); // may block
    if (!readChunk) {
      ZSTD_freeCStream(stream);
      THROW_INT_EXC(EInternalException::getChunkError);
    }
    bEOF = readChunk->IsEOF();

    ZSTD_inBuffer inBuf;
    inBuf.src  = readChunk->GetData();
    inBuf.size = readChunk->GetSize();
    inBuf.pos  = 0;

    while (inBuf.pos < inBuf.size) {
      size_t ret = ZSTD_compressStream(stream, &outBuf, &inBuf);
      if (ZSTD_isError(ret)) {
        ZSTD_freeCStream(stream);
        THROW_INT_EXC(EInternalException::zstdCompressError);
      }
      // If the output buffer is full, flush it and get a new chunk.
      if (outBuf.pos == outBuf.size) {
        compressChunk->SetSize(outBuf.pos);
        fTargetQueueCompressed->ReleaseChunk(compressChunk);
        compressChunk = fSourceQueueCompressed->GetChunk();
        if (!compressChunk) {
          ZSTD_freeCStream(stream);
          THROW_INT_EXC(EInternalException::getChunkError);
        }
        outBuf.dst  = compressChunk->GetData();
        outBuf.size = compressChunk->GetMaxSize();
        outBuf.pos  = 0;
      }

      if (fCancel) {
        ZSTD_freeCStream(stream);
        if (compressChunk) fTargetQueueCompressed->ReleaseChunk(compressChunk);
        if (readChunk)     fTargetQueueDecompressed->ReleaseChunk(readChunk);
        Terminate(-1);
      }
    }
  }

  // Finalize: flush any remaining internal data and write the frame epilogue.
  // ZSTD_endStream returns 0 when the frame is completely written.
  size_t remaining;
  do {
    remaining = ZSTD_endStream(stream, &outBuf);
    if (ZSTD_isError(remaining)) {
      ZSTD_freeCStream(stream);
      THROW_INT_EXC(EInternalException::zstdCompressError);
    }
    if (remaining > 0) {
      // More data to flush — output buffer is full; get a new chunk.
      compressChunk->SetSize(outBuf.pos);
      fTargetQueueCompressed->ReleaseChunk(compressChunk);
      compressChunk = fSourceQueueCompressed->GetChunk();
      if (!compressChunk) {
        ZSTD_freeCStream(stream);
        THROW_INT_EXC(EInternalException::getChunkError);
      }
      outBuf.dst  = compressChunk->GetData();
      outBuf.size = compressChunk->GetMaxSize();
      outBuf.pos  = 0;
    }
  } while (remaining > 0);

  ZSTD_freeCStream(stream);

  if (readChunk != NULL)
    fTargetQueueDecompressed->ReleaseChunk(readChunk);
  if (compressChunk != NULL) {
    compressChunk->SetSize(outBuf.pos);
    compressChunk->SetEOF(true);
    fTargetQueueCompressed->ReleaseChunk(compressChunk);
  }
}
