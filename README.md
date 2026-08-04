# respekt-social

Sledování facebookové stránky Respektu – hlídá příspěvky za posledních 7 dní
a hlásí na Slack, když některému **prudce přibývají komentáře**.

Zpráva vypadá takhle:

> :rotating_light: U příspěvku přibylo za poslední 2 hodiny více než 30
> komentářů (aktuálně 251, přírůstek 68).

Smyslem je zachytit ty jednotky příspěvků týdně, které zničehonic vyběhnou,
včas na to, aby se stihly moderovat.

## Jak to funguje

`scripts/fb_comment_spike_watch.py` běží přes GitHub Actions dvakrát za
hodinu (v minutách 13 a 43):

1. Stáhne z Graph API příspěvky stránky za posledních 7 dní i s počtem
   komentářů (`comments.summary(true)`).
2. Aktuální počet zapíše do časové řady ve `state/fb_spike_state.json`.
3. Spočítá **přírůstek za okno**: aktuální počet minus počet naměřený na
   začátku okna (výchozí 2 hodiny zpět).
4. Když přírůstek překročí práh (výchozí 45), pošle notifikaci na Slack.
5. Workflow commitne aktualizovaný stav zpátky do repa.

Sleduje se tedy **rychlost**, ne absolutní počet: příspěvek, který nasbíral
300 komentářů rovnoměrně za týden, je nezajímavý; příspěvek, kterému jich
přibylo 150 za dopoledne, je událost.

### Detaily chování

- **Základnou je začátek okna, ne poslední běh.** Při hodinovém běhu a
  2hodinovém okně se porovnává se stavem před 2 hodinami – spike se pozná,
  i když roste plynule (např. +20 každou hodinu).
- **Noční klid.** Mezi 22:00 a 7:00 (`QUIET_HOURS`, čas podle `TIMEZONE`)
  se dál měří, ale nenotifikuje. Pokud v noci něco vyběhlo, přijde ráno
  souhrn („Přes noc u příspěvku přibylo až N komentářů"). Bez toho by
  spike ze druhé hodiny ranní do rána vypadl z okna a zmizel.
- **Noční eskalace.** Opravdu velký nápor – ve výchozím nastavení 3×
  práh, tedy 90 komentářů za 2 hodiny (`NIGHT_ESCALATION_FACTOR`) – se
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
- **První pozorování staršího příspěvku nehlásí** – není z čeho přírůstek
  počítat. Ozve se až při dalším běhu.
- **Cooldown.** Po notifikaci se u téhož příspěvku mlčí po dobu
  `COOLDOWN_HOURS` (výchozí = délka okna), aby jedna vášnivá diskuze
  nehlásila každou hodinu.
- **Selhání Slacku** neshodí běh a nezapíše cooldown – příští běh to zkusí
  znovu, místo aby se spike tiše ztratil.
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

Automaticky každé 2 hodiny (cron ve workflow), nebo ručně:
Actions → *FB Comment Spike Watch* → Run workflow.

Lokálně:

```bash
export FB_PAGE_ID=... FB_PAGE_ACCESS_TOKEN=... SLACK_WEBHOOK_URL=...
python3 scripts/fb_comment_spike_watch.py
```

Skript nemá žádné závislosti mimo standardní knihovnu Pythonu.

## Konfigurace

Volitelné proměnné prostředí:

| Proměnná | Výchozí | Popis |
|---|---|---|
| `WINDOW_HOURS` | `2` | délka okna pro měření přírůstku |
| `DELTA_THRESHOLD` | `45` | kolik komentářů musí v okně přibýt |
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

### Proč zrovna 2 hodiny / 45 komentářů

Na stránce nemá ~90 % příspěvků komentáře vůbec a několikrát týdně nějaký
zničehonic vyběhne. Základní hladina je tedy skoro nula a práh může být
citlivý, aniž by to začalo šumět: 30 komentářů za 2 hodiny je tam samo
o sobě výjimečné. Zároveň to hlásí dost brzy na to, aby se stihlo
moderovat – konzervativnější „100 za 4 hodiny" se ozve, až když diskuze
běží půl dne.

Práh je 45 (ne 30), protože se počítají i odpovědi ve vláknech; v přepočtu
na komentáře první úrovně to odpovídá zhruba původním 30.

Po týdnu provozu je vhodné čísla doladit podle toho, kolik notifikací
reálně chodí.

### Spolehlivost cronu

GitHub scheduled workflows nejsou přesné. V ostrém provozu vycházely
rozestupy mezi běhy **2 až 4 hodiny** místo požadované jedné — runnery pod
zátěží běhy tiše zahazují, nejvíc v minutě 0, kdy startuje půlka světa.
Proto cron míří na minuty 13 a 43: mimo špičku a dvakrát za hodinu, aby
zahozený běh tolik nevadil.

Pokud by rozestupy zůstaly příliš velké, jediné spolehlivé řešení je
spouštět to mimo GitHub Actions (vlastní cron, cloud scheduler) a workflow
nechat jen jako zálohu.

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
