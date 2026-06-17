# app.py
# Streamlit app: English overseas RSS Japan-buzz ranker
# - No article-body scraping
# - Excludes RSS sources previously identified as clearly restricted for commercial/non-commercial use
# - Ranks English RSS items by Japan-specific 1000-term hits in title / URL / metadata
# - Repeated hits of the same term use geometric decay: 1.0, 0.5, 0.25, ...
# - Before global ranking, items in the same RSS category are also decayed: 1st=1.0, 2nd=0.5, 3rd=0.25, ...
# - Japan/Japanese are not terms, but add a capped +0.5 anchor bonus if present in title/URL/RSS metadata

from __future__ import annotations

import csv
import hmac
import math
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
import html
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, quote_plus

import pandas as pd
import requests
import streamlit as st

APP_VERSION = "2026-06-17-rss-japan-buzz-auto-4am-jst"
MAX_RANKING_LIMIT = 1000
DEFAULT_RANKING_LIMIT = MAX_RANKING_LIMIT
FIXED_LOOKBACK_HOURS = 168
FIXED_MAX_ENTRIES_PER_FEED = 100
FIXED_REQUEST_INTERVAL_SEC = 0.2
JST = timezone(timedelta(hours=9))
AUTO_UPDATE_HOUR_JST = 4

# Japan / Japanese are intentionally NOT included here.
# The term list is a stricter all-category top-1000 list of Japan-specific terms; broad generic phrases are removed.
# It is NOT category-ratio balanced. Category is set to "all" for all terms.
# Generic terms such as export controls, inbound tourism and Supreme Court, plus blocked terms, are excluded from scoring and display.
JAPAN_SPECIFIC_TERMS_1000_RECORDS = [{'category': 'all', 'term': 'LDP', 'weight': 1.0, 'id': 1},
 {'category': 'all', 'term': 'Komeito', 'weight': 1.0, 'id': 2},
 {'category': 'all', 'term': 'CDP', 'weight': 1.0, 'id': 3},
 {'category': 'all', 'term': 'Constitutional Democratic Party', 'weight': 1.0, 'id': 4},
 {'category': 'all', 'term': 'DPFP', 'weight': 1.0, 'id': 5},
 {'category': 'all', 'term': 'Ishin', 'weight': 1.0, 'id': 6},
 {'category': 'all', 'term': 'Reiwa Shinsengumi', 'weight': 1.0, 'id': 7},
 {'category': 'all', 'term': 'Sangiin', 'weight': 1.0, 'id': 8},
 {'category': 'all', 'term': 'Shugiin', 'weight': 1.0, 'id': 9},
 {'category': 'all', 'term': 'National Diet', 'weight': 1.0, 'id': 10},
 {'category': 'all', 'term': 'Kantei', 'weight': 1.0, 'id': 11},
 {'category': 'all', 'term': 'Kokkai', 'weight': 1.0, 'id': 12},
 {'category': 'all', 'term': 'Kasumigaseki', 'weight': 1.0, 'id': 13},
 {'category': 'all', 'term': 'Nagatacho', 'weight': 1.0, 'id': 14},
 {'category': 'all', 'term': 'Chief Cabinet Secretary', 'weight': 1.0, 'id': 15},
 {'category': 'all', 'term': 'MOFA', 'weight': 1.0, 'id': 16},
 {'category': 'all', 'term': 'MOF', 'weight': 1.0, 'id': 17},
 {'category': 'all', 'term': 'Ministry of Economy Trade and Industry', 'weight': 1.0, 'id': 18},
 {'category': 'all', 'term': 'METI', 'weight': 1.0, 'id': 19},
 {'category': 'all', 'term': 'MIC', 'weight': 1.0, 'id': 20},
 {'category': 'all', 'term': 'MOJ', 'weight': 1.0, 'id': 21},
 {'category': 'all', 'term': 'Ministry of Health Labour and Welfare', 'weight': 1.0, 'id': 22},
 {'category': 'all', 'term': 'MHLW', 'weight': 1.0, 'id': 23},
 {'category': 'all', 'term': 'Ministry of Education Culture Sports Science and Technology', 'weight': 1.0, 'id': 24},
 {'category': 'all', 'term': 'MEXT', 'weight': 1.0, 'id': 25},
 {'category': 'all', 'term': 'Ministry of Agriculture Forestry and Fisheries', 'weight': 1.0, 'id': 26},
 {'category': 'all', 'term': 'MAFF', 'weight': 1.0, 'id': 27},
 {'category': 'all', 'term': 'Financial Services Agency', 'weight': 1.0, 'id': 28},
 {'category': 'all', 'term': 'FSA', 'weight': 1.0, 'id': 29},
 {'category': 'all', 'term': 'NPA', 'weight': 1.0, 'id': 30},
 {'category': 'all', 'term': 'Public Security Intelligence Agency', 'weight': 1.0, 'id': 31},
 {'category': 'all', 'term': 'PSIA', 'weight': 1.0, 'id': 32},
 {'category': 'all', 'term': 'Imperial Household Agency', 'weight': 1.0, 'id': 33},
 {'category': 'all', 'term': 'Tokyo District Court', 'weight': 1.0, 'id': 34},
 {'category': 'all', 'term': 'Tokyo High Court', 'weight': 1.0, 'id': 35},
 {'category': 'all', 'term': 'Special Investigation Department', 'weight': 1.0, 'id': 36},
 {'category': 'all', 'term': 'single-seat districts', 'weight': 1.0, 'id': 37},
 {'category': 'all', 'term': 'no-confidence motion', 'weight': 1.0, 'id': 38},
 {'category': 'all', 'term': 'Tokyo governor', 'weight': 1.0, 'id': 39},
 {'category': 'all', 'term': 'Osaka governor', 'weight': 1.0, 'id': 40},
 {'category': 'all', 'term': 'Okinawa governor', 'weight': 1.0, 'id': 41},
 {'category': 'all', 'term': 'Hokkaido governor', 'weight': 1.0, 'id': 42},
 {'category': 'all', 'term': 'constitutional revision', 'weight': 1.0, 'id': 43},
 {'category': 'all', 'term': 'Article 9', 'weight': 1.0, 'id': 44},
 {'category': 'all', 'term': 'pacifist constitution', 'weight': 1.0, 'id': 45},
 {'category': 'all', 'term': 'anti-conspiracy law', 'weight': 1.0, 'id': 46},
 {'category': 'all', 'term': 'My Number', 'weight': 1.0, 'id': 47},
 {'category': 'all', 'term': 'My Number card', 'weight': 1.0, 'id': 48},
 {'category': 'all', 'term': 'koseki', 'weight': 1.0, 'id': 49},
 {'category': 'all', 'term': 'family registry', 'weight': 1.0, 'id': 50},
 {'category': 'all', 'term': 'technical intern', 'weight': 1.0, 'id': 51},
 {'category': 'all', 'term': 'specified skilled worker', 'weight': 1.0, 'id': 52},
 {'category': 'all', 'term': 'labor standards office', 'weight': 1.0, 'id': 53},
 {'category': 'all', 'term': 'Consumer Affairs Agency', 'weight': 1.0, 'id': 54},
 {'category': 'all', 'term': 'Fair Trade Commission', 'weight': 1.0, 'id': 55},
 {'category': 'all', 'term': 'JFTC', 'weight': 1.0, 'id': 56},
 {'category': 'all', 'term': 'Personal Information Protection Commission', 'weight': 1.0, 'id': 57},
 {'category': 'all', 'term': 'Digital Agency', 'weight': 1.0, 'id': 58},
 {'category': 'all', 'term': 'Reconstruction Agency', 'weight': 1.0, 'id': 59},
 {'category': 'all', 'term': 'Children and Families Agency', 'weight': 1.0, 'id': 60},
 {'category': 'all', 'term': 'Nuclear Regulation Authority', 'weight': 1.0, 'id': 61},
 {'category': 'all', 'term': 'NRA', 'weight': 1.0, 'id': 62},
 {'category': 'all', 'term': 'Agency for Cultural Affairs', 'weight': 1.0, 'id': 63},
 {'category': 'all', 'term': 'Basic Resident Register', 'weight': 1.0, 'id': 64},
 {'category': 'all', 'term': 'yen', 'weight': 1.0, 'id': 65},
 {'category': 'all', 'term': 'BOJ', 'weight': 1.0, 'id': 66},
 {'category': 'all', 'term': 'Nikkei', 'weight': 1.0, 'id': 67},
 {'category': 'all', 'term': 'TOPIX', 'weight': 1.0, 'id': 68},
 {'category': 'all', 'term': 'TSE', 'weight': 1.0, 'id': 69},
 {'category': 'all', 'term': 'Tokyo Stock Exchange', 'weight': 1.0, 'id': 70},
 {'category': 'all', 'term': 'JGB', 'weight': 1.0, 'id': 71},
 {'category': 'all', 'term': 'Tankan', 'weight': 1.0, 'id': 72},
 {'category': 'all', 'term': 'yield curve control', 'weight': 1.0, 'id': 73},
 {'category': 'all', 'term': 'negative interest rates', 'weight': 1.0, 'id': 74},
 {'category': 'all', 'term': 'ultra-low rates', 'weight': 1.0, 'id': 75},
 {'category': 'all', 'term': 'yen intervention', 'weight': 1.0, 'id': 76},
 {'category': 'all', 'term': 'yen carry trade', 'weight': 1.0, 'id': 77},
 {'category': 'all', 'term': 'shunto', 'weight': 1.0, 'id': 78},
 {'category': 'all', 'term': 'spring wage negotiations', 'weight': 1.0, 'id': 79},
 {'category': 'all', 'term': 'long-term care insurance', 'weight': 1.0, 'id': 80},
 {'category': 'all', 'term': 'tuition-free program', 'weight': 1.0, 'id': 81},
 {'category': 'all', 'term': 'daycare shortage', 'weight': 1.0, 'id': 82},
 {'category': 'all', 'term': 'waiting list children', 'weight': 1.0, 'id': 83},
 {'category': 'all', 'term': 'aging population', 'weight': 1.0, 'id': 84},
 {'category': 'all', 'term': 'super-aged society', 'weight': 1.0, 'id': 85},
 {'category': 'all', 'term': 'depopulation', 'weight': 1.0, 'id': 86},
 {'category': 'all', 'term': 'overtime cap', 'weight': 1.0, 'id': 87},
 {'category': 'all', 'term': 'karoshi', 'weight': 1.0, 'id': 88},
 {'category': 'all', 'term': 'death from overwork', 'weight': 1.0, 'id': 89},
 {'category': 'all', 'term': 'power harassment', 'weight': 1.0, 'id': 90},
 {'category': 'all', 'term': 'maternity harassment', 'weight': 1.0, 'id': 91},
 {'category': 'all', 'term': 'black company', 'weight': 1.0, 'id': 92},
 {'category': 'all', 'term': 'salaryman', 'weight': 1.0, 'id': 93},
 {'category': 'all', 'term': 'freeter', 'weight': 1.0, 'id': 94},
 {'category': 'all', 'term': 'parasite single', 'weight': 1.0, 'id': 95},
 {'category': 'all', 'term': 'hikikomori', 'weight': 1.0, 'id': 96},
 {'category': 'all', 'term': 'kodokushi', 'weight': 1.0, 'id': 97},
 {'category': 'all', 'term': 'lonely death', 'weight': 1.0, 'id': 98},
 {'category': 'all', 'term': 'single-person households', 'weight': 1.0, 'id': 99},
 {'category': 'all', 'term': 'akiya', 'weight': 1.0, 'id': 100},
 {'category': 'all', 'term': 'furusato tax', 'weight': 1.0, 'id': 101},
 {'category': 'all', 'term': 'hometown tax', 'weight': 1.0, 'id': 102},
 {'category': 'all', 'term': 'overtourism', 'weight': 1.0, 'id': 103},
 {'category': 'all', 'term': 'point economy', 'weight': 1.0, 'id': 104},
 {'category': 'all', 'term': '2024 logistics problem', 'weight': 1.0, 'id': 105},
 {'category': 'all', 'term': 'food self-sufficiency', 'weight': 1.0, 'id': 106},
 {'category': 'all', 'term': 'renewable surcharge', 'weight': 1.0, 'id': 107},
 {'category': 'all', 'term': 'solar panel rules', 'weight': 1.0, 'id': 108},
 {'category': 'all', 'term': 'ammonia co-firing', 'weight': 1.0, 'id': 109},
 {'category': 'all', 'term': 'economic security law', 'weight': 1.0, 'id': 110},
 {'category': 'all', 'term': 'Tokyo condo market', 'weight': 1.0, 'id': 111},
 {'category': 'all', 'term': 'urban redevelopment', 'weight': 1.0, 'id': 112},
 {'category': 'all', 'term': 'SDF', 'weight': 1.0, 'id': 113},
 {'category': 'all', 'term': 'JSDF', 'weight': 1.0, 'id': 114},
 {'category': 'all', 'term': 'Self-Defense Forces', 'weight': 1.0, 'id': 115},
 {'category': 'all', 'term': 'JASDF', 'weight': 1.0, 'id': 116},
 {'category': 'all', 'term': 'JGSDF', 'weight': 1.0, 'id': 117},
 {'category': 'all', 'term': 'JMSDF', 'weight': 1.0, 'id': 118},
 {'category': 'all', 'term': 'Maritime Self-Defense Force', 'weight': 1.0, 'id': 119},
 {'category': 'all', 'term': 'Air Self-Defense Force', 'weight': 1.0, 'id': 120},
 {'category': 'all', 'term': 'Ground Self-Defense Force', 'weight': 1.0, 'id': 121},
 {'category': 'all', 'term': 'Aegis destroyer', 'weight': 1.0, 'id': 122},
 {'category': 'all', 'term': 'Aegis Ashore', 'weight': 1.0, 'id': 123},
 {'category': 'all', 'term': 'Tomahawk missiles', 'weight': 1.0, 'id': 124},
 {'category': 'all', 'term': 'counterstrike capability', 'weight': 1.0, 'id': 125},
 {'category': 'all', 'term': 'defense buildup', 'weight': 1.0, 'id': 126},
 {'category': 'all', 'term': 'US Forces in Okinawa', 'weight': 1.0, 'id': 127},
 {'category': 'all', 'term': 'Okinawa bases', 'weight': 1.0, 'id': 128},
 {'category': 'all', 'term': 'Futenma', 'weight': 1.0, 'id': 129},
 {'category': 'all', 'term': 'Henoko', 'weight': 1.0, 'id': 130},
 {'category': 'all', 'term': 'Kadena', 'weight': 1.0, 'id': 131},
 {'category': 'all', 'term': 'Yokosuka', 'weight': 1.0, 'id': 132},
 {'category': 'all', 'term': 'Sasebo', 'weight': 1.0, 'id': 133},
 {'category': 'all', 'term': 'Misawa Air Base', 'weight': 1.0, 'id': 134},
 {'category': 'all', 'term': 'Iwakuni', 'weight': 1.0, 'id': 135},
 {'category': 'all', 'term': 'Camp Schwab', 'weight': 1.0, 'id': 136},
 {'category': 'all', 'term': 'Camp Hansen', 'weight': 1.0, 'id': 137},
 {'category': 'all', 'term': 'Yokota Air Base', 'weight': 1.0, 'id': 138},
 {'category': 'all', 'term': 'SOFA', 'weight': 1.0, 'id': 139},
 {'category': 'all', 'term': 'Senkaku', 'weight': 1.0, 'id': 140},
 {'category': 'all', 'term': 'Diaoyu dispute', 'weight': 1.0, 'id': 141},
 {'category': 'all', 'term': 'Sea of Okhotsk', 'weight': 1.0, 'id': 142},
 {'category': 'all', 'term': 'Northern Territories', 'weight': 1.0, 'id': 143},
 {'category': 'all', 'term': 'Kuril Islands dispute', 'weight': 1.0, 'id': 144},
 {'category': 'all', 'term': 'Takeshima', 'weight': 1.0, 'id': 145},
 {'category': 'all', 'term': 'Dokdo dispute', 'weight': 1.0, 'id': 146},
 {'category': 'all', 'term': 'comfort women issue', 'weight': 1.0, 'id': 147},
 {'category': 'all', 'term': 'wartime labor issue', 'weight': 1.0, 'id': 148},
 {'category': 'all', 'term': 'history textbook issue', 'weight': 1.0, 'id': 149},
 {'category': 'all', 'term': 'Yasukuni', 'weight': 1.0, 'id': 150},
 {'category': 'all', 'term': 'Yasukuni Shrine', 'weight': 1.0, 'id': 151},
 {'category': 'all', 'term': 'Nanjing dispute', 'weight': 1.0, 'id': 152},
 {'category': 'all', 'term': 'abduction issue', 'weight': 1.0, 'id': 153},
 {'category': 'all', 'term': 'North Korean abductions', 'weight': 1.0, 'id': 154},
 {'category': 'all', 'term': 'missile alert', 'weight': 1.0, 'id': 155},
 {'category': 'all', 'term': 'J-Alert', 'weight': 1.0, 'id': 156},
 {'category': 'all', 'term': 'air defense identification zone', 'weight': 1.0, 'id': 157},
 {'category': 'all', 'term': 'Quad', 'weight': 1.0, 'id': 158},
 {'category': 'all', 'term': 'FOIP', 'weight': 1.0, 'id': 159},
 {'category': 'all', 'term': 'Free and Open Indo-Pacific', 'weight': 1.0, 'id': 160},
 {'category': 'all', 'term': 'Indo-Pacific strategy', 'weight': 1.0, 'id': 161},
 {'category': 'all', 'term': 'AUKUS cooperation', 'weight': 1.0, 'id': 162},
 {'category': 'all', 'term': 'G7 Hiroshima', 'weight': 1.0, 'id': 163},
 {'category': 'all', 'term': 'Hiroshima summit', 'weight': 1.0, 'id': 164},
 {'category': 'all', 'term': 'CPTPP', 'weight': 1.0, 'id': 165},
 {'category': 'all', 'term': 'RCEP', 'weight': 1.0, 'id': 166},
 {'category': 'all', 'term': 'IPEF', 'weight': 1.0, 'id': 167},
 {'category': 'all', 'term': 'ODA charter', 'weight': 1.0, 'id': 168},
 {'category': 'all', 'term': 'PKO', 'weight': 1.0, 'id': 169},
 {'category': 'all', 'term': 'whaling dispute', 'weight': 1.0, 'id': 170},
 {'category': 'all', 'term': 'IWC withdrawal', 'weight': 1.0, 'id': 171},
 {'category': 'all', 'term': 'bluefin tuna quotas', 'weight': 1.0, 'id': 172},
 {'category': 'all', 'term': 'fisheries dispute', 'weight': 1.0, 'id': 173},
 {'category': 'all', 'term': 'treated water release', 'weight': 1.0, 'id': 174},
 {'category': 'all', 'term': 'ALPS treated water', 'weight': 1.0, 'id': 175},
 {'category': 'all', 'term': 'Fukushima water release', 'weight': 1.0, 'id': 176},
 {'category': 'all', 'term': 'IAEA review', 'weight': 1.0, 'id': 177},
 {'category': 'all', 'term': 'export whitelist', 'weight': 1.0, 'id': 178},
 {'category': 'all', 'term': '2+2 talks', 'weight': 1.0, 'id': 179},
 {'category': 'all', 'term': 'Shangri-La Dialogue', 'weight': 1.0, 'id': 180},
 {'category': 'all', 'term': 'Fukushima Daiichi', 'weight': 1.0, 'id': 181},
 {'category': 'all', 'term': 'Fukushima nuclear plant', 'weight': 1.0, 'id': 182},
 {'category': 'all', 'term': 'Fukushima meltdown', 'weight': 1.0, 'id': 183},
 {'category': 'all', 'term': 'TEPCO', 'weight': 1.0, 'id': 184},
 {'category': 'all', 'term': 'Nankai Trough', 'weight': 1.0, 'id': 185},
 {'category': 'all', 'term': 'Kanto quake', 'weight': 1.0, 'id': 186},
 {'category': 'all', 'term': 'Great East Earthquake', 'weight': 1.0, 'id': 187},
 {'category': 'all', 'term': 'Tohoku quake', 'weight': 1.0, 'id': 188},
 {'category': 'all', 'term': 'Kobe quake', 'weight': 1.0, 'id': 189},
 {'category': 'all', 'term': 'Kumamoto quake', 'weight': 1.0, 'id': 190},
 {'category': 'all', 'term': 'Noto Peninsula quake', 'weight': 1.0, 'id': 191},
 {'category': 'all', 'term': 'earthquake early warning', 'weight': 1.0, 'id': 192},
 {'category': 'all', 'term': 'seismic intensity', 'weight': 1.0, 'id': 193},
 {'category': 'all', 'term': 'shindo', 'weight': 1.0, 'id': 194},
 {'category': 'all', 'term': 'tsunami warning', 'weight': 1.0, 'id': 195},
 {'category': 'all', 'term': 'tsunami advisory', 'weight': 1.0, 'id': 196},
 {'category': 'all', 'term': 'evacuation order', 'weight': 1.0, 'id': 197},
 {'category': 'all', 'term': 'Mount Aso', 'weight': 1.0, 'id': 198},
 {'category': 'all', 'term': 'Sakurajima', 'weight': 1.0, 'id': 199},
 {'category': 'all', 'term': 'Mount Ontake', 'weight': 1.0, 'id': 200},
 {'category': 'all', 'term': 'Mount Unzen', 'weight': 1.0, 'id': 201},
 {'category': 'all', 'term': 'Kirishima', 'weight': 1.0, 'id': 202},
 {'category': 'all', 'term': 'Kilauea no', 'weight': 1.0, 'id': 203},
 {'category': 'all', 'term': 'linear rainband', 'weight': 1.0, 'id': 204},
 {'category': 'all', 'term': 'heatstroke alert', 'weight': 1.0, 'id': 205},
 {'category': 'all', 'term': 'disaster shelters', 'weight': 1.0, 'id': 206},
 {'category': 'all', 'term': 'temporary housing', 'weight': 1.0, 'id': 207},
 {'category': 'all', 'term': 'seawall', 'weight': 1.0, 'id': 208},
 {'category': 'all', 'term': 'nuclear evacuation zone', 'weight': 1.0, 'id': 209},
 {'category': 'all', 'term': 'radiation monitoring', 'weight': 1.0, 'id': 210},
 {'category': 'all', 'term': 'decontamination work', 'weight': 1.0, 'id': 211},
 {'category': 'all', 'term': 'contaminated soil', 'weight': 1.0, 'id': 212},
 {'category': 'all', 'term': 'reconstruction bonds', 'weight': 1.0, 'id': 213},
 {'category': 'all', 'term': 'resilience planning', 'weight': 1.0, 'id': 214},
 {'category': 'all', 'term': 'bullet train suspension', 'weight': 1.0, 'id': 215},
 {'category': 'all', 'term': 'Shinkansen disruption', 'weight': 1.0, 'id': 216},
 {'category': 'all', 'term': 'railway timetable', 'weight': 1.0, 'id': 217},
 {'category': 'all', 'term': 'subway attack', 'weight': 1.0, 'id': 218},
 {'category': 'all', 'term': 'Sarin attack', 'weight': 1.0, 'id': 219},
 {'category': 'all', 'term': 'Aum Shinrikyo', 'weight': 1.0, 'id': 220},
 {'category': 'all', 'term': 'Aleph group', 'weight': 1.0, 'id': 221},
 {'category': 'all', 'term': 'Shinkansen', 'weight': 1.2, 'id': 222},
 {'category': 'all', 'term': 'Kobayashi red yeast rice', 'weight': 1.0, 'id': 223},
 {'category': 'all', 'term': 'beni koji scandal', 'weight': 1.0, 'id': 224},
 {'category': 'all', 'term': 'swine fever', 'weight': 1.0, 'id': 225},
 {'category': 'all', 'term': 'wild boar damage', 'weight': 1.0, 'id': 226},
 {'category': 'all', 'term': 'crowd crush', 'weight': 1.0, 'id': 227},
 {'category': 'all', 'term': 'school lunch issue', 'weight': 1.0, 'id': 228},
 {'category': 'all', 'term': 'unexploded ordnance', 'weight': 1.0, 'id': 229},
 {'category': 'all', 'term': 'ordnance disposal', 'weight': 1.0, 'id': 230},
 {'category': 'all', 'term': 'JAXA', 'weight': 1.0, 'id': 231},
 {'category': 'all', 'term': 'H3 rocket', 'weight': 1.0, 'id': 232},
 {'category': 'all', 'term': 'H2A rocket', 'weight': 1.0, 'id': 233},
 {'category': 'all', 'term': 'SLIM moon lander', 'weight': 1.0, 'id': 234},
 {'category': 'all', 'term': 'Hayabusa2', 'weight': 1.0, 'id': 235},
 {'category': 'all', 'term': 'Michi no Eki', 'weight': 1.0, 'id': 236},
 {'category': 'all', 'term': 'Osaka Expo', 'weight': 1.0, 'id': 237},
 {'category': 'all', 'term': 'World Expo 2025', 'weight': 1.0, 'id': 238},
 {'category': 'all', 'term': 'integrated resort', 'weight': 1.0, 'id': 239},
 {'category': 'all', 'term': 'IR project', 'weight': 1.0, 'id': 240},
 {'category': 'all', 'term': 'Linear Chuo Shinkansen', 'weight': 1.0, 'id': 241},
 {'category': 'all', 'term': 'Chuo Shinkansen', 'weight': 1.0, 'id': 242},
 {'category': 'all', 'term': 'Tokyo Bay redevelopment', 'weight': 1.0, 'id': 243},
 {'category': 'all', 'term': 'Tsukiji redevelopment', 'weight': 1.0, 'id': 244},
 {'category': 'all', 'term': 'Toyosu market', 'weight': 1.0, 'id': 245},
 {'category': 'all', 'term': 'fish market auction', 'weight': 1.0, 'id': 246},
 {'category': 'all', 'term': 'bear culling', 'weight': 1.0, 'id': 247},
 {'category': 'all', 'term': 'hay fever season', 'weight': 1.0, 'id': 248},
 {'category': 'all', 'term': 'kafunsho', 'weight': 1.0, 'id': 249},
 {'category': 'all', 'term': 'Hokkaido', 'weight': 1.0, 'id': 250},
 {'category': 'all', 'term': 'Aomori', 'weight': 1.0, 'id': 251},
 {'category': 'all', 'term': 'Iwate', 'weight': 1.0, 'id': 252},
 {'category': 'all', 'term': 'Miyagi', 'weight': 1.0, 'id': 253},
 {'category': 'all', 'term': 'Akita', 'weight': 1.0, 'id': 254},
 {'category': 'all', 'term': 'Yamagata', 'weight': 1.0, 'id': 255},
 {'category': 'all', 'term': 'Fukushima', 'weight': 1.0, 'id': 256},
 {'category': 'all', 'term': 'Ibaraki', 'weight': 1.0, 'id': 257},
 {'category': 'all', 'term': 'Tochigi', 'weight': 1.0, 'id': 258},
 {'category': 'all', 'term': 'Gunma', 'weight': 1.0, 'id': 259},
 {'category': 'all', 'term': 'Saitama', 'weight': 1.0, 'id': 260},
 {'category': 'all', 'term': 'Chiba', 'weight': 1.0, 'id': 261},
 {'category': 'all', 'term': 'Tokyo', 'weight': 1.0, 'id': 262},
 {'category': 'all', 'term': 'Kanagawa', 'weight': 1.0, 'id': 263},
 {'category': 'all', 'term': 'Niigata', 'weight': 1.0, 'id': 264},
 {'category': 'all', 'term': 'Toyama', 'weight': 1.0, 'id': 265},
 {'category': 'all', 'term': 'Ishikawa', 'weight': 1.0, 'id': 266},
 {'category': 'all', 'term': 'Fukui', 'weight': 1.0, 'id': 267},
 {'category': 'all', 'term': 'Yamanashi', 'weight': 1.0, 'id': 268},
 {'category': 'all', 'term': 'Nagano', 'weight': 1.0, 'id': 269},
 {'category': 'all', 'term': 'Gifu', 'weight': 1.0, 'id': 270},
 {'category': 'all', 'term': 'Shizuoka', 'weight': 1.0, 'id': 271},
 {'category': 'all', 'term': 'Aichi', 'weight': 1.0, 'id': 272},
 {'category': 'all', 'term': 'Mie', 'weight': 1.0, 'id': 273},
 {'category': 'all', 'term': 'Shiga', 'weight': 1.0, 'id': 274},
 {'category': 'all', 'term': 'Kyoto', 'weight': 1.0, 'id': 275},
 {'category': 'all', 'term': 'Osaka', 'weight': 1.0, 'id': 276},
 {'category': 'all', 'term': 'Hyogo', 'weight': 1.0, 'id': 277},
 {'category': 'all', 'term': 'Nara', 'weight': 1.0, 'id': 278},
 {'category': 'all', 'term': 'Wakayama', 'weight': 1.0, 'id': 279},
 {'category': 'all', 'term': 'Tottori', 'weight': 1.0, 'id': 280},
 {'category': 'all', 'term': 'Shimane', 'weight': 1.0, 'id': 281},
 {'category': 'all', 'term': 'Okayama', 'weight': 1.0, 'id': 282},
 {'category': 'all', 'term': 'Hiroshima', 'weight': 1.0, 'id': 283},
 {'category': 'all', 'term': 'Yamaguchi', 'weight': 1.0, 'id': 284},
 {'category': 'all', 'term': 'Tokushima', 'weight': 1.0, 'id': 285},
 {'category': 'all', 'term': 'Kagawa', 'weight': 1.0, 'id': 286},
 {'category': 'all', 'term': 'Ehime', 'weight': 1.0, 'id': 287},
 {'category': 'all', 'term': 'Kochi', 'weight': 1.0, 'id': 288},
 {'category': 'all', 'term': 'Fukuoka', 'weight': 1.0, 'id': 289},
 {'category': 'all', 'term': 'Saga', 'weight': 1.0, 'id': 290},
 {'category': 'all', 'term': 'Nagasaki', 'weight': 1.0, 'id': 291},
 {'category': 'all', 'term': 'Kumamoto', 'weight': 1.0, 'id': 292},
 {'category': 'all', 'term': 'Oita', 'weight': 1.0, 'id': 293},
 {'category': 'all', 'term': 'Miyazaki', 'weight': 1.0, 'id': 294},
 {'category': 'all', 'term': 'Kagoshima', 'weight': 1.0, 'id': 295},
 {'category': 'all', 'term': 'Okinawa', 'weight': 1.0, 'id': 296},
 {'category': 'all', 'term': 'Sapporo', 'weight': 1.0, 'id': 297},
 {'category': 'all', 'term': 'Sendai', 'weight': 1.0, 'id': 298},
 {'category': 'all', 'term': 'Yokohama', 'weight': 1.0, 'id': 299},
 {'category': 'all', 'term': 'Kawasaki city', 'weight': 1.0, 'id': 300},
 {'category': 'all', 'term': 'Saitama city', 'weight': 1.0, 'id': 301},
 {'category': 'all', 'term': 'Chiba city', 'weight': 1.0, 'id': 302},
 {'category': 'all', 'term': 'Nagoya', 'weight': 1.0, 'id': 303},
 {'category': 'all', 'term': 'Kobe', 'weight': 1.0, 'id': 304},
 {'category': 'all', 'term': 'Kitakyushu', 'weight': 1.0, 'id': 305},
 {'category': 'all', 'term': 'Sakai', 'weight': 1.0, 'id': 306},
 {'category': 'all', 'term': 'Hamamatsu', 'weight': 1.0, 'id': 307},
 {'category': 'all', 'term': 'Niigata city', 'weight': 1.0, 'id': 308},
 {'category': 'all', 'term': 'Okayama city', 'weight': 1.0, 'id': 309},
 {'category': 'all', 'term': 'Kumamoto city', 'weight': 1.0, 'id': 310},
 {'category': 'all', 'term': 'Sagamihara', 'weight': 1.0, 'id': 311},
 {'category': 'all', 'term': 'Shizuoka city', 'weight': 1.0, 'id': 312},
 {'category': 'all', 'term': 'Hachioji', 'weight': 1.0, 'id': 313},
 {'category': 'all', 'term': 'Himeji', 'weight': 1.0, 'id': 314},
 {'category': 'all', 'term': 'Naha', 'weight': 1.0, 'id': 315},
 {'category': 'all', 'term': 'Utsunomiya', 'weight': 1.0, 'id': 316},
 {'category': 'all', 'term': 'Maebashi', 'weight': 1.0, 'id': 317},
 {'category': 'all', 'term': 'Mito', 'weight': 1.0, 'id': 318},
 {'category': 'all', 'term': 'Kanazawa', 'weight': 1.0, 'id': 319},
 {'category': 'all', 'term': 'Toyama city', 'weight': 1.0, 'id': 320},
 {'category': 'all', 'term': 'Fukui city', 'weight': 1.0, 'id': 321},
 {'category': 'all', 'term': 'Kofu', 'weight': 1.0, 'id': 322},
 {'category': 'all', 'term': 'Matsumoto', 'weight': 1.0, 'id': 323},
 {'category': 'all', 'term': 'Gifu city', 'weight': 1.0, 'id': 324},
 {'category': 'all', 'term': 'Tsu', 'weight': 1.0, 'id': 325},
 {'category': 'all', 'term': 'Otsu', 'weight': 1.0, 'id': 326},
 {'category': 'all', 'term': 'Wakayama city', 'weight': 1.0, 'id': 327},
 {'category': 'all', 'term': 'Matsue', 'weight': 1.0, 'id': 328},
 {'category': 'all', 'term': 'Takamatsu', 'weight': 1.0, 'id': 329},
 {'category': 'all', 'term': 'Matsuyama', 'weight': 1.0, 'id': 330},
 {'category': 'all', 'term': 'Kochi city', 'weight': 1.0, 'id': 331},
 {'category': 'all', 'term': 'Nagasaki city', 'weight': 1.0, 'id': 332},
 {'category': 'all', 'term': 'Miyazaki city', 'weight': 1.0, 'id': 333},
 {'category': 'all', 'term': 'Kagoshima city', 'weight': 1.0, 'id': 334},
 {'category': 'all', 'term': 'Aomori city', 'weight': 1.0, 'id': 335},
 {'category': 'all', 'term': 'Morioka', 'weight': 1.0, 'id': 336},
 {'category': 'all', 'term': 'Akita city', 'weight': 1.0, 'id': 337},
 {'category': 'all', 'term': 'Yamagata city', 'weight': 1.0, 'id': 338},
 {'category': 'all', 'term': 'Fukushima city', 'weight': 1.0, 'id': 339},
 {'category': 'all', 'term': 'Noto Peninsula', 'weight': 1.0, 'id': 340},
 {'category': 'all', 'term': 'Tohoku', 'weight': 1.0, 'id': 341},
 {'category': 'all', 'term': 'Kanto', 'weight': 1.0, 'id': 342},
 {'category': 'all', 'term': 'Chubu', 'weight': 1.0, 'id': 343},
 {'category': 'all', 'term': 'Kansai', 'weight': 1.0, 'id': 344},
 {'category': 'all', 'term': 'Chugoku region', 'weight': 1.0, 'id': 345},
 {'category': 'all', 'term': 'Shikoku', 'weight': 1.0, 'id': 346},
 {'category': 'all', 'term': 'Kyushu', 'weight': 1.0, 'id': 347},
 {'category': 'all', 'term': 'Setouchi', 'weight': 1.0, 'id': 348},
 {'category': 'all', 'term': 'Sanriku', 'weight': 1.0, 'id': 349},
 {'category': 'all', 'term': 'Boso Peninsula', 'weight': 1.0, 'id': 350},
 {'category': 'all', 'term': 'Izu Peninsula', 'weight': 1.0, 'id': 351},
 {'category': 'all', 'term': 'Kii Peninsula', 'weight': 1.0, 'id': 352},
 {'category': 'all', 'term': 'Tsugaru Strait', 'weight': 1.0, 'id': 353},
 {'category': 'all', 'term': 'Seto Inland Sea', 'weight': 1.0, 'id': 354},
 {'category': 'all', 'term': 'Ogasawara Islands', 'weight': 1.0, 'id': 355},
 {'category': 'all', 'term': 'Amami Oshima', 'weight': 1.0, 'id': 356},
 {'category': 'all', 'term': 'Miyakojima', 'weight': 1.0, 'id': 357},
 {'category': 'all', 'term': 'Ishigaki', 'weight': 1.0, 'id': 358},
 {'category': 'all', 'term': 'Yaeyama', 'weight': 1.0, 'id': 359},
 {'category': 'all', 'term': 'Iriomote', 'weight': 1.0, 'id': 360},
 {'category': 'all', 'term': 'Tsushima', 'weight': 1.0, 'id': 361},
 {'category': 'all', 'term': 'Sado Island', 'weight': 1.0, 'id': 362},
 {'category': 'all', 'term': 'Awaji Island', 'weight': 1.0, 'id': 363},
 {'category': 'all', 'term': 'Oki Islands', 'weight': 1.0, 'id': 364},
 {'category': 'all', 'term': 'Rishiri', 'weight': 1.0, 'id': 365},
 {'category': 'all', 'term': 'Rebun', 'weight': 1.0, 'id': 366},
 {'category': 'all', 'term': 'Shiretoko', 'weight': 1.0, 'id': 367},
 {'category': 'all', 'term': 'Lake Biwa', 'weight': 1.0, 'id': 368},
 {'category': 'all', 'term': 'Toyota', 'weight': 1.1, 'id': 369},
 {'category': 'all', 'term': 'Honda', 'weight': 1.1, 'id': 370},
 {'category': 'all', 'term': 'Nissan', 'weight': 1.1, 'id': 371},
 {'category': 'all', 'term': 'Mazda', 'weight': 1.1, 'id': 372},
 {'category': 'all', 'term': 'Subaru', 'weight': 1.1, 'id': 373},
 {'category': 'all', 'term': 'Suzuki', 'weight': 1.1, 'id': 374},
 {'category': 'all', 'term': 'Mitsubishi Motors', 'weight': 1.1, 'id': 375},
 {'category': 'all', 'term': 'Lexus', 'weight': 1.1, 'id': 376},
 {'category': 'all', 'term': 'Daihatsu', 'weight': 1.1, 'id': 377},
 {'category': 'all', 'term': 'Hino Motors', 'weight': 1.1, 'id': 378},
 {'category': 'all', 'term': 'Yamaha Motor', 'weight': 1.1, 'id': 379},
 {'category': 'all', 'term': 'Kawasaki Heavy Industries', 'weight': 1.1, 'id': 380},
 {'category': 'all', 'term': 'Isuzu', 'weight': 1.1, 'id': 381},
 {'category': 'all', 'term': 'Bridgestone', 'weight': 1.1, 'id': 382},
 {'category': 'all', 'term': 'Yokohama Rubber', 'weight': 1.1, 'id': 383},
 {'category': 'all', 'term': 'Sumitomo Rubber', 'weight': 1.1, 'id': 384},
 {'category': 'all', 'term': 'Aisin', 'weight': 1.1, 'id': 385},
 {'category': 'all', 'term': 'Denso', 'weight': 1.1, 'id': 386},
 {'category': 'all', 'term': 'Toyota Industries', 'weight': 1.1, 'id': 387},
 {'category': 'all', 'term': 'Toyota Tsusho', 'weight': 1.1, 'id': 388},
 {'category': 'all', 'term': 'JTEKT', 'weight': 1.1, 'id': 389},
 {'category': 'all', 'term': 'Nippon Steel', 'weight': 1.1, 'id': 390},
 {'category': 'all', 'term': 'JFE Holdings', 'weight': 1.1, 'id': 391},
 {'category': 'all', 'term': 'Kobe Steel', 'weight': 1.1, 'id': 392},
 {'category': 'all', 'term': 'Mitsubishi Heavy Industries', 'weight': 1.1, 'id': 393},
 {'category': 'all', 'term': 'IHI', 'weight': 1.1, 'id': 394},
 {'category': 'all', 'term': 'Hitachi', 'weight': 1.1, 'id': 395},
 {'category': 'all', 'term': 'Toshiba', 'weight': 1.1, 'id': 396},
 {'category': 'all', 'term': 'Panasonic', 'weight': 1.1, 'id': 397},
 {'category': 'all', 'term': 'Sony', 'weight': 1.1, 'id': 398},
 {'category': 'all', 'term': 'Canon', 'weight': 1.1, 'id': 399},
 {'category': 'all', 'term': 'Nikon', 'weight': 1.1, 'id': 400},
 {'category': 'all', 'term': 'Olympus', 'weight': 1.1, 'id': 401},
 {'category': 'all', 'term': 'Fujifilm', 'weight': 1.1, 'id': 402},
 {'category': 'all', 'term': 'Ricoh', 'weight': 1.1, 'id': 403},
 {'category': 'all', 'term': 'Sharp', 'weight': 1.1, 'id': 404},
 {'category': 'all', 'term': 'NEC', 'weight': 1.1, 'id': 405},
 {'category': 'all', 'term': 'Fujitsu', 'weight': 1.1, 'id': 406},
 {'category': 'all', 'term': 'TDK', 'weight': 1.1, 'id': 407},
 {'category': 'all', 'term': 'Murata Manufacturing', 'weight': 1.1, 'id': 408},
 {'category': 'all', 'term': 'Kyocera', 'weight': 1.1, 'id': 409},
 {'category': 'all', 'term': 'Omron', 'weight': 1.1, 'id': 410},
 {'category': 'all', 'term': 'Keyence', 'weight': 1.1, 'id': 411},
 {'category': 'all', 'term': 'Fanuc', 'weight': 1.1, 'id': 412},
 {'category': 'all', 'term': 'Yaskawa Electric', 'weight': 1.1, 'id': 413},
 {'category': 'all', 'term': 'Rohm', 'weight': 1.1, 'id': 414},
 {'category': 'all', 'term': 'Renesas', 'weight': 1.1, 'id': 415},
 {'category': 'all', 'term': 'Tokyo Electron', 'weight': 1.1, 'id': 416},
 {'category': 'all', 'term': 'Screen Holdings', 'weight': 1.1, 'id': 417},
 {'category': 'all', 'term': 'Advantest', 'weight': 1.1, 'id': 418},
 {'category': 'all', 'term': 'Disco Corp', 'weight': 1.1, 'id': 419},
 {'category': 'all', 'term': 'Lasertec', 'weight': 1.1, 'id': 420},
 {'category': 'all', 'term': 'Sumco', 'weight': 1.1, 'id': 421},
 {'category': 'all', 'term': 'Shin-Etsu Chemical', 'weight': 1.1, 'id': 422},
 {'category': 'all', 'term': 'JSR', 'weight': 1.1, 'id': 423},
 {'category': 'all', 'term': 'Tokyo Ohka Kogyo', 'weight': 1.1, 'id': 424},
 {'category': 'all', 'term': 'Ajinomoto Build-up Film', 'weight': 1.1, 'id': 425},
 {'category': 'all', 'term': 'ABF substrate', 'weight': 1.1, 'id': 426},
 {'category': 'all', 'term': 'Ibiden', 'weight': 1.1, 'id': 427},
 {'category': 'all', 'term': 'Shinko Electric', 'weight': 1.1, 'id': 428},
 {'category': 'all', 'term': 'Mitsubishi Chemical', 'weight': 1.1, 'id': 429},
 {'category': 'all', 'term': 'Sumitomo Chemical', 'weight': 1.1, 'id': 430},
 {'category': 'all', 'term': 'Asahi Kasei', 'weight': 1.1, 'id': 431},
 {'category': 'all', 'term': 'Toray', 'weight': 1.1, 'id': 432},
 {'category': 'all', 'term': 'Teijin', 'weight': 1.1, 'id': 433},
 {'category': 'all', 'term': 'Kuraray', 'weight': 1.1, 'id': 434},
 {'category': 'all', 'term': 'Daikin', 'weight': 1.1, 'id': 435},
 {'category': 'all', 'term': 'Komatsu', 'weight': 1.1, 'id': 436},
 {'category': 'all', 'term': 'Kubota', 'weight': 1.1, 'id': 437},
 {'category': 'all', 'term': 'Makita', 'weight': 1.1, 'id': 438},
 {'category': 'all', 'term': 'SMC', 'weight': 1.1, 'id': 439},
 {'category': 'all', 'term': 'Mitsubishi Electric', 'weight': 1.1, 'id': 440},
 {'category': 'all', 'term': 'Seiko Epson', 'weight': 1.1, 'id': 441},
 {'category': 'all', 'term': 'Casio', 'weight': 1.1, 'id': 442},
 {'category': 'all', 'term': 'Citizen Watch', 'weight': 1.1, 'id': 443},
 {'category': 'all', 'term': 'Seiko Group', 'weight': 1.1, 'id': 444},
 {'category': 'all', 'term': 'Brother Industries', 'weight': 1.1, 'id': 445},
 {'category': 'all', 'term': 'Nintendo', 'weight': 1.1, 'id': 446},
 {'category': 'all', 'term': 'Bandai Namco', 'weight': 1.1, 'id': 447},
 {'category': 'all', 'term': 'Square Enix', 'weight': 1.1, 'id': 448},
 {'category': 'all', 'term': 'Capcom', 'weight': 1.1, 'id': 449},
 {'category': 'all', 'term': 'Konami', 'weight': 1.1, 'id': 450},
 {'category': 'all', 'term': 'Sega Sammy', 'weight': 1.1, 'id': 451},
 {'category': 'all', 'term': 'CyberAgent', 'weight': 1.1, 'id': 452},
 {'category': 'all', 'term': 'DeNA', 'weight': 1.1, 'id': 453},
 {'category': 'all', 'term': 'Gree', 'weight': 1.1, 'id': 454},
 {'category': 'all', 'term': 'Mixi', 'weight': 1.1, 'id': 455},
 {'category': 'all', 'term': 'Koei Tecmo', 'weight': 1.1, 'id': 456},
 {'category': 'all', 'term': 'Sony Interactive Entertainment', 'weight': 1.1, 'id': 457},
 {'category': 'all', 'term': 'SoftBank', 'weight': 1.1, 'id': 458},
 {'category': 'all', 'term': 'Rakuten', 'weight': 1.1, 'id': 459},
 {'category': 'all', 'term': 'NTT', 'weight': 1.1, 'id': 460},
 {'category': 'all', 'term': 'NTT Docomo', 'weight': 1.1, 'id': 461},
 {'category': 'all', 'term': 'KDDI', 'weight': 1.1, 'id': 462},
 {'category': 'all', 'term': 'au', 'weight': 1.1, 'id': 463},
 {'category': 'all', 'term': 'Line Yahoo', 'weight': 1.1, 'id': 464},
 {'category': 'all', 'term': 'Mercari', 'weight': 1.1, 'id': 465},
 {'category': 'all', 'term': 'DMM', 'weight': 1.1, 'id': 466},
 {'category': 'all', 'term': 'Z Holdings', 'weight': 1.1, 'id': 467},
 {'category': 'all', 'term': 'Recruit Holdings', 'weight': 1.1, 'id': 468},
 {'category': 'all', 'term': 'Rikunabi', 'weight': 1.1, 'id': 469},
 {'category': 'all', 'term': 'Indeed parent', 'weight': 1.1, 'id': 470},
 {'category': 'all', 'term': 'Fast Retailing', 'weight': 1.1, 'id': 471},
 {'category': 'all', 'term': 'Uniqlo', 'weight': 1.1, 'id': 472},
 {'category': 'all', 'term': 'GU', 'weight': 1.1, 'id': 473},
 {'category': 'all', 'term': 'Muji', 'weight': 1.1, 'id': 474},
 {'category': 'all', 'term': 'Ryohin Keikaku', 'weight': 1.1, 'id': 475},
 {'category': 'all', 'term': 'Seven & i', 'weight': 1.1, 'id': 476},
 {'category': 'all', 'term': 'Ito-Yokado', 'weight': 1.1, 'id': 477},
 {'category': 'all', 'term': 'Lawson', 'weight': 1.1, 'id': 478},
 {'category': 'all', 'term': 'FamilyMart', 'weight': 1.1, 'id': 479},
 {'category': 'all', 'term': 'Aeon', 'weight': 1.1, 'id': 480},
 {'category': 'all', 'term': 'Don Quijote', 'weight': 1.1, 'id': 481},
 {'category': 'all', 'term': 'Pan Pacific International', 'weight': 1.1, 'id': 482},
 {'category': 'all', 'term': 'Isetan Mitsukoshi', 'weight': 1.1, 'id': 483},
 {'category': 'all', 'term': 'Takashimaya', 'weight': 1.1, 'id': 484},
 {'category': 'all', 'term': 'Daimaru Matsuzakaya', 'weight': 1.1, 'id': 485},
 {'category': 'all', 'term': 'Marui', 'weight': 1.1, 'id': 486},
 {'category': 'all', 'term': 'ZOZO', 'weight': 1.1, 'id': 487},
 {'category': 'all', 'term': 'Rakuten Ichiba', 'weight': 1.1, 'id': 488},
 {'category': 'all', 'term': 'Mitsubishi UFJ', 'weight': 1.1, 'id': 489},
 {'category': 'all', 'term': 'MUFG', 'weight': 1.1, 'id': 490},
 {'category': 'all', 'term': 'Sumitomo Mitsui', 'weight': 1.1, 'id': 491},
 {'category': 'all', 'term': 'SMFG', 'weight': 1.1, 'id': 492},
 {'category': 'all', 'term': 'Mizuho', 'weight': 1.1, 'id': 493},
 {'category': 'all', 'term': 'Nomura', 'weight': 1.1, 'id': 494},
 {'category': 'all', 'term': 'Daiwa Securities', 'weight': 1.1, 'id': 495},
 {'category': 'all', 'term': 'SBI Holdings', 'weight': 1.1, 'id': 496},
 {'category': 'all', 'term': 'Monex', 'weight': 1.1, 'id': 497},
 {'category': 'all', 'term': 'ORIX', 'weight': 1.1, 'id': 498},
 {'category': 'all', 'term': 'Tokio Marine', 'weight': 1.1, 'id': 499},
 {'category': 'all', 'term': 'MS&AD', 'weight': 1.1, 'id': 500},
 {'category': 'all', 'term': 'Sompo', 'weight': 1.1, 'id': 501},
 {'category': 'all', 'term': 'Dai-ichi Life', 'weight': 1.1, 'id': 502},
 {'category': 'all', 'term': 'Nippon Life', 'weight': 1.1, 'id': 503},
 {'category': 'all', 'term': 'Meiji Yasuda', 'weight': 1.1, 'id': 504},
 {'category': 'all', 'term': 'Mitsui Fudosan', 'weight': 1.1, 'id': 505},
 {'category': 'all', 'term': 'Mitsubishi Estate', 'weight': 1.1, 'id': 506},
 {'category': 'all', 'term': 'Sumitomo Realty', 'weight': 1.1, 'id': 507},
 {'category': 'all', 'term': 'Nomura Real Estate', 'weight': 1.1, 'id': 508},
 {'category': 'all', 'term': 'Sekisui House', 'weight': 1.1, 'id': 509},
 {'category': 'all', 'term': 'Daiwa House', 'weight': 1.1, 'id': 510},
 {'category': 'all', 'term': 'Obayashi', 'weight': 1.1, 'id': 511},
 {'category': 'all', 'term': 'Kajima', 'weight': 1.1, 'id': 512},
 {'category': 'all', 'term': 'Taisei', 'weight': 1.1, 'id': 513},
 {'category': 'all', 'term': 'Shimizu Corp', 'weight': 1.1, 'id': 514},
 {'category': 'all', 'term': 'Takenaka', 'weight': 1.1, 'id': 515},
 {'category': 'all', 'term': 'JR East', 'weight': 1.1, 'id': 516},
 {'category': 'all', 'term': 'JR Central', 'weight': 1.1, 'id': 517},
 {'category': 'all', 'term': 'JR West', 'weight': 1.1, 'id': 518},
 {'category': 'all', 'term': 'JR Kyushu', 'weight': 1.1, 'id': 519},
 {'category': 'all', 'term': 'JR Hokkaido', 'weight': 1.1, 'id': 520},
 {'category': 'all', 'term': 'JR Shikoku', 'weight': 1.1, 'id': 521},
 {'category': 'all', 'term': 'Tokyo Metro', 'weight': 1.1, 'id': 522},
 {'category': 'all', 'term': 'Tokyu', 'weight': 1.1, 'id': 523},
 {'category': 'all', 'term': 'Odakyu', 'weight': 1.1, 'id': 524},
 {'category': 'all', 'term': 'Keio', 'weight': 1.1, 'id': 525},
 {'category': 'all', 'term': 'Tobu', 'weight': 1.1, 'id': 526},
 {'category': 'all', 'term': 'Seibu', 'weight': 1.1, 'id': 527},
 {'category': 'all', 'term': 'Keisei', 'weight': 1.1, 'id': 528},
 {'category': 'all', 'term': 'Keikyu', 'weight': 1.1, 'id': 529},
 {'category': 'all', 'term': 'Kintetsu', 'weight': 1.1, 'id': 530},
 {'category': 'all', 'term': 'Hankyu Hanshin', 'weight': 1.1, 'id': 531},
 {'category': 'all', 'term': 'Nankai', 'weight': 1.1, 'id': 532},
 {'category': 'all', 'term': 'NISA', 'weight': 1.2, 'id': 533},
 {'category': 'all', 'term': 'All Nippon Airways', 'weight': 1.1, 'id': 534},
 {'category': 'all', 'term': 'JAL', 'weight': 1.1, 'id': 535},
 {'category': 'all', 'term': 'J-Air', 'weight': 1.1, 'id': 536},
 {'category': 'all', 'term': 'Skymark', 'weight': 1.1, 'id': 537},
 {'category': 'all', 'term': 'Peach Aviation', 'weight': 1.1, 'id': 538},
 {'category': 'all', 'term': 'Zipair', 'weight': 1.1, 'id': 539},
 {'category': 'all', 'term': 'Yamato Holdings', 'weight': 1.1, 'id': 540},
 {'category': 'all', 'term': 'Kuroneko Yamato', 'weight': 1.1, 'id': 541},
 {'category': 'all', 'term': 'Sagawa Express', 'weight': 1.1, 'id': 542},
 {'category': 'all', 'term': 'Nippon Express', 'weight': 1.1, 'id': 543},
 {'category': 'all', 'term': 'Nippon Yusen', 'weight': 1.1, 'id': 544},
 {'category': 'all', 'term': 'NYK Line', 'weight': 1.1, 'id': 545},
 {'category': 'all', 'term': 'Mitsui OSK Lines', 'weight': 1.1, 'id': 546},
 {'category': 'all', 'term': 'K Line', 'weight': 1.1, 'id': 547},
 {'category': 'all', 'term': 'Mitsubishi Corporation', 'weight': 1.1, 'id': 548},
 {'category': 'all', 'term': 'Mitsui & Co', 'weight': 1.1, 'id': 549},
 {'category': 'all', 'term': 'Itochu', 'weight': 1.1, 'id': 550},
 {'category': 'all', 'term': 'Sumitomo Corporation', 'weight': 1.1, 'id': 551},
 {'category': 'all', 'term': 'Marubeni', 'weight': 1.1, 'id': 552},
 {'category': 'all', 'term': 'Sojitz', 'weight': 1.1, 'id': 553},
 {'category': 'all', 'term': 'sogo shosha', 'weight': 1.1, 'id': 554},
 {'category': 'all', 'term': 'keiretsu', 'weight': 1.1, 'id': 555},
 {'category': 'all', 'term': 'zaibatsu', 'weight': 1.1, 'id': 556},
 {'category': 'all', 'term': 'cross-shareholding', 'weight': 1.1, 'id': 557},
 {'category': 'all', 'term': 'TOB', 'weight': 1.1, 'id': 558},
 {'category': 'all', 'term': 'kawaii', 'weight': 0.9, 'id': 559},
 {'category': 'all', 'term': 'otaku', 'weight': 0.9, 'id': 560},
 {'category': 'all', 'term': 'cosplay', 'weight': 0.9, 'id': 561},
 {'category': 'all', 'term': 'Comiket', 'weight': 0.9, 'id': 562},
 {'category': 'all', 'term': 'doujinshi', 'weight': 0.9, 'id': 563},
 {'category': 'all', 'term': 'doujin', 'weight': 0.9, 'id': 564},
 {'category': 'all', 'term': 'maid cafe', 'weight': 0.9, 'id': 565},
 {'category': 'all', 'term': 'Akihabara culture', 'weight': 0.9, 'id': 566},
 {'category': 'all', 'term': 'Harajuku fashion', 'weight': 0.9, 'id': 567},
 {'category': 'all', 'term': 'Lolita fashion', 'weight': 0.9, 'id': 568},
 {'category': 'all', 'term': 'gyaru', 'weight': 0.9, 'id': 569},
 {'category': 'all', 'term': 'decora fashion', 'weight': 0.9, 'id': 570},
 {'category': 'all', 'term': 'visual kei', 'weight': 0.9, 'id': 571},
 {'category': 'all', 'term': 'J-pop', 'weight': 0.9, 'id': 572},
 {'category': 'all', 'term': 'J-rock', 'weight': 0.9, 'id': 573},
 {'category': 'all', 'term': 'city pop', 'weight': 0.9, 'id': 574},
 {'category': 'all', 'term': 'enka', 'weight': 0.9, 'id': 575},
 {'category': 'all', 'term': 'kayokyoku', 'weight': 0.9, 'id': 576},
 {'category': 'all', 'term': 'Vocaloid', 'weight': 0.9, 'id': 577},
 {'category': 'all', 'term': 'Hatsune Miku', 'weight': 0.9, 'id': 578},
 {'category': 'all', 'term': 'Kagamine Rin', 'weight': 0.9, 'id': 579},
 {'category': 'all', 'term': 'Megurine Luka', 'weight': 0.9, 'id': 580},
 {'category': 'all', 'term': 'Utau', 'weight': 0.9, 'id': 581},
 {'category': 'all', 'term': 'Niconico', 'weight': 0.9, 'id': 582},
 {'category': 'all', 'term': 'Pixiv', 'weight': 0.9, 'id': 583},
 {'category': 'all', 'term': 'Line stickers', 'weight': 0.9, 'id': 584},
 {'category': 'all', 'term': 'emoji culture', 'weight': 0.9, 'id': 585},
 {'category': 'all', 'term': 'kaomoji', 'weight': 0.9, 'id': 586},
 {'category': 'all', 'term': 'purikura', 'weight': 0.9, 'id': 587},
 {'category': 'all', 'term': 'gachapon', 'weight': 0.9, 'id': 588},
 {'category': 'all', 'term': 'capsule toy', 'weight': 0.9, 'id': 589},
 {'category': 'all', 'term': 'gacha', 'weight': 0.9, 'id': 590},
 {'category': 'all', 'term': 'oshikatsu', 'weight': 0.9, 'id': 591},
 {'category': 'all', 'term': 'oshi', 'weight': 0.9, 'id': 592},
 {'category': 'all', 'term': 'idol', 'weight': 0.9, 'id': 593},
 {'category': 'all', 'term': 'underground idol', 'weight': 0.9, 'id': 594},
 {'category': 'all', 'term': 'gravure idol', 'weight': 0.9, 'id': 595},
 {'category': 'all', 'term': 'boy band', 'weight': 0.9, 'id': 596},
 {'category': 'all', 'term': 'girl group', 'weight': 0.9, 'id': 597},
 {'category': 'all', 'term': 'Johnny & Associates', 'weight': 0.9, 'id': 598},
 {'category': 'all', 'term': 'Starto Entertainment', 'weight': 0.9, 'id': 599},
 {'category': 'all', 'term': 'AKB48', 'weight': 0.9, 'id': 600},
 {'category': 'all', 'term': 'Nogizaka46', 'weight': 0.9, 'id': 601},
 {'category': 'all', 'term': 'Sakurazaka46', 'weight': 0.9, 'id': 602},
 {'category': 'all', 'term': 'Hinatazaka46', 'weight': 0.9, 'id': 603},
 {'category': 'all', 'term': 'Morning Musume', 'weight': 0.9, 'id': 604},
 {'category': 'all', 'term': 'Perfume', 'weight': 0.9, 'id': 605},
 {'category': 'all', 'term': 'Babymetal', 'weight': 0.9, 'id': 606},
 {'category': 'all', 'term': 'XG', 'weight': 0.9, 'id': 607},
 {'category': 'all', 'term': 'King Gnu', 'weight': 0.9, 'id': 608},
 {'category': 'all', 'term': 'YOASOBI', 'weight': 0.9, 'id': 609},
 {'category': 'all', 'term': 'Ado', 'weight': 0.9, 'id': 610},
 {'category': 'all', 'term': 'Kenshi Yonezu', 'weight': 0.9, 'id': 611},
 {'category': 'all', 'term': 'LiSA', 'weight': 0.9, 'id': 612},
 {'category': 'all', 'term': 'Yumi Matsutoya', 'weight': 0.9, 'id': 613},
 {'category': 'all', 'term': 'Ryuichi Sakamoto', 'weight': 0.9, 'id': 614},
 {'category': 'all', 'term': 'Joe Hisaishi', 'weight': 0.9, 'id': 615},
 {'category': 'all', 'term': 'karaoke', 'weight': 0.9, 'id': 616},
 {'category': 'all', 'term': 'J-drama', 'weight': 0.9, 'id': 617},
 {'category': 'all', 'term': 'taiga drama', 'weight': 0.9, 'id': 618},
 {'category': 'all', 'term': 'asadora', 'weight': 0.9, 'id': 619},
 {'category': 'all', 'term': 'Takarazuka', 'weight': 0.9, 'id': 620},
 {'category': 'all', 'term': 'Takarazuka Revue', 'weight': 0.9, 'id': 621},
 {'category': 'all', 'term': 'kabuki actor', 'weight': 0.9, 'id': 622},
 {'category': 'all', 'term': 'host club', 'weight': 0.9, 'id': 623},
 {'category': 'all', 'term': 'Kabukicho', 'weight': 0.9, 'id': 624},
 {'category': 'all', 'term': 'Shibuya fashion', 'weight': 0.9, 'id': 625},
 {'category': 'all', 'term': 'Halloween in Shibuya', 'weight': 0.9, 'id': 626},
 {'category': 'all', 'term': 'Sanrio', 'weight': 0.9, 'id': 627},
 {'category': 'all', 'term': 'Hello Kitty', 'weight': 0.9, 'id': 628},
 {'category': 'all', 'term': 'My Melody', 'weight': 0.9, 'id': 629},
 {'category': 'all', 'term': 'Kuromi', 'weight': 0.9, 'id': 630},
 {'category': 'all', 'term': 'Cinnamoroll', 'weight': 0.9, 'id': 631},
 {'category': 'all', 'term': 'Pompompurin', 'weight': 0.9, 'id': 632},
 {'category': 'all', 'term': 'Rilakkuma', 'weight': 0.9, 'id': 633},
 {'category': 'all', 'term': 'Sumikko Gurashi', 'weight': 0.9, 'id': 634},
 {'category': 'all', 'term': 'Chiikawa', 'weight': 0.9, 'id': 635},
 {'category': 'all', 'term': 'Domo-kun', 'weight': 0.9, 'id': 636},
 {'category': 'all', 'term': 'Kumamon', 'weight': 0.9, 'id': 637},
 {'category': 'all', 'term': 'Funassyi', 'weight': 0.9, 'id': 638},
 {'category': 'all', 'term': 'Kewpie doll', 'weight': 0.9, 'id': 639},
 {'category': 'all', 'term': 'kokeshi', 'weight': 0.9, 'id': 640},
 {'category': 'all', 'term': 'Maneki-neko', 'weight': 0.9, 'id': 641},
 {'category': 'all', 'term': 'daruma doll', 'weight': 0.9, 'id': 642},
 {'category': 'all', 'term': 'kendama', 'weight': 0.9, 'id': 643},
 {'category': 'all', 'term': 'tamagochi', 'weight': 0.9, 'id': 644},
 {'category': 'all', 'term': 'Tamagotchi', 'weight': 0.9, 'id': 645},
 {'category': 'all', 'term': 'tokusatsu', 'weight': 0.9, 'id': 646},
 {'category': 'all', 'term': 'kaiju', 'weight': 0.9, 'id': 647},
 {'category': 'all', 'term': 'super sentai', 'weight': 0.9, 'id': 648},
 {'category': 'all', 'term': 'Kamen Rider', 'weight': 0.9, 'id': 649},
 {'category': 'all', 'term': 'Ultraman', 'weight': 0.9, 'id': 650},
 {'category': 'all', 'term': 'Godzilla', 'weight': 0.9, 'id': 651},
 {'category': 'all', 'term': 'Mothra', 'weight': 0.9, 'id': 652},
 {'category': 'all', 'term': 'Gamera', 'weight': 0.9, 'id': 653},
 {'category': 'all', 'term': 'Toho monsters', 'weight': 0.9, 'id': 654},
 {'category': 'all', 'term': 'VTuber', 'weight': 0.9, 'id': 655},
 {'category': 'all', 'term': 'Hololive', 'weight': 0.9, 'id': 656},
 {'category': 'all', 'term': 'Nijisanji', 'weight': 0.9, 'id': 657},
 {'category': 'all', 'term': 'Kizuna AI', 'weight': 0.9, 'id': 658},
 {'category': 'all', 'term': 'Studio Ghibli', 'weight': 1.0, 'id': 659},
 {'category': 'all', 'term': 'Hayao Miyazaki', 'weight': 1.0, 'id': 660},
 {'category': 'all', 'term': 'Isao Takahata', 'weight': 1.0, 'id': 661},
 {'category': 'all', 'term': 'Ghibli Park', 'weight': 1.0, 'id': 662},
 {'category': 'all', 'term': 'Totoro', 'weight': 1.0, 'id': 663},
 {'category': 'all', 'term': 'Spirited Away', 'weight': 1.0, 'id': 664},
 {'category': 'all', 'term': 'Princess Mononoke', 'weight': 1.0, 'id': 665},
 {'category': 'all', 'term': 'Howl’s Moving Castle', 'weight': 1.0, 'id': 666},
 {'category': 'all', 'term': 'Kiki’s Delivery Service', 'weight': 1.0, 'id': 667},
 {'category': 'all', 'term': 'Ponyo', 'weight': 1.0, 'id': 668},
 {'category': 'all', 'term': 'The Boy and the Heron', 'weight': 1.0, 'id': 669},
 {'category': 'all', 'term': 'Evangelion', 'weight': 1.0, 'id': 670},
 {'category': 'all', 'term': 'Gundam', 'weight': 1.0, 'id': 671},
 {'category': 'all', 'term': 'Mobile Suit Gundam', 'weight': 1.0, 'id': 672},
 {'category': 'all', 'term': 'One Piece', 'weight': 1.0, 'id': 673},
 {'category': 'all', 'term': 'Demon Slayer', 'weight': 1.0, 'id': 674},
 {'category': 'all', 'term': 'Kimetsu no Yaiba', 'weight': 1.0, 'id': 675},
 {'category': 'all', 'term': 'Dragon Ball', 'weight': 1.0, 'id': 676},
 {'category': 'all', 'term': 'Naruto', 'weight': 1.0, 'id': 677},
 {'category': 'all', 'term': 'Boruto', 'weight': 1.0, 'id': 678},
 {'category': 'all', 'term': 'Jujutsu Kaisen', 'weight': 1.0, 'id': 679},
 {'category': 'all', 'term': 'Attack on Titan', 'weight': 1.0, 'id': 680},
 {'category': 'all', 'term': 'My Hero Academia', 'weight': 1.0, 'id': 681},
 {'category': 'all', 'term': 'Chainsaw Man', 'weight': 1.0, 'id': 682},
 {'category': 'all', 'term': 'Spy x Family', 'weight': 1.0, 'id': 683},
 {'category': 'all', 'term': 'Sailor Moon', 'weight': 1.0, 'id': 684},
 {'category': 'all', 'term': 'Detective Conan', 'weight': 1.0, 'id': 685},
 {'category': 'all', 'term': 'Doraemon', 'weight': 1.0, 'id': 686},
 {'category': 'all', 'term': 'Anpanman', 'weight': 1.0, 'id': 687},
 {'category': 'all', 'term': 'Crayon Shin-chan', 'weight': 1.0, 'id': 688},
 {'category': 'all', 'term': 'Pokemon anime', 'weight': 1.0, 'id': 689},
 {'category': 'all', 'term': 'Digimon anime', 'weight': 1.0, 'id': 690},
 {'category': 'all', 'term': 'Yu-Gi-Oh anime', 'weight': 1.0, 'id': 691},
 {'category': 'all', 'term': 'Beyblade anime', 'weight': 1.0, 'id': 692},
 {'category': 'all', 'term': 'Fullmetal Alchemist', 'weight': 1.0, 'id': 693},
 {'category': 'all', 'term': 'Death Note', 'weight': 1.0, 'id': 694},
 {'category': 'all', 'term': 'Cowboy Bebop', 'weight': 1.0, 'id': 695},
 {'category': 'all', 'term': 'Ghost in the Shell', 'weight': 1.0, 'id': 696},
 {'category': 'all', 'term': 'Akira anime', 'weight': 1.0, 'id': 697},
 {'category': 'all', 'term': 'Your Name', 'weight': 1.0, 'id': 698},
 {'category': 'all', 'term': 'Makoto Shinkai', 'weight': 1.0, 'id': 699},
 {'category': 'all', 'term': 'Suzume', 'weight': 1.0, 'id': 700},
 {'category': 'all', 'term': 'Weathering with You', 'weight': 1.0, 'id': 701},
 {'category': 'all', 'term': 'Neon Genesis Evangelion', 'weight': 1.0, 'id': 702},
 {'category': 'all', 'term': 'Frieren', 'weight': 1.0, 'id': 703},
 {'category': 'all', 'term': 'Oshi no Ko', 'weight': 1.0, 'id': 704},
 {'category': 'all', 'term': 'Haikyu', 'weight': 1.0, 'id': 705},
 {'category': 'all', 'term': 'Blue Lock', 'weight': 1.0, 'id': 706},
 {'category': 'all', 'term': 'Bocchi the Rock', 'weight': 1.0, 'id': 707},
 {'category': 'all', 'term': 'Solo Leveling anime', 'weight': 1.0, 'id': 708},
 {'category': 'all', 'term': 'Mario', 'weight': 1.0, 'id': 709},
 {'category': 'all', 'term': 'Super Mario', 'weight': 1.0, 'id': 710},
 {'category': 'all', 'term': 'Zelda', 'weight': 1.0, 'id': 711},
 {'category': 'all', 'term': 'Legend of Zelda', 'weight': 1.0, 'id': 712},
 {'category': 'all', 'term': 'Donkey Kong', 'weight': 1.0, 'id': 713},
 {'category': 'all', 'term': 'Kirby', 'weight': 1.0, 'id': 714},
 {'category': 'all', 'term': 'Metroid', 'weight': 1.0, 'id': 715},
 {'category': 'all', 'term': 'Animal Crossing', 'weight': 1.0, 'id': 716},
 {'category': 'all', 'term': 'Splatoon', 'weight': 1.0, 'id': 717},
 {'category': 'all', 'term': 'Fire Emblem', 'weight': 1.0, 'id': 718},
 {'category': 'all', 'term': 'Pikmin', 'weight': 1.0, 'id': 719},
 {'category': 'all', 'term': 'Smash Bros', 'weight': 1.0, 'id': 720},
 {'category': 'all', 'term': 'Super Smash Bros', 'weight': 1.0, 'id': 721},
 {'category': 'all', 'term': 'Nintendo Switch', 'weight': 1.0, 'id': 722},
 {'category': 'all', 'term': 'Switch 2', 'weight': 1.0, 'id': 723},
 {'category': 'all', 'term': 'Nintendo Direct', 'weight': 1.0, 'id': 724},
 {'category': 'all', 'term': 'Pokemon games', 'weight': 1.0, 'id': 725},
 {'category': 'all', 'term': 'Pokemon Scarlet', 'weight': 1.0, 'id': 726},
 {'category': 'all', 'term': 'Pokemon Violet', 'weight': 1.0, 'id': 727},
 {'category': 'all', 'term': 'Pokemon Legends', 'weight': 1.0, 'id': 728},
 {'category': 'all', 'term': 'Pikachu', 'weight': 1.0, 'id': 729},
 {'category': 'all', 'term': 'Eevee', 'weight': 1.0, 'id': 730},
 {'category': 'all', 'term': 'Final Fantasy', 'weight': 1.0, 'id': 731},
 {'category': 'all', 'term': 'Dragon Quest', 'weight': 1.0, 'id': 732},
 {'category': 'all', 'term': 'Kingdom Hearts', 'weight': 1.0, 'id': 733},
 {'category': 'all', 'term': 'Persona', 'weight': 1.0, 'id': 734},
 {'category': 'all', 'term': 'Shin Megami Tensei', 'weight': 1.0, 'id': 735},
 {'category': 'all', 'term': 'Monster Hunter', 'weight': 1.0, 'id': 736},
 {'category': 'all', 'term': 'Resident Evil', 'weight': 1.0, 'id': 737},
 {'category': 'all', 'term': 'Street Fighter', 'weight': 1.0, 'id': 738},
 {'category': 'all', 'term': 'Tekken', 'weight': 1.0, 'id': 739},
 {'category': 'all', 'term': 'Yakuza game', 'weight': 1.0, 'id': 740},
 {'category': 'all', 'term': 'Like a Dragon', 'weight': 1.0, 'id': 741},
 {'category': 'all', 'term': 'Sonic the Hedgehog', 'weight': 1.0, 'id': 742},
 {'category': 'all', 'term': 'Metal Gear', 'weight': 1.0, 'id': 743},
 {'category': 'all', 'term': 'Silent Hill', 'weight': 1.0, 'id': 744},
 {'category': 'all', 'term': 'Castlevania', 'weight': 1.0, 'id': 745},
 {'category': 'all', 'term': 'Ace Attorney', 'weight': 1.0, 'id': 746},
 {'category': 'all', 'term': 'Phoenix Wright', 'weight': 1.0, 'id': 747},
 {'category': 'all', 'term': 'Elden Ring', 'weight': 1.0, 'id': 748},
 {'category': 'all', 'term': 'Dark Souls', 'weight': 1.0, 'id': 749},
 {'category': 'all', 'term': 'Sekiro', 'weight': 1.0, 'id': 750},
 {'category': 'all', 'term': 'Bloodborne', 'weight': 1.0, 'id': 751},
 {'category': 'all', 'term': 'Armored Core', 'weight': 1.0, 'id': 752},
 {'category': 'all', 'term': 'Gran Turismo', 'weight': 1.0, 'id': 753},
 {'category': 'all', 'term': 'Dynasty Warriors', 'weight': 1.0, 'id': 754},
 {'category': 'all', 'term': 'Touhou Project', 'weight': 1.0, 'id': 755},
 {'category': 'all', 'term': 'Pachinko', 'weight': 1.0, 'id': 756},
 {'category': 'all', 'term': 'Pachislot', 'weight': 1.0, 'id': 757},
 {'category': 'all', 'term': 'Arcade cabinet', 'weight': 1.0, 'id': 758},
 {'category': 'all', 'term': 'Mount Fuji', 'weight': 0.9, 'id': 759},
 {'category': 'all', 'term': 'Fuji Five Lakes', 'weight': 0.9, 'id': 760},
 {'category': 'all', 'term': 'Lake Kawaguchi', 'weight': 0.9, 'id': 761},
 {'category': 'all', 'term': 'Hakone', 'weight': 0.9, 'id': 762},
 {'category': 'all', 'term': 'Nikko', 'weight': 0.9, 'id': 763},
 {'category': 'all', 'term': 'Kamakura', 'weight': 0.9, 'id': 764},
 {'category': 'all', 'term': 'Koyasan', 'weight': 0.9, 'id': 765},
 {'category': 'all', 'term': 'Kumano Kodo', 'weight': 0.9, 'id': 766},
 {'category': 'all', 'term': 'Shirakawa-go', 'weight': 0.9, 'id': 767},
 {'category': 'all', 'term': 'Gokayama', 'weight': 0.9, 'id': 768},
 {'category': 'all', 'term': 'Takayama', 'weight': 0.9, 'id': 769},
 {'category': 'all', 'term': 'Kanazawa tourism', 'weight': 0.9, 'id': 770},
 {'category': 'all', 'term': 'Higashi Chaya', 'weight': 0.9, 'id': 771},
 {'category': 'all', 'term': 'Kenrokuen', 'weight': 0.9, 'id': 772},
 {'category': 'all', 'term': 'Matsumoto Castle', 'weight': 0.9, 'id': 773},
 {'category': 'all', 'term': 'Himeji Castle', 'weight': 0.9, 'id': 774},
 {'category': 'all', 'term': 'Osaka Castle', 'weight': 0.9, 'id': 775},
 {'category': 'all', 'term': 'Nijo Castle', 'weight': 0.9, 'id': 776},
 {'category': 'all', 'term': 'Hiroshima Peace Memorial', 'weight': 0.9, 'id': 777},
 {'category': 'all', 'term': 'Atomic Bomb Dome', 'weight': 0.9, 'id': 778},
 {'category': 'all', 'term': 'Miyajima', 'weight': 0.9, 'id': 779},
 {'category': 'all', 'term': 'Itsukushima Shrine', 'weight': 0.9, 'id': 780},
 {'category': 'all', 'term': 'Fushimi Inari', 'weight': 0.9, 'id': 781},
 {'category': 'all', 'term': 'Kiyomizu-dera', 'weight': 0.9, 'id': 782},
 {'category': 'all', 'term': 'Kinkaku-ji', 'weight': 0.9, 'id': 783},
 {'category': 'all', 'term': 'Ginkaku-ji', 'weight': 0.9, 'id': 784},
 {'category': 'all', 'term': 'Arashiyama', 'weight': 0.9, 'id': 785},
 {'category': 'all', 'term': 'Bamboo Grove', 'weight': 0.9, 'id': 786},
 {'category': 'all', 'term': 'Gion', 'weight': 0.9, 'id': 787},
 {'category': 'all', 'term': 'Nishiki Market', 'weight': 0.9, 'id': 788},
 {'category': 'all', 'term': 'Nara Park', 'weight': 0.9, 'id': 789},
 {'category': 'all', 'term': 'Todaiji', 'weight': 0.9, 'id': 790},
 {'category': 'all', 'term': 'Kasuga Taisha', 'weight': 0.9, 'id': 791},
 {'category': 'all', 'term': 'Shibuya Crossing', 'weight': 0.9, 'id': 792},
 {'category': 'all', 'term': 'Tokyo Skytree', 'weight': 0.9, 'id': 793},
 {'category': 'all', 'term': 'Tokyo Tower', 'weight': 0.9, 'id': 794},
 {'category': 'all', 'term': 'Sensoji', 'weight': 0.9, 'id': 795},
 {'category': 'all', 'term': 'Meiji Shrine', 'weight': 0.9, 'id': 796},
 {'category': 'all', 'term': 'Tsukiji Outer Market', 'weight': 0.9, 'id': 797},
 {'category': 'all', 'term': 'teamLab Borderless', 'weight': 0.9, 'id': 798},
 {'category': 'all', 'term': 'teamLab Planets', 'weight': 0.9, 'id': 799},
 {'category': 'all', 'term': 'Universal Studios Osaka', 'weight': 0.9, 'id': 800},
 {'category': 'all', 'term': 'Tokyo Disneyland', 'weight': 0.9, 'id': 801},
 {'category': 'all', 'term': 'Tokyo DisneySea', 'weight': 0.9, 'id': 802},
 {'category': 'all', 'term': 'Ghibli Museum', 'weight': 0.9, 'id': 803},
 {'category': 'all', 'term': 'Ghibli Park tourism', 'weight': 0.9, 'id': 804},
 {'category': 'all', 'term': 'Hakone Open-Air Museum', 'weight': 0.9, 'id': 805},
 {'category': 'all', 'term': 'Naoshima art island', 'weight': 0.9, 'id': 806},
 {'category': 'all', 'term': 'Benesse Art Site', 'weight': 0.9, 'id': 807},
 {'category': 'all', 'term': 'Teshima', 'weight': 0.9, 'id': 808},
 {'category': 'all', 'term': 'Setouchi Triennale', 'weight': 0.9, 'id': 809},
 {'category': 'all', 'term': 'Shimanami Kaido', 'weight': 0.9, 'id': 810},
 {'category': 'all', 'term': 'Matsuyama Castle', 'weight': 0.9, 'id': 811},
 {'category': 'all', 'term': 'Dogo Onsen', 'weight': 0.9, 'id': 812},
 {'category': 'all', 'term': 'Beppu Onsen', 'weight': 0.9, 'id': 813},
 {'category': 'all', 'term': 'Yufuin', 'weight': 0.9, 'id': 814},
 {'category': 'all', 'term': 'Kurokawa Onsen', 'weight': 0.9, 'id': 815},
 {'category': 'all', 'term': 'Kinosaki Onsen', 'weight': 0.9, 'id': 816},
 {'category': 'all', 'term': 'Arima Onsen', 'weight': 0.9, 'id': 817},
 {'category': 'all', 'term': 'Kusatsu Onsen', 'weight': 0.9, 'id': 818},
 {'category': 'all', 'term': 'Nozawa Onsen', 'weight': 0.9, 'id': 819},
 {'category': 'all', 'term': 'Ginzan Onsen', 'weight': 0.9, 'id': 820},
 {'category': 'all', 'term': 'Zao Onsen', 'weight': 0.9, 'id': 821},
 {'category': 'all', 'term': 'Jigokudani Monkey Park', 'weight': 0.9, 'id': 822},
 {'category': 'all', 'term': 'snow monkeys', 'weight': 0.9, 'id': 823},
 {'category': 'all', 'term': 'Sapporo Snow Festival', 'weight': 0.9, 'id': 824},
 {'category': 'all', 'term': 'Otaru Canal', 'weight': 0.9, 'id': 825},
 {'category': 'all', 'term': 'Furano lavender', 'weight': 0.9, 'id': 826},
 {'category': 'all', 'term': 'Biei Blue Pond', 'weight': 0.9, 'id': 827},
 {'category': 'all', 'term': 'Niseko', 'weight': 0.9, 'id': 828},
 {'category': 'all', 'term': 'Rusutsu', 'weight': 0.9, 'id': 829},
 {'category': 'all', 'term': 'Hakuba', 'weight': 0.9, 'id': 830},
 {'category': 'all', 'term': 'Nozawa ski resort', 'weight': 0.9, 'id': 831},
 {'category': 'all', 'term': 'Shiga Kogen', 'weight': 0.9, 'id': 832},
 {'category': 'all', 'term': 'Okinawa tourism', 'weight': 0.9, 'id': 833},
 {'category': 'all', 'term': 'Naha tourism', 'weight': 0.9, 'id': 834},
 {'category': 'all', 'term': 'Shuri Castle', 'weight': 0.9, 'id': 835},
 {'category': 'all', 'term': 'Ishigaki tourism', 'weight': 0.9, 'id': 836},
 {'category': 'all', 'term': 'Miyakojima beaches', 'weight': 0.9, 'id': 837},
 {'category': 'all', 'term': 'Kerama Islands', 'weight': 0.9, 'id': 838},
 {'category': 'all', 'term': 'Churaumi Aquarium', 'weight': 0.9, 'id': 839},
 {'category': 'all', 'term': 'Yakushima', 'weight': 0.9, 'id': 840},
 {'category': 'all', 'term': 'Jomon Sugi', 'weight': 0.9, 'id': 841},
 {'category': 'all', 'term': 'Amami Oshima tourism', 'weight': 0.9, 'id': 842},
 {'category': 'all', 'term': 'Kagoshima tourism', 'weight': 0.9, 'id': 843},
 {'category': 'all', 'term': 'Sakurajima tourism', 'weight': 0.9, 'id': 844},
 {'category': 'all', 'term': 'Fukuoka yatai', 'weight': 0.9, 'id': 845},
 {'category': 'all', 'term': 'Dazaifu Tenmangu', 'weight': 0.9, 'id': 846},
 {'category': 'all', 'term': 'Nagasaki lantern festival', 'weight': 0.9, 'id': 847},
 {'category': 'all', 'term': 'Gunkanjima', 'weight': 0.9, 'id': 848},
 {'category': 'all', 'term': 'Kumamoto Castle', 'weight': 0.9, 'id': 849},
 {'category': 'all', 'term': 'Aso caldera', 'weight': 0.9, 'id': 850},
 {'category': 'all', 'term': 'Iya Valley', 'weight': 0.9, 'id': 851},
 {'category': 'all', 'term': 'Tottori Sand Dunes', 'weight': 0.9, 'id': 852},
 {'category': 'all', 'term': 'Amanohashidate', 'weight': 0.9, 'id': 853},
 {'category': 'all', 'term': 'Ise Grand Shrine', 'weight': 0.9, 'id': 854},
 {'category': 'all', 'term': 'Nakasendo trail', 'weight': 0.9, 'id': 855},
 {'category': 'all', 'term': 'Tokaido trail', 'weight': 0.9, 'id': 856},
 {'category': 'all', 'term': 'Tokyo Game Show', 'weight': 1.05, 'id': 857},
 {'category': 'all', 'term': 'Kyoto Animation', 'weight': 1.05, 'id': 858},
 {'category': 'all', 'term': 'JCP', 'weight': 1.0, 'id': 859},
 {'category': 'all', 'term': 'Nippon Ishin no Kai', 'weight': 1.0, 'id': 860},
 {'category': 'all', 'term': 'Kokumin Minshuto', 'weight': 1.0, 'id': 861},
 {'category': 'all', 'term': 'Sanseito', 'weight': 1.0, 'id': 862},
 {'category': 'all', 'term': 'Tomin First no Kai', 'weight': 1.0, 'id': 863},
 {'category': 'all', 'term': 'Jiminto', 'weight': 1.0, 'id': 864},
 {'category': 'all', 'term': 'Jimin', 'weight': 1.0, 'id': 865},
 {'category': 'all', 'term': 'Rikken Minshuto', 'weight': 1.0, 'id': 866},
 {'category': 'all', 'term': 'Komei Party', 'weight': 1.0, 'id': 867},
 {'category': 'all', 'term': 'Komeito Party', 'weight': 1.0, 'id': 868},
 {'category': 'all', 'term': 'Shugiin election', 'weight': 1.0, 'id': 869},
 {'category': 'all', 'term': 'Sangiin election', 'weight': 1.0, 'id': 870},
 {'category': 'all', 'term': 'Kokkai debate', 'weight': 1.0, 'id': 871},
 {'category': 'all', 'term': 'Kantei press conference', 'weight': 1.0, 'id': 872},
 {'category': 'all', 'term': 'Nagatacho politics', 'weight': 1.0, 'id': 873},
 {'category': 'all', 'term': 'Kasumigaseki bureaucracy', 'weight': 1.0, 'id': 874},
 {'category': 'all', 'term': 'Saikosai', 'weight': 1.0, 'id': 875},
 {'category': 'all', 'term': 'Tokyo prosecutors', 'weight': 1.0, 'id': 876},
 {'category': 'all', 'term': 'Tokyo District Public Prosecutors Office', 'weight': 1.0, 'id': 877},
 {'category': 'all', 'term': 'Osaka District Court', 'weight': 1.0, 'id': 878},
 {'category': 'all', 'term': 'Sapporo District Court', 'weight': 1.0, 'id': 879},
 {'category': 'all', 'term': 'Nagoya District Court', 'weight': 1.0, 'id': 880},
 {'category': 'all', 'term': 'Fukuoka District Court', 'weight': 1.0, 'id': 881},
 {'category': 'all', 'term': 'Hiroshima High Court', 'weight': 1.0, 'id': 882},
 {'category': 'all', 'term': 'Osaka High Court', 'weight': 1.0, 'id': 883},
 {'category': 'all', 'term': 'koseki system', 'weight': 1.0, 'id': 884},
 {'category': 'all', 'term': 'juminhyo', 'weight': 1.0, 'id': 885},
 {'category': 'all', 'term': 'zairyu card', 'weight': 1.0, 'id': 886},
 {'category': 'all', 'term': 'My Number system', 'weight': 1.0, 'id': 887},
 {'category': 'all', 'term': 'MyNa health insurance card', 'weight': 1.0, 'id': 888},
 {'category': 'all', 'term': 'Hanko culture', 'weight': 1.0, 'id': 889},
 {'category': 'all', 'term': 'hanko seal', 'weight': 1.0, 'id': 890},
 {'category': 'all', 'term': 'inkan seal', 'weight': 1.0, 'id': 891},
 {'category': 'all', 'term': 'Juki Net', 'weight': 1.0, 'id': 892},
 {'category': 'all', 'term': 'e-Gov', 'weight': 1.0, 'id': 893},
 {'category': 'all', 'term': 'e-Tax', 'weight': 1.0, 'id': 894},
 {'category': 'all', 'term': 'eLTAX', 'weight': 1.0, 'id': 895},
 {'category': 'all', 'term': 'Myna Portal', 'weight': 1.0, 'id': 896},
 {'category': 'all', 'term': 'Mynaportal', 'weight': 1.0, 'id': 897},
 {'category': 'all', 'term': 'Hello Work', 'weight': 1.0, 'id': 898},
 {'category': 'all', 'term': 'Koban', 'weight': 1.0, 'id': 899},
 {'category': 'all', 'term': 'koban police box', 'weight': 1.0, 'id': 900},
 {'category': 'all', 'term': 'chonaikai', 'weight': 1.0, 'id': 901},
 {'category': 'all', 'term': 'jichikai', 'weight': 1.0, 'id': 902},
 {'category': 'all', 'term': 'han system', 'weight': 1.0, 'id': 903},
 {'category': 'all', 'term': 'Boeisho', 'weight': 1.0, 'id': 904},
 {'category': 'all', 'term': 'JMSDF destroyer', 'weight': 1.0, 'id': 905},
 {'category': 'all', 'term': 'Izumo-class destroyer', 'weight': 1.0, 'id': 906},
 {'category': 'all', 'term': 'JS Kaga', 'weight': 1.0, 'id': 907},
 {'category': 'all', 'term': 'JS Izumo', 'weight': 1.0, 'id': 908},
 {'category': 'all', 'term': 'Maya-class destroyer', 'weight': 1.0, 'id': 909},
 {'category': 'all', 'term': 'Mogami-class frigate', 'weight': 1.0, 'id': 910},
 {'category': 'all', 'term': 'Taigei-class submarine', 'weight': 1.0, 'id': 911},
 {'category': 'all', 'term': 'Soryu-class submarine', 'weight': 1.0, 'id': 912},
 {'category': 'all', 'term': 'Type 10 tank', 'weight': 1.0, 'id': 913},
 {'category': 'all', 'term': 'Type 16 maneuver combat vehicle', 'weight': 1.0, 'id': 914},
 {'category': 'all', 'term': 'Type 12 missile', 'weight': 1.0, 'id': 915},
 {'category': 'all', 'term': 'F-2 fighter', 'weight': 1.0, 'id': 916},
 {'category': 'all', 'term': 'C-2 transport aircraft', 'weight': 1.0, 'id': 917},
 {'category': 'all', 'term': 'P-1 patrol aircraft', 'weight': 1.0, 'id': 918},
 {'category': 'all', 'term': 'Osprey deployment', 'weight': 1.0, 'id': 919},
 {'category': 'all', 'term': 'Okinawa base burden', 'weight': 1.0, 'id': 920},
 {'category': 'all', 'term': 'Yokosuka naval base', 'weight': 1.0, 'id': 921},
 {'category': 'all', 'term': 'Sasebo naval base', 'weight': 1.0, 'id': 922},
 {'category': 'all', 'term': 'Kadena Air Base', 'weight': 1.0, 'id': 923},
 {'category': 'all', 'term': 'Iwakuni base', 'weight': 1.0, 'id': 924},
 {'category': 'all', 'term': 'Henoko relocation', 'weight': 1.0, 'id': 925},
 {'category': 'all', 'term': 'Futenma relocation', 'weight': 1.0, 'id': 926},
 {'category': 'all', 'term': 'Naha Air Base', 'weight': 1.0, 'id': 927},
 {'category': 'all', 'term': 'Miyako Strait', 'weight': 1.0, 'id': 928},
 {'category': 'all', 'term': 'Tsushima Strait', 'weight': 1.0, 'id': 929},
 {'category': 'all', 'term': 'Senkaku Islands', 'weight': 1.0, 'id': 930},
 {'category': 'all', 'term': 'Takeshima dispute', 'weight': 1.0, 'id': 931},
 {'category': 'all', 'term': 'Northern Territories dispute', 'weight': 1.0, 'id': 932},
 {'category': 'all', 'term': 'Kuril dispute', 'weight': 1.0, 'id': 933},
 {'category': 'all', 'term': 'Megumi Yokota', 'weight': 1.0, 'id': 934},
 {'category': 'all', 'term': 'J-Alert system', 'weight': 1.0, 'id': 935},
 {'category': 'all', 'term': 'Fukushima treated water', 'weight': 1.0, 'id': 936},
 {'category': 'all', 'term': 'Fukushima decommissioning', 'weight': 1.0, 'id': 937},
 {'category': 'all', 'term': 'TEPCO Fukushima', 'weight': 1.0, 'id': 938},
 {'category': 'all', 'term': 'Onagawa nuclear plant', 'weight': 1.0, 'id': 939},
 {'category': 'all', 'term': 'Kashiwazaki-Kariwa', 'weight': 1.0, 'id': 940},
 {'category': 'all', 'term': 'Sendai nuclear plant', 'weight': 1.0, 'id': 941},
 {'category': 'all', 'term': 'Hamaoka nuclear plant', 'weight': 1.0, 'id': 942},
 {'category': 'all', 'term': 'Rokkasho reprocessing plant', 'weight': 1.0, 'id': 943},
 {'category': 'all', 'term': 'Monju reactor', 'weight': 1.0, 'id': 944},
 {'category': 'all', 'term': 'Abenomics', 'weight': 1.0, 'id': 945},
 {'category': 'all', 'term': 'BOJ Tankan', 'weight': 1.0, 'id': 946},
 {'category': 'all', 'term': 'YCC policy', 'weight': 1.0, 'id': 947},
 {'category': 'all', 'term': 'NISA account', 'weight': 1.0, 'id': 948},
 {'category': 'all', 'term': 'new NISA', 'weight': 1.0, 'id': 949},
 {'category': 'all', 'term': 'iDeCo', 'weight': 1.0, 'id': 950},
 {'category': 'all', 'term': 'GPIF', 'weight': 1.0, 'id': 951},
 {'category': 'all', 'term': 'Keidanren', 'weight': 1.0, 'id': 952},
 {'category': 'all', 'term': 'Rengo', 'weight': 1.0, 'id': 953},
 {'category': 'all', 'term': 'Shunto wage talks', 'weight': 1.0, 'id': 954},
 {'category': 'all', 'term': 'Furusato Nozei', 'weight': 1.0, 'id': 955},
 {'category': 'all', 'term': 'hometown tax donation', 'weight': 1.0, 'id': 956},
 {'category': 'all', 'term': 'J-REIT', 'weight': 1.0, 'id': 957},
 {'category': 'all', 'term': 'Mothers market', 'weight': 1.0, 'id': 958},
 {'category': 'all', 'term': 'TSE Prime', 'weight': 1.0, 'id': 959},
 {'category': 'all', 'term': 'TSE Standard', 'weight': 1.0, 'id': 960},
 {'category': 'all', 'term': 'TSE Growth', 'weight': 1.0, 'id': 961},
 {'category': 'all', 'term': 'Prime Market', 'weight': 1.0, 'id': 962},
 {'category': 'all', 'term': 'Tokyo Pro Market', 'weight': 1.0, 'id': 963},
 {'category': 'all', 'term': 'Nikkei 225', 'weight': 1.0, 'id': 964},
 {'category': 'all', 'term': 'JPX', 'weight': 1.0, 'id': 965},
 {'category': 'all', 'term': 'JPX Prime 150', 'weight': 1.0, 'id': 966},
 {'category': 'all', 'term': 'Osaka Exchange', 'weight': 1.0, 'id': 967},
 {'category': 'all', 'term': 'Nagoya Stock Exchange', 'weight': 1.0, 'id': 968},
 {'category': 'all', 'term': 'Fukuoka Stock Exchange', 'weight': 1.0, 'id': 969},
 {'category': 'all', 'term': 'Sapporo Securities Exchange', 'weight': 1.0, 'id': 970},
 {'category': 'all', 'term': 'JGB futures', 'weight': 1.0, 'id': 971},
 {'category': 'all', 'term': 'JGB yields', 'weight': 1.0, 'id': 972},
 {'category': 'all', 'term': 'super-long JGBs', 'weight': 1.0, 'id': 973},
 {'category': 'all', 'term': 'yen carry', 'weight': 1.0, 'id': 974},
 {'category': 'all', 'term': 'yen-buying intervention', 'weight': 1.0, 'id': 975},
 {'category': 'all', 'term': 'yen-selling intervention', 'weight': 1.0, 'id': 976},
 {'category': 'all', 'term': 'Tokyo CPI', 'weight': 1.0, 'id': 977},
 {'category': 'all', 'term': 'Tankan survey', 'weight': 1.0, 'id': 978},
 {'category': 'all', 'term': 'Tokyo Core CPI', 'weight': 1.0, 'id': 979},
 {'category': 'all', 'term': 'Tokyo condo prices', 'weight': 1.0, 'id': 980},
 {'category': 'all', 'term': 'Tokyo office vacancy', 'weight': 1.0, 'id': 981},
 {'category': 'all', 'term': 'Osaka Expo demand', 'weight': 1.0, 'id': 982},
 {'category': 'all', 'term': 'Keihanshin', 'weight': 1.0, 'id': 983},
 {'category': 'all', 'term': 'Greater Tokyo Area', 'weight': 1.0, 'id': 984},
 {'category': 'all', 'term': 'Shuto Expressway', 'weight': 1.0, 'id': 985},
 {'category': 'all', 'term': 'Toei Subway', 'weight': 1.0, 'id': 986},
 {'category': 'all', 'term': 'Yamanote Line', 'weight': 1.0, 'id': 987},
 {'category': 'all', 'term': 'Chuo Line', 'weight': 1.0, 'id': 988},
 {'category': 'all', 'term': 'Tokaido Shinkansen', 'weight': 1.0, 'id': 989},
 {'category': 'all', 'term': 'Sanyo Shinkansen', 'weight': 1.0, 'id': 990},
 {'category': 'all', 'term': 'Hokuriku Shinkansen', 'weight': 1.0, 'id': 991},
 {'category': 'all', 'term': 'Kyushu Shinkansen', 'weight': 1.0, 'id': 992},
 {'category': 'all', 'term': 'Hokkaido Shinkansen', 'weight': 1.0, 'id': 993},
 {'category': 'all', 'term': 'Akita Shinkansen', 'weight': 1.0, 'id': 994},
 {'category': 'all', 'term': 'Yamagata Shinkansen', 'weight': 1.0, 'id': 995},
 {'category': 'all', 'term': 'Linear Chuo project', 'weight': 1.0, 'id': 996},
 {'category': 'all', 'term': 'SCMaglev', 'weight': 1.0, 'id': 997},
 {'category': 'all', 'term': 'JR Freight', 'weight': 1.0, 'id': 998},
 {'category': 'all', 'term': 'JR Pass price', 'weight': 1.0, 'id': 999},
 {'category': 'all', 'term': 'Takuhaibin', 'weight': 1.0, 'id': 1000}]

BLOCKED_EXACT_NORMALIZED_TERMS = {'activist investor',
 'anti-conspiracy law',
 'arson attack',
 'asylum seeker',
 'avian flu',
 'base pay hike',
 'bear attacks',
 'birth rate',
 'board of audit',
 'bullet train pass',
 'cabinet approval',
 'cabinet office',
 'cabinet reshuffle',
 'cabinet secretariat',
 'cashless payments',
 'casino resort',
 'cedar pollen',
 'child allowance',
 'civil code',
 'coalition government',
 'communist party',
 'condo prices',
 'consumption tax',
 'convenience store chains',
 'core cpi',
 'corporate governance code',
 'critical minerals',
 'currency intervention',
 'current account surplus',
 'cyberattack',
 'data breach',
 'defense budget',
 'deflation',
 'delivery robotics',
 'development aid',
 'diet',
 'driver shortage',
 'east china sea',
 'election administration commission',
 'emergency alert',
 'employee pension',
 'empty homes',
 'energy security',
 'export control',
 'export controls',
 'fertility rate',
 'fiscal stimulus',
 'food poisoning',
 'food self-sufficiency',
 'foreign trainee',
 'government bonds',
 'health insurance premiums',
 'heavy rain warning',
 'house of councillors',
 'house of representatives',
 'household spending',
 'hydrogen strategy',
 'immigration control',
 'inbound tourism',
 'japan',
 'japanese',
 'labor shortage',
 'land prices',
 'landslide',
 'leadership election',
 'lng imports',
 'local assembly',
 'logistics crisis',
 'long-term care insurance',
 'lower house',
 'maglev',
 'management buyout',
 'mayoral election',
 'mbo',
 'microplastics policy',
 'minimum wage',
 'ministry of defense',
 'ministry of finance',
 'ministry of foreign affairs',
 'ministry of internal affairs',
 'ministry of justice',
 'mod',
 'national census',
 'national pension',
 'national police agency',
 'neet',
 'no-confidence motion',
 'nuclear restart',
 'nuclear safety inspection',
 'nursing care',
 'office worker',
 'official gazette',
 'offshore wind',
 'party president election',
 'peacekeeping operations',
 'penal code',
 'pension reform',
 'pfas contamination',
 'policy rate',
 'population decline',
 'prefectural governor',
 'primary balance',
 'proportional representation',
 'proxy fight',
 'public debt',
 'public prosecutors office',
 'rail pass',
 'ransomware attack',
 'rare earths',
 'real wages',
 'referendum',
 'reflation',
 'regional revitalization',
 'residence card',
 'resident registry',
 'rice prices',
 'rice shortage',
 'rural depopulation',
 'sales tax hike',
 'sanctions package',
 'security legislation',
 'security treaty',
 'semiconductor subsidies',
 'shareholder proposal',
 'single-person households',
 'single-seat districts',
 'smart city project',
 'snap election',
 'social democratic party',
 'social security costs',
 'space agency',
 'state secrecy law',
 'status of forces agreement',
 'stewardship code',
 'strategic dialogue',
 'strong yen',
 'supplementary budget',
 'supply chain resilience',
 'supreme court',
 'supreme courts',
 'tender offer',
 'tourism agency',
 'trade deficit',
 'trade surplus',
 'trading house',
 'trilateral summit',
 'tuition-free program',
 'typhoon',
 'ultra-low rates',
 'un security council bid',
 'upper house',
 'visa waiver',
 'volcanic eruption',
 'wage hike',
 'waterworks privatization',
 'weak yen'}
BLOCKED_COMPACT_TERMS = {"japan", "japanese", "playstation", "mod"}
BLOCKED_SUBSTRINGS_COMPACT = {"playstation", "japanese", "japan"}


def normalize_term_for_blocklist(term: str) -> str:
    t = unicodedata.normalize("NFKC", str(term or "")).lower()
    t = re.sub(r"[‐-―\-/_.:+|#?=&%]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_blocked_term(term: str) -> bool:
    """Hard block terms that should never appear in scoring or Matched terms."""
    norm = normalize_term_for_blocklist(term)
    compact = norm.replace(" ", "")
    if norm in BLOCKED_EXACT_NORMALIZED_TERMS or compact in BLOCKED_COMPACT_TERMS:
        return True
    if any(bad in compact for bad in BLOCKED_SUBSTRINGS_COMPACT):
        return True
    # Block only explicitly forbidden standalone short tokens.
    # Do not compare every token against the broad exact blocklist; otherwise
    # valid Japan-specific phrases such as "National Diet" get removed because
    # they contain the generic blocked token "diet".
    BLOCKED_TOKEN_TERMS = {"mod", "neet"}
    tokens = set(re.findall(r"[a-z0-9]+", norm))
    if tokens & BLOCKED_TOKEN_TERMS:
        return True
    return False


_RAW_TERM_COUNT = len(JAPAN_SPECIFIC_TERMS_1000_RECORDS)
JAPAN_SPECIFIC_TERMS_1000_RECORDS = [
    r for r in JAPAN_SPECIFIC_TERMS_1000_RECORDS
    if not is_blocked_term(str(r.get("term", "")))
]
JAPAN_SPECIFIC_TERMS_1000 = [r["term"] for r in JAPAN_SPECIFIC_TERMS_1000_RECORDS]
TERM_CATEGORY_BY_TERM = {r["term"]: r["category"] for r in JAPAN_SPECIFIC_TERMS_1000_RECORDS}
TERM_WEIGHT_BY_TERM = {r["term"]: float(r.get("weight", 1.0)) for r in JAPAN_SPECIFIC_TERMS_1000_RECORDS}
REPEAT_DECAY_FACTOR = 0.5
assert all(not is_blocked_term(t) for t in JAPAN_SPECIFIC_TERMS_1000)

# Conservative initial RSS list.
# Excluded from this initial list: Guardian, Washington Post, Le Monde, Google News RSS, Reuters RSS.
RSS_FEEDS = [{'category': 'top', 'source': 'BBC News', 'url': 'http://feeds.bbci.co.uk/news/rss.xml', 'weight': 5},
 {'category': 'world', 'source': 'BBC News', 'url': 'http://feeds.bbci.co.uk/news/world/rss.xml', 'weight': 5},
 {'category': 'business', 'source': 'BBC News', 'url': 'http://feeds.bbci.co.uk/news/business/rss.xml', 'weight': 4},
 {'category': 'technology',
  'source': 'BBC News',
  'url': 'http://feeds.bbci.co.uk/news/technology/rss.xml',
  'weight': 4},
 {'category': 'entertainment',
  'source': 'BBC News',
  'url': 'http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml',
  'weight': 3},
 {'category': 'top', 'source': 'ABC News', 'url': 'https://feeds.abcnews.com/abcnews/topstories', 'weight': 4},
 {'category': 'international',
  'source': 'ABC News',
  'url': 'https://feeds.abcnews.com/abcnews/internationalheadlines',
  'weight': 5},
 {'category': 'business', 'source': 'ABC News', 'url': 'https://feeds.abcnews.com/abcnews/moneyheadlines', 'weight': 3},
 {'category': 'technology',
  'source': 'ABC News',
  'url': 'https://feeds.abcnews.com/abcnews/technologyheadlines',
  'weight': 3},
 {'category': 'entertainment',
  'source': 'ABC News',
  'url': 'https://feeds.abcnews.com/abcnews/entertainmentheadlines',
  'weight': 3},
 {'category': 'travel', 'source': 'ABC News', 'url': 'https://feeds.abcnews.com/abcnews/travelheadlines', 'weight': 3},
 {'category': 'top', 'source': 'CBS News', 'url': 'https://www.cbsnews.com/latest/rss/main', 'weight': 4},
 {'category': 'world', 'source': 'CBS News', 'url': 'https://www.cbsnews.com/latest/rss/world', 'weight': 4},
 {'category': 'moneywatch', 'source': 'CBS News', 'url': 'https://www.cbsnews.com/latest/rss/moneywatch', 'weight': 3},
 {'category': 'science', 'source': 'CBS News', 'url': 'https://www.cbsnews.com/latest/rss/science', 'weight': 3},
 {'category': 'technology', 'source': 'CBS News', 'url': 'https://www.cbsnews.com/latest/rss/technology', 'weight': 3},
 {'category': 'entertainment',
  'source': 'CBS News',
  'url': 'https://www.cbsnews.com/latest/rss/entertainment',
  'weight': 3},
 {'category': 'news',
  'source': 'Euronews',
  'url': 'https://www.euronews.com/rss?format=mrss&level=theme&name=news',
  'weight': 4},
 {'category': 'travel',
  'source': 'Euronews',
  'url': 'https://www.euronews.com/rss?format=mrss&level=theme&name=travel',
  'weight': 3},
 {'category': 'culture',
  'source': 'Euronews',
  'url': 'https://www.euronews.com/rss?format=mrss&level=theme&name=culture',
  'weight': 3},
 {'category': 'technology',
  'source': 'Euronews',
  'url': 'https://www.euronews.com/rss?format=mrss&level=theme&name=next',
  'weight': 3},
 {'category': 'all', 'source': 'DW', 'url': 'https://rss.dw.com/rdf/rss-en-all', 'weight': 4},
 {'category': 'all', 'source': 'France 24', 'url': 'https://www.france24.com/en/rss', 'weight': 4},
 {'category': 'asia-pacific',
  'source': 'France 24',
  'url': 'https://www.france24.com/en/asia-pacific/rss',
  'weight': 4},
 {'category': 'business-tech',
  'source': 'France 24',
  'url': 'https://www.france24.com/en/business-tech/rss',
  'weight': 3},
 {'category': 'culture', 'source': 'France 24', 'url': 'https://www.france24.com/en/culture/rss', 'weight': 3},
 {'category': 'all', 'source': 'Al Jazeera', 'url': 'https://www.aljazeera.com/xml/rss/all.xml', 'weight': 4},
 {'category': 'tech', 'source': 'The Verge', 'url': 'https://www.theverge.com/rss/index.xml', 'weight': 4},
 {'category': 'technology',
  'source': 'Ars Technica',
  'url': 'https://feeds.arstechnica.com/arstechnica/index',
  'weight': 3},
 {'category': 'technology', 'source': 'Engadget', 'url': 'https://www.engadget.com/rss.xml', 'weight': 3},
 {'category': 'gaming', 'source': 'Polygon', 'url': 'https://www.polygon.com/rss/index.xml', 'weight': 4},
 {'category': 'gaming', 'source': 'Kotaku', 'url': 'https://kotaku.com/rss', 'weight': 4},
 {'category': 'gaming', 'source': 'IGN', 'url': 'https://feeds.feedburner.com/ign/all', 'weight': 4},
 {'category': 'gaming', 'source': 'GameSpot', 'url': 'https://www.gamespot.com/feeds/mashup/', 'weight': 3},
 {'category': 'anime',
  'source': 'Anime News Network',
  'url': 'https://www.animenewsnetwork.com/all/rss.xml',
  'weight': 5},
 {'category': 'anime', 'source': 'Crunchyroll News', 'url': 'https://www.crunchyroll.com/news/rss', 'weight': 4},
 {'category': 'travel', 'source': 'BBC Travel', 'url': 'https://www.bbc.com/travel/feed.rss', 'weight': 4},
 {'category': 'travel', 'source': 'Travel + Leisure', 'url': 'https://www.travelandleisure.com/feed', 'weight': 3},
 {'category': 'travel', 'source': 'Lonely Planet', 'url': 'https://www.lonelyplanet.com/news/feed', 'weight': 3},
 {'category': 'travel-business', 'source': 'Skift', 'url': 'https://skift.com/feed/', 'weight': 3},
 {'category': 'markets',
  'source': 'MarketWatch',
  'url': 'https://feeds.marketwatch.com/marketwatch/topstories/',
  'weight': 3},
 {'category': 'finance', 'source': 'Yahoo Finance', 'url': 'https://finance.yahoo.com/news/rssindex', 'weight': 3}]

CATEGORY_BONUS = {
    "anime": 8,
    "gaming": 6,
    "culture": 5,
    "travel": 5,
    "travel-business": 4,
    "asia-pacific": 4,
    "business": 3,
    "business-tech": 3,
    "markets": 3,
    "finance": 3,
    "technology": 3,
    "tech": 3,
    "entertainment": 3,
    "world": 2,
    "international": 2,
    "top": 1,
    "all": 1,
}

NOISY_LOW_WEIGHT_TERMS = {
    "sake": 0.25,       # for the sake of
    "fuji": 0.5,        # names/products can collide
    "ninja": 0.7,
    "samurai": 0.7,
    "diet": 0.6,        # National Diet vs food diet
    "sharp": 0.6,
    "canon": 0.6,
    "temple": 0.8,
    "shrine": 0.8,
    "volcano": 0.8,
    "earthquake": 0.8,
    "typhoon": 0.8,
}

SCRIPT_BLOCK_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿"
    r"가-힯Ѐ-ӿ؀-ۿ"
    r"฀-๿Ͱ-Ͽ]"
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; JapanBuzzRSSRanker/1.0; "
    "+https://example.com/rss-japan-buzz-ranker)"
)


def is_probably_english_title(title: str) -> bool:
    """Lightweight English-title filter without external language libraries."""
    if not title:
        return False
    t = unicodedata.normalize("NFKC", title).strip()
    if len(t) < 8:
        return False
    if SCRIPT_BLOCK_RE.search(t):
        return False
    letters = [ch for ch in t if ch.isalpha()]
    if len(letters) < 4:
        return False
    ascii_letters = [ch for ch in letters if "a" <= ch.lower() <= "z"]
    ascii_ratio = len(ascii_letters) / max(1, len(letters))
    return ascii_ratio >= 0.85


def clean_url_for_dedupe(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        filtered_query = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            kl = k.lower()
            if kl.startswith("utm_") or kl in {"fbclid", "gclid", "mc_cid", "mc_eid"}:
                continue
            filtered_query.append((k, v))
        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        ))
    except Exception:
        return url.strip().lower()


def normalize_for_matching(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    # Keep a raw-ish version but make URL separators and punctuation searchable as spaces.
    text = re.sub(r"[‐-―\-/_.:+|#?=&%]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def term_pattern(term: str) -> re.Pattern:
    t = normalize_for_matching(term)
    # Allow spaces/hyphen-like separators between phrase parts.
    escaped = re.escape(t).replace(r"\ ", r"[\s\-_/]+")
    return re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])", re.IGNORECASE)


@st.cache_resource(show_spinner=False)
def compiled_term_patterns() -> list[tuple[str, str, float, re.Pattern]]:
    return [
        (
            str(r["term"]),
            str(r.get("category", "")),
            float(r.get("weight", 1.0)),
            term_pattern(str(r["term"])),
        )
        for r in JAPAN_SPECIFIC_TERMS_1000_RECORDS
    ]


def parse_datetime_string(value: str) -> datetime | None:
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        dt = parsedate_to_datetime(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        iso = v.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_entry_datetime(entry: Any) -> datetime | None:
    dt = entry.get("published_dt")
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    for key in ("published", "updated", "created", "pubDate", "dc_date"):
        parsed = parse_datetime_string(entry.get(key, ""))
        if parsed:
            return parsed
    return None


def entry_tags_text(entry: Any) -> str:
    tags = []
    for tag in entry.get("tags", []) or []:
        if isinstance(tag, dict):
            term = tag.get("term") or tag.get("label")
        else:
            term = str(tag)
        if term:
            tags.append(str(term))
    return " ".join(tags)


def build_scoring_text(item: dict[str, Any], include_summary_for_scoring: bool) -> str:
    parts = [
        item.get("title", ""),
        item.get("link", ""),
        item.get("source", ""),
        item.get("feed_category", ""),
        item.get("feed_title", ""),
        item.get("tags", ""),
    ]
    if include_summary_for_scoring:
        # Used only internally for scoring; not displayed as article content.
        parts.append(item.get("summary", ""))
    return normalize_for_matching(" ".join(str(x) for x in parts if x))


def decayed_occurrence_score(n: int, decay_factor: float = REPEAT_DECAY_FACTOR) -> float:
    """Return 1 + 1/2 + 1/4 + ... for repeated appearances of one term."""
    if n <= 0:
        return 0.0
    # Closed form for geometric series; avoids loops and makes the scoring explicit.
    if decay_factor == 1.0:
        return float(n)
    return (1.0 - (decay_factor ** n)) / (1.0 - decay_factor)


def count_japan_terms(item: dict[str, Any], include_summary_for_scoring: bool) -> dict[str, Any]:
    text = build_scoring_text(item, include_summary_for_scoring)
    total_hits = 0
    decayed_weighted_hits = 0.0
    matched_terms: list[str] = []
    matched_terms_with_counts: list[str] = []
    term_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    term_decayed_scores: dict[str, float] = {}

    for term, category, base_weight, pat in compiled_term_patterns():
        # Defensive hard block: even if an old CSV/app cache exists, these terms never score or display.
        if is_blocked_term(term):
            continue
        n = len(pat.findall(text))
        if n <= 0:
            continue
        total_hits += n

        noise_weight = NOISY_LOW_WEIGHT_TERMS.get(term.lower(), 1.0)
        term_score = base_weight * noise_weight * decayed_occurrence_score(n)
        decayed_weighted_hits += term_score

        matched_terms.append(term)
        term_counts[term] = n
        term_decayed_scores[term] = term_score
        category_counts[category] = category_counts.get(category, 0) + 1
        matched_terms_with_counts.append(f"{term}×{n}={term_score:.2f}")

    return {
        "term_total_hits": total_hits,
        "term_decayed_hits": decayed_weighted_hits,
        "term_weighted_hits": decayed_weighted_hits,  # backward-compatible internal name
        "unique_term_hits": len(matched_terms),
        "matched_terms": matched_terms,
        "matched_terms_with_counts": matched_terms_with_counts,
        "term_counts": term_counts,
        "term_decayed_scores": term_decayed_scores,
        "category_counts": category_counts,
    }


JAPAN_ANCHOR_PATTERN = re.compile(r"(?<![a-z0-9])(?:japan|japanese)(?![a-z0-9])", re.IGNORECASE)


def count_japan_anchor_bonus(item: dict[str, Any]) -> dict[str, Any]:
    """Return a small capped bonus when Japan/Japanese appears outside the 1000 terms.

    Japan/Japanese are intentionally not part of the Japan-specific term list.
    If either appears in title / URL / RSS metadata, the article receives +0.5 at most.
    The two words are counted jointly, so repeated appearances do not increase the bonus.
    Summary/body text is intentionally excluded from this anchor bonus.
    """
    text = build_scoring_text(item, include_summary_for_scoring=False)
    found = bool(JAPAN_ANCHOR_PATTERN.search(text))
    return {
        "japan_anchor_hit": int(found),
        "japan_anchor_bonus": 0.5 if found else 0.0,
    }


def xml_text(parent: ET.Element | None, names: list[str]) -> str:
    if parent is None:
        return ""
    wanted = {n.lower() for n in names}
    for child in list(parent):
        local = child.tag.split("}")[-1].lower()
        full = child.tag.lower()
        if local in wanted or full in wanted:
            text = "".join(child.itertext()).strip()
            if text:
                return html.unescape(text)
    return ""


def xml_link(parent: ET.Element | None) -> str:
    if parent is None:
        return ""
    # RSS: <link>https://...</link>
    txt = xml_text(parent, ["link"])
    if txt and not txt.lower().startswith("mailto:"):
        return txt
    # Atom: <link href="..." rel="alternate"/>
    for child in list(parent):
        local = child.tag.split("}")[-1].lower()
        if local == "link":
            href = child.attrib.get("href", "")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in {"alternate", "", None}:
                return html.unescape(href.strip())
    return ""


def xml_categories(parent: ET.Element | None) -> list[dict[str, str]]:
    if parent is None:
        return []
    out: list[dict[str, str]] = []
    for child in list(parent):
        local = child.tag.split("}")[-1].lower()
        if local in {"category", "subject"}:
            value = (child.attrib.get("term") or child.attrib.get("label") or "".join(child.itertext())).strip()
            if value:
                out.append({"term": html.unescape(value)})
    return out


def parse_feed_xml(content: bytes) -> SimpleNamespace:
    root = ET.fromstring(content)
    root_local = root.tag.split("}")[-1].lower()

    feed_title = xml_text(root, ["title"])
    entries: list[dict[str, Any]] = []

    if root_local == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            channel = root
        feed_title = xml_text(channel, ["title"]) or feed_title
        raw_items = channel.findall("item")
    else:
        # Atom or RDF-like feeds: collect direct/descendant entry/item elements.
        raw_items = [el for el in root.iter() if el.tag.split("}")[-1].lower() in {"entry", "item"}]

    for item in raw_items:
        published_raw = (
            xml_text(item, ["pubDate", "published", "updated", "created", "date"])
            or xml_text(item, ["dc:date"])
        )
        entry = {
            "title": xml_text(item, ["title"]),
            "link": xml_link(item),
            "summary": xml_text(item, ["summary", "description", "encoded"]),
            "description": xml_text(item, ["description"]),
            "published": published_raw,
            "updated": xml_text(item, ["updated"]),
            "created": xml_text(item, ["created"]),
            "tags": xml_categories(item),
            "published_dt": parse_datetime_string(published_raw) if published_raw else None,
        }
        entries.append(entry)

    return SimpleNamespace(feed={"title": feed_title}, entries=entries)


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_feed(url: str, timeout_sec: int, auto_update_key: str) -> tuple[Any | None, str | None]:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout_sec)
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        content = resp.content or b""
        if not content.strip():
            return None, "empty response"
        try:
            parsed = parse_feed_xml(content)
        except Exception as e:
            return None, f"XML parse error: {e}"
        if not parsed.entries:
            return None, "no entries found"
        return parsed, None
    except Exception as e:
        return None, repr(e)


def recency_bonus(published_dt: datetime | None, now: datetime, lookback_hours: int) -> float:
    if published_dt is None:
        return 0.0
    age_h = max(0.0, (now - published_dt).total_seconds() / 3600)
    if age_h > lookback_hours:
        return 0.0
    # 0-6 points. Fresh items get more.
    return max(0.0, 6.0 * (1.0 - age_h / max(1, lookback_hours)))


def feed_position_bonus(position: int) -> float:
    # Top of a feed usually means editorial importance.
    return max(0.0, 10.0 - min(position, 10))


def build_item_from_entry(feed_cfg: dict[str, Any], feed_title: str, entry: Any, position: int) -> dict[str, Any]:
    published_dt = parse_entry_datetime(entry)
    link = entry.get("link", "") or ""
    summary = entry.get("summary", "") or entry.get("description", "") or ""
    return {
        "source": feed_cfg["source"],
        "feed_category": feed_cfg["category"],
        "feed_url": feed_cfg["url"],
        "source_weight": float(feed_cfg.get("weight", 1)),
        "feed_title": feed_title,
        "feed_position": int(position),
        "title": (entry.get("title", "") or "").strip(),
        "link": link,
        "dedupe_url": clean_url_for_dedupe(link),
        "summary": re.sub(r"\s+", " ", summary).strip(),
        "tags": entry_tags_text(entry),
        "published_dt": published_dt,
        "published": published_dt.isoformat() if published_dt else "",
    }


def collect_and_rank(
    selected_sources: list[str],
    selected_categories: list[str],
    lookback_hours: int,
    max_entries_per_feed: int,
    request_interval_sec: float,
    timeout_sec: int,
    include_undated: bool,
    include_summary_for_scoring: bool,
    min_unique_terms: int,
    ranking_limit: int,
    auto_update_key: str = "",
    show_progress: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    feeds = [f for f in RSS_FEEDS if f["source"] in selected_sources and f["category"] in selected_categories]
    stats: dict[str, int] = {
        "selected_feeds": len(feeds),
        "feeds_ok": 0,
        "feeds_error": 0,
        # Number of RSS entries pulled from selected feeds before title/date/match filtering.
        "rss_entries_seen": 0,
        # Number of entries after English/date/dedupe filters, before Japan relevance scoring.
        "articles_checked": 0,
        # Number of articles that passed Japan-term or Japan/Japanese-anchor relevance checks before ranking cut.
        "ranking_candidates": 0,
    }

    progress = st.progress(0, text="ニュース情報を収集中…（約30秒）") if (feeds and show_progress) else None
    total_feeds = max(1, len(feeds))

    for feed_i, cfg in enumerate(feeds):
        if request_interval_sec > 0 and feed_i > 0:
            time.sleep(request_interval_sec)

        parsed, err = fetch_feed(cfg["url"], timeout_sec=timeout_sec, auto_update_key=auto_update_key)
        if err:
            stats["feeds_error"] += 1
            errors.append({"source": cfg["source"], "category": cfg["category"], "url": cfg["url"], "error": err})
            if progress:
                progress.progress((feed_i + 1) / total_feeds, text=f"RSS巡回中... {feed_i + 1}/{len(feeds)}")
            continue

        feed_title = ""
        try:
            feed_title = parsed.feed.get("title", "") if parsed and hasattr(parsed, "feed") else ""
        except Exception:
            feed_title = ""

        stats["feeds_ok"] += 1
        entries = list(parsed.entries or [])[:max_entries_per_feed]
        stats["rss_entries_seen"] += len(entries)
        for pos, entry in enumerate(entries):
            item = build_item_from_entry(cfg, feed_title, entry, pos)
            title = item["title"]
            if not is_probably_english_title(title):
                continue

            published_dt = item["published_dt"]
            if published_dt is None:
                if not include_undated:
                    continue
            elif published_dt < cutoff or published_dt > now + timedelta(hours=2):
                continue

            dedupe_key = item["dedupe_url"] or normalize_for_matching(title)
            if not dedupe_key or dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            stats["articles_checked"] += 1

            hit = count_japan_terms(item, include_summary_for_scoring)
            anchor = count_japan_anchor_bonus(item)

            has_japan_terms = hit["term_total_hits"] > 0 and hit["unique_term_hits"] >= min_unique_terms
            has_anchor_only_candidate = anchor["japan_anchor_hit"] == 1
            if not has_japan_terms and not has_anchor_only_candidate:
                continue

            cat_bonus = CATEGORY_BONUS.get(str(cfg["category"]).lower(), 0)
            r_bonus = recency_bonus(published_dt, now, lookback_hours)
            p_bonus = feed_position_bonus(pos)
            source_weight = float(cfg.get("weight", 1))

            if has_japan_terms:
                # Main objective: Japan-specific term density, plus capped Japan/Japanese anchor bonus and RSS editorial prominence.
                term_score_component = 3.0 * hit["term_weighted_hits"]
                unique_term_component = 5.0 * hit["unique_term_hits"]
                anchor_bonus_component = anchor["japan_anchor_bonus"]
                source_component = 1.0 * source_weight
                category_component = 0.8 * cat_bonus
                feed_position_component = 0.7 * p_bonus
                recency_component = 0.8 * r_bonus
                score = (
                    term_score_component
                    + unique_term_component
                    + anchor_bonus_component
                    + source_component
                    + category_component
                    + feed_position_component
                    + recency_component
                )
                anchor_only = 0
            else:
                # If Japan/Japanese is the only Japan-related signal, keep it visible but intentionally weak.
                # Source/category/position/recency bonuses are not added here, so anchor-only articles do not dominate.
                term_score_component = 0.0
                unique_term_component = 0.0
                anchor_bonus_component = anchor["japan_anchor_bonus"]
                source_component = 0.0
                category_component = 0.0
                feed_position_component = 0.0
                recency_component = 0.0
                score = anchor_bonus_component  # capped at 0.5 by count_japan_anchor_bonus()
                anchor_only = 1

            stats["ranking_candidates"] += 1

            rows.append({
                # raw_score is the score before the cross-article category-diversity decay.
                "raw_score": round(score, 2),
                "score": round(score, 2),
                "category_decay_rank": 1,
                "category_decay_multiplier": 1.0,
                "term_score_component": round(term_score_component, 2),
                "unique_term_component": round(unique_term_component, 2),
                "anchor_bonus_component": round(anchor_bonus_component, 2),
                "source_component": round(source_component, 2),
                "category_component": round(category_component, 2),
                "feed_position_component": round(feed_position_component, 2),
                "recency_component": round(recency_component, 2),
                "term_total_hits": hit["term_total_hits"],
                "term_decayed_hits": round(hit["term_decayed_hits"], 2),
                "unique_term_hits": hit["unique_term_hits"],
                "japan_anchor_hit": anchor["japan_anchor_hit"],
                "japan_anchor_bonus": anchor["japan_anchor_bonus"],
                "anchor_only": anchor_only,
                "matched_terms": ", ".join(hit["matched_terms_with_counts"][:18]),
                "term_categories": ", ".join(f"{k}:{v}" for k, v in sorted(hit["category_counts"].items())),
                "title": title,
                "source": item["source"],
                "category": item["feed_category"],
                "published": item["published"],
                "feed_position": item["feed_position"] + 1,
                "source_weight": source_weight,
                "category_bonus_raw": cat_bonus,
                "feed_position_bonus_raw": round(p_bonus, 2),
                "recency_bonus_raw": round(r_bonus, 2),
                "url": item["link"],
            })

        if progress:
            progress.progress((feed_i + 1) / total_feeds, text=f"RSS巡回中... {feed_i + 1}/{len(feeds)}")

    if progress:
        progress.empty()

    df = pd.DataFrame(rows)
    if not df.empty:
        # 1) Sort by raw score within each RSS category.
        # 2) Apply category-diversity decay: for each already-higher raw-score article
        #    in the same category, halve the score.
        #    Example: category top=1.0x, second=0.5x, third=0.25x.
        raw_sort_cols = ["raw_score", "term_decayed_hits", "term_total_hits", "unique_term_hits", "published"]
        df = df.sort_values(by=raw_sort_cols, ascending=[False, False, False, False, False]).reset_index(drop=True)
        df["category_decay_rank"] = df.groupby("category", sort=False).cumcount() + 1
        df["category_decay_multiplier"] = 0.5 ** (df["category_decay_rank"] - 1)
        df["score"] = (df["raw_score"] * df["category_decay_multiplier"]).round(2)

        # 3) Global ranking after category decay.
        df = df.sort_values(
            by=["score", "raw_score", "term_decayed_hits", "term_total_hits", "unique_term_hits", "published"],
            ascending=[False, False, False, False, False, False],
        ).head(ranking_limit).reset_index(drop=True)
        df.insert(0, "rank", range(1, len(df) + 1))

    err_df = pd.DataFrame(errors)
    return df, err_df, stats


def get_jst_auto_update_key(now_utc: datetime | None = None) -> str:
    """Return a daily cache key that flips at 04:00 JST.

    Example:
    - 2026-06-17 03:59 JST -> 2026-06-16-04JST
    - 2026-06-17 04:00 JST -> 2026-06-17-04JST
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    if now_jst.hour < AUTO_UPDATE_HOUR_JST:
        effective_date = (now_jst.date() - timedelta(days=1)).isoformat()
    else:
        effective_date = now_jst.date().isoformat()
    return f"{effective_date}-{AUTO_UPDATE_HOUR_JST:02d}JST"


def get_next_jst_auto_update_text(now_utc: datetime | None = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_jst = now_utc.astimezone(JST)
    next_jst = now_jst.replace(hour=AUTO_UPDATE_HOUR_JST, minute=0, second=0, microsecond=0)
    if now_jst >= next_jst:
        next_jst += timedelta(days=1)
    return next_jst.strftime("%Y-%m-%d %H:%M JST")


@st.cache_data(show_spinner=False)
def collect_and_rank_cached(
    auto_update_key: str,
    selected_sources: tuple[str, ...],
    selected_categories: tuple[str, ...],
    lookback_hours: int,
    max_entries_per_feed: int,
    request_interval_sec: float,
    timeout_sec: int,
    include_undated: bool,
    include_summary_for_scoring: bool,
    min_unique_terms: int,
    ranking_limit: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    return collect_and_rank(
        selected_sources=list(selected_sources),
        selected_categories=list(selected_categories),
        lookback_hours=lookback_hours,
        max_entries_per_feed=max_entries_per_feed,
        request_interval_sec=request_interval_sec,
        timeout_sec=timeout_sec,
        include_undated=include_undated,
        include_summary_for_scoring=include_summary_for_scoring,
        min_unique_terms=min_unique_terms,
        ranking_limit=ranking_limit,
        auto_update_key=auto_update_key,
        show_progress=False,
    )


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, quoting=csv.QUOTE_MINIMAL).encode("utf-8-sig")


def build_google_translate_url(title: str) -> str:
    """Build a Google Translate web URL. This does not call/scrape Google Translate."""
    text = quote_plus(str(title or ""))
    return f"https://translate.google.com/?sl=en&tl=ja&text={text}&op=translate"


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def translate_title_libre_cached(
    title: str,
    endpoint: str,
    api_key: str = "",
    timeout_sec: int = 10,
) -> str:
    """Translate one title using a LibreTranslate-compatible /translate endpoint.

    The app does not bundle or call any paid AI API. If endpoint is blank, translation is skipped.
    Expected response format: {"translatedText": "..."}.
    """
    title = str(title or "").strip()
    endpoint = str(endpoint or "").strip()
    if not title or not endpoint:
        return ""

    url = endpoint.rstrip("/")
    if not url.endswith("/translate"):
        url = url + "/translate"

    payload: dict[str, Any] = {
        "q": title,
        "source": "en",
        "target": "ja",
        "format": "text",
    }
    if api_key:
        payload["api_key"] = api_key

    try:
        r = requests.post(url, json=payload, timeout=timeout_sec)
        if r.status_code >= 400:
            return ""
        data = r.json()
        translated = data.get("translatedText") or data.get("translated_text") or ""
        return str(translated).strip()
    except Exception:
        return ""


def add_translation_columns(
    df: pd.DataFrame,
    mode: str,
    endpoint: str,
    api_key: str,
    timeout_sec: int,
) -> pd.DataFrame:
    out = df.copy()
    out["translate_url"] = out["title"].apply(build_google_translate_url)
    out["title_ja"] = ""

    if mode == "LibreTranslate互換APIで和訳" and endpoint.strip():
        translated: list[str] = []
        for title in out["title"].fillna("").astype(str).tolist():
            translated.append(
                translate_title_libre_cached(
                    title=title,
                    endpoint=endpoint.strip(),
                    api_key=api_key.strip(),
                    timeout_sec=timeout_sec,
                )
            )
            time.sleep(0.03)
        out["title_ja"] = translated

    return out


def get_configured_admin_password() -> str:
    """Read the admin password from Streamlit Secrets.

    Supported formats:
      ADMIN_PASSWORD = "..."
      admin_password = "..."
      [admin]
password = "..."
    """
    candidates: list[str] = []
    try:
        candidates.append(str(st.secrets.get("ADMIN_PASSWORD", "")))
    except Exception:
        pass
    try:
        candidates.append(str(st.secrets.get("admin_password", "")))
    except Exception:
        pass
    try:
        admin_cfg = st.secrets.get("admin", {})
        if isinstance(admin_cfg, dict):
            candidates.append(str(admin_cfg.get("password", "")))
        else:
            candidates.append(str(getattr(admin_cfg, "password", "")))
    except Exception:
        pass
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate:
            return candidate
    return ""


def render_admin_breakdown(df: pd.DataFrame, err_df: pd.DataFrame, stats: dict[str, int]) -> None:
    """Render scoring details only in the password-protected admin view."""
    st.markdown("---")
    st.subheader("管理者画面：スコア内訳")
    st.caption(
        "通常記事: raw_score = 特有語点 + ユニーク語点 + Japan/Japanese補助 + 媒体点 + カテゴリ点 + RSS掲載順位点 + 新着点。"
        "最終score = raw_score × カテゴリ分散係数。Japan/Japaneseだけの記事は0.5点固定です。"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("検索対象記事数", f"{int(stats.get('rss_entries_seen', 0)):,}")
    c2.metric("英語記事数", f"{int(stats.get('articles_checked', 0)):,}")
    c3.metric("候補数", f"{int(stats.get('ranking_candidates', 0)):,}")
    c4.metric("表示件数", f"{len(df):,}")

    if df.empty:
        st.info("候補がありません。")
    else:
        admin_cols = [
            "rank",
            "score",
            "raw_score",
            "category_decay_rank",
            "category_decay_multiplier",
            "term_score_component",
            "unique_term_component",
            "anchor_bonus_component",
            "source_component",
            "category_component",
            "feed_position_component",
            "recency_component",
            "anchor_only",
            "term_total_hits",
            "term_decayed_hits",
            "unique_term_hits",
            "japan_anchor_hit",
            "matched_terms",
            "title",
            "source",
            "category",
            "published",
            "feed_position",
            "url",
        ]
        existing_cols = [c for c in admin_cols if c in df.columns]
        admin_df = df[existing_cols].copy()
        st.dataframe(
            admin_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("url"),
                "score": st.column_config.NumberColumn("score", format="%.2f"),
                "raw_score": st.column_config.NumberColumn("raw_score", format="%.2f"),
                "category_decay_multiplier": st.column_config.NumberColumn("category_decay_multiplier", format="%.4f"),
            },
        )
        st.download_button(
            "管理者用CSVをダウンロード",
            data=df_to_csv_bytes(df),
            file_name="japan_news_ranking_admin_breakdown.csv",
            mime="text/csv",
        )

    if not err_df.empty:
        with st.expander("RSS取得エラー", expanded=False):
            st.dataframe(err_df, use_container_width=True, hide_index=True)


def render_result_cards(df: pd.DataFrame) -> None:
    """Render only a linked ranking: rank number + title plus source link."""
    items: list[str] = []
    for _, row in df.iterrows():
        title = html.escape(str(row.get("title", "") or ""))
        source = html.escape(str(row.get("source", "") or ""))
        display_text = f"{title} ({source})" if source else title
        url = html.escape(str(row.get("url", "") or ""), quote=True)
        if url:
            items.append(
                f'<li><a href="{url}" target="_blank" rel="noopener noreferrer">{display_text}</a></li>'
            )
        else:
            items.append(f"<li>{display_text}</li>")

    if items:
        st.markdown("<ol>" + "".join(items) + "</ol>", unsafe_allow_html=True)


def render_news_ranking_page() -> None:
    st.title("海外で話題の日本のニュース")
    st.markdown(
        """
        <div style="color:#000000; font-size:0.95rem; line-height:1.6; margin-top:0.25rem; margin-bottom:1rem;">
            <div>海外ニュースサイトを周回して、日本のニュースと思われる記事を独自アルゴリズムでランキング化しています。</div>
            <div>日本以外のニュースもわずかに含まれます。</div>
            <div>翻訳機能は無いので、ブラウザの日本語翻訳などをお使いください。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Public users can only run the ranking with fixed/default settings.
    # All configuration controls are shown only after admin authentication.
    lookback_hours = FIXED_LOOKBACK_HOURS
    max_entries_per_feed = FIXED_MAX_ENTRIES_PER_FEED
    request_interval_sec = FIXED_REQUEST_INTERVAL_SEC
    timeout_sec = 12
    include_undated = False
    include_summary_for_scoring = True
    min_unique_terms = 1
    ranking_limit = MAX_RANKING_LIMIT

    sources = sorted({f["source"] for f in RSS_FEEDS})
    categories = sorted({f["category"] for f in RSS_FEEDS})
    selected_sources = sources
    selected_categories = categories

    configured_admin_password = get_configured_admin_password()
    admin_panel_requested = False
    admin_password_ok = False

    auto_update_key = get_jst_auto_update_key()
    force_refresh_requested = False

    with st.sidebar:
        if configured_admin_password:
            st.markdown("---")
            admin_panel_requested = st.checkbox("管理者画面を開く", value=False)
            if admin_panel_requested:
                entered_admin_password = st.text_input("管理者パスワード", type="password")
                admin_password_ok = hmac.compare_digest(entered_admin_password, configured_admin_password)
                if entered_admin_password and not admin_password_ok:
                    st.error("管理者パスワードが違います。")

            if admin_password_ok:
                st.markdown("---")
                st.header("管理者設定")
                st.caption(f"app version: {APP_VERSION}")
                st.caption(f"自動更新: 毎日04:00 JST / 現在の更新枠: {auto_update_key}")
                st.caption(f"次回更新目安: {get_next_jst_auto_update_text()}")
                force_refresh_requested = st.button("今すぐ再取得（管理者）")
                st.caption(
                    f"固定設定: 対象期間 {FIXED_LOOKBACK_HOURS}時間 / "
                    f"RSSごとの最大取得件数 {FIXED_MAX_ENTRIES_PER_FEED}件 / "
                    f"RSSアクセス間隔 {FIXED_REQUEST_INTERVAL_SEC:.1f}秒"
                )
                timeout_sec = st.slider("RSSタイムアウト 秒", 3, 30, timeout_sec, 1)
                include_undated = st.checkbox("日付不明の記事も含める", value=include_undated)
                include_summary_for_scoring = st.checkbox(
                    "RSS summary/description もスコア計算に使う（本文取得はしない）",
                    value=include_summary_for_scoring,
                )
                min_unique_terms = st.slider("最低ユニーク命中語数", 1, 5, min_unique_terms, 1)
                st.caption(f"ランキング上限: {MAX_RANKING_LIMIT} 件")

                selected_sources = st.multiselect("巡回する媒体", sources, default=sources)
                selected_categories = st.multiselect("巡回するカテゴリ", categories, default=categories)

                st.markdown("---")
                st.caption(
                    "除外済み: Guardian / Washington Post / Le Monde / Google News RSS / Reuters RSS。"
                )
        # Secrets未設定時は、公開画面に警告を出さず管理者欄を完全に非表示にする。

    if max_entries_per_feed <= 0:
        st.warning("RSSごとの最大取得件数が0なので、取得対象がありません。")
        return
    if not selected_sources or not selected_categories:
        st.warning("媒体またはカテゴリが未選択です。")
        return

    if force_refresh_requested:
        fetch_feed.clear()
        collect_and_rank_cached.clear()

    with st.spinner("ニュース情報を収集中…（約30秒）"):
        df, err_df, stats = collect_and_rank_cached(
            auto_update_key=auto_update_key,
            selected_sources=tuple(selected_sources),
            selected_categories=tuple(selected_categories),
            lookback_hours=lookback_hours,
            max_entries_per_feed=max_entries_per_feed,
            request_interval_sec=request_interval_sec,
            timeout_sec=timeout_sec,
            include_undated=include_undated,
            include_summary_for_scoring=include_summary_for_scoring,
            min_unique_terms=min_unique_terms,
            ranking_limit=ranking_limit,
        )

    st.caption(
        f"検索対象記事数: {int(stats.get('rss_entries_seen', 0)):,}件 / "
        f"表示: {len(df):,}件"
    )

    if df.empty:
        st.warning("条件に合う記事が見つかりませんでした。")
    else:
        render_result_cards(df)

    if admin_panel_requested:
        if admin_password_ok:
            render_admin_breakdown(df, err_df, stats)
        else:
            st.info("管理者画面を見るには、サイドバーで管理者パスワードを入力してください。")


def main() -> None:
    render_news_ranking_page()


if __name__ == "__main__":
    main()
