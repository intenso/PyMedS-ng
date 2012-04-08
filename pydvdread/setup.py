#!/usr/bin/env python

from distutils.core import setup, Extension
import os.path

dvdreaddir = '/opt/local'
dvdreaddirinc = os.path.join(dvdreaddir, 'include') 
dvdreaddirlib = os.path.join(dvdreaddir, 'lib') 

setup(name = "pydvdread", version = "0.1",
	description = "Python libdvdread interface",
	author = "John-Mark Gurney",
	author_email = "gurney_j@resnet.uoregon.edu",
	packages = [ 'pydvdread' ],
	package_dir = { 'pydvdread': '.' },
	ext_package = "pydvdread",
	ext_modules = [ Extension("_cdvdread", [ "dvdread.i" ],
			swig_opts = [ '-I/usr/include', '-I%s' % dvdreaddirinc ],
			include_dirs = [ dvdreaddirinc, '/usr/include' ],
			library_dirs = [ dvdreaddirlib, ],
			libraries = [ 'dvdread' ]),
		]
)
