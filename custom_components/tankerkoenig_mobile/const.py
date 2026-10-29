"""Constants for the Tankerkönig Mobile integration."""

from datetime import timedelta

DOMAIN = "tankerkoenig_mobile"

API_URL = "https://creativecommons.tankerkoenig.de/json/list.php"
REQUEST_TIMEOUT = 30

# Tankerkönig asks clients not to query more often than every 5 minutes.
MIN_FETCH_GAP = timedelta(minutes=5)
MAX_RADIUS_KM = 25

CONF_ENABLE_HOME = "enable_home"
CONF_ENABLE_MOBILE = "enable_mobile"
CONF_TRACKER = "tracker_entity"
CONF_RADIUS = "radius_km"
CONF_FUEL_TYPE = "fuel_type"
CONF_STATION_FILTER = "station_filter"
CONF_SCAN_INTERVAL = "scan_interval_min"
CONF_DWELL_MINUTES = "dwell_min"
CONF_STATIONARY_TOLERANCE = "stationary_tolerance_m"
CONF_PAUSE_ZONES = "pause_zones"
CONF_FIXED_TIMES = "fixed_times"

DEFAULT_RADIUS = 5
DEFAULT_FUEL_TYPE = "diesel"
DEFAULT_SCAN_INTERVAL = 167  # minutes, roughly the former scan_interval of 10000 s
DEFAULT_DWELL_MINUTES = 10
DEFAULT_STATIONARY_TOLERANCE = 200  # meters

FUEL_TYPES = ["diesel", "e5", "e10"]

LOCATION_HOME = "home"
LOCATION_MOBILE = "mobile"

# Config flow sections (options are stored flat).
SECTION_HOME = "home"
SECTION_MOBILE = "mobile"

LOCATION_NAMES = {
    LOCATION_HOME: "Tankerkönig Zuhause",
    LOCATION_MOBILE: "Tankerkönig Unterwegs",
}

STORAGE_VERSION = 1
