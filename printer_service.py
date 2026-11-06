from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple
import shutil
import time
import uuid
import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageWin
import win32con
import win32print
import win32ui


# Windows GDI DeviceCaps constants
HORZRES = 8
VERTRES = 10
LOGPIXELSX = 88
LOGPIXELSY = 90
PHYSICALWIDTH = 110
PHYSICALHEIGHT = 111
PHYSICALOFFSETX = 112
PHYSICALOFFSETY = 113

# Windows Printer Status flags
PRINTER_STATUS_PAUSED = 0x00000001
PRINTER_STATUS_ERROR = 0x00000002
PRINTER_STATUS_PAPER_JAM = 0x00000008
PRINTER_STATUS_PAPER_OUT = 0x00000010
PRINTER_STATUS_OFFLINE = 0x00000080
PRINTER_STATUS_DOOR_OPEN = 0x00400000
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400

# Windows Print Job Status flags
JOB_STATUS_PAUSED = 0x00000001
JOB_STATUS_ERROR = 0x00000002
JOB_STATUS_DELETING = 0x00000004
JOB_STATUS_SPOOLING = 0x00000008
JOB_STATUS_PRINTING = 0x00000010
JOB_STATUS_OFFLINE = 0x00000020
JOB_STATUS_PAPEROUT = 0x00000040
JOB_STATUS_PRINTED = 0x00000080
JOB_STATUS_DELETED = 0x00000100
JOB_STATUS_BLOCKED_DEVQ = 0x00000200
JOB_STATUS_USER_INTERVENTION = 0x00000400


def check_printer_status(printer_name: str) -> Tuple[bool, str, int]:
    """
    Checks if the selected Windows printer is online and ready, or offline/paused/errored.
    Returns:
        (is_ready, status_description, pending_job_count)
    """
    if not printer_name:
        return False, "Yazıcı seçilmedi", 0

    try:
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(hprinter, 2)
            status = info.get("Status", 0)
            attrs = info.get("Attributes", 0)
            jobs = info.get("cJobs", 0)

            reasons = []
            if (status & PRINTER_STATUS_OFFLINE) or (attrs & PRINTER_ATTRIBUTE_WORK_OFFLINE):
                reasons.append("Çevrimdışı (Kapalı veya Bağlı Değil)")
            if status & PRINTER_STATUS_PAUSED:
                reasons.append("Duraklatılmış")
            if status & PRINTER_STATUS_PAPER_OUT:
                reasons.append("Kağıt Bitti")
            if status & PRINTER_STATUS_PAPER_JAM:
                reasons.append("Kağıt Sıkışmış")
            if status & PRINTER_STATUS_DOOR_OPEN:
                reasons.append("Kapak Açık")

            if reasons:
                return False, " / ".join(reasons), jobs

            return True, "Hazır (Çevrimiçi)", jobs
        finally:
            win32print.ClosePrinter(hprinter)
    except Exception as exc:
        return False, f"Durum okunamadı: {exc}", 0


def get_installed_printers() -> Tuple[List[str], str]:
    """Returns a list of installed printers (local & network) and the default printer name."""
    printers: List[str] = []
    default_printer = ""
    try:
        default_printer = win32print.GetDefaultPrinter()
    except Exception:
        default_printer = ""

    for flags in (
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
        win32print.PRINTER_ENUM_LOCAL,
    ):
        try:
            enum_result = win32print.EnumPrinters(flags)
            for p in enum_result:
                if p and len(p) > 2 and p[2] and p[2] not in printers:
                    printers.append(p[2])
            if printers:
                break
        except Exception:
            continue

    if not printers:
        try:
            import subprocess
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Printer | Select-Object -ExpandProperty Name"],
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=0x08000000,
            )
            for line in out.splitlines():
                name = line.strip()
                if name and name not in printers:
                    printers.append(name)
        except Exception:
            pass

    if default_printer and default_printer not in printers:
        printers.insert(0, default_printer)

    return printers, default_printer


def wait_for_spool_job_completion(
    printer_name: str,
    job_title: str,
    status_callback: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    timeout_seconds: int = 300,
) -> None:
    """
    Waits until the specific job_title is processed by the printer, or until the printer queue
    has room (<= 1 active job) and no physical paper/offline error exists.
    Never raises false errors on normal driver flags like BLOCKED_DEVQ (which HP LaserJet M111w sets while busy).
    """
    start_time = time.time()
    time.sleep(0.6)

    while True:
        if cancel_check and cancel_check():
            raise RuntimeError("Kullanıcı tarafından iptal edildi.")

        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            # Don't fail the job if it was already sent to spooler; just let the user know or proceed
            return

        try:
            hprinter = win32print.OpenPrinter(printer_name)
            try:
                info = win32print.GetPrinter(hprinter, 2)
                p_status = info.get("Status", 0)
                p_attrs = info.get("Attributes", 0)

                if (p_status & PRINTER_STATUS_OFFLINE) or (p_attrs & PRINTER_ATTRIBUTE_WORK_OFFLINE):
                    raise RuntimeError("Yazıcı çevrimdışı oldu!")
                if p_status & PRINTER_STATUS_PAPER_OUT:
                    raise RuntimeError("Yazıcıda kağıt bitti!")
                if p_status & PRINTER_STATUS_PAPER_JAM:
                    raise RuntimeError("Yazıcıda kağıt sıkıştı!")
                if p_status & PRINTER_STATUS_DOOR_OPEN:
                    raise RuntimeError("Yazıcı kapağı açık!")

                jobs = win32print.EnumJobs(hprinter, 0, 999, 1)
            finally:
                win32print.ClosePrinter(hprinter)
        except RuntimeError:
            raise
        except Exception:
            # If EnumJobs transiently fails on network printer, wait briefly and retry
            time.sleep(0.8)
            continue

        target_job = None
        for j in jobs:
            doc_name = j.get("pDocument", "") or ""
            if doc_name == job_title:
                target_job = j
                break

        if target_job is None:
            # Job has left the Windows print queue (completed printing!)
            return

        j_status = target_job.get("Status", 0)

        # Only fail on true physical paper/offline stops
        if j_status & JOB_STATUS_OFFLINE:
            raise RuntimeError("Yazıcı çevrimdışı!")
        if j_status & JOB_STATUS_PAPEROUT:
            raise RuntimeError("Yazıcıda kağıt bitti!")

        # If job is marked deleted/printed, it's done
        if j_status & (JOB_STATUS_PRINTED | JOB_STATUS_DELETED | JOB_STATUS_DELETING):
            time.sleep(0.4)
            return

        if status_callback:
            total_in_queue = len(jobs)
            if total_in_queue > 1:
                status_callback(f"Yazıcı Basıyor (Kuyruk: {total_in_queue})...")
            else:
                status_callback("Yazıcı Basıyor...")

        time.sleep(0.8)


def print_pdf_file(
    pdf_path: str | Path,
    printer_name: str,
    copies: int = 1,
    duplex: bool = False,
    max_pages: int | None = None,
    wait_for_completion: bool = True,
    status_callback: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    """
    Renders each page of a PDF (up to max_pages if specified) and sends ALL requested copies
    explicitly inside a single atomic GDI print document (Page 1..N repeated `copies` times).
    This guarantees that if the document prints, the exact number of copies is ALWAYS printed
    on every printer (HP LaserJet M111w, HP Smart Tank, etc.).
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF bulunamadı: {path}")

    if not printer_name:
        raise ValueError("Yazıcı seçilmedi.")

    copies = max(1, int(copies))

    with fitz.open(path) as doc:
        if len(doc) == 0:
            raise ValueError("PDF dosyası boş (0 sayfa).")

        pages_to_render = len(doc)
        if max_pages is not None and max_pages > 0:
            pages_to_render = min(len(doc), max_pages)

        hprinter = win32print.OpenPrinter(printer_name)
        try:
            hdc = win32ui.CreateDC()
            try:
                if duplex and pages_to_render > 1:
                    try:
                        props = win32print.GetPrinter(hprinter, 2)
                        devmode = props.get("pDevMode")
                        if devmode:
                            devmode.Duplex = win32con.DMDUP_VERTICAL
                            devmode.Fields |= win32con.DM_DUPLEX
                            hdc.CreateDC("WINSPOOL", printer_name, devmode)
                        else:
                            hdc.CreatePrinterDC(printer_name)
                    except Exception:
                        hdc.CreatePrinterDC(printer_name)
                else:
                    hdc.CreatePrinterDC(printer_name)
            except Exception:
                hdc = win32ui.CreateDC()
                hdc.CreatePrinterDC(printer_name)

            printable_w = hdc.GetDeviceCaps(HORZRES)
            printable_h = hdc.GetDeviceCaps(VERTRES)
            dpi_x = hdc.GetDeviceCaps(LOGPIXELSX) or 200
            dpi_y = hdc.GetDeviceCaps(LOGPIXELSY) or 200
            render_dpi = min(max(dpi_x, dpi_y, 180), 220)

            rendered_pages: List[Image.Image] = []
            for page_index in range(pages_to_render):
                page = doc[page_index]
                pix = page.get_pixmap(dpi=render_dpi, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                page_is_landscape = img.width > img.height
                paper_is_landscape = printable_w > printable_h
                if page_is_landscape != paper_is_landscape:
                    img = img.rotate(90, expand=True)

                rendered_pages.append(img)

            short_uid = uuid.uuid4().hex[:6]
            job_title = f"Fatura [{short_uid}] ({copies} Kopya) - {path.name}"

            if status_callback:
                status_callback(f"Yazıcıya Gönderiliyor ({copies} Kopya)...")

            hdc.StartDoc(job_title)
            try:
                # Send all copies inside the same document in collated order:
                # Copy 1 (Page 1..N), Copy 2 (Page 1..N), ...
                # Because it is one atomic document, the printer CANNOT print 1 copy and skip the 2nd!
                for copy_idx in range(1, copies + 1):
                    if cancel_check and cancel_check():
                        raise RuntimeError("Kullanıcı tarafından iptal edildi.")

                    for img in rendered_pages:
                        hdc.StartPage()

                        scale = min(printable_w / img.width, printable_h / img.height)
                        target_w = int(img.width * scale)
                        target_h = int(img.height * scale)
                        offset_x = (printable_w - target_w) // 2
                        offset_y = (printable_h - target_h) // 2

                        dib = ImageWin.Dib(img)
                        dib.draw(
                            hdc.GetHandleOutput(),
                            (offset_x, offset_y, offset_x + target_w, offset_y + target_h),
                        )
                        hdc.EndPage()

                hdc.EndDoc()
            except Exception:
                try:
                    hdc.AbortDoc()
                except Exception:
                    pass
                raise
            finally:
                try:
                    hdc.DeleteDC()
                except Exception:
                    pass
        finally:
            win32print.ClosePrinter(hprinter)

    if wait_for_completion:
        wait_for_spool_job_completion(
            printer_name=printer_name,
            job_title=job_title,
            status_callback=status_callback,
            cancel_check=cancel_check,
        )
    else:
        # Small breather so spooler registers jobs cleanly in order
        time.sleep(0.4)


def move_to_printed_folder(
    pdf_path: str | Path,
    use_date_subfolder: bool = False,
    archive_folder_name: str = "Yazdırılanlar",
) -> Path:
    """
    Moves a printed PDF file into the 'Yazdırılanlar' folder inside the same directory.
    If a file with the same name already exists, appends a counter so nothing is overwritten.
    """
    src = Path(pdf_path)
    if not src.exists():
        raise FileNotFoundError(f"Taşınacak dosya bulunamadı: {src}")

    target_dir = src.parent / archive_folder_name
    if use_date_subfolder:
        date_str = datetime.now().strftime("%Y-%m-%d")
        target_dir = target_dir / date_str

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / src.name

    if dest.exists():
        stem = src.stem
        suffix = src.suffix
        counter = 1
        while True:
            candidate = target_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            counter += 1

    shutil.move(str(src), str(dest))
    return dest
