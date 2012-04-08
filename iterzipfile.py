#!/usr/bin/env python
# Copyright 2006 John-Mark Gurney <jmg@funkthat.com>

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/iterzipfile.py#1 $

import binascii
import os
import zipfile
from zipfile import *
try:
	import zlib # We may need its compression method
except ImportError:
	zlib = None

__all__ = zipfile.__all__

def dupfile(fp):
	if not hasattr(fp, 'fileno'):
		raise ValueError, 'must be operating on real file'
	newfp = os.fdopen(os.dup(fp.fileno()))
	return newfp

class ZipFile(ZipFile):
	def readiter(self, name, blksize=16384):
		"""Return file bytes (as a string) for name."""

		if self.mode not in ("r", "a"):
			raise RuntimeError, 'read() requires mode "r" or "a"'
		if not self.fp:
			raise RuntimeError, "Attempt to read ZIP archive " \
			    "that was already closed"
		zinfo = self.getinfo(name)
		fp = dupfile(self.fp)
		fp.seek(zinfo.file_offset, 0)
		if zinfo.compress_type == ZIP_STORED:
			assert zinfo.file_size == zinfo.compress_size
			i = 0
			while i < zinfo.file_size:
				yield fp.read(min(blksize, zinfo.file_size - i))
		elif zinfo.compress_type == ZIP_DEFLATED:
			if not zlib:
				raise RuntimeError, "De-compression requires " \
				    "the (missing) zlib module"
			# zlib compress/decompress code by Jeremy Hylton of CNRI
			uncomp = 0
			comp = 0
			dc = zlib.decompressobj(-15)
			crc = None
			doflush = False
			while uncomp < zinfo.file_size:
				if not dc.unconsumed_tail:
					compread = min(blksize,
					    zinfo.compress_size - comp)
					bytes = fp.read(compread)
					comp += compread
					if compread == 0:
						doflush = True
				else:
					bytes = dc.unconsumed_tail
				if doflush:
					# need to feed in unused pad byte so
					# that zlib won't choke
					bytes = dc.decompress('Z') + dc.flush()
				else:
					bytes = dc.decompress(bytes, blksize)
				yield bytes
				uncomp += len(bytes)
				if crc is None:
					crc = binascii.crc32(bytes)
				else:
					crc = binascii.crc32(bytes, crc)
			if crc != zinfo.CRC:
				raise BadZipfile, "Bad CRC-32 for file %s" % \
				    name
		else:
			raise BadZipfile, "Unsupported compression method " \
			    "%d for file %s" % (zinfo.compress_type, name)
