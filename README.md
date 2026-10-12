# ims-brick-harita

Ankara 2026 brick dağılımını (5 kişi, 83 brick) ve **günün nöbetçi eczanelerini** gösteren tek sayfalık Leaflet haritası (`index.html`). Sol panelde (telefonda alttan açılan çekmecede) her kişinin kendi bricklerindeki bugünün nöbetçileri listelenir; ortak bricklerdeki eczane her iki kişinin altında da görünür. Eczane aranabilir; telefon, not ve yol tarifi bağlantısı gösterilir. Tek dosya hem PC hem mobilde çalışır.

## Dosyalar
- `index.html` — harita ve panel (kişi → bricklerindeki nöbetçiler, arama, haritada rozetler)
- `update_pharmacies.py` — nöbetçileri API'den çekip bricklere eşler, `index.html`'e yazar
- `ph.json` — brick → tüm eczane adları + brick grupları (sadece Python okur, sayfaya gömülmez)
- `brick_rules.json` — eşleşmeyenler için ipucu kuralları
- `unmatched.json` — son çalıştırmada eşleşmeyen nöbetçiler (betik üretir)
- `.github/workflows/update_map.yml` — günlük otomatik güncelleme

Kişi–brick dağılımı `index.html` içindeki `OWN` / `PERS` verisindedir; kişi adı değiştirmek için ikisinde de değiştirin.

## Veri kaynağı
1. **Ana kaynak — AEO:** `GET https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD}` (Ankara Eczacı Odası'nın kendi sayfasının kullandığı JSON API'si). Site Cloudflare arkasında olduğu için istek önce Playwright ile açılan oturumdan yapılır; olmazsa `curl_cffi` denenir. Ayrıntı: `aeo_api_findings.md`.
2. **Yedek kaynak — eczaneler.gen.tr:** AEO'dan yeterli kayıt gelmezse `https://www.eczaneler.gen.tr/nobetci-ankara` sayfası okunur (düz HTTP → `curl_cffi` → tarayıcı sırasıyla). Sayfa dün/bugün/yarın için üç liste içerir; başlıklardaki tarihe göre istenen gün seçilir. Bu site resmi oda sitesi değil, listeleri derleyen üçüncü taraf bir portaldır (kendi kullanım koşullarında "Sitene Ekle" aracını önerir); bu yüzden yalnızca yedek olarak, günde en fazla birkaç istekle kullanılır. Yedek kullanıldığında sayfadaki bilgi satırında "kaynak: eczaneler.gen.tr" yazar. Bu kaynakta koordinat yoktur; yol tarifi bağlantısı adresten üretilir.

Kaynağı elle seçmek için: `--source aeo|eczaneler|auto` (varsayılan `auto`).

## Eşleştirme
1. Eczane adı `ph.json` içinde birebir geçiyorsa o brick (aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir).
2. `ph.json`'da yoksa `brick_rules.json` kuralları ve brick adındaki mahalle ipuçları.
3. Eşleşmeyenler nedeniyle birlikte `unmatched.json`'a yazılır: `ambiguous`, `not-in-ph-no-rule`, `district-mismatch` (ad `ph.json`'da var ama ilçe/adres hiçbir adayla uyuşmuyor).

## Kurulum ve kullanım
```bash
pip install -r requirements.txt
playwright install chromium

python update_pharmacies.py                      # çek, index.html'i güncelle
python update_pharmacies.py --dry-run            # yazmadan özet göster
python update_pharmacies.py --date 2026-10-11    # belirli gün
python update_pharmacies.py --source eczaneler   # yalnızca yedek kaynak
python update_pharmacies.py --fetcher curl       # AEO için Playwright yerine curl_cffi
python update_pharmacies.py --rows-json x.json   # scrape etmeden hazır liste (test)
```
Veri 20 kayıttan azsa betik hata verir ve `index.html`'e dokunmaz; ayrıntı için `debug/` klasörüne bakın.

## Eşleşmeyenleri çözmek
- `ambiguous` / `not-in-ph-no-rule`: `brick_rules.json`'a kural ekleyin (`keywords`: adreste geçen mahalle, `districts`: ilçe).
- `district-mismatch`: `ph.json`'daki o eczanenin brick'ini kontrol edin; aynı isimli farklı bir eczane ya da eski kayıt olabilir.

## Otomatik güncelleme
`update_map.yml` her gün 06:00 ve 09:30'da (TR) çalışır, değişiklik varsa `index.html` ve `unmatched.json`'ı commit eder. Elle: Actions → *Run workflow*. Settings → Actions → Workflow permissions: *Read and write*. Sayfa, nöbet gününün sabah 09:00'da değiştiğini varsayar (`index.html` içinde `NOBET_SAATI`).
