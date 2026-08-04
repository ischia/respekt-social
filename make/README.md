# Spouštění přes Make.com

GitHub Actions cron se u tohohle repozitáře ukázal jako nepoužitelný: za
dvě hodiny po nasazení vynechal **všechny čtyři** naplánované sloty, přes noc
jel s rozestupy 2–4 hodiny místo jedné. Detekce spiků přitom stojí a padá
s tím, jak často se měří.

Řešení: Make.com scénář, který v pravidelném intervalu odpálí workflow přes
GitHub API. Cron ve workflow zůstává jako záloha — když občas vystřelí, nic
se nezkazí (běh navíc jen zapíše další měření).

## 1. Vytvoř GitHub token

github.com → Settings (účtu, ne repa) → Developer settings → Personal access
tokens → **Fine-grained tokens** → Generate new token

- **Token name**: `make-fb-spike-watch`
- **Expiration**: podle chuti; při „No expiration" si aspoň poznamenej, že
  existuje. Jinak počítej s tím, že po expiraci spouštění tiše přestane
  fungovat.
- **Repository access**: Only select repositories → `ischia/respekt-social`
- **Permissions** → Repository permissions → **Actions**: `Read and write`
  (nic jiného není potřeba)

Generate token → zkopíruj (`github_pat_…`). Zobrazí se jen jednou.

## 2. Scénář v Make

Scenarios → Create a new scenario → přidej modul **HTTP → Make a request**:

| Pole | Hodnota |
|---|---|
| URL | `https://api.github.com/repos/ischia/respekt-social/actions/workflows/fb-spike-watch.yml/dispatches` |
| Method | `POST` |
| Headers | `Authorization` = `Bearer github_pat_…`<br>`Accept` = `application/vnd.github+json`<br>`X-GitHub-Api-Version` = `2022-11-28` |
| Body type | `Raw` |
| Content type | `JSON (application/json)` |
| Request content | `{"ref":"main"}` |
| Parse response | vypnuto (GitHub vrací prázdné tělo) |

Scheduling (ikona hodin dole vlevo): **Every 30 minutes**.

Ulož a zapni scénář (toggle vpravo nahoře).

### Spotřeba kreditů

Účet Respekt Media má 10 000 kreditů měsíčně a dosavadní provoz spotřebuje
v průměru 181 denně (~5 400 měsíčně). Jeden HTTP modul = 1 kredit za
spuštění:

| Interval | Denně | Měsíčně | Celkem i s dosavadním provozem |
|---|---|---|---|
| 30 min | 48 | 1 440 | ~6 900 (69 %) |
| 20 min | 72 | 2 160 | ~7 600 (76 %) |
| 15 min | 96 | 2 880 | ~8 300 (83 %) |

**30 minut** dává při dvouhodinovém okně čtyři měření na okno, což na
spolehlivou základnu stačí, a nechává skoro třetinu rozpočtu jako rezervu.
20 minut se taky vejde. Pod 15 minut nemá smysl chodit — přínos je malý
a rezerva mizí.

## 3. Ověření

Po prvním spuštění (tlačítko „Run once") zkontroluj:

- v Make: modul vrátil **204 No Content** — to je úspěch, GitHub na
  `dispatches` nevrací žádné tělo
- v GitHubu: Actions → měl by přibýt běh s událostí `workflow_dispatch`

Když modul vrátí **404**, token nemá právo `Actions: Read and write` nebo
nevidí repozitář. Když **401**, token je špatně zkopírovaný nebo expiroval.

## Co se stane, když spouštění vypadne

Skript je na výpadky odolný: základnu bere z nejnovějšího měření *před*
začátkem okna, takže delší mezera přírůstek spíš nadhodnotí, než aby ho
ztratila. Historie se u každého příspěvku drží na dvojnásobek okna, takže
mezera do 4 hodin se ustojí. Delší výpadek znamená, že se u příspěvku začne
měřit od nejstaršího dostupného vzorku.
