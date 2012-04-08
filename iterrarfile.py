#!/usr/bin/env python
# Copyright 2008 John-Mark Gurney <jmg@funkthat.com>

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/iterrarfile.py#1 $

import rarfile
from rarfile import *

class RarFile(rarfile.RarFile):
	def readiter(self, name, blksize=16384):
		yield self.read(name)
