import re
import unicodedata
import html as html_lib
import time
import io
import base64
import pandas as pd
import streamlit as st
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


POLISH_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}


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


DATA_URL = "https://raw.githubusercontent.com/kacper16010/coach-monitor/data/results.csv"
DATA_BRANCH_API_URL = "https://api.github.com/repos/kacper16010/coach-monitor/branches/data"
COMMENTS_CONTENTS_API_URL = "https://api.github.com/repos/kacper16010/coach-monitor/contents/comments.csv"
SOURCES_CONTENTS_API_URL = "https://api.github.com/repos/kacper16010/coach-monitor/contents/league_sources.csv"
RAW_DATA_URL_TEMPLATE = "https://raw.githubusercontent.com/kacper16010/coach-monitor/{sha}/results.csv"
REFRESHABLE_LEAGUES = {"Ekstraklasa", "1 Liga", "2 Liga", "3 Liga", "4 Liga"}
REFRESH_POLL_SECONDS = 20
REFRESH_STALE_AFTER_SECONDS = 30 * 60
REFRESH_RETRY_AFTER_SECONDS = 10 * 60
REFRESH_REQUEST_MESSAGE = (
    "Refresh requested for {league}. Data will update in the background in a few minutes."
)
PAGE_QUERY_PARAM = "page"


def normalize_name(name):
    if pd.isna(name):
        return ""

    name = str(name).strip()
    name = re.sub(r"\s+[A-Z]{3}(\/[A-Z]{3})*$", "", name).strip()

    name = unicodedata.normalize("NFKD", name)
    name = "".join(char for char in name if not unicodedata.combining(char))

    return " ".join(name.lower().split())


def parse_polish_date(date_text):
    if pd.isna(date_text):
        return pd.NaT

    parts = str(date_text).strip().split()

    if len(parts) != 3:
        return pd.NaT

    try:
        day = int(parts[0])
        month = POLISH_MONTHS.get(parts[1])
        year = int(parts[2])
    except ValueError:
        return pd.NaT

    if month is None:
        return pd.NaT

    return pd.Timestamp(year=year, month=month, day=day)


def format_date(date_value):
    if pd.isna(date_value):
        return ""

    return f"{date_value.day}.{date_value.month:02d}.{date_value.year}"


def make_row_key(row):
    return "|".join([
        str(row.get("league", "")),
        str(row.get("group", "")),
        str(row.get("club", "")),
    ])


def clean_comment(value):
    if pd.isna(value):
        return ""

    value = str(value)
    if value == "-":
        return ""

    return value.strip()


def get_difference_signature(row):
    return "|".join([
        normalize_name(row.get("superscore_coach", "")),
        normalize_name(row.get("ninetyminut_coach", "")),
    ])


def prepare_table(dataframe):
    dataframe = dataframe.copy()
    dataframe["row_key"] = dataframe.apply(make_row_key, axis=1)

    columns_to_show = [
        "row_key",
        "league",
        "group",
        "club",
        "superscore_coach",
        "superscore_change_date",
        "previous_superscore_coach",
        "ninetyminut_coach",
        "change_date",
        "is_difference_calculated",
        "comment",
        "comment_updated_at",
    ]

    column_names = {
        "row_key": "Row Key",
        "league": "League",
        "group": "Group",
        "club": "Club",
        "superscore_coach": "SuperScore Coach",
        "superscore_change_date": "SuperScore Change Date",
        "previous_superscore_coach": "Previous SuperScore Coach",
        "ninetyminut_coach": "90minut Coach",
        "change_date": "Change Date",
        "is_difference_calculated": "Is Difference",
        "comment": "Comment",
        "comment_updated_at": "Comment Updated At",
    }

    table = dataframe[columns_to_show].rename(columns=column_names)

    return table.fillna("").replace("", "-")


def render_results_table(dataframe, show_league=False):
    columns = []
    if show_league:
        columns.extend([
            ("league", "League", "context-column"),
            ("group", "Group", "context-column"),
        ])
    columns.extend([
        ("club", "Club", "club-column"),
        ("previous_superscore_coach", "Previous Coach", "history-column"),
        ("superscore_change_date", "Change Date", "history-date-column"),
        ("superscore_coach", "SuperScore Coach", "current-coach current-start"),
        ("ninetyminut_coach", "90minut Coach", "current-coach current-end"),
        ("change_date", "Change Date", "ninety-date-column"),
        ("comment", "Comment", "comment-column"),
    ])

    context_group = '<th colspan="2">Competition</th>' if show_league else ""
    group_header = (
        f'{context_group}<th rowspan="2" class="club-column">Club</th>'
        '<th colspan="2">SuperScore History</th>'
        '<th colspan="2" class="current-group">Current Comparison</th>'
        '<th colspan="1">90minut Info</th>'
        '<th rowspan="2">Notes</th>'
    )
    column_header = ""
    if show_league:
        column_header += "<th>League</th><th>Group</th>"
    column_header += (
        '<th class="history-column">Previous Coach</th>'
        '<th class="history-date-column">Change Date</th>'
        '<th class="current-coach current-start">SuperScore Coach</th>'
        '<th class="current-coach current-end">90minut Coach</th>'
        '<th class="ninety-date-column">Change Date</th>'
    )
    body_rows = []

    for _, row in dataframe.iterrows():
        if row.get("is_difference_calculated"):
            row_class = "coach-monitor-difference"
        elif row.get("is_ignored_difference"):
            row_class = "coach-monitor-ignored"
        else:
            row_class = ""
        cells = []

        for column, _, css_class in columns:
            value = row.get(column, "")
            value = "" if pd.isna(value) else str(value).strip()

            if column == "comment":
                timestamp = row.get("comment_updated_at", "")
                timestamp = "" if pd.isna(timestamp) else str(timestamp).strip()
                comment_text = html_lib.escape(value) if value else "-"
                timestamp_html = (
                    f'<small class="coach-monitor-comment-time">{html_lib.escape(timestamp)}</small>'
                    if timestamp
                    else ""
                )
                ignored_html = (
                    '<small class="coach-monitor-comment-time">Ignored difference</small>'
                    if row.get("is_ignored_difference")
                    else ""
                )
                cells.append(
                    f'<td class="{css_class}"><div>{comment_text}</div>'
                    f"{timestamp_html}{ignored_html}</td>"
                )
            else:
                cells.append(
                    f'<td class="{css_class}">'
                    f"{html_lib.escape(value) if value else '-'}</td>"
                )

        body_rows.append(f'<tr class="{row_class}">{"".join(cells)}</tr>')

    st.markdown(
        f"""
        <div class="coach-monitor-table-wrap">
            <table class="coach-monitor-table">
                <thead>
                    <tr class="coach-monitor-group-header">{group_header}</tr>
                    <tr class="coach-monitor-column-header">{column_header}</tr>
                </thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </div>
        <style>
            .coach-monitor-table-wrap {{
                width: 100%;
                max-height: 560px;
                overflow: auto;
                border: 1px solid rgba(49, 51, 63, 0.2);
            }}
            .coach-monitor-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.9rem;
                white-space: nowrap;
            }}
            .coach-monitor-table th {{
                position: sticky;
                z-index: 1;
                background: #262730;
                color: #ffffff;
                text-align: left;
                font-weight: 600;
            }}
            .coach-monitor-group-header th {{
                top: 0;
                height: 1.8rem;
                padding-top: 0.35rem;
                padding-bottom: 0.35rem;
                background: #18181b;
                font-size: 0.72rem;
                text-transform: uppercase;
                letter-spacing: 0;
                text-align: center;
            }}
            .coach-monitor-column-header th {{
                top: 2.5rem;
            }}
            .coach-monitor-group-header th[rowspan="2"] {{
                text-align: left;
                vertical-align: middle;
            }}
            .coach-monitor-table th,
            .coach-monitor-table td {{
                padding: 0.55rem 0.65rem;
                border-bottom: 1px solid rgba(49, 51, 63, 0.12);
                vertical-align: top;
            }}
            .coach-monitor-table td.club-column {{
                font-weight: 700;
            }}
            .coach-monitor-table td.history-column,
            .coach-monitor-table td.history-date-column {{
                font-size: 0.82rem;
                opacity: 0.72;
            }}
            .coach-monitor-table td.current-coach {{
                font-weight: 700;
            }}
            .coach-monitor-table .current-start {{
                border-left: 2px solid rgba(148, 163, 184, 0.7);
            }}
            .coach-monitor-table .current-end {{
                border-right: 2px solid rgba(148, 163, 184, 0.7);
            }}
            .coach-monitor-table tr.coach-monitor-difference td {{
                background: #dc2626;
                color: white;
            }}
            .coach-monitor-table tr.coach-monitor-ignored td {{
                background: rgba(107, 114, 128, 0.22);
            }}
            .coach-monitor-comment-time {{
                display: block;
                margin-top: 0.2rem;
                font-size: 0.72rem;
                opacity: 0.78;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def merge_persistent_comments(results_df, comments_df):
    results_df = results_df.copy()

    if "comment" not in results_df.columns:
        results_df["comment"] = ""
    if "comment_updated_at" not in results_df.columns:
        results_df["comment_updated_at"] = ""
    if "ignored_signature" not in results_df.columns:
        results_df["ignored_signature"] = ""

    if comments_df.empty or "row_key" not in comments_df.columns:
        return results_df

    comments = comments_df.copy()
    for column in ("comment", "comment_updated_at", "ignored_signature"):
        if column not in comments.columns:
            comments[column] = ""
        comments[column] = comments[column].fillna("")

    comments = comments.drop_duplicates(subset=["row_key"], keep="last")
    comment_lookup = comments.set_index("row_key")
    results_df["row_key"] = results_df.apply(make_row_key, axis=1)

    matched = results_df["row_key"].isin(comment_lookup.index)
    results_df.loc[matched, "comment"] = results_df.loc[matched, "row_key"].map(
        comment_lookup["comment"]
    )
    results_df.loc[matched, "comment_updated_at"] = results_df.loc[
        matched, "row_key"
    ].map(comment_lookup["comment_updated_at"])
    results_df.loc[matched, "ignored_signature"] = results_df.loc[
        matched, "row_key"
    ].map(comment_lookup["ignored_signature"])

    return results_df.drop(columns=["row_key"])

def get_last_checked_for_league(df, league_name, group_name=None):
    if group_name is None:
        league_df = df[df["league"] == league_name]
    else:
        league_df = df[(df["league"] == league_name) & (df["group"] == group_name)]

    if league_df.empty or "last_checked" not in league_df.columns:
        return None

    checked_values = league_df["last_checked"].dropna().astype(str)
    if checked_values.empty:
        return None

    return str(checked_values.max())


def get_global_last_checked(df):
    if df.empty or "last_checked" not in df.columns:
        return ""

    parsed = pd.to_datetime(df["last_checked"], errors="coerce")
    if parsed.notna().any():
        return parsed.max().strftime("%Y-%m-%d %H:%M:%S")

    checked_values = df["last_checked"].dropna().astype(str)
    if checked_values.empty:
        return ""

    return str(checked_values.max())


def get_competition_label(row):
    league = str(row.get("league", "")).strip()
    group = str(row.get("group", "")).strip()
    return f"{league} - {group}" if group else league


def get_github_api_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    token = get_github_actions_token()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def get_data_branch_sha():
    response = requests.get(
        DATA_BRANCH_API_URL,
        headers=get_github_api_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["commit"]["sha"]


def read_results_csv_from_data_branch():
    try:
        data_sha = get_data_branch_sha()
        data_url = RAW_DATA_URL_TEMPLATE.format(sha=data_sha)
        response = requests.get(
            f"{data_url}?t={int(time.time())}",
            headers={"Cache-Control": "no-cache"},
            timeout=20,
        )
        response.raise_for_status()
        return pd.read_csv(io.StringIO(response.text))
    except (requests.RequestException, KeyError, ValueError):
        data_url = f"{DATA_URL}?t={int(time.time())}"
        response = requests.get(
            data_url,
            headers={"Cache-Control": "no-cache"},
            timeout=20,
        )
        response.raise_for_status()
        return pd.read_csv(io.StringIO(response.text))


def read_comments_from_data_branch():
    response = requests.get(
        COMMENTS_CONTENTS_API_URL,
        params={"ref": "data", "t": int(time.time())},
        headers=get_github_api_headers(),
        timeout=10,
    )

    if response.status_code == 404:
        return pd.DataFrame(
            columns=[
                "row_key",
                "league",
                "group",
                "club",
                "comment",
                "comment_updated_at",
                "ignored_signature",
            ]
        ), None

    response.raise_for_status()
    payload = response.json()
    content = base64.b64decode(payload["content"]).decode("utf-8")
    return pd.read_csv(io.StringIO(content), keep_default_na=False), payload["sha"]


def format_comment_timestamp():
    return datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y %H:%M")


def comments_from_results(df):
    comments = df.copy()
    if "ignored_signature" not in comments.columns:
        comments["ignored_signature"] = ""
    comments["row_key"] = comments.apply(make_row_key, axis=1)
    comments = comments[
        [
            "row_key",
            "league",
            "group",
            "club",
            "comment",
            "comment_updated_at",
            "ignored_signature",
        ]
    ]
    has_comment = comments["comment"].fillna("").astype(str).str.strip().ne("")
    is_ignored = (
        comments["ignored_signature"].fillna("").astype(str).str.strip().ne("")
    )
    return comments[has_comment | is_ignored]


def save_club_preferences(df, row_key, new_comment_value, ignore_difference):
    token = get_github_actions_token()

    if not token:
        st.error("GitHub token is not configured. Cannot save comments.")
        return False

    source_rows = df.copy()
    source_rows["row_key"] = source_rows.apply(make_row_key, axis=1)
    selected = source_rows[source_rows["row_key"] == row_key]
    if selected.empty:
        st.error("The selected club no longer exists in the current data.")
        return False

    selected_row = selected.iloc[0]
    requested_comment = (
        None if new_comment_value is None else clean_comment(new_comment_value)
    )

    for attempt in range(3):
        try:
            comments_df, file_sha = read_comments_from_data_branch()
        except (requests.RequestException, KeyError, ValueError) as error:
            st.error(f"Could not read saved comments: {error}")
            return False

        if file_sha is None:
            comments_df = comments_from_results(df)

        expected_columns = [
            "row_key",
            "league",
            "group",
            "club",
            "comment",
            "comment_updated_at",
            "ignored_signature",
        ]
        for column in expected_columns:
            if column not in comments_df.columns:
                comments_df[column] = ""

        mask = comments_df["row_key"] == row_key
        current_comment = (
            clean_comment(comments_df.loc[mask, "comment"].iloc[-1])
            if mask.any()
            else clean_comment(selected_row.get("comment", ""))
        )
        new_comment = (
            current_comment if requested_comment is None else requested_comment
        )
        current_ignored_signature = (
            str(comments_df.loc[mask, "ignored_signature"].iloc[-1]).strip()
            if mask.any()
            else str(selected_row.get("ignored_signature", "")).strip()
        )
        raw_difference = str(selected_row.get("result", "")).upper() == "DIFFERENCE"
        desired_ignored_signature = (
            get_difference_signature(selected_row)
            if ignore_difference and raw_difference
            else ""
        )
        if (
            new_comment == current_comment
            and desired_ignored_signature == current_ignored_signature
        ):
            return True

        current_timestamp = (
            str(comments_df.loc[mask, "comment_updated_at"].iloc[-1]).strip()
            if mask.any()
            else str(selected_row.get("comment_updated_at", "")).strip()
        )
        comment_timestamp = (
            format_comment_timestamp() if new_comment else ""
        ) if new_comment != current_comment else current_timestamp
        values = {
            "row_key": row_key,
            "league": selected_row.get("league", ""),
            "group": selected_row.get("group", ""),
            "club": selected_row.get("club", ""),
            "comment": new_comment,
            "comment_updated_at": comment_timestamp,
            "ignored_signature": desired_ignored_signature,
        }

        if mask.any():
            for column, value in values.items():
                comments_df.loc[mask, column] = value
        else:
            comments_df = pd.concat(
                [comments_df, pd.DataFrame([values])],
                ignore_index=True,
            )

        csv_content = comments_df[expected_columns].to_csv(index=False)
        payload = {
            "message": f"Update comment ({selected_row.get('club', row_key)})",
            "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
            "branch": "data",
        }
        if file_sha:
            payload["sha"] = file_sha

        response = requests.put(
            COMMENTS_CONTENTS_API_URL,
            headers=get_github_api_headers(),
            json=payload,
            timeout=20,
        )

        if response.status_code in (200, 201):
            return True
        if response.status_code == 409 and attempt < 2:
            continue

        st.error(f"Saving comment failed: {response.status_code}")
        st.text(response.text)
        return False

    return False


def read_source_config():
    try:
        response = requests.get(
            SOURCES_CONTENTS_API_URL,
            params={"ref": "main", "t": int(time.time())},
            headers=get_github_api_headers(),
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        content = base64.b64decode(payload["content"]).decode("utf-8-sig")
        source_df = pd.read_csv(io.StringIO(content), keep_default_na=False)
        return source_df, payload["sha"]
    except (requests.RequestException, KeyError, ValueError):
        return pd.read_csv("league_sources.csv", keep_default_na=False), None


def normalize_source_config(source_df):
    columns = [
        "league",
        "group",
        "enabled",
        "superscore_table_url",
        "ninetyminut_table_url",
    ]
    source_df = source_df.copy()
    for column in columns:
        if column not in source_df.columns:
            source_df[column] = ""

    source_df = source_df[columns].fillna("")
    source_df["enabled"] = source_df["enabled"].apply(
        lambda value: str(value).strip().lower() in {"true", "1", "yes", "enabled"}
    )
    return source_df


def save_source_config(source_df):
    token = get_github_actions_token()
    if not token:
        st.error("GitHub token is not configured. Cannot save league sources.")
        return False

    source_df = normalize_source_config(source_df)
    source_df["enabled"] = source_df["enabled"].map({True: "true", False: "false"})
    csv_content = source_df.to_csv(index=False)

    for attempt in range(3):
        try:
            response = requests.get(
                SOURCES_CONTENTS_API_URL,
                params={"ref": "main", "t": int(time.time())},
                headers=get_github_api_headers(),
                timeout=10,
            )
            response.raise_for_status()
            file_sha = response.json()["sha"]
            payload = {
                "message": "Update league source links",
                "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
                "sha": file_sha,
                "branch": "main",
            }
            response = requests.put(
                SOURCES_CONTENTS_API_URL,
                headers=get_github_api_headers(),
                json=payload,
                timeout=20,
            )
        except (requests.RequestException, KeyError, ValueError) as error:
            st.error(f"Saving league sources failed: {error}")
            return False

        if response.status_code in (200, 201):
            return True
        if response.status_code == 409 and attempt < 2:
            continue

        st.error(f"Saving league sources failed: {response.status_code}")
        st.text(response.text)
        return False

    return False


def ensure_refresh_state():
    if "refresh_requests" not in st.session_state:
        st.session_state.refresh_requests = {}
    if "refresh_successes" not in st.session_state:
        st.session_state.refresh_successes = {}


def get_refresh_key(league_name, group_name=None):
    if group_name is None:
        return league_name

    return f"{league_name}:{group_name}"


def is_league_source_configured(league_name, group_name=None):
    try:
        source_df = normalize_source_config(
            pd.read_csv("league_sources.csv", keep_default_na=False)
        )
    except (OSError, ValueError):
        return False

    expected_group = group_name or ""
    matching = source_df[
        (source_df["league"] == league_name)
        & (source_df["group"] == expected_group)
    ]
    if matching.empty:
        return False

    row = matching.iloc[0]
    return bool(
        row["enabled"]
        and str(row["superscore_table_url"]).strip()
        and str(row["ninetyminut_table_url"]).strip()
    )


def page_to_slug(page):
    page = str(page)

    if "Differences" in page:
        return "differences"
    if "Search" in page:
        return "search"
    if "Ekstraklasa" in page:
        return "ekstraklasa"
    if "1 Liga" in page:
        return "1-liga"
    if "2 Liga" in page:
        return "2-liga"
    if "3 Liga" in page:
        group = page.split(" - ", 1)[-1]
        return f"3-liga-{slugify(group)}"
    if "4 Liga" in page:
        region = page.split(" - ", 1)[-1]
        return f"4-liga-{slugify(region)}"
    if "Notifications" in page:
        return "notifications"
    if "Settings" in page:
        return "settings"

    return slugify(page)


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "page"


POLISH_CHAR_FOLDS = {
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
    "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
}


def normalize_search_text(value):
    value = str(value)

    for polish, latin in POLISH_CHAR_FOLDS.items():
        value = value.replace(polish, latin)

    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.lower().strip()


def get_query_param(name):
    try:
        value = st.query_params.get(name)
    except AttributeError:
        value = st.experimental_get_query_params().get(name)

    if isinstance(value, list):
        return value[0] if value else None

    return value


def set_query_param(name, value):
    try:
        st.query_params[name] = value
    except AttributeError:
        st.experimental_set_query_params(**{name: value})


def get_initial_page(navigation_options):
    page_by_slug = {page_to_slug(option): option for option in navigation_options}
    requested_slug = get_query_param(PAGE_QUERY_PARAM)

    if requested_slug in page_by_slug:
        return page_by_slug[requested_slug]

    return navigation_options[0]


def render_refresh_spinner(message):
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:0.55rem;padding-top:0.35rem;">
            <div style="
                width:18px;
                height:18px;
                border:3px solid rgba(49, 51, 63, 0.18);
                border-top-color:#16a34a;
                border-radius:50%;
                animation:coach-monitor-spin 0.8s linear infinite;
            "></div>
            <span>{message}</span>
        </div>
        <style>
        @keyframes coach-monitor-spin {{
            from {{ transform: rotate(0deg); }}
            to {{ transform: rotate(360deg); }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _show_league_page(df, league_name, group_name=None):
    ensure_refresh_state()
    refresh_key = get_refresh_key(league_name, group_name)

    if st.session_state.refresh_requests.get(refresh_key):
        df = load_data()

    if group_name is None:
        league_df = df[df["league"] == league_name].sort_values(by="club")
        title = league_name
    else:
        league_df = df[
            (df["league"] == league_name) & (df["group"] == group_name)
        ].sort_values(by="club")
        title = f"{league_name} - {group_name}"

    st.header(title)

    current_last_checked = get_last_checked_for_league(df, league_name, group_name)
    refresh_request = st.session_state.refresh_requests.get(refresh_key)
    elapsed_seconds = 0
    if refresh_request:
        elapsed_seconds = int(time.time() - refresh_request.get("started_at", time.time()))
    is_refreshing = (
        refresh_request is not None
        and elapsed_seconds < REFRESH_RETRY_AFTER_SECONDS
    )

    if refresh_request:
        previous_last_checked = refresh_request.get("started_last_checked")
        if (
            current_last_checked is not None
            and current_last_checked != previous_last_checked
        ):
            st.session_state.refresh_requests.pop(refresh_key, None)
            st.session_state.refresh_successes[refresh_key] = (
                f"{title} has been updated successfully."
            )
            st.rerun()

    if league_name in REFRESHABLE_LEAGUES:
        source_configured = is_league_source_configured(league_name, group_name)
        button_col, spinner_col = st.columns([1, 4])
        refresh_label = title

        with button_col:
            if st.button(
                f"Refresh {refresh_label}",
                key=f"refresh_{refresh_key}",
                disabled=is_refreshing or not source_configured,
            ):
                if trigger_github_refresh(league_name, group_name):
                    st.session_state.refresh_requests[refresh_key] = {
                        "started_last_checked": current_last_checked,
                        "started_at": time.time(),
                    }
                    st.rerun()

        with spinner_col:
            if is_refreshing:
                render_refresh_spinner("Refreshing data...")
            elif not source_configured:
                st.caption("Add both source links and enable this competition in Settings.")

    success_message = st.session_state.refresh_successes.pop(refresh_key, None)
    if success_message:
        st.success(success_message)

    if refresh_request:
        st.info(REFRESH_REQUEST_MESSAGE.format(league=title))
        if elapsed_seconds >= REFRESH_RETRY_AFTER_SECONDS:
            st.warning(
                "The refresh is taking longer than expected. The button is available "
                "again, so you can retry without reloading the page."
            )
        if elapsed_seconds >= REFRESH_STALE_AFTER_SECONDS:
            st.session_state.refresh_requests.pop(refresh_key, None)

    if league_df.empty:
        st.info("No data available yet.")
        return

    differences = league_df[league_df["is_difference_calculated"] == True]

    col1, col2, col3 = st.columns(3)

    col1.metric("Clubs monitored", len(league_df))
    col2.metric("Differences detected", len(differences))
    col3.metric("Last refresh", league_df["last_checked"].iloc[0])

    render_results_table(league_df)


if hasattr(st, "fragment"):
    show_league_page = st.fragment(run_every=f"{REFRESH_POLL_SECONDS}s")(_show_league_page)
else:
    show_league_page = _show_league_page


def show_comment_editor(df, league_name, group_name=None):
    """Comment editing UI, rendered outside the auto-refreshing fragment.

    It must live outside show_league_page's st.fragment(run_every=...):
    that fragment silently reruns every REFRESH_POLL_SECONDS to poll for
    background data refreshes, which would wipe out whatever the user is
    currently typing into the comment box before they get a chance to
    save it.
    """
    if group_name is None:
        league_df = df[df["league"] == league_name]
    else:
        league_df = df[(df["league"] == league_name) & (df["group"] == group_name)]

    if league_df.empty:
        return

    refresh_key = get_refresh_key(league_name, group_name)
    table = prepare_table(league_df)

    st.subheader("Comment and difference")

    club_labels = {
        row["Row Key"]: row["Club"]
        for _, row in table.iterrows()
    }

    selected_row_key = st.selectbox(
        "Club",
        options=list(club_labels.keys()),
        format_func=lambda key: club_labels.get(key, key),
        key=f"comment_club_select_{refresh_key}",
    )

    current_row = table[table["Row Key"] == selected_row_key].iloc[0]
    current_comment = current_row["Comment"]
    current_comment = "" if current_comment == "-" else current_comment
    current_updated_at = current_row["Comment Updated At"]
    current_updated_at = "" if current_updated_at == "-" else current_updated_at
    selected_source_row = league_df[
        league_df.apply(make_row_key, axis=1) == selected_row_key
    ].iloc[0]
    current_signature = get_difference_signature(selected_source_row)
    ignored_signature = str(selected_source_row.get("ignored_signature", "")).strip()
    raw_difference = str(selected_source_row.get("result", "")).upper() == "DIFFERENCE"

    new_comment = st.text_area(
        "Comment",
        value=current_comment,
        key=f"comment_text_{refresh_key}_{selected_row_key}",
    )

    if current_updated_at:
        st.caption(f"Last updated: {current_updated_at}")

    ignore_difference = st.checkbox(
        "Ignore this coach difference",
        value=bool(raw_difference and ignored_signature == current_signature),
        disabled=not raw_difference,
        key=f"ignore_difference_{refresh_key}_{selected_row_key}",
        help="This applies only to the current pair of coach names.",
    )

    if st.button("Save", key=f"save_comment_{refresh_key}_{selected_row_key}"):
        if save_club_preferences(
            df,
            selected_row_key,
            new_comment,
            ignore_difference,
        ):
            st.success("Saved.")
            st.rerun()


def load_data():
    df = read_results_csv_from_data_branch()

    if "group" not in df.columns:
        df["group"] = ""
    if "superscore_change_date" not in df.columns:
        df["superscore_change_date"] = ""
    if "comment" not in df.columns:
        df["comment"] = ""
    if "comment_updated_at" not in df.columns:
        df["comment_updated_at"] = ""
    if "ignored_signature" not in df.columns:
        df["ignored_signature"] = ""
    if "previous_superscore_coach" not in df.columns:
        df["previous_superscore_coach"] = ""

    df["group"] = df["group"].fillna("")
    df["superscore_change_date"] = df["superscore_change_date"].fillna("")
    df["superscore_change_date"] = df["superscore_change_date"].astype(str).str[:10]
    df["comment"] = df["comment"].fillna("")
    df["comment_updated_at"] = df["comment_updated_at"].fillna("")
    df["ignored_signature"] = df["ignored_signature"].fillna("")
    df["previous_superscore_coach"] = df["previous_superscore_coach"].fillna("")

    try:
        comments_df, _ = read_comments_from_data_branch()
        df = merge_persistent_comments(df, comments_df)
    except (requests.RequestException, KeyError, ValueError):
        pass

    df["change_date_parsed"] = df["change_date"].apply(parse_polish_date)
    df = df.sort_values(by="change_date_parsed", ascending=False)
    df["change_date"] = df["change_date_parsed"].apply(format_date)

    df["is_raw_difference"] = (
        df["result"].astype(str).str.upper().eq("DIFFERENCE")
    )
    df["difference_signature"] = df.apply(get_difference_signature, axis=1)
    df["is_ignored_difference"] = (
        df["is_raw_difference"]
        & df["ignored_signature"].astype(str).str.strip().ne("")
        & df["ignored_signature"].eq(df["difference_signature"])
    )
    df["is_difference_calculated"] = (
        df["is_raw_difference"] & ~df["is_ignored_difference"]
    )

    return df


def get_next_refresh():
    now = datetime.now()

    next_hour = now.replace(minute=0, second=0, microsecond=0)

    if now.minute > 0:
        next_hour += timedelta(hours=1)

    if next_hour.hour < 8:
        next_hour = next_hour.replace(hour=8)

    if next_hour.hour > 21:
        next_hour = (next_hour + timedelta(days=1)).replace(hour=8)

    return next_hour.strftime("%H:%M")


def get_github_actions_token_info():
    raw_token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    token = raw_token.strip() if raw_token else ""

    return token or None, {
        "present": raw_token is not None,
        "non_empty": bool(token),
        "length": len(token),
    }


def get_github_actions_token():
    token, _ = get_github_actions_token_info()
    return token


def trigger_github_refresh(league, group=None):
    token, token_info = get_github_actions_token_info()

    if not token:
        st.error(
            "GitHub refresh token is not configured. Add GITHUB_ACTIONS_TOKEN "
            "to the Render environment variables and redeploy the app."
        )
        st.caption(
            "Token diagnostics: "
            f"present={token_info['present']}, "
            f"non_empty={token_info['non_empty']}, "
            f"length={token_info['length']}"
        )
        return False

    url = "https://api.github.com/repos/kacper16010/coach-monitor/actions/workflows/update-data.yml/dispatches"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    payload = {
        "ref": "main",
        "inputs": {
            "league": league,
            "group": group or "all",
        },
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=15,
        )
    except requests.RequestException as error:
        st.error(f"Refresh request failed: {error}")
        return False

    if response.status_code == 204:
        return True

    if response.status_code in (401, 403):
        st.error(
            f"Refresh request failed: {response.status_code}. "
            "The token was found, but GitHub rejected it. Check repository access "
            "and Actions: read/write permission."
        )
    else:
        st.error(f"Refresh request failed: {response.status_code}")
    st.text(response.text)
    return False

st.set_page_config(page_title="Coach Monitor", layout="wide")

st.title("Coach Monitor")
ensure_refresh_state()


st.caption("Data source: SuperScore and 90minut. Last update is shown in each league tab.")

df = load_data()

global_last_checked = get_global_last_checked(df)

st.info(f"Last full refresh: {global_last_checked}")


all_differences = df[df["is_difference_calculated"] == True]
all_raw_differences = df[df["is_raw_difference"] == True]

with st.sidebar:
    st.header("Coach Monitor")

    diff_label = (
        f"🔴 Differences ({len(all_differences)})"
        if len(all_differences) > 0
        else "Differences (0)"
    )

    navigation_options = [
        diff_label,
        "🔍 Search",
        "⚽ Ekstraklasa",
        "⚽ 1 Liga",
        "⚽ 2 Liga",
        "⚽ 3 Liga - Group 1",
        "⚽ 3 Liga - Group 2",
        "⚽ 3 Liga - Group 3",
        "⚽ 3 Liga - Group 4",
    ]

    for region in FOURTH_LEAGUE_REGIONS:
        navigation_options.append(f"⚽ 4 Liga - {region}")

    navigation_options.extend([
        "📧 Notifications",
        "⚙️ Settings",
    ])

    current_query_slug = get_query_param(PAGE_QUERY_PARAM)
    initial_page = get_initial_page(navigation_options)
    selected_page = st.session_state.get("selected_page")

    if selected_page not in navigation_options:
        st.session_state.selected_page = initial_page
    elif current_query_slug != st.session_state.get("applied_page_slug"):
        st.session_state.selected_page = initial_page
        st.session_state.applied_page_slug = current_query_slug

    page = st.radio(
        "Navigation",
        navigation_options,
        index=navigation_options.index(initial_page),
        key="selected_page",
    )

    page_slug = page_to_slug(page)
    if get_query_param(PAGE_QUERY_PARAM) != page_slug:
        set_query_param(PAGE_QUERY_PARAM, page_slug)
    st.session_state.applied_page_slug = page_slug


if "Differences" in page:
    st.header("Differences")

    if len(all_raw_differences) == 0:
        st.success("No coach differences detected.")
    else:
        difference_labels = all_raw_differences.apply(get_competition_label, axis=1)
        filter_options = list(dict.fromkeys(difference_labels.tolist()))
        selected_competitions = st.multiselect(
            "Leagues",
            options=filter_options,
            default=filter_options,
            key="differences_league_filter",
        )
        filtered_differences = all_raw_differences[
            difference_labels.isin(selected_competitions)
        ].copy()

        if filtered_differences.empty:
            st.info("No differences for the selected leagues.")
        else:
            filtered_differences["Row Key"] = filtered_differences.apply(
                make_row_key,
                axis=1,
            )

            active_count = int((~filtered_differences["is_ignored_difference"]).sum())
            ignored_count = int(filtered_differences["is_ignored_difference"].sum())
            if active_count:
                st.error(f"{active_count} active coach differences detected.")
            else:
                st.success("No active differences for the selected leagues.")
            if ignored_count:
                st.caption(f"Ignored differences shown at the bottom: {ignored_count}")

            results_table = filtered_differences.sort_values(
                by=["is_ignored_difference", "league", "group", "club"],
                ascending=[True, True, True, True],
            )
            render_results_table(results_table, show_league=True)

            st.subheader("Manage ignored differences")
            ignore_editor = filtered_differences[
                [
                    "Row Key",
                    "is_ignored_difference",
                    "league",
                    "group",
                    "club",
                    "superscore_coach",
                    "ninetyminut_coach",
                ]
            ].rename(columns={
                "is_ignored_difference": "Ignore",
                "league": "League",
                "group": "Group",
                "club": "Club",
                "superscore_coach": "SuperScore Coach",
                "ninetyminut_coach": "90minut Coach",
            })
            ignore_editor = ignore_editor.sort_values(
                by=["Ignore", "League", "Group", "Club"],
                ascending=[True, True, True, True],
            )

            edited_ignores = st.data_editor(
                ignore_editor,
                width="stretch",
                hide_index=True,
                disabled=[
                    "League",
                    "Group",
                    "Club",
                    "SuperScore Coach",
                    "90minut Coach",
                ],
                column_config={
                    "Row Key": None,
                    "Ignore": st.column_config.CheckboxColumn("Ignore"),
                },
                key="global_difference_ignore_editor",
            )

            if st.button("Save ignored differences", type="primary"):
                current_ignore_by_key = {
                    make_row_key(row): bool(row.get("is_ignored_difference"))
                    for _, row in filtered_differences.iterrows()
                }
                changes = [
                    (row["Row Key"], bool(row["Ignore"]))
                    for _, row in edited_ignores.iterrows()
                    if bool(row["Ignore"])
                    != current_ignore_by_key.get(row["Row Key"], False)
                ]

                if not changes:
                    st.info("No ignore settings changed.")
                else:
                    saved = all(
                        save_club_preferences(df, row_key, None, ignored)
                        for row_key, ignored in changes
                    )
                    if saved:
                        st.success("Ignored differences saved.")
                        st.rerun()


elif "Search" in page:
    st.header("Search")

    search_query = st.text_input("Club name", placeholder="e.g. Wisła, Lech, Widzew...")

    if search_query.strip():
        normalized_query = normalize_search_text(search_query)
        matches = df[
            df["club"].apply(normalize_search_text).str.contains(normalized_query, na=False)
        ]
    else:
        matches = df

    if matches.empty:
        st.info("No clubs match that search.")
    else:
        matches = matches.sort_values(by=["league", "group", "club"])
        st.caption(f"{len(matches)} club(s) found.")

        render_results_table(matches, show_league=True)


elif page == "⚽ Ekstraklasa":
    show_league_page(df, "Ekstraklasa")
    show_comment_editor(df, "Ekstraklasa")


elif page == "⚽ 1 Liga":
    show_league_page(df, "1 Liga")
    show_comment_editor(df, "1 Liga")


elif page == "⚽ 2 Liga":
    show_league_page(df, "2 Liga")
    show_comment_editor(df, "2 Liga")


elif page.startswith("⚽ 3 Liga"):
    group = page.replace("⚽ 3 Liga - ", "")
    show_league_page(df, "3 Liga", group)
    show_comment_editor(df, "3 Liga", group)


elif page.startswith("⚽ 4 Liga"):
    region = page.replace("⚽ 4 Liga - ", "")
    show_league_page(df, "4 Liga", region)
    show_comment_editor(df, "4 Liga", region)


elif page == "📧 Notifications":
    st.header("Notifications")
    st.write("Email notifications will be added later.")


elif page == "⚙️ Settings":
    st.header("League sources")
    st.caption(
        "Update the table links for a new season. A configured row needs both URLs "
        "and the Enabled switch. Saving commits the file to main; GitHub Actions "
        "then refreshes data automatically."
    )

    source_df, _ = read_source_config()
    source_df = normalize_source_config(source_df)
    edited_sources = st.data_editor(
        source_df,
        width="stretch",
        height=720,
        hide_index=True,
        disabled=["league", "group"],
        column_config={
            "league": st.column_config.TextColumn("League"),
            "group": st.column_config.TextColumn("Group / region"),
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "superscore_table_url": st.column_config.LinkColumn("SuperScore table"),
            "ninetyminut_table_url": st.column_config.LinkColumn("90minut table"),
        },
        key="league_sources_editor",
    )

    configured = (
        edited_sources["enabled"]
        & edited_sources["superscore_table_url"].astype(str).str.strip().ne("")
        & edited_sources["ninetyminut_table_url"].astype(str).str.strip().ne("")
    )
    st.caption(f"Configured: {int(configured.sum())} of {len(edited_sources)} competitions")

    if st.button("Save league sources", type="primary"):
        invalid_enabled = edited_sources["enabled"] & ~configured
        if invalid_enabled.any():
            st.error("Every enabled row must contain both a SuperScore and a 90minut URL.")
        elif save_source_config(edited_sources):
            st.success("League sources saved. The automatic GitHub workflow has started.")
            st.rerun()
