#!/usr/bin/env python3
"""
Flight Journey Tracker — Streamlit web app
"""

import streamlit as st
from streamlit_folium import st_folium
from streamlit_autorefresh import st_autorefresh
import tracker

st.set_page_config(page_title="Flight Tracker", page_icon="✈", layout="wide")


# ── Sidebar ────────────────────────────────────────────────────────────────────

FLIGHTS = [
    "JBU126/KTPA/KJFK/2026-03-04/14:30-05:00/17:45-05:00",
    "SQ23/KJFK/WSSS/2026-03-04/22:05-05:00/05:21+08:00",
    "SQ914/WSSS/RPLL/2026-03-06/07:40+08:00/11:25+08:00",
]

# Auto-refresh every 5 minutes
st_autorefresh(interval=5 * 60 * 1000, key="autorefresh")


# ── Fetch flights ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_flights(flight_lines: tuple) -> list:
    result = []
    for line in flight_lines:
        result.append(tracker.parse_and_fetch_flight(line))
    tracker.assign_statuses(result)
    return result

with st.spinner("Fetching flights…"):
    flights = fetch_all_flights(tuple(FLIGHTS))


# ── Map ────────────────────────────────────────────────────────────────────────

col_title, col_btn = st.columns([6, 1])
with col_title:
    st.title("Flight Journey")
with col_btn:
    st.write("")  # nudge button down to align with title
    if st.button("↺ Refresh", use_container_width=True):
        fetch_all_flights.clear()
        st.rerun()

m = tracker.build_map(flights)
st_folium(m, use_container_width=True, height=620, returned_objects=[])


# ── Status table ───────────────────────────────────────────────────────────────

st.subheader("Journey Status")
STATUS_DISPLAY = {
    "airborne":  ("✈ Airborne",  "#00cfff"),
    "on_ground": ("⊙ On ground", "#ffaa00"),
    "completed": ("✓ Completed", "#888888"),
    "upcoming":  ("○ Upcoming",  "#3366cc"),
    "unknown":   ("? Unknown",   "#555555"),
}

for f in flights:
    status                = f.get("status", "unknown")
    label, color          = STATUS_DISPLAY.get(status, ("?", "#555555"))
    dep_str               = tracker.fmt_local(f.get("departure_dt"), f.get("origin_tz_offset", 0))
    arr_str               = tracker.fmt_local(f.get("arr_dt"),       f.get("dest_tz_offset",   0))
    orig                  = tracker.airport_label(f["origin_icao"]) if f.get("origin_icao") else "?"
    dest                  = tracker.airport_label(f["dest_icao"])   if f.get("dest_icao")   else "?"

    estimated   = f.get("position_estimated", False)
    est_note    = "&nbsp;·&nbsp;<span style='color:#666;font-style:italic'>est. position</span>" if estimated else ""
    st.markdown(
        f"""<div style="
            padding: 10px 14px; margin-bottom: 8px; border-radius: 6px;
            border-left: 4px solid {color}; background: rgba(255,255,255,0.04);
            font-family: monospace;">
            <span style="font-size:15px; font-weight:bold;">{f.get('display_name', f['callsign'])}</span>
            &nbsp;&nbsp;
            <span style="color:{color};">{label}</span>{est_note}
            &nbsp;&nbsp;
            <span style="color:#aaa;">{orig} → {dest}</span>
            <br>
            <span style="font-size:12px; color:#888;">
                Dep: {dep_str} &nbsp;·&nbsp; Arr: {arr_str}
            </span>
        </div>""",
        unsafe_allow_html=True,
    )

with st.expander("Debug"):
    token = tracker._opensky_bearer()
    st.write("OpenSky token obtained:", token is not None)
    st.write("OpenSky auth detail:", tracker._opensky_debug)
    for f in flights:
        st.write(f"{f.get('display_name', f['callsign'])}: lat={f.get('latitude')}, on_ground={f.get('on_ground')}, status={f.get('status')}, estimated={f.get('position_estimated', False)}")
