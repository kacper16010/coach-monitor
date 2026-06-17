import subprocess
import re
import unicodedata
import pandas as pd
import streamlit as st


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

    day = int(parts[0])
    month = POLISH_MONTHS.get(parts[1])
    year = int(parts[2])

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


def show_league_page(df, league_name, group_name=None):
    if group_name is None:
        league_df = df[df["league"] == league_name]
        title = league_name
    else:
        league_df = df[(df["league"] == league_name) & (df["group"] == group_name)]
        title = f"{league_name} - {group_name}"

    st.header(title)

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
        use_container_width=True,
        height=665,
        hide_index=True,
    )


st.set_page_config(page_title="Coach Monitor", layout="wide")

st.title("Coach Monitor")

if st.button("🔄 Refresh Data"):
    with st.spinner("Updating club list..."):
        subprocess.run(["py", "generate_clubs_csv.py"])

    with st.spinner("Refreshing coach data..."):
        subprocess.run(["py", "compare_clubs.py"])

    st.success("Data refreshed.")
    st.rerun()


df = pd.read_csv("results.csv")

if "group" not in df.columns:
    df["group"] = ""

df["group"] = df["group"].fillna("")

df["change_date_parsed"] = df["change_date"].apply(parse_polish_date)
df = df.sort_values(by="change_date_parsed", ascending=False)
df["change_date"] = df["change_date_parsed"].apply(format_date)

df["is_difference_calculated"] = df["result"].astype(str).str.upper().eq("DIFFERENCE")

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

    page = st.radio("Navigation", navigation_options)


if "Differences" in page:
    st.header("Differences")

    if len(all_differences) == 0:
        st.success("No coach differences detected.")
    else:
        st.error(f"{len(all_differences)} coach differences detected.")

        differences_table = prepare_table(all_differences)

        st.dataframe(
            differences_table.style.apply(color_rows, axis=1),
            use_container_width=True,
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