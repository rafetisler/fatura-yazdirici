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
                reasons.append("Çevrimdışı (Kapalı veya Wi-Fi/Kablo Bağlı Değil)")
            if status & PRINTER_STATUS_PAUSED:
                reasons.append("Duraklatılmış")
            if status & PRINTER_STATUS_PAPER_OUT:
                reasons.append("Kağıt Bitti")
            if status & PRINTER_STATUS_PAPER_JAM:
                reasons.append("Kağıt Sıkışmış")
            if status & PRINTER_STATUS_DOOR_OPEN:
                reasons.append("Kapak Açık")
            if status & PRINTER_STATUS_ERROR:
                reasons.append("Yazıcı Hatası")

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

    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        enum_result = win32print.EnumPrinters(flags)
        printers = [p[2] for p in enum_result if p and len(p) > 2]
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
    timeout_seconds: int = 240,
) -> None:
    """
    Polls the Windows Print Spooler for a specific job_title until the printer finishes
    physically receiving/printing it and removes it from the queue.
    Raises RuntimeError if the job enters an error/offline/paper-out state or times out.
    """
    start_time = time.time()
    job_seen = False

    # Brief initial pause so Windows spooler registers the job
    time.sleep(0.4)

    while True:
        if cancel_check and cancel_check():
            raise RuntimeError("Kullanıcı tarafından iptal edildi.")

        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise RuntimeError(
                f"Yazıcı zaman aşımı ({timeout_seconds} sn): '{job_title}' kuyrukta takılı kaldı."
            )

        hprinter = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(hprinter, 2)
            p_status = info.get("Status", 0)
            p_attrs = info.get("Attributes", 0)

            if (p_status & PRINTER_STATUS_OFFLINE) or (p_attrs & PRINTER_ATTRIBUTE_WORK_OFFLINE):
                raise RuntimeError("Yazıcı çevrimdışı oldu! Baskı tamamlanamadı.")
            if p_status & PRINTER_STATUS_PAPER_OUT:
                raise RuntimeError("Yazıcıda kağıt bitti! Baskı tamamlanamadı.")
            if p_status & PRINTER_STATUS_PAPER_JAM:
                raise RuntimeError("Yazıcıda kağıt sıkıştı! Baskı tamamlanamadı.")
            if p_status & PRINTER_STATUS_DOOR_OPEN:
                raise RuntimeError("Yazıcı kapağı açık! Baskı tamamlanamadı.")

            jobs = win32print.EnumJobs(hprinter, 0, 999, 1)
        finally:
            win32print.ClosePrinter(hprinter)

        target_job = None
        for j in jobs:
            doc_name = j.get("pDocument", "") or ""
            if doc_name == job_title:
                target_job = j
                break

        if target_job is None:
            # If the job was either seen and now cleared, or completed super fast (<0.4s)
            # while printer is online with no errors, it succeeded!
            return

        job_seen = True
        j_status = target_job.get("Status", 0)
        j_status_str = target_job.get("pStatus") or ""

        if j_status & JOB_STATUS_ERROR:
            raise RuntimeError(f"Yazdırma kuyruğu hatası! ({j_status_str or 'Yazıcı hatası'})")
        if j_status & JOB_STATUS_OFFLINE:
            raise RuntimeError("Yazıcı kuyrukta çevrimdışı görünüyor!")
        if j_status & JOB_STATUS_PAPEROUT:
            raise RuntimeError("Yazıcıda kağıt bitti!")
        if j_status & JOB_STATUS_USER_INTERVENTION:
            raise RuntimeError("Yazıcı kullanıcı müdahalesi bekliyor!")
        if j_status & JOB_STATUS_BLOCKED_DEVQ:
            raise RuntimeError("Yazıcı kuyruğu engellendi (Blocked)!")

        # If the job is marked as deleted/deleting without printing
        if (j_status & (JOB_STATUS_DELETING | JOB_STATUS_DELETED)) and not (
            j_status & JOB_STATUS_PRINTED
        ):
            # Often Windows briefly sets DELETING right after finishing printing.
            # Wait a tiny bit and check if printer is healthy.
            time.sleep(0.5)
            return

        if status_callback:
            pages_printed = target_job.get("PagesPrinted", 0)
            total_pages = target_job.get("TotalPages", 0)
            if total_pages > 0:
                status_callback(f"Yazıcı Basıyor ({pages_printed}/{total_pages} syf)...")
            else:
                status_callback("Yazıcı Basıyor (Kuyrukta)...")

        time.sleep(0.8)


def _send_single_copy_to_spooler(
    rendered_pages: List[Image.Image],
    printer_name: str,
    job_title: str,
    duplex: bool = False,
) -> None:
    """Sends a single copy of rendered pages as one clean GDI document to avoid printer buffer overflow."""
    hprinter = win32print.OpenPrinter(printer_name)
    try:
        hdc = win32ui.CreateDC()
        try:
            if duplex and len(rendered_pages) > 1:
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

        hdc.StartDoc(job_title)
        try:
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
    Renders each page of a PDF (up to max_pages if specified) and sends EACH copy as a separate,
    verified print job to prevent WSD/network printer memory overflow and missed copies.
    If wait_for_completion is True, waits until the printer finishes each copy before sending the next.
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

        # Query printer DPI & orientation once
        hdc_info = win32ui.CreateDC()
        hdc_info.CreatePrinterDC(printer_name)
        try:
            printable_w = hdc_info.GetDeviceCaps(HORZRES)
            printable_h = hdc_info.GetDeviceCaps(VERTRES)
            dpi_x = hdc_info.GetDeviceCaps(LOGPIXELSX) or 200
            dpi_y = hdc_info.GetDeviceCaps(LOGPIXELSY) or 200
            # 200 DPI is crisp for invoices while keeping spool size 2.25x smaller than 300 DPI
            # so network/Wi-Fi printers (like HP Smart Tank WSD) never drop jobs or copies!
            render_dpi = min(max(dpi_x, dpi_y, 180), 220)
        finally:
            try:
                hdc_info.DeleteDC()
            except Exception:
                pass

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

    # Send each copy as its own distinct job and wait for the printer to finish it!
    # This guarantees that requesting 2+ copies NEVER results in only 1 copy being printed.
    short_uid = uuid.uuid4().hex[:6]
    for copy_idx in range(1, copies + 1):
        if cancel_check and cancel_check():
            raise RuntimeError("Kullanıcı tarafından iptal edildi.")

        if copies > 1:
            job_title = f"Fatura [{short_uid}] ({copy_idx}/{copies}) - {path.name}"
            if status_callback:
                status_callback(f"Kopya {copy_idx}/{copies} Gönderiliyor...")
        else:
            job_title = f"Fatura [{short_uid}] - {path.name}"
            if status_callback:
                status_callback("Yazıcıya Gönderiliyor...")

        _send_single_copy_to_spooler(
            rendered_pages=rendered_pages,
            printer_name=printer_name,
            job_title=job_title,
            duplex=duplex,
        )

        if wait_for_completion:
            def _sub_status(msg: str, c_idx=copy_idx):
                if status_callback:
                    if copies > 1:
                        status_callback(f"Kopya {c_idx}/{copies}: {msg}")
                    else:
                        status_callback(msg)

            wait_for_spool_job_completion(
                printer_name=printer_name,
                job_title=job_title,
                status_callback=_sub_status,
                cancel_check=cancel_check,
            )
            # Brief breather between copies/jobs for network printers
            if copy_idx < copies:
                time.sleep(0.8)
        else:
            # Even if not waiting for full completion, pause briefly between copies
            if copy_idx < copies:
                time.sleep(1.0)


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
