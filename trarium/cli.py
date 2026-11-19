"""CLI اصلی تراریوم."""

import os
import sys
import click
from rich.console import Console
from rich.panel import Panel

from trarium import __version__
from trarium.cache import CachedTranslator
from trarium.extractors import get_extractor
from trarium.translators import get_translator
from trarium.translators.openrouter import FREE_MODELS
from trarium.processors.pipeline import TranslationPipeline
from trarium.output import save_as_text, save_as_pdf, save_as_epub

console = Console()


def print_banner():
    banner = f"""
[bold cyan]
╔══════════════════════════════════════════╗
║       🌿 تراریوم (Trarium) 🌿          ║
║   ترجمه خودکار کتاب با هوش مصنوعی      ║
║          نسخه {__version__}                      ║
╚══════════════════════════════════════════╝
[/]"""
    console.print(banner)


def parse_skip_pages(skip_str):
    """Parse skip pages string."""
    result = set()
    if not skip_str:
        return result
    for part in skip_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start), int(end)
            if start < 1 or end < 1 or start > end:
                raise ValueError(f"بازه نامعتبر: {part}")
            for i in range(start, end + 1):
                result.add(i - 1)
        else:
            page = int(part)
            if page < 1:
                raise ValueError(f"شماره صفحه نامعتبر: {part}")
            result.add(page - 1)
    return result


@click.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--to", "-t", default="fa", help="زبان مقصد")
@click.option("--provider", "-p", type=click.Choice(["openrouter", "gemini", "groq", "router9", "openai_compatible"]),
              default="openrouter", help="ارائه‌دهنده هوش مصنوعی")
@click.option("--api-key", "-k", default=None, help="API Key")
@click.option("--model", "-m", default=None, help="نام مدل")
@click.option("--base-url", default=None, help="Base URL برای providerهای OpenAI-compatible")
@click.option("--ocr", is_flag=True, help="OCR برای PDF اسکن‌شده")
@click.option("--ocr-method", type=click.Choice(["auto", "paddleocr", "surya", "easyocr", "tesseract", "openrouter"]), default="auto", show_default=True)
@click.option("--output", "-o", default=None, help="مسیر فایل خروجی")
@click.option("--format", "-f", "out_format", type=click.Choice(["txt", "pdf", "epub"]),
              default="txt", help="فرمت خروجی")
@click.option("--chunk-size", default=15000, help="حداکثر اندازه هر بخش")
@click.option("--delay", default=0.0, help="تاخیر بین درخواست‌ها")
@click.option("--workers", "-w", default=5, help="تعداد درخواست‌های موازی")
@click.option("--skip-pages", default=None, help="شماره صفحات برای رد کردن (مثال: 1,5,10-15)")
@click.version_option(__version__)
def main(file_path, to, provider, api_key, model, base_url, ocr, ocr_method, output, out_format, chunk_size, delay, workers, skip_pages):
    """تراریوم - ترجمه خودکار کتاب 🚀"""
    print_banner()

    if provider == "openrouter":
        if not api_key:
            api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            console.print("[red]❌ OpenRouter API Key لازمه![/]")
            console.print("  https://openrouter.ai/keys")
            sys.exit(1)
        if not model:
            model = FREE_MODELS["Auto (پیشنهادی)"]
    elif provider == "gemini":
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            console.print("[red]❌ Google API Key لازمه![/]")
            console.print("  https://aistudio.google.com/apikey")
            sys.exit(1)
        if not model:
            model = "gemini-3.5-flash-lite"
    elif provider == "groq":
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        model = model or "openai/gpt-oss-120b"
    elif provider == "openai_compatible":
        api_key = api_key or os.environ.get("TRARIUM_API_KEY")
        model = model or os.environ.get("TRARIUM_MODEL")
        base_url = base_url or os.environ.get("TRARIUM_BASE_URL")
        if not base_url:
            console.print("[red]❌ --base-url برای OpenAI-compatible لازمه![/]")
            sys.exit(1)
    else:  # router9
        api_key = api_key or os.environ.get("ROUTER9_API_KEY") or "9router"
        model = model or "auto"

    if chunk_size <= 0:
        raise click.BadParameter("chunk-size باید بزرگ‌تر از صفر باشد")
    if workers < 1:
        raise click.BadParameter("workers باید حداقل 1 باشد")
    if delay < 0:
        raise click.BadParameter("delay نمی‌تواند منفی باشد")

    # پارس صفحات رد شده
    user_skip = parse_skip_pages(skip_pages) if skip_pages else set()
    if user_skip:
        console.print(f"  🚫 صفحات رد شده: {sorted([i+1 for i in user_skip])}")

    console.print(Panel("[bold]📄 مرحله ۱: استخراج متن[/]", style="cyan"))

    try:
        extractor = get_extractor(file_path, use_ocr=ocr, ocr_method=ocr_method)
        console.print(f"  📁 فایل: {file_path}")
        console.print(f"  🔧 روش: {extractor.__class__.__name__}")
        book = extractor.extract(file_path)
        console.print(f"  📖 عنوان: {book.title}")
        console.print(f"  ✍️  نویسنده: {book.author}")
        console.print(f"  📊 بخش‌ها: {len(book.chapters)}")
        console.print(f"  📏 کاراکتر: {book.total_chars:,}")
    except Exception as e:
        console.print(f"[red]❌ خطا در استخراج: {e}[/]")
        sys.exit(1)

    if book.total_chars == 0:
        console.print("[red]❌ متنی استخراج نشد![/]")
        sys.exit(1)

    console.print(Panel(f"[bold]🌍 مرحله ۲: ترجمه به {to} ({provider})[/]", style="cyan"))

    try:
        translator = CachedTranslator(get_translator(provider, api_key=api_key, model_name=model, base_url=base_url, provider_name="OpenAI-compatible") if provider == "openai_compatible" else get_translator(provider, api_key=api_key, model_name=model), provider, model)

        # فیلتر کردن فصل‌های رد شده
        if user_skip:
            active_chapters = [ch for i, ch in enumerate(book.chapters) if i not in user_skip]
            if not active_chapters:
                console.print("[red]❌ همه فصل‌ها رد شدن![/]")
                sys.exit(1)
            book.chapters = active_chapters
            book.total_chars = sum(len(ch.get("content", "")) for ch in active_chapters)
            console.print(f"  📊 بخش‌های فعال: {len(active_chapters)}")

        pipeline = TranslationPipeline(
            translator=translator,
            target_lang=to,
            chunk_size=chunk_size,
            delay_between_requests=delay,
            max_workers=workers,
        )
        result = pipeline.run(book)
    except Exception as e:
        console.print(f"[red]❌ خطا در ترجمه: {e}[/]")
        sys.exit(1)

    console.print(Panel("[bold]💾 مرحله ۳: ذخیره خروجی[/]", style="cyan"))

    try:
        if out_format == "epub":
            save_as_epub(result, output)
        elif out_format == "pdf":
            save_as_pdf(result, output)
        else:
            save_as_text(result, output)
        console.print("\n[bold green]🎉 تراریوم تموم شد![/]\n")
    except Exception as e:
        console.print(f"[red]❌ خطا در ذخیره: {e}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
