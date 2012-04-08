#!/usr/bin/env python
# Copyright 2006 John-Mark Gurney <jmg@funkthat.com>

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/itertarfile.py#1 $

import tarfile
from tarfile import *

TAR_PLAIN = tarfile.TAR_PLAIN
TAR_GZIPPED = tarfile.TAR_GZIPPED
TAR_BZ2 = 'bz2'

__all__ = tarfile.__all__

class TarFileCompat(tarfile.TarFileCompat):
	def __init__(self, file, mode="r", compression=TAR_PLAIN):
		if compression != TAR_BZ2:
			tarfile.TarFileCompat.__init__(self, file, mode, compression)
			return

		self.tarfile = TarFile.bz2open(file, mode)
		if mode[0:1] == "r":
			members = self.tarfile.getmembers()
			for i in xrange(len(members)):
				m = members[i]
				m.filename = m.name
				m.file_size = m.size
				m.date_time = time.gmtime(m.mtime)[:6]

	def readiter(self, name, blksize=16384):
		f = self.tarfile.extractfile(self.tarfile.getmember(name))
		while True:
			data = f.read(blksize)
			if data == '':
				break
			yield data
