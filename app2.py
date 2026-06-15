import streamlit as st
from spacetrack import SpaceTrackClient
import spacetrack.operators as op
from skyfield.api import EarthSatellite, load, wgs84
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta, timezone
import math
import bisect

# --- Math & Spatial Helpers ---
def euclidean_km(p1, p2) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))

def angular_sep_deg(gs_pos: tuple, sat_pos_a: tuple, sat_pos_b: tuple) -> float:
    """Angle at gs_pos between lines of sight to sat_pos_a and sat_pos_b (degrees)."""
    v1 = [sat_pos_a[i] - gs_pos[i] for i in range(3)]
    v2 = [sat_pos_b[i] - gs_pos[i] for i in range(3)]
    mag1 = math.sqrt(sum(x * x for x in v1))
    mag2 = math.sqrt(sum(x * x for x in v2))
    if mag1 < 1e-9 or mag2 < 1e-9:
        return 0.0
    cos_a = sum(v1[i] * v2[i] for i in range(3)) / (mag1 * mag2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))

def parse_tle_string(tle_string, ts):
    """Parses a block of TLE strings into chronologically sorted EarthSatellite objects."""
    lines = tle_string.strip().split('\n')
    entries = []
    for i in range(0, len(lines), 2):
        if i+1 >= len(lines): break
        l1, l2 = lines[i].strip(), lines[i+1].strip()
        try:
            sat = EarthSatellite(l1, l2, "sat", ts)
            epoch_dt = sat.epoch.utc_datetime()
            entries.append((epoch_dt, sat))
        except Exception:
            continue
    entries.sort(key=lambda x: x[0])
    return entries

def best_tle(entries: list, t_dt: datetime, max_age_days=7):
    """Finds the closest TLE in time for accurate relative propagation."""
    if not entries:
        return None
    epochs = [e[0] for e in entries]
    idx = bisect.bisect_left(epochs, t_dt)
    if idx == 0:
        cand_epoch, cand_sat = entries[0]
    elif idx >= len(entries):
        cand_epoch, cand_sat = entries[-1]
    else:
        be, bs = entries[idx - 1]
        ae, as_ = entries[idx]
        if abs((ae - t_dt).total_seconds()) < abs((be - t_dt).total_seconds()):
            cand_epoch, cand_sat = ae, as_
        else:
            cand_epoch, cand_sat = be, bs
    age_days = abs((t_dt - cand_epoch).total_seconds()) / 86400.0
    return None if age_days > max_age_days else cand_sat

# Ground station for beam calculation
GS_SELETAR = wgs84.latlon(1.3972, 103.8343) # Singapore

# --- Website Page Config ---
st.set_page_config(page_title="Orbital Tracker", layout="wide")

st.title("🛰️ Satellite TLE Data & Proximity Explorer")
st.markdown("Extract and visualize orbital elements and intercept screening parameters directly from Space-Track.org")

# --- Sidebar Inputs ---
st.sidebar.header("Settings")
st.sidebar.info("Enter your Space-Track.org credentials below.")
ST_USER = st.sidebar.text_input("Username (Email)")
ST_PASS = st.sidebar.text_input("Password", type="password")

st.sidebar.markdown("---")
st.sidebar.header("Tracking Parameters")

# Target satellites and reference satellite (e.g. ST-2)
sat_input = st.sidebar.text_input("Candidate NORAD IDs (comma separated)", value="41838, 43874, 50321")
ref_sat_input = st.sidebar.text_input("Reference Satellite NORAD ID (e.g., ST-2)", value="37606")

# Date limitations
min_allowed_date = datetime(2003, 1, 1).date()
max_allowed_date = datetime.now().date()

start_date = st.sidebar.date_input(
    "Start Date", 
    value=max_allowed_date - timedelta(days=7),
    min_value=min_allowed_date,
    max_value=max_allowed_date
)

end_date = st.sidebar.date_input(
    "End Date", 
    value=max_allowed_date,
    min_value=min_allowed_date,
    max_value=max_allowed_date
)

run_button = st.sidebar.button("Generate Graphs")

# --- Backend Logic ---
if run_button:
    if not ST_USER or not ST_PASS:
        st.error("Please enter your Space-Track credentials in the sidebar.")
    else:
        try:
            with st.spinner("Fetching data from Space-Track..."):
                st_client = SpaceTrackClient(identity=ST_USER, password=ST_PASS)
                
                sat_list = [s.strip() for s in sat_input.split(",") if s.strip()]
                ref_id = ref_sat_input.strip()
                
                drange = op.inclusive_range(start_date, end_date)
                
                # Fetch Candidate TLEs
                tle_data = st_client.gp_history(norad_cat_id=sat_list, epoch=drange, format='tle')
                # Fetch Reference TLEs
                ref_tle_data = st_client.gp_history(norad_cat_id=ref_id, epoch=drange, format='tle')

            if not tle_data or not ref_tle_data:
                st.warning("Insufficient data found for these satellites in the selected range.")
            else:
                ts = load.timescale()
                ref_tles = parse_tle_string(ref_tle_data, ts)
                
                lines = tle_data.strip().split('\n')
                plot_data = {sat: {
                    'epoch': [], 'inc': [], 'raan': [], 'ecc': [], 'arg_pe': [], 
                    'mean_anom': [], 'mean_mo': [], 'lon': [], 'dist': [], 'ang_sep': []
                } for sat in sat_list}

                for i in range(0, len(lines), 2):
                    if i+1 >= len(lines): break
                    l1, l2 = lines[i].strip(), lines[i+1].strip()
                    nid = str(int(l1[2:7]))
                    if nid not in plot_data: continue

                    sat_obj = EarthSatellite(l1, l2, nid, ts)
                    t = sat_obj.epoch
                    t_dt = t.utc_datetime()
                    
                    # Calculate basic orbital elements
                    plot_data[nid]['epoch'].append(t_dt)
                    plot_data[nid]['inc'].append(math.degrees(sat_obj.model.inclo))
                    plot_data[nid]['raan'].append(math.degrees(sat_obj.model.nodeo))
                    plot_data[nid]['ecc'].append(sat_obj.model.ecco)
                    plot_data[nid]['arg_pe'].append(math.degrees(sat_obj.model.argpo))
                    plot_data[nid]['mean_anom'].append(math.degrees(sat_obj.model.mo))
                    plot_data[nid]['mean_mo'].append(sat_obj.model.no_kozai * 1440 / (2 * math.pi))
                    
                    cand_geo = sat_obj.at(t)
                    plot_data[nid]['lon'].append(cand_geo.subpoint().longitude.degrees)

                    # --- Proximity & Beam calculations ---
                    ref_sat_best = best_tle(ref_tles, t_dt)
                    if ref_sat_best:
                        cand_pos = tuple(cand_geo.position.km)
                        ref_pos = tuple(ref_sat_best.at(t).position.km)
                        gs_pos = tuple(GS_SELETAR.at(t).position.km)
                        
                        dist_km = euclidean_km(cand_pos, ref_pos)
                        ang_deg = angular_sep_deg(gs_pos, ref_pos, cand_pos)
                        
                        plot_data[nid]['dist'].append(dist_km)
                        plot_data[nid]['ang_sep'].append(ang_deg)
                    else:
                        plot_data[nid]['dist'].append(None)
                        plot_data[nid]['ang_sep'].append(None)

                # --- Create Plotly Visuals ---
                fig = make_subplots(
                    rows=9, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                    subplot_titles=(
                        "Inclination (°)", "RAAN (°)", "Eccentricity", "Arg of Perigee (°)", 
                        "Mean Anomaly (°)", "Mean Motion", "Longitude (°)", 
                        f"3D Distance to {ref_id} (km)", f"Uplink Beam Separation vs {ref_id} at Seletar (°)"
                    )
                )

                sat_colors = [
                    '#D62728', '#1F77B4', '#2CA02C', '#FF7F0E', '#9467BD', 
                    '#17BECF', '#E377C2', '#BCBD22', '#8C564B', '#FF9896'
                ]

                for idx, sat in enumerate(sat_list):
                    if not plot_data[sat]['epoch']: continue
                    
                    current_color = sat_colors[idx % len(sat_colors)]
                    
                    # Map the data keys to the respective row
                    params = [
                        ('inc', 1), ('raan', 2), ('ecc', 3), ('arg_pe', 4), 
                        ('mean_anom', 5), ('mean_mo', 6), ('lon', 7), 
                        ('dist', 8), ('ang_sep', 9)
                    ]
                    
                    for p_key, row in params:
                        # Skip if there's no data for the proximity plots (missing reference TLEs)
                        if p_key in ['dist', 'ang_sep'] and all(v is None for v in plot_data[sat][p_key]):
                            continue
                            
                        fig.add_trace(go.Scatter(
                            x=plot_data[sat]['epoch'], y=plot_data[sat][p_key],
                            name=f"Sat {sat}", legendgroup=f"group_{sat}",
                            showlegend=(True if row == 1 else False),
                            mode='lines+markers',
                            line=dict(color=current_color),     
                            marker=dict(color=current_color)    
                        ), row=row, col=1)

                fig.update_layout(
                    height=2000, 
                    hovermode="x unified", 
                    template="plotly_dark", 
                    margin=dict(t=80, b=50, l=50, r=50)
                )
                
                fig.update_xaxes(showline=True, linewidth=1, linecolor='gray', mirror=True)
                fig.update_yaxes(showline=True, linewidth=1, linecolor='gray', mirror=True)
                fig.update_annotations(yshift=15) 
                
                st.plotly_chart(fig, use_container_width=True)
                st.success("Graphs generated! Two new plots assessing proximity and uplink beam separation have been added at the bottom.")

        except Exception as e:
            st.error(f"Error: {e}")