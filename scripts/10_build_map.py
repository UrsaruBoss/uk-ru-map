#!/usr/bin/env python3
"""
Build an interactive tactical map from a KML file and supplementary datasets.

Inputs:
- assets/doc.kml
- assets/images/
- assets/geo/*

Output:
- outputs/index.html
"""
# 10_build_map.py

import os
import folium
import xml.etree.ElementTree as ET

# Optional: geopandas for Ukraine border
try:
    import geopandas as gpd
    HAS_GEOPANDAS = True
except Exception:
    HAS_GEOPANDAS = False

# --- CONFIG ---
KML_FILE = os.path.join("assets", "doc.kml")
IMAGES_FOLDER = os.path.join("assets", "images")
OUTPUT_MAP = os.path.join("outputs", "index.html")

# Keep only "live-ish" relevant folders (name contains any of these tokens)
# When True, process placemarks in all folders (useful for debugging / one-off runs)
PROCESS_ALL_FOLDERS = False

FOLDERS_TO_KEEP = [
    "Frontline",
    "Important Areas",
    "Ukrainian Unit Positions",
    "Russian Unit Positions",
    "Axis",
    "Events",          # include common 'Events' / 'Incidents' folders
    "Ukrainian",      # matches "Ukrainian Presence", "Ukrainian Kherson Counterattack" etc.
    "Russian",        # matches "Russian ... Offensive" etc.
]

# Skip archives / old stuff (folder name contains any of these)
FOLDER_BLACKLIST_KEYWORDS = [
    "Archive", "Archives", "Old", "Backup", "Past", "History"
]

# --- Colors ---
COLORS = {
    "front":   "#EDEDED",   # off-white (not pure white)

    # RU: darker red line and wine-like fill
    "ru_line": "#E24A4A",
    "ru_fill": "#4A0A0A",

    # UA: steel blue line and dark fill
    "ua_line": "#4D86FF",
    "ua_fill": "#0B1E4B",

    # historic / old axes
    "hist_line": "#A9A9A9",
    "hist_fill": "#2A2A2A",

    # borders
    "ua_border": "#6AA8FF",   # UA border lighter than control fill
    "ru_border": "#804E4E",   # RU border subtle

    "other_line": "#888888",
    "other_fill": "#2F2F2F",
}


def hex_kml_to_html(kml_color: str) -> str:
    """KML color = AABBGGRR -> HTML = #RRGGBB"""
    if not kml_color:
        return "#FF0000"
    clean = kml_color.strip().lstrip("#")
    if len(clean) == 8:
        clean = clean[2:]  # drop alpha
    if len(clean) != 6:
        return "#FF0000"
    # BBGGRR -> RRGGBB
    return f"#{clean[4:6]}{clean[2:4]}{clean[0:2]}"

def is_blacklisted_folder(folder_name: str) -> bool:
    ln = (folder_name or "").lower()
    return any(k.lower() in ln for k in FOLDER_BLACKLIST_KEYWORDS)

def is_allowed_folder(folder_name: str) -> bool:
    if PROCESS_ALL_FOLDERS:
        return True
    if not folder_name:
        return False
    if is_blacklisted_folder(folder_name):
        return False
    return any(token.lower() in folder_name.lower() for token in FOLDERS_TO_KEEP)

def parse_kml_styles(root, ns):
    """
    Return:
      style_defs: { "#styleId": {icon, color, fill, width} }
      style_maps: { "#styleMapId": "#resolvedStyleId" }
    """
    style_defs = {}
    style_maps = {}

    # 1) Styles
    for style in root.findall(".//kml:Style", ns):
        s_id = style.get("id")
        if not s_id:
            continue

        data = {"icon": None, "color": None, "fill": None, "width": None}

        # Icon
        icon_href = style.find(".//kml:Icon/kml:href", ns)
        if icon_href is not None and icon_href.text:
            fname = os.path.basename(icon_href.text.strip())
            # Resolve to filesystem image path in assets/images so folium can read it
            src_path = os.path.join(IMAGES_FOLDER, fname)
            if os.path.exists(src_path):
                data["icon"] = src_path

        # Line style
        line_color = style.find(".//kml:LineStyle/kml:color", ns)
        if line_color is not None and line_color.text:
            data["color"] = hex_kml_to_html(line_color.text)

        line_width = style.find(".//kml:LineStyle/kml:width", ns)
        if line_width is not None and line_width.text:
            try:
                data["width"] = float(line_width.text.strip())
            except Exception:
                pass

        # Poly style
        poly_color = style.find(".//kml:PolyStyle/kml:color", ns)
        if poly_color is not None and poly_color.text:
            data["fill"] = hex_kml_to_html(poly_color.text)

        style_defs[f"#{s_id}"] = data

    # 2) StyleMaps (UAControlMap uses these heavily)
    for sm in root.findall(".//kml:StyleMap", ns):
        sm_id = sm.get("id")
        if not sm_id:
            continue

        normal_pair = None
        for pair in sm.findall(".//kml:Pair", ns):
            key = pair.find("kml:key", ns)
            url = pair.find("kml:styleUrl", ns)
            if key is None or url is None or not url.text:
                continue
            if key.text.strip() == "normal":
                normal_pair = url.text.strip()
                break

        if normal_pair:
            style_maps[f"#{sm_id}"] = normal_pair

    return style_defs, style_maps

def resolve_style(style_url: str, style_defs: dict, style_maps: dict):
    """Resolve #StyleMap -> #Style if needed"""
    if not style_url:
        return None
    if style_url in style_maps:
        style_url = style_maps[style_url]
    return style_defs.get(style_url)

def classify_feature(folder_name: str, placemark_name: str) -> str:
    """
    Returns: 'historic' | 'ua' | 'ru' | 'other'
    """
    fn = (folder_name or "").lower()
    nm = (placemark_name or "").lower()

    # Historic / initial invasion
    if "initial invasion" in nm or "initial" in nm and "invasion" in nm:
        return "historic"
    if "kyiv axis" in nm and ("initial" in nm or "invasion" in nm):
        return "historic"
    if "2022" in nm and ("axis" in nm or "offensive" in nm):
        return "historic"

    # UA by name or folder
    if "ukrainian" in nm or "ukrainian" in fn:
        return "ua"
    if "kherson counterattack" in nm or "counterattack" in nm and "ukrainian" in nm:
        return "ua"

    # RU by name or folder
    if "russian" in nm or "russian" in fn:
        return "ru"
    if "important areas" in fn:
        # usually occupied/controlled areas
        return "ru"

    return "other"

def add_country_border(m: folium.Map, gdf, name_or_iso, color, weight, opacity, layer_name,
                       fill=False, fill_color=None, fill_opacity=0.0, show=True, control=True):
    fg = folium.FeatureGroup(name=layer_name, show=show, control=control).add_to(m)

    cols = {c.lower(): c for c in gdf.columns}
    iso_col  = cols.get("iso_a3") or cols.get("adm0_a3") or cols.get("sov_a3")
    name_col = cols.get("admin") or cols.get("name") or cols.get("sovereignt")

    # Select
    if iso_col and isinstance(name_or_iso, str) and len(name_or_iso) == 3:
        country = gdf[gdf[iso_col] == name_or_iso]
    elif name_col:
        country = gdf[gdf[name_col].astype(str).str.lower().str.contains(str(name_or_iso).lower(), na=False)]
    else:
        country = gdf[gdf.astype(str).apply(
            lambda r: r.str.contains(str(name_or_iso), case=False, na=False).any(), axis=1
        )]

    if country.empty:
        print(f"Warning: {name_or_iso} not found in shapefile. Columns: {list(gdf.columns)}")
        return None

    # CRS to WGS84
    if country.crs is None:
        country = country.set_crs("EPSG:4326")
    else:
        country = country.to_crs("EPSG:4326")

    folium.GeoJson(
        country.__geo_interface__,
        style_function=lambda feat: {
            "color": color,
            "weight": weight,
            "opacity": opacity,
            "fill": fill,
            "fillColor": fill_color if fill_color else color,
            "fillOpacity": fill_opacity
        },
        name=layer_name
    ).add_to(fg)

    return fg

def add_borders_from_shp(m: folium.Map):
    shp_path = os.path.join("assets", "geo", "ne_110m_admin_0_countries.shp")
    if not os.path.exists(shp_path):
        print(f"Error: shapefile not found at {shp_path}")
        return None, None

    if not HAS_GEOPANDAS:
        print("Error: geopandas not available. Install geopandas.")
        return None, None

    import geopandas as gpd
    gdf = gpd.read_file(shp_path)

    # halo rings (not part of layer control)
    add_country_border(
        m, gdf, "UKR",
        color="#111111", weight=5, opacity=0.85,
        layer_name="UA Border (halo)",
        fill=False, show=True, control=False
    )
    add_country_border(
        m, gdf, "UKR",
        color="#000000", weight=7, opacity=0.65,
        layer_name="UA Border (halo)",
        fill=False, show=True, control=False
    )

    # real borders (user-controllable)
    fg_ua_border = add_country_border(
        m, gdf, "UKR",
        color=COLORS["ua_border"], weight=3.5, opacity=0.95,
        layer_name="UA Border", fill=False, show=True, control=True
    )

    fg_ru_border = add_country_border(
        m, gdf, "RUS",
        color=COLORS["ru_border"], weight=2.2, opacity=0.55,
        layer_name="Russia Border", fill=False, show=False, control=True
    )

    print("Added UA borders (and optional RU) from shapefile.")
    return fg_ua_border, fg_ru_border

import folium

def add_legend_and_layers(m, COLORS, layer_vars: dict):
    """
    COLORS: dict cu cheile tale (front, ru_fill, ru_line, ua_fill, ua_line, hist_line, ua_border, ru_border)
    layer_vars: dict {"Label": "leaflet_layer_var_name", ...} unde value = fg.get_name()
    """

    map_var = m.get_name()

    # construim lista de checkbox-uri din layer_vars
    rows = []
    for label, varname in layer_vars.items():
        safe_id = "lay_" + "".join([c if c.isalnum() else "_" for c in label.lower()])
        rows.append(f"""
          <label class="ucdpLayerRow" for="{safe_id}">
            <input type="checkbox" id="{safe_id}" data-layer="{varname}">
            <span>{label}</span>
          </label>
        """)

    layers_html = "\n".join(rows)

    html = f"""
    <style>
      .dock-btn{{
    cursor:pointer;
    user-select:none;
    padding: 4px 9px;
    border-radius: 9px;
    border: 1px solid rgba(255,255,255,0.14);
    background: rgba(255,255,255,0.06);
    font-weight: 900;
    font-size: 12px;
    opacity: .92;
    transition: transform 90ms ease, opacity 140ms ease, background 140ms ease, box-shadow 140ms ease;
    box-shadow: 0 6px 16px rgba(0,0,0,0.18);
  }}
  .dock-btn:hover{{
    opacity: 1;
    background: rgba(255,255,255,0.10);
    box-shadow: 0 10px 22px rgba(0,0,0,0.30);
  }}
  .dock-btn:active{{
    transform: translateY(1px);
    opacity: .95;
  }}
  .dock-btn.dock-on{{
    background: rgba(255,255,255,0.12);
    box-shadow: 0 10px 26px rgba(0,0,0,0.35);
  }}

  .dock-pill{{
    display:inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid rgba(255,255,255,0.14);
    background: rgba(255,255,255,0.06);
    font-size: 11px;
    font-weight: 900;
    opacity:.88;
    cursor:pointer;
    user-select:none;
    transition: opacity 140ms ease, background 140ms ease, box-shadow 140ms ease, transform 90ms ease;
    box-shadow: 0 6px 16px rgba(0,0,0,0.14);
  }}
  .dock-pill:hover{{ opacity:1; background: rgba(255,255,255,0.10); box-shadow: 0 10px 22px rgba(0,0,0,0.26); }}
  .dock-pill:active{{ transform: translateY(1px); }}
  .dock-pill.dock-on{{ opacity:1; background: rgba(255,255,255,0.12); box-shadow: 0 10px 26px rgba(0,0,0,0.32); }} 
      #mapdock {{
        position: fixed;
        bottom: 20px;
        right: 20px;
        z-index: 999999;
        background: rgba(0,0,0,0.78);
        padding: 10px 12px;
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 12px;
        font-size: 13px;
        color: #EDEDED;
        font-family: Arial, sans-serif;
        line-height: 1.35;
        min-width: 260px;
        max-width: 320px;
        box-shadow: 0 6px 18px rgba(0,0,0,0.35);
        pointer-events: auto;
        backdrop-filter: blur(3px);
      }}

      #mapdock .top {{
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:10px;
        margin-bottom: 8px;
      }}
      #mapdock .title {{
        font-weight: 900;
        letter-spacing: 0.3px;
        opacity: .95;
      }}
      #mapdock .btn {{
        cursor:pointer;
        user-select:none;
        padding: 4px 8px;
        border-radius: 9px;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
        font-weight: 800;
        font-size: 12px;
        opacity: .92;
      }}
      #mapdock.mapdock-collapsed .body {{ display:none; }}

      #mapdock .tabs {{
        display:flex;
        gap:8px;
        margin-bottom: 10px;
      }}
      #mapdock .tab {{
        flex:1;
        text-align:center;
        padding: 6px 8px;
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
        cursor:pointer;
        user-select:none;
        font-weight: 900;
        font-size: 12px;
        opacity: .86;
      }}
      #mapdock .tab.active {{
        opacity: 1;
        background: rgba(255,255,255,0.12);
      }}

      #mapdock .panel {{ display:none; }}
      #mapdock .panel.active {{ display:block; }}

      /* Legend rows */
      #mapdock .legendRow {{
        display:flex;
        align-items:center;
        gap:8px;
        margin:6px 0;
      }}

      /* Layers rows */
      .ucdpLayerRow {{
        display:flex;
        align-items:center;
        gap:10px;
        padding: 6px 8px;
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.04);
        margin: 6px 0;
        cursor:pointer;
        user-select:none;
      }}
      .ucdpLayerRow:hover {{
        background: rgba(255,255,255,0.07);
      }}
      .ucdpLayerRow input {{
        transform: scale(1.05);
      }}

      #mapdock .hint {{
        margin-top: 8px;
        opacity: .7;
        font-size: 11px;
      }}
    </style>

    <div id="mapdock">
      <div class="top">
        <div class="title">Legend & Layers</div>
        <div class="dock-btn dock-on" id="mapdockToggle">Hide</div>
      </div>

      <div class="body">
        <div class="tabs">
          <div class="tab active" data-tab="legend">Legend</div>
          <div class="tab" data-tab="layers">Layers</div>
        </div>

        <div class="panel active" id="panelLegend">
          <div class="legendRow">
            <span style="width:22px; height:0; border-top:3px solid {COLORS['front']}; display:inline-block;"></span>
            <span>Frontline</span>
          </div>

          <div class="legendRow">
            <span style="width:18px; height:12px; background:{COLORS['ru_fill']}; border:2px solid {COLORS['ru_line']}; display:inline-block;"></span>
            <span>RU Control / Occupied</span>
          </div>

          <div class="legendRow">
            <span style="width:18px; height:12px; background:{COLORS['ua_fill']}; border:2px solid {COLORS['ua_line']}; display:inline-block;"></span>
            <span>UA Control / Presence</span>
          </div>

          <div class="legendRow">
            <span style="width:22px; height:0; border-top:2px dashed {COLORS['ru_line']}; display:inline-block;"></span>
            <span>RU Axis</span>
          </div>

          <div class="legendRow">
            <span style="width:22px; height:0; border-top:2px dashed {COLORS['ua_line']}; display:inline-block;"></span>
            <span>UA Axis</span>
          </div>

          <div class="legendRow">
            <span style="width:22px; height:0; border-top:2px dashed {COLORS['hist_line']}; display:inline-block;"></span>
            <span>Historic</span>
          </div>

          <div class="legendRow">
            <span style="width:22px; height:0; border-top:2px solid {COLORS['ua_border']}; display:inline-block;"></span>
            <span>Ukraine border</span>
          </div>

          <div class="legendRow">
            <span style="width:22px; height:0; border-top:2px solid {COLORS['ru_border']}; display:inline-block;"></span>
            <span>Russia border</span>
          </div>

          <div class="hint">Tip: Layers tab = toggles. Legend tab = meaning.</div>
        </div>

        <div class="panel" id="panelLayers">
          {layers_html}
          <div class="hint">Toggle overlays here (replaces Leaflet LayerControl).</div>
        </div>
      </div>
    </div>

    <script>
      (function(){{
        var MAP_NAME = {map_var!r};

        function waitFor(name, tries, cb) {{
          tries = tries || 200;
          var t = setInterval(function() {{
            if (window[name]) {{
              clearInterval(t);
              cb(window[name]);
            }} else if (--tries <= 0) {{
              clearInterval(t);
              console.warn("mapdock: missing " + name);
            }}
          }}, 50);
        }}

        waitFor(MAP_NAME, 200, function(map){{
          var dock = document.getElementById("mapdock");
var btnToggle = document.getElementById("mapdockToggle");

function applyState(collapsed){{
  dock.classList.toggle("mapdock-collapsed", collapsed);
  btnToggle.textContent = collapsed ? "Show" : "Hide";
  btnToggle.classList.toggle("dock-on", !collapsed);
}}

// restore
try {{
  var st = localStorage.getItem("mapdock_collapsed");
  applyState(st === "1");
}} catch(e) {{
  applyState(false);
}}

btnToggle.addEventListener("click", function(ev){{
  ev.preventDefault(); ev.stopPropagation();
  var collapsed = !dock.classList.contains("mapdock-collapsed");
  applyState(collapsed);
  try {{ localStorage.setItem("mapdock_collapsed", collapsed ? "1" : "0"); }} catch(e){{}}
}});


          // tabs
          function setTab(which) {{
            document.querySelectorAll("#mapdock .tab").forEach(function(t) {{
              t.classList.toggle("active", t.getAttribute("data-tab") === which);
            }});
            document.getElementById("panelLegend").classList.toggle("active", which === "legend");
            document.getElementById("panelLayers").classList.toggle("active", which === "layers");
          }}
          document.querySelectorAll("#mapdock .tab").forEach(function(t){{
            t.addEventListener("click", function(ev){{
              ev.preventDefault(); ev.stopPropagation();
              setTab(t.getAttribute("data-tab"));
            }});
          }});

          // keep map from stealing interactions when hovering dock
          dock.addEventListener("mouseenter", function(){{
            try {{
              map.dragging.disable();
              map.scrollWheelZoom.disable();
              map.doubleClickZoom.disable();
              map.boxZoom.disable();
              map.keyboard.disable();
            }} catch(e){{}}
          }});
          dock.addEventListener("mouseleave", function(){{
            try {{
              map.dragging.enable();
              map.scrollWheelZoom.enable();
              map.doubleClickZoom.enable();
              map.boxZoom.enable();
              map.keyboard.enable();
            }} catch(e){{}}
          }});

          // layers toggle hookup
          function syncCheckboxes() {{
            document.querySelectorAll("#panelLayers input[type='checkbox'][data-layer]").forEach(function(cb){{
              var lname = cb.getAttribute("data-layer");
              var layerObj = window[lname];
              if (!layerObj) {{
                // layer might be missing if name wrong
                cb.checked = false;
                cb.disabled = true;
                return;
              }}
              cb.checked = map.hasLayer(layerObj);
            }});
          }}

          function attach() {{
            document.querySelectorAll("#panelLayers input[type='checkbox'][data-layer]").forEach(function(cb){{
              cb.addEventListener("change", function(ev){{
                ev.preventDefault(); ev.stopPropagation();
                var lname = cb.getAttribute("data-layer");
                var layerObj = window[lname];
                if (!layerObj) return;

                if (cb.checked) map.addLayer(layerObj);
                else map.removeLayer(layerObj);
              }});
            }});

            // whenever overlays change (from code or elsewhere), resync
            map.on("overlayadd", syncCheckboxes);
            map.on("overlayremove", syncCheckboxes);

            // initial sync
            syncCheckboxes();
          }}

          // some layers may be defined after map init; poll a bit
          var tries = 0;
          var poll = setInterval(function(){{
            tries++;
            // if at least one layer resolves, attach, then stop
            var any = false;
            document.querySelectorAll("#panelLayers input[type='checkbox'][data-layer]").forEach(function(cb){{
              var lname = cb.getAttribute("data-layer");
              if (window[lname]) any = true;
            }});
            if (any || tries > 160) {{
              clearInterval(poll);
              attach();
            }}
          }}, 50);
        }});
      }})();
    </script>
    """

    m.get_root().html.add_child(folium.Element(html))


import json
from datetime import datetime
from datetime import datetime
import os, json
import folium

def add_ucdp_events_layer(m, ucdp_json_path=None):
    # Try common locations for UCDP dataset and fall back to the provided path
    candidates = [
        os.path.join("data", "processed", "ucdp_events_filtered.json"),
        os.path.join("data", "ucdp_events_filtered.json"),
        os.path.join("assets", "ucdp_events_filtered.json"),
        "ucdp_events_filtered.json",
    ]

    if ucdp_json_path is None:
        for p in candidates:
            if os.path.exists(p):
                ucdp_json_path = p
                break

    if not ucdp_json_path or not os.path.exists(ucdp_json_path):
        print(f"Warning: cannot find UCDP dataset. Tried: {candidates}. Continuing with empty dataset (filter panel will be shown).")
        # create empty dataset and feature group so the UI/filter still appears
        fg_ucdp = folium.FeatureGroup(name="UCDP Events", show=False).add_to(m)
        fg_var = fg_ucdp.get_name()
        empty_fc = {"type": "FeatureCollection", "features": []}
        empty_meta = {"min_date": "", "max_date": "", "max_best": 0, "max_civ": 0, "count": 0}
        m.get_root().html.add_child(folium.Element(f"<script>window.__ucdp_fc = {json.dumps(empty_fc)};</script>"))
        m.get_root().html.add_child(folium.Element(f"<script>window.__ucdp_meta = {json.dumps(empty_meta)};</script>"))
        return fg_ucdp, fg_var

    print(f"Using UCDP dataset: {ucdp_json_path}")
    with open(ucdp_json_path, "r", encoding="utf-8") as f:
        u = json.load(f)

    # If the JSON is already a GeoJSON FeatureCollection, use it directly.
    if isinstance(u, dict) and u.get("type") == "FeatureCollection" and "features" in u:
        fc = u
        features = fc.get("features", [])

        fg_ucdp = folium.FeatureGroup(name="UCDP Events", show=False).add_to(m)
        fg_var = fg_ucdp.get_name()

        # dataset JS global
        m.get_root().html.add_child(
            folium.Element(f"<script>window.__ucdp_fc = {json.dumps(fc)};</script>")
        )

        # meta for defaults based on properties if available
        dates = [ft.get("properties", {}).get("date", "") for ft in features]
        dates = [d for d in dates if isinstance(d, str) and len(d) >= 10]

        bests = [ft.get("properties", {}).get("best", 0) for ft in features]
        civs  = [ft.get("properties", {}).get("civ", 0) for ft in features]

        meta = {
            "min_date": min(dates) if dates else "",
            "max_date": max(dates) if dates else "",
            "max_best": int(max(bests)) if bests else 0,
            "max_civ": int(max(civs)) if civs else 0,
            "count": len(features),
        }

        m.get_root().html.add_child(
            folium.Element(f"<script>window.__ucdp_meta = {json.dumps(meta)};</script>")
        )

        print(f"UCDP FeatureCollection: {len(features)} features")
        return fg_ucdp, fg_var

    # If the file is a top-level list, determine whether it's GeoJSON features or raw events
    if isinstance(u, list):
        if u and isinstance(u[0], dict) and (u[0].get("type") == "Feature" or "geometry" in u[0]):
            # assume list of GeoJSON features
            fc = {"type": "FeatureCollection", "features": u}
            features = u

            fg_ucdp = folium.FeatureGroup(name="UCDP Events", show=False).add_to(m)
            fg_var = fg_ucdp.get_name()

            m.get_root().html.add_child(
                folium.Element(f"<script>window.__ucdp_fc = {json.dumps(fc)};</script>")
            )

            dates = [ft.get("properties", {}).get("date", "") for ft in features]
            dates = [d for d in dates if isinstance(d, str) and len(d) >= 10]

            bests = [ft.get("properties", {}).get("best", 0) for ft in features]
            civs  = [ft.get("properties", {}).get("civ", 0) for ft in features]

            meta = {
                "min_date": min(dates) if dates else "",
                "max_date": max(dates) if dates else "",
                "max_best": int(max(bests)) if bests else 0,
                "max_civ": int(max(civs)) if civs else 0,
                "count": len(features),
            }

            m.get_root().html.add_child(
                folium.Element(f"<script>window.__ucdp_meta = {json.dumps(meta)};</script>")
            )

            print(f"UCDP FeatureCollection (from list): {len(features)} features")
            return fg_ucdp, fg_var
        else:
            # assume list of raw event dicts; wrap into expected structure
            events_list = u
            u = {"events": events_list}
            # fall through to events parsing below

    def pick_first(ev, keys, default=""):
        for k in keys:
            v = ev.get(k)
            if v is None:
                continue
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
        return default

    def build_summary(ev):
        event_type = pick_first(ev, ["event_type", "type_of_violence", "event_clarity", "event"], "")
        a1 = pick_first(ev, ["side_a", "actor1", "actor_a", "actor"], "")
        a2 = pick_first(ev, ["side_b", "actor2", "actor_b"], "")
        where = pick_first(ev, ["where_coordinates", "location", "adm_2", "adm_1", "country"], "")
        notes = pick_first(ev, ["notes", "comment", "description", "source_article", "source_office"], "")

        bits = []
        if event_type:
            bits.append(event_type)
        if a1 or a2:
            bits.append(" vs ".join([x for x in [a1, a2] if x]))
        if where:
            bits.append(where)
        if notes:
            notes_short = notes[:240] + ("…" if len(notes) > 240 else "")
            bits.append(notes_short)

        return " | ".join([b for b in bits if b])

    features = []
    for ev in (u.get("events") or []):
        lat = ev.get("latitude"); lon = ev.get("longitude")
        if lat is None or lon is None:
            continue

        date_str = (ev.get("date_start") or ev.get("date_end") or "")
        date10 = date_str[:10] if isinstance(date_str, str) and len(date_str) >= 10 else ""

        best = int(ev.get("best", 0) or 0)
        civ  = int(ev.get("deaths_civilians", 0) or 0)
        prec = int(ev.get("where_prec", 9) or 9)

        conflict = ev.get("conflict_name", "") or ""
        where = ev.get("where_coordinates", "") or ev.get("adm_1", "") or ev.get("country", "") or ""

        event_type = pick_first(ev, ["event_type", "type_of_violence", "event"], "")
        side_a = pick_first(ev, ["side_a", "actor1", "actor_a"], "")
        side_b = pick_first(ev, ["side_b", "actor2", "actor_b"], "")
        source = pick_first(ev, ["source", "source_office", "source_original", "source_article"], "")
        notes = pick_first(ev, ["notes", "comment", "description"], "")

        summary = build_summary(ev)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": {
                "date": date10,
                "best": best,
                "civ": civ,
                "prec": prec,
                "conflict": conflict,
                "where": where,

                "event_type": event_type,
                "side_a": side_a,
                "side_b": side_b,
                "source": source,
                "notes": (notes[:600] + ("…" if len(notes) > 600 else "")) if isinstance(notes, str) else "",
                "summary": summary,
            }
        })

    fc = {"type": "FeatureCollection", "features": features}

    fg_ucdp = folium.FeatureGroup(name="UCDP Events", show=False).add_to(m)
    fg_var = fg_ucdp.get_name()

    # dataset JS global
    m.get_root().html.add_child(
        folium.Element(f"<script>window.__ucdp_fc = {json.dumps(fc)};</script>")
    )

    # meta for defaults
    dates = [ft.get("properties", {}).get("date", "") for ft in features]
    dates = [d for d in dates if isinstance(d, str) and len(d) >= 10]

    bests = [ft.get("properties", {}).get("best", 0) for ft in features]
    civs  = [ft.get("properties", {}).get("civ", 0) for ft in features]

    meta = {
        "min_date": min(dates) if dates else "",
        "max_date": max(dates) if dates else "",
        "max_best": int(max(bests)) if bests else 0,
        "max_civ": int(max(civs)) if civs else 0,
        "count": len(features),
    }

    m.get_root().html.add_child(
        folium.Element(f"<script>window.__ucdp_meta = {json.dumps(meta)};</script>")
    )

    print(f"UCDP dataset: {len(features)} points")
    return fg_ucdp, fg_var


def add_ucdp_filter_panel(m, fg_var):
    map_var = m.get_name()
    fg_var_json = json.dumps(fg_var) # Ensure safe JS string

    html = f"""
    <style>
      #ucdpFilter {{
        position: fixed;
        top: 18px;
        right: 18px;
        z-index: 999999;
        background: rgba(0,0,0,0.78);
        padding: 12px 14px;
        border: 1px solid rgba(255,255,255,0.16);
        border-radius: 12px;
        color: #EDEDED;
        font-family: Arial, sans-serif;
        font-size: 13px;
        min-width: 300px;
        max-width: 380px;
        box-shadow: 0 10px 28px rgba(0,0,0,0.45);
        backdrop-filter: blur(3px);
        pointer-events: auto;
      }}
      .leaflet-top.leaflet-right {{ margin-top: 92px; }}

      #ucdpFilter .row {{
        display:flex; justify-content:space-between; gap:10px; align-items:center;
        margin: 8px 0;
      }}
      #ucdpFilter .lbl {{ opacity:.85; }}

      #ucdpFilter input[type="date"], #ucdpFilter input[type="number"] {{
        width: 160px;
        background: rgba(255,255,255,0.06);
        color: #EDEDED;
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 8px;
        padding: 4px 6px;
        outline: none;
      }}
      #ucdpFilter input[type="range"] {{
        width: 160px;
      }}

      #ucdpFilter .btn {{
        cursor:pointer;
        user-select:none;
        padding: 5px 9px;
        border-radius: 9px;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
        font-weight: 800;
        font-size: 12px;
        opacity: .92;
      }}
      #ucdpFilter .btn:active {{ transform: translateY(1px); }}

      #ucdpFilter .dock-pill {{
        display:inline-block;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
        font-size: 11px;
        font-weight: 800;
        opacity:.9;
        cursor:pointer;
        user-select:none;
      }}
      #ucdpFilter .dock-pill:hover {{ opacity: 1; }}

      #ucdpFilter.ucdp-collapsed .body {{ display:none; }}

      #ucdpAbout {{
        margin-top:10px;
        padding-top:10px;
        border-top:1px solid rgba(255,255,255,0.12);
        font-size:11.5px;
        line-height:1.25;
        opacity:.85;
      }}
      #ucdpAbout details {{ margin-top:6px; }}
      #ucdpAbout summary {{ cursor:pointer; font-weight:800; opacity:.95; }}
    </style>

    <div id="ucdpFilter">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:10px;">
        <div style="font-weight:900; letter-spacing:.3px;">🧭 UCDP Event Filters</div>
        <div class="dock-btn dock-on" id="ucdpToggle">Hide</div>
      </div>

      <div class="body" style="margin-top:10px;">
        <div id="ucdpMetaLine" style="opacity:.72; font-size:11.5px; margin-bottom:10px;">
          Loading dataset…
        </div>

        <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px;">
          <div class="dock-pill" data-range="7">Last 7d</div>
          <div class="dock-pill" data-range="30">Last 30d</div>
          <div class="dock-pill" data-range="90">Last 90d</div>
          <div class="dock-pill" id="ucdpAll">All</div>
        </div>

        <div class="row"><span class="lbl">From</span><input id="ucdpFrom" type="date" /></div>
        <div class="row"><span class="lbl">To</span><input id="ucdpTo" type="date" /></div>

        <div class="row"><span class="lbl">Min fatalities (Best)</span>
          <input id="ucdpMinBest" type="number" value="0" min="0" step="1" /></div>

        <div class="row"><span class="lbl">Min civilian</span>
          <input id="ucdpMinCiv" type="number" value="0" min="0" step="1" /></div>

        <div class="row">
          <span class="lbl">Max location precision</span>
          <div style="display:flex; flex-direction:column; gap:4px; align-items:flex-end;">
            <input id="ucdpMaxPrec" type="range" value="6" min="1" max="9" step="1" />
            <div id="ucdpPrecLabel" style="opacity:.85; font-size:11px;">≤ 6 (Low)</div>
          </div>
        </div>

        <div style="display:flex; gap:8px; margin-top:10px;">
          <div class="dock-btn" id="ucdpApply">Apply</div>
          <div class="dock-btn" id="ucdpReset">Reset</div>
        </div>

        <div id="ucdpAbout">
          <div style="font-weight:800;">About UCDP</div>
          <div style="opacity:.9; margin-top:4px;">
            UCDP (Uppsala Conflict Data Program) catalogs conflict events. Values shown here are per-record estimates.
          </div>
          <details>
            <summary>What do Best / Civ / where_prec mean?</summary>
            <div style="margin-top:6px; opacity:.95;">
              <div><b>Best</b>: UCDP “best estimate” for total fatalities for that event record.</div>
              <div><b>Civ</b>: estimated civilian fatalities (subset of total, when available).</div>
              <div><b>where_prec</b>: location precision code. Lower = more precise; higher = more coarse.</div>
            </div>
          </details>
        </div>
      </div>
    </div>

    <script>
      (function() {{
        var MAP_NAME = {map_var!r};
        var GROUP_NAME = {fg_var!r};

        function waitFor(name, tries, cb) {{
          tries = tries || 200;
          var t = setInterval(function() {{
            if (window[name]) {{
              clearInterval(t);
              cb(window[name]);
            }} else if (--tries <= 0) {{
              clearInterval(t);
              console.warn("UCDP: missing " + name);
            }}
          }}, 50);
        }}

        function parseDate(s) {{
          if (!s || s.length < 10) return null;
          var y = parseInt(s.slice(0,4),10);
          var m = parseInt(s.slice(5,7),10) - 1;
          var d = parseInt(s.slice(8,10),10);
          if (isNaN(y)||isNaN(m)||isNaN(d)) return null;
          return new Date(Date.UTC(y,m,d));
        }}
        function toISODate(dt) {{
          var y = dt.getUTCFullYear();
          var m = String(dt.getUTCMonth()+1).padStart(2,'0');
          var d = String(dt.getUTCDate()).padStart(2,'0');
          return y + "-" + m + "-" + d;
        }}
        function clampDateISO(dateISO, minISO, maxISO) {{
          if (minISO && dateISO < minISO) return minISO;
          if (maxISO && dateISO > maxISO) return maxISO;
          return dateISO;
        }}

        function esc(s) {{
          s = (s === undefined || s === null) ? "" : String(s);
          return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
        }}

        function precLabel(x) {{
          x = parseInt(x || 9, 10);
          if (x <= 2) return "High";
          if (x <= 4) return "Medium";
          if (x <= 6) return "Low";
          return "Very low";
        }}

        function init(map, ucdpGroup) {{
          if (!window.__ucdp_fc) {{
            console.warn("UCDP: window.__ucdp_fc missing");
            return;
          }}
          var meta = window.__ucdp_meta || {{}};

          var elFrom = document.getElementById("ucdpFrom");
          var elTo   = document.getElementById("ucdpTo");
          var elMinBest = document.getElementById("ucdpMinBest");
          var elMinCiv  = document.getElementById("ucdpMinCiv");
          var elMaxPrec = document.getElementById("ucdpMaxPrec");
          var elPrecLbl = document.getElementById("ucdpPrecLabel");
          var elMetaLine = document.getElementById("ucdpMetaLine");

          // --- defaults: From = max_date - 30d, To = today (clamped to max_date) ---
          var maxISO = meta.max_date || "";
          var minISO = meta.min_date || "";
          var todayISO = toISODate(new Date());

          var toISO = maxISO ? clampDateISO(todayISO, minISO, maxISO) : todayISO;

          var fromISO = "";
          if (maxISO) {{
            var maxDt = parseDate(maxISO);
            if (maxDt) {{
              var fromDt = new Date(maxDt.getTime() - 30*24*3600*1000);
              fromISO = toISODate(fromDt);
              fromISO = clampDateISO(fromISO, minISO, maxISO);
            }}
          }}
          if (!fromISO) fromISO = minISO || "";

          // restore last UI state if exists
          try {{
            var st = localStorage.getItem("ucdp_ui_state");
            if (st) {{
              var obj = JSON.parse(st);
              if (obj.from) fromISO = obj.from;
              if (obj.to) toISO = obj.to;
              if (obj.minBest !== undefined) elMinBest.value = obj.minBest;
              if (obj.minCiv !== undefined) elMinCiv.value = obj.minCiv;
              if (obj.maxPrec !== undefined) elMaxPrec.value = obj.maxPrec;
            }}
          }} catch(e) {{}}

          elFrom.value = fromISO;
          elTo.value = toISO;

          function updatePrecLabel() {{
            var v = parseInt(elMaxPrec.value || "6", 10);
            elPrecLbl.textContent = "≤ " + v + " (" + precLabel(v) + ")";
          }}
          updatePrecLabel();
          elMaxPrec.addEventListener("input", updatePrecLabel);

          // meta line
          if (elMetaLine) {{
            var cnt = meta.count || (window.__ucdp_fc.features ? window.__ucdp_fc.features.length : 0);
            elMetaLine.textContent = "Dataset: " + cnt + " events | Range: " + (minISO||"?") + " → " + (maxISO||"?");
          }}

          var geoLayer = null;

          function buildLayer() {{
            if (!map.hasLayer(ucdpGroup)) return;

            if (geoLayer) {{
              try {{ ucdpGroup.removeLayer(geoLayer); }} catch(e) {{}}
              geoLayer = null;
            }}

            var from = parseDate(elFrom.value);
            var to   = parseDate(elTo.value);
            var minBest = parseInt(elMinBest.value || "0", 10);
            var minCiv  = parseInt(elMinCiv.value || "0", 10);
            var maxPrec = parseInt(elMaxPrec.value || "9", 10);

            // persist UI state
            try {{
              localStorage.setItem("ucdp_ui_state", JSON.stringify({{
                from: elFrom.value, to: elTo.value,
                minBest: minBest, minCiv: minCiv, maxPrec: maxPrec
              }}));
            }} catch(e) {{}}

            geoLayer = L.geoJSON(window.__ucdp_fc, {{
              filter: function(feat) {{
                var p = (feat && feat.properties) ? feat.properties : {{}};
                var dt = parseDate(p.date);
                if (from && dt && dt < from) return false;
                if (to && dt && dt > to) return false;
                if ((p.best||0) < minBest) return false;
                if ((p.civ||0) < minCiv) return false;
                if ((p.prec||9) > maxPrec) return false;
                return true;
              }},
              pointToLayer: function(feat, latlng) {{
                var p = (feat && feat.properties) ? feat.properties : {{}};
                var r = 2.0 + Math.min(10.0, Math.sqrt(p.best || 0));
                return L.circleMarker(latlng, {{
                  radius: r, weight: 1, color: "#FFD166", fillOpacity: 0.55
                }});
              }},
              onEachFeature: function(feat, layer) {{
                var p = (feat && feat.properties) ? feat.properties : {{}};

                var what =
                  p.summary || (
                    (p.event_type ? (p.event_type + " | ") : "") +
                    ((p.side_a || p.side_b) ? ((p.side_a||"") + (p.side_b ? " vs " + p.side_b : "")) : "") +
                    (p.where ? (" | " + p.where) : "")
                  );

                var defs =
                  "<div style='margin-top:10px; padding-top:10px; border-top:1px solid rgba(0,0,0,0.10);'>" +
                  "<div style='font-weight:800; margin-bottom:6px;'>What these fields mean</div>" +
                  "<div style='opacity:.92; line-height:1.25;'>" +
                  "<div><b>Best</b>: UCDP best estimate for total fatalities (record-level).</div>" +
                  "<div><b>Civ</b>: estimated civilian fatalities (subset of total, when available).</div>" +
                  "<div><b>where_prec</b>: location precision code (lower = more precise).</div>" +
                  "</div></div>";

                var html =
                  "<div style='font-family:Arial;font-size:12px; max-width:340px;'>" +
                  "<div style='font-weight:900; font-size:13px; margin-bottom:2px;'>" + esc(p.conflict||"") + "</div>" +
                  "<div style='opacity:.82; margin-bottom:6px;'>" + esc(p.where||"") + "</div>" +

                  (what ? (
                    "<div style='margin:8px 0; padding:8px; border-radius:10px; background:rgba(0,0,0,0.06);'>" +
                    "<div style='font-weight:800; margin-bottom:4px;'>What happened</div>" +
                    "<div style='opacity:.95; line-height:1.25;'>" + esc(what) + "</div>" +
                    "</div>"
                  ) : "") +

                  "<div style='display:grid; grid-template-columns: 1fr 1fr; gap:6px;'>" +
                    "<div><b>Date:</b> " + esc(p.date||"") + "</div>" +
                    "<div style='text-align:right; opacity:.85;'><b>Loc precision:</b> " + esc(precLabel(p.prec)) + "</div>" +
                    "<div><b>Best:</b> " + esc(p.best||0) + "</div>" +
                    "<div style='text-align:right;'><b>Civ:</b> " + esc(p.civ||0) + "</div>" +
                    "<div><b>Prec:</b> " + esc(p.prec||9) + "</div>" +
                    "<div></div>" +
                  "</div>" +

                  (p.source ? ("<div style='margin-top:8px; opacity:.75;'><b>Source:</b> " + esc(p.source) + "</div>") : "") +
                  (p.notes ? ("<div style='margin-top:8px; opacity:.85; line-height:1.25;'><b>Notes:</b> " + esc(p.notes) + "</div>") : "") +

                  defs +
                  "</div>";

                layer.bindPopup(html, {{maxWidth: 360}});
              }}
            }});

            ucdpGroup.addLayer(geoLayer);
          }}

          function setRangeDays(days) {{
            var maxISO = (meta.max_date || "");
            var minISO = (meta.min_date || "");
            if (!maxISO) return;

            var maxDt = parseDate(maxISO);
            if (!maxDt) return;

            var toISO = clampDateISO(toISODate(new Date()), minISO, maxISO);
            var toDt = parseDate(toISO) || maxDt;

            var fromDt = new Date(toDt.getTime() - days*24*3600*1000);
            var fromISO = clampDateISO(toISODate(fromDt), minISO, maxISO);

            elFrom.value = fromISO;
            elTo.value = toISO;
            buildLayer();
          }}

          // buttons
          document.getElementById("ucdpApply").addEventListener("click", function(ev) {{
            ev.preventDefault(); ev.stopPropagation();
            buildLayer();
          }});
          document.getElementById("ucdpReset").addEventListener("click", function(ev) {{
            ev.preventDefault(); ev.stopPropagation();

            // reset to smart defaults again
            elMinBest.value = 0;
            elMinCiv.value = 0;
            elMaxPrec.value = 6;
            updatePrecLabel();

            elFrom.value = fromISO;
            elTo.value = toISO;
            buildLayer();
          }});

          // quick pills - CORRECTED SELECTOR HERE
          Array.from(document.querySelectorAll("#ucdpFilter .dock-pill[data-range]")).forEach(function(el) {{
            el.addEventListener("click", function(ev) {{
              ev.preventDefault(); ev.stopPropagation();
              var d = parseInt(el.getAttribute("data-range") || "30", 10);
              setRangeDays(d);
            }});
          }});

          document.getElementById("ucdpAll").addEventListener("click", function(ev) {{
            ev.preventDefault(); ev.stopPropagation();
            elFrom.value = meta.min_date || "";
            elTo.value = meta.max_date || toISODate(new Date());
            buildLayer();
          }});

          // collapse toggle (uniform)
          var panel = document.getElementById("ucdpFilter");
          var btn = document.getElementById("ucdpToggle");

          function applyState(collapsed){{
            panel.classList.toggle("ucdp-collapsed", collapsed);
            btn.textContent = collapsed ? "Show" : "Hide";
            btn.classList.toggle("dock-on", !collapsed);
          }}

          // restore
          try {{
            var st = localStorage.getItem("ucdp_collapsed");
            applyState(st === "1");
          }} catch(e) {{
            applyState(false);
          }}

          btn.addEventListener("click", function(ev){{
            ev.preventDefault(); ev.stopPropagation();
            var collapsed = panel.classList.contains("ucdp-collapsed") ? false : true;
            applyState(collapsed);
            try {{ localStorage.setItem("ucdp_collapsed", collapsed ? "1" : "0"); }} catch(e) {{}}
          }});

          // prevent map stealing clicks while on panel
          panel.addEventListener("mouseenter", function() {{
            try {{
              map.dragging.disable();
              map.scrollWheelZoom.disable();
              map.doubleClickZoom.disable();
              map.boxZoom.disable();
              map.keyboard.disable();
            }} catch(e) {{}}
          }});
          panel.addEventListener("mouseleave", function() {{
            try {{
              map.dragging.enable();
              map.scrollWheelZoom.enable();
              map.doubleClickZoom.enable();
              map.boxZoom.enable();
              map.keyboard.enable();
            }} catch(e) {{}}
          }});

          // build when overlay toggled ON
          map.on('overlayadd', function(e) {{
            if (e.layer === ucdpGroup) {{
              buildLayer();
            }}
          }});

          if (map.hasLayer(ucdpGroup)) buildLayer();
        }}

        waitFor(MAP_NAME, 200, function(map) {{
          waitFor(GROUP_NAME, 200, function(group) {{
            init(map, group);
          }});
        }});
      }})();
    </script>
    """

    m.get_root().html.add_child(folium.Element(html))
    print("UCDP filter panel added (top-right).")

def _fmt_int(n):
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)

def _fmt_billions(x):
    try:
        return f"{float(x):.2f}B"
    except Exception:
        return str(x)

def _pick_top3_categories(categories_dict):
    """
    categories_dict = { "Tanks": { "usd_estimated": ... }, ... }
    Return list of top 3 categories as [(name, usd_estimated), ...].
    """
    rows = []
    for name, obj in (categories_dict or {}).items():
        usd = obj.get("usd_estimated")
        if isinstance(usd, (int, float)):
            rows.append((name, usd))
    rows.sort(key=lambda t: t[1], reverse=True)
    return rows[:3]
def add_stats_panel(m, json_path=None):
    # Prefer processed war stats produced by other scripts. Fall back to legacy names.
    candidates = [os.path.join("data", "processed", "war_stats.json"), "stats_razboi.json", "data/war_stats.json"]

    if json_path is None:
        for p in candidates:
            if os.path.exists(p):
                json_path = p
                break

    if not json_path or not os.path.exists(json_path):
        print(f"Warning: cannot find stats JSON. Tried: {candidates}")
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Warning: cannot read {json_path}: {e}")
        return

    # ---------- timestamps ----------
    stats_ts = data.get("timestamp_utc") or data.get("timestamp") or ""
    try:
        if isinstance(stats_ts, str) and "T" in stats_ts:
            stats_ts_pretty = stats_ts.replace("T", " ").replace("+00:00", " UTC")
        else:
            stats_ts_pretty = str(stats_ts)
    except Exception:
        stats_ts_pretty = str(stats_ts)

    map_ts_pretty = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # ---------- inputs ----------
    ru_p = (data.get("russia") or {}).get("personnel") or {}
    ua_p_dead = (data.get("ukraine") or {}).get("personnel_dead_ualosses")

    ru_eq = (data.get("russia") or {}).get("equipment_oryx") or {}
    ua_eq = (data.get("ukraine") or {}).get("equipment_oryx") or {}

    ru_b = ru_eq.get("total_billion_usd_estimated")
    ua_b = ua_eq.get("total_billion_usd_estimated")

    ru_categories = (ru_eq.get("categories") or {})
    ua_categories = (ua_eq.get("categories") or {})

    # ---------- helpers ----------
    def _pick_top_n_categories(categories_dict, n=10, min_usd=0.0):
        rows = []
        for name, obj in (categories_dict or {}).items():
            usd = None
            if isinstance(obj, dict):
                usd = obj.get("usd_estimated")
            if isinstance(usd, (int, float)) and usd >= float(min_usd):
                rows.append((name, float(usd)))
        rows.sort(key=lambda t: t[1], reverse=True)
        return rows[:n]

    def _count_with_usd(categories_dict, min_usd=0.0):
        c = 0
        for _, obj in (categories_dict or {}).items():
            if isinstance(obj, dict) and isinstance(obj.get("usd_estimated"), (int, float)):
                if float(obj["usd_estimated"]) >= float(min_usd):
                    c += 1
        return c

    def categories_html(categories_dict, limit=10, min_usd=50_000_000):
        rows = _pick_top_n_categories(categories_dict, n=limit, min_usd=min_usd)
        if not rows:
            return "<div style='opacity:.75'>n/a</div>"

        out = []
        for name, usd in rows:
            out.append(
                "<div style='display:flex; justify-content:space-between; gap:10px;'>"
                f"<span style='opacity:.9'>{name}</span>"
                f"<span style='font-weight:800'>${usd/1e9:.2f}B</span>"
                "</div>"
            )

        total_ok = _count_with_usd(categories_dict, min_usd=min_usd)
        remaining = max(0, total_ok - len(rows))
        if remaining > 0:
            out.append(f"<div style='opacity:.7; margin-top:4px;'>+{remaining} more categories</div>")

        return "".join(out)

    # ---------- sources text ----------
    src_ru_p = "UA GenStaff + UA MoD (reported)"
    src_ua_p = "UALosses (documented deaths)"
    src_eq = "Oryx (visually confirmed)"

    ru_casualties = ru_p.get("personnel")
    ua_deaths_doc = ua_p_dead

    MIN_USD_TO_SHOW = 50_000_000
    TOP_LIMIT = 10

    html = f"""
    <style>
      #warstats {{
        pointer-events: auto;
      }}
      #warstats .ws-body {{ display:block; }}
      #warstats.ws-collapsed .ws-body {{ display:none; }}

      /* unified button look (matching the rest of the UI) */
      #warstats .ws-btn {{
        cursor: pointer;
        user-select: none;
        padding: 4px 9px;
        border-radius: 9px;
        border: 1px solid rgba(255,255,255,0.14);
        background: rgba(255,255,255,0.06);
        font-weight: 900;
        font-size: 12px;
        opacity: .92;
        transition: transform 90ms ease, opacity 140ms ease, background 140ms ease, box-shadow 140ms ease;
        box-shadow: 0 6px 16px rgba(0,0,0,0.18);
      }}
      #warstats .ws-btn:hover {{
        opacity: 1;
        background: rgba(255,255,255,0.10);
        box-shadow: 0 10px 22px rgba(0,0,0,0.30);
      }}
      #warstats .ws-btn:active {{
        transform: translateY(1px);
        opacity: .95;
      }}
      #warstats .ws-btn.ws-on {{
        background: rgba(255,255,255,0.12);
        box-shadow: 0 10px 26px rgba(0,0,0,0.35);
      }}
    </style>

    <div id="warstats" style="
        position: fixed;
        top: 18px;
        left: 18px;
        z-index: 999999;
        background: rgba(0,0,0,0.78);
        padding: 12px 14px;
        border: 1px solid rgba(255,255,255,0.16);
        border-radius: 12px;
        color: #EDEDED;
        font-family: Arial, sans-serif;
        font-size: 13px;
        min-width: 290px;
        max-width: 360px;
        box-shadow: 0 10px 28px rgba(0,0,0,0.45);
        backdrop-filter: blur(3px);
    ">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:10px;">
        <div style="font-weight:900; letter-spacing:.3px;">
          War Stats (snapshot)
        </div>
<div class="dock-btn dock-on" id="warstatsToggle" title="Show/Hide">Hide</div>
      </div>

      <div class="ws-body" id="warstatsBody">
        <div style="opacity:.72; font-size:11.5px; line-height:1.25; margin:8px 0 10px 0;">
          <div><span style="opacity:.8;">Stats updated:</span> {stats_ts_pretty}</div>
          <div><span style="opacity:.8;">Map generated:</span> {map_ts_pretty}</div>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
          <div style="border:1px solid rgba(255,255,255,0.10); border-radius:10px; padding:10px;">
            <div style="font-weight:900; color:{COLORS['ru_line']}; margin-bottom:6px;">RU</div>

            <div style="display:flex; justify-content:space-between;">
              <span style="opacity:.85;">Casualties</span>
              <span style="font-weight:900;">{_fmt_int(ru_casualties)}</span>
            </div>
            <div style="opacity:.65; font-size:11px; margin:2px 0 8px 0;">
              Source: {src_ru_p}
            </div>

            <div style="display:flex; justify-content:space-between;">
              <span style="opacity:.85;">Equipment $</span>
              <span style="font-weight:900;">{_fmt_billions(ru_b)}</span>
            </div>
            <div style="opacity:.65; font-size:11px; margin-top:2px;">
              Source: {src_eq}
            </div>
          </div>

          <div style="border:1px solid rgba(255,255,255,0.10); border-radius:10px; padding:10px;">
            <div style="font-weight:900; color:{COLORS['ua_line']}; margin-bottom:6px;">UA</div>

            <div style="display:flex; justify-content:space-between;">
              <span style="opacity:.85;">Deaths</span>
              <span style="font-weight:900;">{_fmt_int(ua_deaths_doc)}</span>
            </div>
            <div style="opacity:.65; font-size:11px; margin:2px 0 8px 0;">
              Source: {src_ua_p}
            </div>

            <div style="display:flex; justify-content:space-between;">
              <span style="opacity:.85;">Equipment $</span>
              <span style="font-weight:900;">{_fmt_billions(ua_b)}</span>
            </div>
            <div style="opacity:.65; font-size:11px; margin-top:2px;">
              Source: {src_eq}
            </div>
          </div>
        </div>

        <div style="margin-top:10px; border-top:1px solid rgba(255,255,255,0.12); padding-top:10px;">
          <div style="font-weight:900; margin-bottom:6px; opacity:.95;">
            Equipment losses by category ($, top)
          </div>
          <div style="opacity:.65; font-size:11px; margin-bottom:8px;">
            Showing categories ≥ ${MIN_USD_TO_SHOW/1e6:.0f}M
          </div>

          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div>
              <div style="font-weight:900; color:{COLORS['ru_line']}; margin-bottom:4px;">RU</div>
              {categories_html(ru_categories, limit=TOP_LIMIT, min_usd=MIN_USD_TO_SHOW)}
            </div>
            <div>
              <div style="font-weight:900; color:{COLORS['ua_line']}; margin-bottom:4px;">UA</div>
              {categories_html(ua_categories, limit=TOP_LIMIT, min_usd=MIN_USD_TO_SHOW)}
            </div>
          </div>

          <div style="margin-top:8px; opacity:.55; font-size:11px; line-height:1.25;">
            Note: RU “casualties” are reported by Ukrainian official sources; UA shown as documented deaths (different methodology).
          </div>
        </div>
      </div>

      <script>
        (function() {{
          var panel = document.getElementById('warstats');
          var btn = document.getElementById('warstatsToggle');
          if (!panel || !btn) return;

          function applyState(collapsed) {{
            panel.classList.toggle('ws-collapsed', collapsed);
            btn.textContent = collapsed ? 'Show' : 'Hide';
            btn.classList.toggle('ws-on', !collapsed);
          }}

          // Restore state
          try {{
            var st = localStorage.getItem('warstats_collapsed');
            applyState(st === '1');
          }} catch(e) {{
            applyState(false);
          }}

          btn.addEventListener('click', function(ev) {{
            ev.preventDefault();
            ev.stopPropagation();

            var collapsed = panel.classList.toggle('ws-collapsed');
            btn.textContent = collapsed ? 'Show' : 'Hide';
            btn.classList.toggle('ws-on', !collapsed);

            try {{
              localStorage.setItem('warstats_collapsed', collapsed ? '1' : '0');
            }} catch(e) {{}}
          }});
        }})();
      </script>
    </div>
    """

    m.get_root().html.add_child(folium.Element(html))
    print("Stats panel added (top-left).")

def build_map():
    if not os.path.exists(KML_FILE):
        print(f"Error: KML file not found: {KML_FILE}")
        return

    tree = ET.parse(KML_FILE)
    root = tree.getroot()

    ns_url = root.tag.split("}")[0].strip("{")
    ns = {"kml": ns_url}
    prefix = f"{{{ns_url}}}"

    # Styles
    style_defs, style_maps = parse_kml_styles(root, ns)

    # Map
    m = folium.Map(location=[48.5, 36.0], zoom_start=6, tiles="CartoDB dark_matter")

    # Layers
    fg_front  = folium.FeatureGroup(name="Frontline", show=True).add_to(m)
    fg_ctrl   = folium.FeatureGroup(name="Control Areas", show=True).add_to(m)
    fg_axis   = folium.FeatureGroup(name="Axes (UA/RU/Historic)", show=True).add_to(m)
    fg_ua     = folium.FeatureGroup(name="UA Units", show=False).add_to(m)
    fg_ru     = folium.FeatureGroup(name="RU Units", show=False).add_to(m)

    # Add Ukraine border (optional)
    # 2) borders (RETURN groups that actually contain shapes)
    fg_ua_border, fg_ru_border = add_borders_from_shp(m)

    # 3) ucdp (RETURN group that actually contains points)
    fg_ucdp, fg_ucdp_var = add_ucdp_events_layer(m, None)

    # Prefer detected stats file (no hard-coded legacy filename)
    add_stats_panel(m)
    if fg_ucdp_var:
        add_ucdp_filter_panel(m, fg_ucdp_var)

    # Register available layers for the legend/controls (only include groups that exist)
    layer_vars = {
        "Frontline": fg_front.get_name(),
        "Control Areas": fg_ctrl.get_name(),
        "Axes (UA/RU/Historic)": fg_axis.get_name(),
        "UA Units": fg_ua.get_name(),
        "RU Units": fg_ru.get_name(),
    }
    if fg_ua_border:
        layer_vars["UA Border"] = fg_ua_border.get_name()
    if fg_ru_border:
        layer_vars["Russia Border"] = fg_ru_border.get_name()
    if fg_ucdp:
        layer_vars["UCDP Events"] = fg_ucdp.get_name()

    add_legend_and_layers(m, COLORS, layer_vars=layer_vars)




    stats = {"ua": 0, "ru": 0, "front": 0, "polys": 0, "axis": 0, "ignored": 0}
    ignored_folder_counts = {}
    ignored_samples = {}  # folder_name -> list of up to 3 sample placemark names

    def add_point(lat, lon, name, conf, target_group, fallback_color):
        icon_path = conf.get("icon") if conf else None
        if icon_path:
            try:
                # If local file exists, use it (assets/images/...)
                if os.path.exists(icon_path):
                    icon_obj = folium.CustomIcon(icon_image=icon_path, icon_size=(22, 22))
                    folium.Marker([lat, lon], icon=icon_obj, popup=name, tooltip=name).add_to(target_group)
                    return
                # Allow external URLs or data URIs
                if isinstance(icon_path, str) and (icon_path.startswith("http") or icon_path.startswith("data:") or icon_path.startswith("/")):
                    icon_obj = folium.CustomIcon(icon_image=icon_path, icon_size=(22, 22))
                    folium.Marker([lat, lon], icon=icon_obj, popup=name, tooltip=name).add_to(target_group)
                    return
            except Exception as e:
                print(f"Warning: failed to load icon {icon_path}: {e}")

        # fallback marker
        folium.CircleMarker(
            location=[lat, lon],
            radius=3,
            weight=1,
            color=fallback_color,
            fill=True,
            fill_opacity=0.9,
            popup=name,
            tooltip=name,
        ).add_to(target_group)

    def add_line(path, target_group, color, weight=2.5, opacity=0.9, dashed=False):
        kwargs = {}
        if dashed:
            kwargs["dash_array"] = "6, 6"
        folium.PolyLine(path, color=color, weight=weight, opacity=opacity, **kwargs).add_to(target_group)

    def add_polygon(path, target_group, border_color, fill_color, fill_opacity, name):
        folium.Polygon(
            locations=path,
            color=border_color,
            weight=2,
            fill=True,
            fill_color=fill_color,
            fill_opacity=fill_opacity,
            popup=name,
            tooltip=name if name else None,
        ).add_to(target_group)

    def process_elements(folder_element, folder_name):
        nonlocal stats

        for pm in folder_element.findall(f"./{prefix}Placemark"):
            name_el = pm.find(f"{prefix}name")
            name = name_el.text.strip() if (name_el is not None and name_el.text) else ""

            style_el = pm.find(f"{prefix}styleUrl")
            style_url = style_el.text.strip() if (style_el is not None and style_el.text) else None
            conf = resolve_style(style_url, style_defs, style_maps) or {}

            kind = classify_feature(folder_name, name)

            # --- POINT ---
            point = pm.find(f".//{prefix}Point/{prefix}coordinates")
            if point is not None and point.text:
                lon, lat, *_ = point.text.strip().split(",")
                lat = float(lat); lon = float(lon)

                # Prefer folder-based for units
                if "ukrainian unit positions" in (folder_name or "").lower() or kind == "ua":
                    add_point(lat, lon, name, conf, fg_ua, fallback_color=COLORS["ua_line"])
                    stats["ua"] += 1
                elif "russian unit positions" in (folder_name or "").lower() or kind == "ru":
                    add_point(lat, lon, name, conf, fg_ru, fallback_color=COLORS["ru_line"])
                    stats["ru"] += 1
                else:
                    add_point(lat, lon, name, conf, fg_axis, fallback_color="#FFAA00")
                continue

            # --- LINESTRING ---
            line = pm.find(f".//{prefix}LineString/{prefix}coordinates")
            if line is not None and line.text:
                raw = line.text.strip().split()
                path = [[float(c.split(",")[1]), float(c.split(",")[0])] for c in raw]

                # Frontline always white
                if "frontline" in (folder_name or "").lower():
                    add_line(path, fg_front, color=COLORS["front"], weight=2.7, opacity=0.95, dashed=False)
                    stats["front"] += 1
                else:
                    # Axes: UA blue dashed, RU red dashed, historic grey dashed
                    if kind == "ua":
                        add_line(path, fg_axis, color=COLORS["ua_line"], weight=2.5, opacity=0.9, dashed=True)
                    elif kind == "historic":
                        add_line(path, fg_axis, color=COLORS["hist_line"], weight=2.3, opacity=0.8, dashed=True)
                    elif kind == "ru":
                        add_line(path, fg_axis, color=COLORS["ru_line"], weight=2.5, opacity=0.9, dashed=True)
                    else:
                        add_line(path, fg_axis, color=COLORS["other_line"], weight=2.2, opacity=0.8, dashed=True)
                    stats["axis"] += 1
                continue

            # --- POLYGON ---
            poly = pm.find(f".//{prefix}Polygon//{prefix}coordinates")
            if poly is not None and poly.text:
                raw = poly.text.strip().split()
                path = [[float(c.split(",")[1]), float(c.split(",")[0])] for c in raw]

                if kind == "ru":
                    border = COLORS["ru_line"]; fill = COLORS["ru_fill"]; opacity = 0.28
                elif kind == "ua":
                    border = COLORS["ua_line"]; fill = COLORS["ua_fill"]; opacity = 0.22
                elif kind == "historic":
                    border = COLORS["hist_line"]; fill = COLORS["hist_fill"]; opacity = 0.18
                else:
                    border = COLORS["other_line"]; fill = COLORS["other_fill"]; opacity = 0.18

                # Polygons always in Control Areas layer
                add_polygon(path, fg_ctrl, border, fill, opacity, name)
                stats["polys"] += 1
                continue

    def process_folder(element):
        nonlocal stats, ignored_folder_counts, ignored_samples

        for folder in element.findall(f"./{prefix}Folder"):
            name_el = folder.find(f"{prefix}name")
            folder_name = name_el.text.strip() if (name_el is not None and name_el.text) else ""

            if is_allowed_folder(folder_name):
                process_elements(folder, folder_name)
            else:
                pls = folder.findall(f".//{prefix}Placemark")
                cnt = len(pls)
                stats["ignored"] += cnt
                if cnt > 0:
                    ignored_folder_counts[folder_name] = ignored_folder_counts.get(folder_name, 0) + cnt
                    if folder_name not in ignored_samples:
                        ignored_samples[folder_name] = []
                    for pm in pls[:3]:
                        n_el = pm.find(f"{prefix}name")
                        nm = n_el.text.strip() if (n_el is not None and n_el.text) else "<no name>"
                        if len(ignored_samples[folder_name]) < 3:
                            ignored_samples[folder_name].append(nm)

            process_folder(folder)

    doc = root.find(f"./{prefix}Document")
    start_node = doc if doc is not None else root
    process_folder(start_node)

    # Ensure images from IMAGES_FOLDER are copied next to the output map (outputs/images/)
    try:
        src_img_dir = IMAGES_FOLDER
        dst_img_dir = os.path.join(os.path.dirname(OUTPUT_MAP), "images")
        if os.path.exists(src_img_dir) and os.path.isdir(src_img_dir):
            os.makedirs(dst_img_dir, exist_ok=True)
            import shutil
            for fname in os.listdir(src_img_dir):
                src = os.path.join(src_img_dir, fname)
                if not os.path.isfile(src):
                    continue
                if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico')):
                    dst = os.path.join(dst_img_dir, fname)
                    try:
                        shutil.copy2(src, dst)
                    except Exception as e:
                        print(f"Warning: failed copying image {src} to {dst}: {e}")
    except Exception as e:
        print(f"Warning: failed to prepare images folder: {e}")

    #folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUTPUT_MAP)

    print("\nDone.")
    print(f"  UA units: {stats['ua']}")
    print(f"  RU units: {stats['ru']}")
    print(f"  Front segments: {stats['front']}")
    print(f"  Polygons (control areas): {stats['polys']}")
    print(f"  Axis lines: {stats['axis']}")
    print(f"  Ignored placemarks (archive/other): {stats['ignored']}")
    print(f"Open: {OUTPUT_MAP}")

if __name__ == "__main__":
    build_map()