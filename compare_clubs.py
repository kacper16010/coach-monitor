from urllib.request import Request, urlopen
import csv
import html
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright
import argparse
import os

start_time = time.time()

COACH_ALIASES = {
    "yuriy shatalov": "jurij szatalow",

}


MAX_WORKERS = 10


def normalize_name(name):
    if not name:
        return None

    name = name.strip()
    name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", name).strip()

    polish_chars = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
        "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
    }

    for polish, latin in polish_chars.items():
        name = name.replace(polish, latin)

    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = " ".join(name.lower().split())

    name = COACH_ALIASES.get(name, name)

    return name


def get_superscore_coach(browser, url):
    page = browser.new_page()

    blocked_words = [
        "TRENER",
        "NAPASTNICY",
        "POMOCNICY",
        "OBROŃCY",
        "OBRONCY",
        "BRAMKARZE",
        "INNE",
        "SKŁAD",
        "SKLAD",
        "MECZE",
        "TABELA",
        "STATYSTYKI",
        "NAJLEPSI GRACZE",
        "INFORMACJE O DRUŻYNIE",
        "INFORMACJE O DRUZYNIE",
    ]

    name_pattern = re.compile(
        r"^[A-ZĄĆĘŁŃÓŚŹŻÁÉÍÓÚÝČŠŽĽĹŔÄÖÜ][a-ząćęłńóśźżáéíóúýčšžľĺŕäöü]+"
        r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻÁÉÍÓÚÝČŠŽĽĹŔÄÖÜ][a-ząćęłńóśźżáéíóúýčšžľĺŕäöü]+)+$"
    )

    def extract_from_lines(lines):
        for i, line in enumerate(lines):
            if "TRENER" not in line.upper():
                continue

            for candidate in lines[i + 1:i + 10]:
                candidate = candidate.strip()
                upper_candidate = candidate.upper()

                if not candidate:
                    continue

                if any(word in upper_candidate for word in blocked_words):
                    continue

                if re.fullmatch(r"\d+", candidate):
                    continue

                if "LAT" in upper_candidate:
                    continue

                if candidate.startswith("("):
                    continue

                if name_pattern.match(candidate):
                    return candidate

        return None

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)

        for _ in range(20):
            text = page.locator("body").inner_text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]

            coach = extract_from_lines(lines)

            if coach:
                return coach

            page.wait_for_timeout(1000)

        return None

    finally:
        page.close()


def get_ninetyminut_coach(url):
    if not url:
        return None, None

    html_text = None

    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urlopen(request, timeout=30)
            html_text = response.read().decode("iso-8859-2", errors="ignore")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)

    index = html_text.find("Trener:")
    if index == -1:
        return None, None

    fragment = html_text[index:index + 500]

    pattern = r"<b>\s*(.*?)\s*</b>\s*\(od\s*(.*?)\)"
    matches = re.findall(pattern, fragment, flags=re.IGNORECASE | re.DOTALL)

    if matches:
        coach_name, change_date = matches[-1]

        coach_name = re.sub(r"<.*?>", "", coach_name).strip()
        change_date = re.sub(r"<.*?>", "", change_date).strip()

        coach_name = html.unescape(coach_name)
        change_date = html.unescape(change_date)

        coach_name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", coach_name).strip()

        if not coach_name:
            return None, None

        return coach_name, change_date

    trainer_cell = fragment.split("</td>", 1)[0]
    name_matches = re.findall(
        r"<b>\s*([^<]+?)\s*</b>",
        trainer_cell,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for coach_name in reversed(name_matches):
        coach_name = re.sub(r"<.*?>", "", coach_name).strip()
        coach_name = html.unescape(coach_name)
        coach_name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", coach_name).strip()

        if coach_name:
            return coach_name, None

    return None, None


def result_key(row):
    return (
        row.get("league", ""),
        row.get("group", ""),
        row.get("club", ""),
    )


def get_superscore_change_date(previous_row, superscore_coach, last_checked):
    if not previous_row:
        return ""

    previous_change_date = previous_row.get("superscore_change_date", "")

    if not superscore_coach:
        return previous_change_date

    previous_coach = previous_row.get("superscore_coach")

    if normalize_name(previous_coach) != normalize_name(superscore_coach):
        return last_checked[:10]

    return previous_change_date[:10]


def process_club(browser, club, last_checked, previous_row=None):
    league = club["league"]
    group = club.get("group", "")
    club_name = club["club"]
    superscore_url = club["superscore_url"]
    ninetyminut_url = club["ninetyminut_url"]

    try:
        superscore_coach = get_superscore_coach(browser, superscore_url)
    except Exception as e:
        superscore_coach = None
        print(f"SuperScore error for {club_name}: {e}")

    try:
        ninetyminut_coach, change_date = get_ninetyminut_coach(ninetyminut_url)
    except Exception as e:
        ninetyminut_coach = None
        change_date = None
        print(f"90minut error for {club_name}: {e}")

    has_superscore_coach = bool(superscore_coach)
    has_ninetyminut_coach = bool(ninetyminut_coach)

    if not has_superscore_coach and not has_ninetyminut_coach:
        is_difference = False
        result = "UNKNOWN"
    elif has_superscore_coach != has_ninetyminut_coach:
        is_difference = True
        result = "DIFFERENCE"
    else:
        is_difference = normalize_name(superscore_coach) != normalize_name(ninetyminut_coach)
        result = "DIFFERENCE" if is_difference else "MATCH"

    superscore_change_date = get_superscore_change_date(
        previous_row,
        superscore_coach,
        last_checked,
    )

    return {
        "league": league,
        "group": group,
        "club": club_name,
        "superscore_coach": superscore_coach,
        "superscore_change_date": superscore_change_date,
        "ninetyminut_coach": ninetyminut_coach,
        "previous_90minut_coach": "",
        "change_date": change_date,
        "comment": previous_row.get("comment", "") if previous_row else "",
        "comment_updated_at": previous_row.get("comment_updated_at", "") if previous_row else "",
        "last_checked": last_checked,
        "is_difference": is_difference,
        "result": result,
    }


def print_result(row):
    print("-" * 40)
    print("Club:", row["club"])
    print("SuperScore coach:", row["superscore_coach"])
    print("SuperScore change date:", row["superscore_change_date"])
    print("90minut coach:", row["ninetyminut_coach"])
    print("90minut change date:", row["change_date"])
    print("Result:", row["result"])


parser = argparse.ArgumentParser()
parser.add_argument("--league", default="all")
parser.add_argument("--group", default="all")
args = parser.parse_args()

selected_league = args.league.lower()
selected_group = args.group.lower()
is_partial_refresh = selected_league != "all" or selected_group != "all"

last_checked = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")

with open("clubs.csv", "r", encoding="utf-8") as file:
    clubs = list(csv.DictReader(file))

existing_results = []

if os.path.exists("results.csv"):
    with open("results.csv", "r", encoding="utf-8") as file:
        existing_results = list(csv.DictReader(file))

previous_results_by_key = {
    result_key(row): row
    for row in existing_results
}

if selected_league != "all":
    clubs = [
        club for club in clubs
        if club["league"].lower() == selected_league
    ]

if selected_group != "all":
    clubs = [
        club for club in clubs
        if str(club.get("group", "")).lower() == selected_group
    ]


if is_partial_refresh and not clubs:
    keep_existing_results = True
    print("No clubs found for partial refresh. Keeping existing results.csv rows.")
    results = existing_results
else:
    keep_existing_results = False
    results = []


def process_club_worker(club, last_checked, previous_row):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            return process_club(browser, club, last_checked, previous_row)
        finally:
            browser.close()


with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = [
        executor.submit(
            process_club_worker,
            club,
            last_checked,
            previous_results_by_key.get(result_key(club)),
        )
        for club in clubs
    ]

    for future in as_completed(futures):
        row = future.result()
        results.append(row)
        print_result(row)


results.sort(key=lambda x: (x["league"], x["group"], x["club"]))


if is_partial_refresh and not keep_existing_results and os.path.exists("results.csv"):
    existing_results = [
        row for row in existing_results
        if not (
            (selected_league == "all" or row["league"].lower() == selected_league)
            and (selected_group == "all" or str(row.get("group", "")).lower() == selected_group)
        )
    ]

    results = existing_results + results

results.sort(key=lambda x: (x["league"], x["group"], x["club"]))


with open("results.csv", "w", encoding="utf-8", newline="") as file:
    fieldnames = [
        "league",
        "group",
        "club",
        "superscore_coach",
        "superscore_change_date",
        "ninetyminut_coach",
        "previous_90minut_coach",
        "change_date",
        "comment",
        "comment_updated_at",
        "last_checked",
        "is_difference",
        "result",
    ]

    writer = csv.DictWriter(file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)


end_time = time.time()

print()
print("Saved results to results.csv")
print("Clubs checked:", len(results))
print("Execution time:", round(end_time - start_time, 2), "seconds")
