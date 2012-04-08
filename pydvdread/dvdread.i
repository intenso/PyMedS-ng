// File : dvdread.i
%module cdvdread
%include "typemaps.i"
%include "stdint.i"

%include "cmalloc.i"
%include "carrays.i"
%include "cdata.i"
%allocators(void);

%include "cpointer.i"
%pointer_cast(void *, unsigned char *, voidptr_to_ucharptr);

typedef long ssize_t;
typedef long long off_t;

%{
#include "dvdread/dvd_reader.h"
#include "dvdread/ifo_read.h"
#include "dvdread/nav_read.h"
%}

%typemap (in,numinputs=1) (unsigned char *uc128, unsigned int uc128) (unsigned char tempa[128], int tempb) {
	$1 = tempa;
	$2 = tempb = PyInt_AsLong($input);
	if (tempb <= 0 || tempb > 128) {
		PyErr_SetString(PyExc_ValueError, "int out of range (0,128]");
		return NULL;
	}
}

%typemap (argout) (unsigned char *uc128, unsigned int uc128) {
	$result = SWIG_Python_AppendOutput($result, PyString_FromStringAndSize(tempa$argnum, tempb$argnum));
}

%typemap (in,numinputs=1) (char *c33, unsigned int c33) (char tempa[33], int tempb) {
	$1 = tempa;
	$2 = tempb = PyInt_AsLong($input);
	if (tempb <= 0 || tempb > 33) {
		PyErr_SetString(PyExc_ValueError, "int out of range (0,33]");
		return NULL;
	}
}

%typemap (argout) (char *c33, unsigned int c33) {
	$result = SWIG_Python_AppendOutput($result, PyString_FromStringAndSize(tempa$argnum, tempb$argnum));
}

int DVDUDFVolumeInfo( dvd_reader_t *, char *c33, unsigned int c33,
                      unsigned char *uc128, unsigned int uc128);
int DVDISOVolumeInfo( dvd_reader_t *, char *c33, unsigned int c33,
                      unsigned char *uc128, unsigned int uc128);

/* Clear them */
%typemap (in,numinputs=1) (unsigned char *uc128, unsigned int uc128) (unsigned char tempa[128], int tempb);
%typemap (argout) (unsigned char *uc128, unsigned int uc128);
%typemap (in,numinputs=1) (char *c33, unsigned int c33) (char tempa[33], int tempb);
%typemap (argout) (char *c33, unsigned int c33);

%include dvdread/dvd_reader.h

%include dvdread/ifo_read.h

%include <stdint.h>
%include dvdread/ifo_types.h

%array_functions(audio_attr_t, audio_attr)
%array_functions(cell_playback_t, cell_playback)
%array_functions(cell_position_t, cell_position)
%array_functions(map_ent_t, map_ent)
%array_functions(pgc_program_map_t, pgc_program_map)
%array_functions(pgci_lu_t, pgci_lu)
%array_functions(pgci_srp_t, pgci_srp)
%array_functions(ptt_info_t, ptt_info)
%array_functions(subp_attr_t, subp_attr)
%array_functions(title_info_t, title_info)
%array_functions(ttu_t, ttu)
%array_functions(txtdt_lu_t, txtdt_lu)
%array_functions(unsigned char, uchar)
%array_functions(vm_cmd_t, vm_cmd)
%array_functions(vts_attributes_t, vts_attributes)
%array_functions(vts_tmap_t, vts_tmap)

%include dvdread/nav_read.h
