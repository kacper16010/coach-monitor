import re
import unicodedata
import time
import io
import base64
import pandas as pd
import streamlit as st
import os
import requests
from datetime import datetime, timedelta


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
RESULTS_CONTENTS_API_URL = "https://api.github.com/repos/kacper16010/coach-monitor/contents/results.csv"
RAW_DATA_URL_TEMPLATE = "https://raw.githubusercontent.com/kacper16010/coach-monitor/{sha}/results.csv"
REFRESHABLE_LEAGUES = {"Ekstraklasa", "1 Liga", "2 Liga", "3 Liga", "4 Liga"}
REFRESH_POLL_SECONDS = 20
REFRESH_STALE_AFTER_SECONDS = 30 * 60
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


def color_rows(row):
    if row["Is Difference"]:
        return ["background-color: #ff4d4d; color: white"] * len(row)

    return [""] * len(row)


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


def prepare_table(dataframe):
    dataframe = dataframe.copy()
    dataframe["row_key"] = dataframe.apply(make_row_key, axis=1)

    columns_to_show = [
        "row_key",
        "club",
        "superscore_coach",
        "superscore_change_date",
        "ninetyminut_coach",
        "change_date",
        "is_difference_calculated",
        "comment",
    ]

    column_names = {
        "row_key": "Row Key",
        "club": "Club",
        "superscore_coach": "SuperScore Coach",
        "superscore_change_date": "SuperScore Change Date",
        "ninetyminut_coach": "90minut Coach",
        "change_date": "Change Date",
        "is_difference_calculated": "Is Difference",
        "comment": "Comment",
    }

    table = dataframe[columns_to_show].rename(columns=column_names)

    return table.fillna("").replace("", "-")

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
        return pd.read_csv(data_url)


def get_results_file_sha():
    response = requests.get(
        RESULTS_CONTENTS_API_URL,
        params={"ref": "data"},
        headers=get_github_api_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["sha"]


def save_results_csv_to_data_branch(df, message):
    token = get_github_actions_token()

    if not token:
        st.error("GitHub token is not configured. Cannot save comments.")
        return False

    csv_content = df.to_csv(index=False)
    payload = {
        "message": message,
        "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
        "sha": get_results_file_sha(),
        "branch": "data",
    }

    response = requests.put(
        RESULTS_CONTENTS_API_URL,
        headers=get_github_api_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code in (200, 201):
        return True

    st.error(f"Saving comments failed: {response.status_code}")
    st.text(response.text)
    return False


def apply_comment_edits(df, edited_table):
    updated_df = df.copy()

    if "comment" not in updated_df.columns:
        updated_df["comment"] = ""

    updated_df["row_key"] = updated_df.apply(make_row_key, axis=1)
    comments_by_key = {
        row["Row Key"]: clean_comment(row["Comment"])
        for _, row in edited_table.iterrows()
    }

    updated_df["comment"] = updated_df["row_key"].map(comments_by_key).fillna(updated_df["comment"])
    updated_df = updated_df.drop(columns=["row_key"])

    return updated_df


def ensure_refresh_state():
    if "refresh_requests" not in st.session_state:
        st.session_state.refresh_requests = {}
    if "refresh_successes" not in st.session_state:
        st.session_state.refresh_successes = {}


def get_refresh_key(league_name, group_name=None):
    if group_name is None:
        return league_name

    return f"{league_name}:{group_name}"


def page_to_slug(page):
    page = str(page)

    if "Differences" in page:
        return "differences"
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
        league_df = df[df["league"] == league_name]
        title = league_name
    else:
        league_df = df[(df["league"] == league_name) & (df["group"] == group_name)]
        title = f"{league_name} - {group_name}"

    st.header(title)

    current_last_checked = get_last_checked_for_league(df, league_name, group_name)
    refresh_request = st.session_state.refresh_requests.get(refresh_key)
    is_refreshing = refresh_request is not None

    if refresh_request:
        previous_last_checked = refresh_request.get("started_last_checked")
        if (
            current_last_checked is not None
            and current_last_checked != previous_last_checked
        ):
            st.session_state.refresh_requests.pop(refresh_key, None)
            st.session_state.refresh_successes[refresh_key] = (
                f"{league_name} has been updated successfully."
            )
            st.rerun()

    if league_name in REFRESHABLE_LEAGUES:
        button_col, spinner_col = st.columns([1, 4])
        refresh_label = title

        with button_col:
            if st.button(
                f"Refresh {refresh_label}",
                key=f"refresh_{refresh_key}",
                disabled=is_refreshing,
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

    success_message = st.session_state.refresh_successes.pop(refresh_key, None)
    if success_message:
        st.success(success_message)

    if refresh_request:
        elapsed_seconds = int(time.time() - refresh_request.get("started_at", time.time()))
        st.info(REFRESH_REQUEST_MESSAGE.format(league=league_name))
        if elapsed_seconds >= REFRESH_STALE_AFTER_SECONDS:
            st.warning(
                "This refresh is taking longer than usual. You can request it again, "
                "or leave this page open while the background workflow finishes."
            )

    if league_df.empty:
        st.info("No data available yet.")
        return

    differences = league_df[league_df["is_difference_calculated"] == True]

    col1, col2, col3 = st.columns(3)

    col1.metric("Clubs monitored", len(league_df))
    col2.metric("Differences detected", len(differences))
    col3.metric("Last refresh", league_df["last_checked"].iloc[0])

    table = prepare_table(league_df)

    edited_table = st.data_editor(
        table,
        width="stretch",
        height=665,
        hide_index=True,
        key=f"table_{refresh_key}",
        disabled=[
            "Club",
            "SuperScore Coach",
            "SuperScore Change Date",
            "90minut Coach",
            "Change Date",
            "Is Difference",
        ],
        column_config={
            "Row Key": None,
            "Comment": st.column_config.TextColumn("Comment"),
        },
    )

    if st.button("Save comments", key=f"save_comments_{refresh_key}"):
        updated_df = apply_comment_edits(df, edited_table)

        if save_results_csv_to_data_branch(updated_df, f"Update comments ({title})"):
            st.success("Comments saved.")
            st.rerun()


if hasattr(st, "fragment"):
    show_league_page = st.fragment(run_every=f"{REFRESH_POLL_SECONDS}s")(_show_league_page)
else:
    show_league_page = _show_league_page


def load_data():
    df = read_results_csv_from_data_branch()

    if "group" not in df.columns:
        df["group"] = ""
    if "superscore_change_date" not in df.columns:
        df["superscore_change_date"] = ""
    if "comment" not in df.columns:
        df["comment"] = ""

    df["group"] = df["group"].fillna("")
    df["superscore_change_date"] = df["superscore_change_date"].fillna("")
    df["superscore_change_date"] = df["superscore_change_date"].astype(str).str[:10]
    df["comment"] = df["comment"].fillna("")

    df["change_date_parsed"] = df["change_date"].apply(parse_polish_date)
    df = df.sort_values(by="change_date_parsed", ascending=False)
    df["change_date"] = df["change_date_parsed"].apply(format_date)

    df["is_difference_calculated"] = (
        df["result"].astype(str).str.upper().eq("DIFFERENCE")
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

    response = requests.post(url, headers=headers, json=payload)

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

with st.sidebar:
    st.header("Coach Monitor")

    diff_label = (
        f"🔴 Differences ({len(all_differences)})"
        if len(all_differences) > 0
        else "Differences (0)"
    )

    navigation_options = [
        diff_label,
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

    if len(all_differences) == 0:
        st.success("No coach differences detected.")
    else:
        st.error(f"{len(all_differences)} coach differences detected.")

        differences_table = prepare_table(all_differences)

        st.dataframe(
            differences_table.style.apply(color_rows, axis=1),
            width="stretch",
            height=500,
            hide_index=True,
        )


elif page == "⚽ Ekstraklasa":
    show_league_page(df, "Ekstraklasa")


elif page == "⚽ 1 Liga":
    show_league_page(df, "1 Liga")


elif page == "⚽ 2 Liga":
    show_league_page(df, "2 Liga")


elif page.startswith("⚽ 3 Liga"):
    group = page.replace("⚽ 3 Liga - ", "")
    show_league_page(df, "3 Liga", group)


elif page.startswith("⚽ 4 Liga"):
    region = page.replace("⚽ 4 Liga - ", "")
    show_league_page(df, "4 Liga", region)


elif page == "📧 Notifications":
    st.header("Notifications")
    st.write("Email notifications will be added later.")


elif page == "⚙️ Settings":
    st.header("Settings")
    st.write("Automatic refresh: not configured yet")
