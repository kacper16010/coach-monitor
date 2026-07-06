import re
import unicodedata
import time
import pandas as pd
import streamlit as st
import os
import requests
APP_VERSION = "0.0.1-dev"
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
    "Dolnośląskie",
    "Kujawsko-Pomorskie",
    "Lubelskie",
    "Lubuskie",
    "Łódzkie",
    "Małopolskie",
    "Mazowieckie",
    "Opolskie",
    "Podkarpackie",
    "Podlaskie",
    "Pomorskie",
    "Śląskie",
    "Świętokrzyskie",
    "Warmińsko-Mazurskie",
    "Wielkopolskie",
    "Zachodniopomorskie",
]


DATA_URL = "https://raw.githubusercontent.com/kacper16010/coach-monitor/data/results.csv"
REFRESHABLE_LEAGUES = {"Ekstraklasa", "1 Liga", "2 Liga"}
REFRESH_POLL_SECONDS = 20
REFRESH_STALE_AFTER_SECONDS = 30 * 60
REFRESH_REQUEST_MESSAGE = (
    "Refresh requested for {league}. Data will update in the background in a few minutes."
)


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


def prepare_table(dataframe):
    columns_to_show = [
        "club",
        "superscore_coach",
        "ninetyminut_coach",
        "change_date",
        "is_difference_calculated",
    ]

    column_names = {
        "club": "Club",
        "superscore_coach": "SuperScore Coach",
        "ninetyminut_coach": "90minut Coach",
        "change_date": "Change Date",
        "is_difference_calculated": "Is Difference",
    }

    return dataframe[columns_to_show].rename(columns=column_names)

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


def ensure_refresh_state():
    if "refresh_requests" not in st.session_state:
        st.session_state.refresh_requests = {}


def get_refresh_key(league_name, group_name=None):
    if group_name is None:
        return league_name

    return f"{league_name}:{group_name}"


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
    refresh_completed = False

    if refresh_request:
        previous_last_checked = refresh_request.get("started_last_checked")
        if (
            previous_last_checked is not None
            and current_last_checked is not None
            and current_last_checked != previous_last_checked
        ):
            st.session_state.refresh_requests.pop(refresh_key, None)
            refresh_request = None
            is_refreshing = False
            refresh_completed = True

    if league_name in REFRESHABLE_LEAGUES and group_name is None:
        button_col, spinner_col = st.columns([1, 4])

        with button_col:
            if st.button(
                f"Refresh {league_name}",
                key=f"refresh_{refresh_key}",
                disabled=is_refreshing,
            ):
                if trigger_github_refresh(league_name):
                    st.session_state.refresh_requests[refresh_key] = {
                        "started_last_checked": current_last_checked,
                        "started_at": time.time(),
                    }
                    st.rerun()

        with spinner_col:
            if is_refreshing:
                render_refresh_spinner("Refreshing data...")

    if refresh_completed:
        st.success(f"{league_name} has been updated successfully.")

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

    st.dataframe(
        table.style.apply(color_rows, axis=1),
        width="stretch",
        height=665,
        hide_index=True,
    )


if hasattr(st, "fragment"):
    show_league_page = st.fragment(run_every=f"{REFRESH_POLL_SECONDS}s")(_show_league_page)
else:
    show_league_page = _show_league_page


def load_data():
    data_url = f"{DATA_URL}?t={int(time.time())}"
    df = pd.read_csv(data_url)

    if "group" not in df.columns:
        df["group"] = ""

    df["group"] = df["group"].fillna("")

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


def trigger_github_refresh(league):
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

left, right = st.columns([8, 2])

with left:
    st.title("Coach Monitor")

with right:
    st.caption(f"Version {APP_VERSION}")
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

    page = st.radio(
        "Navigation",
        navigation_options,
        key="selected_page",
    )


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
