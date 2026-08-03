"""Testy detekční logiky. Spouští se `python3 tests/test_spike_watch.py`.

Graph API ani Slack se nevolají — obojí je nahrazeno falešnou implementací,
takže testy běží offline a bez tokenů.
"""

import datetime
import importlib.util
import json
import os
import pathlib
import tempfile
import time
import urllib.error

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "fb_comment_spike_watch.py"
)
spec = importlib.util.spec_from_file_location("watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

NOW = int(time.time())
H = 3600


def iso(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S%z")


def post(pid, count, age_hours=48, msg="text"):
    return {
        "id": pid,
        "message": msg,
        "created_time": iso(NOW - age_hours * H),
        "permalink_url": f"https://fb.com/{pid}",
        "comments": {"summary": {"total_count": count}},
    }


def run(posts, state, slack_fails=False, defaults=False, **env):
    # Většina testů byla psaná proti oknu 4h/100; drží se explicitně, aby
    # změna výchozích hodnot skriptu nerozbila jejich očekávání.
    if not defaults:
        env = {"WINDOW_HOURS": "4", "DELTA_THRESHOLD": "100", **env}
    sent = []
    watch.fetch_posts = lambda *a, **k: posts

    def fake_post(url, payload):
        if slack_fails:
            raise urllib.error.URLError("connection refused")
        sent.append(payload["text"])
        return 200, "ok"

    watch.http_post_json = fake_post

    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump(state, f)

    for k in ("WINDOW_HOURS", "DELTA_THRESHOLD", "COOLDOWN_HOURS"):
        os.environ.pop(k, None)
    os.environ.update(
        FB_PAGE_ID="1",
        FB_PAGE_ACCESS_TOKEN="t",
        SLACK_WEBHOOK_URL="https://hooks.slack.com/x",
        STATE_FILE=path,
        **env,
    )
    watch.main()
    with open(path) as f:
        new_state = json.load(f)
    os.unlink(path)
    return sent, new_state


def samples(*pairs):
    """pairs = (hodin_zpět, počet)"""
    return {"samples": [[NOW - h * H, c] for h, c in pairs]}


# --- skloňování ---
assert watch.plural(1, "hodinu", "hodiny", "hodin") == "hodinu"
assert watch.plural(4, "hodinu", "hodiny", "hodin") == "hodiny"
assert watch.plural(5, "hodinu", "hodiny", "hodin") == "hodin"
print("OK: skloňování")

# --- 1. přírůstek přes práh vs. pod prahem ---
sent, state = run(
    [
        post("A", 1251),  # 1251-1100 = 151 > 100 -> hlásit
        post("B", 1251),  # 1251-1200 =  51 <= 100 -> ticho
        post("C", 900),   # 900-500 = 400, ale starý týden -> baseline ze 4h
    ],
    {
        "A": samples((6, 1000), (4, 1100), (2, 1200)),
        "B": samples((6, 1000), (4, 1200), (2, 1240)),
        "C": samples((6, 500), (4, 880), (2, 890)),  # 900-880 = 20 -> ticho
    },
)
assert len(sent) == 1, sent
assert "za poslední 4 hodiny" in sent[0], sent[0]
assert "více než 100 komentářů" in sent[0], sent[0]
assert "aktuálně 1251" in sent[0], sent[0]
assert state["A"]["last_alert_ts"] > 0
assert "last_alert_ts" not in state["B"]
print("OK: přírůstek v okně")
print("   zpráva:", sent[0].split("\n")[0])

# --- 2. baseline se bere od začátku okna, ne od posledního běhu ---
# +60 mezi běhy, ale +180 za 4h -> musí hlásit
sent, _ = run(
    [post("A", 1180)],
    {"A": samples((6, 900), (4, 1000), (2, 1120))},
)
assert len(sent) == 1, sent
print("OK: základna je začátek okna (ne poslední běh)")

# --- 3. nový příspěvek mladší než okno: baseline = 0 od vzniku ---
sent, _ = run([post("NEW", 150, age_hours=1)], {})
assert len(sent) == 1, sent
assert "aktuálně 150" in sent[0]
print("OK: čerstvý příspěvek hlásí i bez historie")

# nový, ale pod prahem
sent, _ = run([post("NEW", 80, age_hours=1)], {})
assert sent == [], sent
print("OK: čerstvý příspěvek pod prahem mlčí")

# --- 4. poprvé viděný starý příspěvek nehlásí (není z čeho počítat) ---
sent, state = run([post("OLD", 5000, age_hours=72)], {})
assert sent == [], sent
assert state["OLD"]["samples"] == [[NOW, 5000]] or len(state["OLD"]["samples"]) == 1
print("OK: první pozorování starého postu jen měří, nehlásí")

# --- 5. cooldown: sustained spike nehlásí každý běh ---
st = samples((6, 1000), (4, 1100), (2, 1200))
st["last_alert_ts"] = NOW - 1 * H  # notifikováno před hodinou
sent, _ = run([post("A", 1400)], {"A": st})
assert sent == [], sent
print("OK: cooldown drží pusu")

st["last_alert_ts"] = NOW - 5 * H  # cooldown vypršel
sent, _ = run([post("A", 1400)], {"A": st})
assert len(sent) == 1, sent
print("OK: po vypršení cooldownu hlásí znovu")

# --- 6. konfigurovatelnost ---
sent, _ = run(
    [post("A", 1160)],
    {"A": samples((3, 1000), (2, 1100))},
    WINDOW_HOURS="2",
    DELTA_THRESHOLD="50",
)
assert len(sent) == 1, sent
assert "za poslední 2 hodiny" in sent[0] and "více než 50" in sent[0], sent[0]
print("OK: WINDOW_HOURS + DELTA_THRESHOLD")

sent, _ = run([post("A", 200, age_hours=1)], {}, WINDOW_HOURS="1", DELTA_THRESHOLD="99")
assert "za poslední 1 hodinu" in sent[0], sent[0]
print("OK: skloňování v reálné zprávě")

# --- 7. migrace ze starého formátu stavu (číslo -> dict) ---
sent, state = run([post("A", 300)], {"A": 250})
assert sent == [], sent
assert isinstance(state["A"], dict), state
print("OK: starý formát stavu se zahodí bez pádu")

# --- 8. úklid postů mimo okno + ořez historie ---
sent, state = run([post("A", 10)], {"OLD": samples((2, 5)), "A": samples((2, 8))})
assert "OLD" not in state, state
print("OK: úklid postů mimo okno")

old = samples((20, 1), (10, 2), (9, 3), (2, 4))  # 20h a 10h jsou za hranicí 8h
sent, state = run([post("A", 5)], {"A": old})
kept = [s[0] for s in state["A"]["samples"]]
assert all(t >= NOW - 8 * H for t in kept), kept
assert len(kept) == 2, kept  # -2h a nové měření
print("OK: historie ořezaná na 2× okno")

# --- 9. selhání Slacku ---
sent, state = run(
    [post("A", 1251)],
    {"A": samples((6, 1000), (4, 1100), (2, 1200))},
    slack_fails=True,
)
assert sent == [], sent
assert "last_alert_ts" not in state["A"], state
print("OK: selhání Slacku neshodí běh a nezapíše cooldown")

print("\nVšechny testy prošly.")

# ==================== NOČNÍ REŽIM ====================
print("\n--- noční režim ---")

# --- in_quiet_hours: interval přes půlnoc ---
def at_hour(h, tz="Europe/Prague"):
    """epoch odpovídající dané místní hodině."""
    import zoneinfo
    d = datetime.datetime.now(zoneinfo.ZoneInfo(tz)).replace(
        hour=h, minute=30, second=0, microsecond=0
    )
    return int(d.timestamp())

for h in (22, 23, 0, 3, 6):
    assert watch.in_quiet_hours(at_hour(h), "Europe/Prague", "22-7"), h
for h in (7, 8, 12, 18, 21):
    assert not watch.in_quiet_hours(at_hour(h), "Europe/Prague", "22-7"), h
print("OK: 22-7 přes půlnoc")

# nepřetáčený interval
assert watch.in_quiet_hours(at_hour(3), "Europe/Prague", "1-5")
assert not watch.in_quiet_hours(at_hour(6), "Europe/Prague", "1-5")
print("OK: interval bez přechodu půlnoci")

# vypnuto / nesmysl
assert not watch.in_quiet_hours(at_hour(3), "Europe/Prague", "")
assert not watch.in_quiet_hours(at_hour(3), "Europe/Prague", "nesmysl")
assert not watch.in_quiet_hours(at_hour(3), "Europe/Prague", "5-5")
print("OK: vypnutý / neplatný QUIET_HOURS mlčí bezpečně")

# neexistující zóna nesmí shodit běh
assert isinstance(watch.in_quiet_hours(at_hour(3), "Neexistuje/Zona", "22-7"), bool)
print("OK: neplatná zóna nespadne")

# --- v noci se neposílá, jen zapamatuje ---
QUIET = {"QUIET_HOURS": "0-23"}   # celý den je "noc"
DEN = {"QUIET_HOURS": ""}          # noční režim vypnutý

sent, state = run(
    [post("A", 1251)],
    {"A": samples((6, 1000), (4, 1100), (2, 1200))},
    **QUIET,
)
assert sent == [], sent
assert state["A"]["pending_delta"] == 151, state
assert state["A"]["pending_count"] == 1251, state
assert "last_alert_ts" not in state["A"]
print("OK: v noci se nenotifikuje, jen zapamatuje")

# --- ráno přijde souhrn ---
st = samples((6, 1000), (4, 1100), (2, 1200))
st["pending_delta"] = 151
st["pending_count"] = 1251
sent, state = run([post("A", 1300)], {"A": st}, **DEN)
assert len(sent) == 1, sent
assert "Přes noc" in sent[0], sent[0]
assert "až 151 komentářů" in sent[0], sent[0]
assert "aktuálně 1251" in sent[0], sent[0]
assert "pending_delta" not in state["A"], state
assert state["A"]["last_alert_ts"] > 0
print("OK: ráno dorazí souhrn a pending se smaže")
print("   zpráva:", sent[0].split("\n")[0])

# --- v noci se drží nejsilnější přírůstek, ne poslední ---
st = samples((6, 1000), (4, 1100), (2, 1200))
st["pending_delta"] = 300
sent, state = run([post("A", 1251)], {"A": st}, **QUIET)  # delta 151 < 300
assert sent == [], sent
assert state["A"]["pending_delta"] == 300, state
print("OK: pending drží maximum")

# --- selhání Slacku u ranního souhrnu pending nesmaže ---
st = samples((6, 1000), (4, 1100), (2, 1200))
st["pending_delta"] = 151
st["pending_count"] = 1251
sent, state = run([post("A", 1300)], {"A": st}, slack_fails=True, **DEN)
assert sent == [], sent
assert state["A"]["pending_delta"] == 151, state
print("OK: selhání Slacku nezahodí ranní souhrn")

# --- pod prahem se v noci nic nezapamatuje ---
sent, state = run(
    [post("B", 1251)],
    {"B": samples((6, 1000), (4, 1200), (2, 1240))},
    **QUIET,
)
assert sent == [], sent
assert "pending_delta" not in state["B"], state
print("OK: pod prahem se v noci nic neukládá")

# --- měření běží i v noci (kvůli základně pro ráno) ---
sent, state = run([post("A", 500)], {"A": samples((2, 490))}, **QUIET)
assert len(state["A"]["samples"]) == 2, state
print("OK: v noci se dál vzorkuje")

print("\nVšechny testy prošly.")

# ==================== VÝCHOZÍ HODNOTY (2h / 30) ====================
print("\n--- výchozí hodnoty ---")

# 35 komentářů za 2h -> hlásit
sent, _ = run(
    [post("A", 35)],
    {"A": samples((3, 0), (2, 0), (1, 12))},
    defaults=True, QUIET_HOURS="",
)
assert len(sent) == 1, sent
assert "za poslední 2 hodiny" in sent[0], sent[0]
assert "více než 30 komentářů" in sent[0], sent[0]
print("OK: výchozí 2h/30 hlásí")
print("   zpráva:", sent[0].split("\n")[0])

# 25 za 2h -> ticho
sent, _ = run(
    [post("B", 25)],
    {"B": samples((3, 0), (2, 0), (1, 10))},
    defaults=True, QUIET_HOURS="",
)
assert sent == [], sent
print("OK: výchozí 2h/30 pod prahem mlčí")

# typický mrtvý příspěvek (90 % případů) nikdy nehlásí
sent, state = run(
    [post("DEAD", 0)],
    {"DEAD": samples((3, 0), (2, 0), (1, 0))},
    defaults=True, QUIET_HOURS="",
)
assert sent == [], sent
print("OK: příspěvek bez komentářů nehlásí")

print("\nVšechny testy prošly.")
