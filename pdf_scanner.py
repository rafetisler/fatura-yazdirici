from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
import os
import re
import unicodedata
import pymupdf as fitz  # PyMuPDF


def normalize_turkish_text(text: str) -> str:
    """Normalizes Turkish characters and whitespace for reliable keyword matching."""
    if not text:
        return ""
    replacements = {
        "İ": "i",
        "I": "i",
        "ı": "i",
        "Ş": "s",
        "ş": "s",
        "Ğ": "g",
        "ğ": "g",
        "Ü": "u",
        "ü": "u",
        "Ö": "o",
        "ö": "o",
        "Ç": "c",
        "ç": "c",
    }
    for src, target in replacements.items():
        text = text.replace(src, target)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Keywords indicating the invoice summary / grand total block has concluded
TOTAL_KEYWORDS = [
    "odenecek tutar",
    "genel toplam",
    "vergiler dahil toplam",
    "kdv dahil toplam",
    "kdv dahil tutar",
    "kdv dahil",
    "toplam tutar",
    "fatura toplami",
    "hesaplanan kdv",
    "mal hizmet toplam tutari",
    "ödenecek tutar",
]

# Keywords typically found in overflow footer pages (bank info, notes, legal disclaimers)
FOOTER_OVERFLOW_KEYWORDS = [
    "iban",
    "banka",
    "hesap no",
    "sube",
    "swift",
    "mersis",
    "ticaret sicil",
    "v.d.",
    "vergi dairesi",
    "e-arsiv izni",
    "e-fatura",
    "isbu fatura",
    "itiraz",
]


@dataclass
class PdfInvoiceItem:
    file_path: Path
    file_name: str
    page_count: int
    printable_pages: int  # How many pages will actually be printed (e.g., 1 if page 2 is redundant)
    copies: int = 1
    selected: bool = True
    status: str = "Bekliyor"
    error_message: str = ""
    page2_is_redundant: bool = False
    page2_reason: str = ""
    page2_preview: str = ""
    force_print_all: bool = False  # User can toggle between 'Sadece 1. Sayfa' and 'Tüm Sayfalar'

    @property
    def effective_pages_to_print(self) -> int:
        if self.page_count <= 0:
            return 0
        if self.force_print_all:
            return self.page_count
        return self.printable_pages

    @property
    def page_badge_text(self) -> str:
        if self.page_count <= 0:
            return "Hatalı PDF"
        elif self.page_count == 1:
            return "1 Sayfa"
        elif self.page2_is_redundant:
            return f"{self.page_count} Sayfa (2.Sayfa Gereksiz)"
        else:
            return f"{self.page_count} Sayfa (Tamamı Dolu)"

    @property
    def is_multi_page(self) -> bool:
        return self.page_count >= 2


def analyze_pdf_pages(doc: fitz.Document) -> tuple[bool, int, str, str]:
    """
    Analyzes a multi-page invoice PDF to check if page 1 already contains the Grand Total / KDV Dahil
    and page 2+ only contains bank/footer overflow.

    Returns:
        (page2_is_redundant, recommended_printable_pages, reason_summary, page2_text_preview)
    """
    page_count = len(doc)
    if page_count <= 1:
        return False, page_count, "Tek sayfalık fatura", ""

    page1_raw = doc[0].get_text("text") or ""
    page2_raw = doc[1].get_text("text") or ""

    page1_norm = normalize_turkish_text(page1_raw)
    page2_norm = normalize_turkish_text(page2_raw)

    # Clean preview of page 2 for user tooltip/inspection
    page2_lines = [line.strip() for line in page2_raw.splitlines() if line.strip()]
    page2_preview = " | ".join(page2_lines[:6])
    if len(page2_preview) > 180:
        page2_preview = page2_preview[:177] + "..."

    # 1. Check if page 2 is completely empty or almost empty
    if len(page2_norm) < 15:
        return (
            True,
            1,
            "2. sayfa boş veya sadece çizgi/logo taşması var",
            page2_preview or "(2. sayfada metin yok)",
        )

    # 2. Check if Grand Total / KDV Dahil keywords exist on Page 1 vs Page 2
    p1_matched_totals = [kw for kw in TOTAL_KEYWORDS if kw in page1_norm]
    p2_matched_totals = [kw for kw in TOTAL_KEYWORDS if kw in page2_norm]

    # Strongest signals that totals are concluded on Page 1
    strong_total_keywords = [
        "odenecek tutar",
        "genel toplam",
        "vergiler dahil toplam",
        "kdv dahil",
        "toplam tutar",
        "fatura toplami",
    ]
    p1_has_strong_total = any(kw in page1_norm for kw in strong_total_keywords)
    p2_has_strong_total = any(kw in page2_norm for kw in strong_total_keywords)

    # Also check "yalniz ... tl" / "yalnizca" (amount in words, always printed right under totals)
    p1_has_amount_in_words = bool(
        re.search(r"\byalniz\b.*(tl|lira|kurus|usd|eur)", page1_norm)
    )

    p2_has_footer_kw = [kw for kw in FOOTER_OVERFLOW_KEYWORDS if kw in page2_norm]

    # Case A: Page 1 has strong total (or amount in words) AND Page 2 does NOT have strong total
    if (p1_has_strong_total or p1_has_amount_in_words) and not p2_has_strong_total:
        if p2_has_footer_kw:
            reason = "Genel Toplam/KDV 1. sayfada; 2. sayfada sadece Banka/IBAN/Alt bilgi var"
        else:
            reason = "Genel Toplam/KDV Dahil rakam 1. sayfada tamamlanmış"
        return True, 1, reason, page2_preview

    # Case B: Both Page 1 and Page 2 mention some total keywords, or Page 1 has general total keywords
    # while Page 2 is very short and mostly bank/IBAN info
    if p1_matched_totals and not p2_has_strong_total and len(page2_norm) < 600:
        return (
            True,
            1,
            "Toplam bilgisi 1. sayfada; 2. sayfada kısa alt bilgi taşması var",
            page2_preview,
        )

    # Otherwise Page 2 likely contains actual invoice items or the grand total table itself
    if p2_has_strong_total:
        reason = "Genel Toplam / Ödenecek Tutar 2. sayfada yer alıyor (2. sayfa gerekli)"
    else:
        reason = "Fatura kalemleri 2. sayfada devam ediyor (2. sayfa gerekli)"

    return False, page_count, reason, page2_preview


def analyze_single_pdf_file(
    pdf_path: str | Path,
    base_folder: Path | None = None,
    default_copies: int = 1,
    smart_skip_redundant_pages: bool = True,
) -> PdfInvoiceItem:
    """Analyzes a single PDF file and returns a PdfInvoiceItem."""
    pdf_p = Path(pdf_path)
    try:
        if base_folder and pdf_p.parent != base_folder and pdf_p.is_relative_to(base_folder):
            rel_name = str(pdf_p.relative_to(base_folder))
        else:
            rel_name = pdf_p.name
    except Exception:
        rel_name = pdf_p.name

    try:
        with fitz.open(pdf_p) as doc:
            page_count = len(doc)
            is_redundant, rec_pages, reason, p2_preview = analyze_pdf_pages(doc)

        printable_pages = (
            rec_pages if (smart_skip_redundant_pages and is_redundant) else page_count
        )

        return PdfInvoiceItem(
            file_path=pdf_p,
            file_name=rel_name,
            page_count=page_count,
            printable_pages=printable_pages,
            copies=default_copies,
            selected=True,
            status="Bekliyor",
            page2_is_redundant=is_redundant,
            page2_reason=reason,
            page2_preview=p2_preview,
            force_print_all=not (smart_skip_redundant_pages and is_redundant),
        )
    except Exception as exc:
        return PdfInvoiceItem(
            file_path=pdf_p,
            file_name=rel_name,
            page_count=0,
            printable_pages=0,
            copies=default_copies,
            selected=False,
            status="Okuma Hatası",
            error_message=str(exc),
        )


def scan_folder_for_pdfs(
    folder_path: str | Path,
    default_copies: int = 1,
    smart_skip_redundant_pages: bool = True,
    include_subfolders: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> List[PdfInvoiceItem]:
    """
    Scans the given folder for PDF files (including subfolders except 'Yazdırılanlar'),
    counts pages, and detects redundant 2nd pages using multi-threaded parallel analysis.
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return []

    raw_files: List[Path] = []

    if include_subfolders:
        # Fast os.walk that prunes 'Yazdırılanlar' directories immediately without entering them
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d.lower() != "yazdırılanlar"]
            for fname in files:
                if fname.lower().endswith(".pdf"):
                    raw_files.append(Path(root) / fname)
    else:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() == ".pdf":
                raw_files.append(p)

    pdf_files = sorted(raw_files, key=lambda x: str(x).lower())
    total = len(pdf_files)
    if total == 0:
        return []

    results: List[Optional[PdfInvoiceItem]] = [None] * total

    def _worker(idx_and_path: tuple[int, Path]) -> tuple[int, PdfInvoiceItem]:
        idx, p = idx_and_path
        item = analyze_single_pdf_file(
            pdf_path=p,
            base_folder=folder,
            default_copies=default_copies,
            smart_skip_redundant_pages=smart_skip_redundant_pages,
        )
        return idx, item

    max_workers = min(8, (os.cpu_count() or 4))
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, item in executor.map(_worker, enumerate(pdf_files)):
            results[idx] = item
            completed += 1
            if progress_callback:
                progress_callback(completed, total, item.file_name)

    return [r for r in results if r is not None]
