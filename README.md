# respekt-social

Sledování facebookové stránky Respektu – hlídá příspěvky za posledních 7 dní
a hlásí na Slack, když příspěvek překročí další pásmo počtu komentářů.

## Jak to funguje

`scripts/fb_comment_spike_watch.py` běží přes GitHub Actions každé 2 hodiny:

1. Stáhne z Graph API příspěvky stránky za posledních 7 dní i s počtem
   komentářů (`comments.summary(true)`).
2. Pro každý příspěvek spočítá **pásmo** = počet komentářů zaokrouhlený dolů
   na násobek 50 (0–49 → 0, 50–99 → 50, 100–149 → 100, …).
3. Porovná s nejvyšším už nahlášeným pásmem uloženým v
   `state/fb_spike_tiers.json`. Je-li aktuální pásmo vyšší, pošle Slack
   notifikaci a nové pásmo si zapíše.
4. Workflow commitne aktualizovaný stav zpátky do repa.

Díky pásmům se u jednoho příspěvku neposílá pořád dokola totéž: první
notifikace přijde při 50 komentářích, další až při 100, pak při 150 atd.
Pokud odeslání na Slack selže, stav se pro daný příspěvek **neuloží** a
příští běh se o notifikaci pokusí znovu.

Příspěvky, které vypadnou ze 7denního okna, se ze stavového souboru
automaticky odstraní, aby nerostl donekonečna.

## Nastavení

### 1. GitHub secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Popis |
|---|---|
| `FB_PAGE_ID` | ID facebookové stránky |
| `FB_PAGE_ACCESS_TOKEN` | Page Access Token s `pages_read_engagement` a `pages_read_user_content` |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |

### 2. Slack Incoming Webhook

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
| `TIER_SIZE` | `50` | velikost pásma v komentářích |
| `LOOKBACK_DAYS` | `7` | kolik dní zpět hledat příspěvky |
| `STATE_FILE` | `state/fb_spike_tiers.json` | cesta ke stavovému souboru |

## Co dál (v2)

Současná verze hlásí **absolutní** počet komentářů. Zamýšlené rozšíření je
detekce podle **rychlosti přírůstku** v časovém intervalu (např. odchylka
oproti obvyklému tempu stránky), aby se ozval i příspěvek, který nabírá
komentáře nezvykle rychle, ale absolutně jich má zatím málo. K tomu bude
potřeba ukládat i časovou řadu, ne jen poslední pásmo.

## Složka `make/`

Dřívější pokus postavit totéž v Make.com (blueprint + návod). Řešení
přes Python skript ho nahradilo – složka zůstává jen pro historii a lze
ji smazat.
