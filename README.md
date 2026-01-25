# Ukraine–Russia Conflict Map (KML + UCDP)

## Overview

This repo builds an interactive analytical map by combining:

* Tactical layers extracted from a large KML dataset (frontline, control polygons, unit positions, axes)
* Event-level conflict data from UCDP (Uppsala Conflict Data Program)
* Snapshot statistics (personnel + equipment) from public datasets

The focus is **data engineering + visualization**: parsing, filtering, normalizing, and presenting multiple sources in a single explorable map.

## What’s included

* **`outputs/map.html`**: final interactive map (open locally in a browser)
* **Custom UI panels** inside the map:

  * War stats panel (top-left)
  * UCDP event filters (top-right)
  * Legend + Layers dock (bottom-right)

## Data Inputs

Expected input locations (you can change paths in scripts if needed):

* **KML tactical dataset**

  * `assets/doc.kml`
  * `assets/images/` (icons referenced by KML styles)

* **Borders (optional, for country outlines)**

  * `assets/geo/ne_110m_admin_0_countries.*` (Natural Earth shapefile)

* **UCDP events (processed GeoJSON)**

  * `data/processed/ucdp_live_data.json` (GeoJSON FeatureCollection preferred)

* **War stats snapshot (processed JSON)**

  * `data/processed/war_stats.json`

## Scripts

This project is designed as a small pipeline. Typical structure:

### 00–09: Data gather / prep (optional but recommended)

These scripts produce the processed JSON files used by the map builder.

* **`scripts/00_fetch_ucdp.py`**
  Downloads or refreshes UCDP event data and exports a normalized GeoJSON FeatureCollection.
  Output:

  * `data/processed/ucdp_live_data.json`

* **`scripts/01_build_war_stats.py`**
  Produces the snapshot stats panel dataset (personnel + equipment totals and category breakdown).
  Output:

  * `data/processed/war_stats.json`

> If you already have these outputs, you can skip the gather scripts.

### 10: Build map (main step)

* **`scripts/10_build_map.py`**
  Parses the KML, filters folders, classifies features, builds layers, injects UI, and writes the final HTML.
  Output:

  * `outputs/map.html`

## Pipeline: Quick Start

### 1) Install dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2) Ensure inputs exist

* `assets/doc.kml`
* `assets/images/` (optional icons)
* `assets/geo/ne_110m_admin_0_countries.*` (optional borders)
* `data/processed/ucdp_live_data.json` (UCDP events)
* `data/processed/war_stats.json` (stats panel)

### 3) Build the map

```bash
python scripts/10_build_map.py
```

Open:

* `outputs/map.html`

## Running with Docker

Build:

```bash
docker compose build
```

Run map build:

```bash
docker compose run --rm map python scripts/10_build_map.py
```

Output:

* `outputs/map.html`

## Interactive Features

### Layer system

* Frontline
* Control areas (polygons)
* Axes (UA / RU / historic)
* UA units (markers)
* RU units (markers)
* Borders (UA border shown by default; RU optional)
* UCDP events (toggleable)

### UCDP filter panel (top-right)

* Date range (From / To)
* Min fatalities (Best)
* Min civilian fatalities
* Max location precision (`where_prec`)
* Presets: last 7d / 30d / 90d / all

### Stats panel (top-left)

* Personnel snapshot
* Equipment cost estimate snapshot
* Top loss categories by estimated value

Panels are collapsible and store UI state in `localStorage`.

## Notes on UCDP fields

* **Best**: UCDP “best estimate” for total fatalities for that event record
* **Civ**: estimated civilian fatalities (subset of total when available)
* **where_prec**: location precision code (lower = more precise; higher = more coarse)

## Output

Main artifact:

* `outputs/map.html`

This file is self-contained and can be shared/opened without a backend server.

## Project Goals

* Parse a large hierarchical KML efficiently
* Filter “archive/old” layers automatically
* Normalize multiple datasets into a coherent spatial UI
* Provide fast interactive exploration (layers + event filters)

## License / Disclaimer

This repository is intended for **data processing and visualization**.
All values come from external datasets and may use different methodologies.
Do not treat the map as definitive attribution or ground truth.
