"""رجیستری فرمت‌ها و تشخیص نوع فایل برای تراریوم.

هدف: هر فایلی که «متن» دارد ترجمه شود، حتی اگر پسوندش در هیچ لیستی نباشد.
فقط چیزهایی که متن قابل استخراج ندارند (ویدیو، صدا، عکس خام، آرشیو فشردهٔ باینری)
با پیام روشن رد می‌شوند.
"""
from __future__ import annotations

from pathlib import Path

from trarium.extractors.catalog import (
    ALL_EXTENSIONS as CATALOG_EXTENSIONS,
    AUDIO_EXTENSIONS as CATALOG_AUDIO_EXTENSIONS,
    BINARY_EXTENSIONS as CATALOG_BINARY_EXTENSIONS,
    DATABASE_EXTENSIONS as CATALOG_DATABASE_EXTENSIONS,
    DISPLAY_GROUPS,
    EXECUTABLE_EXTENSIONS as CATALOG_EXECUTABLE_EXTENSIONS,
    IMAGE_EXTENSIONS as CATALOG_IMAGE_EXTENSIONS,
    NON_TEXT_EXTENSIONS,
    TRANSLATABLE_EXTENSIONS as CATALOG_TRANSLATABLE_EXTENSIONS,
    VIDEO_EXTENSIONS as CATALOG_VIDEO_EXTENSIONS,
    search_extensions,
)

# --- متن ساده و نشانه‌گذاری ------------------------------------------------
PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".text", ".md", ".markdown", ".mdx", ".rst", ".rest", ".adoc",
    ".asciidoc", ".org", ".wiki", ".mediawiki", ".textile", ".creole", ".pod",
    ".rdoc", ".tex", ".latex", ".bib", ".bibtex", ".ris", ".enw", ".nbib",
    ".csv", ".tsv", ".psv", ".ssv", ".json", ".jsonl", ".ndjson", ".json5",
    ".jsonc", ".jsonld", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".env", ".editorconfig", ".gitignore", ".dockerignore",
    ".hcl", ".tf", ".tfvars", ".graphql", ".gql", ".proto", ".thrift",
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue",
    ".svelte", ".java", ".kt", ".kts", ".scala", ".groovy", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cxx", ".cs", ".go", ".rs", ".php", ".rb",
    ".swift", ".dart", ".sql", ".css", ".scss", ".sass", ".less", ".styl",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".pl",
    ".pm", ".lua", ".ex", ".exs", ".erl", ".hrl", ".hs", ".ml", ".mli",
    ".fs", ".fsi", ".fsx", ".clj", ".cljs", ".cljc", ".edn", ".r", ".jl",
    ".asm", ".s", ".f", ".for", ".f90", ".f95", ".cob", ".cbl", ".bas",
    ".vb", ".vbs", ".vba", ".m", ".mm", ".nix", ".cabal", ".gemspec",
    ".rake", ".dockerfile", ".makefile", ".mk", ".cmake", ".gradle",
    ".ipynb", ".log", ".out", ".patch", ".diff", ".srt", ".vtt",
}

# --- HTML/XML و مشتقات ------------------------------------------------------
HTML_LIKE_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".xml", ".fb2", ".fb3", ".xps", ".oxps",
    ".opml", ".rss", ".atom", ".xsl", ".xslt", ".xsd", ".dtd", ".svg",
    ".mhtml", ".mht", ".webarchive", ".docbook", ".tei", ".jats", ".nxml",
    ".ttml", ".dfxp", ".smil", ".pls", ".m3u",
}

# --- زیرنویس ----------------------------------------------------------------
# نکته: ‎.scc و ‎.stl زیرنویس هگز/باینری‌اند و در دستهٔ بدون‌متن می‌مانند.
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".smi",
                       ".sami", ".sbv", ".lrc"}

# --- فایل‌های ZIP-محور (سند آفیس / ODF) ------------------------------------
ZIP_DOCUMENT_EXTENSIONS = {
    ".docx", ".docm", ".dotx", ".dotm", ".pptx", ".pptm", ".ppsx", ".pps",
    ".potx", ".potm", ".xlsx", ".xlsm", ".xltx", ".xltm", ".odt", ".ott",
    ".ods", ".ots", ".odp", ".otp", ".odg", ".odf", ".fodt", ".fods",
    ".fodp", ".epub",
}

# --- اسناد قدیمی/باینری که فقط «بهترین تلاش» ممکن است ----------------------
# ‎.rb عمداً اینجا نیست: در این پروژه یعنی Ruby و در PLAIN_TEXT است.
LEGACY_DOCUMENT_EXTENSIONS = {
    ".doc", ".wps", ".wpd", ".wri", ".lrf", ".lit", ".pdb", ".prc",
    ".snb", ".oeb", ".ibooks", ".tcr", ".mobi", ".azw", ".azw1", ".azw3",
    ".azw4", ".kf8", ".pml", ".pmd", ".sxw", ".sxc", ".sxi", ".sxd", ".sda",
    ".uot", ".uof", ".pages", ".numbers", ".key",
}

ALREADY_NATIVE = {".pdf", ".epub", ".txt"}

# --- چیزهایی که متن قابل ترجمه ندارند --------------------------------------
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif",
    ".heic", ".heif", ".avif", ".ico", ".j2k", ".jp2", ".jpx", ".jpm",
    ".psd", ".psb", ".xcf", ".kra", ".dng", ".cr2", ".cr3", ".nef", ".arw",
    ".orf", ".rw2", ".pef", ".raf", ".tga", ".pcx", ".pnm", ".pbm", ".pgm",
    ".ppm", ".exr", ".hdr", ".dds",
}

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga", ".opus", ".wma",
    ".aiff", ".aif", ".amr", ".ape", ".ac3", ".dts", ".caf", ".mid", ".midi",
    ".mka", ".tta", ".wv", ".mpc", ".3ga",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg",
    ".mpeg", ".m2v", ".mts", ".m2ts", ".vob", ".ogv", ".3gp", ".3gpp",
    ".f4v", ".asf", ".rm", ".rmvb", ".divx", ".mxf", ".y4m",
}

ARCHIVE_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".tbz2",
    ".xz", ".txz", ".lzma", ".zst", ".z", ".lz", ".lzh", ".lha", ".cpio",
    ".lz4", ".cab", ".arj", ".ace", ".sit", ".sitx", ".iso", ".img",
    ".dmg", ".apk", ".aab", ".ipa", ".deb", ".rpm", ".pkg", ".msi",
    ".exe", ".dll", ".so", ".dylib", ".jar", ".war", ".class", ".pyc",
    ".bin", ".whl", ".egg", ".gem", ".crate", ".nupkg", ".vsix", ".xpi",
    ".crx", ".otf", ".ttf", ".woff", ".woff2",
}

# --- متن‌های نمایشی ---------------------------------------------------------
FORMAT_FAMILIES = [
    ("📄 متن ساده", ".txt .md .rst .adoc .org .csv .tsv .json .yaml .toml .log"),
    ("📚 کتاب", ".epub .pdf .fb2 .mobi .azw3 .cbz .docx .odt .rtf .doc"),
    ("🌐 وب و XML", ".html .htm .xhtml .xml .mhtml .opml .rss"),
    ("📊 آفیس", ".docx .pptx .xlsx .odt .ods .odp .rtf"),
    ("🎬 زیرنویس", ".srt .vtt .ass .ssa .sub .smi .lrc"),
    ("💻 کد", ".py .js .ts .java .cpp .go .rs .sql .sh .ipynb"),
    ("🗜️ آرشیو", ".zip (فایل‌های متنی داخلش)"),
]

# لیست پیشنهادی برای فیلتر فایل در رابط کاربری (همهٔ فرمت‌های متنی شناخته‌شده)
TRANSLATABLE_EXTENSIONS = sorted(
    (PLAIN_TEXT_EXTENSIONS - HTML_LIKE_EXTENSIONS)
    | HTML_LIKE_EXTENSIONS
    | ZIP_DOCUMENT_EXTENSIONS
    | LEGACY_DOCUMENT_EXTENSIONS
    | SUBTITLE_EXTENSIONS
    | {".pdf", ".txt", ".epub", ".zip"}
)

# پسوندهایی که فقط با استخراج بهترین‌تلاش خوانده می‌شوند
BEST_EFFORT_EXTENSIONS = LEGACY_DOCUMENT_EXTENSIONS | {".doc", ".wps", ".wpd"}

# --- ادغام کاتالوگ کامل پسوندها --------------------------------------------
# کاتالوگ (catalog.py) فهرست اولویت‌دار و کامل پسوندها را دارد؛ اینجا فقط
# مجموعه‌های موجود گسترش می‌یابند تا رفتار قبلی حفظ شود.
IMAGE_EXTENSIONS = IMAGE_EXTENSIONS | CATALOG_IMAGE_EXTENSIONS
AUDIO_EXTENSIONS = AUDIO_EXTENSIONS | CATALOG_AUDIO_EXTENSIONS
VIDEO_EXTENSIONS = VIDEO_EXTENSIONS | CATALOG_VIDEO_EXTENSIONS

# فایل‌های باینری/تخصصی که متن قابل استخراج ندارند (اجرایی، دیتابیس، مدل سه‌بعدی).
BINARY_ONLY_EXTENSIONS = (
    (CATALOG_EXECUTABLE_EXTENSIONS | CATALOG_DATABASE_EXTENSIONS | CATALOG_BINARY_EXTENSIONS)
    & CATALOG_EXTENSIONS
) - set(TRANSLATABLE_EXTENSIONS)

# همهٔ پسوندهای شناسایی‌شدهٔ تراریوم (بیش از ۷۴۰ مورد) و زیرمجموعهٔ متن‌دار آن.
SUPPORTED_EXTENSIONS = sorted(CATALOG_EXTENSIONS)
TRANSLATABLE_EXTENSIONS = sorted(set(TRANSLATABLE_EXTENSIONS) | set(CATALOG_TRANSLATABLE_EXTENSIONS))

# فقط همین‌ها در فهرست نمایشی دیده می‌شوند؛ بقیه با جست‌وجو پیدا می‌شوند.
DISPLAY_FORMAT_GROUPS = DISPLAY_GROUPS


def format_stats() -> tuple[int, int]:
    """(تعداد کل پسوندهای شناسایی‌شده، تعداد پسوندهای متن‌دار قابل ترجمه)"""
    return len(SUPPORTED_EXTENSIONS), len(TRANSLATABLE_EXTENSIONS)


def search_formats(query: str, limit: int = 120) -> list[str]:
    """جست‌وجو در کل کاتالوگ پسوندها (برای باکس جست‌وجوی رابط کاربری)."""
    return search_extensions(query, limit=limit)


def is_known_extension(ext: str) -> bool:
    return ext.lower() in SUPPORTED_EXTENSIONS


def extension_of(file_path: str | Path) -> str:
    """پسوند فایل؛ برای فایل‌هایی مثل ‎.gitignore و ‎.env هم درست کار می‌کند."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix:
        return suffix
    name = path.name.lower()
    if name.startswith(".") and name.count(".") == 1:
        return name
    return ""


def is_translatable_extension(ext: str) -> bool:
    ext = ext.lower()
    return ext in TRANSLATABLE_EXTENSIONS or ext in SUBTITLE_EXTENSIONS


def is_non_text_extension(ext: str) -> bool:
    """پسوندی که در کاتالوگ هست ولی متن ندارد (عکس/صدا/ویدیو/باینری)."""
    return ext.lower() in NON_TEXT_EXTENSIONS


def human_format(ext: str) -> str:
    """نام خوانا برای پیام‌های رابط کاربری."""
    ext = ext.lower()
    table = {
        ".pdf": "PDF", ".epub": "EPUB", ".txt": "متن ساده",
        ".docx": "سند Word", ".doc": "سند Word قدیمی", ".rtf": "Rich Text",
        ".odt": "سند OpenDocument", ".pptx": "ارائه PowerPoint",
        ".xlsx": "صفحه‌گسترده Excel", ".html": "صفحه وب", ".htm": "صفحه وب",
        ".xml": "XML", ".fb2": "کتاب FB2", ".mobi": "کتاب MOBI",
        ".azw3": "کتاب AZW3", ".zip": "آرشیو ZIP", ".srt": "زیرنویس SRT",
        ".vtt": "زیرنویس WebVTT", ".ass": "زیرنویس ASS",
        ".mp4": "ویدیو", ".mkv": "ویدیو", ".avi": "ویدیو", ".mov": "ویدیو",
        ".mp3": "فایل صوتی", ".wav": "فایل صوتی", ".m4a": "فایل صوتی",
        ".jpg": "تصویر", ".jpeg": "تصویر", ".png": "تصویر",
    }
    return table.get(ext, f"فایل {ext or 'بدون پسوند'}")
