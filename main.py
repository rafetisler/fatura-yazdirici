import json
import os
import sys
from pathlib import Path
from typing import List

import pymupdf as fitz  # PyMuPDF
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdf_scanner import (
    PdfInvoiceItem,
    analyze_single_pdf_file,
    scan_folder_for_pdfs,
)
from printer_service import (
    check_printer_status,
    get_installed_printers,
    move_to_printed_folder,
    print_pdf_file,
)


def _get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_FILE = _get_app_dir() / "settings.json"


class PrintWorker(QThread):
    """Background thread to print selected PDFs without freezing the UI."""

    progress_changed = pyqtSignal(int, int, str)  # current_idx, total, filename
    item_status_updated = pyqtSignal(int, str, str)  # row_index, status_text, error_msg
    finished_batch = pyqtSignal(int, int)  # success_count, fail_count

    def __init__(
        self,
        jobs: List[tuple[int, PdfInvoiceItem]],
        printer_name: str,
        duplex_multipage: bool,
        auto_move: bool,
        use_date_subfolder: bool,
        wait_for_spooler: bool = True,
    ):
        super().__init__()
        self.jobs = jobs
        self.printer_name = printer_name
        self.duplex_multipage = duplex_multipage
        self.auto_move = auto_move
        self.use_date_subfolder = use_date_subfolder
        self.wait_for_spooler = wait_for_spooler
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.jobs)
        success_count = 0
        fail_count = 0

        for idx, (row_index, item) in enumerate(self.jobs, start=1):
            if self._cancelled:
                break

            self.progress_changed.emit(idx, total, item.file_name)
            self.item_status_updated.emit(row_index, "Yazıcıya Gönderiliyor...", "")

            try:
                pages_to_print = item.effective_pages_to_print
                use_duplex = self.duplex_multipage and pages_to_print >= 2

                def on_status(msg: str, r=row_index):
                    self.item_status_updated.emit(r, msg, "")

                print_pdf_file(
                    pdf_path=item.file_path,
                    printer_name=self.printer_name,
                    copies=item.copies,
                    duplex=use_duplex,
                    max_pages=pages_to_print,
                    wait_for_completion=self.wait_for_spooler,
                    status_callback=on_status,
                    cancel_check=lambda: self._cancelled,
                )

                if self.auto_move:
                    moved_dest = move_to_printed_folder(
                        pdf_path=item.file_path,
                        use_date_subfolder=self.use_date_subfolder,
                    )
                    item.file_path = moved_dest
                    self.item_status_updated.emit(
                        row_index, "Basıldı & Taşındı", ""
                    )
                else:
                    self.item_status_updated.emit(row_index, "Basıldı", "")

                success_count += 1
            except Exception as exc:
                fail_count += 1
                self.item_status_updated.emit(row_index, "Hata!", str(exc))

        self.finished_batch.emit(success_count, fail_count)


class StatCard(QFrame):
    """Modern summary card for statistics at the top of the dashboard."""

    def __init__(self, title: str, value: str, accent_color: str = "#2563EB"):
        super().__init__()
        self.setObjectName("StatCard")
        self.setStyleSheet(
            f"""
            QFrame#StatCard {{
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-left: 5px solid {accent_color};
                border-radius: 8px;
                padding: 8px 12px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 700;")

        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(
            f"color: {accent_color}; font-size: 18px; font-weight: 700;"
        )

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.value_lbl)

    def set_value(self, value: str):
        self.value_lbl.setText(value)


class PdfPreviewPanel(QFrame):
    """Built-in fast PDF page viewer with page navigation, zoom, and page-2 status banner."""

    def __init__(self):
        super().__init__()
        self.setObjectName("PreviewPanel")
        self.setStyleSheet(
            """
            QFrame#PreviewPanel {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            """
        )
        self.current_item: PdfInvoiceItem | None = None
        self.current_page_idx: int = 0
        self.zoom_factor: float = 1.15

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Header row: Title + Open Externally button
        header_row = QHBoxLayout()
        self.lbl_title = QLabel("👁️ Hızlı Fatura Önizleme")
        self.lbl_title.setStyleSheet(
            "font-weight: 700; font-size: 13px; color: #0F172A; border: none;"
        )
        self.btn_open_ext = QPushButton("↗ Dışarıda Aç")
        self.btn_open_ext.setToolTip("Varsayılan PDF okuyucuda tam ekran aç")
        self.btn_open_ext.clicked.connect(self.open_externally)
        self.btn_open_ext.setEnabled(False)

        header_row.addWidget(self.lbl_title, stretch=1)
        header_row.addWidget(self.btn_open_ext)
        layout.addLayout(header_row)

        # Smart analysis banner
        self.lbl_banner = QLabel("Tablodan bir faturaya tıklayarak içeriğini burada görebilirsiniz.")
        self.lbl_banner.setWordWrap(True)
        self.lbl_banner.setStyleSheet(
            "background-color: #F1F5F9; color: #475569; font-size: 12px; font-weight: 600; padding: 6px 8px; border-radius: 6px;"
        )
        layout.addWidget(self.lbl_banner)

        # Scroll area for rendered PDF page image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setStyleSheet(
            "QScrollArea { background-color: #E2E8F0; border: 1px solid #CBD5E1; border-radius: 6px; }"
        )

        self.lbl_image = QLabel("📄\n\nÖnizleme için listeden bir fatura seçin\nveya PDF dosyalarını buraya sürükleyip bırakın")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 600; padding: 24px;")
        self.scroll_area.setWidget(self.lbl_image)
        layout.addWidget(self.scroll_area, stretch=1)

        # Footer navigation & zoom controls
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self.btn_prev = QPushButton("◀ Önceki")
        self.btn_prev.clicked.connect(self.prev_page)
        self.btn_prev.setEnabled(False)

        self.lbl_page_info = QLabel("Sayfa: - / -")
        self.lbl_page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_page_info.setStyleSheet("font-weight: 700; font-size: 12px; color: #1E293B;")

        self.btn_next = QPushButton("Sonraki ▶")
        self.btn_next.clicked.connect(self.next_page)
        self.btn_next.setEnabled(False)

        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.setToolTip("Küçült")
        self.btn_zoom_out.setFixedWidth(44)
        self.btn_zoom_out.clicked.connect(self.zoom_out)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Büyüt")
        self.btn_zoom_in.setFixedWidth(44)
        self.btn_zoom_in.clicked.connect(self.zoom_in)

        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.lbl_page_info, stretch=1)
        nav_row.addWidget(self.btn_next)
        nav_row.addWidget(self.btn_zoom_out)
        nav_row.addWidget(self.btn_zoom_in)

        layout.addLayout(nav_row)

    def clear_preview(self):
        self.current_item = None
        self.current_page_idx = 0
        self.lbl_title.setText("👁️ Hızlı Fatura Önizleme")
        self.lbl_banner.setText("Tablodan bir faturaya tıklayarak içeriğini burada görebilirsiniz.")
        self.lbl_banner.setStyleSheet(
            "background-color: #F1F5F9; color: #475569; font-size: 12px; font-weight: 600; padding: 6px 8px; border-radius: 6px;"
        )
        self.lbl_image.setPixmap(QPixmap())
        self.lbl_image.setText(
            "📄\n\nÖnizleme için listeden bir fatura seçin\nveya PDF dosyalarını buraya sürükleyip bırakın"
        )
        self.lbl_page_info.setText("Sayfa: - / -")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_open_ext.setEnabled(False)

    def load_invoice_item(self, item: PdfInvoiceItem):
        self.current_item = item
        self.current_page_idx = 0
        self.btn_open_ext.setEnabled(item.file_path.exists())
        self.lbl_title.setText(f"👁️ {item.file_name}")
        self._render_current_page()

    def prev_page(self):
        if self.current_item and self.current_page_idx > 0:
            self.current_page_idx -= 1
            self._render_current_page()

    def next_page(self):
        if self.current_item and self.current_page_idx + 1 < self.current_item.page_count:
            self.current_page_idx += 1
            self._render_current_page()

    def zoom_in(self):
        self.zoom_factor = min(2.5, self.zoom_factor + 0.2)
        self._render_current_page()

    def zoom_out(self):
        self.zoom_factor = max(0.6, self.zoom_factor - 0.2)
        self._render_current_page()

    def open_externally(self):
        if self.current_item and self.current_item.file_path.exists():
            os.startfile(str(self.current_item.file_path))

    def _render_current_page(self):
        item = self.current_item
        if not item or not item.file_path.exists() or item.page_count <= 0:
            self.lbl_image.setPixmap(QPixmap())
            self.lbl_image.setText("Dosya bulunamadı veya okunamadı.")
            return

        total = item.page_count
        page_num = self.current_page_idx + 1
        self.lbl_page_info.setText(f"Sayfa: {page_num} / {total}")
        self.btn_prev.setEnabled(self.current_page_idx > 0)
        self.btn_next.setEnabled(self.current_page_idx + 1 < total)

        # Update top banner according to whether this specific page will be printed or skipped
        will_be_printed = page_num <= item.effective_pages_to_print
        if total == 1:
            self.lbl_banner.setText("✅ Tek sayfalık fatura (Bu sayfa yazdırılacak)")
            self.lbl_banner.setStyleSheet(
                "background-color: #DCFCE7; color: #15803D; font-size: 12px; font-weight: 700; padding: 6px 8px; border-radius: 6px;"
            )
        elif will_be_printed:
            self.lbl_banner.setText(
                f"✅ Sayfa {page_num}/{total} (Yazdırılacak) — {item.page2_reason}"
            )
            self.lbl_banner.setStyleSheet(
                "background-color: #DCFCE7; color: #15803D; font-size: 12px; font-weight: 700; padding: 6px 8px; border-radius: 6px;"
            )
        else:
            self.lbl_banner.setText(
                f"✂️ Sayfa {page_num}/{total} (ATLANACAK - BASILMAYACAK)\nSebep: {item.page2_reason}"
            )
            self.lbl_banner.setStyleSheet(
                "background-color: #FEF3C7; color: #B45309; font-size: 12px; font-weight: 700; padding: 6px 8px; border-radius: 6px; border: 1px solid #F59E0B;"
            )

        try:
            with fitz.open(item.file_path) as doc:
                page = doc[self.current_page_idx]
                # Calculate fit width based on viewport width and zoom factor
                viewport_w = max(340, self.scroll_area.viewport().width() - 24)
                base_scale = viewport_w / max(1.0, page.rect.width)
                matrix = fitz.Matrix(base_scale * self.zoom_factor, base_scale * self.zoom_factor)
                pix = page.get_pixmap(matrix=matrix, alpha=False)

                qimg = QImage(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    QImage.Format.Format_RGB888,
                ).copy()
                self.lbl_image.setPixmap(QPixmap.fromImage(qimg))
        except Exception as exc:
            self.lbl_image.setPixmap(QPixmap())
            self.lbl_image.setText(f"Önizleme oluşturulamadı:\n{exc}")


class MainWindow(QMainWindow):
    COL_SELECT = 0
    COL_NAME = 1
    COL_PAGES = 2
    COL_PRINT_RANGE = 3
    COL_COPIES = 4
    COL_TOTAL_SHEETS = 5
    COL_STATUS = 6

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🖨️ Otomatik Fatura Yazdırma ve Akıllı Sayfa Tasarruf Sistemi")
        self.resize(1380, 800)
        self.setAcceptDrops(True)

        self.items: List[PdfInvoiceItem] = []
        self.current_folder: str = ""
        self.worker: PrintWorker | None = None
        self._updating_table = False
        self._auto_pilot_pending_trigger = False

        # Watchdog Timer for Auto-Pilot Folder Monitoring
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(3000)  # Check every 3 seconds
        self.watch_timer.timeout.connect(self._on_watchdog_tick)

        self._apply_global_styles()
        self._build_ui()
        self._load_printers()
        self._load_settings()

    def _apply_global_styles(self):
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #F8FAFC;
            }
            QLabel {
                color: #1E293B;
                font-family: 'Segoe UI', sans-serif;
            }
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 6px 12px;
                color: #1E293B;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #F1F5F9;
                border-color: #94A3B8;
            }
            QPushButton:pressed {
                background-color: #E2E8F0;
            }
            QPushButton#PrimaryBtn {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                padding: 10px 22px;
                font-size: 14px;
                font-weight: 700;
                border-radius: 8px;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #1D4ED8;
            }
            QPushButton#PrimaryBtn:disabled {
                background-color: #94A3B8;
            }
            QPushButton#FolderBtn {
                background-color: #0F172A;
                color: #FFFFFF;
                border: none;
                padding: 8px 16px;
            }
            QPushButton#FolderBtn:hover {
                background-color: #1E293B;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 13px;
                color: #0F172A;
            }
            QTableWidget {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFC;
                color: #0F172A;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                gridline-color: #E2E8F0;
                font-size: 13px;
                selection-background-color: #DBEAFE;
                selection-color: #0F172A;
            }
            QTableWidget::item {
                color: #0F172A;
                padding: 4px 8px;
            }
            QHeaderView::section {
                background-color: #F1F5F9;
                color: #334155;
                font-weight: 700;
                font-size: 12px;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #CBD5E1;
            }
            QCheckBox {
                font-size: 13px;
                color: #1E293B;
                spacing: 6px;
            }
            QProgressBar {
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                text-align: center;
                background-color: #E2E8F0;
                color: #0F172A;
                font-weight: 600;
            }
            QProgressBar::chunk {
                background-color: #22C55E;
                border-radius: 5px;
            }
            """
        )

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # 1. Top Control Bar (Folder, Drag & Drop hint, Printer Selection, Preview Toggle)
        top_frame = QFrame()
        top_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; }"
        )
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(8)

        self.btn_choose_folder = QPushButton("📁 Klasör Seç")
        self.btn_choose_folder.setObjectName("FolderBtn")
        self.btn_choose_folder.clicked.connect(self.choose_folder)

        self.btn_add_files = QPushButton("➕ PDF Ekle")
        self.btn_add_files.setToolTip("Bilgisayardan tek veya çoklu PDF dosyası seçip listeye ekle (veya sürükle-bırak)")
        self.btn_add_files.clicked.connect(self.choose_pdf_files)

        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText(
            "Klasör seçin veya Klasör/PDF dosyalarını doğrudan bu pencereye sürükleyip bırakın..."
        )
        self.txt_folder.setReadOnly(True)

        self.btn_refresh = QPushButton("🔄 Yenile")
        self.btn_refresh.setToolTip("Klasördeki PDF'leri yeniden tara")
        self.btn_refresh.clicked.connect(self.refresh_folder)

        lbl_printer = QLabel("🖨️ Yazıcı:")
        lbl_printer.setStyleSheet("font-weight: 600; border: none;")

        self.cmb_printer = QComboBox()
        self.cmb_printer.setMinimumWidth(220)
        self.cmb_printer.currentTextChanged.connect(self.update_printer_status_badge)

        self.lbl_printer_status = QLabel("Kontrol ediliyor...")
        self.lbl_printer_status.setStyleSheet(
            "font-weight: 700; font-size: 12px; padding: 4px 10px; border-radius: 6px;"
        )

        self.btn_toggle_preview = QPushButton("👁️ Önizleme Paneli: Açık")
        self.btn_toggle_preview.setCheckable(True)
        self.btn_toggle_preview.setChecked(True)
        self.btn_toggle_preview.clicked.connect(self.toggle_preview_panel)

        self.btn_auto_pilot = QPushButton("👀 Otomatik Pilot (Canlı İzle): Kapalı")
        self.btn_auto_pilot.setCheckable(True)
        self.btn_auto_pilot.setChecked(False)
        self.btn_auto_pilot.setToolTip(
            "Aktif edildiğinde seçili klasörü her 3 saniyede bir canlı izler.\n"
            "Klasöre yeni düşen PDF faturaları otomatik analiz eder, gereksiz 2. sayfasını atlar,\n"
            "yazıcıdan çıktısını alır ve 'Yazdırılanlar' klasörüne taşır!"
        )
        self.btn_auto_pilot.clicked.connect(self.toggle_auto_pilot)
        self._style_auto_pilot_btn(False)

        top_layout.addWidget(self.btn_choose_folder)
        top_layout.addWidget(self.btn_add_files)
        top_layout.addWidget(self.txt_folder, stretch=1)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addWidget(self.btn_auto_pilot)
        top_layout.addSpacing(8)
        top_layout.addWidget(lbl_printer)
        top_layout.addWidget(self.cmb_printer)
        top_layout.addWidget(self.lbl_printer_status)
        top_layout.addWidget(self.btn_toggle_preview)

        main_layout.addWidget(top_frame)

        # 2. Summary Statistics Cards
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)

        self.card_total = StatCard("TOPLAM FATURA", "0 Adet", "#2563EB")
        self.card_single = StatCard("1 SAYFALIK FATURA", "0 Adet", "#16A34A")
        self.card_saved = StatCard(
            "2. SAYFASI GEREKSİZ (TASARRUF)", "0 Fatura (0 Kağıt)", "#0D9488"
        )
        self.card_multi = StatCard("GERÇEK 2+ SAYFALIK", "0 Adet", "#D97706")
        self.card_sheets = StatCard("BASILACAK NET KAĞIT", "0 Sayfa", "#7C3AED")

        stats_layout.addWidget(self.card_total)
        stats_layout.addWidget(self.card_single)
        stats_layout.addWidget(self.card_saved)
        stats_layout.addWidget(self.card_multi)
        stats_layout.addWidget(self.card_sheets)

        main_layout.addLayout(stats_layout)

        # 3. Quick Selection & Copy Controls Bar
        toolbar_frame = QFrame()
        toolbar_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; }"
        )
        toolbar_layout = QHBoxLayout(toolbar_frame)
        toolbar_layout.setContentsMargins(12, 8, 12, 8)
        toolbar_layout.setSpacing(8)

        lbl_sel = QLabel("Hızlı Seçim:")
        lbl_sel.setStyleSheet("font-weight: 600; border: none;")

        btn_sel_all = QPushButton("Tümünü İşaretle")
        btn_sel_all.clicked.connect(lambda: self.set_check_state_by_filter("all"))

        btn_sel_none = QPushButton("İşaretleri Kaldır")
        btn_sel_none.clicked.connect(lambda: self.set_check_state_by_filter("none"))

        btn_sel_single = QPushButton("Sadece 1 Sayfalıklar")
        btn_sel_single.clicked.connect(lambda: self.set_check_state_by_filter("single"))

        btn_sel_multi = QPushButton("Sadece 2+ Sayfalıklar")
        btn_sel_multi.setStyleSheet(
            "QPushButton { color: #B45309; border-color: #F59E0B; background-color: #FFFBEB; }"
            "QPushButton:hover { background-color: #FEF3C7; }"
        )
        btn_sel_multi.clicked.connect(lambda: self.set_check_state_by_filter("multi"))

        toolbar_layout.addWidget(lbl_sel)
        toolbar_layout.addWidget(btn_sel_all)
        toolbar_layout.addWidget(btn_sel_none)
        toolbar_layout.addWidget(btn_sel_single)
        toolbar_layout.addWidget(btn_sel_multi)

        toolbar_layout.addStretch()

        lbl_copies = QLabel("İşaretlilerin Kopya Sayısını Yap:")
        lbl_copies.setStyleSheet("font-weight: 600; border: none;")
        toolbar_layout.addWidget(lbl_copies)

        for c_num in (1, 2, 3):
            btn_c = QPushButton(f"{c_num} Kopya")
            btn_c.clicked.connect(lambda _checked, n=c_num: self.set_copies_for_checked(n))
            toolbar_layout.addWidget(btn_c)

        self.spin_bulk_copies = QSpinBox()
        self.spin_bulk_copies.setRange(1, 50)
        self.spin_bulk_copies.setValue(4)
        self.spin_bulk_copies.setMinimumWidth(75)
        btn_apply_spin = QPushButton("Uygula")
        btn_apply_spin.clicked.connect(
            lambda: self.set_copies_for_checked(self.spin_bulk_copies.value())
        )
        toolbar_layout.addWidget(self.spin_bulk_copies)
        toolbar_layout.addWidget(btn_apply_spin)

        main_layout.addWidget(toolbar_frame)

        # 4. Center Splitter: Left = Invoice Table, Right = Built-in Fast PDF Preview Panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Yazdır",
                "Fatura Dosya Adı (Tıkla: Önizle | Çift Tıkla: Aç)",
                "PDF Analiz & Sayfa Durumu",
                "Basılacak Sayfa Seçimi",
                "Kopya Sayısı",
                "Net Çıktı",
                "Durum",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self.on_table_cell_clicked)
        self.table.currentCellChanged.connect(self.on_current_cell_changed)
        self.table.cellDoubleClicked.connect(self.on_table_double_clicked)
        self.table.itemChanged.connect(self.on_table_item_changed)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_SELECT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_PAGES, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            self.COL_PRINT_RANGE, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(self.COL_COPIES, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            self.COL_TOTAL_SHEETS, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        self.preview_panel = PdfPreviewPanel()

        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 62)
        self.splitter.setStretchFactor(1, 38)
        self.splitter.setSizes([820, 520])

        main_layout.addWidget(self.splitter, stretch=1)

        # 5. Bottom Action & Options Bar
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet(
            "QFrame { background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px; }"
        )
        bottom_layout = QVBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(14, 10, 14, 10)
        bottom_layout.setSpacing(8)

        options_row = QHBoxLayout()
        self.chk_smart_skip = QCheckBox(
            "💡 Akıllı Kağıt Tasarrufu: Genel Toplam / KDV Dahil 1. sayfadaysa 2. sayfayı (Banka/IBAN taşmasını) otomatik atla"
        )
        self.chk_smart_skip.setStyleSheet("font-weight: 700; color: #0F766E;")
        self.chk_smart_skip.setChecked(True)
        self.chk_smart_skip.toggled.connect(self.on_smart_skip_toggled)

        self.chk_wait_spooler = QCheckBox(
            "🛡️ Güvenli Kuyruk Takibi (Yazıcı kağıdı basıp bitirmeden sıradakine geçme ve dosyayı taşıma)"
        )
        self.chk_wait_spooler.setStyleSheet("font-weight: 700; color: #1D4ED8;")
        self.chk_wait_spooler.setChecked(True)

        options_row.addWidget(self.chk_smart_skip)
        options_row.addSpacing(20)
        options_row.addWidget(self.chk_wait_spooler)
        options_row.addStretch()
        bottom_layout.addLayout(options_row)

        options_row2 = QHBoxLayout()
        self.chk_auto_move = QCheckBox(
            'Yazdırılan PDF\'leri otomatik olarak "Yazdırılanlar" klasörüne taşı'
        )
        self.chk_auto_move.setChecked(True)

        self.chk_date_subfolder = QCheckBox(
            "Tarih alt klasörü kullan (Yazdırılanlar/YYYY-AA-GG)"
        )
        self.chk_date_subfolder.setChecked(False)

        self.chk_duplex = QCheckBox(
            "Tamamı basılacak 2+ sayfalık faturaları Arkalı-Önlü (Duplex) yazdır"
        )
        self.chk_duplex.setChecked(False)

        options_row2.addWidget(self.chk_auto_move)
        options_row2.addSpacing(15)
        options_row2.addWidget(self.chk_date_subfolder)
        options_row2.addSpacing(15)
        options_row2.addWidget(self.chk_duplex)
        options_row2.addStretch()

        bottom_layout.addLayout(options_row2)

        action_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(
            "Hazır — Klasör seçebilir veya PDF dosyalarını sürükleyip bırakabilirsiniz"
        )

        self.btn_open_archive = QPushButton("📂 Yazdırılanlar Klasörünü Aç")
        self.btn_open_archive.clicked.connect(self.open_archive_folder)

        self.btn_print = QPushButton("🚀 İşaretli Faturaları Yazdır")
        self.btn_print.setObjectName("PrimaryBtn")
        self.btn_print.clicked.connect(self.start_printing)

        action_row.addWidget(self.progress_bar, stretch=1)
        action_row.addWidget(self.btn_open_archive)
        action_row.addWidget(self.btn_print)

        bottom_layout.addLayout(action_row)
        main_layout.addWidget(bottom_frame)

    # --- Drag & Drop Support ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local_path = Path(url.toLocalFile())
                if local_path.is_dir() or local_path.suffix.lower() == ".pdf":
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return

        folders: List[Path] = []
        pdf_files: List[Path] = []

        for url in urls:
            p = Path(url.toLocalFile())
            if p.is_dir():
                folders.append(p)
            elif p.is_file() and p.suffix.lower() == ".pdf":
                pdf_files.append(p)

        # If a folder is dropped, load that folder directly
        if folders and not pdf_files:
            self.load_folder(str(folders[0]))
            return

        # If PDF files (or mix of folders and PDFs) are dropped, add/load them
        all_pdfs: List[Path] = list(pdf_files)
        for folder in folders:
            for sub_p in folder.rglob("*.pdf"):
                if "yazdırılanlar" not in [part.lower() for part in sub_p.parts]:
                    all_pdfs.append(sub_p)

        if all_pdfs:
            self.add_dropped_pdf_files(all_pdfs)

    def choose_pdf_files(self):
        start_dir = self.current_folder if self.current_folder else str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Listeye Eklenecek PDF Faturaları Seçin",
            start_dir,
            "PDF Dosyaları (*.pdf)",
        )
        if files:
            self.add_dropped_pdf_files([Path(f) for f in files])

    def add_dropped_pdf_files(self, pdf_paths: List[Path]):
        existing_paths = {item.file_path.resolve() for item in self.items if item.file_path.exists()}
        added_count = 0

        for p in pdf_paths:
            try:
                resolved = p.resolve()
            except Exception:
                resolved = p
            if resolved in existing_paths:
                continue
            item = analyze_single_pdf_file(
                pdf_path=p,
                base_folder=None,
                default_copies=1,
                smart_skip_redundant_pages=self.chk_smart_skip.isChecked(),
            )
            self.items.append(item)
            existing_paths.add(resolved)
            added_count += 1

        if added_count > 0:
            if not self.current_folder and pdf_paths:
                self.current_folder = str(pdf_paths[0].parent)
                self.txt_folder.setText(f"{self.current_folder} (Sürükle-Bırak / Seçilen Dosyalar)")
            self.populate_table()
            self.update_summary_cards()
            self.progress_bar.setFormat(
                f"{added_count} adet yeni PDF eklendi (Toplam: {len(self.items)} fatura)."
            )
            if self.table.rowCount() > 0:
                self.table.selectRow(self.table.rowCount() - added_count)

    def toggle_preview_panel(self, checked: bool):
        self.preview_panel.setVisible(checked)
        self.btn_toggle_preview.setText(
            "👁️ Önizleme Paneli: Açık" if checked else "👁️ Önizleme Paneli: Kapalı"
        )

    def _style_auto_pilot_btn(self, active: bool):
        if active:
            self.btn_auto_pilot.setText("🟢 Otomatik Pilot (Canlı İzliyor)")
            self.btn_auto_pilot.setStyleSheet(
                "QPushButton { background-color: #16A34A; color: #FFFFFF; border: 1px solid #15803D; font-weight: 700; padding: 6px 14px; border-radius: 6px; }"
                "QPushButton:hover { background-color: #15803D; }"
            )
        else:
            self.btn_auto_pilot.setText("👀 Otomatik Pilot (Canlı İzle): Kapalı")
            self.btn_auto_pilot.setStyleSheet(
                "QPushButton { background-color: #F8FAFC; color: #334155; border: 1px solid #CBD5E1; font-weight: 600; padding: 6px 14px; border-radius: 6px; }"
                "QPushButton:hover { background-color: #E2E8F0; }"
            )

    def toggle_auto_pilot(self, checked: bool):
        if checked and (not self.current_folder or not Path(self.current_folder).exists()):
            QMessageBox.information(
                self,
                "Önce Klasör Seçin",
                "Otomatik Pilot (Canlı İzleme) modunu başlatmak için lütfen önce izlenecek fatura klasörünü seçin.",
            )
            self.btn_auto_pilot.setChecked(False)
            self._style_auto_pilot_btn(False)
            return

        self._style_auto_pilot_btn(checked)
        if checked:
            self.watch_timer.start()
            self.progress_bar.setFormat(
                f"🟢 Otomatik Pilot Aktif: '{self.current_folder}' klasörü canlı izleniyor..."
            )
            # Trigger an immediate scan & print if unprinted files exist
            self._on_watchdog_tick()
        else:
            self.watch_timer.stop()
            self._auto_pilot_pending_trigger = False
            self.progress_bar.setFormat("Otomatik Pilot kapatıldı.")

    def _on_watchdog_tick(self):
        """Periodically checks the watched folder for newly arrived PDF files and auto-prints them."""
        if not self.btn_auto_pilot.isChecked():
            return
        if not self.current_folder:
            return

        folder = Path(self.current_folder)
        if not folder.exists() or not folder.is_dir():
            return

        # If currently printing, wait until current batch completes
        if self.worker and self.worker.isRunning():
            return

        # Discover all current PDF files in folder (excluding 'Yazdırılanlar')
        known_paths = set()
        for item in self.items:
            try:
                known_paths.add(item.file_path.resolve())
            except Exception:
                known_paths.add(item.file_path)

        new_pdf_paths: List[Path] = []
        for p in folder.rglob("*"):
            if "yazdırılanlar" in [part.lower() for part in p.parts]:
                continue
            if not (p.is_file() and p.suffix.lower() == ".pdf"):
                continue
            try:
                resolved = p.resolve()
            except Exception:
                resolved = p

            if resolved not in known_paths:
                # Verify file is completely written (not partial download) and > 0 bytes
                try:
                    if p.stat().st_size <= 0:
                        continue
                    with fitz.open(p) as test_doc:
                        if len(test_doc) <= 0:
                            continue
                except Exception:
                    # Still being downloaded/written by browser or Google Drive; skip until next tick
                    continue
                new_pdf_paths.append(p)

        if new_pdf_paths:
            new_pdf_paths.sort(key=lambda x: str(x).lower())
            for np in new_pdf_paths:
                new_item = analyze_single_pdf_file(
                    pdf_path=np,
                    base_folder=folder,
                    default_copies=1,
                    smart_skip_redundant_pages=self.chk_smart_skip.isChecked(),
                )
                self.items.append(new_item)

            self.populate_table()
            self.update_summary_cards()
            if self.table.rowCount() > 0:
                self.table.selectRow(self.table.rowCount() - 1)

        # Check if there are any selected unprinted items waiting to be printed
        pending_jobs = [
            (row, item)
            for row, item in enumerate(self.items)
            if item.selected
            and item.page_count > 0
            and item.status == "Bekliyor"
            and item.file_path.exists()
        ]

        if pending_jobs:
            printer_name = self.cmb_printer.currentText()
            is_ready, status_text, _ = check_printer_status(printer_name)
            self.update_printer_status_badge()
            if not is_ready:
                self.progress_bar.setFormat(
                    f"👀 Otomatik Pilot Beklemede: {len(pending_jobs)} yeni fatura algılandı, yazıcının açılması bekleniyor ({status_text})..."
                )
                return

            # Start silent automatic printing of pending jobs!
            self.start_printing(silent_auto=True)

    def _load_printers(self):
        self.cmb_printer.clear()
        printers, default_printer = get_installed_printers()
        if not printers:
            self.cmb_printer.addItem("Yazıcı Bulunamadı")
            return

        self.cmb_printer.addItems(printers)
        if default_printer in printers:
            self.cmb_printer.setCurrentText(default_printer)
        self.update_printer_status_badge()

    def update_printer_status_badge(self):
        printer_name = self.cmb_printer.currentText()
        if not printer_name or printer_name == "Yazıcı Bulunamadı":
            self.lbl_printer_status.setText("Yazıcı Yok")
            self.lbl_printer_status.setStyleSheet(
                "background-color: #FEE2E2; color: #B91C1C; font-weight: 700; font-size: 12px; padding: 5px 10px; border-radius: 6px; border: 1px solid #FCA5A5;"
            )
            return

        is_ready, status_text, jobs = check_printer_status(printer_name)
        job_suffix = f" ({jobs} bekleyen iş)" if jobs > 0 else ""
        if is_ready:
            self.lbl_printer_status.setText(f"🟢 {status_text}{job_suffix}")
            self.lbl_printer_status.setStyleSheet(
                "background-color: #DCFCE7; color: #15803D; font-weight: 700; font-size: 12px; padding: 5px 10px; border-radius: 6px; border: 1px solid #86EFAC;"
            )
        else:
            self.lbl_printer_status.setText(f"🔴 {status_text}{job_suffix}")
            self.lbl_printer_status.setStyleSheet(
                "background-color: #FEE2E2; color: #B91C1C; font-weight: 700; font-size: 12px; padding: 5px 10px; border-radius: 6px; border: 1px solid #FCA5A5;"
            )

    def _load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            last_folder = data.get("last_folder", "")
            last_printer = data.get("last_printer", "")
            self.chk_smart_skip.blockSignals(True)
            self.chk_smart_skip.setChecked(data.get("smart_skip", True))
            self.chk_smart_skip.blockSignals(False)
            self.chk_wait_spooler.setChecked(data.get("wait_spooler", True))
            self.chk_auto_move.setChecked(data.get("auto_move", True))
            self.chk_date_subfolder.setChecked(data.get("date_subfolder", False))
            self.chk_duplex.setChecked(data.get("duplex", False))

            show_prev = data.get("show_preview", True)
            self.btn_toggle_preview.setChecked(show_prev)
            self.toggle_preview_panel(show_prev)

            if last_printer and self.cmb_printer.findText(last_printer) >= 0:
                self.cmb_printer.setCurrentText(last_printer)

            if last_folder and Path(last_folder).exists():
                self.load_folder(last_folder)
        except Exception:
            pass

    def _save_settings(self):
        try:
            data = {
                "last_folder": self.current_folder,
                "last_printer": self.cmb_printer.currentText(),
                "smart_skip": self.chk_smart_skip.isChecked(),
                "wait_spooler": self.chk_wait_spooler.isChecked(),
                "auto_move": self.chk_auto_move.isChecked(),
                "date_subfolder": self.chk_date_subfolder.isChecked(),
                "duplex": self.chk_duplex.isChecked(),
                "show_preview": self.btn_toggle_preview.isChecked(),
            }
            CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    def choose_folder(self):
        start_dir = self.current_folder if self.current_folder else str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Faturaların Bulunduğu Klasörü Seçin", start_dir
        )
        if folder:
            self.load_folder(folder)

    def refresh_folder(self):
        self.update_printer_status_badge()
        if self.current_folder and Path(self.current_folder).exists():
            self.load_folder(self.current_folder)
        else:
            self.choose_folder()

    def on_smart_skip_toggled(self, checked: bool):
        for row, item in enumerate(self.items):
            if item.page_count >= 2 and item.page2_is_redundant:
                item.force_print_all = not checked
                cmb = self.table.cellWidget(row, self.COL_PRINT_RANGE)
                if isinstance(cmb, QComboBox):
                    cmb.blockSignals(True)
                    cmb.setCurrentIndex(0 if checked else 1)
                    self._style_range_combo(cmb, item.force_print_all)
                    cmb.blockSignals(False)
                self._update_row_total_sheets_cell(row)
        self.update_summary_cards()
        if self.preview_panel.current_item:
            self.preview_panel._render_current_page()

    def load_folder(self, folder_path: str):
        self.current_folder = folder_path
        self.txt_folder.setText(folder_path)
        self._save_settings()

        self.items = scan_folder_for_pdfs(
            folder_path,
            default_copies=1,
            smart_skip_redundant_pages=self.chk_smart_skip.isChecked(),
        )
        self.populate_table()
        self.update_summary_cards()

        if not self.items:
            self.preview_panel.clear_preview()
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Seçilen klasörde PDF dosyası bulunamadı.")
        else:
            self.table.selectRow(0)
            self.preview_panel.load_invoice_item(self.items[0])
            redundant_cnt = sum(1 for i in self.items if i.page2_is_redundant)
            if redundant_cnt > 0:
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat(
                    f"{len(self.items)} fatura analiz edildi. ({redundant_cnt} faturada gereksiz 2. sayfa tespit edilip otomatik 1 sayfaya düşürüldü!)"
                )
            else:
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat(
                    f"{len(self.items)} adet PDF fatura analiz edildi ve listelendi."
                )

    def populate_table(self):
        self._updating_table = True
        self.table.setRowCount(len(self.items))

        bold_font = QFont("Segoe UI", 10, QFont.Weight.Bold)

        for row, item in enumerate(self.items):
            self.table.setRowHeight(row, 42)

            # 0. Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk_item.setCheckState(
                Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked
            )
            chk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_SELECT, chk_item)

            # 1. File Name + Tooltip with Page 2 preview if multi-page
            name_item = QTableWidgetItem(f"📄  {item.file_name}")
            name_item.setForeground(QColor("#0F172A"))
            name_item.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            tooltip_lines = [f"Dosya: {item.file_path}"]
            if item.page_count >= 2:
                tooltip_lines.append(f"Analiz: {item.page2_reason}")
                if item.page2_preview:
                    tooltip_lines.append(f"2. Sayfa İçeriği: {item.page2_preview}")
            name_item.setToolTip("\n".join(tooltip_lines))
            self.table.setItem(row, self.COL_NAME, name_item)

            # 2. Page Analysis Badge
            page_item = QTableWidgetItem(item.page_badge_text)
            page_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            page_item.setFont(bold_font)
            page_item.setToolTip(
                "\n".join(tooltip_lines[1:]) if len(tooltip_lines) > 1 else ""
            )

            if item.page_count <= 0:
                page_item.setForeground(QColor("#DC2626"))
                page_item.setBackground(QColor("#FEE2E2"))
            elif item.page_count == 1:
                page_item.setForeground(QColor("#15803D"))
                page_item.setBackground(QColor("#DCFCE7"))
            elif item.page2_is_redundant:
                page_item.setForeground(QColor("#0F766E"))
                page_item.setBackground(QColor("#CCFBF1"))
            else:
                page_item.setForeground(QColor("#B45309"))
                page_item.setBackground(QColor("#FEF3C7"))
            self.table.setItem(row, self.COL_PAGES, page_item)

            # 3. Print Page Range Selector
            if item.page_count >= 2:
                cmb_range = QComboBox()
                cmb_range.addItem("✂️ Sadece 1. Sayfa (2.yi Atla)")
                cmb_range.addItem(f"📑 Tüm Sayfalar (1-{item.page_count})")
                cmb_range.setCurrentIndex(1 if item.force_print_all else 0)
                cmb_range.setToolTip(
                    f"Analiz Sonucu: {item.page2_reason}\n"
                    f"2. Sayfa Özeti: {item.page2_preview}"
                )
                self._style_range_combo(cmb_range, item.force_print_all)
                cmb_range.currentIndexChanged.connect(
                    lambda idx, r=row: self.on_row_range_changed(r, idx)
                )
                self.table.setCellWidget(row, self.COL_PRINT_RANGE, cmb_range)
            else:
                single_lbl_item = QTableWidgetItem("1. Sayfa")
                single_lbl_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                single_lbl_item.setForeground(QColor("#64748B"))
                self.table.setItem(row, self.COL_PRINT_RANGE, single_lbl_item)

            # 4. Copies SpinBox Widget
            spin = QSpinBox()
            spin.setRange(1, 99)
            spin.setValue(item.copies)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setStyleSheet(
                "QSpinBox { font-weight: bold; padding: 3px 6px; margin: 4px 10px; }"
            )
            spin.valueChanged.connect(
                lambda val, r=row: self.on_row_copies_changed(r, val)
            )
            self.table.setCellWidget(row, self.COL_COPIES, spin)

            # 5. Total Printed Pages for this Invoice
            total_pages = item.effective_pages_to_print * item.copies
            sheets_item = QTableWidgetItem(f"{total_pages} Sayfa")
            sheets_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sheets_item.setForeground(QColor("#1E293B"))
            sheets_item.setFont(bold_font)
            self.table.setItem(row, self.COL_TOTAL_SHEETS, sheets_item)

            # 6. Status
            status_item = QTableWidgetItem(item.status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setFont(bold_font)
            self._style_status_cell(status_item, item.status, item.error_message)
            self.table.setItem(row, self.COL_STATUS, status_item)

        self._updating_table = False

    def _style_range_combo(self, cmb: QComboBox, is_all_pages: bool):
        if not is_all_pages:
            cmb.setStyleSheet(
                "QComboBox { background-color: #F0FDFA; color: #0F766E; border: 1px solid #5EEAD4; font-weight: 700; margin: 4px 6px; }"
            )
        else:
            cmb.setStyleSheet(
                "QComboBox { background-color: #FFFBEB; color: #B45309; border: 1px solid #FCD34D; font-weight: 700; margin: 4px 6px; }"
            )

    def _style_status_cell(
        self, cell: QTableWidgetItem, status: str, error_msg: str = ""
    ):
        cell.setText(status)
        if error_msg:
            cell.setToolTip(error_msg)
        if "Yazdırıldı" in status or "Basıldı" in status:
            cell.setForeground(QColor("#15803D"))
            cell.setBackground(QColor("#DCFCE7"))
        elif "Hata" in status:
            cell.setForeground(QColor("#B91C1C"))
            cell.setBackground(QColor("#FEE2E2"))
        elif (
            "Yazdırılıyor" in status
            or "Basıyor" in status
            or "Gönderiliyor" in status
            or "Kopya" in status
        ):
            cell.setForeground(QColor("#1D4ED8"))
            cell.setBackground(QColor("#DBEAFE"))
        else:
            cell.setForeground(QColor("#475569"))
            cell.setBackground(QColor("#F8FAFC"))

    def on_table_cell_clicked(self, row: int, _col: int):
        if 0 <= row < len(self.items):
            self.preview_panel.load_invoice_item(self.items[row])

    def on_current_cell_changed(self, current_row: int, _current_col: int, prev_row: int, _prev_col: int):
        if current_row != prev_row and 0 <= current_row < len(self.items):
            self.preview_panel.load_invoice_item(self.items[current_row])

    def on_row_range_changed(self, row: int, combo_index: int):
        if 0 <= row < len(self.items):
            item = self.items[row]
            item.force_print_all = combo_index == 1
            if not item.force_print_all:
                item.printable_pages = 1
            cmb = self.table.cellWidget(row, self.COL_PRINT_RANGE)
            if isinstance(cmb, QComboBox):
                self._style_range_combo(cmb, item.force_print_all)
            self._update_row_total_sheets_cell(row)
            self.update_summary_cards()
            if self.preview_panel.current_item == item:
                self.preview_panel._render_current_page()

    def _update_row_total_sheets_cell(self, row: int):
        if 0 <= row < len(self.items):
            item = self.items[row]
            total_pages = item.effective_pages_to_print * item.copies
            cell = self.table.item(row, self.COL_TOTAL_SHEETS)
            if cell:
                cell.setText(f"{total_pages} Sayfa")

    def on_table_item_changed(self, table_item: QTableWidgetItem):
        if self._updating_table:
            return
        if table_item.column() == self.COL_SELECT:
            row = table_item.row()
            if 0 <= row < len(self.items):
                self.items[row].selected = (
                    table_item.checkState() == Qt.CheckState.Checked
                )
                self.update_summary_cards()

    def on_row_copies_changed(self, row: int, new_copies: int):
        if 0 <= row < len(self.items):
            self.items[row].copies = new_copies
            self._update_row_total_sheets_cell(row)
            self.update_summary_cards()

    def set_check_state_by_filter(self, mode: str):
        self._updating_table = True
        for row, item in enumerate(self.items):
            if item.page_count <= 0:
                should_check = False
            elif mode == "all":
                should_check = True
            elif mode == "none":
                should_check = False
            elif mode == "single":
                should_check = item.effective_pages_to_print == 1
            elif mode == "multi":
                should_check = item.effective_pages_to_print >= 2
            else:
                should_check = item.selected

            item.selected = should_check
            chk_cell = self.table.item(row, self.COL_SELECT)
            if chk_cell:
                chk_cell.setCheckState(
                    Qt.CheckState.Checked if should_check else Qt.CheckState.Unchecked
                )
        self._updating_table = False
        self.update_summary_cards()

    def set_copies_for_checked(self, copies: int):
        for row, item in enumerate(self.items):
            if item.selected:
                item.copies = copies
                spin = self.table.cellWidget(row, self.COL_COPIES)
                if isinstance(spin, QSpinBox):
                    spin.blockSignals(True)
                    spin.setValue(copies)
                    spin.blockSignals(False)
                self._update_row_total_sheets_cell(row)
        self.update_summary_cards()

    def update_summary_cards(self):
        total_files = len(self.items)
        single_page_files = sum(1 for i in self.items if i.page_count == 1)
        redundant_page_files = sum(
            1 for i in self.items if i.page_count >= 2 and i.page2_is_redundant
        )
        true_multi_files = sum(
            1 for i in self.items if i.page_count >= 2 and not i.page2_is_redundant
        )

        selected_items = [i for i in self.items if i.selected and i.page_count > 0]
        total_pages_to_print = sum(
            i.effective_pages_to_print * i.copies for i in selected_items
        )
        saved_sheets = sum(
            (i.page_count - i.effective_pages_to_print) * i.copies
            for i in selected_items
            if i.page_count > i.effective_pages_to_print
        )

        self.card_total.set_value(f"{total_files} Adet (Seçili: {len(selected_items)})")
        self.card_single.set_value(f"{single_page_files} Adet")
        self.card_saved.set_value(
            f"{redundant_page_files} Fatura ({saved_sheets} Kağıt Kurtarıldı)"
        )
        self.card_multi.set_value(f"{true_multi_files} Adet")
        self.card_sheets.set_value(f"{total_pages_to_print} Sayfa")

    def on_table_double_clicked(self, row: int, _col: int):
        if 0 <= row < len(self.items):
            pdf_path = self.items[row].file_path
            if pdf_path.exists():
                os.startfile(str(pdf_path))
            else:
                QMessageBox.warning(
                    self,
                    "Dosya Bulunamadı",
                    f"Dosya taşınmış veya silinmiş olabilir:\n{pdf_path}",
                )

    def open_archive_folder(self):
        if not self.current_folder:
            QMessageBox.information(self, "Bilgi", "Önce bir fatura klasörü seçin.")
            return
        archive_dir = Path(self.current_folder) / "Yazdırılanlar"
        archive_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(archive_dir))

    def start_printing(self, silent_auto: bool = False):
        if self.worker and self.worker.isRunning():
            return

        printer_name = self.cmb_printer.currentText()
        if not printer_name or printer_name == "Yazıcı Bulunamadı":
            if not silent_auto:
                QMessageBox.warning(
                    self, "Yazıcı Hatası", "Lütfen geçerli bir yazıcı seçin."
                )
            return

        self.update_printer_status_badge()
        is_ready, status_text, pending_jobs = check_printer_status(printer_name)
        if not is_ready:
            if silent_auto:
                return
            if self.chk_auto_move.isChecked():
                warn_reply = QMessageBox.warning(
                    self,
                    "⚠️ Yazıcı Şu Anda Çevrimdışı / Hazır Değil!",
                    f"Seçili yazıcı ({printer_name}) şu anda hazır görünmüyor:\n"
                    f"Durum: {status_text}\n"
                    f"Kuyrukta Bekleyen İş: {pending_jobs} adet\n\n"
                    f"Yazıcı kapalı veya çevrimdışıyken gönderilen belgeler Windows yazdırma kuyruğunda bekler ve yazıcı açıldığında basılır.\n\n"
                    f"• 'Yes (Evet)' derseniz: Belgeler kuyruğa eklenir ANCAK yazıcı çevrimdışı olduğu için dosyalar 'Yazdırılanlar' klasörüne TAŞINMAZ (klasörünüzde kalır).\n"
                    f"• 'No (Hayır)' derseniz: İşlem iptal edilir (Önce yazıcınızı açıp tekrar deneyebilirsiniz).",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if warn_reply != QMessageBox.StandardButton.Yes:
                    return
                effective_auto_move = False
            else:
                effective_auto_move = False
        else:
            effective_auto_move = self.chk_auto_move.isChecked()

        jobs = [
            (row, item)
            for row, item in enumerate(self.items)
            if item.selected and item.page_count > 0 and item.file_path.exists()
        ]

        if not jobs:
            if not silent_auto:
                QMessageBox.information(
                    self,
                    "Seçili Fatura Yok",
                    "Yazdırılacak işaretli bir PDF faturası bulunamadı.",
                )
            return

        if not silent_auto:
            total_copies_pages = sum(
                item.effective_pages_to_print * item.copies for _, item in jobs
            )
            saved_pages = sum(
                (item.page_count - item.effective_pages_to_print) * item.copies
                for _, item in jobs
                if item.page_count > item.effective_pages_to_print
            )
            multi_count = sum(
                1 for _, item in jobs if item.effective_pages_to_print >= 2
            )

            confirm_msg = (
                f"Seçili Yazıcı: {printer_name}\n"
                f"Yazdırılacak Fatura Sayısı: {len(jobs)} adet\n"
                f"  • Tek Sayfa Basılacak: {len(jobs) - multi_count} adet\n"
                f"  • Çok Sayfa Basılacak: {multi_count} adet\n"
                f"  • Gereksiz 2. Sayfadan Kurtarılan: {saved_pages} yaprak kağıt\n"
                f"Toplam Basılacak Net Kağıt: {total_copies_pages} sayfa\n\n"
                f"Yazdırma işlemini başlatmak istiyor musunuz?"
            )

            reply = QMessageBox.question(
                self,
                "Yazdırma Onayı",
                confirm_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._last_batch_was_silent_auto = silent_auto
        self._save_settings()
        self.btn_print.setEnabled(False)
        self.btn_choose_folder.setEnabled(False)
        self.btn_add_files.setEnabled(False)
        self.btn_refresh.setEnabled(False)

        self.progress_bar.setMaximum(len(jobs))
        self.progress_bar.setValue(0)

        self.worker = PrintWorker(
            jobs=jobs,
            printer_name=printer_name,
            duplex_multipage=self.chk_duplex.isChecked(),
            auto_move=effective_auto_move,
            use_date_subfolder=self.chk_date_subfolder.isChecked(),
            wait_for_spooler=self.chk_wait_spooler.isChecked(),
        )
        self.worker.progress_changed.connect(self.on_print_progress)
        self.worker.item_status_updated.connect(self.on_item_status_updated)
        self.worker.finished_batch.connect(self.on_print_finished)
        self.worker.start()

    def on_print_progress(self, current_idx: int, total: int, filename: str):
        self.progress_bar.setValue(current_idx)
        self.progress_bar.setFormat(
            f"Yazdırılıyor ({current_idx}/{total}): {filename}"
        )

    def on_item_status_updated(self, row: int, status: str, error_msg: str):
        if 0 <= row < len(self.items):
            self.items[row].status = status
            self.items[row].error_message = error_msg
            cell = self.table.item(row, self.COL_STATUS)
            if cell:
                self._style_status_cell(cell, status, error_msg)

            if "Yazdırıldı" in status or "Basıldı" in status:
                self._updating_table = True
                self.items[row].selected = False
                chk_cell = self.table.item(row, self.COL_SELECT)
                if chk_cell:
                    chk_cell.setCheckState(Qt.CheckState.Unchecked)
                self._updating_table = False

    def on_print_finished(self, success_count: int, fail_count: int):
        self.btn_print.setEnabled(True)
        self.btn_choose_folder.setEnabled(True)
        self.btn_add_files.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.update_summary_cards()
        self.update_printer_status_badge()

        was_silent = getattr(self, "_last_batch_was_silent_auto", False)
        if was_silent and self.btn_auto_pilot.isChecked():
            summary = f"🟢 Otomatik Pilot: {success_count} fatura otomatik basıldı (Hata: {fail_count}) — Canlı izleme devam ediyor..."
            self.progress_bar.setFormat(summary)
            return

        summary = f"Tamamlandı! Başarılı: {success_count} | Hatalı: {fail_count}"
        self.progress_bar.setFormat(summary)

        if fail_count == 0:
            QMessageBox.information(
                self,
                "Yazdırma Tamamlandı",
                f"{success_count} adet fatura başarıyla yazdırıldı.",
            )
        else:
            QMessageBox.warning(
                self,
                "Yazdırma Tamamlandı (Bazı Hatalar Var)",
                f"Başarılı: {success_count}\nHatalı: {fail_count}\n\n"
                "Hatalı satırların üzerine gelerek hata detayını görebilirsiniz.",
            )


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
