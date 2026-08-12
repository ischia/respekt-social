# respekt-social

Sledování facebookové stránky Respektu – hlídá příspěvky za posledních 7 dní
a hlásí do Slacku, Google Chatu nebo mailem, když některému **prudce
přibývají komentáře**.

Zpráva vypadá takhle:

> U příspěvku přibylo za poslední 2 hodiny 68 komentářů (aktuálně 251).
> _V otázce migrace se i středové a proevropské strany dostaly do vleku
> krajní pravice a její demagogie_
> https://www.facebook.com/tydenikrespekt/posts/…

Smyslem je zachytit ty jednotky příspěvků týdně, které zničehonic vyběhnou,
včas na to, aby se stihly moderovat.

## Jak to funguje

`scripts/fb_comment_spike_watch.py` běží přes GitHub Actions každých
30 minut (spouští ho Make.com, viz [make/README.md](make/README.md)):

1. Stáhne z Graph API příspěvky stránky za posledních 7 dní i s počtem
   komentářů (`comments.summary(true)`).
2. Aktuální počet zapíše do časové řady ve `state/fb_spike_state.json`.
3. Spočítá **přírůstek za okno**: aktuální počet minus počet naměřený na
   začátku okna (výchozí 2 hodiny zpět).
4. Když přírůstek překročí práh (výchozí 25), pošle upozornění do
   nastavených kanálů.
5. Workflow commitne aktualizovaný stav zpátky do repa.

Sleduje se tedy **rychlost**, ne absolutní počet: příspěvek, který nasbíral
300 komentářů rovnoměrně za týden, je nezajímavý; příspěvek, kterému jich
přibylo 150 za dopoledne, je událost.

### Detaily chování

- **Základnou je začátek okna, ne poslední běh.** Porovnává se se stavem
  před 2 hodinami, ne s předchozím měřením – spike se pozná, i když roste
  plynule (např. +20 každou hodinu).
- **Noční klid.** Mezi 22:00 a 7:00 (`QUIET_HOURS`, čas podle `TIMEZONE`)
  se dál měří, ale nenotifikuje. Pokud v noci něco vyběhlo, přijde ráno
  souhrn („Přes noc u příspěvku přibylo až N komentářů"). Bez toho by
  spike ze druhé hodiny ranní do rána vypadl z okna a zmizel.
- **Noční eskalace.** Opravdu velký nápor – ve výchozím nastavení 3×
  práh, tedy 75 komentářů za 2 hodiny (`NIGHT_ESCALATION_FACTOR`) – se
  ozve i v noci, protože pět hodin nemoderované diskuze napáchá víc škody
  než jedno probuzení. `NIGHT_ESCALATION_FACTOR=0` to vypne úplně.
- **Čerstvé příspěvky.** Příspěvek mladší než okno má základnu 0, protože
  všechny jeho komentáře nutně přibyly uvnitř okna. Ozve se tak i post,
  který explodoval hodinu po zveřejnění.
- **Poměrný práh u kratšího měření.** Když je měření kratší než celé okno
  (čerstvý příspěvek), zkrátí se poměrně i práh: za hodinu při okně 2 h
  stačí polovina. Jinak by prudce startující příspěvek propadl jen proto,
  že ještě nestihl nasbírat počet odpovídající plnému oknu — přesně to,
  co je u moderace potřeba chytit nejdřív. Práh přitom nikdy neklesne pod
  polovinu, aby pár komentářů pár minut po vydání nedělalo poplach.
- **Popisek příspěvku.** Posty začínají „👉 https://rspkt.cz/… Vlastní
  text"; šipka i zkrácený odkaz se z upozornění odloupnou (odkaz na
  příspěvek je ve zprávě už jednou) a zbude jen text, kurzívou. Když po
  očištění nic nezbude, řádek se vynechá.
- **První pozorování staršího příspěvku nehlásí** – není z čeho přírůstek
  počítat. Ozve se až při dalším běhu.
- **Cooldown.** Po notifikaci se u téhož příspěvku mlčí po dobu
  `COOLDOWN_HOURS` (výchozí = délka okna), aby jedna vášnivá diskuze
  nehlásila každou hodinu.
- **Selhání kanálu** neshodí běh. Když neuspěje ani jeden, cooldown se
  nezapíše a příští běh to zkusí znovu, místo aby se spike tiše ztratil.
- **Úklid.** Příspěvky, které vypadnou ze 7denního okna, se ze stavového
  souboru odstraní; historie měření se u každého drží jen na 2× délku okna.

## Nastavení

### 1. GitHub secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Popis |
|---|---|
| `FB_PAGE_ID` | ID facebookové stránky |
| `FB_PAGE_ACCESS_TOKEN` | Page Access Token s `pages_read_engagement` a `pages_read_user_content` |

A **aspoň jeden kanál** pro upozornění; nenastavený secret = kanál vypnutý,
dá se jich zapnout i víc naráz:

| Secret | Kanál |
|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook |
| `GOOGLE_CHAT_WEBHOOK_URL` | webhook prostoru v Google Chatu |
| `SMTP_HOST` + `MAIL_TO` | e-mail; volitelně `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_PORT` (587), `MAIL_FROM` |

Zpráva se považuje za doručenou, když uspěl **aspoň jeden** kanál. Kdyby se
čekalo na všechny, jeden rozbitý kanál by způsobil, že se to samé posílá do
funkčních kanálů při každém běhu znovu.

### Google Chat

V prostoru: **Apps & integrations → Webhooks → Add webhook** → zkopírovat
URL. Vyžaduje Google Workspace; pokud jsou webhooky v organizaci zakázané,
musí je povolit admin.

### E-mail

Přes SMTP, bez další knihovny. U Gmailu / Workspace je potřeba **App
Password** (vyžaduje zapnuté dvoufázové ověření), ne běžné heslo:
`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` = adresa,
`SMTP_PASSWORD` = app password. `MAIL_TO` snese víc adres oddělených čárkou.

E-mail je z těch tří kanálů nejpomalejší — doručuje se se zpožděním a snadno
zapadne. Jako jediný kanál pro něco, co se má stihnout moderovat, ho
nedoporučuju; dává smysl vedle Slacku nebo Chatu, aby se o výběhu dozvěděl
i někdo, kdo chat nepoužívá.

### 2. Slack Incoming Webhook (pokud používáš Slack)

api.slack.com/apps → Create New App → From scratch → Incoming Webhooks
(zapnout) → Add New Webhook to Workspace → vybrat kanál → zkopírovat URL.
Webhook je vázaný na jeden konkrétní kanál.

### 3. Facebook Page Access Token

Nejstabilnější varianta je System User token přes Business Manager (nevyprší):
Business Settings → Users → System Users → Add → Assign Assets (stránka,
Full control) → Generate New Token → vybrat aplikaci a oprávnění.

Aplikace musí mít `pages_read_engagement` povolené v App Dashboard →
App Review → Permissions and Features (Advanced Access). Pokud stránka i
aplikace patří do stejného Business Manageru, bývá to schválené hned;
jinak může Facebook vyžadovat Business Verification.

## Spuštění

Spouští ho Make.com každých 30 minut (viz [make/README.md](make/README.md)).
Cron ve workflow zůstává jako záloha – GitHub ho u tohohle repozitáře
nedodržuje, viz sekci o spolehlivosti níž. Ručně: Actions →
*FB Comment Spike Watch* → Run workflow.

Lokálně:

```bash
export FB_PAGE_ID=... FB_PAGE_ACCESS_TOKEN=...
export SLACK_WEBHOOK_URL=...   # nebo GOOGLE_CHAT_WEBHOOK_URL, nebo SMTP_HOST + MAIL_TO
python3 scripts/fb_comment_spike_watch.py
```

Skript nemá žádné závislosti mimo standardní knihovnu Pythonu.

## Konfigurace

Volitelné proměnné prostředí:

| Proměnná | Výchozí | Popis |
|---|---|---|
| `WINDOW_HOURS` | `2` | délka okna pro měření přírůstku |
| `DELTA_THRESHOLD` | `25` | kolik komentářů musí v okně přibýt |
| `COMMENT_FILTER` | `stream` | `stream` počítá i odpovědi ve vláknech (sedí s Facebookem), `toplevel` jen první úroveň |
| `COOLDOWN_HOURS` | = `WINDOW_HOURS` | jak dlouho po notifikaci mlčet u téhož příspěvku |
| `LOOKBACK_DAYS` | `7` | kolik dní zpět hledat příspěvky |
| `QUIET_HOURS` | `22-7` | noční klid, prázdná hodnota = vypnuto |
| `TIMEZONE` | `Europe/Prague` | zóna, podle které se počítá noční klid |
| `NIGHT_ESCALATION_FACTOR` | `3` | kolikanásobek prahu probudí i v noci (0 = nikdy) |
| `STATE_FILE` | `state/fb_spike_state.json` | cesta ke stavovému souboru |

Nastavují se v workflow v sekci `env:` u kroku „Spustit sledování spiků".

### Počítání komentářů

Graph API ve výchozím nastavení počítá jen komentáře **první úrovně**, takže
hlásí zhruba o třetinu nižší číslo, než je vidět na Facebooku (naměřeno 68
vs. 103 u téhož příspěvku). Skript proto používá `filter(stream)`, který
zahrnuje i odpovědi ve vláknech — čísla v notifikaci pak sedí s tím, co
uvidíš, až příspěvek otevřeš.

Změna `COMMENT_FILTER` posune všechna čísla naráz, takže by porovnání
s dosavadní základnou udělalo spike ze všech sledovaných příspěvků
najednou. Skript si proto použitý způsob počítání ukládá do stavu a při
změně historii zahodí a začne měřit znovu (první běh po přepnutí tedy
nehlásí nic).

### Proč zrovna 2 hodiny / 25 komentářů

Naměřeno na reálném týdnu: 80 příspěvků, medián 2 komentáře, třetina bez
komentářů úplně. Nad 150 komentářů se dostalo šest příspěvků – a mezi nimi
a zbytkem je propast (195, pak 47). Výběhy jsou tedy od běžného provozu
jasně oddělené a základní hladina je skoro nula, takže práh může být
citlivý, aniž by to začalo šumět. Zároveň to hlásí dost brzy na to, aby se stihlo
moderovat – konzervativnější „100 za 4 hodiny" se ozve, až když diskuze
běží půl dne.

Práh 25 vychází z naměřeného provozu, ne z odhadu. Rozhodující je rozdíl
mezi *celkovým počtem* komentářů a *rychlostí* jejich přibývání — příspěvek
se 650 komentáři přidal za čtyři hodiny devět, zatímco skutečný nápor
udělal 69 za dvě hodiny. Detekce cílí na to druhé.

Naměřené hodnoty (přírůstek za 2h okno napříč všemi sledovanými příspěvky):

| Přírůstek | Situace |
|---|---|
| +69 | skutečný výbuch, který chceme hlásit |
| +29 | nejaktivnější příspěvek běžného dne |
| +16 | běžný provoz, hlásit nechceme |

Práh 25 tedy zachytí výbuch i výrazný nadprůměr, ale běžný provoz ne.
Zvýšením na 45 se hlásí jen skutečné výbuchy (řádově jednotky za týden),
snížením pod 20 to začne šumět.

Pozor na past: příspěvek s vysokým *celkovým* počtem komentářů se nemusí
ozvat nikdy, pokud je nabíral rovnoměrně přes celý den. To je záměr, ne
chyba.

Po týdnu provozu je vhodné čísla doladit podle toho, kolik notifikací
reálně chodí.

### Proč spouští Make, a ne cron ve workflow

GitHub scheduled workflows se u tohohle repozitáře ukázaly jako
nepoužitelné. Přes noc jely s rozestupy 2–4 hodiny místo požadované jedné
a po přesunu cronu mimo špičku (minuty 13 a 43) vynechaly **všechny čtyři**
sloty během dvou hodin. Detekce spiků přitom stojí a padá na tom, jak často
se měří.

Spouštění proto obstarává Make.com přes `workflow_dispatch` každých
30 minut. Cron ve workflow zůstává jako záloha — když občas vystřelí,
jen přibude další měření.

## Co dál

Práh je zatím **absolutní číslo** stejné pro všechny příspěvky. Další krok
je práh **relativní k obvyklému tempu stránky** – hlásit, když příspěvek
roste nezvykle rychle *vzhledem k tomu, co je na téhle stránce běžné*
(např. odchylka od mediánu přírůstků). Data na to už se sbírají, stav drží
časovou řadu.

## Složka `make/`

Dřívější pokus postavit totéž v Make.com (blueprint + návod). Řešení
přes Python skript ho nahradilo – složka zůstává jen pro historii a lze
ji smazat.
