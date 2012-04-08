#!/usr/bin/env python
# Copyright 2006-2008 John-Mark Gurney <jmg@funkthat.com>

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/ZipStorage.py#1 $

import itertools
import os.path
import sets
import time
import iterzipfile
zipfile = iterzipfile
import itertarfile
tarfile = itertarfile
try:
	import iterrarfile
	rarfile = iterrarfile
except ImportError:
	class rarfile:
		pass

	rarfile = rarfile()
	rarfile.is_rarfile = lambda x: False

import FileDIDL
from DIDLLite import StorageFolder, Item, VideoItem, AudioItem, TextItem, ImageItem, Resource
from FSStorage import FSObject, registerklassfun

from twisted.python import log, threadable
from twisted.spread import pb
from twisted.web import http
from twisted.web import server
from twisted.web import resource

def inserthierdict(d, name, obj, sep):
	if not name:
		return

	if sep is not None:
		i = name.find(sep)

	if sep is None or i == -1:
		d[name] = obj
		return

	dname = name[:i]
	rname = name[i + 1:]
	# remaining path components
	try:
		inserthierdict(d[dname], rname, obj, sep)
	except KeyError:
		d[dname] = {}
		inserthierdict(d[dname], rname, obj, sep)

def buildNameHier(names, objs, sep):
	ret = {}
	for n, o in itertools.izip(names, objs):
		#Skip directories in a TarFile or RarFile
		if hasattr(o, 'isdir') and o.isdir():
			continue
		inserthierdict(ret, n, o, sep)

	return ret

class ZipFileTransfer(pb.Viewable):
	def __init__(self, zf, name, request):
		self.zf = zf
		self.size = zf.getinfo(name).file_size
		self.iter = zf.readiter(name)
		self.request = request
		self.written = 0
		request.registerProducer(self, 0)

	def resumeProducing(self):
		if not self.request:
			return
		# get data and write to request.
		try:
			data = self.iter.next()
			if data:
				self.written += len(data)
				# this .write will spin the reactor, calling
				# .doWrite and then .resumeProducing again, so
				# be prepared for a re-entrant call
				self.request.write(data)
		except StopIteration:
			if self.request:
				self.request.unregisterProducer()
				self.request.finish()
				self.request = None

	def pauseProducing(self):
		pass

	def stopProducing(self):
		# close zipfile
		self.request = None

	# Remotely relay producer interface.

	def view_resumeProducing(self, issuer):
		self.resumeProducing()

	def view_pauseProducing(self, issuer):
		self.pauseProducing()

	def view_stopProducing(self, issuer):
		self.stopProducing()

	synchronized = ['resumeProducing', 'stopProducing']

threadable.synchronize(ZipFileTransfer)

class ZipResource(resource.Resource):
	# processors = {}

	isLeaf = True

	def __init__(self, zf, name, mt):
		resource.Resource.__init__(self)
		self.zf = zf
		self.zi = zf.getinfo(name)
		self.name = name
		self.mt = mt

	def getFileSize(self):
		return self.zi.file_size

	def render(self, request):
		request.setHeader('content-type', self.mt)

		# We could possibly send the deflate data directly!
		if None and self.encoding:
			request.setHeader('content-encoding', self.encoding)

		if request.setLastModified(time.mktime(list(self.zi.date_time) +
		    [ 0, 0, -1])) is http.CACHED:
			return ''

		request.setHeader('content-length', str(self.getFileSize()))
		if request.method == 'HEAD':
			return ''

		# return data
		ZipFileTransfer(self.zf, self.name, request)
		# and make sure the connection doesn't get closed
		return server.NOT_DONE_YET

class ZipItem:
	'''Basic zip stuff initalization'''

	def __init__(self, *args, **kwargs):
		self.zo = kwargs['zo']
		del kwargs['zo']
		self.zf = kwargs['zf']
		del kwargs['zf']
		self.name = kwargs['name']
		del kwargs['name']

	def checkUpdate(self):
		self.doUpdate()
		return self.zo.checkUpdate()

class ZipFile(ZipItem, Item):
	def __init__(self, *args, **kwargs):
		self.mimetype = kwargs['mimetype']
		del kwargs['mimetype']
		ZipItem.__init__(self, *args, **kwargs)
		self.zi = self.zf.getinfo(self.name)
		kwargs['content'] = ZipResource(self.zf, self.name,
		    self.mimetype)
		Item.__init__(self, *args, **kwargs)
		self.url = '%s/%s' % (self.cd.urlbase, self.id)
		self.res = Resource(self.url, 'http-get:*:%s:*' % self.mimetype)
		self.res.size = self.zi.file_size

	def doUpdate(self):
		pass

class ZipChildDir(ZipItem, StorageFolder):
	'''This is to represent a child dir of the zip file.'''

	def __init__(self, *args, **kwargs):
		self.hier = kwargs['hier']
		self.sep = kwargs['sep']
		del kwargs['hier'], kwargs['sep']
		ZipItem.__init__(self, *args, **kwargs)
		del kwargs['zf'], kwargs['zo'], kwargs['name']
		StorageFolder.__init__(self, *args, **kwargs)

		# mapping from path to objectID
		self.pathObjmap = {}

	def doUpdate(self):
		doupdate = False
		children = sets.Set(self.hier.keys())
		for i in self.pathObjmap.keys():
			if i not in children:
				# delete
				doupdate = True
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		cursep = self.sep
		for i in children:
			if i in self.pathObjmap:
				continue

			# new object
			pathname = cursep.join((self.name, i))
			if isinstance(self.hier[i], dict):
				# must be a dir
				self.pathObjmap[i] = self.cd.addItem(self.id,
				    ZipChildDir, i, zf=self.zf, zo=self,
				    name=pathname, hier=self.hier[i],
				    sep=cursep)
			else:
				klass, mt = FileDIDL.buildClassMT(ZipFile, i)
				if klass is None:
					continue
				self.pathObjmap[i] = self.cd.addItem(self.id,
				    klass, i, zf = self.zf, zo = self,
				    name = pathname, mimetype = mt)
			doupdate = True

		# sort our children
		self.sort(lambda x, y: cmp(x.title, y.title))
		if doupdate:
			StorageFolder.doUpdate(self)

	def __repr__(self):
		return '<ZipChildDir: len: %d>' % len(self.pathObjmap)

def tryTar(path):
	# Try to see if it's a tar file
	if path[-2:] == 'gz':
		comp = tarfile.TAR_GZIPPED
	elif path[-3:] == 'bz2':
		comp = tarfile.TAR_BZ2
	else:
		comp = tarfile.TAR_PLAIN
	return tarfile.TarFileCompat(path, compression=comp)

def canHandle(path):
	if zipfile.is_zipfile(path):
		return True

	if rarfile.is_rarfile(path):
		return True

	#tar is cheaper on __init__ than zipfile
	return tryTar(path)

def genZipFile(path):
	if zipfile.is_zipfile(path):
		return zipfile.ZipFile(path)

	if rarfile.is_rarfile(path):
		return rarfile.RarFile(path)

	try:
		return tryTar(path)
	except:
		#import traceback
		#traceback.print_exc(file=log.logfile)
		raise

class ZipObject(FSObject, StorageFolder):
	seps = [ '/', '\\' ]

	def __init__(self, *args, **kwargs):
		'''If a zip argument is passed it, use that as the zip archive.'''
		path = kwargs['path']
		del kwargs['path']

		StorageFolder.__init__(self, *args, **kwargs)
		FSObject.__init__(self, path)

		# mapping from path to objectID
		self.pathObjmap = {}

	def doUpdate(self):
		# open the zipfile as necessary.
		self.zip = genZipFile(self.FSpath)
		nl = self.zip.namelist()
		cnt = 0
		cursep = None
		for i in self.seps:
			newsum = sum([ j.count(i) for j in nl ])
			if newsum > cnt:
				cursep = i
				cnt = newsum
		self.sep = cursep
		hier = buildNameHier(nl, self.zip.infolist(), cursep)

		doupdate = False
		children = sets.Set(hier.keys())
		for i in self.pathObjmap.keys():
			if i not in children:
				doupdate = True
				# delete
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for i in children:
			if i in self.pathObjmap:
				continue

			# new object
			if isinstance(hier[i], dict):
				# must be a dir
				self.pathObjmap[i] = self.cd.addItem(self.id,
				    ZipChildDir, i, zf=self.zip, zo=self,
				    name=i, hier=hier[i], sep=cursep)
			else:
				klass, mt = FileDIDL.buildClassMT(ZipFile, i)
				if klass is None:
					continue
				self.pathObjmap[i] = self.cd.addItem(self.id,
				    klass, i, zf = self.zip, zo = self,
				    name = i, mimetype = mt)
			doupdate = True

		# sort our children
		self.sort(lambda x, y: cmp(x.title, y.title))

		if doupdate:
			StorageFolder.doUpdate(self)

def detectzipfile(path, fobj):
	try:
		canHandle(path)
	except:
		return None, None

	return ZipObject, { 'path': path }

registerklassfun(detectzipfile)
