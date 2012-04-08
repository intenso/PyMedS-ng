#!/usr/bin/env python
#
#  Copyright 2006 John-Mark Gurney.
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

__all__ = [ 'decode_title', 'decode_description' ]

try:
	from ctypes import *
	import ctypes.util
	import os.path

	path = os.path.dirname(__file__)
	if not path:
		path = '.'
	hd = ctypes.cdll.LoadLibrary(os.path.join(path, 'libhuffdecode.so.1'))
	hd.decodetitle.restype = c_int
	hd.decodetitle.argtypes = [ c_char_p, c_char_p, c_int ]
	hd.decodedescription.restype = c_int
	hd.decodedescription.argtypes = [ c_char_p, c_char_p, c_int ]

	def docall(fun, s):
		buflen = 256
		while True:
			buf = ctypes.create_string_buffer(buflen)
			cnt = fun(s, buf, buflen)
			if cnt < buflen:
				break

			buflen *= 2

		return buf.value.decode('iso8859-1')

	decode_title = lambda x: docall(hd.decodetitle, x)
	decode_description = lambda x: docall(hd.decodedescription, x)

except ImportError:
	def foo(*args):
		raise NotImplementedError, 'Failed to import ctypes'
	decode_title = decode_description = foo

except OSError:
	def foo(*args):
		raise NotImplementedError, 'Failed to find library huffdecode'
	decode_title = decode_description = foo
