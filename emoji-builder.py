#!/usr/bin/python
#
# Copyright 2012 Google, Inc.
# Written by Behdad Esfahbod <behdad@google.com>
# Modified by Stuart Gill <stuartg@google.com>
#

import sys, glob, re, os, struct, io
from fontTools import ttx, ttLib

if len (sys.argv) not in [7, 8]:
	print >>sys.stderr, """
Usage: emjoi-builder.py [-d] img-prefix strike-size width height font.ttf out-font.ttf

This will search for files that have img-prefix followed by a hex number,
and end in ".png".  For example, if img-prefix is "icons/", then files
with names like "icons/1f4A9.png" will be loaded.

strike-size is the point size to record for the images in the font

width, height are respectively the actual pixel sizes of the PNG when fully rendered

The script then embeds color bitmaps in the font, for characters that the
font already supports, and writes the new font out.

If the -d parameter is given, the 'glyf' table is dropped from the font.
"""
	sys.exit (1)

drop_glyf = False
if "-d" in sys.argv:
	drop_glyf = True
	sys.argv.remove ("-d")

img_prefix = sys.argv[1]
strike_size = int (sys.argv[2])
width = int (sys.argv[3])
height = int (sys.argv[4])
font_file = sys.argv[5]
out_file = sys.argv[6]

def encode_smallGlyphMetrics (width, height,
			      x_bearing, y_bearing,
			      advance,
			      stream):
	# smallGlyphMetrics
	# Type	Name
	# BYTE	height
	# BYTE	width
	# CHAR	BearingX
	# CHAR	BearingY
	# BYTE	Advance
	stream.extend ([height, width, x_bearing, y_bearing, advance])

# http://www.microsoft.com/typography/otspec/ebdt.htm
def encode_ebdt_format1 (img, stream):

	if img.get_format () != cairo.FORMAT_ARGB32:
		raise "Expected FORMAT_ARGB32, but image has format %d" % img.get_format ()

	width = img.get_width ()
	height = img.get_height ()
	stride = img.get_stride ()
	data = img.get_data ()

	if stride != width * 4:
		raise "Code assumes packed lines, but data is not packed.  Fixme."

	encode_smallGlyphMetrics (width, height, 0, height, width, stream)

	# FIXME Handle endian-ness
	stream.extend (data)
	#for y in range (height):
	#	for x in range (width):
	#		stream.extend (data[y * stride + x * 4 + 2])

# http://www.microsoft.com/typography/otspec/ebdt.htm
def encode_ebdt_format17 (png_stream, png_length, stream, width, height):

	encode_smallGlyphMetrics (width, height, 0, height, width, stream)

	# ULONG data length
	stream.extend (struct.pack(">L", png_length))

	png_array = bytearray(png_stream.read())
	stream.extend (png_array)


img_files = {}
for img_file in glob.glob ("%s*.png" % img_prefix):
	uchar = int (img_file[len (img_prefix):-4], 16)
	img_files[uchar] = img_file
if not img_files:
	raise Exception ("No image files found: '%s*.png'" % img_prefix)
print "Found images for %d characters in '%s*.png'." % (len (img_files), img_prefix)

font = ttx.TTFont (font_file)
print "Loaded font '%s'." % font_file

cmaps = font['cmap']
unicode_cmap = cmaps.getcmap (3, 10)

glyph_imgs = {}
for uchar, img_file in img_files.items ():
	if uchar in unicode_cmap.cmap:
		glyph_name = unicode_cmap.cmap[uchar]
		glyph_id = font.getGlyphID (glyph_name)
		glyph_imgs[glyph_id] = img_file
glyphs = sorted (glyph_imgs.keys ())
if not glyphs:
	raise Exception ("No common characteres found between font and image dir.")
print "Embedding images for %d glyphs." % len (glyphs)

ebdt = bytearray (struct.pack (">L", 0x00020000))
bitmap_offsets = []
for glyph in glyphs:
	img_file = glyph_imgs[glyph]
	#print "Embedding %s for glyph #%d" % (img_file, glyph)
	sys.stdout.write ('.')
	# img = cairo.ImageSurface.create_from_png (img_file)
	img_length = os.path.getsize(img_file)
	img_stream = io.open(img_file, 'rb')
	offset = len (ebdt)
#	encode_ebdt_format1 (img, ebdt)
	encode_ebdt_format17 (img_stream, img_length, ebdt, width, height)
	bitmap_offsets.append ((glyph, offset))
print
print "EBDT table synthesized: %d bytes." % len (ebdt)


def encode_indexSubTable1 (offsets, stream):
	stream.extend (struct.pack(">H", 1)) # USHORT indexFormat
	stream.extend (struct.pack(">H", 17)) # USHORT imageFormat
	imageDataOffset = offsets[0][1]
	stream.extend (struct.pack(">L", imageDataOffset)) # ULONG imageDataOffset
	for gid, offset in offsets:
		stream.extend (struct.pack(">L", offset - imageDataOffset)) # ULONG offsetArray
	stream.extend (struct.pack(">L", 0)) # XXX see spec!

def encode_sbitLineMetrics_hori (stream, x_ppem, y_ppem):
	# sbitLineMetrics
	# Type	Name
	# CHAR	ascender
	# CHAR	descender
	# BYTE	widthMax
	# CHAR	caretSlopeNumerator
	# CHAR	caretSlopeDenominator
	# CHAR	caretOffset
	# CHAR	minOriginSB
	# CHAR	minAdvanceSB
	# CHAR	maxBeforeBL
	# CHAR	minAfterBL
	# CHAR	pad1
	# CHAR	pad2
	stream.extend (struct.pack ("bbBbbbbbbbbb", x_ppem, 0, y_ppem, 0, 0, 0, 0, 0, 0, 0, 0, 0))

def encode_sbitLineMetrics_vert (stream, x_ppem, y_ppem):
	encode_sbitLineMetrics_hori (stream, x_ppem, y_ppem) # XXX


def encode_bitmapSizeTable (offsets, x_ppem, y_ppem, stream):
	# count number of ranges
	count = 1
	start = offsets[0][0]
	last = start
	for gid, offset in offsets[1:]:
		if last + 1 != gid:
			count += 1
		last = gid
	headersLen = count * 8

	headers = bytearray ()
	subtables = bytearray ()
	start = offsets[0][0]
	start_id = 0
	last = start
	last_id = 0
	for gid, offset in offsets[1:]:
		if last + 1 != gid:
			headers.extend (struct.pack(">HHL", start, last, headersLen + len (subtables)))
			encode_indexSubTable1 (offsets[start_id:last_id+1], subtables)

			start = gid
			start_id = last_id + 1
		last = gid
		last_id += 1
	headers.extend (struct.pack(">HHL", start, last, headersLen + len (subtables)))
	encode_indexSubTable1 (offsets[start_id:last_id+1], subtables)

	indexTablesSize = len (headers) + len (subtables)
	numberOfIndexSubTables = count
	bitmapSizeTableSize = 48

	# bitmapSizeTable
	# Type	Name	Description
	# ULONG	indexSubTableArrayOffset	offset to index subtable from beginning of EBLC.
	stream.extend (struct.pack(">L", len (stream) + bitmapSizeTableSize))
	# ULONG	indexTablesSize	number of bytes in corresponding index subtables and array
	stream.extend (struct.pack(">L", indexTablesSize))
	# ULONG	numberOfIndexSubTables	an index subtable for each range or format change
	stream.extend (struct.pack(">L", numberOfIndexSubTables))
	# ULONG	colorRef	not used; set to 0.
	stream.extend (struct.pack(">L", 0))
	# sbitLineMetrics	hori	line metrics for text rendered horizontally
	encode_sbitLineMetrics_hori (stream, x_ppem, y_ppem)
	encode_sbitLineMetrics_vert (stream, x_ppem, y_ppem)
	# sbitLineMetrics	vert	line metrics for text rendered vertically
	# USHORT	startGlyphIndex	lowest glyph index for this size
	stream.extend (struct.pack(">H", offsets[0][0]))
	# USHORT	endGlyphIndex	highest glyph index for this size
	stream.extend (struct.pack(">H", offsets[-1][0]))
	# BYTE	ppemX	horizontal pixels per Em
	stream.extend (struct.pack(">B", x_ppem))
	# BYTE	ppemY	vertical pixels per Em
	stream.extend (struct.pack(">B", y_ppem))
	# BYTE	bitDepth	the Microsoft rasterizer v.1.7 or greater supports the following bitDepth values, as described below: 1, 2, 4, and 8.
	stream.extend (struct.pack(">B", 32)) # XXX
	# CHAR	flags	vertical or horizontal (see bitmapFlags)
	stream.extend (struct.pack(">b", 0x01))

	stream.extend (headers)
	stream.extend (subtables)

eblc = bytearray (struct.pack (">L", 0x00020000))
eblc.extend (struct.pack(">L", 1)) # ULONG numSizes
encode_bitmapSizeTable (bitmap_offsets, strike_size, strike_size, eblc)
print "EBLC table synthesized: %d bytes." % len (eblc)

def add_table (font, tag, data):
	tab = ttLib.tables.DefaultTable.DefaultTable (tag)
	tab.data = str(data)
	font[tag] = tab

add_table (font, 'CBDT', ebdt)
add_table (font, 'CBLC', eblc)

if drop_glyf:
	del font['glyf']
	print "Dropped 'glyf' table."

font.save (out_file)
print "Output font '%s' generated." % out_file
