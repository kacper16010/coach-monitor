from urllib.request import Request, urlopen
import csv
import html
import re
import unicodedata
from difflib import SequenceMatcher
from playwright.sync_api import sync_playwright
import argparse
import os


SOURCE_CONFIG_FILE = "league_sources.csv"


LEAGUES = [
    {
        "league": "Ekstraklasa",
        "group": "",
        # HERE CHANGE LINK FOR EKSTRAKLASA SUPERSCORE
        "superscore_table_url": "https://superscore.live/pl-PL/pilka-nozna/rozgrywki/ekstraklasa/0ayigwtr/klasyfikacja?season=33WqTu7gkUPTXyAK0iMzm",
        # HERE CHANGE LINK FOR EKSTRAKLASA 90MINUT
        "ninetyminut_table_url": "http://www.90minut.pl/liga/1/liga14675.html",
        "enabled": True,
    },

    {
        "league": "1 Liga",
        "group": "",
        # HERE CHANGE LINK FOR 1 LIGA SUPERSCORE
        "superscore_table_url": "https://superscore.live/pl-PL/pilka-nozna/rozgrywki/i-liga/epo3jyw3/klasyfikacja?season=33bPCIgEKv63MQ0RRoPx7",
        # HERE CHANGE LINK FOR 1 LIGA 90MINUT
        "ninetyminut_table_url": "http://www.90minut.pl/liga/1/liga14676.html",
        "enabled": True,
    },

    {
        "league": "2 Liga",
        "group": "",
        # HERE CHANGE LINK FOR 2 LIGA SUPERSCORE
        "superscore_table_url": "https://superscore.live/pl-PL/pilka-nozna/rozgrywki/ii-liga/8qiy8yxl/klasyfikacja?season=33fxuk1JOt34NAYfWKDQt",
        # HERE CHANGE LINK FOR 2 LIGA 90MINUT
        "ninetyminut_table_url": "http://www.90minut.pl/liga/1/liga14677.html",
        "enabled": True,
    },

    {
        "league": "3 Liga",
        "group": "Group 1",
        # HERE CHANGE LINK FOR 3 LIGA GROUP 1 SUPERSCORE
        "superscore_table_url": None,
        # HERE CHANGE LINK FOR 3 LIGA GROUP 1 90MINUT
        "ninetyminut_table_url": None,
        "enabled": False,
    },
    {
        "league": "3 Liga",
        "group": "Group 2",
        # HERE CHANGE LINK FOR 3 LIGA GROUP 2 SUPERSCORE
        "superscore_table_url": None,
        # HERE CHANGE LINK FOR 3 LIGA GROUP 2 90MINUT
        "ninetyminut_table_url": None,
        "enabled": False,
    },
    {
        "league": "3 Liga",
        "group": "Group 3",
        # HERE CHANGE LINK FOR 3 LIGA GROUP 3 SUPERSCORE
        "superscore_table_url": None,
        # HERE CHANGE LINK FOR 3 LIGA GROUP 3 90MINUT
        "ninetyminut_table_url": None,
        "enabled": False,
    },
    {
        "league": "3 Liga",
        "group": "Group 4",
        # HERE CHANGE LINK FOR 3 LIGA GROUP 4 SUPERSCORE
        "superscore_table_url": None,
        # HERE CHANGE LINK FOR 3 LIGA GROUP 4 90MINUT
        "ninetyminut_table_url": None,
        "enabled": False,
    },
]


FOURTH_LEAGUE_REGIONS = [
    "Dolnoslaskie",
    "Kujawsko-Pomorskie",
    "Lubelskie",
    "Lubuskie",
    "Lodzkie",
    "Malopolskie",
    "Mazowieckie",
    "Opolskie",
    "Podkarpackie",
    "Podlaskie",
    "Pomorskie",
    "Slaskie",
    "Swietokrzyskie",
    "Warminsko-Mazurskie",
    "Wielkopolskie",
    "Zachodniopomorskie",
]


for region in FOURTH_LEAGUE_REGIONS:
    LEAGUES.append({
        "league": "4 Liga",
        "group": region,
        "superscore_table_url": None,
        "ninetyminut_table_url": None,
        "enabled": False,
    })


def parse_enabled(value, superscore_table_url, ninetyminut_table_url):
    value = str(value or "").strip().lower()

    if value in ("false", "0", "no", "n", "disabled"):
        return False
    if value in ("true", "1", "yes", "y", "enabled"):
        return True

    return bool(superscore_table_url and ninetyminut_table_url)


def load_leagues_from_source_config(path=SOURCE_CONFIG_FILE):
    if not os.path.exists(path):
        return LEAGUES

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    leagues = []

    for row in rows:
        league = str(row.get("league", "")).strip()
        group = str(row.get("group", "")).strip()
        superscore_table_url = str(row.get("superscore_table_url", "")).strip()
        ninetyminut_table_url = str(row.get("ninetyminut_table_url", "")).strip()

        if not league:
            continue

        leagues.append({
            "league": league,
            "group": group,
            "superscore_table_url": superscore_table_url or None,
            "ninetyminut_table_url": ninetyminut_table_url or None,
            "enabled": parse_enabled(
                row.get("enabled", ""),
                superscore_table_url,
                ninetyminut_table_url,
            ),
        })

    return leagues


LEAGUES = load_leagues_from_source_config()


SPECIAL_MATCHES = {
    "piast": "piast gliwice",
    "lech": "lech poznan",
    "jagiellonia": "jagiellonia bialystok",
    "widzew": "widzew lodz",
    "slask": "slask wroclaw",
    "gornik": "gornik zabrze",
    "wisla krakow": "wisla krakow",
    "wisla plock": "wisla plock",

    # 1 Liga mappings
    "pogon g m": "pogon grodzisk mazowiecki",
    "s mielec": "stal mielec",
    "s rzeszow": "stal rzeszow",

    # 2 Liga mappings
    "legia warszawa ii": "legia ii warszawa",
    "slask wroclaw ii": "slask ii wroclaw",
}


CLUB_NAME_STOPWORDS = {
    "aks",
    "ap",
    "gks",
    "kks",
    "ks",
    "lks",
    "mks",
    "rks",
    "rts",
    "skra",
    "zks",
}


def normalize_club_name(name):
    name = name.strip().lower()

    polish_chars = {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }

    for polish, latin in polish_chars.items():
        name = name.replace(polish, latin)

    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))

    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = " ".join(name.split())

    return name


def comparable_club_name(name):
    normalized = normalize_club_name(name)
    tokens = [
        token
        for token in normalized.split()
        if token not in CLUB_NAME_STOPWORDS
    ]

    return " ".join(tokens)


def club_match_score(first_name, second_name):
    first = comparable_club_name(first_name)
    second = comparable_club_name(second_name)

    if not first or not second:
        return 0

    if first == second:
        return 100

    first_tokens = set(first.split())
    second_tokens = set(second.split())
    overlap = len(first_tokens & second_tokens)
    max_tokens = max(len(first_tokens), len(second_tokens))
    token_score = int((overlap / max_tokens) * 100) if max_tokens else 0
    sequence_score = int(SequenceMatcher(None, first, second).ratio() * 100)

    if first in second or second in first:
        return max(token_score, sequence_score, 88)

    return max(token_score, sequence_score)


def get_superscore_clubs(url):
    clubs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        links = page.locator("a").evaluate_all("""
            elements => elements.map(a => ({
                text: a.innerText,
                href: a.href
            }))
        """)

        browser.close()

    for link in links:
        text = link["text"].strip()
        href = link["href"]

        if "/pilka-nozna/druzyny/" in href and text:
            if not href.endswith("/sklad"):
                href = href.rstrip("/") + "/sklad"

            clubs[text] = href

    return clubs


def get_90minut_clubs(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    response = urlopen(request)

    html_text = response.read().decode("iso-8859-2", errors="ignore")

    pattern = r'href="([^"]*skarb\.php\?id_klub=\d+[^"]*)"[^>]*>(.*?)</a>'
    matches = re.findall(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)

    clubs = {}

    for href, name in matches:
        name = re.sub("<.*?>", "", name).strip()
        name = html.unescape(name)

        if not name:
            continue

        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = "http://www.90minut.pl" + href
        else:
            full_url = "http://www.90minut.pl/" + href

        clubs[name] = full_url

    return clubs


def find_matching_90minut_club(ss_name, ninetyminut_clubs):
    ss_normalized = normalize_club_name(ss_name)

    if ss_normalized in SPECIAL_MATCHES:
        target_name = SPECIAL_MATCHES[ss_normalized]

        for nm_name, nm_url in ninetyminut_clubs.items():
            nm_normalized = normalize_club_name(nm_name)

            if nm_normalized == target_name:
                return nm_name, nm_url

        return None, None

    best_match = (None, None, 0)

    for nm_name, nm_url in ninetyminut_clubs.items():
        nm_normalized = normalize_club_name(nm_name)

        if ss_normalized == nm_normalized:
            return nm_name, nm_url

        if ss_normalized in nm_normalized or nm_normalized in ss_normalized:
            return nm_name, nm_url

        score = club_match_score(ss_name, nm_name)
        if score > best_match[2]:
            best_match = (nm_name, nm_url, score)

    if best_match[2] >= 82:
        print("FUZZY MATCH:", ss_name, "=>", best_match[0], "score:", best_match[2])
        return best_match[0], best_match[1]

    return None, None


def generate_rows_for_league(config):
    league = config["league"]
    group = config["group"]
    superscore_table_url = config["superscore_table_url"]
    ninetyminut_table_url = config["ninetyminut_table_url"]

    if not config["enabled"]:
        print(f"SKIPPED: {league} {group} - not configured yet")
        return []

    if not superscore_table_url or not ninetyminut_table_url:
        print(f"SKIPPED: {league} {group} - missing URLs")
        return []

    print(f"Generating: {league} {group}")

    superscore_clubs = get_superscore_clubs(superscore_table_url)
    ninetyminut_clubs = get_90minut_clubs(ninetyminut_table_url)

    rows = []

    for ss_name, ss_url in superscore_clubs.items():
        matched_90_name, matched_90_url = find_matching_90minut_club(
            ss_name,
            ninetyminut_clubs
        )

        if matched_90_url is None:
            print("NO MATCH:", league, group, ss_name)
            matched_90_name = ss_name
            matched_90_url = ""

        rows.append({
            "league": league,
            "group": group,
            "club": matched_90_name,
            "superscore_url": ss_url,
            "ninetyminut_url": matched_90_url,
        })

    return rows




parser = argparse.ArgumentParser()
parser.add_argument("--league", default="all")
parser.add_argument("--group", default="all")
args = parser.parse_args()

selected_league = args.league.lower()
selected_group = args.group.lower()

generated_rows = []
is_partial_refresh = selected_league != "all" or selected_group != "all"

for league_config in LEAGUES:
    league_name = league_config["league"].lower()
    group_name = str(league_config["group"]).lower()

    if selected_league != "all" and league_name != selected_league:
        continue
    if selected_group != "all" and group_name != selected_group:
        continue

    league_rows = generate_rows_for_league(league_config)
    generated_rows.extend(league_rows)


if is_partial_refresh and not generated_rows:
    rows = []

    if os.path.exists("clubs.csv"):
        with open("clubs.csv", "r", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

    print("No rows generated for partial refresh. Keeping existing clubs.csv rows.")
elif selected_league == "all" and selected_group == "all":
    rows = generated_rows
else:
    rows = []

    if os.path.exists("clubs.csv"):
        with open("clubs.csv", "r", encoding="utf-8") as file:
            existing_rows = list(csv.DictReader(file))

        rows = [
            row for row in existing_rows
            if not (
                row["league"].lower() == selected_league
                and (selected_group == "all" or str(row.get("group", "")).lower() == selected_group)
            )
        ]

    rows.extend(generated_rows)


with open("clubs.csv", "w", encoding="utf-8", newline="") as file:
    fieldnames = [
        "league",
        "group",
        "club",
        "superscore_url",
        "ninetyminut_url",
    ]

    writer = csv.DictWriter(file, fieldnames=fieldnames)

    writer.writeheader()
    writer.writerows(rows)


print("Saved clubs.csv")
print("Generated rows:", len(generated_rows))
print("Total clubs saved:", len(rows))
