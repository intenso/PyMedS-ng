#!/usr/bin/env python
# Copyright 2006 John-Mark Gurney <jmg@funkthat.com>
'''Shoutcast Radio Feed'''

__version__ = '$Change: 1233 $'
# $Id: //depot/python/pymeds/pymeds-0.5/shoutcast.py#2 $

# The handling of defers and state in this module is not very good.  It
# needs some work to ensure that error cases are properly handled.  What
# do we do if we get no URLs for a PLS?  Do we delete ourselves just to be
# readded (when we get PLS refeshing working)?  Do we set a content-length
# to zero till we get one?

import ConfigParser
import StringIO
import os.path
import random

import traceback

from py_shoutcast import *

from DIDLLite import Container, MusicGenre, Item, AudioItem, Resource
from FSStorage import registerklassfun

from twisted.protocols import shoutcast
from twisted.python import log, threadable
from twisted.internet import defer, protocol, reactor
from twisted.web import error, http, resource, server
from twisted.web.client import getPage, _parse

PLSsection = 'playlist'

def cmpStation(a, b, keys = ( 'MimeType', 'Name', 'PLS_URL', 'Bitrate' )):
	if filter(lambda k, x = a, y = b: x[k] != y[k], keys):
		return False
	return True

def stationwbitratecmp(x, y):
	x, y = map(lambda a: a.title.split('-', 1)[1], (x, y))
	return cmp(x, y)

class GenreFeedAsync(feeds.GenreFeed):
	genre_url = 'http://www.shoutcast.com/sbin/newxml.phtml'

	def __init__(self, *args, **kwargs):
		self.havegenre = False
		self.fetchinggenre = None
		feeds.GenreFeed.__init__(self, *args, **kwargs)

	def gotGenre(self, page):
		self.genre = page
		self.havegenre = True

		# Wake everyone up
		self.fetchinggenre.callback(1)

	def errGenre(self, failure):
		raise NotImplementedError, failure

	def fetch_genres(self):
		if self.havegenre:
			return self.genre
		if not self.fetchinggenre:
			# Need to start fetching
			getPage(self.genre_url.encode('ascii')) \
			    .addCallbacks(self.gotGenre, self.errGenre)
			self.fetchinggenre = defer.Deferred()
		# Always raise this if we are waiting.
		raise self.fetchinggenre

	synchronized = ['fetch_genres', 'gotGenre', ]

threadable.synchronize(GenreFeedAsync)

class ShoutcastFeedAsync(feeds.ShoutcastFeed):
	def __init__(self, *args, **kwargs):
		feeds.ShoutcastFeed.__init__(self, *args, **kwargs)

		self.shout_url = \
		    'http://www.shoutcast.com/sbin/newxml.phtml?genre=' + \
		    self.genre

		self.havestations = False
		self.fetchingstations = None

	def gotStations(self, page):
		self.stations = page
		self.havestations = True

		# Wake everyone up
		self.fetchingstations.callback(1)

	def errStations(self, failure):
		raise NotImplementedError, failure

	def fetch_stations(self):
		if self.havestations:
			return self.stations
		if not self.fetchingstations:
			# Need to start fetching
			getPage(self.shout_url.encode('ascii')) \
			    .addCallbacks(self.gotStations, self.errStations)
			self.fetchingstations = defer.Deferred()
		# Always raise this if we are waiting.
		raise self.fetchingstations

	synchronized = ['fetch_stations', 'gotStations', ]

threadable.synchronize(ShoutcastFeedAsync)

class ShoutTransfer(shoutcast.ShoutcastClient):
	userAgent = 'because you block user-agents'
	def __init__(self, request, passback):
		shoutcast.ShoutcastClient.__init__(self)
		self.request = request
		self.passback = passback
		request.registerProducer(self, 1)

	def connectionLost(self, reason):
		#traceback.print_stack()
		log.msg('connectionLost:', `self.request`, `self.passback`)
		shoutcast.ShoutcastClient.connectionLost(self, reason)
		if self.request:
			self.request.unregisterProducer()
		if self.passback:
			self.passback(self.request)

		self.passback = None
		self.request = None

	def handleResponse(self, response):
		#Drop the data, the parts get the important data, if we got
		#here, the connection closed and we are going to die anyways.
		pass

	def stopProducing(self):
		if self.transport is not None:
			shoutcast.ShoutcastClient.stopProducing(self)
		self.request = None
		self.passback = None

	def gotMP3Data(self, data):
		if self.request is not None:
			self.request.write(data)

	def gotMetaData(self, data):
		log.msg("meta:", `data`)
		pass

	# Remotely relay producer interface.

	def view_resumeProducing(self, issuer):
		self.resumeProducing()

	def view_pauseProducing(self, issuer):
		self.pauseProducing()

	def view_stopProducing(self, issuer):
		self.stopProducing()

	synchronized = ['resumeProducing', 'stopProducing']

threadable.synchronize(ShoutTransfer)

class ShoutProxy(resource.Resource):
	# We should probably expire the PLS after a while.

	# setResponseCode(self, code, message=None)
	# setHeader(self, k, v)
	# write(self, data)
	# finish(self)

	isLeaf = True

	def __init__(self, url, mt):
		resource.Resource.__init__(self)
		self.shoutpls = url
		self.mt = mt
		self.urls = None
		self.fetchingurls = False

	def dump_exc(self):
		exc = StringIO.StringIO()
		traceback.print_exc(file=exc)
		exc.seek(0)
		self.request.setHeader('content-type', 'text/html')
		self.request.write(error.ErrorPage(http.INTERNAL_SERVER_ERROR,
		    http.RESPONSES[http.INTERNAL_SERVER_ERROR],
		    '<pre>%s</pre>' % exc.read()).render(self.request))
		self.request.finish()
		self.request = None

	def startNextConnection(self, request):
		url = self.urls[self.urlpos]
		self.urlpos = (self.urlpos + 1) % len(self.urls)
		scheme, host, port, path = _parse(url)
		#print `url`
		protocol.ClientCreator(reactor, ShoutTransfer, request,
		    self.startNextConnection).connectTCP(host, port)

	def triggerdefered(self, fun):
		map(fun, self.afterurls)
		self.afterurls = None

	def gotPLS(self, page):
		self.fetchingurls = False
		try:
			pls = ConfigParser.SafeConfigParser()
			pls.readfp(StringIO.StringIO(page))
			assert pls.getint(PLSsection, 'Version') == 2
			assert pls.has_option(PLSsection, 'numberofentries')
			cnt = pls.getint(PLSsection, 'numberofentries')
			self.urls = []
			for i in range(cnt):
				i += 1	# stupid one based arrays
				self.urls.append(pls.get(PLSsection,
				    'File%d' % i))
			#log.msg('pls urls:', self.urls)
			self.urlpos = random.randrange(len(self.urls))
		except:
			self.dump_exc()
			self.urls = None
			self.triggerdefered(lambda x: x.errback(1))
			return

		self.triggerdefered(lambda x: x.callback(1))

	def errPLS(self, failure):
		self.fetchingurls = False
		self.triggerdefered(lambda x: x.errback(1))

	def processRequest(self, ign, request):
		self.startNextConnection(request)

	def errRequest(self, failure, request):
		request.write(failure.render(self.request))
		request.finish()

	def render(self, request):
		request.setHeader('content-type', self.mt)
		# XXX - PS3 doesn't support streaming, this makes it think
		# that is has data, but it needs to d/l the entire thing.
		#request.setHeader('content-length', 1*1024*1024)

		if request.method == 'HEAD':
			return ''

		# need to start the state machine
		#   a) fetch the playlist
		#   b) choose a random starting point
		#   c) connect to the server
		#   d) select next server and goto c
		# return data
		if self.urls is None:
			if not self.fetchingurls:
				# Get the PLS
				self.fetchingurls = True
				# Not really sure if ascii is the correct one,
				# shouldn't getPage do proper escaping for me?
				getPage(self.shoutpls.encode('ascii')) \
				    .addCallbacks(self.gotPLS, self.errPLS)
				self.afterurls = [ defer.Deferred() ]
			else:
				self.afterurls.append(defer.Deferred())
			# Always add the callback if we don't have urls
			self.afterurls[-1].addCallbacks(self.processRequest,
			    errback=self.errRequest, callbackArgs=(request, ),
			    errbackArgs=(request, ))
		else:
			self.startNextConnection(request)
		# and make sure the connection doesn't get closed
		return server.NOT_DONE_YET

	synchronized = [ 'gotPLS', 'render', 'startNextConnection',
			 'triggerdefered', ]
threadable.synchronize(ShoutProxy)

class ShoutStation(AudioItem):
	def __init__(self, *args, **kwargs):
		self.station = kwargs['station']
		del kwargs['station']

		kwargs['content'] = ShoutProxy(self.station['PLS_URL'],
		    self.station['MimeType'].encode('ascii'))
		AudioItem.__init__(self, *args, **kwargs)
		self.url = '%s/%s' % (self.cd.urlbase, self.id)
		self.res = Resource(self.url, 'http-get:*:%s:*' % \
		    self.station['MimeType'].encode('ascii'))
		#self.res = Resource(self.url + '/pcm', 'http-get:*:%s:*' % \
		#    'audio/x-wav')
		self.bitrate = self.station['Bitrate'] * 128 # 1024k / 8bit

class ShoutGenre(MusicGenre):
	def __init__(self, *args, **kwargs):
		self.genre = kwargs['genre']
		del kwargs['genre']
		self.feeds = ShoutcastFeedAsync(self.genre)
		self.sl = None
		self.pathObjmap = {}

		MusicGenre.__init__(self, *args, **kwargs)

	def genStations(self, stations):
		ret = {}
		dupcnt = {}

		for i in stations:
			name = i['Name']
			if name in ret:
				# got a dup
				if name not in dupcnt:
					dupcnt[name] = 2

				ret['%s - %d' % (name, dupcnt[name])] = i
				dupcnt[name] += 1
			else:
				ret[name] = i

		return ret

	def checkUpdate(self):
		self.doUpdate()
		return self

	def doUpdate(self):
		#traceback.print_stack(file=log.logfile)
		stations = self.feeds.parse_stations()
		if stations == self.sl:
			return

		nl = self.genStations(stations)

		doupdate = False
		for i in self.pathObjmap.keys():
			if i not in nl:
				# delete
				doupdate = True
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for name, i in nl.iteritems():
			if name in self.pathObjmap:
				if cmpStation(i, self.cd[self.pathObjmap[name]].station):
					continue
				# Didn't match, readd
				self.cd.delItem(self.pathObjmap[name])
				del self.pathObjmap[name]

			doupdate = True
			self.pathObjmap[name] = self.cd.addItem(self.id,
			    ShoutStation, '%sk-%s' % (i['Bitrate'], name),
			    station = i)

		self.sl = stations

		# sort our children
		self.sort(lambda *a: stationwbitratecmp(*a))
		if doupdate:
			Container.doUpdate(self)

class ShoutCast(Container):
	def __init__(self, *args, **kwargs):
		Container.__init__(self, *args, **kwargs)

		self.genres = GenreFeedAsync()
		self.genre_list = None
		self.pathObjmap = {}

	def checkUpdate(self):
		self.doUpdate()
		return self

	def doUpdate(self):
		#traceback.print_stack(file=log.logfile)
		nl = self.genres.parse_genres()
		if nl == self.genre_list:
			return

		doupdate = False
		for i in self.pathObjmap.keys():
			if i not in nl:
				# delete
				doupdate = True
				self.cd.delItem(self.pathObjmap[i])
				del self.pathObjmap[i]

		for i in nl:
			if i in self.pathObjmap:
				continue
			doupdate = True
			self.pathObjmap[i] = self.cd.addItem(self.id,
				ShoutGenre, i, genre = i)

		self.genre_list = nl

		# sort our children
		self.sort(lambda x, y: cmp(x.title, y.title))
		if doupdate:
			Container.doUpdate(self)

def detectshoutcastfile(path, fobj):
	path = os.path.basename(path)
	if path == 'SHOUTcast Radio':
		return ShoutCast, { }
	return None, None

registerklassfun(detectshoutcastfile)
