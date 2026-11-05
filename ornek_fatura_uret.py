"""
Örnek fatura PDF'leri oluşturma betiği (Test amaçlı).
1 sayfalık, 2. sayfası sadece banka/IBAN taşması olan (gereksiz 2. sayfa) ve
gerçek 2 sayfalık (toplamı 2. sayfada olan) örnek faturalar üretir.
"""
from pathlib import Path
import pymupdf as fitz  # PyMuPDF


def create_sample_invoices():
    sample_dir = Path(__file__).parent / "ornek_faturalar"
    sample_dir.mkdir(exist_ok=True)

    # 1. Tek sayfalık standart faturalar
    for filename in [
        "Elektrik_Faturasi_Kasim_2026.pdf",
        "Dogalgaz_Faturasi_Kasim_2026.pdf",
        "Su_Faturasi_Kasim_2026.pdf",
    ]:
        doc = fitz.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_text((50, 70), f"E-ARSIV FATURA: {filename}", fontsize=15)
        p1.insert_text((50, 130), "1. Kalem: Aylik Tuketim Bedeli - 850,00 TL", fontsize=12)
        p1.insert_text((50, 170), "Mal Hizmet Toplam Tutari: 850,00 TL", fontsize=12)
        p1.insert_text((50, 195), "Hesaplanan KDV (%20): 170,00 TL", fontsize=12)
        p1.insert_text((50, 220), "Vergiler Dahil Toplam Tutar: 1.020,00 TL", fontsize=13)
        p1.insert_text((50, 245), "Odenecek Tutar: 1.020,00 TL", fontsize=14)
        doc.save(sample_dir / filename)
        doc.close()

    # 2. 2 Sayfalık AMA Genel Toplam 1. sayfada, 2. sayfada sadece Banka / IBAN bilgisi var (2. sayfa gereksiz!)
    for filename in [
        "Internet_Fiber_Fatura_Kasim_2026.pdf",
        "Yazilim_Lisans_Faturasi_Kasim.pdf",
    ]:
        doc = fitz.open()
        p1 = doc.new_page(width=595, height=842)
        p1.insert_text((50, 70), f"E-FATURA: {filename}", fontsize=15)
        p1.insert_text((50, 130), "1. Fiber Kurumsal Paket - 1.500,00 TL", fontsize=12)
        p1.insert_text((50, 180), "Mal Hizmet Toplam Tutari: 1.500,00 TL", fontsize=12)
        p1.insert_text((50, 205), "KDV Dahil Toplam: 1.800,00 TL", fontsize=13)
        p1.insert_text((50, 230), "Genel Toplam / Odenecek Tutar: 1.800,00 TL", fontsize=14)
        p1.insert_text((50, 260), "Yalniz: BinSekizYuz TL", fontsize=11)

        p2 = doc.new_page(width=595, height=842)
        p2.insert_text((50, 70), "BANKA HESAP BILGILERIMIZ:", fontsize=12)
        p2.insert_text((50, 95), "Ziraat Bankasi - TR00 0001 0002 0003 0004 0005 01", fontsize=11)
        p2.insert_text((50, 120), "Garanti BBVA - IBAN: TR99 0006 2000 0001 2345 67", fontsize=11)
        p2.insert_text((50, 150), "Mersis No: 0123456789000015 | Ticaret Sicil: 12345", fontsize=10)
        doc.save(sample_dir / filename)
        doc.close()

    # 3. Gerçek 2 Sayfalık Fatura (Çok kalemli, Genel Toplam ve KDV 2. sayfada!)
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 70), "E-FATURA: Toptanci_Malzeme_Faturasi_01.pdf (Sayfa 1/2)", fontsize=15)
    for idx in range(1, 22):
        p1.insert_text((50, 100 + idx * 30), f"Kalem {idx}: Endustriyel Yedek Parca #{idx} - 500,00 TL", fontsize=11)

    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 70), "Sayfa 2/2 - Kalemlerin Devami:", fontsize=13)
    p2.insert_text((50, 110), "Kalem 22: Montaj Seti - 2.000,00 TL", fontsize=11)
    p2.insert_text((50, 150), "Mal Hizmet Toplam Tutari: 12.500,00 TL", fontsize=12)
    p2.insert_text((50, 175), "Hesaplanan KDV (%20): 2.500,00 TL", fontsize=12)
    p2.insert_text((50, 200), "Genel Toplam / Odenecek Tutar: 15.000,00 TL", fontsize=14)
    doc.save(sample_dir / "Toptanci_Malzeme_Faturasi_01.pdf")
    doc.close()

    # Eski 3 sayfalık test dosyasını temizle
    old_3p = sample_dir / "Kargo_Hizmet_Faturasi_Detayli.pdf"
    if old_3p.exists():
        old_3p.unlink()

    print(f"Örnek faturalar güncellendi: {sample_dir}")


if __name__ == "__main__":
    create_sample_invoices()
