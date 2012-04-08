#!/usr/bin/env python
# Copyright 2006 John-Mark Gurney <jmg@funkthat.com>
'''DVD Handling'''

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/dvd.py#1 $

default_audio_lang = 'en'

import itertools
import os
import sets

import sys
sys.path.append('mpegts')
try:
	import mpegts
	audiofilter = lambda x, y: mpegts.DVDAudioFilter(x, y)
except ImportError:
	print >>sys.stderr, 'module mpegts could not be loaded, not filtering audio'
	audiofilter = lambda x, y: x

from pydvdread import *

from DIDLLite import StorageFolder, Movie, VideoItem, Resource
from FSStorage import FSObject, registerklassfun

from twisted.python import log, threadable
from twisted.spread import pb
from twisted.web import resource, server

def gennameindexes(pref, item):
	ret = []
	d = {}
	for i, title in enumerate(item):
		t = '%s %d (%s)' % (pref, i + 1, title.time)
		ret.append(t)
		d[t] = i

	return ret, d

class IterTransfer(pb.Viewable):
	def __init__(self, iterable, request):
		self.iter = iter(iterable)
		self.request = request
		request.registerProducer(self, 0)

	def resumeProducing(self):
		if not self.request:
			return
		# get data and write to request.
		try:
			data = self.iter.next()
			if data:
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

threadable.synchronize(IterTransfer)

class IterGenResource(resource.Resource):
	isLeaf = True

	def __init__(self, itergen):
		resource.Resource.__init__(self)

		self.itergen = itergen

	def render(self, request):
		request.setHeader('content-type', 'video/mpeg')

		if request.method == 'HEAD':
			return ''

		# return data
		IterTransfer(self.itergen(), request)
		# and make sure the connection doesn't get closed
		return server.NOT_DONE_YET

class DVDChapter(VideoItem):
	def __init__(self, *args, **kwargs):
		self.dvdtitle = kwargs['dvdtitle']
		self.chapter = kwargs['chapter']
		del kwargs['dvdtitle'], kwargs['chapter']

		audio = self.dvdtitle.selectaudio(default_audio_lang)
		kwargs['content'] = IterGenResource(lambda i = self.chapter,
		    p = audio.pos: audiofilter(i, 0x80 + p))
		VideoItem.__init__(self, *args, **kwargs)

		self.url = '%s/%s' % (self.cd.urlbase, self.id)
		self.res = Resource(self.url, 'http-get:*:video/mpeg:*')
		#self.res.size = self.chapter.size

	def doUpdate(self):
		pass

class DVDTitle(StorageFolder):
	def __init__(self, *args, **kwargs):
		self.dvdtitle = kwargs['dvdtitle']
		self.dvddisc = kwargs['dvddisc']
		del kwargs['dvdtitle'], kwargs['dvddisc']

		audio = self.dvdtitle.selectaudio(default_audio_lang)
		kwargs['content'] = IterGenResource(lambda dt = self.dvdtitle,
		    p = audio.pos: audiofilter(itertools.chain(*dt), 0x80 + p))
		StorageFolder.__init__(self, *args, **kwargs)

		self.url = '%s/%s' % (self.cd.urlbase, self.id)
		self.res = Resource(self.url, 'http-get:*:video/mpeg:*')

		# mapping from path to objectID
		self.pathObjmap = {}

	def checkUpdate(self):
		self.doUpdate()
		#return self.dvddisc.checkUpdate()
		return self

	def doUpdate(self):
		doupdate = False
		origchildren, toindex = gennameindexes('Chapter', self.dvdtitle)
		children = sets.Set(origchildren)
		for i in self.pathObjmap.keys():
			if i not in children:
				doupdate = True
				# delete
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for i in origchildren:
			if i in self.pathObjmap:
				continue

			# new object
			self.pathObjmap[i] = self.cd.addItem(self.id,
			    DVDChapter, i, dvdtitle = self.dvdtitle,
			    chapter = self.dvdtitle[toindex[i]])
			doupdate = True

		if doupdate:
			StorageFolder.doUpdate(self)


class DVDDisc(FSObject, StorageFolder):
	def __init__(self, *args, **kwargs):
		path = kwargs['path']
		del kwargs['path']

		StorageFolder.__init__(self, *args, **kwargs)
		FSObject.__init__(self, path)

		# mapping from path to objectID
		self.pathObjmap = {}

	def doUpdate(self):
		# open the DVD as necessary.
		self.dvd = DVD(self.FSpath)

		doupdate = False
		origchildren, toindex = gennameindexes('Title', self.dvd)
		children = sets.Set(origchildren)
		for i in self.pathObjmap.keys():
			if i not in children:
				doupdate = True
				# delete
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for i in origchildren:
			if i in self.pathObjmap:
				continue

			# new object
			self.pathObjmap[i] = self.cd.addItem(self.id, DVDTitle,
			    i, dvdtitle = self.dvd[toindex[i]], dvddisc = self)
			doupdate = True

		if doupdate:
			StorageFolder.doUpdate(self)

def detectdvd(path, fobj):
	if os.path.isdir(path):
		# Make sure we there is only a VIDEO_TS in there, even
		# if there is a VIDEO_TS w/ other files, we will open
		# the VIDEO_TS as a DVD (if it is one)
		ld = os.listdir(path)
		if ld == ['VIDEO_TS' ]:
			pass
		elif not filter(lambda x: x[:4] != 'VTS_' and
		    x[:9] != 'VIDEO_TS.', ld):
			pass
		else:
			return None, None

	d = DVD(path)
	return DVDDisc, { 'path': path }

registerklassfun(detectdvd)
