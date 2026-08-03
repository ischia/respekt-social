#!/usr/bin/env python3
"""
FB Comment Spike Watch
Sleduje příspěvky na FB stránce Respektu za posledních 7 dní a upozorňuje
na Slacku, když příspěvku *přibude* za sledované okno (výchozí 2 hodiny)
víc než daný počet komentářů (výchozí 30).

Hlídá se tedy rychlost přírůstku, ne absolutní počet: příspěvek, který
nasbíral 300 komentářů rovnoměrně za týden, je nezajímavý; příspěvek,
kterému jich přibylo 150 za dopoledne, je událost.

Stav (naměřené počty komentářů v čase) se ukládá do JSON souboru ve stejném
repu, aby šlo přírůstek mezi běhy vůbec spočítat.

Vyžaduje proměnné prostředí:
    FB_PAGE_ID              - ID facebookové stránky
    FB_PAGE_ACCESS_TOKEN    - Page Access Token s pages_read_engagement
                              a pages_read_user_content
    SLACK_WEBHOOK_URL       - Incoming Webhook URL pro kanál

Volitelné:
    WINDOW_HOURS            - délka okna pro měření přírůstku (výchozí 2)
    DELTA_THRESHOLD         - kolik komentářů musí v okně přibýt (výchozí 30)
    COOLDOWN_HOURS          - jak dlouho po notifikaci mlčet u téhož
                              příspěvku (výchozí = WINDOW_HOURS)
    LOOKBACK_DAYS           - kolik dní zpět hledat příspěvky (výchozí 7)
    QUIET_HOURS             - noční klid ve tvaru "22-7" (výchozí), prázdná
                              hodnota = vypnuto. V klidu se dál měří, jen se
                              nenotifikuje; ráno přijde souhrn.
    TIMEZONE                - zóna pro noční klid (výchozí Europe/Prague)
    STATE_FILE              - cesta ke stavovému souboru
                              (výchozí state/fb_spike_state.json)
"""

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

GRAPH_API_VERSION = "v19.0"


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


def fetch_posts(page_id, access_token, since_epoch, limit=100):
    """Stáhne posty stránky za posledních N dní, s počtem komentářů.

    Facebook Graph API vrací výsledky obalené v {"data": [...], "paging": {...}}.
    """
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{page_id}/posts"
    params = {
        "fields": "id,message,created_time,permalink_url,comments.summary(true).limit(0)",
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


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            state = json.load(f)
        except json.JSONDecodeError:
            print(f"Varování: {path} nejde přečíst jako JSON, začínám s prázdným stavem.", file=sys.stderr)
            return {}

    # Starší verze skriptu ukládala jen číslo (dosažené pásmo po 50).
    # Takový záznam nenese historii v čase a pro měření přírůstku je
    # nepoužitelný — zahodíme ho a začneme u daného postu měřit znovu.
    return {k: v for k, v in state.items() if isinstance(v, dict)}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
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


def send_night_summary(webhook_url, post, delta, comment_count, window_hours):
    """Ráno po nočním klidu: co se v noci semlelo."""
    message = post.get("message", "").strip()
    snippet = (message[:120] + "…") if len(message) > 120 else message
    hodiny = plural(window_hours, "hodinu", "hodiny", "hodin")
    komentaru = plural(delta, "komentář", "komentáře", "komentářů")
    text = (
        f":crescent_moon: Přes noc u příspěvku přibylo až {delta} {komentaru} "
        f"za {window_hours} {hodiny} (aktuálně {comment_count}).\n"
        f"{snippet}\n"
        f"{post.get('permalink_url', '')}"
    )
    return post_to_slack(webhook_url, text)


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


def send_slack_notification(webhook_url, post, delta, comment_count, window_hours, threshold):
    message = post.get("message", "").strip()
    snippet = (message[:120] + "…") if len(message) > 120 else message
    hodiny = plural(window_hours, "hodinu", "hodiny", "hodin")
    komentaru = plural(threshold, "komentář", "komentáře", "komentářů")
    text = (
        f":rotating_light: U příspěvku přibylo za poslední {window_hours} {hodiny} "
        f"více než {threshold} {komentaru} (aktuálně {comment_count}, "
        f"přírůstek {delta}).\n"
        f"{snippet}\n"
        f"{post.get('permalink_url', '')}"
    )
    return post_to_slack(webhook_url, text)


def post_to_slack(webhook_url, text):
    # Selhání Slacku nesmí shodit celý běh — jinak by se neuložil stav a
    # příště by se už odeslané notifikace poslaly znovu.
    try:
        status, body = http_post_json(webhook_url, {"text": text})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"Slack webhook selhal ({e.code}): {detail}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"Slack webhook nedostupný: {e.reason}", file=sys.stderr)
        return False

    if status != 200:
        print(f"Slack webhook vrátil status {status}: {body}", file=sys.stderr)
        return False
    return True


def main():
    page_id = env("FB_PAGE_ID", required=True)
    access_token = env("FB_PAGE_ACCESS_TOKEN", required=True)
    slack_webhook = env("SLACK_WEBHOOK_URL", required=True)
    window_hours = int(env("WINDOW_HOURS", "2"))
    threshold = int(env("DELTA_THRESHOLD", "30"))
    cooldown_hours = int(env("COOLDOWN_HOURS", str(window_hours)))
    lookback_days = int(env("LOOKBACK_DAYS", "7"))
    state_file = env("STATE_FILE", "state/fb_spike_state.json")
    quiet_spec = env("QUIET_HOURS", "22-7")
    tz_name = env("TIMEZONE", "Europe/Prague")

    now = int(time.time())
    window_seconds = window_hours * 3600
    since_epoch = now - lookback_days * 86400
    quiet = in_quiet_hours(now, tz_name, quiet_spec)

    posts = fetch_posts(page_id, access_token, since_epoch)
    state = load_state(state_file)

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
                slack_webhook,
                post,
                entry["pending_delta"],
                entry.get("pending_count", comment_count),
                window_hours,
            )
            if ok:
                entry.pop("pending_delta", None)
                entry.pop("pending_count", None)
                entry["last_alert_ts"] = now
                sent += 1
            else:
                failed += 1
            continue

        if base is None:
            continue

        base_count, _base_ts = base
        delta = comment_count - base_count
        if delta <= threshold:
            continue

        # Prudká diskuze běží klidně půl dne; bez cooldownu by hlásila
        # při každém běhu znovu.
        last_alert = entry.get("last_alert_ts", 0)
        if now - last_alert < cooldown_hours * 3600:
            continue

        if quiet:
            # V noci se neruší, jen se zapamatuje nejsilnější přírůstek.
            if delta > entry.get("pending_delta", 0):
                entry["pending_delta"] = delta
                entry["pending_count"] = comment_count
            continue

        ok = send_slack_notification(
            slack_webhook, post, delta, comment_count, window_hours, threshold
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

    save_state(state_file, state)
    print(
        f"Zpracováno postů: {len(posts)}, odesláno notifikací: {sent}"
        + (" (noční klid — notifikace odloženy na ráno)" if quiet else "")
        + (f", neodesláno (chyba Slacku): {failed}" if failed else "")
    )


if __name__ == "__main__":
    main()
