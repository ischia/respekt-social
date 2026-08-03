#!/usr/bin/env python3
"""
FB Comment Spike Watch
Sleduje příspěvky na FB stránce Respektu za posledních 7 dní a upozorňuje
na Slacku, když příspěvek překročí další pásmo počtu komentářů (po 50).

Stav (nejvyšší nahlášené pásmo pro každý příspěvek) se ukládá do JSON
souboru ve stejném repu, aby se notifikace neposílaly opakovaně.

Vyžaduje proměnné prostředí:
    FB_PAGE_ID              - ID facebookové stránky
    FB_PAGE_ACCESS_TOKEN    - Page Access Token s pages_read_engagement
                              a pages_read_user_content
    SLACK_WEBHOOK_URL       - Incoming Webhook URL pro kanál

Volitelné:
    TIER_SIZE               - velikost pásma v komentářích (výchozí 50)
    LOOKBACK_DAYS           - kolik dní zpět hledat příspěvky (výchozí 7)
    STATE_FILE              - cesta ke stavovému souboru
                              (výchozí state/fb_spike_tiers.json)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
            return json.load(f)
        except json.JSONDecodeError:
            print(f"Varování: {path} nejde přečíst jako JSON, začínám s prázdným stavem.", file=sys.stderr)
            return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def current_tier(comment_count, tier_size):
    """Nejvyšší dosažené pásmo. 0-49 -> 0 (žádný spike), 50-99 -> 50, atd."""
    return (comment_count // tier_size) * tier_size


def send_slack_notification(webhook_url, post, tier, comment_count, lookback_days):
    message = post.get("message", "").strip()
    snippet = (message[:120] + "…") if len(message) > 120 else message
    text = (
        f":rotating_light: Příspěvek překročil *{tier}* komentářů "
        f"(aktuálně {comment_count}) za posledních {lookback_days} dní.\n"
        f"{snippet}\n"
        f"{post.get('permalink_url', '')}"
    )
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
    tier_size = int(env("TIER_SIZE", "50"))
    lookback_days = int(env("LOOKBACK_DAYS", "7"))
    state_file = env("STATE_FILE", "state/fb_spike_tiers.json")

    since_epoch = int(time.time()) - lookback_days * 86400

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
        tier = current_tier(comment_count, tier_size)

        # Chybějící klíč = post ještě nikdy nesledovaný = pásmo 0.
        # Žádný Aggregator, žádné hádání, jestli "záznam" existuje.
        last_tier = state.get(post_id, 0)

        if tier > last_tier:
            ok = send_slack_notification(
                slack_webhook, post, tier, comment_count, lookback_days
            )
            if ok:
                state[post_id] = tier
                sent += 1
            else:
                failed += 1
            # Pokud se odeslání nepovede, last_tier NEaktualizujeme —
            # příští běh to zkusí znovu, místo aby se spike tiše ztratil.

    # Posty, které vypadly z okna, se už v odpovědi nikdy neobjeví; bez
    # úklidu by stavový soubor rostl donekonečna.
    for stale_id in set(state) - seen_ids:
        del state[stale_id]

    save_state(state_file, state)
    print(
        f"Zpracováno postů: {len(posts)}, odesláno notifikací: {sent}"
        + (f", neodesláno (chyba Slacku): {failed}" if failed else "")
    )


if __name__ == "__main__":
    main()
