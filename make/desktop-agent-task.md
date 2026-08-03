# Úkol pro Claude (desktop / browser use): postavit scénář v Make.com

## Kontext

Cílem je scénář v Make.com, který hlídá FB stránku Respektu a posílá Slack
notifikaci, když má nějaký příspěvek za posledních 7 dní víc než 50
komentářů. Import hotového blueprintu (`fb-comment-spike-watch.blueprint.json`
ve stejné složce) selhal, takže scénář postav ručně kliknutím v UI Make.com
v Chromu, který je již otevřený a přihlášený.

## Než začneš

Pokud nemáš k dispozici tyto tři hodnoty, **zastav se a zeptej se uživatele**
– nehádej je a nevymýšlej si placeholder hodnoty, které by scénář tiše
rozbily:

1. **PAGE_ID** – ID Facebook stránky Respektu (číslo).
2. **FB_ACCESS_TOKEN** – Page Access Token s oprávněním `pages_read_engagement`
   (a `pages_show_list`, `pages_read_user_content`).
3. **SLACK_WEBHOOK_URL** – celá URL Slack Incoming Webhooku
   (tvar `https://hooks.slack.com/services/...`).

## Kroky v Make.com UI

1. Otevři **make.com → Scenarios → Create a new scenario**.
2. V okně výběru appky klikni na vyhledávací pole a napiš `HTTP`. Vyber
   appku **HTTP** → akci **Make a request**.
3. Nastav modul 1 takto:
   - **URL**:
     `https://graph.facebook.com/v19.0/{PAGE_ID}/posts?fields=id,message,created_time,permalink_url,comments.summary(true).limit(0)&since={UNIX_TIMESTAMP_7_DNI_ZPET}&limit=100&access_token={FB_ACCESS_TOKEN}`
     - `{PAGE_ID}` a `{FB_ACCESS_TOKEN}` nahraď skutečnými hodnotami z kroku výše.
     - `{UNIX_TIMESTAMP_7_DNI_ZPET}` nahraď buď pevným číslem (spočítej
       aktuální unix čas mínus 604800 sekund), nebo funkcí Make
       `{{formatDate(addDays(now; -7); "X")}}` vloženou přímo do pole URL
       (Make umožňuje psát funkce přímo do textového pole URL).
   - **Method**: `GET`
   - **Parse response**: zapni (toggle "Parse response" na Yes/On) – Make
     pak automaticky rozparsuje JSON odpověď na strukturovaná pole.
   - Ulož modul (OK).
4. Přidej další modul kliknutím na `+` za modulem 1. Vyhledej appku
   **Flow Control** → akci **Iterator**.
   - **Array**: klikni do pole a namapuj `Data` z výstupu modulu 1
     (v mapovacím panelu by se měl objevit jako `1. Data`). Pokud Make
     nabízí přímo pole `data` z JSON odpovědi, vyber ho.
5. Přidej další modul za Iterátorem. Vyhledej appku **HTTP** → akci
   **Make a request** znovu (druhý HTTP modul, tentokrát pro Slack).
6. Než modul nastavíš, **přidej filtr** na spojnici (šipce) mezi Iterátorem
   a tímto novým HTTP modulem – klikni na ikonu filtru (přesýpací hodiny/
   trychtýř) na spojnici:
   - **Label**: `Více než 50 komentářů`
   - **Podmínka**: namapuj pole `comments > summary > total_count` z
     Iterátoru (modul 2) → operátor **Greater than** → hodnota `50`
   - Ulož filtr.
7. Nastav druhý HTTP modul (Slack):
   - **URL**: vlož `{SLACK_WEBHOOK_URL}` (skutečná Slack webhook URL)
   - **Method**: `POST`
   - **Body type**: `Raw`
   - **Content type**: `JSON (application/json)`
   - **Request content**:
     ```
     {"text": "🚨 Příspěvek má přes 50 komentářů za posledních 7 dní: {{2.message}} ({{2.comments.summary.total_count}} komentářů) - {{2.permalink_url}}"}
     ```
     - Hodnoty `{{2.message}}`, `{{2.comments.summary.total_count}}`,
       `{{2.permalink_url}}` namapuj z výstupu Iterátoru (modul 2) přes
       mapovací panel místo ručního psaní, pokud Make ruční zápis
       proměnných v tomto poli nepřijme.
   - Ulož modul.
8. Klikni na název scénáře nahoře a přejmenuj na **FB Comment Spike Watch**.
9. Klikni na ikonku hodin/scheduling dole vlevo (harmonogram scénáře):
   - Nastav **Run scenario → Every 1 hour(s)**.
10. **Ulož scénář** (Save), ale **nezapínej ho (neaktivuj toggle vpravo
    nahoře)** – nech ho vypnutý, dokud uživatel sám neověří nastavení a
    nepotvrdí spuštění.
11. Spusť **jeden testovací běh ručně** (tlačítko "Run once" vlevo dole),
    zkontroluj, že modul 1 vrátil data (žádná chyba typu 400/401 –
    to by znamenalo špatný token nebo PAGE_ID) a že filtr/Slack modul
    proběhl bez chyby (i kdyby žádný příspěvek neprošel přes práh, to je
    v pořádku).
12. Napiš uživateli shrnutí: kolik příspěvků modul 1 vrátil, jestli test
    proběhl bez chyby, a že scénář zůstal **vypnutý** čeká na jeho
    potvrzení k aktivaci.

## Co nedělat

- Neaktivuj scénář natrvalo bez výslovného potvrzení uživatele.
- Nezadávej si vlastní/vymyšlené hodnoty za PAGE_ID, token nebo Slack
  webhook – pokud je nemáš, zeptej se.
- Neměň práh 50 komentářů ani okno 7 dní bez zeptání – to je zadání v1,
  rozšíření na dynamický časový interval přijde v další iteraci.
