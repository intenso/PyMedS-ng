#!/usr/bin/env python
# Copyright 2006-2008 John-Mark Gurney <jmg@funkthat.com>
'''MPEG-TS Handling'''

__version__ = '$Change: 1227 $'
# $Id: //depot/python/pymeds/pymeds-0.5/mpegtsmod.py#1 $

tsselpypath = 'mpegts/tssel.py'
default_audio_lang = 'eng'

import array
import itertools
import os
import sets
import struct

import sys
mpegtspath = 'mpegts'
if mpegtspath not in sys.path:
	sys.path.append(mpegtspath)
import mpegts
import tssel

from DIDLLite import StorageFolder, VideoItem, Resource
from FSStorage import FSObject, registerklassfun

from twisted.python import log, threadable
from twisted.spread import pb
from twisted.internet import abstract, process, protocol, reactor
from twisted.web import error, http, resource, server

class _LimitedFile(file):
	def __init__(self, *args, **kwargs):
		self.__size = kwargs['size']
		del kwargs['size']
		file.__init__(self, *args, **kwargs)

	def remain(self):
		pos = self.tell()
		if pos > self.__size:
			return 0
		return self.__size - pos

	def read(self, size=-1):
		if size < 0:
			return file.read(self, self.remain())

		return file.read(self, min(size, self.remain()))

def _gennameindexes(chan):
	ret = []
	d = {}

	for i in chan:
		t = '%s %s.%s' % (i['name'], i['major'], i['minor'])
		ret.append(t)
		d[t] = i

	return ret, d

class MPEGTSTransfer(pb.Viewable):
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

threadable.synchronize(MPEGTSTransfer)

class DynamTSTransfer(pb.Viewable):
	def __init__(self, path, pmt, *pids):
		self.path = path
		#log.msg("DynamTSTransfer: pmt: %s, pids: %s" % (pmt, pids))
		self.pmt = pmt
		self.pids = pids
		self.didpat = False

	def resumeProducing(self):
		if not self.request:
			return

		repcnt = 0
		data = self.fp.read(min(abstract.FileDescriptor.bufferSize,
		    self.size - self.written) // 188 * 188)
		dataarray = array.array('B', data)
		for i in xrange(0, len(data), 188):
			if data[i] != 'G':
				print 'bad sync'
				continue
			frst = dataarray[i + 1]
			pid = (frst & 0x1f) << 8 | dataarray[i + 2]

			if not frst & 0x40:
				continue
			elif not self.didpat and pid == 0:
				startpmt = i + 4
				if ((dataarray[i + 3] >> 4) & 0x3) == 0x3:
					# Adaptation
					startpmt += dataarray[startpmt] + 1
				startpmt += dataarray[startpmt] + 1
				assert data[startpmt] =='\x00', (startpmt,
				    data[i:startpmt + 4])
				arraysize = ((dataarray[startpmt + 1] &
				    0xf) << 8) | dataarray[startpmt + 2]
				startpmt += 3
				arraysize -= 4	# CRC
				# Remaining fields before array
				startpmt += 5
				arraysize -= 5
				for startpmt in xrange(startpmt,
				    min(i + 188 - 3, startpmt + arraysize), 4):
					prognum, ppid = struct.unpack('>2H',
					    data[startpmt:startpmt + 4])
					ppid = ppid & 0x1fff
					if ppid == self.pmt:
						break
				else:
					raise KeyError, 'unable to find pmt(%d) in pkt: %s' % (pmt, `data[i:i + 188]`)

				self.pats = itertools.cycle(tssel.genpats(
				    self.pmt, prognum))
				self.didpat = True

			if pid == 0 and self.didpat:
				assert data[i + 4] =='\x00' and \
				    data[i + 5] == '\x00', 'error: %s' % `data[i:i + 10]`
				repcnt += 1
				pn = self.pats.next()
				data = data[:i] + pn + data[i +
				    188:]

		if repcnt > 1:
			print 'repcnt:', repcnt, 'len(data):', len(data)

		if data:
			self.written += len(data)
			self.request.write(data)
		if self.request and self.fp.tell() == self.size:
			self.request.unregisterProducer()
			self.request.finish()
			self.request = None

	def pauseProducing(self):
		pass

	def stopProducing(self):
		self.fp.close()
		self.request = None

	def render(self, request):
		path = self.path
		pmt = self.pmt
		pids = self.pids
		self.request = request

		fsize = size = os.path.getsize(path)

		request.setHeader('accept-ranges','bytes')

		request.setHeader('content-type', 'video/mpeg')

		try:
			self.fp = open(path)
		except IOError, e:
			import errno
			if e[0] == errno.EACCESS:
				return error.ForbiddenResource().render(request)
			else:
				raise

		if request.setLastModified(os.path.getmtime(path)) is http.CACHED:
			return ''

		trans = True
		# Commented out because it's totally broken. --jknight 11/29/04
		# XXX - fixed? jmg 2/17/06
		range = request.getHeader('range')

		tsize = size
		if range is not None:
			# This is a request for partial data...
			bytesrange = range.split('=')
			assert bytesrange[0] == 'bytes', \
			    "Syntactically invalid http range header!"
			start, end = bytesrange[1].split('-', 1)
			if start:
				start = int(start)
				self.fp.seek(start)
				if end and int(end) < size:
					end = int(end)
				else:
					end = size - 1
			else:
				lastbytes = int(end)
				if size < lastbytes:
					lastbytes = size
				start = size - lastbytes
				self.fp.seek(start)
				fsize = lastbytes
				end = size - 1
			start = start // 188 * 188
			self.fp.seek(start)
			size = (end + 1) // 188 * 188
			fsize = end - int(start) + 1
			# start is the byte offset to begin, and end is the
			# byte offset to end..  fsize is size to send, tsize
			# is the real size of the file, and size is the byte
			# position to stop sending.

			if fsize <= 0:
				request.setResponseCode(http.REQUESTED_RANGE_NOT_SATISFIABLE
		)
				fsize = tsize
				trans = False
			else:
				request.setResponseCode(http.PARTIAL_CONTENT)
				request.setHeader('content-range',"bytes %s-%s/%s " % (
				str(start), str(end), str(tsize)))

		request.setHeader('content-length', str(fsize))

		if request.method == 'HEAD' or trans is False:
			request.method = 'HEAD'
			return ''

		self.size = tsize
		self.written = 0
		request.registerProducer(self, 0)

		return server.NOT_DONE_YET

class MPEGTSResource(resource.Resource):
	isLeaf = True

	def __init__(self, *args):
		resource.Resource.__init__(self)

		self.args = args

	def render(self, request):
		request.setHeader('content-type', 'video/mpeg')

		# return data
		return DynamTSTransfer(*self.args).render(request)

class MPEGTS(FSObject, VideoItem):
	def __init__(self, *args, **kwargs):
		self.path = path = kwargs['path']
		del kwargs['path']
		self.tvct = tvct = kwargs['tvct']
		del kwargs['tvct']

		#log.msg('tvct w/ keys:', tvct, tvct.keys())

		kwargs['content'] = MPEGTSResource(path, tvct['PMTpid'],
		    *sum(mpegts.getaudiovideopids(tvct['PMT']), []))
		VideoItem.__init__(self, *args, **kwargs)
		FSObject.__init__(self, path)

		self.url = '%s/%s' % (self.cd.urlbase, self.id)
		self.res = Resource(self.url, 'http-get:*:video/mpeg:*')

	def doUpdate(self):
		pass

class MultiMPEGTS(FSObject, StorageFolder):
	def __init__(self, *args, **kwargs):
		path = kwargs['path']
		del kwargs['path']

		StorageFolder.__init__(self, *args, **kwargs)
		FSObject.__init__(self, path)

		# mapping from path to objectID
		self.pathObjmap = {}

	def doUpdate(self):
		f = mpegts.TSPStream(_LimitedFile(self.FSpath,
		    size= 2*1024*1024))
		self.tvct = mpegts.GetTVCT(f)

		doupdate = False
		origchildren, toindex = _gennameindexes(self.tvct['channels'])
		#log.msg('MultiMPEGTS doUpdate: tvct: %s' % self.tvct)
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
			if toindex[i]['prog_num'] == 0:
				log.msg('bogus:', toindex[i])
				continue

			#log.msg('real tvct:', toindex[i], toindex.keys(),
			#    self.tvct)
			self.pathObjmap[i] = self.cd.addItem(self.id, MPEGTS,
			    i, path = self.FSpath, tvct = toindex[i])
			doupdate = True

		if doupdate:
			StorageFolder.doUpdate(self)

def findtsstream(fp, pktsz=188):
	d = fp.read(200*pktsz)
	i = 5
	pos = 0
	while i and pos < len(d) and pos != -1:
		if d[pos] == 'G':
			i -= 1
			pos += pktsz
		else:
			i = 5
			pos = d.find('G', pos + 1)

	if i or pos == -1:
		return False

	return True

def detectmpegts(path, fobj):
	if not findtsstream(fobj):
		return None, None

	f = mpegts.TSPStream(_LimitedFile(path, size= 2*1024*1024))
	tvct = mpegts.GetTVCT(f)

	if len(tvct['channels']) == 1:
		#return None, None
		# We might reenable this once we have pid filtering working
		# fast enough.
		return MPEGTS, { 'path': path, 'tvct': tvct['channels'][0] }
	elif len(tvct['channels']) > 1:
		#log.msg('MultiMPEGTS: path: %s' % path)
		return MultiMPEGTS, { 'path': path }

	return None, None

registerklassfun(detectmpegts)
