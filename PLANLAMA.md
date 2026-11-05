# 🖨️ Otomatik Fatura Yazdırma ve Yönetim Sistemi - Proje Planı

Bu belge, her ay klasörlerde biriken PDF faturalarının sayfa sayılarını analiz eden, istenilen kopya adetlerine göre toplu olarak yazıcıdan çıktısını alan ve yazdırılan dosyaları otomatik arşivleyen masaüstü uygulamasının mimari ve işlevsel planını içerir.

---

## 🎯 1. Projenin Amacı ve Temel İhtiyaçlar
- **Klasör Tarama:** Seçilen bir klasördeki tüm `.pdf` uzantılı faturaları tek tıkla listelemek.
- **Sayfa Analizi:** Her PDF'in kaç sayfadan oluştuğunu (`1 Sayfa`, `2 Sayfa`, `3+ Sayfa`) otomatik tespit edip kullanıcıya net şekilde göstermek ve özetlemek.
- **Esnek Kopya Yönetimi:** Tüm faturalara varsayılan bir kopya sayısı (örn. 1 veya 2) atayabilmek; istenirse listedeki belirli faturaları tek tek veya çoklu seçerek kopya sayılarını (`1`, `2`, `3` vb.) özelleştirebilmek.
- **Kesintisiz Toplu Baskı:** Sistemdeki bağlı yazıcıları listelemek, seçilen yazıcıya tüm listeyi sırayla ve arka planda otomatik göndermek.
- **Mükerrer Baskı Önleme (Arşivleme):** Başarıyla yazdırılan PDF'leri otomatik olarak aynı dizin altındaki `Yazdırılanlar` (veya tarih bazlı `Yazdırılanlar/2026-11`) klasörüne taşımak.

---

## 🖥️ 2. Kullanıcı Arayüzü (GUI) Tasarımı

Modern, hızlı ve göz yormayan bir masaüstü arayüzü (**CustomTkinter** veya **PyQt6**) kullanılacaktır:

1. **Üst Panel (Klasör & Yazıcı Seçimi):**
   - `📁 Klasör Seç` butonu ve seçili klasör yolu gösterimi.
   - `🖨️ Yazıcı Seçimi` açılır menüsü (Windows'ta tanımlı yazıcıları otomatik çeker, varsayılan yazıcı seçili gelir).
   - `🔄 Yenile` butonu.

2. **Özet Bilgi Çubuğu (İstatistik Kartları):**
   - **Toplam Fatura:** `X Adet`
   - **1 Sayfalık Faturalar:** `Y Adet` (Yeşil rozet)
   - **2+ Sayfalık Faturalar:** `Z Adet` (Turuncu dikkat rozeti - çok sayfalı olduğu hemen fark edilir)
   - **Toplam Harcanacak Kağıt:** Kopya sayılarına göre dinamik hesaplanan toplam sayfa/yaprak adedi.

3. **Orta Panel (Fatura Tablosu / Listesi):**
   - Sütunlar: `[✓ Seçim] | [Dosya Adı] | [Sayfa Durumu (1 Sayfa / 2 Sayfa)] | [Kopya Sayısı (- 1 +)] | [Durum (Bekliyor / Yazdırıldı)]`
   - **Hızlı Filtreleme & Toplu İşlem Butonları:**
     - `Tümünü Seç / Seçimi Kaldır`
     - `Seçilenlerin Kopya Sayısını Ayarla: [1] [2] [3]`
     - `Sadece 2+ Sayfalıkları Seç` (Çok sayfalı faturalara farklı işlem yapmak isterseniz tek tıkla seçmek için)
   - Satıra çift tıklandığında ilgili PDF'i varsayılan PDF görüntüleyicide açıp kontrol etme imkanı.

4. **Alt Panel (Aksiyon ve Ayarlar):**
   - `[✓] Yazdırılan dosyaları otomatik olarak "Yazdırılanlar" klasörüne taşı` (Onay kutusu - varsayılan aktif).
   - İlerleme Çubuğu (Progress Bar): `Yazdırılıyor: 4 / 25 (fatura_04.pdf)`
   - `🚀 Seçilenleri Yazdır` ana aksiyon butonu.

---

## ⚙️ 3. Teknik Mimari ve Kullanılacak Teknolojiler

Uygulama **Windows** ortamında en kararlı çalışacak şekilde **Python** ile geliştirilecektir:

| Bileşen | Teknoloji / Kütüphane | Seçim Nedeni |
| :--- | :--- | :--- |
| **Masaüstü Arayüzü** | `CustomTkinter` veya `PyQt6` | Modern görünüm, tablo ve buton kontrolü kolaylığı. |
| **PDF Okuma & Analiz** | `PyMuPDF (fitz)` veya `pypdf` | Çok hızlı PDF sayfa sayısı okuma (100 PDF'i <1 saniyede analiz eder). |
| **Yazıcı Yönetimi** | `pywin32 (win32print)` + `PyMuPDF / SumatraPDF CLI` | Windows yazıcılarını listeleme ve Adobe Reader'a bağımlı kalmadan sessiz (arka planda pencere açmadan) doğrudan yazdırma. |
| **Dosya Yönetimi** | `pathlib` & `shutil` | Güvenli dosya taşıma, aynı isimli dosya varsa üzerine yazmadan numaralandırma (`fatura_1.pdf`). |
| **Paketleme (İsteğe Bağlı)** | `PyInstaller` | Masaüstünden çift tıklayarak çalıştırılabilir tek bir `.exe` dosyası üretimi. |

> **💡 Sessiz Yazdırma (Silent Printing) Notu:**
> Windows'ta PDF'leri pencere açılmadan ve kopya sayısını tam kontrol ederek yazdırmak için iki güvenilir yöntem vardır:
> 1. **Gömülü SumatraPDF CLI veya Doğrudan Win32 GDI Baskı (`PyMuPDF` + `win32ui`):** Bilgisayarda Adobe Acrobat yüklü olmasa bile PDF sayfasını yüksek çözünürlükte doğrudan Windows yazıcı kuyruğuna gönderir. Kopya sayısı doğrudan yazıcı sürücüsüne iletilir.

---

## 🔄 4. Çalışma Akışı (Adım Adım Senaryo)

```
[Klasör Seçilir]
       │
       ▼
[PDF'ler Taranır & Sayfa Sayıları Okunur]
       │
       ├──► 1 Sayfalık olanlar normal işaretlenir
       └──► 2 ve üzeri sayfalı olanlar tabloda vurgulanır
       │
       ▼
[Kullanıcı Kopya Sayılarını Belirler] (Varsayılan: 1, Seçilenler: 2+)
       │
       ▼
["Yazdır" Butonuna Basılır]
       │
       ▼
[Her PDF Sırayla Seçili Yazıcıya Kopya Sayısı Kadar Gönderilir]
       │
       ▼
[Baskı Kuyruğuna İletilen PDF -> "Yazdırılanlar" Klasörüne Taşınır]
```

---

## 🚀 5. Geliştirme Aşamaları (Yol Haritası)

- **Faz 1: Çekirdek Modüllerin Yazılması**
  - Klasör okuma ve PDF sayfa sayısı analiz modülü.
  - Windows yazıcı listeleme ve doğrudan PDF yazdırma motoru.
  - Güvenli dosya taşıma (`Yazdırılanlar` arşivleme) modülü.
- **Faz 2: Masaüstü Arayüzünün (GUI) İnşası**
  - Tablo görünümü, sayfa sayısı rozetleri, toplu kopya sayısı değiştirme kontrolleri.
  - Kağıt/Sayfa hesaplayıcı ve ilerleme çubuğu.
- **Faz 3: Test ve `.exe` Haline Getirme**
  - Sanal yazıcıda (`Microsoft Print to PDF`) veya gerçek yazıcıda test.
  - Masaüstüne kısayol / `.exe` oluşturulması.

---

## ❓ Üzerine Düşünülecek / Karar Verilecek Noktalar

1. **Çok Sayfalı (2 Sayfa) Faturaların Baskı Tipi:**
   - 2 sayfalık bir faturadan **2 kopya** istendiğinde, sayfalar `1-2, 1-2` (Harmanlanmış / Collated) şeklinde mi çıkmalı? *(Varsayılan olarak harmanlanmış `1-2, 1-2` planlandı).*
   - Yazıcınızda **Arkalı-Önlü (Duplex)** özelliği varsa, 2 sayfalık faturalar için arayüzde *"2 sayfalıkları arkalı-önlü yazdır"* seçeneği de bulunsun mu?
2. **Arşiv Klasörü Yapısı:**
   - Taşınacak klasör doğrudan seçilen klasörün içinde `Yazdırılanlar` adında mı olsun, yoksa `Yazdırılanlar/2026-11-04` gibi yazdırıldığı günün tarihiyle mi gruplansın?

