# ims-brick-harita

Ankara 2026 brick dağılımını (5 kişi, 83 brick) ve **günün nöbetçi eczanelerini** gösteren tek sayfalık Leaflet haritası (`index.html`). Sol panelde her kişinin kendi bricklerindeki bugünün nöbetçileri listelenir; ortak bricklerdeki eczane her iki kişinin altında da görünür. Panelden eczane aranabilir.

## Dosyalar
- `index.html` — harita ve sol panel (kişi → bricklerindeki nöbetçiler, arama, haritada rozetler)
- `ph.json` — brick → tüm eczane adları + brick grupları (sadece Python okur, sayfaya gömülmez)
- `brick_rules.json` — eşleşmeyenler için ipucu kuralları
- `unmatched.json` — son çalıştırmada eşleşmeyen nöbetçiler (betik üretir)
- `update_pharmacies.py` — nöbetçileri çekip `index.html`'e yazan betik

Kişi–brick dağılımı `index.html` içindeki `OWN` / `PERS` verisindedir; kişi adı değiştirmek için ikisinde de değiştirin.

## Nasıl çalışır
`update_pharmacies.py`, [Ankara Eczacı Odası](https://www.aeo.org.tr/nobetci-eczaneler) sayfasındaki nöbetçi eczaneleri Playwright ile çeker, `ph.json`'a göre bricklere eşler ve **yalnızca günün nöbetçilerini** `index.html` içindeki `<script id="nobet-data">` bloğuna yazar.

E�leştirme sırası:
1. Eczane adı `ph.json` içinde birebir geçiyorsa o brick (aynı ad birden çok brickte varsa ilçe/mahalle ipuçlarıyla seçilir).
2. `ph.json`'da yoksa `brick_rules.json` kuralları ve brick adındaki mahalle ipuçları.
3. Eşleşmeyenler nedeniyle birlikte `unmatched.json`'a yazılır: `ambiguous`, `not-in-ph-no-rule`, `district-mismatch` (ad `ph.json`'da var ama ilçe hiçbir adayla uyuşmuyor).

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

`district-mismatch` çıkıyorsa `ph.json`'daki o eczanenin brick'ini kontrol edin; aynı isimli farklı bir eczane olabilir.

## Otomatik güncelleme
`.github/workflows/nobet.yml` her gün 08:30 ve 12:30 (TR) çalışır, değişiklik varsa `index.html` ve `unmatched.json`'ı commit eder. Elle tetiklemek için Actions → *Run workflow*. Depo ayarlarında Actions'a yazma izni (Settings → Actions → Workflow permissions) verilmelidir.
