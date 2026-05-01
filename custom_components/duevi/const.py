"""Constants for the Duevi CE-LAN Alarm integration."""

DOMAIN = "duevi"
CONF_PIN = "pin"

# --- Protocol field keys (returned by read_inputs_stat) ---
KEY_LINE_STATE = "line_state"
KEY_TX_STATE = "tx_state"
KEY_TX_FLAGS = "tx_flags"

# --- Sensor technology codes (protocol query 53) ---
SENSOR_TECH_CONTACT = 0
SENSOR_TECH_CONTACT_TAMPER = 1
SENSOR_TECH_CONTACT_DUAL = 2
SENSOR_TECH_REED = 3
SENSOR_TECH_VIBRATION = 4
SENSOR_TECH_PIR = 5
SENSOR_TECH_ROLLER = 6

# Technologies to expose as HA binary sensors
INCLUDED_SENSOR_TECHS = {
    SENSOR_TECH_CONTACT,
    SENSOR_TECH_CONTACT_TAMPER,
    SENSOR_TECH_CONTACT_DUAL,
    SENSOR_TECH_REED,
    SENSOR_TECH_PIR,
}

# --- Line state values (protocol query 54) ---
LINE_STATE_NORMAL = 0
LINE_STATE_SHORT = 1
LINE_STATE_ALARM = 2
LINE_STATE_TAMPER = 3

# --- Device family names (protocol query 56) ---
DEVICE_FAMILY_NAMES = {
    0: "", 1: "DVT", 3: "ESP8-BUS", 16: "CELAN",
    64: "TX6C", 65: "MINI-C", 70: "VIPER", 71: "TX6C-AES",
    75: "MINI-C-RDC", 128: "VIDEO-PIR",
}

# --- Panel state machine values (protocol query 60) ---
SM_DISARMED = (0, 12)
SM_ARMING = (1, 13)
SM_ARMED = (2,)
SM_ALARM = (3, 6)
SM_PENDING = (4,)
SM_TAMPER = (5, 11)
SM_PANIC = (9,)

# --- Polling / availability ---
DEFAULT_PORT = 5570
FAILURE_THRESHOLD = 5
