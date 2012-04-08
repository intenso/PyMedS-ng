#!/usr/bin/env python
#
#  Copyright 2006-2007 John-Mark Gurney.
#  All rights reserved.
# 
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions
#  are met:
#  1. Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
#  2. Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
# 
#  THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
#  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
#  OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
#  HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
#  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
#  OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.
# 
# 	$Id$
#

import atschuff
import itertools
import os
import sets
import struct
import time
import traceback

TSSYNC = '\x47'
TSPKTLEN = 188
READBLK = 1024

def attribreprlist(obj, attrs):
	return map(lambda x, y = obj: '%s: %s' % (x, repr(getattr(y, x))),
	    itertools.ifilter(lambda x, y = obj: hasattr(y, x), attrs))

class UnReadSTR:
	def __init__(self, s):
		self.s = s
		self.pos = 0

		self._buf = []
		self._buftot = 0

	def __nonzero__(self):
		return self._buftot or self.pos < len(self.s)

	def tell(self):
		return self.pos - self._buftot

	def unread(self, buf):
		self._buf.append(buf)
		self._buftot += len(buf)

	def peek(self, size):
		r = self.read(size)
		self.unread(r)
		return r

	def sread(self, cnt = -1):
		oldpos = self.pos
		if cnt == -1:
			self.pos = len(self.s)
			return self.s[oldpos:]

		self.pos += cnt
		if self.pos > len(self.s):
			self.pos = len(self.s)

		return self.s[oldpos:self.pos]

	def read(self, size = None):
		if size is None and self._buf:
			ret = self._buf.pop()
			self._buftot -= len(ret)
		elif size is None:
			ret = self.sread()
		else:
			ret = []
			while size and self._buftot:
				ret.append(self._buf[-1][:size])
				l = len(ret[-1])
				if size > l:
					assert len(self._buf[-1]) == l
					self._buf.pop()
				else:
					self._buf[-1] = self._buf[-1][size:]
				self._buftot -= l
				size -= l 

			if size:
				ret.append(self.sread(size))

			ret = ''.join(ret)

		return ret

def DVDAudioFilter(itr, subchan):
	'''subchan should be in the range [0x80, 0x8f], this will filter out all other subchannels in that range that do not match subchan.'''
	assert subchan >= 0x80 and subchan <= 0x8f

	def checksubchan(pes, sc = subchan):
		if pes.stream_id != 0xbd:
			return False

		subchan = ord(pes.data[0])
		if subchan == sc or subchan < 0x80 or subchan > 0x8f:
			return True

		return False

	# XXX - I probably should mess w/ muxr so SCR is stable.
	for i in itr:
		j = Pack(UnReadSTR(i))

		if filter(checksubchan, j):
			yield i

def findcodes(buf):
	ret = []

	i = 0
	l = len(buf)
	while i < l:
		j = buf.find('\x00\x00\x01', i)
		if j == -1 or (i + 4) >= l:
			break
		ret.append((j, buf[j + 3]))
		i = j + 4
	return ret

class UnRead(file):
	def __init__(self, *args, **kwargs):
		super(UnRead, self).__init__(*args, **kwargs)
		self._buf = []
		self._buftot = 0

	def unread(self, buf):
		self._buf.append(buf)
		self._buftot += len(buf)

	def peek(self, size):
		r = self.read(size)
		self.unread(r)
		return r

	def read(self, size = None):
		if size is None and self._buf:
			ret = self._buf.pop()
			self._buftot -= len(ret)
		elif size is None:
			ret = super(UnRead, self).read()
		else:
			ret = []
			while size and self._buftot:
				ret.append(self._buf[-1][:size])
				l = len(ret[-1])
				if size > l:
					assert len(self._buf[-1]) == l
					self._buf.pop()
				else:
					self._buf[-1] = self._buf[-1][size:]
				self._buftot -= l
				size -= l

			if size:
				ret.append(super(UnRead, self).read(size))

			ret = ''.join(ret)

		return ret

def read_timestamp(buf):
	assert len(buf) == 5
	assert (ord(buf[0]) & 0x1) == 1
	assert (ord(buf[2]) & 0x1) == 1
	assert (ord(buf[4]) & 0x1) == 1
	return (long(ord(buf[0]) & 0xe) << 29) | (ord(buf[1]) << 21) | \
	    ((ord(buf[2]) & 0xfe) << 14) | (ord(buf[3]) << 7) | \
	    ((ord(buf[4]) & 0xfe) >> 1)

def read_escr(buf):
        assert len(buf) == 6
        assert (ord(buf[0]) & 0x4) == 0x4 and (ord(buf[2]) & 0x4) == 0x4
        assert (ord(buf[4]) & 0x4) == 0x4 and (ord(buf[5]) & 0x1) == 0x1

        base = (long(ord(buf[0]) & 0x38) << 27) | ((ord(buf[0]) & 0x3) << 28) |\
	    (ord(buf[1]) << 20) | ((ord(buf[2]) & 0xf8) << 15) | \
	    ((ord(buf[2]) & 0x3) << 13) | (ord(buf[3]) << 5) | \
	    ((ord(buf[4]) & 0xf8) >> 3)
        extension = ((ord(buf[4]) & 0x3) << 7) | (ord(buf[5]) >> 1)

        return (base, extension)

class MPEGWriter:
	END_CODE = '\xb9'

	def __init__(self, f):
		self.f = f
		self.SCR = (0, 0)

	def write_header(self, header):
		self.f.write('\x00\x00\x01' + header)

	def close(self):
		self.write_header(self.END_CODE)

	def __del__(self):
		self.close()

class PES:
	PROGRAM_STREAM_MAP_ID = 0xbc
	PRIVATE_1_ID = 0xbd
	PADDING_ID = 0xbe
	PRIVATE_2_ID = 0xbf
	IS_AUDIO_ID = lambda x: (x & 0xe0) == 0xc0
	IS_VIDEO_ID = lambda x: (x & 0xf0) == 0xe0
	ECM_ID = 0xf0
	EMM_ID = 0xf1
	DSMCC_ID = 0xf2
	H2221_TYPE_E_ID = 0xf8
	PROGRAM_STREAM_DIRECTORY_ID = 0xff

	def __init__(self, buf):
		# Pull out an IndexError first
		assert buf[0] == '\x00' and buf[:3] == '\x00\x00\x01'
		self.stream_id = ord(buf[3])
		self.length = (ord(buf[4]) << 8) | ord(buf[5])
		if self.length == 0:
			self.length = len(buf)
		else:
			self.length += 6
		if len(buf) < self.length:
			raise IndexError, 'not enough data'

		if self.stream_id == self.PADDING_ID:
			# Validate padding?
			#self.length -= 6
			pass
		elif self.stream_id in (self.PROGRAM_STREAM_MAP_ID,
		    self.PRIVATE_2_ID, self.ECM_ID, self.EMM_ID, self.DSMCC_ID,
		    self.H2221_TYPE_E_ID, self.PROGRAM_STREAM_DIRECTORY_ID, ):
			self.data = buf[6:self.length]
		else:
			i = 6
			assert (ord(buf[i]) & 0xc0) == 0x80
			self.scrambling_control = (ord(buf[i]) & 0x30) >> 4
			self.priority = bool(ord(buf[i]) & 0x8)
			self.data_alignment = bool(ord(buf[i]) & 0x4)
			self.copyright = bool(ord(buf[i]) & 0x2)
			self.originalcopy = bool(ord(buf[i]) & 0x1)
			i +=1
			ptsdts_flag = (ord(buf[i]) & 0xc0) >> 6
			escr_flag = bool(ord(buf[i]) & 0x20)
			es_rate_flag = bool(ord(buf[i]) & 0x10)
			dsm_trick_mode_flag = bool(ord(buf[i]) & 0x8)
			additional_copy_info_flag = bool(ord(buf[i]) & 0x4)
			crc_flag = bool(ord(buf[i]) & 0x2)
			extension_flag = bool(ord(buf[i]) & 0x1)
			header_end = i + 2 + ord(buf[i + 1])
			i += 2
			if ptsdts_flag == 0x2:
				assert (ord(buf[i]) & 0xf0) == 0x20
				self.PTS = read_timestamp(buf[i:i + 5])
				i += 5
			elif ptsdts_flag == 0x3:
				assert (ord(buf[i]) & 0xf0) == 0x30
				self.PTS = read_timestamp(buf[i:i + 5])
				i += 5
				assert (ord(buf[i]) & 0xf0) == 0x10
				self.DTS = read_timestamp(buf[i:i + 5])
				i += 5
			elif ptsdts_flag == 0x1:
				raise ValueError, \
				    "ptsdts flag forbidden: %d" % ptsdts_flag
			if escr_flag:
				self.ESCR = read_escr(buf[i:i + 6])
				i += 6
			if es_rate_flag:
				assert (ord(buf[i]) & 0x80) == 0x80
				assert (ord(buf[i + 2]) & 0x01) == 0x01
				self.ES_rate = ((ord(buf[i]) & 0x7f) << 15) | \
				    (ord(buf[i + 1]) << 7) | \
				    (ord(buf[i + 2]) >> 1)
				i += 3
			if dsm_trick_mode_flag:
				self.trick_mode_control = ord(buf[i]) >> 5
				self.trick_mode_bits = ord(buf[i]) & 0x1f
				i += 1
			if additional_copy_info_flag:
				assert (ord(buf[i]) & 0x80) == 0x80
				self.additional_copy_info = ord(buf[i]) & 0x7f
				i += 1
			if crc_flag:
				self.prev_crc = (ord(buf[i]) << 8) | \
				    ord(buf[i + 1])
				i += 2
			if extension_flag:
				private_data_flag = bool(ord(buf[i]) & 0x80)
				pack_header_field_flag = bool(ord(buf[i]) & \
				    0x40)
				program_packet_sequence_counter_flag = \
				    bool(ord(buf[i]) & 0x20)
				pstd_buffer_flag = bool(ord(buf[i]) & 0x10)
				pes_extension_flag_2 = bool(ord(buf[i]) & 0x01)
				i += 1
				if private_data_flag:
					self.private_data = buf[i:i + 16]
					i += 16
				if pack_header_field_flag:
					pack_field_length = ord(buf[i])
					self.pack_header = buf[i + 1:i + 1 +
					    pack_field_length]
					i += 1 + pack_field_length
				if program_packet_sequence_counter_flag:
					assert (ord(buf[i]) & 0x80) == 0x80
					self.sequence_counter = \
					    ord(buf[i]) & 0x7f
					i += 1
					assert (ord(buf[i]) & 0x80) == 0x80
					self.mpeg1_mpeg2_ident = \
					    bool(ord(buf[i]) & 0x4)
					self.original_stuff_len = \
					    ord(buf[i]) & 0x3f
					i += 1
				if pstd_buffer_flag:
					assert (ord(buf[i]) & 0xc0) == 0x40
					self.pstd_buffer_scale = \
					    bool(ord(buf[i]) & 0x20)
					self.pstd_buffer_size = \
					    ((ord(buf[i]) & 0x1f) << 8) | \
					    ord(buf[i + 1])
					i += 2
				if pes_extension_flag_2:
					assert (ord(buf[i]) & 0x80) == 0x80
					extension_field_length = \
					    ord(buf[i]) & 0x7f
					self.extension_field = buf[i + 1:i + \
					    1 + extension_field_length]
					i += 1 + extension_field_length

			assert i <= header_end
			self.data = buf[header_end:self.length]

	def __repr__(self):
		# XXX - data length
		v = ( 'length', 'scrambling_control',
			'priority', 'data_alignment', 'copyright',
			'originalcopy', 'PTS', 'DTS', 'ESCR', 'ES_rate',
			'trick_mode_control', 'trick_mode_bits',
			'additional_copy_info', 'pack_header',
			'sequence_counter', 'mpeg1_mpeg2_ident',
			'original_stuff_len', 'pstd_buffer_scale',
			'pstd_buffer_size', 'extension_field', )
		return '<PES: stream_id: %#x, %s>' % (self.stream_id,
		    ', '.join(attribreprlist(self, v)), )

class Pack(list):
	def __init__(self, f = None, **keyw):
		super(Pack, self).__init__()
		if f is not None:
			d = f.read(14)
			assert d[:4] == '\x00\x00\x01\xba'
			assert (ord(d[4]) & 0xc0) == 0x40
			self.SCR = read_escr(d[4:10])
			assert ord(d[12]) & 0x3 == 0x3
			m = map(ord, d[10:13])
			self.muxr = (m[0] << 14) | (m[1] << 6) | (m[2] >> 2)
			self.stuff_len = ord(d[13]) & 0x7
			f.read(self.stuff_len)

			# system header
			d = f.peek(6)
			if d[:4] == '\x00\x00\x01\xbb':
				f.read(6)
				hlen = (ord(d[4]) << 8) | ord(d[5])
				header = f.read(hlen)
				oh = map(ord, header)
				assert (oh[0] & 0x80) == 0x80 and \
				    (oh[2] & 0x1) == 0x1
				self.rate_bound = ((oh[0] & 0x7f) << 15) | \
				    (oh[1] << 7) | (oh[2] >> 1)
				self.audio_bound = oh[3] >> 2
				self.fixed = bool(oh[3] & 0x2)
				self.CSPS = bool(oh[3] & 0x1)
				self.system_audio_lock = bool(oh[4] & 0x80)
				self.system_video_lock = bool(oh[4] & 0x40)
				assert (oh[4] & 0x20) == 0x20
				self.video_bound = oh[4] & 0x1f
				self.packet_rate_restriction = \
				    bool(oh[5] & 0x80)
				d = f.peek(1)
				self.streams = {}
				while ord(d) & 0x80:
					d = map(ord, f.read(3))
					assert (d[1] & 0xc0) == 0xc0
					scaleflag = bool(d[1] & 0x20)
					self.streams[d[0]] = (((d[1] & 0x1f) <<
					    8) | d[2]) * (128, 1024)[scaleflag]
					d = f.peek(1)
			# PES packets
			d = f.peek(3)
			bytestoread = 2048
			while (f or d) and d == '\x00\x00\x01':
				try:
					d = f.read(bytestoread)
					self.append(PES(d))
					f.unread(d[self[-1].length:])
				except IndexError:
					f.unread(d)
					bytestoread <<= 2
				d = f.peek(4)
		else:
			self.SCR = keyw['SCR']
			self.muxr = keyw['muxr']	# in bps (converts to 50 bytes/sec)
			self.stuff_len = 0

	def __repr__(self):
		v = [ 'SCR', 'muxr', 'stuff_len',
			'rate_bound', 'audio_bound', 'fixed', 'CSPS',
			'system_audio_lock', 'system_video_lock',
			'video_bound', 'packet_rate_restriction',
			'streams',
		]
		return '<Pack: %s %s>' % (', '.join(attribreprlist(self, v)),
		    list.__repr__(self))

	def __str__(self):
		buf = []
		buf.append('\x00\x00\x01\xba')
		clock = (1l << 46) | (((self.SCR[0] >> 30) & 0x7) << 43) | \
		    (1l << 42) | (((self.SCR[0] >> 15) & 0x7ffff) << 27) | \
		    (1 << 26) | ((self.SCR[0] & 0x7fff) << 11) | (1 << 10) | \
		    ((self.SCR[1] << 1) & 0x3fe) | 0x1
		for i in range(6):
			buf.append(chr(clock >> ((5 - i) * 8) & 0xff))
		muxr = self.muxr / 50 / 8
		buf.append(chr((muxr >> 14) & 0xff))
		buf.append(chr((muxr >> 6) & 0xff))
		buf.append(chr(((muxr << 2) & 0xfc) | 0x3))
		buf.append(chr(0xf8 | (self.stuff_len & 7)))
		buf.append('\xff' * self.stuff_len)
		buf.extend(map(str, self))
		return ''.join(buf)

# These are strings due to the floating point numbers
frame_rate_code = {
	0x0: 'forbidden',	0x1: '23.976',	0x2: '24',	0x3: '25',
	0x4: '29.97',		0x5: '30',	0x6: '50',	0x7: '59.95',
	0x8: '60',		0x9: 'reserved',		0xa: 'reserved',
	0xb: 'reserved',	0xc: 'reserved',		0xd: 'reserved',
	0xe: 'reserved',	0xf: 'reserved',
}
chroma_format = {
	0x0: 'reserved',	0x1: '4:2:0',	0x2: '4:2:2',	0x3: '4:4:4',
}

class BitRate(int):
	def __init__(self, bitrate):
		super(BitRate, self).__init__(bitrate)

	def __str__(self):
		return repr(self)

	def __repr__(self):
		return '%dbps' % self

def max_bitrate_descriptor(b):
	assert len(b) == 3
	return BitRate((((ord(b[0]) & 0x3f) << 16) |
	    ((ord(b[1]) & 0xff) << 8) | (ord(b[0]) & 0xff)) * 50 * 8)

class ISO639LangDescriptor(list):
	atypedict = {
		0:	'undefined',
		1:	'clean effects',
		2:	'hearing impaired',
		3:	'visual impaired commentary',
	}

	def __init__(self, b):
		assert len(b) % 4 == 0

		for i in range(len(b) / 4):
			lang = unicode(b[i * 4:i * 4 + 3], 'iso8859-1')
			atype = self.atypedict[ord(b[i * 4 + 3])]
			self.append((lang, atype))

class VStreamDescriptor:
	def __init__(self, b):
		if not b:
			self.mpeg2 = None
			self.multiple_frame_rate = None
			self.frame_rate_code = None
			self.constrained_parameter = None
			self.still_picture = None
			return

		fb = ord(b[0])
		# XXX - libdvbpsi says no not for mpeg2 flag, but my data
		# seems to say otherwise.
		self.mpeg2 = not bool(fb & 0x04)
		assert (not self.mpeg2 and len(b) == 1) or (self.mpeg2 and
		    len(b) == 3)
		self.multiple_frame_rate = bool(fb & 0x80)
		self.frame_rate_code = frame_rate_code[(fb & 0x78) >> 3]
		self.constrained_parameter = bool(fb & 0x02)
		self.still_picture = bool(fb & 0x01)
		if self.mpeg2:
			self.profile_level_indication = ord(b[1])
			tb = ord(b[2])
			self.chroma_format = chroma_format[(tb & 0xc0) >> 6]
			self.frame_rate_extension = bool(tb & 0x20)

	def __repr__(self):
		v = ['mpeg2', 'multiple_frame_rate', 'frame_rate_code',
		    'constrained_parameter', 'still_picture',
		    'profile_level_indication', 'chroma_format',
		    'frame_rate_extension', ]

		return '<VStream: %s>' % (', '.join(attribreprlist(self, v)), )

class AC3Descriptor:
	src_dict = { 0: '48k', 1: '44.1k', 2: '32k', 3: None, 4: '48k or 44.1k',
		5: '48k or 32k', 6: '44.1k or 32k', 7: '48k or 44.1k or 32k' }

	brc_dict = { 0: 32, 1: 40, 2: 48, 3: 56, 4: 64, 5: 80, 6: 96, 7: 112,
		8: 128, 9: 160, 10: 192, 11: 224, 12: 256, 13: 320, 14: 384,
		15: 448, 16: 512, 17: 576, 18: 640, }

	sm_dict = { 0: 'Not indicated', 1: 'NOT Dolby surround encoded',
		2: 'Dolby surround encoded', 3: 'Reserved', }

	bsmod_dict = { 0: 'main: complete', 1: 'main: music and effects',
		2: 'associated: visually imparied',
		3: 'associated: hearing imparied', 4: 'associated: dialogue',
		5: 'associated: commentary', 6: 'associated: emergency', }

	bs_mod = property(lambda x: x.bsmoddesc())
	num_channels = property(lambda x: x.numchan_dict[x.numchan])

	def bsmoddesc(self):
		if self.bsmod == 7:
			if (self.numchan & 0x8) and self.numchan == 1:
				return 'associated: voice over'
			else:
				return 'main: karaoke'
		else:
			return self.bsmod_dict[self.bsmod]

	numchan_dict = { 0: '1+1', 1: '1/0', 2: '2/0', 3: '3/0', 4: '2/1',
		5: '3/1', 6: '2/2', 7: '3/2', 8: '1', 9: '<=2', 10: '<=3',
		11: '<=4', 12: '<=5', 13: '<=6', 14: 'Reserved',
		15: 'Reserved', }

	def __init__(self, data):
		srcbsid = ord(data[0])
		self.sample_rate = self.src_dict[srcbsid >> 5]
		self.bsid = srcbsid & 0x1f
		brcsm = ord(data[1])
		self.br_exact = (brcsm & 0x80) == 0x80
		self.bitrate = self.brc_dict[(brcsm >> 2) & 0x1f]
		self.surround_mode = self.sm_dict[brcsm & 0x3]
		bsmodnumchanfullsvc = ord(data[2])
		self.bsmod = bsmodnumchanfullsvc >> 6
		numchan = (bsmodnumchanfullsvc >> 1) & 0xf
		self.numchan = numchan

		# KTVU only puts 3 bytes here
		if len(data) == 3:
			return

		i = 4
		# dropped langcod as not used per A/52a 3.4
		if numchan == 0:
			i += 1
		if self.bsmod < 2:
			self.mainid = ord(data[i]) >> 5
		else:
			self.asvcflags = ord(data[i])
		i += 1
		if i >= len(data):
			self.text = ''
			return

		textlangcode = ord(data[i])
		textlen = textlangcode >> 1
		i += 1
		txt = data[i:i + textlen]
		if textlangcode & 1:
			self.text = txt.decode('latin-1')
		else:
			assert NotImplementedError, \
			    'the following code is untested'
			self.text = ''.join(map(lambda x:
			    unichr(ord(x[0]) * 256 + ord(x[1])),
			    [txt[i:i+2] for i in range(0, len(txt), 2)]))
		
	def __repr__(self):
		v = ['sample_rate', 'bsid', 'br_exact', 'bitrate',
		    'surround_mode', 'bs_mod', 'num_channels', 'mainid',
		    'asvcflags', 'text', ]

		return '<AC3Descritor: %s>' % (', '.join(attribreprlist(self,
		    v)), )

class ContentAdvisory(list):
	def __init__(self, data):
		list.__init__(self)

		cnt = ord(data[0]) & 0x3f
		i = 1
		for j in xrange(cnt):
			region, dim = struct.unpack('>BB', data[i: i + 2])
			i += 2
			d = {}
			for j in xrange(dim):
				d[ord(data[i])] = ord(data[i + 1]) & 0xf
				i += 2
			desclen = ord(data[i])
			desc = MultiStringStruct(data[i + 1: i + 1 + desclen])
			self.append((region, d, desc))

	def __repr__(self):
		return '<ContentAdvisory: %s>' % list.__repr__(self)

class ServiceLocationDescriptor(list):
	tag = 0xa1
	sldel = '>BH3c'

	def __init__(self, data):
		step = struct.calcsize(self.sldel)
		assert ((len(data) - 3) % step) == 0

		list.__init__(self)

		self.pcr_pid, cnt = struct.unpack('>HB', data[:3])
		self.pcr_pid &= 0x1fff
		for i in range(cnt):
			type, pid, a, b, c = struct.unpack(self.sldel,
			    data[3 + i * step:3 + (i + 1) * step])
			pid &= 0x1fff
			lang = a + b + c
			if lang == '\x00' * 3:
				lang = None
			self.append({ 'type': type, 'pid': pid, 'lang': lang })

	def __repr__(self):
		return '<ServiceLocationDescriptor: pcr_pid: %d, %s>' % \
		    (self.pcr_pid, list.__repr__(self))

class MultiStringStruct(dict):
	'''Conforms to Section 6.10 of A/65b.'''

	def decode(self, comp, mode, data):
		assert (mode == 0 and comp in (1, 2)) or comp == 0, \
		    'mode: %#x, comp: %#x' % (mode, comp)
		if comp == 0:
			if mode == 0x3f:
				return data.decode('UTF-16-BE')
			elif mode == 0x3e:
				# http://www.unicode.org/reports/tr6/
				raise NotImplementedError, 'Unicode Technical Report #6, A Standard Compression Scheme for Unicode'

			# There are additional limitations
			assert mode < 0x34, 'Invalid mode: %#x' % mode

			return ''.join(map(lambda x, y = mode * 256:
			    unichr(ord(x) + y), data))

		assert (comp == 1 or comp == 2) and mode == 0xff, \
		    'Invalid comp: %#x, mode: %#x' % (comp, mode)

		if comp == 1:
			return atschuff.decode_title(data)
		else:
			return atschuff.decode_description(data)

	def __init__(self, data):
		cnt = ord(data[0])
		off = 1
		for i in range(cnt):
			lang = data[off:off + 3]
			nseg = ord(data[off + 3])
			segs = []
			self[lang] = segs
			off += 4
			for j in range(nseg):
				comp_type = ord(data[off])
				mode = ord(data[off + 1])
				nbytes = ord(data[off + 2])
				segs.append(self.decode(comp_type, mode,
				    data[off + 3: off + 3 + nbytes]))

class ComponentNameDescriptor(MultiStringStruct):
	def __repr__(self):
		return '<ComponentNameDescriptor: %s>' % \
		    MultiStringStruct.__repr__(self)

def FindMe(data):
	raise RuntimeError, 'found me'

Descriptors = {
	# 0-63 Are listed in ISO 13818-1 Table 2-40
	2:	VStreamDescriptor,		# ISO 13818-1 Section 2.6.2
	# 3:	Audio,				# ISO 13818-1 Section 2.6.3
	10:	ISO639LangDescriptor,		# ISO 13818-1 Section 2.6.18
	14:	max_bitrate_descriptor,		# ISO 13818-1 Section 2.6.26
	0x81:	AC3Descriptor,			# A/53d Section 5.7.3.1
	0x87:	ContentAdvisory,		# A/65b Section 6.9.4
	# 0xa0:	ExtendedChannelName,		# A/65b Section 6.9.5
	0xa1:	ServiceLocationDescriptor,	# A/65b Section 6.9.6
	# 0xa2:	TimeShiftedService,		# A/65b Section 6.9.7
	0xa3:	ComponentNameDescriptor,	# A/65b Section 6.9.8
	# 0xad:	undefined,			# A/53d Section 5.7.3.4
	0xb6:	FindMe,				# A/57a Section 7 (ContentId)
}

PIDs = {
	0x00:	('PAT', 'Program Association Table'),
	0x01:	('CAT', 'Conditional Access Table'),
	0x02:	('TSDT', 'Program Stream Descriptor Table'),
	0x10:	('NIT', 'Network Information Table'),
	0x11:	('BAT', 'Bouquet Association Table'),
	0x11:	('SDT', 'Service Descriptor Table'),
	0x12:	('EIT', 'Event Information Table'),
	0x13:	('RST', 'running Status Table'),
	0x14:	('TOT', 'Time Offset Table'),
}

def psip_calc_crc32(data, verbose = False, table = (
  0x00000000l, 0x04c11db7l, 0x09823b6el, 0x0d4326d9l,
  0x130476dcl, 0x17c56b6bl, 0x1a864db2l, 0x1e475005l,
  0x2608edb8l, 0x22c9f00fl, 0x2f8ad6d6l, 0x2b4bcb61l,
  0x350c9b64l, 0x31cd86d3l, 0x3c8ea00al, 0x384fbdbdl,
  0x4c11db70l, 0x48d0c6c7l, 0x4593e01el, 0x4152fda9l,
  0x5f15adacl, 0x5bd4b01bl, 0x569796c2l, 0x52568b75l,
  0x6a1936c8l, 0x6ed82b7fl, 0x639b0da6l, 0x675a1011l,
  0x791d4014l, 0x7ddc5da3l, 0x709f7b7al, 0x745e66cdl,
  0x9823b6e0l, 0x9ce2ab57l, 0x91a18d8el, 0x95609039l,
  0x8b27c03cl, 0x8fe6dd8bl, 0x82a5fb52l, 0x8664e6e5l,
  0xbe2b5b58l, 0xbaea46efl, 0xb7a96036l, 0xb3687d81l,
  0xad2f2d84l, 0xa9ee3033l, 0xa4ad16eal, 0xa06c0b5dl,
  0xd4326d90l, 0xd0f37027l, 0xddb056fel, 0xd9714b49l,
  0xc7361b4cl, 0xc3f706fbl, 0xceb42022l, 0xca753d95l,
  0xf23a8028l, 0xf6fb9d9fl, 0xfbb8bb46l, 0xff79a6f1l,
  0xe13ef6f4l, 0xe5ffeb43l, 0xe8bccd9al, 0xec7dd02dl,
  0x34867077l, 0x30476dc0l, 0x3d044b19l, 0x39c556ael,
  0x278206abl, 0x23431b1cl, 0x2e003dc5l, 0x2ac12072l,
  0x128e9dcfl, 0x164f8078l, 0x1b0ca6a1l, 0x1fcdbb16l,
  0x018aeb13l, 0x054bf6a4l, 0x0808d07dl, 0x0cc9cdcal,
  0x7897ab07l, 0x7c56b6b0l, 0x71159069l, 0x75d48ddel,
  0x6b93dddbl, 0x6f52c06cl, 0x6211e6b5l, 0x66d0fb02l,
  0x5e9f46bfl, 0x5a5e5b08l, 0x571d7dd1l, 0x53dc6066l,
  0x4d9b3063l, 0x495a2dd4l, 0x44190b0dl, 0x40d816bal,
  0xaca5c697l, 0xa864db20l, 0xa527fdf9l, 0xa1e6e04el,
  0xbfa1b04bl, 0xbb60adfcl, 0xb6238b25l, 0xb2e29692l,
  0x8aad2b2fl, 0x8e6c3698l, 0x832f1041l, 0x87ee0df6l,
  0x99a95df3l, 0x9d684044l, 0x902b669dl, 0x94ea7b2al,
  0xe0b41de7l, 0xe4750050l, 0xe9362689l, 0xedf73b3el,
  0xf3b06b3bl, 0xf771768cl, 0xfa325055l, 0xfef34de2l,
  0xc6bcf05fl, 0xc27dede8l, 0xcf3ecb31l, 0xcbffd686l,
  0xd5b88683l, 0xd1799b34l, 0xdc3abdedl, 0xd8fba05al,
  0x690ce0eel, 0x6dcdfd59l, 0x608edb80l, 0x644fc637l,
  0x7a089632l, 0x7ec98b85l, 0x738aad5cl, 0x774bb0ebl,
  0x4f040d56l, 0x4bc510e1l, 0x46863638l, 0x42472b8fl,
  0x5c007b8al, 0x58c1663dl, 0x558240e4l, 0x51435d53l,
  0x251d3b9el, 0x21dc2629l, 0x2c9f00f0l, 0x285e1d47l,
  0x36194d42l, 0x32d850f5l, 0x3f9b762cl, 0x3b5a6b9bl,
  0x0315d626l, 0x07d4cb91l, 0x0a97ed48l, 0x0e56f0ffl,
  0x1011a0fal, 0x14d0bd4dl, 0x19939b94l, 0x1d528623l,
  0xf12f560el, 0xf5ee4bb9l, 0xf8ad6d60l, 0xfc6c70d7l,
  0xe22b20d2l, 0xe6ea3d65l, 0xeba91bbcl, 0xef68060bl,
  0xd727bbb6l, 0xd3e6a601l, 0xdea580d8l, 0xda649d6fl,
  0xc423cd6al, 0xc0e2d0ddl, 0xcda1f604l, 0xc960ebb3l,
  0xbd3e8d7el, 0xb9ff90c9l, 0xb4bcb610l, 0xb07daba7l,
  0xae3afba2l, 0xaafbe615l, 0xa7b8c0ccl, 0xa379dd7bl,
  0x9b3660c6l, 0x9ff77d71l, 0x92b45ba8l, 0x9675461fl,
  0x8832161al, 0x8cf30badl, 0x81b02d74l, 0x857130c3l,
  0x5d8a9099l, 0x594b8d2el, 0x5408abf7l, 0x50c9b640l,
  0x4e8ee645l, 0x4a4ffbf2l, 0x470cdd2bl, 0x43cdc09cl,
  0x7b827d21l, 0x7f436096l, 0x7200464fl, 0x76c15bf8l,
  0x68860bfdl, 0x6c47164al, 0x61043093l, 0x65c52d24l,
  0x119b4be9l, 0x155a565el, 0x18197087l, 0x1cd86d30l,
  0x029f3d35l, 0x065e2082l, 0x0b1d065bl, 0x0fdc1becl,
  0x3793a651l, 0x3352bbe6l, 0x3e119d3fl, 0x3ad08088l,
  0x2497d08dl, 0x2056cd3al, 0x2d15ebe3l, 0x29d4f654l,
  0xc5a92679l, 0xc1683bcel, 0xcc2b1d17l, 0xc8ea00a0l,
  0xd6ad50a5l, 0xd26c4d12l, 0xdf2f6bcbl, 0xdbee767cl,
  0xe3a1cbc1l, 0xe760d676l, 0xea23f0afl, 0xeee2ed18l,
  0xf0a5bd1dl, 0xf464a0aal, 0xf9278673l, 0xfde69bc4l,
  0x89b8fd09l, 0x8d79e0bel, 0x803ac667l, 0x84fbdbd0l,
  0x9abc8bd5l, 0x9e7d9662l, 0x933eb0bbl, 0x97ffad0cl,
  0xafb010b1l, 0xab710d06l, 0xa6322bdfl, 0xa2f33668l,
  0xbcb4666dl, 0xb8757bdal, 0xb5365d03l, 0xb1f740b4l
)):
	'''Validate a PSIP CRC.  Include the CRC in the data.  The return value will be the valid data, or an exception will be raised if invalid.'''

	if verbose:
		i_crc = 0xffffffffl
		for i in data:
			i_crc = ((i_crc << 8) & 0xffffffffl) ^ table[(i_crc >>
			    24) ^ ord(i)]
			print hex(i_crc)
	else:
		i_crc = reduce(lambda x, y: ((x << 8) & 0xffffffffl) ^
		    table[(x >> 24) ^ ord(y)], data, 0xffffffffl)
	return i_crc

def psip_crc32(data):
	return psip_calc_crc32(data) == 0

def getdescriptors(tb):
	d = {}
	i = 0
	while i < len(tb):
		t = ord(tb[i])
		if d.has_key(t):
			l = ord(tb[i + 1])
			data = tb[i + 2: i + 2 + l]
			#print repr(d[t]), t, repr(data)
		#assert not d.has_key(t)
		l = ord(tb[i + 1])
		data = tb[i + 2: i + 2 + l]
		try:
			item = Descriptors[t](data)
		except KeyError:
			item = data

		try:
			d[t].append(item)
		except KeyError:
			d[t] = [ item ]

		i += 2 + l

	return d

class TSPSIPHandler(dict):
	'''This object is used to represent the tables that come in on a
specific PID.  Since there can be multiple tables on a specific PID
(ATSC's 0x1ffb), a dictionary of callable objects must be passed in,
and the key is the table number.'''

	def __init__(self, *t):
		super(TSPSIPHandler, self).__init__()
		self.update(*t)
		self.discontinuity = True
		self.complete = False
		self.last_continuity = None

		# config knobs
		self.current_only = True
		self.ignerror = False

	def next_continuity(self, nc):
		if self.last_continuity is None:
			return True

		return ((self.last_continuity + 1) % 16) != nc

	def get_table_id(self):
		if self.complete:
			return self._table_id

		return None

	table_id = property(get_table_id)

	def decode_section_header(self, payload, i):
		self._table_id = ord(payload[i])
		self.syntax = bool(ord(payload[i + 1]) & 0x80)
		self.private = bool(ord(payload[i + 1]) & 0x40)
		self.sect_len = (((ord(payload[i + 1]) & 0xf) << 8) | \
		    ord(payload[i + 2])) + 3
		self.stored_sects = [ payload[i:] ]
		#print 'bar', i, repr(payload)
		self.stored_len = len(self.stored_sects[0])
		self.discontinuity = False

	def __call__(self, p):
		'''Pass in a TSPacket instance.'''

		if p.error and not self.ignerror:
			return

		if p.start:
			payload = p.payload
			i = ord(payload[0]) + 1
			self.decode_section_header(payload, i)
		else:
			if self.discontinuity or \
			    self.next_continuity(p.continuity):
				self.discontinuity = True
				return
			self.stored_sects.append(p.payload)
			self.stored_len += len(p.payload)

		while self.table_id != 0xff:
			if self.stored_len < self.sect_len:
				# we need more data
				self.last_continuity = p.continuity
				return

			payload = ''.join(self.stored_sects)
			assert len(payload) == self.stored_len

			if self.syntax:
				# XXX I may need to include the skipped part
				#  above in the crc calculations.
				if not psip_crc32(payload[:self.sect_len]):
					raise ValueError, \
					    'CRC check failed: %s' % \
					    `payload[:self.sect_len]`
				self.extension = (ord(payload[3]) << 8) | \
				    ord(payload[4])
				self.version = (ord(payload[5]) & 0x3e) >> 1
				self.current_next = bool(ord(payload[5]) & 1)
				self.section_number = ord(payload[6])
				self.last_section_number = ord(payload[7])
				self.protocol_version = ord(payload[8])
				# don't include the CRC
				self.table = payload[8:self.sect_len - 4]
				#if self.last_section_number:
				#	print repr(self), repr(p)
			else:
				self.table = payload[3:self.sect_len]

			self.complete = True
			if self.current_only and not self.current_next:
				continue

			# If this fails there are multiple sections
			try:
				self[self.table_id].clean_up()
				self[self.table_id](self)
			except KeyError:
				pass	# No handler, ignore or raise exception?

			# hmm. I had a packet with some low bits clear
			# the spec seems to imply that there can be multiple
			# sections, but every case I've seen in the world
			# there isn't.
			if ord(payload[self.sect_len]) != 0xff:
				#print 'prev:', self.last_section_number
				# I should make sure there is enough data
				self.decode_section_header(payload,
				    self.sect_len)
				#print 'starting next section:', repr(self), repr(payload)
				continue
			else:
				break

	def __repr__(self,  v=('table_id', 'syntax', 'private', 'table',
		    'extension', 'version', 'current_next', 'section_number',
		    'last_section_number', 'protocol_version', )):
		return '<TSPSIPHandler: %s, table objects: %s>' % \
		    (', '.join(attribreprlist(self, v)), super(TSPSIPHandler,
		    self).__repr__())

class PSIPObject(object):
	def parse_table(self, tbl):
		raise NotImplementedError

	def repr_part(self):
		return []

	def __call__(self, psip):
		if psip.syntax:
			self.version = psip.version
			self.current_next = psip.current_next
			self.section_number = psip.section_number
			self.last_section_number = psip.last_section_number
		else:
			self.version = None
			self.current_next = None
			self.section_number = None
			self.last_section_number = None

		self.parse_table(psip)

	def __repr__(self, v=('version', 'current_next', 'section_number',
	    'last_section_number', )):
		return '<%s: %s>' % (self.__class__.__name__,
		    ', '.join(attribreprlist(self, v) + self.repr_part()))

class PAT(PSIPObject, dict):
	def __init__(self):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		PSIPObject.__init__(self)
		dict.__init__(self)

		self.pid_dict = {}

	def clean_up(self):
		self.pid_dict = {}
		self.clear()

	def has_pid(self, pid):
		return self.pid_dict.has_key(pid)

	def get_prog(self, pid):
		return self.pid_dict[pid]

	def parse_table(self, psip, s = '>HH', sl = struct.calcsize('>HH')):
		assert psip.table_id == 0x00

		for i in range(len(psip.table) / sl):
			prog, pid = struct.unpack(s, psip.table[i * sl:(i +
			    1) * sl])
			pid &= 0x1fff
			self.pid_dict[pid] = prog
			self[prog] = pid

	def repr_part(self):
		return [ dict.__repr__(self) ]

def getaudiovideopids(pmt, lang = None):
	anapid = None
	apids = []
	vpids = []
	for i in pmt.es:
		cpid = i[1]
		j = i[2]
		if i[0] == 2:
			vpids.append(cpid)
		elif i[0] == 129:
			apids.append(cpid)
		elif j.has_key(5) and i[0] != 5:
			assert 'AC-3' in map(lambda x: x[:4], j[5]), (i, j)
			if lang is None or lang == j[10][0][0]:
				apids.append(cpid)
			else:
				anapid = cpid

	if not apids and anapid is not None:
		apids.append(anapid)

	return (apids, vpids)

def iteravpids(stream, avpids):
	avpids = sets.ImmutableSet(avpids)

	for i in stream:
		if SimpleTSPacket(i).pid in avpids:
			yield i

class PMT(PSIPObject, dict):
	def __init__(self):
		PSIPObject.__init__(self)
		dict.__init__(self)

		self.pcrpid = None
		self.es = []

	def clean_up(self):
		self.clear()
		del self.es[:]

	def __nonzero__(self):
		return len(self) or bool(self.es)

	def parse_table(self, psip):
		assert psip.table_id == 0x02

		tb = psip.table
		pcrpid = ((ord(tb[0]) & 0x1f) << 8) | ord(tb[1])
		self.pcrpid = pcrpid
		ltmp = ((ord(tb[2]) & 0xf) << 8) | ord(tb[3]) + 4
		self.update(getdescriptors(tb[4:ltmp]))
		i = ltmp

		es = self.es
		while i < len(tb):
			t = ord(tb[i])
			p = ((ord(tb[i + 1]) & 0x1f) << 8) | ord(tb[i + 2])
			l = ((ord(tb[i + 3]) & 0x0f) << 8) | ord(tb[i + 4])
			i += 5
			d = getdescriptors(tb[i:i + l])
			i += l
			es.append((t, p, d))

	def repr_part(self):
		return [ 'PCRpid: %d' % self.pcrpid, dict.__repr__(self),
		    'ES: %s' % `self.es` ]

def channelmajorminorsort(x, y):
	if x['major'] != y['major']:
		return cmp(x['major'], y['major'])

	return cmp(x['minor'], y['minor'])

def gpstoutc(gps, utcoff):
	gpstrue = gps - utcoff
	return gpstrue + 315990000

class STT(PSIPObject):
	def __init__(self):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		PSIPObject.__init__(self)

	def clean_up(self):
		self.utc = None
		self.ds_status = None
		self.ds_day_of_month = None
		self.ds_hour = None

	def parse_table(self, psip):
		assert psip.table_id == 0xcd and psip.table[0] == '\x00'

		system_time, gps_utc_offset, daylight_savings = \
		    struct.unpack('>IBH', psip.table[1:8])
		ds_status = daylight_savings >> 15
		ds_day_of_month = (daylight_savings >> 8) & 0x1f
		ds_hour = daylight_savings & 0xff
		utc = gpstoutc(system_time, gps_utc_offset)
		self.utc = utc
		self.ds_status = ds_status
		self.ds_day_of_month = ds_day_of_month
		self.ds_hour = ds_hour

	def repr_part(self, v=('ds_status', 'ds_day_of_month', 'ds_hour', )):
		return [ `time.ctime(self.utc)`, ] + attribreprlist(self, v)

class MGT(list):
	def __init__(self, pidtable):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		super(MGT, self).__init__()
		self.pidtable = pidtable
		self.watch = {}

	def clean_up(self):
		del self[:]

	def __call__(self, psip):
		assert psip.table_id == 0xc7 and psip.table[0] == '\x00'

		ntables = struct.unpack('>H', psip.table[1:3])[0]
		i = 3
		for foo in xrange(ntables):
			type, pid, version, nbytes, desclen = \
			    struct.unpack('>HHBIH', psip.table[i:i + 11])
			i += 11
			pid &= 0x1fff
			version &= 0x1f
			desclen &= 0xfff
			desc = getdescriptors(psip.table[i:i + desclen])
			self.append((type, pid, version, nbytes, desc))
			i += desclen

			# start watch
			if type >= 0x100 and type <= 0x17f:
				if self.pidtable.has_key(pid):
					# XXX - check that it's in watch
					pass
				else:
					self.watch[type] = { 'pid': pid,
					    'version': version, }
					self.pidtable[pid] = TSPSIPHandler({
					    0xcb: EIT() })
			elif type >= 0x200 and type <= 0x27f:
				if self.pidtable.has_key(pid):
					# XXX - check that it's in watch
					pass
				else:
					#print 'adding ett', pid
					self.watch[type] = { 'pid': pid,
					    'version': version, }
					self.pidtable[pid] = TSPSIPHandler({
					    0xcc: ETT() })


		desclen = struct.unpack('>H', psip.table[i:i + 2])[0]
		desclen &= 0xfff
		desc = getdescriptors(psip.table[i:i + desclen])
		self.desc = desc
		#print `self`

	def __repr__(self):
		return '<MGT: descriptors: %s, loop: %s>' % (`self.desc`,
		    list.__repr__(self))

class EIT(list):
	def __init__(self):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		super(EIT, self).__init__()

	def clean_up(self):
		del self[:]

	def __call__(self, psip):
		assert psip.table_id == 0xcb and psip.table[0] == '\x00'

		ntables = ord(psip.table[1])
		i = 2
		for foo in xrange(ntables):
			event_id, start_time, lochilen, lolength, titlelen = \
			    struct.unpack('>HIBHB', psip.table[i:i + 10])
			i += 10
			event_id &= 0x3fff
			etm_location = (lochilen >> 4) & 0x3
			length = ((lochilen & 0xf) << 16) | lolength
			title = MultiStringStruct(psip.table[i:i + titlelen])
			i += titlelen
			desclen = struct.unpack('>H', psip.table[i:i + 2])[0]
			i += 2
			desclen &= 0xfff
			desc = getdescriptors(psip.table[i:i + desclen])
			i += desclen

			# XXX - UTC offset should be what?
			self.append((event_id, etm_location,
			    gpstoutc(start_time, 0), length, title, desc))

		#print `self`

	def __repr__(self):
		return '<EIT: %s>' % list.__repr__(self)

class TVCT(PSIPObject, dict):
	def __init__(self):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		PSIPObject.__init__(self)
		dict.__init__(self)

	def clean_up(self):
		self.clear()

	def parse_table(self, psip):
		assert psip.table_id == 0xc8

		self['channels'] = []
		tb = psip.table
		i = ord(tb[0]) + 1
		chancnt = ord(tb[i])
		i += 1
		for foo in range(chancnt):
			shrtnm = ''.join(map(lambda x: unichr((ord(x[0]) <<
			    8) | ord(x[1])), [tb[i + x * 2:i + (x + 1) * 2] for
			    x in range(7)])).rstrip(unichr(0))
			i += 7 * 2
			major = (((ord(tb[i]) << 8) | ord(tb[i + 1])) >> 2) & \
			    0x3ff
			minor = ((ord(tb[i + 1]) & 0x3) << 8) | ord(tb[i + 2])
			mode = ord(tb[i + 3])
			i += 4
			carrier, tsid, prog_num, flagsa, source, desc_len = \
			    struct.unpack('>IHHHHH', tb[i:i + 14])
			i += 14
			etm_loc = (flagsa & 0xc000) >> 14
			access_control = bool(flagsa & 0x2000)
			hidden = bool(flagsa & 0x1000)
			hide_guide = bool(flagsa & 0x200)
			service = flagsa & 0x3f
			desc_len &= 0x3ff
			descs = getdescriptors(tb[i:i + desc_len])
			i += desc_len
			self['channels'].append({ 'name': shrtnm,
			    'major': major, 'minor': minor, 'mode': mode,
			    'carrier': carrier, 'tsid': tsid,
			    'prog_num': prog_num, 'source': source,
			    'etm_loc': etm_loc,
			    'access_control': access_control, 'hidden': hidden,
			    'service': service, 'descriptors': descs })

		desc_len = ((ord(tb[i]) & 0x3) << 8) | ord(tb[i + 1])
		i += 2
		self['descriptors'] = getdescriptors(tb[i:i + desc_len])

	def repr_part(self):
		return [ dict.__repr__(self), ]

class ETT(dict):
	def __init__(self):
		'''In order to prevent confusion, you can't init w/ a packet.'''

		super(ETT, self).__init__()

	def clean_up(self):
		pass

	def __call__(self, psip):
		assert psip.table_id == 0xcc and psip.table[0] == '\x00'

		id, event = struct.unpack('>HH', psip.table[1:5])
		event >>= 2
		desc = MultiStringStruct(psip.table[5:])
		self[(id, event)] = desc

		#print `self`

	def __repr__(self):
		return '<ETT: %s>' % dict.__repr__(self)

class TSPESHandler:
	def __init__(self, cb):
		self.cb = cb
		self.discontinuity = True
		self.complete = False
		self.last_continuity = None
		self.pes_len = None

	def next_continuity(self, nc):
		if self.last_continuity is None:
			return True

		return ((self.last_continuity + 1) % 16) == nc

	def is_video(self):
		return (self.stream_id & 0xf0) == 0xe0

	def __call__(self, p):
		if p.error:
			#print 'got error:', `p`
			return

		if p.start:
			if self.pes_len == 0:
				assert self.is_video()
				# if we were unbounded, dump the last one
				if self.next_continuity(p.continuity):
					self.cb(''.join(self.stored_sects))

			payload = p.payload
			if payload[:3] != '\x00\x00\x01':
				raise ValueError, 'packet start code invalid'
			self.stream_id = ord(payload[3])
			self.pes_len = (ord(payload[4]) << 8) | ord(payload[5])
			if not self.is_video():
				#print 'pes', hex(self.stream_id), repr(p)
				assert self.pes_len != 0
			# A value of 0 indicates that the PES packet
			# length is neither specified nor bounded and is
			# allowed only in PES packets whose payload is a
			# video elementary stream contained in Transport
			# Stream packets. -- iso-13818-1 Sect. 2.4.3.7
			if self.pes_len != 0:
				self.pes_len += 6	# add in header
			self.stored_sects = [ payload ]
			self.stored_len = len(self.stored_sects[0])
			self.discontinuity = False
		else:
			if self.discontinuity or \
			    not self.next_continuity(p.continuity):
				self.discontinuity = True
				return
			self.stored_sects.append(p.payload)
			self.stored_len += len(p.payload)

		self.last_continuity = p.continuity
		if self.stored_len < self.pes_len or self.pes_len == 0:
			return

		ps = ''.join(self.stored_sects)
		assert self.stored_len == self.pes_len and \
		    self.pes_len == len(ps)
		self.cb(ps)

def read_clock(buf):
	assert len(buf) == 6

	base = (long(ord(buf[0])) << 25) | (ord(buf[1]) << 17) | \
	    (ord(buf[2]) << 9) | (ord(buf[3]) << 1) | \
	    (ord(buf[4]) >> 7)
	extension = ((ord(buf[4]) & 0x1) << 8) | ord(buf[5])

	return (base, extension)

class SimpleTSPacket:
	def __init__(self, p):
		assert len(p) == TSPKTLEN
		assert p[0] == TSSYNC

		f = ord(p[1])
		self.error = bool(f & 0x80)
		self.start = bool(f & 0x40)
		self.priority = bool(f & 0x20)
		self.pid = ((f & 0x1f) << 8) + ord(p[2])
		if self.pid == 0x1fff:
			return
		f = ord(p[3])
		self.scramble = (f & 0xc0) >> 6
		adapt = (f & 0x30) >> 4
		self.continuity = f & 0xf
		if self.error:
			return

class TSPacket:
	def __init__(self, *p):
		assert len(p) <= 1
		if len(p) == 0:
			return
		p = p[0]
		origp = p

		assert len(p) == TSPKTLEN
		assert p[0] == TSSYNC

		f = ord(p[1])
		self.error = bool(f & 0x80)
		self.start = bool(f & 0x40)
		self.priority = bool(f & 0x20)
		self.pid = ((f & 0x1f) << 8) + ord(p[2])
		if self.pid == 0x1fff:
			return
		f = ord(p[3])
		self.scramble = (f & 0xc0) >> 6
		adapt = (f & 0x30) >> 4
		self.continuity = f & 0xf
		if self.error:
			return
		i = 4
		adapt_len = ord(p[4])
		# XXX - this is a large adapt, is it real?
		if (adapt >= 2 and adapt_len >= 188) or self.scramble:
			return

		if adapt >= 2:
			if adapt == 3:
				pass
				# see below
				#assert adapt_len >= 0 and adapt_len <= 182
			else:
				pass
				# my reading of the spec says this, but in
				# practice this isn't the case
				#assert adapt == 2 and adapt_len == 183
			buf = p[i + 1:i + 1 + adapt_len]
			#print self.error, self.start, self.priority, self.pid, self.scramble, adapt, self.continuity, adapt_len, i
			#assert len(buf) == adapt_len, 'adapt: %d, lengths: %d, %d, buf[0]: %02x' % (adapt, len(buf), adapt_len, ord(buf[0]))
			try:
				self.decode_adaptation(buf)
			except:
				pass
			# XXX - handle adpatation
			i += 1 + adapt_len
		self.payload = p[i:]

	def decode_adaptation(self, adp):
		if len(adp) == 0:
			return

		self.discontinuity_indicator = bool(ord(adp[0]) & 0x80)
		self.random_access_indicator = bool(ord(adp[0]) & 0x40)
		self.elementary_stream_priority = bool(ord(adp[0]) & 0x20)
		PCR_flag = bool(ord(adp[0]) & 0x10)
		OPCR_flag = bool(ord(adp[0]) & 0x08)
		splicing_point_flag = bool(ord(adp[0]) & 0x04)
		transport_private_data_flag = bool(ord(adp[0]) & 0x02)
		adaptation_field_extension_flag = bool(ord(adp[0]) & 0x01)

		i = 1
		if PCR_flag:
			self.PCR = read_clock(adp[i: i + 6])
			i += 6
		if OPCR_flag:
			self.OPCR = read_clock(adp[i: i + 6])
			i += 6
		if splicing_point_flag:
			self.splice_countdown = ord(adp[i])
			i += 1
		if transport_private_data_flag:
			plen = ord(adp[i])
			self.private_data = adp[i + 1: i + 1 + plen]
			i += 1 + plen
		if adaptation_field_extension_flag:
			alen = ord(adp[i])
			ltw_flag = ord(adp[i + 1]) & 0x80
			piecewise_rate_flag = ord(adp[i + 1]) & 0x40
			seamless_splice_flag = ord(adp[i + 1]) & 0x20
			i += 2
			if ltw_flag:
				self.ltw_valid = bool(ord(adp[i]) & 0x80)
				self.ltw_offset = ((ord(adp[i]) & 0x7f) <<
				    8) | ord(adp[i + 1])
				i += 2
			if piecewise_rate_flag:
				self.piecewise_rate = ((ord(adp[i]) & 0x3f) <<
				    16) | (ord(adp[i + 1]) << 8) | \
				    ord(adp[i + 2])
				i += 3
			if seamless_splice_flag:
				self.splice_type = (ord(adp[i]) & 0xf0) >> 4
				self.DTS_next_AU = read_timestamp(adp[i:i + 5])

	def __repr__(self):
		v = ('pid', 'error', 'start', 'continuity', 'priority',
		    'scramble', 'payload', 'discontinuity_indicator',
		    'random_access_indicator', 'elementary_stream_priority',
		    'PCR', 'OPCR', 'splice_countdown', 'private_data',
		    'ltw_valid', 'ltw_offset', 'piecewise_rate', 'splice_type',
		    'DTS_next_AU', )
		return '<TSPacket: %s>' % ', '.join(attribreprlist(self, v))

	def __str__(self):
		'''We may want to save the original packet, and return it until the
data gets modified.'''
		if self.error:
			return '%c%s' % (TSSYNC, '\xff' * 187)
		sb = (self.pid >> 8) & 0x1f
		if self.error:
			sb |= 0x80
		if self.start:
			sb |= 0x40
		if self.priority:
			sb |= 0x20
		tb = self.pid & 0xff
		fb = ((self.scramble & 0x3) << 6) | (self.continuity & 0xf)
		alenstr = ''
		if self.adaptation:
			fb |= 1 << 5
			alenstr = chr(len(self.adaptation))
		if self.payload:
			fb |= 1 << 4
		ret = '%c%c%c%c%s%s%s' % (TSSYNC, sb, tb, fb, alenstr,
		    self.adaptation, self.payload)
		if len(ret) != TSPKTLEN:
			pass
			#print >>sys.stderr, repr(self)
			#print >>sys.stderr, len(self.adaptation), len(self.payload)
		assert len(ret) == TSPKTLEN
		return ret

class TSPStream:
	'''This class takes a file object, and outputs TS packets.'''

	def __init__(self, f, endpos = None):
		self.f = f
		self.endpos = endpos

	def __iter__(self):
		foundsync = False
		buf = self.f.read(READBLK)
		fppos = self.f.tell()
		while buf:
			if self.endpos is not None and \
			     fppos > self.endpos:
				break

			if not foundsync:
				try:
					start = buf.index(TSSYNC)
				except ValueError:
					buf = self.f.read(READBLK)
					fppos = self.f.tell()
					continue

				try:
					if buf[start + TSPKTLEN] == '\x47':
						#if start != 0:
						#	print >>sys.stderr, 'sync off:', start, 'data:', repr(buf[:start])
						foundsync = True
					else:
						#print >>sys.stderr, 'drop to sync:', start, 'data:', repr(buf[:start])
						buf = buf[start + 1:]
						continue
				except IndexError:
					nbuf = self.f.read(READBLK)
					fppos = self.f.tell()
					if not nbuf:
						return
					buf += nbuf

				continue

			if buf[start] != '\x47':
				#print >>sys.stderr, 'sync failed'
				foundsync = False
				continue

			t = buf[start:start + TSPKTLEN]
			if len(t) != TSPKTLEN:
				r = self.f.read(READBLK)
				fppos = self.f.tell()
				if not r:
					#No more data
					break
				buf = buf[start:] + r
				start = 0
				if not buf:
					buf = self.f.read(READBLK)
					fppos = self.f.tell()
				continue

			self.itempos = fppos - len(buf)
			yield t
			buf = buf[start + TSPKTLEN:]
			start = 0
			if not buf:
				buf = self.f.read(READBLK)
				fppos = self.f.tell()

import getopt
import re
import sys

def usage():
	print 'Usage: %s -lmty <mpegtsstream>' % sys.argv[0]
	print '       %s -k <pid> <mpegtsstream>' % sys.argv[0]
	print '       %s -b [ -p ] <mpegtsstream>' % sys.argv[0]
	print '       %s -c <channel> -o <output> <mpegtsstream>' % sys.argv[0]
	print ''
	print '    -l list channels'
	print '    -m print PAT and PMT'
	print '    -t print TVCT'
	print ''
	print '    -b bandwidth'
	print '    -p sort by percentage'
	print ''
	print '    -c channel to capture'
	print '    -o file to output channel'
	print ''
	print '    -k print PCR of pid stream'
	print ''
	print 'Options for all:'
	print '    -y file offset when done'
	print '    -s <start>  Starting pos'
	print '    -e <end>    Ending pos'

def findchannel(tvct, chan):
	for i in tvct['channels']:
		if isinstance(chan, int):
			if i['minor'] == chan:
				return i
		elif isinstance(chan, tuple):
			assert len(chan) == 2
			if i['major'] == chan[0] and i['minor'] == chan[1]:
				return i
		else:
			if i['name'] == chan:
				return i

	return None

def GetTVCT(tsstream):
	listchan = True
	needtvct = True

	needpat = False

	pat = PAT()
	pmts = {}
	tvct = TVCT()

	psippids = { 0x00: TSPSIPHandler({ 0x00: pat }),
		0x1ffb: TSPSIPHandler({ 0xc8: tvct, }),
	}

	def getpmt(pid, pm = pmts, psp = psippids):
		if not pm.has_key(pid):
			pm[pid] = PMT()
			psp[pid] = TSPSIPHandler({ 0x02: pm[pid] })

	def needpmts(pm = pmts):
		for i in pm.itervalues():
			if not i:
				return True

		return False

	for i in itertools.imap(TSPacket, tsstream):
		try:
			psippids[i.pid](i)
		except ValueError:
			continue
		except KeyError:
			pass

		# XXX - we need to give up finding the TVCT after a while, and
		# pretend we found it w/ some defaults.  KCSM doesn't
		# broadcast a TVCT.
		if needtvct and tvct:
			needtvct = False
			needpat = True

		if needpat and pat:
			needpat = False
			for j in pat.itervalues():
				getpmt(j)

		if not (needtvct or needpat or needpmts()):
			break

	try:
		lst = tvct['channels']
		lst.sort(channelmajorminorsort)
	except KeyError:
		# unable to find TVCT
		lst = pat.items()
		lst.sort()
		lst = map(lambda x, y: { 'name': 'PAT%d' % x[1],
		    'prog_num': x[1], 'major': '?', 'minor': y}, lst,
		    range(1, len(pat) + 1))
		tvct = { 'channels': lst }

	for i in lst:
		if i['prog_num'] != 0:
			i['PMT'] = pmts[pat[i['prog_num']]]
			i['PMTpid'] = pat[i['prog_num']]

	return tvct

def main():
	try:
		opts, args = getopt.getopt(sys.argv[1:], "bc:e:hk:lmo:pr:s:ty")
	except getopt.GetoptError:
		# print help information and exit:
		usage()
		sys.exit(2)

	printbyteoffset = False
	printbandwidth = False
	printbandwidthpercent = False
	listchan = False
	channelsel = None
	programsel = None
	output = None
	allmaps = False
	printtvct = False
	startpos = None
	endpos = None

	pcrpid = None

	needtvct = False
	needpat = False
	channelfnd = False
	needallmaps = False
	cuts = False

	for o, a in opts:
		if o == '-b':
			printbandwidth = True
		elif o == "-c":
			try:
				channelsel = int(a)
			except ValueError:
				try:
					channelsel = tuple(map(int,
					    re.split('[-.]', a, 1)))
				except ValueError:
					channelsel = a
			if channelsel is not None:
				needpat = True
				needtvct = True
		elif o == '-e':
			endpos = int(a)
		elif o == '-k':
			pcrpid = int(a)
		elif o == "-m":
			allmaps = True
			needallmaps = True
			needpat = True
		elif o == '-s':
			startpos = int(a)
		elif o == '-t':
			printtvct = True
			needtvct = True
		elif o == "-l":
			listchan = True
			needpat = True
			needtvct = True
		elif o in ("-h", "--help"):
			usage()
			sys.exit()
		elif o == '-o':
			output = a
		elif o == '-p':
			printbandwidthpercent = True
		elif o == '-r':
			programsel = int(a)
			needpat = True
		elif o == '-y':
			printbyteoffset = True

	if len(args) != 1 or (channelsel and not output):
		usage()
		sys.exit()

	inp = open(args[0])
	if startpos is not None:
		inp.seek(startpos)
	s = TSPStream(inp, endpos)
	pat = PAT()
	pmts = {}
	tvct = TVCT()
	stt = STT()

	def null(p):
		#print 'null', repr(p)
		pass
	null.clean_up = lambda: None

	psippids = { 0x00: TSPSIPHandler({ 0x00: pat }),
	}
	pidcnt = {}

	mgt = MGT(psippids)
	psippids[0x1ffb] = TSPSIPHandler({
			#0xc7: mgt,
			0xc8: tvct,
			0xcd: stt,
			})

	def getpmt(pid, pm = pmts, psp = psippids):
		if not pm.has_key(pid):
			pm[pid] = PMT()
			psp[pid] = TSPSIPHandler({ 0x02: pm[pid] })

	def needpmts(pm = pmts):
		for i in pm.itervalues():
			if not i:
				return True

		return False

	lastpcr = None
	lastpatpos = None

	for i in itertools.imap(TSPacket, s):
		#if hasattr(i, 'splice_countdown') or hasattr(i, 'DTS_next_AU'):
		#	print 'splice_countdown:', repr(i)
		#if hasattr(i, 'PCR'):
		#	print 'PCR:', repr(i)
		#if i.pid in (48, 64, 80, 112):
		#	print `i`
		#	print s.itempos, `i`

		if i.pid == 0 or lastpatpos is None:
			lastpatpos = s.itempos

		if pcrpid is not None and i.pid == pcrpid:
			if lastpcr is not None:
				# I've only seen 2703 as the largest
				if i.PCR[0] - lastpcr[0] > 3000:
					print lastpatpos
			lastpcr = i.PCR

		try:
			psippids[i.pid](i)
		except ValueError, x:
			#import traceback
			#print traceback.print_exc()
			print >>sys.stderr, 'bad crc:', repr(i)
			continue
		except KeyError:
			pass

		try:
			pidcnt[i.pid] += 1
		except KeyError:
			pidcnt[i.pid] = 1

		# XXX - we need to give up finding the TVCT after a while, and
		# pretend we found it w/ some defaults.  KCSM doesn't
		# broadcast a TVCT.
		if needtvct and tvct:
			# Handle TVCT
			needtvct = False

		if programsel is not None and pat:
			channelfnd = pat[programsel]
			getpmt(channelfnd)

		if channelsel is not None and pat and tvct:
			channelfnd = findchannel(tvct, channelsel)
			if channelfnd is None:
				sys.stderr.write("Unable to find channel: %s\n"
				    % channelsel)
				channelsel = None
			else:
				channelfnd = pat[channelfnd['prog_num']]
				getpmt(channelfnd)

		if needpat and pat:
			needpat = False
			for pn, j in pat.iteritems():
				if pn == 0:
					# XXX - NIT
					continue
				if listchan or allmaps:
					getpmt(j)

		if needallmaps and pat and pmts:
			for i in pat.itervalues():
				if not pmts.has_key(i):
					break
			needallmaps = False

		#print `tvct`, `pat`, `pmts`
		#print needtvct, needpat, needpmts(), printbandwidth, needallmaps
		if not (needtvct or needpat or needpmts() or printbandwidth or
		    needallmaps or pcrpid):
			break

	if channelfnd and pmts[channelfnd]:
		av = getaudiovideopids(pmts[channelfnd])
		os.system("python tssel.py '%s' %s %s > '%s'" % (args[0],
		    channelfnd, ' '.join(map(str, itertools.chain(*av))),
		    output))

	if allmaps:
		print repr(pat)
		print repr(pmts)

	if printtvct:
		print repr(tvct)

	if listchan:
		#List channels
		#try:
		lst = tvct['channels']
		lst.sort(channelmajorminorsort)
		#except KeyError:
		#	# unable to find TVCT
		#	sys.stderr.write("unable to find TVCT table, faking it.\n")
		#	lst = pat.items()
		#	lst.sort()
		#	lst = map(lambda x, y: { 'prog_num': x[1], 'major': '?', 'minor': y}, lst, range(1, len(pat) + 1))
		for i in lst:
			if i['prog_num'] != 0 and i['prog_num'] != 0xffff:
				#print repr(pmts[pat[i['prog_num']]])
				av = getaudiovideopids(pmts[pat[i['prog_num']]])
				prog_info = '\t'.join(map(lambda x:
				    ','.join(map(str, x)), av))
			else:
				prog_info = ''
			print ('%(major)d.%(minor)d\t%(name)s\t' % i) + \
			    prog_info

	if printbandwidth:
		totpkts = sum(pidcnt.itervalues())
		i = pidcnt.items()
		if printbandwidthpercent:
			def secondfirst(x, y):
				if x[1] == y[1]:
					return cmp(x[0], y[0])
				return cmp(x[1], y[1])
			i.sort(secondfirst)
		else:
			i.sort()
		for pid, cnt in i:
			print '%4d\t%d\t%5.2f' % (pid, cnt,
			    float(cnt) * 100 / totpkts)

	if printbyteoffset:
		print inp.tell()

def justprint(v, p):
	'''v is pid, p is the data'''
	if v != 49:
		return
	pes = PES(p)
	if pes.data[3] != '\x00':
		print `pes`
		return
		fc = findcodes(pes.data)
		print 'justprint', v, len(p), repr(pes), repr(pes.data[:20]), fc
		for i in filter(lambda x: x[1] == '\x00', fc):
			print `pes.data[i[0] + 3: i[0] + 7]`
			if ((ord(pes.data[i[0] + 5]) & 0x38) >> 3) in (2, 3):
				print 'non I frame found: %d' % \
				    ((ord(pes.data[i[0] + 5]) & 0x38) >> 3)

if __name__ == '__main__':
	if True:
		main()
		sys.exit(0)

	if False:
		ps = UnRead(sys.argv[1])
		while ps:
			print `Pack(ps)`
		sys.exit()

	s = TSPStream(open(sys.argv[1]))
	if False:
		cleaned = open(sys.argv[2], 'w')
		for j in s:
			cleaned.write(str(j))
			continue
		sys.exit()

	pids = {}
	skipped = 0
	cont = {}
	count = 0
	pat = PAT()
	pmts = {}
	pesstreams = {}
	tvct = TVCT()
	pidhandlers = { 0x00: TSPSIPHandler({ 0x00: pat }),
		0x1ffb: TSPSIPHandler({ 0xc8: tvct }),
	}
	first = last = None
	for j in itertools.imap(TSPacket, s):
		count += 1
		if j.pid == 8191 or (j.pid != 0 and j.pid != 48):
			skipped += 1
			continue
		else:
			#if j.pid > 4000:
			#print `j`
			try:
				pidhandlers[j.pid](j)
			except KeyError:
				pass
			except ValueError, x:
				print 'VE:', x
			#if pidhandlers[0x1ffb][0xc8]:
			#	print repr(pidhandlers[0x1ffb][0xc8])
		# We should probably cache which ones we've added, and remove
		# Ones that aren't there.  Or do a clean_up callback.
		for k in pidhandlers[0][0].itervalues():
			if pmts.has_key(k):
				continue
			pmts[k] = PMT()
			pidhandlers[k] = TSPSIPHandler({ 0x02: pmts[k] })

		for k in itertools.ifilter(lambda x: x.es, pmts.itervalues()):
			#print repr(k)
			for l in k.es:
				if pesstreams.has_key(l[1]):
					continue
				print repr(l)
				pesstreams[l[1]] = TSPESHandler(lambda x, y =
				    l[1]: justprint(y, x))
				pidhandlers[l[1]] = pesstreams[l[1]]

		try:
			if (cont[j.pid] + 1) % 16 != j.continuity:
				pass
				#print 'continuity failed'
			cont[j.pid] = j.continuity
		except KeyError:
			cont[j.pid] = j.continuity

		try:
			pids[j.pid] += 1
		except KeyError:
			pids[j.pid] = 1
	p = pids.items()
	p.sort()
	print p
	print 'skipped:', skipped
	print 'total:', count
