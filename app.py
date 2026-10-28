
import os

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, inspect, MetaData, Table, select, text

load_dotenv()

st.set_page_config(
    page_title="Mining Operations Dashboard",
    page_icon="⛏️",
    layout="wide",
)

st.title("⛏️ Mining Operations Dashboard")
st.caption("Aiven MySQL | Data Warehouse Analytics")

# ---------- Database connection ----------
@st.cache_resource
def get_engine():
    ca_path = os.getenv("MYSQL_SSL_CA")
    connect_args = {}

    if ca_path:
        if not os.path.isfile(ca_path):
            raise FileNotFoundError(
                f"CA certificate not found: {ca_path}"
            )
        connect_args["ssl"] = {"ca": ca_path}

    url = URL.create(
        "mysql+pymysql",
        username=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        database=os.environ["MYSQL_DATABASE"],
    )

    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


try:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception as exc:
    st.error(f"Cannot connect to Aiven: {exc}")
    st.stop()

inspector = inspect(engine)
tables = inspector.get_table_names()

if not tables:
    st.warning("No tables found in this database.")
    st.stop()

st.sidebar.header("Dashboard controls")

default = (
    "dw_FactCycleTransport"
    if "dw_FactCycleTransport" in tables
    else tables[0]
)

table_name = st.sidebar.selectbox(
    "Database table",
    tables,
    index=tables.index(default),
)

if st.sidebar.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()


# ---------- Load selected table ----------
@st.cache_data(ttl=60)
def load_table(name):
    # Reflect the table to quote identifiers safely.
    metadata = MetaData()

    with engine.connect() as conn:
        table = Table(
            name, metadata, autoload_with=conn
        )
        return pd.read_sql(select(table), conn)


try:
    df = load_table(table_name)
except Exception as exc:
    st.error(f"Unable to read table: {exc}")
    st.stop()

if df.empty:
    st.warning(f"`{table_name}` contains no records.")
    st.stop()

# Infer date columns, including dates stored as strings.
date_cols = []
for col in df.columns:
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        date_cols.append(col)
    elif any(word in col.lower() for word in
             ["date", "datetime", "timestamp"]):
        parsed = pd.to_datetime(df[col], errors="coerce")
        if parsed.notna().any():
            df[col] = parsed
            date_cols.append(col)

numeric_cols = df.select_dtypes(include="number").columns.tolist()

categorical_cols = [
    col for col in df.columns
    if col not in numeric_cols and col not in date_cols
    and df[col].nunique(dropna=True) <= 100
]

# ---------- Filters ----------
filtered = df.copy()

st.sidebar.subheader("Filters")

if date_cols:
    date_col = st.sidebar.selectbox(
        "Date column", date_cols
    )
    valid_dates = filtered[date_col].dropna()

    if not valid_dates.empty:
        minimum = valid_dates.min().date()
        maximum = valid_dates.max().date()

        date_range = st.sidebar.date_input(
            "Date range",
            value=(minimum, maximum),
            min_value=minimum,
            max_value=maximum,
        )

        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            start, end = date_range
            filtered = filtered[
                filtered[date_col].notna()
                & filtered[date_col].dt.date.between(start, end)
            ]

if categorical_cols:
    category = st.sidebar.selectbox(
        "Category filter",
        ["All"] + categorical_cols,
    )

    if category != "All":
        options = sorted(
            filtered[category].dropna().astype(str).unique()
        )
        chosen = st.sidebar.multiselect(
            f"Values for {category}",
            options,
            default=options,
        )
        filtered = filtered[
            filtered[category].astype(str).isin(chosen)
        ]

# ---------- KPI cards ----------
st.subheader(f"Overview — {table_name}")

a, b, c, d = st.columns(4)
a.metric("Records", f"{len(filtered):,}")
b.metric("Columns", len(filtered.columns))
c.metric("Missing cells", f"{filtered.isna().sum().sum():,}")
d.metric(
    "Duplicate rows",
    f"{filtered.duplicated().sum():,}",
)

# Mining-specific indicators, when matching columns exist.
cycle_col = next(
    (c for c in numeric_cols if c.lower() == "cycle_minutes"),
    None,
)
payload_col = next(
    (c for c in numeric_cols if c.lower() == "payload_tonnes"),
    None,
)
trips_col = next(
    (c for c in numeric_cols if c.lower() == "nb_trajets"),
    None,
)

if table_name == "dw_FactCycleTransport":
    st.subheader("Mining performance")
    cards = st.columns(3)

    if cycle_col:
        cards[0].metric(
            "Average cycle time",
            f"{filtered[cycle_col].mean():,.2f} min",
        )
    if payload_col:
        cards[1].metric(
            "Total payload",
            f"{filtered[payload_col].sum():,.2f} tonnes",
        )
    if trips_col:
        cards[2].metric(
            "Total trips",
            f"{filtered[trips_col].sum():,.0f}",
        )

# ---------- Charts ----------
st.subheader("Explore your data")

if numeric_cols:
    metric = st.selectbox("Numeric metric", numeric_cols)

    group_options = ["No grouping"] + categorical_cols
    group_by = st.selectbox("Group by", group_options)

    if group_by != "No grouping":
        chart_data = (
            filtered.groupby(group_by, dropna=False)[metric]
            .mean()
            .reset_index()
            .sort_values(metric, ascending=False)
            .head(25)
        )

        fig = px.bar(
            chart_data,
            x=group_by,
            y=metric,
            title=f"Average {metric} by {group_by}",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        fig = px.histogram(
            filtered,
            x=metric,
            title=f"Distribution of {metric}",
        )
        st.plotly_chart(fig, use_container_width=True)

if date_cols and numeric_cols:
    st.subheader("Trend over time")

    trend_date = st.selectbox("Date field", date_cols)
    trend_metric = st.selectbox(
        "Trend metric",
        numeric_cols,
        key="trend_metric",
    )

    trend_data = filtered.dropna(
        subset=[trend_date, trend_metric]
    ).copy()

    if not trend_data.empty:
        trend_data["period"] = (
            trend_data[trend_date].dt.date
        )

        trend = (
            trend_data.groupby("period")[trend_metric]
            .mean()
            .reset_index()
        )

        fig = px.line(
            trend,
            x="period",
            y=trend_metric,
            markers=True,
            title=f"{trend_metric} over time",
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------- Rejected records ----------
st.subheader("Data quality")

reject_table = "stg_Mining_Cycles_Rejets"

if reject_table in tables:
    with engine.connect() as conn:
        reject_count = conn.execute(
            text(f"SELECT COUNT(*) FROM `{reject_table}`")
        ).scalar_one()

    st.metric("Rejected records in database", f"{reject_count:,}")

    with engine.connect() as conn:
        rejects = pd.read_sql(
            text(f"SELECT * FROM `{reject_table}` LIMIT 5000"),
            conn,
        )

    st.dataframe(rejects, use_container_width=True)
else:
    st.info("No rejection table was found.")

# ---------- Data explorer ----------
st.subheader("Data explorer")
st.dataframe(filtered, use_container_width=True, hide_index=True)

st.download_button(
    "Download filtered data as CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name=f"{table_name}.csv",
    mime="text/csv",
)
