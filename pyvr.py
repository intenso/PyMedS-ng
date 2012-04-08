#!/usr/bin/env python
# Copyright 2008 John-Mark Gurney <jmg@funktaht.com>
'''PVR Interface'''

__version__ = '$Change: 1109 $'
# $Id: //depot/python/pymeds/main/shoutcast.py#13 $

from DIDLLite import Container, Item, VideoItem, Resource
from FSStorage import registerklassfun

import os.path
import time
from twisted.internet import reactor
from twisted.python import log
import twisted.web
import urlparse

def getPage(url, contextFactory=None, *args, **kwargs):
	"""Download a web page as a string.

	Download a page. Return the HTTPClientFactory, which will
	callback with a page (as a string) or errback with a
	description of the error.

	See HTTPClientFactory to see what extra args can be passed.
	"""
	from twisted.web.client import _parse, HTTPClientFactory

	scheme, host, port, path = _parse(url)
	factory = HTTPClientFactory(url, *args, **kwargs)
	if scheme == 'https':
		from twisted.internet import ssl
		if contextFactory is None:
			contextFactory = ssl.ClientContextFactory()
		reactor.connectSSL(host, port, factory, contextFactory)
	else:
		reactor.connectTCP(host, port, factory)
	return factory

class PYVRShow(VideoItem):
	def __init__(self, *args, **kwargs):
		baseurl = kwargs['url']
		self.info = kwargs['info']
		del kwargs['info'], kwargs['url']

		VideoItem.__init__(self, *args, **kwargs)

		url = self.info['link']
		sc = urlparse.urlparse(url)[0]
		if not sc:
			# need to combine w/ base url
			url = urlparse.urljoin(baseurl, url)
		self.res = Resource(url,
		    'http-get:*:%s:*' % self.info['mimetype'])
		self.res.duration = self.info['duration']

	def doUpdate(self):
		pass

import xml.sax
import xml.sax.handler
from xml.sax.saxutils import unescape

class RecordingXML(xml.sax.handler.ContentHandler):
	dataels = ('title', 'subtitle', 'duration', 'mimetype', 'link',
	    'delete', )

	def __init__(self):
		self.shows = {}
		self.data = None

	def characters(self, chars):
		if self.data is not None:
			self.data.append(chars)

	def startElement(self, name, attrs):
		if name in self.dataels:
			self.data = []
			self.curel = name
		elif name == 'record':
			self.currec = {}

	def endElement(self, name):
		if name in self.dataels:
			data = unescape(''.join(self.data))
			self.currec[self.curel] = data
		elif name == 'record':
			rec = self.currec
			try:
				self.shows[rec['title']].append(rec)
			except KeyError:
				self.shows[rec['title']] = [ rec ]

		self.data = None

def recxmltoobj(page):
	obj = RecordingXML()
	xml.sax.parseString(page, obj)

	return obj.shows

class PYVRShows(Container):
	def __init__(self, *args, **kwargs):
		self.pyvr = kwargs['pyvr']
		del kwargs['pyvr']
		self.show = kwargs['show']
		del kwargs['show']

		Container.__init__(self, *args, **kwargs)

		self.pathObjmap = {}
		self.shows = {}
		self.lastmodified = None

	def checkUpdate(self):
		self.pyvr.checkUpdate()
		if self.pyvr.lastmodified != self.lastmodified:
			self.doUpdate()

	@staticmethod
	def getunique(eps, ep):
		i = 1
		while True:
			title = '%s Copy %d' % (ep['subtitle'], i)
			if not eps.has_key(title):
				return title
			i += 1

	@staticmethod
	def eplisttodict(eps):
		ret = {}
		for pos, i in enumerate(eps):
			title = i['subtitle']
			if ret.has_key(title):
				print 'WARNING: dup:', `i`, `ret[title]`
				title = PYVRShows.getunique(ret, i)
			i['pos'] = pos
			ret[title] = i

		return ret

	def doUpdate(self):
		nl = self.eplisttodict(self.pyvr.shows[self.show])

		doupdate = False
		for i in self.pathObjmap.keys():
			if i not in nl:
				# delete
				doupdate = True
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for i in nl:
			if i in self.pathObjmap and self.shows[i] == nl[i]:
				continue
			doupdate = True
			if i in self.pathObjmap:
				# changed
				self.cd.delItem(self.pathObjmap[i])
			self.pathObjmap[i] = self.cd.addItem(self.id,
				PYVRShow, i, url=self.pyvr.url, info=nl[i])

		self.shows = nl

		# sort our children
		#self.sort(lambda x, y: cmp(x.title, y.title))
		self.sort(lambda x, y: cmp(x.info['pos'], y.info['pos']))
		if doupdate:
			Container.doUpdate(self)

		self.lastmodified = self.pyvr.lastmodified

class PYVR(Container):
	def __init__(self, *args, **kwargs):
		self.url = kwargs['url']
		del kwargs['url']

		Container.__init__(self, *args, **kwargs)

		self.pathObjmap = {}
		self.pend = None
		self.lastmodified = None
		self.newobjs = None
		self.objs = {}
		self.lastcheck = 0

	def checkUpdate(self):
		if self.pend is not None:
			raise self.pend

		if time.time() - self.lastcheck < 10:
			return

		# Check to see if any changes have been made
		self.runCheck()

		raise self.pend

	def runCheck(self):
		print 'runCheck'
		self.page = getPage(self.url, method='HEAD')
		self.page.deferred.addErrback(self.errCheck).addCallback(
		    self.doCheck)
		self.pend = self.page.deferred

	def errCheck(self, x):
		print 'errCheck:', `x`
		self.runCheck()

	def doCheck(self, x):
		print 'doCheck:', self.page.status
		if self.page.status != '200':
			print 'foo'
			return reactor.callLater(.01, self.runCheck)

		self.lastcheck = time.time()
		slm = self.page.response_headers['last-modified']
		if slm == self.lastmodified:
			# Page the same, don't do anything
			self.pend = None
			return

		self.page = getPage(self.url)
		self.page.deferred.addCallback(self.parsePage)
		self.pend = self.page.deferred

		return self.pend

	def parsePage(self, page):
		slm = self.page.response_headers['last-modified']
		self.lastmodified = slm
		del self.page
		self.pend = None

		self.newobjs = recxmltoobj(page)
		self.doUpdate()

	def doUpdate(self):
		if self.newobjs is None:
			import traceback
			traceback.print_stack(file=log.logfile)
			return

		nl = self.newobjs

		doupdate = False
		for i in self.pathObjmap.keys():
			if i not in nl:
				# delete
				doupdate = True
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		# This data is referenced when adding new shows
		self.shows = nl
		for i in nl:
			if i in self.pathObjmap:
				continue
			doupdate = True
			try:
				self.pathObjmap[i] = self.cd.addItem(self.id,
					PYVRShows, i, show=i, pyvr=self)
			except:
				import traceback
				traceback.print_exc(file=log.logfile)
				raise

		self.newobjs = None

		# sort our children
		self.sort(lambda x, y: cmp(x.title, y.title))
		if doupdate:
			Container.doUpdate(self)

def detectpyvrfile(path, fobj):
	bn = os.path.basename(path)
	if bn == 'PYVR':
		return PYVR, { 'url': fobj.readline().strip() }
	return None, None

registerklassfun(detectpyvrfile)
