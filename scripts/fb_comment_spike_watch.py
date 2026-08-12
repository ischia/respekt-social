#!/usr/bin/env python3
"""
FB Comment Spike Watch
Sleduje příspěvky na FB stránce Respektu za posledních 7 dní a upozorňuje
na Slacku, v Google Chatu nebo mailem, když příspěvku *přibude* za
sledované okno (výchozí 2 hodiny)
víc než daný počet komentářů (výchozí 25).

Hlídá se tedy rychlost přírůstku, ne absolutní počet: příspěvek, který
nasbíral 300 komentářů rovnoměrně za týden, je nezajímavý; příspěvek,
kterému jich přibylo 150 za dopoledne, je událost.

Stav (naměřené počty komentářů v čase) se ukládá do JSON souboru ve stejném
repu, aby šlo přírůstek mezi běhy vůbec spočítat.

Vyžaduje proměnné prostředí:
    FB_PAGE_ID              - ID facebookové stránky
    FB_PAGE_ACCESS_TOKEN    - Page Access Token s pages_read_engagement
                              a pages_read_user_content

Alespoň jeden kanál pro upozornění (dá se jich zapnout víc naráz):
    SLACK_WEBHOOK_URL       - Slack Incoming Webhook
    GOOGLE_CHAT_WEBHOOK_URL - webhook prostoru v Google Chatu
    SMTP_HOST + MAIL_TO     - odesílání mailem; volitelně SMTP_PORT (587),
                              SMTP_USER, SMTP_PASSWORD, MAIL_FROM

Volitelné:
    WINDOW_HOURS            - délka okna pro měření přírůstku (výchozí 2)
    DELTA_THRESHOLD         - kolik komentářů musí v okně přibýt (výchozí 25)
    COMMENT_FILTER          - "stream" (výchozí) počítá i odpovědi ve
                              vláknech, takže čísla sedí s Facebookem;
                              "toplevel" jen komentáře první úrovně
    COOLDOWN_HOURS          - jak dlouho po notifikaci mlčet u téhož
                              příspěvku (výchozí = WINDOW_HOURS)
    LOOKBACK_DAYS           - kolik dní zpět hledat příspěvky (výchozí 7)
    QUIET_HOURS             - noční klid ve tvaru "22-7" (výchozí), prázdná
                              hodnota = vypnuto. V klidu se dál měří, jen se
                              nenotifikuje; ráno přijde souhrn.
    TIMEZONE                - zóna pro noční klid (výchozí Europe/Prague)
    NIGHT_ESCALATION_FACTOR - kolikanásobek prahu probudí i v noci
                              (výchozí 3, hodnota 0 = nikdy nerušit)
    STATE_FILE              - cesta ke stavovému souboru
                              (výchozí state/fb_spike_state.json)
"""

import datetime
import email.message
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

GRAPH_API_VERSION = "v19.0"
# Klíč ve stavu, pod kterým se drží metadata běhu (ne příspěvek).
META_KEY = "_meta"


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        print(f"Chybí povinná proměnná prostředí: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fb-spike-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "fb-spike-watch/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8")


def fetch_posts(page_id, access_token, since_epoch, comment_filter="stream", limit=100):
    """Stáhne posty stránky za posledních N dní, s počtem komentářů.

    comment_filter="stream" počítá i odpovědi ve vláknech, takže číslo sedí
    s tím, co je vidět na Facebooku; "toplevel" počítá jen komentáře první
    úrovně (to je výchozí chování Graph API a vychází zhruba o třetinu níž).

    Facebook Graph API vrací výsledky obalené v {"data": [...], "paging": {...}}.
    """
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/posts"
    params = {
        "fields": (
            "id,message,created_time,permalink_url,"
            f"comments.summary(true).filter({comment_filter}).limit(0)"
        ),
        "since": str(since_epoch),
        "limit": str(limit),
        "access_token": access_token,
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"

    posts = []
    while url:
        try:
            body = http_get_json(url)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            print(f"Chyba Graph API ({e.code}): {detail}", file=sys.stderr)
            sys.exit(1)

        posts.extend(body.get("data", []))
        # Graph API stránkuje výsledky; sledujeme "next" kurzor, pokud existuje.
        # V "since" okně na jednu stránku obvykle nemá vůbec dojít, ale pro
        # jistotu (napr. výjimečně nabitý týden) stránkování respektujeme.
        url = body.get("paging", {}).get("next")

    return posts


def load_state(path, comment_filter):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            print(f"Varování: {path} nejde přečíst jako JSON, začínám s prázdným stavem.", file=sys.stderr)
            return {}

    # Změna způsobu počítání komentářů posune všechna čísla naráz (stream
    # počítá i odpovědi, toplevel ne). Porovnávat nové počty se starou
    # základnou by udělalo spike ze všech sledovaných příspěvků najednou,
    # takže historii radši zahodíme a začneme měřit znovu.
    stored = state.get(META_KEY, {}).get("filter")
    if stored != comment_filter:
        if stored is not None:
            print(
                f"Způsob počítání komentářů se změnil ({stored} -> {comment_filter}), "
                "historie se zahazuje a měření začíná znovu.",
                file=sys.stderr,
            )
        return {}

    # Starší verze skriptu ukládala jen číslo (dosažené pásmo po 50).
    # Takový záznam nenese historii v čase a pro měření přírůstku je
    # nepoužitelný — zahodíme ho a začneme u daného postu měřit znovu.
    return {
        k: v for k, v in state.items() if k != META_KEY and isinstance(v, dict)
    }


def save_state(path, state, comment_filter):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = dict(state)
    payload[META_KEY] = {"filter": comment_filter}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_created_time(value):
    """Graph API vrací čas ve tvaru 2026-08-03T10:15:22+0000. Vrací epoch, nebo None."""
    if not value:
        return None
    try:
        return int(datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except (ValueError, TypeError):
        return None


def plural(n, one, few, many):
    """České skloňování podle počtu: 1 hodinu, 2-4 hodiny, 5+ hodin."""
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def format_span(seconds):
    """Délka měření i se shodným přívlastkem: "poslední 2 hodiny",
    "posledních 28 minut". U 5 a víc se mění i tvar "poslední"."""
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        n, jednotka = minutes, plural(minutes, "minutu", "minuty", "minut")
    else:
        n = round(seconds / 3600)
        jednotka = plural(n, "hodinu", "hodiny", "hodin")
    posledni = plural(n, "poslední", "poslední", "posledních")
    return f"{posledni} {n} {jednotka}"


def required_delta(threshold, elapsed_seconds, window_seconds):
    """Kolik komentářů musí přibýt, aby to byl spike.

    U příspěvku, který sledujeme kratší dobu než celé okno, se práh poměrně
    zkrátí — jinak by čerstvý příspěvek s prudkým náběhem propadl jen proto,
    že ještě nestihl nasbírat počet odpovídající plnému oknu. Zároveň
    neklesne pod polovinu prahu, aby pár komentářů během chvilky nedělalo
    poplach.
    """
    if elapsed_seconds >= window_seconds:
        return threshold
    prorated = threshold * elapsed_seconds / window_seconds
    return max(prorated, threshold / 2)


def in_quiet_hours(now, tz_name, spec):
    """Je teď noční klid? Spec má tvar "22-7" (od 22:00 do 6:59 místního času).

    Prázdný spec = noční režim vypnutý.
    """
    if not spec or not spec.strip():
        return False

    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        print(f"Varování: QUIET_HOURS='{spec}' nejde přečíst, noční režim vypnut.", file=sys.stderr)
        return False

    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        # Bez tzdat radši hlásit ve špatnou hodinu než mlčet celý den.
        print(f"Varování: časovou zónu {tz_name} nelze načíst, používám UTC.", file=sys.stderr)
        tz = datetime.timezone.utc

    hour = datetime.datetime.fromtimestamp(now, tz).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Interval přes půlnoc, např. 22-7.
    return hour >= start or hour < end


def post_snippet(post, limit=160):
    """Popisek příspěvku do zprávy, kurzívou.

    Příspěvky obvykle začínají "👉 https://rspkt.cz/300011924 Vlastní text…".
    Vedoucí emoji ani zkrácený odkaz v upozornění k ničemu nejsou — odkaz na
    příspěvek je ve zprávě už jednou — takže se odloupnou a zbude jen text.
    Vrací prázdný řetězec, když po očištění nic nezůstane.
    """
    text = " ".join((post.get("message") or "").split())

    # Střídavě odloupávat vedoucí ozdoby a odkazy, dokud se něco mění:
    # posty mívají i "👉 odkaz 👉 odkaz text".
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r'^[^\w"\'„(\[]+', "", text)
        text = re.sub(r"^https?://\S+\s*", "", text)

    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit].rstrip(" ,.;:-") + "…"
    return f"_{text}_"


def compose(headline, post):
    """Zpráva = titulek, popisek příspěvku (když nějaký je) a odkaz."""
    parts = [headline, post_snippet(post), post.get("permalink_url", "")]
    return "\n".join(part for part in parts if part)


def send_night_escalation(sinks, post, delta, comment_count, elapsed):
    """V noci se běžně mlčí, tohle je ale moc velké na to nechat běžet do rána."""
    komentaru = plural(delta, "komentář", "komentáře", "komentářů")
    text = compose(
        f"🚨 *I přes noční klid:* u příspěvku přibylo za "
        f"{format_span(elapsed)} {delta} {komentaru} "
        f"(aktuálně {comment_count}). Nejspíš to chce moderaci hned.",
        post,
    )
    return deliver(sinks, text)


def send_night_summary(sinks, post, delta, comment_count, elapsed):
    """Ráno po nočním klidu: co se v noci semlelo."""
    komentaru = plural(delta, "komentář", "komentáře", "komentářů")
    text = compose(
        f"🌙 Přes noc u příspěvku přibylo až {delta} {komentaru} "
        f"za {format_span(elapsed)} (aktuálně {comment_count}).",
        post,
    )
    return deliver(sinks, text)


def baseline_for(post, entry, now, window_seconds):
    """Kolik komentářů měl příspěvek na začátku okna a jak dlouhé měření je.

    Vrací (počet_komentářů, epoch_měření) nebo None, pokud zatím není z čeho
    přírůstek počítat (post známe příliš krátce).
    """
    window_start = now - window_seconds

    # Příspěvek vznikl až uvnitř okna → všechny jeho komentáře přibyly v okně.
    created = parse_created_time(post.get("created_time"))
    if created is not None and created >= window_start:
        return 0, created

    samples = entry.get("samples", [])
    if not samples:
        return None

    # Nejnovější měření, které je ještě před začátkem okna = správná základna.
    before = [s for s in samples if s[0] <= window_start]
    if before:
        return before[-1][1], before[-1][0]

    # Okno zatím není celé pokryté (post sledujeme kratší dobu než window).
    # Bereme nejstarší, co máme — přírůstek tak spíš podhodnotíme, což je
    # bezpečnější než hlásit planý poplach.
    return samples[0][1], samples[0][0]


def send_spike_notification(sinks, post, delta, comment_count, elapsed):
    komentaru = plural(delta, "komentář", "komentáře", "komentářů")
    text = compose(
        f"U příspěvku přibylo za {format_span(elapsed)} "
        f"{delta} {komentaru} (aktuálně {comment_count}).",
        post,
    )
    return deliver(sinks, text)


def webhook_json(url, payload, name):
    """Pošle JSON na webhook. Vrací True při úspěchu, jinak zaloguje důvod."""
    try:
        status, body = http_post_json(url, payload)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"{name} selhal ({e.code}): {detail}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"{name} nedostupný: {e.reason}", file=sys.stderr)
        return False
    except ValueError as e:
        # Nevalidní URL (prázdný/poškozený secret) shodí Request() dřív, než
        # se stihne pokusit o síťové spojení — HTTPError/URLError to
        # nezachytí. Bez tohohle by pádem tohoto kanálu spadl celý běh a
        # neuložil by se stav, takže by výpadek "zapomněl" i naměřené
        # hodnoty za celou dobu, kdy secret chyběl.
        print(f"{name} má nesprávně nastavenou URL ({url!r}): {e}", file=sys.stderr)
        return False

    if status not in (200, 204):
        print(f"{name} vrátil status {status}: {body}", file=sys.stderr)
        return False
    return True


def send_email(text):
    """Pošle upozornění mailem přes SMTP. Předmět je první řádek zprávy."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("MAIL_FROM") or user
    recipients = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]

    lines = text.split("\n")
    msg = email.message.EmailMessage()
    msg["Subject"] = lines[0][:200]
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        print(f"E-mail se nepodařilo odeslat: {e}", file=sys.stderr)
        return False
    return True


def build_sinks():
    """Kanály, do kterých se hlásí. Nastavuje se tím, co je v prostředí.

    Slack i Google Chat berou stejný tvar {"text": ...}, takže se liší jen
    adresou. Alespoň jeden kanál musí být nastavený, jinak by skript měřil
    do prázdna.
    """
    sinks = []

    slack = os.environ.get("SLACK_WEBHOOK_URL")
    if slack:
        sinks.append(("Slack", lambda t, u=slack: webhook_json(u, {"text": t}, "Slack")))

    chat = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL")
    if chat:
        sinks.append(
            ("Google Chat", lambda t, u=chat: webhook_json(u, {"text": t}, "Google Chat"))
        )

    if os.environ.get("SMTP_HOST") and os.environ.get("MAIL_TO"):
        sinks.append(("e-mail", send_email))

    if not sinks:
        print(
            "Není nastavený žádný kanál pro upozornění — nastav SLACK_WEBHOOK_URL, "
            "GOOGLE_CHAT_WEBHOOK_URL nebo SMTP_HOST + MAIL_TO.",
            file=sys.stderr,
        )
        sys.exit(1)
    return sinks


def deliver(sinks, text):
    """Rozešle zprávu do všech nastavených kanálů.

    Za doručené se považuje, když uspěl aspoň jeden kanál. Kdyby stačilo až
    "všechny", jeden rozbitý kanál by způsobil, že se zpráva pošle znovu při
    každém běhu — a do funkčních kanálů by chodila pořád dokola.
    """
    ok_any = False
    for name, send in sinks:
        try:
            ok = send(text)
        except Exception as e:
            # Poslední pojistka: ať selže kanál jakkoli neočekávaně (špatný
            # secret, síťová knihovna, cokoli), nesmí to strhnout celý běh —
            # jinak by se neuložil stav a výpadek jednoho kanálu by "smazal"
            # i naměřená data za dobu, kdy byl rozbitý.
            print(f"Kanál {name} spadl neočekávaně: {e}", file=sys.stderr)
            ok = False
        if ok:
            ok_any = True
        else:
            print(f"Kanál {name} zprávu nepřijal.", file=sys.stderr)
    return ok_any


def main():
    page_id = env("FB_PAGE_ID", required=True)
    access_token = env("FB_PAGE_ACCESS_TOKEN", required=True)
    window_hours = int(env("WINDOW_HOURS", "2"))
    threshold = int(env("DELTA_THRESHOLD", "25"))
    cooldown_hours = int(env("COOLDOWN_HOURS", str(window_hours)))
    lookback_days = int(env("LOOKBACK_DAYS", "7"))
    state_file = env("STATE_FILE", "state/fb_spike_state.json")
    comment_filter = env("COMMENT_FILTER", "stream")
    quiet_spec = env("QUIET_HOURS", "22-7")
    tz_name = env("TIMEZONE", "Europe/Prague")
    escalation_factor = float(env("NIGHT_ESCALATION_FACTOR", "3"))
    # 0 = v noci nikdy nerušit, ani při sebevětším náporu.
    escalation_at = threshold * escalation_factor if escalation_factor > 0 else None

    sinks = build_sinks()

    now = int(time.time())
    window_seconds = window_hours * 3600
    since_epoch = now - lookback_days * 86400
    quiet = in_quiet_hours(now, tz_name, quiet_spec)

    posts = fetch_posts(page_id, access_token, since_epoch, comment_filter)
    state = load_state(state_file, comment_filter)

    sent = 0
    failed = 0
    seen_ids = set()
    for post in posts:
        post_id = post.get("id")
        if not post_id:
            continue
        seen_ids.add(post_id)

        comment_count = (
            post.get("comments", {}).get("summary", {}).get("total_count", 0)
        )
        entry = state.setdefault(post_id, {"samples": []})

        base = baseline_for(post, entry, now, window_seconds)

        # Měření zapisujeme vždy, i když se zrovna nenotifikuje — jinak by
        # nebylo z čeho počítat přírůstek při příštím běhu.
        entry["samples"].append([now, comment_count])
        # Držíme dvojnásobek okna, ať je vždy po ruce měření z doby *před*
        # jeho začátkem, i když se běh jednou vynechá.
        cutoff = now - 2 * window_seconds
        entry["samples"] = [s for s in entry["samples"] if s[0] >= cutoff]

        # Skončil noční klid a v noci se u tohohle příspěvku něco dělo →
        # ráno se to ohlásí souhrnem. Spike z půl třetí v noci by jinak
        # zmizel, protože do rána vypadne z okna.
        if not quiet and "pending_delta" in entry:
            ok = send_night_summary(
                sinks,
                post,
                entry["pending_delta"],
                entry.get("pending_count", comment_count),
                entry.get("pending_elapsed", window_seconds),
            )
            if ok:
                entry.pop("pending_delta", None)
                entry.pop("pending_count", None)
                entry.pop("pending_elapsed", None)
                entry["last_alert_ts"] = now
                sent += 1
            else:
                failed += 1
            continue

        if base is None:
            continue

        base_count, base_ts = base
        delta = comment_count - base_count
        elapsed = max(now - base_ts, 60)
        if delta <= required_delta(threshold, elapsed, window_seconds):
            continue

        # Prudká diskuze běží klidně půl dne; bez cooldownu by hlásila
        # při každém běhu znovu.
        last_alert = entry.get("last_alert_ts", 0)
        if now - last_alert < cooldown_hours * 3600:
            continue

        if quiet:
            # Opravdu velký nápor se ozve i v noci — pět hodin nemoderované
            # diskuze je horší než jeden probuzený člověk.
            if escalation_at and delta >= escalation_at:
                ok = send_night_escalation(
                    sinks, post, delta, comment_count, elapsed
                )
                if ok:
                    entry["last_alert_ts"] = now
                    # Ráno už není co dohlašovat, tohle bylo ohlášeno hned.
                    entry.pop("pending_delta", None)
                    entry.pop("pending_count", None)
                    entry.pop("pending_elapsed", None)
                    sent += 1
                else:
                    failed += 1
                continue

            # Jinak se neruší, jen se zapamatuje nejsilnější přírůstek.
            if delta > entry.get("pending_delta", 0):
                entry["pending_delta"] = delta
                entry["pending_count"] = comment_count
                entry["pending_elapsed"] = elapsed
            continue

        ok = send_spike_notification(
            sinks, post, delta, comment_count, elapsed
        )
        if ok:
            entry["last_alert_ts"] = now
            sent += 1
        else:
            failed += 1
        # Pokud se odeslání nepovede, last_alert_ts NEnastavujeme —
        # příští běh to zkusí znovu, místo aby se spike tiše ztratil.

    # Posty, které vypadly ze sledovaného okna, se už v odpovědi neobjeví;
    # bez úklidu by stavový soubor rostl donekonečna.
    for stale_id in set(state) - seen_ids:
        del state[stale_id]

    save_state(state_file, state, comment_filter)
    print(
        f"Zpracováno postů: {len(posts)}, odesláno notifikací: {sent}"
        + (" (noční klid — notifikace odloženy na ráno)" if quiet else "")
        + (f", neodesláno (chyba Slacku): {failed}" if failed else "")
    )


if __name__ == "__main__":
    main()
