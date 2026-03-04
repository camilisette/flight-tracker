#!/usr/bin/env python3
"""
Flight Journey Tracker
----------------------
Add a series of connected flights and visualize their live positions on a map.

Usage:
    python tracker.py AA100 BA203 EK405
    python tracker.py --callsigns AAL100 BAW203 UAE405   # use ICAO callsigns directly
"""

import argparse
import base64
import math
import sys
import webbrowser
import os
from datetime import date, datetime, timezone, timedelta
import requests
import folium
from folium import plugins
import airportsdata

try:
    from FlightRadarAPI import FlightRadar24API as _FR24
    _fr24 = _FR24()
except Exception:
    _fr24 = None

OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPENSKY_ROUTES_URL = "https://opensky-network.org/api/routes"

# Loaded once at startup: ICAO airport code -> airport record
_AIRPORTS = airportsdata.load("ICAO")


def _load_icon() -> str | None:
    """Load icon.{png,jpg,svg} from the icons/ folder as a data URI."""
    icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    for ext, mime in [("png", "image/png"), ("jpg", "image/jpeg"),
                      ("jpeg", "image/jpeg"), ("svg", "image/svg+xml")]:
        path = os.path.join(icons_dir, f"icon.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            return f"data:{mime};base64,{data}"
    return None

_ICON_DATA_URI = _load_icon()

# Partial IATA -> ICAO airline code mapping (common carriers)
# Extend this as needed
IATA_TO_ICAO = {
    "AA": "AAL", "AB": "ABR", "AC": "ACA", "AF": "AFR", "AI": "AIC",
    "AK": "AXM", "AM": "AMX", "AR": "ARG", "AS": "ASA", "AT": "RAM",
    "AV": "AVA", "AY": "FIN", "AZ": "AZA", "B6": "JBU", "BA": "BAW",
    "BR": "EVA", "CA": "CCA", "CI": "CAL", "CM": "CMP", "CX": "CPA",
    "CZ": "CSN", "DE": "CFG", "DL": "DAL", "EI": "EIN", "EK": "UAE",
    "ET": "ETH", "EW": "EWG", "EY": "ETD", "F9": "FFT", "FI": "ICE",
    "FM": "CSH", "FR": "RYR", "FZ": "FDB", "G3": "GLO", "GF": "GFA",
    "HA": "HAL", "HU": "CHH", "HX": "CRK", "IB": "IBE", "JJ": "TAM",
    "JL": "JAL", "JP": "ADR", "KA": "HDA", "KC": "KZR", "KE": "KAL",
    "KL": "KLM", "KQ": "KQA", "KU": "KAC", "LA": "LAN", "LH": "DLH",
    "LO": "LOT", "LX": "SWR", "LY": "ELY", "MF": "CXA", "MH": "MAS",
    "MS": "MSR", "MU": "CES", "MX": "MXA", "NH": "ANA", "NK": "NKS",
    "NZ": "ANZ", "OK": "CSA", "OS": "AUA", "OU": "CTN", "OZ": "AAR",
    "PC": "PGT", "PK": "PIA", "PR": "PAL", "PS": "AUI", "PX": "ANG",
    "QF": "QFA", "QR": "QTR", "RJ": "RJA", "RO": "ROT", "S7": "SBI",
    "SA": "SAA", "SK": "SAS", "SN": "BEL", "SQ": "SIA", "SU": "AFL",
    "SV": "SVA", "TG": "THA", "TK": "THY", "TP": "TAP", "TW": "TWB",
    "U2": "EZY", "UA": "UAL", "UL": "SRI", "UN": "UTA", "UT": "UTA",
    "UX": "AEA", "VN": "HVN", "VS": "VIR", "VY": "VLG", "W6": "WZZ",
    "WN": "SWA", "WS": "WJA", "XQ": "SXS", "XY": "NAS", "YM": "MGX",
    "ZH": "CSZ",
}


def iata_to_icao_callsign(flight: str) -> str:
    """Convert IATA flight number (e.g. AA100) to ICAO callsign (e.g. AAL100)."""
    flight = flight.strip().upper()
    # Split into airline code and flight number
    # Handle both 2-char (AA100) and 3-char (AAL100) prefixes
    for length in (2, 3):
        code = flight[:length]
        number = flight[length:]
        if number.isdigit():
            if length == 3:
                # Already looks like an ICAO callsign
                return flight
            icao = IATA_TO_ICAO.get(code)
            if icao:
                return icao + number
            print(f"  [warn] No ICAO mapping for airline code '{code}', using as-is: {flight}")
            return flight
    print(f"  [warn] Could not parse flight number '{flight}', using as-is")
    return flight


def great_circle_points(lat1: float, lon1: float, lat2: float, lon2: float, n: int = 100) -> list[tuple]:
    """Return n+1 (lat, lon) points along the great circle between two coordinates."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    d = 2 * math.asin(math.sqrt(
        math.sin((lat2 - lat1) / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    ))
    if d == 0:
        return [(math.degrees(lat1), math.degrees(lon1))]
    points = []
    for i in range(n + 1):
        f = i / n
        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(lat1) * math.cos(lon1) + b * math.cos(lat2) * math.cos(lon2)
        y = a * math.cos(lat1) * math.sin(lon1) + b * math.cos(lat2) * math.sin(lon2)
        z = a * math.sin(lat1) + b * math.sin(lat2)
        lat = math.atan2(z, math.sqrt(x ** 2 + y ** 2))
        lon = math.atan2(y, x)
        points.append((math.degrees(lat), math.degrees(lon)))
    return points


def get_airport_coords(icao: str) -> tuple[float, float] | None:
    """Return (lat, lon) for an ICAO airport code, or None if not found."""
    ap = _AIRPORTS.get(icao.upper())
    if ap:
        return ap["lat"], ap["lon"]
    return None


def airport_label(icao: str) -> str:
    """Return IATA code for display, falling back to ICAO if not found."""
    ap = _AIRPORTS.get(icao.upper())
    if ap and ap.get("iata"):
        return ap["iata"]
    return icao


def fetch_route(callsign: str) -> tuple[str, str] | tuple[None, None]:
    """Query OpenSky /routes for origin and destination ICAO airport codes."""
    try:
        resp = requests.get(OPENSKY_ROUTES_URL, params={"callsign": callsign}, timeout=10)
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        route = data.get("route", [])
        if len(route) >= 2:
            return route[0], route[-1]
    except Exception as e:
        print(f"  [warn] Could not fetch route for {callsign}: {e}")
    return None, None


def fetch_flight_state(callsign: str) -> dict | None:
    """Query OpenSky for a specific callsign. Returns state dict or None."""
    padded = callsign.ljust(8)  # OpenSky pads callsigns to 8 chars
    params = {"callsign": callsign}
    try:
        resp = requests.get(OPENSKY_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [error] OpenSky request failed for {callsign}: {e}")
        return None

    states = data.get("states") or []
    # Find best match (callsign field is index 1, stripped)
    for s in states:
        if s[1] and s[1].strip().upper() == callsign.upper():
            origin_icao, dest_icao = fetch_route(callsign)
            return {
                "callsign": s[1].strip(),
                "icao24": s[0],
                "origin_country": s[2],
                "longitude": s[5],
                "latitude": s[6],
                "altitude_m": s[7],
                "on_ground": s[8],
                "velocity_ms": s[9],
                "heading": s[10],
                "origin_icao": origin_icao,
                "dest_icao": dest_icao,
            }

    return None


def lookup_flight_info(flight_number: str, flight_date: date | None) -> dict:
    """Look up origin, destination and scheduled times from FlightRadar24."""
    if _fr24 is None:
        print("  [warn] FlightRadarAPI not available")
        return {}
    try:
        results = _fr24.search(flight_number)
        flight_list = (results.get("results") or {}).get("flights") or []
        for item in flight_list:
            fid = item.get("id")
            if not fid:
                continue
            details = _fr24.get_flight_details(fid)
            if not details:
                continue
            dep_ts = (details.get("time") or {}).get("scheduled", {}).get("departure")
            arr_ts = (details.get("time") or {}).get("scheduled", {}).get("arrival")
            if not dep_ts:
                continue
            dep_dt = datetime.fromtimestamp(dep_ts, tz=timezone.utc)
            origin_tz_offset = (
                (details.get("airport") or {}).get("origin", {})
                .get("timezone", {}).get("offset", 0)
            )
            dep_local = dep_dt + timedelta(seconds=origin_tz_offset)
            if flight_date and dep_local.date() != flight_date:
                continue
            dest_tz_offset = (
                (details.get("airport") or {}).get("destination", {})
                .get("timezone", {}).get("offset", 0)
            )
            arr_dt = datetime.fromtimestamp(arr_ts, tz=timezone.utc) if arr_ts else None
            return {
                "origin_icao": ((details.get("airport") or {}).get("origin", {})
                                .get("code", {}).get("icao")),
                "dest_icao":   ((details.get("airport") or {}).get("destination", {})
                                .get("code", {}).get("icao")),
                "departure_dt":     dep_dt,
                "arr_dt":           arr_dt,
                "origin_tz_offset": origin_tz_offset,
                "dest_tz_offset":   dest_tz_offset,
            }
    except Exception as e:
        print(f"  [warn] FlightRadar24 lookup failed: {e}")
    return {}


def fmt_local(dt: datetime | None, tz_offset_secs: int = 0) -> str:
    """Format a datetime as HH:MM (UTC±HH:MM).

    If tz_offset_secs is non-zero (from FR24), convert the UTC dt into that
    local time. Otherwise fall back to the timezone already embedded in dt.
    """
    if dt is None:
        return "?"
    if tz_offset_secs:
        local = dt + timedelta(seconds=tz_offset_secs)
        total = tz_offset_secs
    elif dt.tzinfo is not None:
        local = dt
        total = int(dt.utcoffset().total_seconds())
    else:
        return dt.strftime("%H:%M")
    sign = "+" if total >= 0 else "-"
    h, m = divmod(abs(total) // 60, 60)
    return f"{local.strftime('%H:%M')} (UTC{sign}{h:02d}:{m:02d})"


STATUS_STYLE = {
    #            color       dash_array  opacity  weight
    "airborne":  ("#00cfff", None,       0.75,    2.5),
    "on_ground": ("#ffaa00", None,       0.5,     2.0),
    "completed": ("#555555", None,       0.35,    1.5),
    "upcoming":  ("#3366cc", "6 4",      0.45,    1.5),
    "unknown":   ("#444444", "4 4",      0.25,    1.0),
}

STATUS_LABEL = {
    "airborne":  ("✈", "#00cfff", "Airborne"),
    "on_ground": ("⊙", "#ffaa00", "On ground"),
    "completed": ("✓", "#888888", "Completed"),
    "upcoming":  ("○", "#3366cc", "Upcoming"),
    "unknown":   ("?", "#555555", "Unknown"),
}


def assign_statuses(flights: list[dict]) -> None:
    """Assign a 'status' field to each flight in-place based on date and live data."""
    today = date.today()

    now_aware = datetime.now(timezone.utc)
    now_naive = datetime.now()

    # First pass: apply date/time-based status where unambiguous
    for f in flights:
        fd  = f.get("flight_date")
        dep = f.get("departure_dt")
        arr = f.get("arr_dt")
        now = now_aware if (dep and dep.tzinfo) or (arr and arr.tzinfo) else now_naive
        if dep and arr and dep <= now <= arr:
            pass  # within flight window — let OpenSky decide airborne vs on_ground
        elif dep and now < dep:
            f["status"] = "upcoming"
        elif arr and now > arr:
            f["status"] = "completed"
        elif fd and fd < today:
            f["status"] = "completed"
        elif fd and fd > today:
            f["status"] = "upcoming"
        # otherwise: determined by OpenSky result below

    # Second pass: for today's/undated flights use live data + position in journey
    # Find pivot: first airborne, fallback to first on-ground
    pivot = next(
        (i for i, f in enumerate(flights)
         if f.get("latitude") is not None and not f.get("on_ground")),
        None,
    )
    if pivot is None:
        pivot = next(
            (i for i, f in enumerate(flights) if f.get("latitude") is not None),
            None,
        )

    for i, f in enumerate(flights):
        if "status" in f:
            continue  # already set by date logic
        if f.get("latitude") is not None and not f.get("on_ground"):
            f["status"] = "airborne"
        elif f.get("latitude") is not None and f.get("on_ground"):
            f["status"] = "on_ground"
        elif pivot is not None:
            f["status"] = "completed" if i < pivot else "upcoming"
        else:
            f["status"] = "unknown"


def layover_airport(flights: list[dict]) -> str | None:
    """Return the ICAO code of the layover airport if the traveller is between flights."""
    statuses = [f.get("status") for f in flights]
    has_completed = "completed" in statuses
    has_upcoming = "upcoming" in statuses
    is_between = has_completed and has_upcoming and "airborne" not in statuses and "on_ground" not in statuses
    if not is_between:
        return None
    # Layover airport = destination of the last completed flight
    for f in reversed(flights):
        if f.get("status") == "completed" and f.get("dest_icao"):
            return f["dest_icao"]
    return None


def build_map(flights: list[dict]) -> folium.Map:
    """Build a Folium map with flight positions and journey arcs."""
    # Collect all coordinates to fit the map bounds
    all_coords = []
    for f in flights:
        if f.get("latitude") is not None:
            all_coords.append((f["latitude"], f["longitude"]))
        for icao in (f.get("origin_icao"), f.get("dest_icao")):
            if icao:
                c = get_airport_coords(icao)
                if c:
                    all_coords.append(c)

    m = folium.Map(location=[20, 0], zoom_start=2, tiles="CartoDB dark_matter")

    if all_coords:
        lats = [c[0] for c in all_coords]
        lons = [c[1] for c in all_coords]
        m.fit_bounds(
            [[min(lats), min(lons)], [max(lats), max(lons)]],
            padding=(40, 40),
        )

    # Draw route arcs and airport dots per flight
    for f in flights:
        status = f.get("status", "unknown")
        color, dash_array, opacity, weight = STATUS_STYLE[status]

        origin_coords = get_airport_coords(f.get("origin_icao") or "")
        dest_coords = get_airport_coords(f.get("dest_icao") or "")

        if origin_coords and dest_coords:
            arc = great_circle_points(*origin_coords, *dest_coords)
            folium.PolyLine(
                locations=arc,
                color=color,
                weight=weight,
                opacity=opacity,
                dash_array=dash_array,
                tooltip=f"{f['callsign']} ({status}): {f.get('origin_icao')} → {f.get('dest_icao')}",
            ).add_to(m)
            dep_str = fmt_local(f.get("departure_dt"), f.get("origin_tz_offset", 0))
            arr_str = fmt_local(f.get("arr_dt"), f.get("dest_tz_offset", 0))
            for icao, coords, kind, time_str in [
                (f.get("origin_icao"), origin_coords, "Departure", dep_str),
                (f.get("dest_icao"),   dest_coords,   "Arrival",   arr_str),
            ]:
                display = airport_label(icao)
                popup_html = (
                    f'<div style="font-family:monospace">'
                    f'<b>{display}</b> ({icao})<br>{kind}: {time_str}</div>'
                )
                folium.CircleMarker(
                    location=coords,
                    radius=5,
                    color=color,
                    fill=True,
                    fill_color="#0a0a0a",
                    fill_opacity=1.0,
                    tooltip=f"{display} — {kind} {time_str}",
                    popup=folium.Popup(popup_html, max_width=200),
                ).add_to(m)
                folium.Marker(
                    location=coords,
                    icon=folium.DivIcon(
                        html=(
                            f'<div style="font-family:monospace;font-size:11px;'
                            f'color:{color};white-space:nowrap;'
                            f'margin-top:8px;margin-left:6px;">'
                            f'{display}</div>'
                        ),
                        icon_size=(40, 16),
                        icon_anchor=(0, 0),
                    ),
                ).add_to(m)

    def _icon_html(heading: int = 0, size: int = 32) -> str:
        """Return DivIcon HTML for the custom icon (or emoji fallback)."""
        if _ICON_DATA_URI:
            return (
                f'<img src="{_ICON_DATA_URI}" width="{size}" height="{size}" '
                f'style="transform:rotate({heading}deg);display:block;'
                f'filter:drop-shadow(0 0 4px #006688);">'
            )
        return (
            f'<div style="font-size:{size}px;transform:rotate({heading}deg);'
            f'display:inline-block;color:#00cfff;'
            f'text-shadow:0 0 6px #006688;">✈</div>'
        )

    # Airborne: icon at live GPS position, rotated to heading
    for f in flights:
        if f.get("status") != "airborne":
            continue
        lat, lon  = f["latitude"], f["longitude"]
        alt_ft    = round(f["altitude_m"] * 3.28084) if f["altitude_m"] else "?"
        speed_kts = round(f["velocity_ms"] * 1.94384) if f["velocity_ms"] else "?"
        heading   = round(f["heading"]) if f["heading"] else 0
        popup_html = f"""
        <div style="font-family: monospace; min-width: 160px;">
            <b>{f['callsign']}</b> — Airborne<br>
            {f.get('origin_icao','?')} → {f.get('dest_icao','?')}<br>
            Altitude: {alt_ft} ft<br>
            Speed: {speed_kts} kts<br>
            Heading: {heading}°
        </div>"""
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=220),
            tooltip=f"{f['callsign']} — {alt_ft} ft",
            icon=folium.DivIcon(html=_icon_html(heading), icon_size=(32, 32), icon_anchor=(16, 16)),
        ).add_to(m)

    # On ground: icon at GPS position, no rotation
    for f in flights:
        if f.get("status") != "on_ground":
            continue
        lat, lon = f["latitude"], f["longitude"]
        ap_label = airport_label(f.get("dest_icao") or "")
        folium.Marker(
            location=[lat, lon],
            tooltip=f"{f['callsign']} — on ground at {ap_label}",
            icon=folium.DivIcon(html=_icon_html(0), icon_size=(32, 32), icon_anchor=(16, 16)),
        ).add_to(m)

    # Layover: show icon at the layover airport
    layover = layover_airport(flights)
    if layover:
        coords = get_airport_coords(layover)
        if coords:
            folium.Marker(
                location=coords,
                tooltip=f"In layover at {airport_label(layover)}",
                icon=folium.DivIcon(html=_icon_html(0), icon_size=(32, 32), icon_anchor=(16, 16)),
            ).add_to(m)
            folium.Marker(
                location=coords,
                icon=folium.DivIcon(
                    html=f'<div style="font-size:11px;color:#ffdd00;font-family:monospace;'
                         f'white-space:nowrap;margin-top:34px;">⏳ {airport_label(layover)}</div>',
                    icon_anchor=(0, 0),
                ),
            ).add_to(m)

    # Legend
    legend_items = ""
    for f in flights:
        status = f.get("status", "unknown")
        icon, color, label = STATUS_LABEL[status]
        orig_lbl = airport_label(f["origin_icao"]) if f.get("origin_icao") else "?"
        dest_lbl = airport_label(f["dest_icao"])   if f.get("dest_icao")   else "?"
        route = f"{orig_lbl}→{dest_lbl}" if f.get("origin_icao") else ""
        dep_str = fmt_local(f.get("departure_dt"), f.get("origin_tz_offset", 0)) if f.get("departure_dt") else ""
        arr_str = fmt_local(f.get("arr_dt"), f.get("dest_tz_offset", 0)) if f.get("arr_dt") else ""
        times_str = f"{dep_str} → {arr_str}" if dep_str and arr_str else dep_str or arr_str
        legend_items += (
            f"<li style='margin:4px 0'>"
            f"<span style='color:{color}'>{icon}</span> "
            f"<b>{f['callsign']}</b> "
            f"<span style='color:#aaa'>{label}"
            f"{' · ' + route if route else ''}</span>"
            f"{'<br><span style=\"color:#888;font-size:11px;padding-left:14px\">' + times_str + '</span>' if times_str else ''}"
            f"</li>"
        )

    layover_banner = ""
    if layover:
        layover_banner = f"<div style='margin:6px 0 2px;color:#ffdd00'>⏳ Layover at {airport_label(layover)}</div>"

    legend_html = f"""
    <div style="
        position: fixed; bottom: 30px; left: 30px; z-index: 1000;
        background: rgba(0,0,0,0.8); color: #eee;
        padding: 12px 16px; border-radius: 8px;
        font-family: monospace; font-size: 13px;
        border: 1px solid #444; min-width: 200px;
    ">
        <b>Flight Journey</b>
        {layover_banner}
        <ul style="margin: 6px 0 0 0; padding-left: 16px;">
            {legend_items}
        </ul>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


def parse_and_fetch_flight(raw: str, use_callsign: bool = False) -> dict:
    """Parse a flight string and fetch live data. Returns a flight dict.

    Format: FLIGHT[/ORIGIN[/DEST[/DATE[/DEP_TIME[/ARR_TIME]]]]]
    Example: JBU126/KTPA/KJFK/2026-03-04/14:30-05:00/17:45-05:00
    """
    today = date.today()
    parts = raw.strip().upper().split("/")
    flight_raw    = parts[0]
    manual_origin = parts[1] if len(parts) >= 2 else None
    manual_dest   = parts[2] if len(parts) >= 3 else None
    flight_date   = None
    departure_dt  = None
    manual_arr_dt = None

    if len(parts) >= 4:
        try:
            flight_date = date.fromisoformat(parts[3])
        except ValueError:
            print(f"  [warn] Could not parse date '{parts[3]}', ignoring")
    if len(parts) >= 5 and flight_date:
        try:
            departure_dt = datetime.fromisoformat(f"{parts[3]}T{parts[4]}")
        except ValueError:
            print(f"  [warn] Could not parse departure time '{parts[4]}', ignoring")
    if len(parts) >= 6 and flight_date:
        try:
            arr_str   = parts[5]
            candidate = datetime.fromisoformat(f"{parts[3]}T{arr_str}")
            if departure_dt and candidate < departure_dt:
                next_day  = (flight_date + timedelta(days=1)).isoformat()
                candidate = datetime.fromisoformat(f"{next_day}T{arr_str}")
            manual_arr_dt = candidate
        except ValueError:
            print(f"  [warn] Could not parse arrival time '{parts[5]}', ignoring")

    callsign = flight_raw if use_callsign else iata_to_icao_callsign(flight_raw)
    date_str = f" ({flight_date})" if flight_date else ""
    print(f"\nFlight: {flight_raw}{date_str} → callsign {callsign}")

    fr24_info: dict = {}
    if not (manual_origin and manual_dest and departure_dt):
        print(f"  Looking up schedule via FlightRadar24...")
        fr24_info = lookup_flight_info(flight_raw, flight_date)
        if fr24_info:
            manual_origin = manual_origin or fr24_info.get("origin_icao")
            manual_dest   = manual_dest   or fr24_info.get("dest_icao")
            departure_dt  = departure_dt  or fr24_info.get("departure_dt")
            print(f"  FR24: {manual_origin} → {manual_dest}, "
                  f"dep {fmt_local(departure_dt, fr24_info.get('origin_tz_offset', 0))}, "
                  f"arr {fmt_local(fr24_info.get('arr_dt'), fr24_info.get('dest_tz_offset', 0))}")
        else:
            print(f"  FR24: no data found")

    now = datetime.now(timezone.utc) if (departure_dt and departure_dt.tzinfo) else datetime.now()
    skip_opensky = False
    skip_reason  = None
    arr_dt_check = manual_arr_dt or fr24_info.get("arr_dt")
    in_window    = departure_dt and arr_dt_check and departure_dt <= now <= arr_dt_check

    if not in_window:
        if departure_dt and now < departure_dt:
            skip_opensky, skip_reason = True, f"departs at {departure_dt.strftime('%H:%M')}, not yet"
        elif arr_dt_check and now > arr_dt_check:
            skip_opensky, skip_reason = True, "past arrival time"
        elif flight_date and flight_date > today:
            skip_opensky, skip_reason = True, "future date"
        elif flight_date and flight_date < today:
            skip_opensky, skip_reason = True, "past date"

    state = None
    if not skip_opensky:
        print(f"  Querying OpenSky...")
        state = fetch_flight_state(callsign)

    if state:
        print(f"  Found: lat={state['latitude']}, lon={state['longitude']}, "
              f"alt={round(state['altitude_m'] * 3.28084) if state['altitude_m'] else '?'} ft")
        if manual_origin: state["origin_icao"] = manual_origin
        if manual_dest:   state["dest_icao"]   = manual_dest
        state["flight_date"]      = flight_date
        state["departure_dt"]     = departure_dt
        state["arr_dt"]           = manual_arr_dt or fr24_info.get("arr_dt")
        state["origin_tz_offset"] = fr24_info.get("origin_tz_offset", 0)
        state["dest_tz_offset"]   = fr24_info.get("dest_tz_offset", 0)
        if state.get("origin_icao") and state.get("dest_icao"):
            print(f"  Route: {state['origin_icao']} → {state['dest_icao']}")
        else:
            print(f"  Route: not available (use FLIGHT/ORIGIN/DEST to specify)")
    else:
        if skip_reason:
            print(f"  Skipping OpenSky ({skip_reason})")
        else:
            print(f"  Not found in OpenSky (flight may not be airborne)")
        state = {
            "callsign":        callsign,
            "latitude":        None,
            "longitude":       None,
            "origin_icao":     manual_origin,
            "dest_icao":       manual_dest,
            "flight_date":     flight_date,
            "departure_dt":    departure_dt,
            "arr_dt":          manual_arr_dt or fr24_info.get("arr_dt"),
            "origin_tz_offset": fr24_info.get("origin_tz_offset", 0),
            "dest_tz_offset":   fr24_info.get("dest_tz_offset", 0),
        }
    return state


def main():
    parser = argparse.ArgumentParser(description="Visualize a flight journey on a live map.")
    parser.add_argument("flights", nargs="+",
                        help="Flight numbers in order (e.g. AA100 BA203 EK405)")
    parser.add_argument("--callsigns", action="store_true",
                        help="Treat inputs as ICAO callsigns directly (skip IATA conversion)")
    parser.add_argument("--output", default="map.html",
                        help="Output HTML file (default: map.html)")
    args = parser.parse_args()

    print(f"\nFlight Journey Tracker")
    print(f"======================")

    flights = []
    for raw in args.flights:
        flights.append(parse_and_fetch_flight(raw, use_callsign=args.callsigns))

    assign_statuses(flights)

    print(f"\nJourney status:")
    for f in flights:
        print(f"  {f['callsign']}: {f.get('status', 'unknown')}")

    print(f"\nBuilding map...")
    m = build_map(flights)

    output_path = os.path.abspath(args.output)
    m.save(output_path)
    print(f"Saved: {output_path}")

    webbrowser.open(f"file://{output_path}")
    print("Opened in browser.")


if __name__ == "__main__":
    main()
