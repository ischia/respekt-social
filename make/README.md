# FB Comment Spike Watch – Make.com scénář

Sleduje FB stránku Respektu, kontroluje příspěvky za posledních 7 dní a pošle
Slack notifikaci, pokud má některý příspěvek víc než 50 komentářů.

**Prah 50 komentářů je zatím pevný fixní práh (v1).** Později se nahradí
logikou založenou na rychlosti přírůstku za časový interval (např. z-score
oproti historii stránky) – k tomu bude potřeba ukládat historii počtu
komentářů (Make Data Store nebo externí storage).

## Import blueprintu

1. V Make: **Scenarios → Create a new scenario → ⋮ (vpravo nahoře) → Import Blueprint**
2. Nahraj `fb-comment-spike-watch.blueprint.json`
3. Po importu Make požádá o doplnění connections u obou HTTP modulů – u
   HTTP modulů žádná connection není potřeba (jde o generický HTTP modul,
   ne o vyhrazenou Facebook Pages / Slack appku), stačí doplnit hodnoty níže.

## Co je potřeba doplnit ručně po importu

V modulu **1. Načíst příspěvky**:
- `{{PAGE_ID}}` → nahraď ID FB stránky Respektu (najdeš přes
  `https://graph.facebook.com/PAGE_USERNAME?fields=id&access_token=...`
  nebo v Page Transparency / About sekci stránky)
- `{{FB_PAGE_ACCESS_TOKEN}}` → Page Access Token (viz předchozí diskuze
  o System User tokenu, nebo dočasně Page Access Token z Graph API Exploreru
  pro rychlé otestování – ten ale expiruje po pár hodinách)

V modulu **3. Poslat Slack notifikaci**:
- `{{SLACK_WEBHOOK_PATH}}` → nahraď částí URL z tvého Slack Incoming
  Webhooku (Slack App → Incoming Webhooks → Add New Webhook to Workspace →
  zkopíruj celou URL a nahraď jí celé `url` pole, nejjednodušší je
  přepsat celé pole `url` v modulu 3 vlastní webhook URL)

## Harmonogram

Scénář je nastaven na běh **každou hodinu** (`scheduling.interval: 3600`
v blueprintu). Po importu zkontroluj v Make záložku **Scheduling** u
scénáře, jestli se interval načetl – pokud ne, nastav ručně "Run every 1 hour".

## Pokud import blueprintu selže nebo vypadá rozbitě

Postav scénář ručně, moduly v tomto pořadí:

1. **HTTP → Make a request**
   - Method: `GET`
   - URL: `https://graph.facebook.com/v19.0/{PAGE_ID}/posts?fields=id,message,created_time,permalink_url,comments.summary(true).limit(0)&since={timestamp 7 dní zpět}&limit=100&access_token={TOKEN}`
2. **Flow Control → Iterator**
   - Array: `{{1.data}}` (výstup z předchozího HTTP modulu)
3. **Filter** (mezi Iteratorem a dalším modulem)
   - Podmínka: `{{2.comments.summary.total_count}}` **Greater than** `50`
4. **HTTP → Make a request** (Slack)
   - Method: `POST`
   - URL: tvůj Slack Incoming Webhook
   - Body type: Raw / JSON
   - Content: `{"text": "Příspěvek má přes 50 komentářů: {{2.permalink_url}}"}`
5. V nastavení scénáře (ozubené kolo) nastav **Scheduling → Run every 1 hour**
