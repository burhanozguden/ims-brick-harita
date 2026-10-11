# AEO nöbetçi eczane API incelemesi

**İncelenen sayfa:** `https://www.aeo.org.tr/nobetci-eczaneler`  
**İnceleme zamanı:** 11 Ekim 2026  
**Yöntem:** Chrome DevTools Network kaydı açıkken sayfa yenilendi; gizli durumdaki **Ara** düğmesi tetiklendi; Fetch/XHR kayıtları ve sayfanın satır içi JavaScript kodu karşılaştırıldı.

## Sonuç

Nöbetçi eczane verisini döndüren endpoint:

```text
GET https://www.aeo.org.tr/getPharmacies/{YYYY-MM-DD}
```

İnceleme sırasında kullanılan gerçek istek:

```text
GET https://www.aeo.org.tr/getPharmacies/2026-10-11
```

İstek gövdesi/payload **yoktur**. Tarih, URL yol parametresi olarak ISO biçiminde (`YYYY-MM-DD`) gönderilir.

Yanıt HTTP 200'dür ve gövde geçerli JSON'dur. Örnek üst seviye yapı:

```json
{
  "status": "success",
  "html": "<div class=\"petition-list\"> ... </div>"
}
```

Önemli ayrıntı: Sunucu, JSON gövdesine rağmen yanıt başlığını yanlış/şaşırtıcı biçimde `Content-Type: text/html; charset=UTF-8` olarak döndürüyor. Bu nedenle bazı istemciler otomatik JSON algılamayabilir; yanıt metni alınıp `JSON.parse()` ile ayrıştırılabilir. Sayfanın kendi kodu doğrudan `response.json()` kullanıyor.

`html` alanında eczaneler render edilmiş HTML olarak geliyor. Her eczane kartında ad, ilçe, adres, telefon ve Google Maps koordinat bağlantısı bulunuyor. Örnek işaretler:

```html
<div class="inline-box" data-name="ÇİĞDEM" data-district="Çankaya">
<h4>ÇİĞDEM ECZANESİ</h4>
<a href="https://www.google.com/maps/search/?api=1&query=39.888669284912,32.79733300209">
<a href="tel:03122860385">
```

## Nöbetçi eczane isteğinin ayrıntıları

**HTTP method:** `GET`  
**Request body/payload:** Yok  
**Durum:** `200 OK`  
**Yanıt MIME başlığı:** `text/html; charset=UTF-8`  
**Gerçek gövde biçimi:** JSON  
**Gözlenen gövde uzunluğu:** 199.444 bayt (2026-10-11 verisi)

DevTools'ta gözlenen request headers:

```text
:authority: www.aeo.org.tr
:method: GET
:path: /getPharmacies/2026-10-11
:scheme: https
accept: */*
accept-encoding: gzip, deflate, br, zstd
accept-language: en-US,en;q=0.9
priority: u=1, i
referer: https://www.aeo.org.tr/nobetci-eczaneler?
sec-ch-ua: "Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Linux"
sec-fetch-dest: empty
sec-fetch-mode: cors
sec-fetch-site: same-origin
user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36
```

Tarayıcı oturumunda ayrıca `ci_session`, `cf_clearance` ve analitik çerezleri gönderildi. Hassas ve kısa ömürlü oldukları için değerleri bu rapora yazılmadı.

Gözlenen önemli response headers:

```text
cache-control: no-store, no-cache, must-revalidate
cf-cache-status: DYNAMIC
content-encoding: zstd
content-type: text/html; charset=UTF-8
pragma: no-cache
server: cloudflare
x-powered-by: PHP/8.3.35
```

## Sayfanın çağrı mantığı

Sayfanın satır içi JavaScript kodu endpoint'i şu şekilde çağırıyor:

```javascript
function getPharmacies(formattedDate) {
    const url = "https://www.aeo.org.tr/getPharmacies/" + formattedDate;
    fetch(url, { method: "GET" })
        .then(response => {
            if (!response.ok) throw new Error("Hata");
            return response.json();
        })
        .then(data => {
            if (data.status == "success") {
                document.getElementById("pharmaciesList").innerHTML = data.html;
                getList();
            }
        });
}
```

Çağrı iki durumda yapılır:

1. `DOMContentLoaded` sırasında, istemcinin o günkü tarihiyle.
2. `#search-input-date` alanında `change` olayı oluştuğunda. Görünen `DD.MM.YYYY` değer `YYYY-MM-DD` biçimine çevrilir ve endpoint çağrılır.

Eczane adı ve ilçe filtreleri yeni API isteği yapmaz; yüklenmiş `.inline-box` öğelerini tarayıcı içinde filtreler.

## “Ara” düğmesi hakkındaki önemli bulgu

DOM'da düğme mevcuttur ancak CSS ile gizlidir:

```html
<button class="btn btn-red" style="display: none">Ara</button>
```

Düğme programatik olarak tıklandığında formun varsayılan `GET` submit işlemi gerçekleşti ve sayfa `https://www.aeo.org.tr/nobetci-eczaneler?` adresine yeniden yüklendi. Yeniden yüklemenin ardından aşağıdaki iki birinci taraf Fetch isteği tekrar yapıldı:

```text
GET /server-time.php
GET /getPharmacies/2026-10-11
```

Dolayısıyla güncel sayfada veri yüklemeyi doğrudan “Ara” düğmesi değil; sayfa açılışı ve tarih alanının `change` olayı tetikliyor.

## Gözlenen tüm Fetch/XHR istekleri

### 1. Sunucu tarihi

```text
GET https://www.aeo.org.tr/server-time.php
```

**Body:** Yok  
**Yanıt:** `200 OK`  
**Content-Type:** `application/json`  
**Örnek yanıt:**

```json
{"date":"2026-10-11"}
```

Request headers, eczane isteğiyle büyük ölçüde aynıdır. Buna ek olarak ilk kayıtta aşağıdakiler görüldü:

```text
cache-control: no-cache
pragma: no-cache
referer: https://www.aeo.org.tr/nobetci-eczaneler?
```

Kod bu endpoint'i sayfa açılışında ve her 5 dakikada bir çağırır; gün değişmişse sayfayı yeniler.

### 2. Nöbetçi eczane verisi

```text
GET https://www.aeo.org.tr/getPharmacies/2026-10-11
```

Bu, aranan veri endpoint'idir. Ayrıntıları yukarıdadır.

### 3. Google Analytics telemetrisi

Network kaydında iki adet aşağıdaki biçimde istek görüldü:

```text
POST https://www.google-analytics.com/g/collect?...&en=page_view...
POST https://www.google-analytics.com/g/collect?...&en=Sayfa%20G%C3%B6r%C3%BCnt%C3%BCleme...
```

**Body:** Boş (`Content-Length: 0`); olay parametreleri URL query string'indedir.  
**Yanıt:** `204 No Content`  
**İlgisi:** Nöbetçi eczane verisiyle ilgili değildir.

Başlıca headers:

```text
accept: */*
content-length: 0
origin: https://www.aeo.org.tr
referer: https://www.aeo.org.tr/
sec-fetch-mode: no-cors
sec-fetch-site: cross-site
```

### 4. Cloudflare RUM telemetrisi

```text
POST https://www.aeo.org.tr/cdn-cgi/rum?
```

**Content-Type:** `application/json`  
**Body:** Sayfa yükleme zamanı, tarayıcı/sürüm, bellek ve performans ölçümleri içeren yaklaşık 895 baytlık JSON telemetrisi.  
**Yanıt:** `204 No Content`  
**İlgisi:** Nöbetçi eczane verisiyle ilgili değildir.

Başlıca headers:

```text
accept: */*
content-type: application/json
content-length: 895
origin: https://www.aeo.org.tr
referer: https://www.aeo.org.tr/nobetci-eczaneler?
sec-fetch-mode: cors
sec-fetch-site: same-origin
```

## Doğrudan istemci kullanımı ve Cloudflare uyarısı

Aynı endpoint tarayıcı oturumunda `200` döndürdü. Buna karşılık, oturum çerezleri olmadan yapılan basit bir `curl` denemesi şu sonucu verdi:

```text
HTTP 403
Content-Type: text/html; charset=UTF-8
Yanıt boyutu: 5413 bayt
```

Bu, endpoint'in doğru olmasına rağmen Cloudflare'ın çıplak HTTP istemcilerini engelleyebildiğini doğruluyor. Güvenilir uygulama için:

- Playwright/gerçek tarayıcı oturumunda alınan geçerli `cf_clearance` ile aynı context üzerinden isteği yapmak,
- ya da tarayıcı context'inin `request` API'sini kullanarak çerezleri otomatik taşımak

gerekebilir. `cf_clearance` değeri kısa ömürlüdür; kaynak koda sabitlenmemelidir.

Tarayıcı içinde önerilen temel çağrı:

```javascript
const date = "2026-10-11";
const response = await fetch(`https://www.aeo.org.tr/getPharmacies/${date}`, {
  method: "GET",
  credentials: "include",
  headers: {
    "Accept": "*/*"
  }
});

if (!response.ok) throw new Error(`HTTP ${response.status}`);
const data = JSON.parse(await response.text());
```

Özetle, doğru veri kaynağı **`GET /getPharmacies/{YYYY-MM-DD}`** endpoint'idir; istek payload'ı yoktur, yanıt JSON'dur fakat `Content-Type` yanlış biçimde `text/html` gelir ve Cloudflare oturumu gerekebilir.
