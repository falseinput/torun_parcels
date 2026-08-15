"""Single source of truth for the Toruń parcels pipeline.

Everything downstream (SQL generation, validation, the viewer legend) is derived
from the constants here so the ownership mapping cannot drift between the
transform step and the checks that verify it.
"""

# --- Source -----------------------------------------------------------------

WFS_URL = "https://mtorun-wms.webewid.pl/iip/ows"
WFS_VERSION = "1.1.0"
TYPENAME = "ms:dzialki"

# The service advertises EPSG:2177 / 2180 / 4326 only -- there is no 3857.
# 2177 is the one we fetch: it carries centimetre precision, whereas the
# server rounds 4326 output to 6 decimal places (+/-5.55 cm in latitude,
# which would exceed our 4.49 cm tile quantisation and become the binding
# precision constraint).
SOURCE_CRS = "EPSG:2177"

# Cross-check CRS. The server will also emit 4326 directly; we use that as an
# independent witness that our local reprojection is correct (see validate.py).
CHECK_CRS = "EPSG:4326"

# The attribute carrying the ownership category. DescribeFeatureType must
# expose it or the build is meaningless.
GROUP_FIELD = "GRUPA_REJESTROWA"

# Fields that exist in the schema but are empty on every record, because
# art. 40a ust. 2 pkt 1 of Prawo geodezyjne i kartograficzne limits what the
# service may publish without a fee. Listed so nobody wastes time on them.
KNOWN_EMPTY_FIELDS = ("POLE_EWIDENCYJNE", "KLASOUZYTKI_EGIB")

# --- Expectations (CI guards) ----------------------------------------------

EXPECTED_FEATURES = 46059
COUNT_TOLERANCE = 0.02  # +/- 2%

# The service advertises its own contract in GetCapabilities, so assert against
# that rather than against constants frozen at implementation time. fetch.py
# captures it to build/source_contract.json each run.
EXPECTED_DEFAULT_SRS = "urn:ogc:def:crs:EPSG::2177"
EXPECTED_OUTPUT_FORMAT = "text/xml; subtype=gml/3.1.1"

# The advertised WGS84BoundingBox is the precise expectation; this generous
# envelope only catches an advertised bbox that is itself nonsense (roughly
# Poland). A silent axis flip lands in the North Sea off Norway and trips both.
SANITY_BBOX = (14.0, 49.0, 24.2, 55.0)
BBOX_PAD = 0.02  # degrees of slack around the advertised envelope

# Max acceptable disagreement between our reprojection and the server's own
# EPSG:4326 output. The server rounds to 1e-6 deg, so pure rounding alone
# accounts for ~5.6 cm; anything beyond 6 cm means a real transform error.
AXIS_CHECK_FEATURES = 400
AXIS_CHECK_MAX_DELTA_CM = 6.0

# --- Tiling -----------------------------------------------------------------

LAYER_NAME = "parcels"
MIN_ZOOM = 11
MAX_ZOOM = 16
# Detail 13 => extent 8192 => 4.49 cm quantisation at z16/lat 53.
# Same precision as maxzoom 17 but costs +0.04 MB instead of +3.4 MB.
#
# NOTE: -d applies at MAXZOOM only. Lower zooms use tippecanoe's --low-detail,
# which defaults to 12 (extent 4096). That is intentional: centimetre precision
# matters where you inspect parcel boundaries, not on overview zooms, and 4096
# keeps the low-zoom tiles small.
DETAIL = 13
TILE_EXTENT = 1 << DETAIL
LOW_DETAIL = 12
LOW_TILE_EXTENT = 1 << LOW_DETAIL

# --- Ownership classification ----------------------------------------------
#
# Registry groups per Rozporządzenie Ministra Rozwoju, Pracy i Technologii
# z 27.07.2021 w sprawie ewidencji gruntów i budynków (Dz.U. 2021 poz. 1390),
# § 14 / Załącznik nr 2. There are 16 groups, not 15.

REGISTRY_GROUPS = {
    1: "Skarb Państwa (bez użytkowników wieczystych)",
    2: "Skarb Państwa (z użytkownikami wieczystymi)",
    3: "Jednoosobowe spółki SP, przedsiębiorstwa państwowe, państwowe osoby prawne",
    4: "Gminy i związki międzygminne (bez użytkowników wieczystych)",
    5: "Gminy i związki międzygminne (z użytkownikami wieczystymi)",
    6: "Jednoosobowe spółki JST i osoby prawne powołane przez samorząd",
    7: "Osoby fizyczne",
    8: "Spółdzielnie",
    9: "Kościoły i związki wyznaniowe",
    10: "Wspólnoty gruntowe",
    11: "Powiaty i związki powiatów (bez użytkowników wieczystych)",
    12: "Powiaty i związki powiatów (z użytkownikami wieczystymi)",
    13: "Województwa (bez użytkowników wieczystych)",
    14: "Województwa (z użytkownikami wieczystymi)",
    15: "Spółki prawa handlowego",
    16: "Inne podmioty niewymienione w pkt 1-15",
}

# klasa -> registry groups. Ordered as the legend should read.
#
# Judgement calls, for the record:
#   * 3 folds into skarb_panstwa and 6 into gmina -- same owner in substance,
#     and 14 / 28 parcels respectively would be invisible as their own class.
#   * spoldzielnia stays separate at 2.32%: in a Polish city these are housing
#     cooperatives forming large coherent blocks, and merging them into
#     spolka_handlowa would paint residential land as commercial.
#   * powiat + województwo merge because Toruń is a miasto na prawach powiatu,
#     so powiat land is 7 parcels while voivodeship land is 907.
CLASS_MAP = {
    "osoba_fizyczna": [7],
    "gmina": [4, 5, 6],
    "skarb_panstwa": [1, 2, 3],
    "spolka_handlowa": [15],
    "spoldzielnia": [8],
    "wojewodztwo_powiat": [11, 12, 13, 14],
    "pozostale": [9, 10, 16],
}

CLASS_LABELS = {
    "osoba_fizyczna": "Osoby fizyczne",
    "gmina": "Gmina Toruń",
    "skarb_panstwa": "Skarb Państwa",
    "spolka_handlowa": "Spółki prawa handlowego",
    "spoldzielnia": "Spółdzielnie",
    "wojewodztwo_powiat": "Województwo / powiat",
    "pozostale": "Pozostałe (kościoły, wspólnoty, inne)",
}

# Groups where public ownership coincides with a użytkownik wieczysty: the land
# is publicly owned but privately controlled. Kept as a flag rather than
# doubling the class count.
PERPETUAL_USUFRUCT_GROUPS = [2, 5, 12, 14]

FALLBACK_CLASS = "pozostale"


def group_to_class(group: int) -> str:
    for klasa, groups in CLASS_MAP.items():
        if group in groups:
            return klasa
    return FALLBACK_CLASS


def all_mapped_groups() -> set:
    out = set()
    for groups in CLASS_MAP.values():
        out |= set(groups)
    return out


def build_classification_sql() -> str:
    """Generate the ogr2ogr -sql body from CLASS_MAP.

    Generated rather than hand-written so the SQL and the Python used to check
    it can never disagree. Requires -dialect SQLITE (the default OGR dialect
    has no CASE expression).
    """
    lines = [
        "SELECT geometry,",
        "  ID_DZIALKI AS id, NUMER_DZIALKI AS nr, NUMER_OBREBU AS obreb,",
        f"  CAST({GROUP_FIELD} AS integer) AS grupa,",
        "  CASE",
    ]
    for klasa, groups in CLASS_MAP.items():
        if klasa == FALLBACK_CLASS:
            continue
        in_list = ",".join(str(g) for g in sorted(groups))
        lines.append(
            f"    WHEN CAST({GROUP_FIELD} AS integer) IN ({in_list}) THEN '{klasa}'"
        )
    lines.append(f"    ELSE '{FALLBACK_CLASS}'")
    lines.append("  END AS klasa,")
    uw_list = ",".join(str(g) for g in sorted(PERPETUAL_USUFRUCT_GROUPS))
    lines.append(
        f"  CASE WHEN CAST({GROUP_FIELD} AS integer) IN ({uw_list}) "
        "THEN 1 ELSE 0 END AS uw"
    )
    lines.append("FROM dzialki")
    return "\n".join(lines)


# --- Layout -----------------------------------------------------------------

# Name of the local build artefact. The published name is API_TILESET_NAME.
TILESET_NAME = "parcels-evidence.pmtiles"

# --- Published API ----------------------------------------------------------
#
# Two endpoints, both static files:
#   GET {SITE_BASE_URL}/api/v1/parcels.pmtiles   the tileset, always latest
#   GET {SITE_BASE_URL}/api/v1/parcels.json      TileJSON 3.0.0 + provenance
#
# The v1 prefix versions the contract: a future change of shape ships as v2
# while v1 keeps working.

SITE_BASE_URL = "https://falseinput.github.io/torun_parcels"
API_VERSION = "v1"
API_DIR = f"api/{API_VERSION}"
API_TILESET_NAME = "parcels.pmtiles"
API_METADATA_NAME = "parcels.json"

TILEJSON_NAME = "torun-parcels"
TILEJSON_DESCRIPTION = (
    "Ownership category, parcel number and geometry for Toruń cadastral parcels."
)

# vector_layers[].fields for the TileJSON document. Keys must match exactly the
# attributes the transform step selects into the tiles.
FIELD_DESCRIPTIONS = {
    "id": "Full parcel identifier (ID_DZIALKI)",
    "nr": "Parcel number within the obręb",
    "obreb": "Obręb (cadastral district) number",
    "grupa": "EGiB registry group, 1-16",
    "klasa": "Ownership class",
    "uw": "1 if the parcel is held in użytkowanie wieczyste, otherwise 0",
}

# --- Attribution ------------------------------------------------------------
#
# Art. 40c ust. 3 Prawo geodezyjne i kartograficzne obliges anyone who uses
# material from the panstwowy zasob geodezyjny i kartograficzny to state its
# source in published works. The Open Data Act (11 Aug 2021) additionally lets
# the provider require the time of creation and the time of acquisition, so the
# manifest and the viewer carry both.

ATTRIBUTION_SOURCE = (
    "Państwowy zasób geodezyjny i kartograficzny — Prezydent Miasta Torunia"
)
ATTRIBUTION_DATASET = "Ewidencja gruntów i budynków (EGiB)"
LEGAL_BASIS = "art. 40c ust. 3 Prawo geodezyjne i kartograficzne"


def attribution_text(created: str | None, retrieved: str | None) -> str:
    """Single-line attribution for the TileJSON `attribution` field."""
    text = f"Źródło: {ATTRIBUTION_SOURCE}. {ATTRIBUTION_DATASET}."
    if created:
        text += f" Stan na {created}."
    if retrieved:
        text += f" Pobrano {retrieved}."
    return text
