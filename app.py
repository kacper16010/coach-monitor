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

    return str(league_df["last_checked"].iloc[0])

def show_refresh_overlay(league_name: str):
    st.markdown(
        f"""
        <style>
        .refresh-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(255, 255, 255, 0.82);
            z-index: 999999;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            font-family: sans-serif;
        }}

        .spinner {{
            border: 6px solid #f3f3f3;
            border-top: 6px solid #16a34a;
            border-radius: 50%;
            width: 56px;
            height: 56px;
            animation: spin 1s linear infinite;
            margin-bottom: 18px;
        }}

        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
        </style>

        <div class="refresh-overlay">
            <div class="spinner"></div>
            <h2>Refreshing {league_name}</h2>
            <p>Updating data from SuperScore and 90minut.</p>
            <p>This may take a few minutes. Please do not close this page.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )



def show_league_page(df, league_name, group_name=None):
    if group_name is None:
        league_df = df[df["league"] == league_name]
        title = league_name
    else:
        league_df = df[(df["league"] == league_name) & (df["group"] == group_name)]
        title = f"{league_name} - {group_name}"

    st.header(title)

    if "refreshing_league" not in st.session_state:
        st.session_state.refreshing_league = None

    if "refresh_started_last_checked" not in st.session_state:
        st.session_state.refresh_started_last_checked = None

    if "refresh_started_at" not in st.session_state:
        st.session_state.refresh_started_at = None

    current_last_checked = get_last_checked_for_league(df, league_name, group_name)
    is_refreshing = st.session_state.refreshing_league == league_name

    if league_name in ["Ekstraklasa", "1 Liga", "2 Liga"]:
        if st.button(
            f"🔄 Refresh {league_name}",
            disabled=st.session_state.refreshing_league is not None,
        ):
            st.session_state.refresh_started_last_checked = current_last_checked
            st.session_state.refresh_started_at = time.time()
            trigger_github_refresh(league_name)
            st.session_state.refreshing_league = league_name
            st.rerun()

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

    if is_refreshing:
        previous_last_checked = st.session_state.refresh_started_last_checked

        if (
            previous_last_checked is not None
            and current_last_checked is not None
            and current_last_checked != previous_last_checked
        ):
            st.session_state.refreshing_league = None
            st.session_state.refresh_started_last_checked = None
            st.session_state.refresh_started_at = None
            st.success(f"{league_name} has been updated successfully.")
            st.rerun()
        else:
            st.info(f"Refresh requested for {league_name}. Data will update in the background in a few minutes.")
            st.session_state.refreshing_league = None
            st.session_state.refresh_started_last_checked = None
            st.session_state.refresh_started_at = None

def load_data():
    DATA_URL = "https://raw.githubusercontent.com/kacper16010/coach-monitor/data/results.csv"
    df = pd.read_csv(DATA_URL)

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

def trigger_github_refresh(league):
    token = os.getenv("GITHUB_ACTIONS_TOKEN")

    if not token:
        st.error("GitHub refresh token is not configured.")
        return

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
        st.success(f"Refresh requested for: {league}. Data should update in a few minutes.")
    else:
        st.error(f"Refresh request failed: {response.status_code}")
        st.text(response.text)

st.set_page_config(page_title="Coach Monitor", layout="wide")

left, right = st.columns([8, 2])

with left:
    st.title("Coach Monitor")

with right:
    st.caption(f"Version {APP_VERSION}")
if "refreshing_league" not in st.session_state:
    st.session_state.refreshing_league = None

if "refresh_started_last_checked" not in st.session_state:
    st.session_state.refresh_started_last_checked = None

if "refresh_started_at" not in st.session_state:
    st.session_state.refresh_started_at = None


st.caption("Data source: SuperScore and 90minut. Last update is shown in each league tab.")

df = load_data()

global_last_checked = ""

if not df.empty and "last_checked" in df.columns:
    global_last_checked = str(df["last_checked"].max())

st.info(f"🕒 Last refresh: {global_last_checked}")

  
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