#!/usr/bin/env python

import pydvdread
import sys

def bcdtoint(bcd):
	base = 1
	ret = 0
	while bcd:
		assert bcd % 16 < 10, 'invalid bcd digit in: %#x' % bcd
		ret += bcd % 16 * base
		base *= 10
		bcd /= 16
	return ret

def strdvdtime(tobj):
	return '%x:%02x:%02x.%02x' % (tobj.hour, tobj.minute, tobj.second,
		    tobj.frame_u & 0x3f)

def dumppgc(j):
	print 'nr_of_progs:', j.nr_of_programs
	print 'nr_of_cells:', j.nr_of_cells
	print 'time:', strdvdtime(j.playback_time)
	for k in range(j.nr_of_programs):
		print 'program_map[%d]:' % k, pydvdread.uchar_getitem(
		    j.program_map, k)
	for k in range(j.nr_of_cells):
		l = pydvdread.cell_playback_getitem(j.cell_playback, k)
		print 'cell_playback[%d]:' % k, '%d-%d' % (l.first_sector,
		    l.last_sector), strdvdtime(l.playback_time)

try:
	dvd = pydvdread.DVD('/dev/null')
except ValueError:
	pass
except:
	assert 0, 'Failed to fail.'

dvd = pydvdread.DVD('/dev/rdisk1')
print dvd, ', '.join(map(repr, dvd))
try:
	print `dvd[(5, 5)]`
except IndexError:
	pass
except:
	assert 0, 'Failed to fail.'

print 'vmg'
ifo = dvd.getifo(0)
assert ifo.txtdt_mgi is None
print 'ifo:', `ifo`
print '# vts:', ifo.vmgi_mat.vmg_nr_of_title_sets
print 'provider:', `ifo.vmgi_mat.provider_identifier`
print 'nr_of_srpts:', ifo.tt_srpt.nr_of_srpts
for i in range(ifo.tt_srpt.nr_of_srpts):
	print `ifo.tt_srpt.title`
	j = pydvdread.title_info_getitem(ifo.tt_srpt.title, i)
	print 'playbacktype:', j.pb_ty.multi_or_random_pgc_title, j.pb_ty.jlc_exists_in_cell_cmd, j.pb_ty.jlc_exists_in_prepost_cmd, j.pb_ty.jlc_exists_in_button_cmd, j.pb_ty.jlc_exists_in_tt_dom, j.pb_ty.chapter_search_or_play, j.pb_ty.title_or_time_play
	print '# angles:', j.nr_of_angles
	print '# ptts:', j.nr_of_ptts
	print 'parental id:', j.parental_id
	print 'title set #:', j.title_set_nr
	print 'vts ttn:', j.vts_ttn

for ifonum in range(ifo.vmgi_mat.vmg_nr_of_title_sets):
	print 'vts'
	ifo = dvd.getifo(1 + ifonum)
	print 'ifo:', `ifo`
	print dir(ifo)
	print '# of srpts:', ifo.vts_ptt_srpt.nr_of_srpts
	for i in range(ifo.vts_ptt_srpt.nr_of_srpts):
		print 'title #:', i
		j = pydvdread.ttu_getitem(ifo.vts_ptt_srpt.title, i)
		print '# of ptts:', j.nr_of_ptts
		for i in range(j.nr_of_ptts):
			k = pydvdread.ptt_info_getitem(j.ptt, i)
			print 'pgcn:', k.pgcn
			print 'pgn:', k.pgn

	print 'pgc:', `ifo.vts_pgcit.nr_of_pgci_srp`
	for i in range(ifo.vts_pgcit.nr_of_pgci_srp):
		dumppgc(pydvdread.pgci_srp_getitem(ifo.vts_pgcit.pgci_srp, i).pgc)
