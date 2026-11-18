"""کاتالوگ کامل پسوندهای شناخته‌شدهٔ تراریوم.

این فهرست از درخواست کاربر گرفته شده و اولویت‌دار است (PRIORITY 1 یعنی رایج‌ترین).
هر پسوند در فهرست، «شناسایی» می‌شود؛ اما فقط دسته‌ای که واقعاً متن دارد به
استخراج و ترجمه می‌رود. دسته‌های تصویر/صدا/ویدیو/آرشیو/اجرایی/دیتابیس با
پیام روشن رد می‌شوند، چون متن قابل ترجمه ندارند.

قاعدهٔ طراحی: هر پسوندی که در هیچ‌کدام از دسته‌های «بدون متن» نباشد، متنی
در نظر گرفته می‌شود. بنابراین فهرستی که در `RAW_PRIORITY_LISTS` می‌آید
هیچ‌وقت جا نمی‌افتد و همهٔ پسوندهای ناشناخته هم بهترین‌تلاش می‌شوند.
"""
from __future__ import annotations

import re

# فهرست خام، دسته‌بندی‌شده بر اساس اولویت (منبع: فهرست کاربر).
RAW_PRIORITY_LISTS = r"""
# PRIORITY 1
.epub
.epub2
.epub3
.pdf
.txt
.doc
.docx
.rtf
.odt
.md
.markdown
.html
.htm
.xhtml
.xml
.fb2
.fb3
.mobi
.azw
.azw3
.kfx
.cbz
.cbr
.cbt
.cb7
.cba
.jpg
.jpeg
.png
.webp
.tiff
.tif
.bmp
.gif
.heic
.heif
.avif
.srt
.vtt
.ass
.ssa
.sub
.smi
.sami
.lrc
.json
.yaml
.yml
.toml
.csv
.tsv
.ppt
.pptx
.odp
.xls
.xlsx
.ods
.py
.js
.ts
.jsx
.tsx
.java
.cpp
.c
.h
.cs
.go
.rs
.php
.rb
.swift
.kt
.dart
.sql
.css
.scss
.vue
.svelte
.sh
.ps1
.bat
.bash
.cmd
.patch
.diff
.text
.pyi
.rss
.atom
.cc
.cxx
.hpp
.jl
.kts
.mm
.odg
.odf
.fodp
.m3u
.pls
.pml
.smil
.ipynb
.tex
.latex
.adoc
.asciidoc
.org
.wiki
.mediawiki
.zip
.rar
.7z
.tar
.gz

# PRIORITY 2
.azw1
.azw4
.kf8
.lit
.lrf
.pdb
.prc
.tcr
.snb
.oeb
.ibooks
.docbook
.ott
.wps
.wpd
.sxw
.xps
.oxps
.pages
.fodt
.uot
.uof
.dot
.dotx
.dotm
.docm
.odm
.wri
.pw
.pmd
.qmd
.rmd
.rst
.rest
.textile
.creole
.pod
.rdoc
.txtz
.htmlz
.lz
.lzh
.lha
.cb8
.j2k
.jp2
.jpf
.jpx
.jpm
.mj2
.jxr
.exr
.hdr
.psd
.psb
.svg
.svgz
.eps
.ai
.indd
.xcf
.kra
.afdesign
.afphoto
.dng
.cr2
.cr3
.nef
.arw
.orf
.rw2
.pef
.sr2
.srf
.raf
.pnm
.pbm
.pgm
.ppm
.pfm
.pcx
.tga
.icb
.dib
.ico
.cur
.pict
.pct
.wmf
.emf
.cgm
.dxf
.dwg
.dwf
.pptm
.pps
.ppsx
.pot
.potx
.potm
.sldx
.sldm
.otp
.sxi
.key
.numbers
.xlsm
.xlsb
.xlt
.xltx
.xltm
.ots
.sxc
.dif
.slk
.sylk
.prn
.dbf
.mdb
.accdb
.sqlite
.sqlite3
.db
.sqlitedb
.fods
.psv
.ssv
.ndjson
.jsonl
.json5
.jsonc
.geojson
.hjson
.hcl
.ini
.cfg
.conf
.properties
.env
.editorconfig
.gitignore
.dockerignore
.dockerfile
.makefile
.mk
.cmake
.gradle
.gradle.kts
.xsd
.xsl
.xslt
.dtd
.rng
.rnc
.wsdl
.wadl
.soap
.graphql
.gql
.proto
.thrift
.avsc
.avdl
.openapi
.swagger
.raml

# PRIORITY 3
.mp3
.wav
.flac
.aac
.m4a
.ogg
.opus
.wma
.aiff
.aif
.amr
.ape
.ac3
.dts
.caf
.mid
.midi
.mka
.tta
.wv
.mpc
.oga
.3ga
.3gpp
.mp4
.m4v
.mkv
.avi
.mov
.wmv
.flv
.webm
.mpg
.mpeg
.m2v
.mts
.m2ts
.vob
.ogv
.3gp
.f4v
.asf
.rm
.rmvb
.divx
.xvid
.mxf
.dpx
.y4m
.sbv
.idx
.dfxp
.ttml
.scc
.stl
.cap
.nxml
.jats
.tei
.bib
.bibtex
.ris
.enw
.nbib
.biblatex
.csl
.opml
.mbox
.eml
.emlx
.msg
.pst
.ost
.vcf
.vcard
.ics
.ical
.mhtml
.mht
.warc
.webarchive
.phtml
.asp
.aspx
.jsp
.erb
.eex
.leex
.hbs
.handlebars
.mustache
.twig
.jinja
.j2
.ejs
.njk
.liquid
.mdx
.marko
.sass
.less
.styl
.stylus
.coffee
.litcoffee
.mjs
.cjs
.esm
.jsonld
.r
.rdata
.rds
.rnw
.sas
.sps
.do
.dta
.stata
.m
.mat
.nb
.wl
.wls
.pde
.ino
.asm
.s
.inc
.f
.for
.f90
.f95
.f03
.f08
.cob
.cbl
.bas
.bi
.vb
.vbs
.vba
.pl
.pm
.lua
.ex
.exs
.erl
.hrl
.hs
.lhs
.ml
.mli
.fs
.fsi
.fsx
.clj
.cljs
.cljc
.edn
.scala
.sc
.groovy
.gvy
.sol
.v
.vhd
.vhdl
.tcl
.awk
.sed
.zsh
.fish
.nu
.psm1
.psd1
.rake
.gemspec
.podspec
.cabal
.stack
.nix
.tf
.tfvars
.pkr
.bicep
.arm

# PRIORITY 4
.dcm
.dicom
.nii
.nii.gz
.mha
.mhd
.nrrd
.vtk
.vtu
.vtp
.obj
.ply
.gltf
.glb
.fbx
.3ds
.blend
.ma
.mb
.usdz
.usd
.abc
.step
.stp
.iges
.igs
.sldprt
.sldasm
.ipt
.iam
.f3d
.skp
.ifc
.rvt
.rfa
.gcode
.nc
.tap
.cnc
.gerber
.gbr
.gtl
.gbl
.gto
.gbo
.gts
.gbs
.gko
.drl
.art
.pcb
.sch
.kicad_pcb
.kicad_sch
.asc
.pcbdoc
.schdoc
.odb
.spice
.cir
.lib
.model
.raw
.log
.out
.tr
.tr0
.pwl
.sparam
.s2p
.s3p
.s4p
.s8p
.touchstone
.fits
.fit
.fts
.hdf
.h5
.hdf5
.netcdf
.grib
.grb
.bufr
.zarr
.parquet
.feather
.arrow
.orc
.avro
.pickle
.pkl
.joblib
.npy
.npz
.sav
.xpt
.sas7bdat
.sas7bcat
.sas7bvew
.sas7bndx
.por
.mtz
.cif
.mmcif
.mol
.sdf
.mol2
.xyz
.inchi
.smi
.smiles
.cml
.cdxml
.molx
.mrv
.jdx
.jcamp
.ab1
.fasta
.fa
.fas
.fna
.faa
.fastq
.fq
.sam
.bam
.cram
.bcf
.gff
.gff3
.gtf
.bed
.bim
.bai
.bw
.bigwig
.wig
.maf
.paf
.gbk
.gb
.genbank
.embl
.aln
.clustal
.phylip
.nex
.nexus
.tree
.nwk
.newick
.meg
.megx
.phy

# PRIORITY 5
.iso
.img
.dmg
.vhdx
.vmdk
.vdi
.qcow
.qcow2
.wim
.esd
.cab
.arj
.ace
.z
.bz
.bz2
.xz
.lzma
.zst
.br
.tgz
.tbz
.tbz2
.txz
.tlz
.cpio
.lz4
.sz
.sit
.sitx
.sea
.hqx
.bin
.uue
.uu
.yenc
.par
.par2
.apk
.aab
.ipa
.deb
.rpm
.pkg
.msi
.msix
.appx
.exe
.dll
.so
.dylib
.jar
.war
.ear
.class
.pyc
.pyo
.pyd
.whl
.egg
.gem
.crate
.nupkg
.snupkg
.vsix
.vsixmanifest
.xpi
.crx
.oxt
.xlam
.xla
.xll
.xlb
.xlc
.xlw
.sxd
.sxm
.sda
.sdd
.sdc
.sdw
.sgl
.smf
.svm
.sv
.brd
.kicad_wks
.kicad_pro
.kicad_mod
.kicad_sym
.kicad_fp
.kicad_dru
.kicad_prl
.kicad_jobset
.ger
.gbrjob
.gm1
.gm2
.gm3
.gp1
.gp2
.gp3
.gp4
.gp5
.gpi
.gpt
.xln
.xlnx
.ses
.sesx
.aep
.aepx
.prproj
.premiere
.drp
.kdenlive
.veg
.mlt
.fcpxml
.otio
.edl
.ale
.aml
.cube
.3dl
.look
.lut
.xmp
.aae
.sidecar
.thm
.lrv
.moff
.mod
.braw
.r3d
.crm
.mos
.mrw
.iiq
.ptx
.pxn
.qtk
.rwl
.srw
"""

_PRIORITY_HEADER = re.compile(r"^#\s*PRIORITY\s*(\d)", re.IGNORECASE)
_EXTENSION_LINE = re.compile(r"^\.([A-Za-z0-9][A-Za-z0-9_+.\-]*)$")


def _parse_priorities(raw: str) -> dict[int, frozenset[str]]:
    """خطوط فهرست خام را به مجموعهٔ پسوندها به‌تفکیک اولویت تبدیل می‌کند."""
    buckets: dict[int, set[str]] = {1: set(), 2: set(), 3: set(), 4: set(), 5: set()}
    current = 1
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header = _PRIORITY_HEADER.match(stripped)
        if header:
            current = int(header.group(1))
            continue
        match = _EXTENSION_LINE.match(stripped)
        if match:
            buckets[current].add("." + match.group(1).lower())
    return {priority: frozenset(values) for priority, values in buckets.items()}


PRIORITY_EXTENSIONS = _parse_priorities(RAW_PRIORITY_LISTS)
ALL_EXTENSIONS = frozenset().union(*PRIORITY_EXTENSIONS.values())

# --- دسته‌های «بدون متن قابل ترجمه» ----------------------------------------
# هر چیزی که اینجا نیست، متن‌دار در نظر گرفته می‌شود و ترجمه می‌شود.

IMAGE_EXTENSIONS = frozenset(
    """
    .jpg .jpeg .png .webp .tiff .tif .bmp .gif .heic .heif .avif .j2k .jp2 .jpf
    .jpx .jpm .mj2 .jxr .exr .hdr .psd .psb .ai .indd .xcf .kra .afdesign .afphoto
    .dng .cr2 .cr3 .nef .arw .orf .rw2 .pef .sr2 .srf .raf .pnm .pbm .pgm .ppm
    .pfm .pcx .tga .icb .dib .ico .cur .pict .pct .wmf .emf .cgm .dpx .braw .r3d
    .crm .mos .mrw .iiq .ptx .pxn .qtk .rwl .srw .svgz .thm .lrv
    """.split()
)

AUDIO_EXTENSIONS = frozenset(
    """
    .mp3 .wav .flac .aac .m4a .ogg .opus .wma .aiff .aif .amr .ape .ac3 .dts
    .caf .mid .midi .mka .tta .wv .mpc .oga .3ga
    """.split()
)

# نکته: «.ts» را ویدیو حساب نمی‌کنیم چون در این پروژه یعنی TypeScript؛ فایل
# واقعی MPEG-TS با بررسی محتوا (sniff) تشخیص داده و رد می‌شود.
VIDEO_EXTENSIONS = frozenset(
    """
    .mp4 .m4v .mkv .avi .mov .wmv .flv .webm .mpg .mpeg .m2v .mts .m2ts .vob
    .ogv .3gp .3gpp .f4v .asf .rm .rmvb .divx .xvid .mxf .y4m
    """.split()
)

ARCHIVE_EXTENSIONS = frozenset(
    """
    .rar .7z .tar .gz .tgz .bz .bz2 .tbz .tbz2 .xz .txz .lzma .zst .z .br
    .cpio .lz4 .sz .sit .sitx .sea .hqx .cab .arj .ace .lz .lzh .lha
    .cbz .cbr .cbt .cb7 .cba .cb8
    """.split()
)

EXECUTABLE_EXTENSIONS = frozenset(
    """
    .exe .dll .so .dylib .jar .war .ear .class .pyc .pyo .pyd .whl .egg .gem
    .crate .nupkg .snupkg .vsix .vsixmanifest .xpi .crx .oxt .apk .aab .ipa
    .deb .rpm .pkg .msi .msix .appx .iso .img .dmg .vhd .vhdx .vmdk .vdi
    .qcow .qcow2 .wim .esd .bin .uue .uu .yenc .par .par2 .aep .aepx .prproj
    .premiere .drp .kdenlive .veg .mlt .fcpxml .otio .edl .ale .aml .lut .cube
    .3dl .look .sidecar .aae .xlam .xla .xll .xlb .xlc .xlw .vsd
    """.split()
)

DATABASE_EXTENSIONS = frozenset(
    """
    .mdb .accdb .sqlite .sqlite3 .db .sqlitedb .dbf .sav .xpt .sas7bdat
    .sas7bcat .sas7bvew .sas7bndx .por .dta .stata .rdata .rds .pickle .pkl
    .joblib .npy .npz .parquet .feather .arrow .orc .avro .zarr .hdf .h5 .hdf5
    .netcdf .grib .grb .bufr .mat .fits .fit .fts .mtz
    """.split()
)

# مدل سه‌بعدی/مهندسی/علمی با ساختار باینری (نسخهٔ متنی‌شان در دستهٔ متن است).
BINARY_EXTENSIONS = frozenset(
    """
    .dcm .dicom .nii .nii.gz .mha .mhd .nrrd .vtk .vtu .vtp .glb .fbx .3ds
    .blend .ma .mb .usdz .usd .abc .sldprt .sldasm .ipt .iam .f3d .skp
    .rvt .rfa .dwg .dwf .brd .pcbdoc .schdoc .odb .ses .sesx .bam .cram .bcf
    .bai .bw .bigwig .ab1 .gnm .kmz .pst .ost
    .msg .mode .icns .idx .scc .stl .cbr .cbt .cb7 .cba .cb8
    """.split()
)

NON_TEXT_EXTENSIONS = (
    IMAGE_EXTENSIONS
    | AUDIO_EXTENSIONS
    | VIDEO_EXTENSIONS
    | ARCHIVE_EXTENSIONS
    | EXECUTABLE_EXTENSIONS
    | DATABASE_EXTENSIONS
    | BINARY_EXTENSIONS
) & ALL_EXTENSIONS

# همهٔ پسوندهایی که واقعاً متن دارند و به استخراج می‌روند.
TEXT_EXTENSIONS = ALL_EXTENSIONS - NON_TEXT_EXTENSIONS
# آرشیو ZIP (و مشتقات زیپ) می‌تواند متن داخلش داشته باشد، پس ترجمه‌شدنی است.
# کتاب‌های کمیک آرشیو تصویری‌اند و متن قابل ترجمه ندارند.
COMIC_EXTENSIONS = frozenset({".cbz", ".cbr", ".cbt", ".cb7", ".cba", ".cb8"})

ZIP_CONTAINER_EXTENSIONS = frozenset({".zip", ".epub", ".txtz", ".htmlz"})
TRANSLATABLE_EXTENSIONS = (TEXT_EXTENSIONS | ZIP_CONTAINER_EXTENSIONS) - {
    ext for ext in NON_TEXT_EXTENSIONS if ext not in ZIP_CONTAINER_EXTENSIONS
}

# --- فهرست نمایشی کوتاه برای کاربر (بقیه فقط با جست‌وجو دیده می‌شوند) ------
DISPLAY_GROUPS = [
    ("📄 متن و کتاب", ".txt .md .rst .pdf .epub .docx .doc .rtf .odt .fb2"),
    ("🌐 وب و نشانه‌گذاری", ".html .htm .xhtml .xml .mhtml .tex .adoc .mdx"),
    ("📊 آفیس", ".pptx .ppt .xlsx .xls .ods .odp .csv .tsv"),
    ("🎬 زیرنویس", ".srt .vtt .ass .ssa .sub .smi .lrc"),
    ("💻 کد و داده", ".py .js .ts .java .cpp .go .rs .sql .json .yaml .toml"),
    ("✉️ ایمیل و تقویم", ".eml .mbox .vcf .ics"),
    ("🧬 دادهٔ متنی علمی", ".fasta .gff .gff3 .sam .wig .pdb .cif"),
    ("🔧 فنی و CAD متنی", ".gcode .nc .dxf .step .stl .obj .gltf .kicad_pcb"),
    ("🗜️ آرشیو متنی", ".zip (فایل‌های متنی داخلش)"),
]

DISPLAY_EXTENSIONS = [
    ext
    for _, examples in DISPLAY_GROUPS
    for ext in examples.split()
    if ext.startswith(".")
]


def search_extensions(query: str, limit: int = 120) -> list[str]:
    """جست‌وجو در کل کاتالوگ؛ خالی یعنی هیچ نتیجه‌ای نیست."""
    needle = (query or "").strip().lower().lstrip(".")
    if not needle:
        return []
    matches = [ext for ext in ALL_EXTENSIONS if needle in ext.lstrip(".")]
    matches.sort(key=lambda ext: (ext in NON_TEXT_EXTENSIONS, len(ext), ext))
    return matches[:limit]
