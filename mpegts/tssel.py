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

import sys
sys.path.append('/Users/jgurney/p4/bktrau/info')

import itertools
import mpegts
import sets
import struct
import sys

def usage():
	print >>sys.stderr, 'Usage: %s <file> <pmtpid> <pid> ...' % sys.argv[0]
	sys.exit(1)

def genpats(pmt, prognum):
	BASEPAT = map(None, "\x47\x40\x00\x10\x00\x00\xb0\x0d\x00\x00\xc1\x00\x00\x00\x00\xe0\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff")

	patidx = 4 + 1	# TS header + pointer table
	BASEPAT[patidx +  8] = chr(prognum >> 8)
	BASEPAT[patidx +  9] = chr(prognum & 0xff)
	BASEPAT[patidx + 10] = chr(0xe0 | ((pmt >> 8) & 0x1f))
	BASEPAT[patidx + 11] = chr(pmt & 0xff)
	newcrc = mpegts.psip_calc_crc32(''.join(BASEPAT[patidx:patidx + 12]))
	newcrc = map(lambda x, crc = newcrc: chr((crc >> (8 * (3 - x))) & 0xff), range(4))
	BASEPAT[patidx + 12:patidx + 16] = newcrc

	assert len(BASEPAT) == mpegts.TSPKTLEN

	ret = []

	# Generate continuity_counter
	old = ord(BASEPAT[3]) & 0xf0
	for i in range(16):	# continuity
		BASEPAT[3] = chr(old | i)
		ret.append(''.join(BASEPAT))

	return ret

def producets(inp, pmtpid, *pids):
	#print `inp`, `pmtpid`, `pids`
	# XXX - check if all pids are ints? in range?
	pids = sets.Set(pids)

	stream = mpegts.TSPStream(inp)
	didpmt = False
	for i in stream:
		frst = ord(i[1])
		# Get first and error bits for testing.
		pid = (frst & 0x1f) << 8 | ord(i[2])
		if frst & 0x80:
			continue
		elif pid == 0 and didpmt:
			yield pats.next()
		elif pid == pmtpid and frst & 0x40:
			if not didpmt:
				startpmt = 4
				if ((ord(i[3]) >> 4) & 0x3) == 0x3:
					# Has adaptation field
					startpmt += ord(i[startpmt]) + 1

				startpmt += ord(i[startpmt]) + 1
				assert i[startpmt] == '\x02', (startpmt, i[0:10])
				pats = itertools.cycle(genpats(pmtpid, struct.unpack('>H', i[startpmt + 3:startpmt + 5])[0]))
				yield pats.next()
				didpmt = True
			# XXX - we probably want to rewrite the PMT to only
			# include the pids we are sending.
			yield i
		elif pid in pids and didpmt:
			yield i

def main():
	if len(sys.argv) < 3:
		usage()

	pmtpid = int(sys.argv[2])
	pids = map(int, sys.argv[3:])

	inp = open(sys.argv[1])
	out = sys.stdout

	producer = producets(inp, pmtpid, *pids)
	filter(lambda x: out.write(x), producer)

if __name__ == '__main__':
	main()
