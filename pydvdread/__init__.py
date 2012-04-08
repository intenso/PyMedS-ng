#!/usr/bin/env python

from cdvdread import *
import itertools
import sys

class DVDReadError(Exception):
	pass

__all__ = [	'DVD',
	]

dvd_domain_valid = [ DVD_READ_INFO_FILE, DVD_READ_INFO_BACKUP_FILE,
		DVD_READ_MENU_VOBS, DVD_READ_TITLE_VOBS, ]
dvd_domain_valid_names = [ 'DVD_READ_INFO_FILE', 'DVD_READ_INFO_BACKUP_FILE',
		'DVD_READ_MENU_VOBS', 'DVD_READ_TITLE_VOBS', ]
try:
	dvd_domain_valid = set(dvd_domain_valid)
except NameError:
	pass

# Make sure the library matches what we are compiled for
assert DVDREAD_VERSION == DVDVersion()

DVDInit()

def attribreprlist(obj, attrs):
	return map(lambda x, y = obj: '%s: %s' % (x, repr(getattr(y, x))), itertools.ifilter(lambda x, y = obj: hasattr(y, x), attrs))

def bcdtoint(bcd):
        base = 1
        ret = 0
        while bcd:
                assert bcd % 16 < 10, 'invalid bcd digit in: %#x' % bcd
                ret += bcd % 16 * base
                base *= 10
                bcd /= 16
        return ret

class DVDTime:
	'''Should be able to perform math, though until I get the frame rate bit info, I really can't do anything about it.'''
	__slots__ = [ '_hour', '_minute', '_second', '_frame_u', '_rate', '_seconds', '_frames' ]
	hour = property(lambda x: x._hour)
	minute = property(lambda x: x._minute)
	second = property(lambda x: x._second)
	frame = property(lambda x: x._frame)
	rate = property(lambda x: x._rate)
	ratestr = property(lambda x: x._ratestr)
	seconds = property(lambda x: x._seconds)
	frames = property(lambda x: x._frames)

	def __init__(self, dt):
		'''Take a dvd time object that has the attributes hour, minute, second and frame_u (bits 6-7 are frame rate, bits 0-5 is frame.'''
		self._hour = bcdtoint(dt.hour)
		self._minute = bcdtoint(dt.minute)
		self._second = bcdtoint(dt.second)
		self._frame = bcdtoint(dt.frame_u & 0x3f)
		fr = (dt.frame_u & 0xc0) >> 6
		assert fr in (1, 3), 'Unknown frame rate: %d' % fr
		self._rate = [-1, 25.0, -1, 29.97 ][fr]
		self._ratestr = [-1, '25.0', -1, '29.97' ][fr]
		self._seconds = (self._hour * 60 + self._minute) * 60 + self._second + self._frame / self._rate
		self._frames = self._seconds * self._rate

	def __int__(self):
		return int(self._seconds)

	def __float__(self):
		return self._seconds

	def __repr__(self):
		return '%s@%s' % (str(self), self.ratestr)

	def hhmmss(self):
		return '%02d:%02d:%02d' % (self._hour, self._minute, self._second)

	def __str__(self):
		return '%02d:%02d:%02d.%02d' % (self._hour, self._minute, self._second, self._frame)

class audio_attr:
	pos = property(lambda x: x._pos)
	lang = property(lambda x: x._lang)
	audio_format = property(lambda x: x._audio_format)
	lang_type = property(lambda x: x._lang_type)
	application_mode = property(lambda x: x._application_mode)
	quantization = property(lambda x: x._quantization)
	sample_frequency = property(lambda x: x._sample_frequency)
	channels = property(lambda x: x._channels)

	def __init__(self, audioattr, pos):
		self._pos = pos
		lc = audioattr.lang_code
		self._lang = chr(lc>>8) + chr(lc & 255)
		self._audio_format = audioattr.audio_format
		self._lang_type = audioattr.lang_type
		self._application_mode = audioattr.application_mode
		self._quantization = audioattr.quantization
		self._sample_frequency = audioattr.sample_frequency
		self._channels = audioattr.channels

	def __repr__(self):
		v = [ 'pos', 'lang', 'sample_frequency', 'channels' ]
		return '<audio_attr: %s>' % ', '.join(attribreprlist(self, v))

class IFO:
	ifodata = None		# __del__

	# VMGI
	vmgi_mat = property(lambda x: x.ifodata.vmgi_mat)
	tt_srpt = property(lambda x: x.ifodata.tt_srpt)
	first_play_pgc = property(lambda x: x.ifodata.first_play_pgc)
	ptl_mait = property(lambda x: x.ifodata.ptl_mait)
	vts_atrt = property(lambda x: x.ifodata.vts_atrt)
	txtdt_mgi = property(lambda x: x.ifodata.txtdt_mgi)

	# Common
	pgci_ut = property(lambda x: x.ifodata.pgci_ut)
	menu_c_adt = property(lambda x: x.ifodata.menu_c_adt)
	menu_vobu_admap = property(lambda x: x.ifodata.menu_vobu_admap)

	# VTSI
	vtsi_mat = property(lambda x: x.ifodata.vtsi_mat)
	vts_ptt_srpt = property(lambda x: x.ifodata.vts_ptt_srpt)
	vts_pgcit = property(lambda x: x.ifodata.vts_pgcit)
	vts_tmapt = property(lambda x: x.ifodata.vts_tmapt)
	vts_c_adt = property(lambda x: x.ifodata.vts_c_adt)
	vts_vobu_admap = property(lambda x: x.ifodata.vts_vobu_admap)

	def __init__(self, dvd, i):
		self.ifodata = ifoOpen(dvd.dvdreader, i)
		self.dvd = dvd
		self.audio = {}

		if i:
			# we are a VTS, populate some data
			for i in xrange(self.vtsi_mat.nr_of_vts_audio_streams):
				j = audio_attr(audio_attr_getitem(
				    self.vtsi_mat.vts_audio_attr, i), i)
				self.audio[j.lang] = j

	def __del__(self):
		if self.ifodata:
			ifoClose(self.ifodata)
		self.ifodata = None

class DVDProgram:
	'''This represents a chapter in a title.'''

	time = property(lambda x: x._time)
	size = property(lambda x: x._size)

	def __init__(self, dvdtitle, cpb):
		self.dvdtitle = dvdtitle
		self.cpb = cpb
		self._time = DVDTime(self.cpb.playback_time)
		self._size = (self.cpb.last_sector - self.cpb.first_sector +
		    1)  * self.dvdtitle.dvd.blocksize

	def blockiter(self, blkcnt = 16):
		# last isn't inclusive
		last = self.cpb.last_sector + 1
		for i in xrange(self.cpb.first_sector, last, blkcnt):
			yield self.dvdtitle.vob.pread(min(blkcnt, last - i), i)

	def __iter__(self):
		blklen = self.dvdtitle.dvd.blocksize
		for i in self.blockiter():
			for j in xrange(0, len(i), blklen):
				yield i[j:j + blklen]

	def __repr__(self):
		return '<DVDProgram: Time: %s>' % \
		    DVDTime(self.cpb.playback_time)

class DVDFile:
	dvdfile = None		# __del__

	def __init__(self, dvd, vts, dom):
		assert dom in dvd_domain_valid, 'Must be one of: %s' % `dvd_domain_valid_names`

		self.dvdfile = DVDOpenFile(dvd.dvdreader, vts, dom)
		if self.dvdfile is None:
			raise ValueError, 'DVD file (%d, %d) does not exist' % (vts, dom)
		self.vts = vts
		self.dom = dom
		self.dvd = dvd

	def __del__(self):
		if self.dvdfile:
			DVDCloseFile(self.dvdfile)
		self.dvdfile = None

	def pread(self, nblocks, blkoff):
		assert self.dom in (DVD_READ_MENU_VOBS, DVD_READ_TITLE_VOBS), \
		    'Must be of type DVD_READ_MENU_VOBS or DVD_READ_TITLE_VOBS.'

		buf = malloc_void(nblocks * self.dvd.blocksize)
		assert buf, 'buf allocation failed'
		try:
			b = DVDReadBlocks(self.dvdfile, blkoff, nblocks,
			    voidptr_to_ucharptr(buf))
			ret = cdata(buf, b * self.dvd.blocksize)
			return ret
		finally:
			free_void(buf)

	def seek(self, pos, whence = 0):
		assert whence == 0, 'Only SEEK_SET is supported'
		return DVDFileSeek(self.dvdfile, pos)

	def read(self, *args):
		if len(args) == 0:
			#read it all
			res = []
			data = 1
			while data:
				data = self.read(65536)
				res.append(data)
			return ''.join(res)

		assert len(args) == 1, 'Only takes one argument: count'
		buf = malloc_void(*args)
		assert buf, 'buf allocation failed'
		try:
			b = DVDReadBytes(self.dvdfile, buf, *args)
			ret = cdata(buf, b)
			return ret
		finally:
			free_void(buf)

	def __len__(self):
		return DVDFileSize(self.dvdfile) * self.dvd.blocksize

class DVDTitle:
	'''This is a title.'''

	time = property(lambda x: x._time)
	audio = property(lambda x: x.vts.audio)

	def selectaudio(self, lang):
		if isinstance(lang, basestring):
			lang = [ lang ]

		for l in lang:
			try:
				return self.audio[l]
			except KeyError:
				pass

		for l in self.lang:
			if l.pos == 0:
				return l

	def __init__(self, dvd, vts, ttu):
		'''dvd is of class DVD, vts is the vts number one based, and ttu is the sub-vts one based.'''

		self.dvd = dvd
		self.vts = dvd.getifo(vts)
		assert ttu > 0 and ttu <= self.vts.vts_pgcit.nr_of_pgci_srp
		self.pgci = pgci_srp_getitem(self.vts.vts_pgcit.pgci_srp, ttu - 1).pgc
		self.ttu = ttu
		self.vob = DVDFile(dvd, vts, DVD_READ_TITLE_VOBS)
		self._time = DVDTime(self.pgci.playback_time)

	def __len__(self):
		return self.pgci.nr_of_programs

	def __getitem__(self, key):
		if key < 0 or key >= len(self):
			raise IndexError
		assert key < self.pgci.nr_of_cells, \
		    'key cannot be mapped from program to cell(%d)' % \
		    (self.pgci.nr_of_programs, self.pgci.nr_of_cells)
		# cell is stored starting at 1, adjust to 0
		cell = uchar_getitem(self.pgci.program_map, key) - 1
		cell_playback = cell_playback_getitem(self.pgci.cell_playback,
		    cell)
		return DVDProgram(self, cell_playback)

	def __repr__(self):
		return '<DVDTitle: Chapters: %d, Time: %s>' % (len(self), self.time)

	def data(self):
		'''Returns the data in blocks for the title.'''
		for i in self:
			for j in i:
				yield j

class discid:
	__slots__ = [ '_discid' ]
	discid = property(lambda x: x._discid)

	def __init__(self, discid):
		self._discid = discid

	def __str__(self):
		return ''.join(map(lambda x: '%02x' % ord(x), self.discid))

	def __repr__(self):
		return '<DVD ID: %s>' % ''.join(map(lambda x: '%02x' % ord(x), self.discid))

class DVD:
	'''Children must keep a reference to this object so that we don't close
	the dvd before all the files are closed.  This does mean children will
	need to know the insides of this class, but that is fine since it's all
	internal to this implementation anyways.'''

	dvdreader = None	# __del__

	blocksize = property(lambda x: x._blocksize)
	volid = property(lambda x: x._volid)
	volsetid = property(lambda x: x._volsetid)
	cache = property(lambda x: DVDUDFCacheLevel(x.dvdreader, -1), lambda x, y: DVDUDFCacheLevel(x.dvdreader, bool(y)))

	def __init__(self, path):
		self.dvdreader = DVDOpen(path)
		if self.dvdreader is None:
			raise ValueError, 'path is not a DVD'

		self._discidval = None
		# XXX - this may need to be dynamicly probed
		self._blocksize = 2048

		# pull in the volid and volsetid
		r, volid, volsetid = DVDUDFVolumeInfo(self.dvdreader, 32, 128)
		if r != 0:
			self._volid = volid[:volid.index('\x00')].decode('latin-1')
			self._volsetid = volsetid
		else:
			# Fall back to ISO, shouldn't happen as all
			# DVD's are UDF
			r, volid, voldsetid = DVDISOVolumeInfo(self.dvdreader, 33, 128)
			assert r == 0
			# Techinically more restrictive [A-Z0-9_]
			self._volid = volid[:volid.index('\x00')].decode('ascii')
			self._volsetid = volsetid

		self.vmg = self.getifo(0)
		self._len = self.vmg.tt_srpt.nr_of_srpts

	def __del__(self):
		if self.dvdreader:
			DVDClose(self.dvdreader)
		self.dvdreader = None

	def getifo(self, i):
		return IFO(self, i)

	def _discid(self):
		if self._discidval is not None:
			return self._discidval

		buf = malloc_void(16)
		assert buf, 'buf allocation failed'
		try:
			r = DVDDiscID(self.dvdreader, voidptr_to_ucharptr(buf))
			if r == -1:
				raise DVDReadError, "failed to compute disc id"
			self._discidval = discid(cdata(buf, 16))
			return self._discidval
		finally:
			free_void(buf)
	discid = property(_discid)

	def __len__(self):
		return self._len

	def __getitem__(self, key):
		if key < 0 or key >= len(self):
			raise IndexError
		title = title_info_getitem(self.vmg.tt_srpt.title, key)

		return DVDTitle(self, title.title_set_nr, title.vts_ttn)

	def __repr__(self):
		return '<DVD: %s, ID: %s, Titles: %s>' % (self._volid, self.discid, len(self))
