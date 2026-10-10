# ims-brick-harita

Ankara IMS brick sınırlarını, hastaneleri ve **günlük nöbetçi eczaneleri** gösteren tek sayfalık Leaflet haritası (`index.html`).

## Nasıl çalışır
`update_pharmacies.py`, [Ankara Eczacı Odası](https://www.aeo.org.tr/nobetci-eczaneler) sayfasındaki nöbetçi eczaneleri Playwright ile çeker, `index.html` içindeki `PH` (brick → eczane adları) tablosuna göre bricklere eşler ve sonucu ayrı bir `<script id="nobet-data">` bloğuna yazar. `PH`'ye dokunulmaz.

E�leştirme sırası:
1. Eczane adı `PH` içinde birebir geçiyorsa o brick (aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir).
2. `PH`'de yoksa `brick_rules.json` kuralları ve brick adındaki mahalle ipuçları.
3. Eşleşmeyenler nedeniyle birlikte `unmatched.json`'a yazılır: `ambiguous`, `not-in-ph-no-rule`, `district-mismatch` (ad PH'de var ama ilçe hiçbir adayla uyuşmuyor).

## Kurulum
```bash
pip install -r requirements.txt
playwright install chromium
```

## Kullanım
```bash
python update_pharmacies.py                      # çek, index.html'i güncelle
python update_pharmacies.py --dry-run            # yazmadan özet göster
python update_pharmacies.py --date 2026-10-11    # belirli tarih
python update_pharmacies.py --rows-json x.json   # scrape etmeden hazır liste (test)
```
Veri 20 kayıttan azsa betik hata verir ve `index.html`'e dokunmaz; ayrıntı için `debug/` klasörüne bakın.

## Eşleşmeyenleri çözmek
`unmatched.json`'daki kayıtlar için `brick_rules.json`'a kural ekleyin:
- `keywords`: adreste geçen mahalle/semt (güçlü ipucu)
- `districts`: AEO'daki ilçe (zayıf ipucu)

`district-mismatch` çıkıyorsa `PH`'deki o eczanenin brick'ini kontrol edin; aynı isimli farklı bir eczane olabilir.

## Otomatik güncelleme
`.github/workflows/nobet.yml` her gün 08:30 ve 12:30 (TR) çalışır, değişiklik varsa `index.html` ve `unmatched.json`'ı commit eder. Elle tetiklemek için Actions → *Run workflow*. Depo ayarlarında Actions'a yazma izni (Settings → Actions → Workflow permissions) verilmelidir.
