# 🖨️ Otomatik Fatura Yazdırma ve Akıllı Sayfa Tasarruf Sistemi

Her ay klasörlerde biriken e-Fatura / e-Arşiv PDF dosyalarının sayfa sayılarını analiz eden, **gereksiz 2. sayfaları (banka/IBAN/alt bilgi taşmalarını) otomatik tespit edip atlayan**, istenilen kopya sayılarına göre Windows yazıcı kuyruğunu canlı takip ederek toplu çıktı alan ve basılan faturaları otomatik arşivleyen masaüstü uygulaması.

## ✨ Temel Özellikler

- **🔍 Hızlı PDF & Sayfa Analizi:** Seçilen klasör (ve alt klasörlerdeki) tüm PDF faturalarını saniyenin altında tarar; `1 Sayfa`, `2 Sayfa (2. Sayfa Gereksiz)` ve `2+ Sayfa (Tamamı Dolu)` olarak sınıflandırır.
- **💡 Akıllı Kağıt Tasarrufu:** Genel Toplam / KDV Dahil / Ödenecek Tutar bilgisi 1. sayfada yer alıp 2. sayfasında sadece banka hesap bilgileri (IBAN, Mersis No vb.) bulunan faturalarda 2. sayfayı otomatik atlar (`Sadece 1. Sayfa` basar). İstenirse tablo üzerinden tek tıkla `Tüm Sayfalar` seçilebilir.
- **🛡️ Güvenli Kuyruk Takibi (Spooler Monitoring):**
  - Faturaları yazıcıya tek tek gönderir, Windows Yazdırma Kuyruğunu (`win32print.EnumJobs`) canlı izler.
  - Yazıcı kağıdı fiziksel olarak basıp kuyruktan düşürmeden sıradaki faturaya geçmez ve dosyayı `Yazdırılanlar` klasörüne taşımaz.
  - Çoklu kopya (`2 Kopya`, `3 Kopya` vb.) istendiğinde her kopyayı ayrı ve doğrulanmış bir iş olarak göndererek Wi-Fi/WSD yazıcılarda hafıza taşması veya eksik kopya çıkması sorununu önler.
- **🟢 Canlı Yazıcı Durum Kontrolü:** Yazıcının Çevrimiçi (`Hazır`) veya Çevrimdışı (`Offline`, `Kağıt Bitti`, `Duraklatıldı`) durumunu anlık gösterir.
- **📂 Otomatik Arşivleme:** Basımı tamamlanan faturaları otomatik olarak `Yazdırılanlar` klasörüne taşır, mükerrer baskıyı engeller.

## 🚀 Kurulum ve Çalıştırma

### 1. Python ile Çalıştırma
```bash
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```
Veya klasör içindeki `Baslat.bat` / `FaturaYazdirici.vbs` dosyasına çift tıklayarak doğrudan başlatabilirsiniz.

### 2. Tek Dosya `.exe` Olarak Derleme (PyInstaller)
```bash
.\.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile --name "FaturaYazdirici" --icon "app_icon.ico" main.py
```
Derlenen `FaturaYazdirici.exe` dosyası `dist/` klasörü altında oluşturulur.

