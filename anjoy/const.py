"""Protocol constants for Anjoy (安佳威视) IP cameras.

Two control surfaces live on a stock Anjoy camera:

* the **web SOAP-over-HTTP API** (port 80) — what this client speaks by default;
  auth is DES(key ``"WebLogin"``) of user/pass, hex, in the SOAP header
  (see :mod:`anjoy.des`);
* the **binary AJ protocol** (``XML_ANJVISION`` / ``XML_TOPSEE``) on ``comm_server``
  TCP 8091 — richer, but the wire framing/auth are capture-gated (:mod:`anjoy.comm`).

Values reconstructed from the stock web-UI JS, the vendor SDK/tools, and a stock
device config dump. Anything not yet confirmed on live hardware is marked below.
"""

from __future__ import annotations

# -- service ports (from a stock <StreamAccess> config dump) ----------------
WEB_PORT = 80            # SOAP HTTP API + web UI
HTTPS_PORT = 443
RTSP_PORT = 554          # Auth="1"
COMM_PORT = 8091         # comm_server / PTZ — binary AJ protocol
HIK_PORT = 8000          # Hik-compat SDK shim (hik_auth="0": no auth)
DAHUA_PORT = 37777       # Dahua-compat shim (dh_auth="0": no auth)
H5_PORT = 12351          # h5live_server
RTMP_PORT = 1935

# discovery (proven working — tools/aj_udpsearch.py)
DISCOVERY_SRC_PORT = 36584
DISCOVERY_DST_PORT = 3001
DISCOVERY_MSG_TYPE = "SYSTEM_SEARCHIPC_MESSAGE"

DEFAULT_PORT = WEB_PORT
DEFAULT_TIMEOUT = 10.0
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "123456"     # factory default

# factory network defaults (vendor-stated)
FACTORY_IP = "192.168.0.123"
FACTORY_ALT_IPS = ("192.168.1.188", "192.168.1.109")

# -- SOAP HTTP endpoints (from the stock web-UI JS) -------------------------
# Device / system / info
GET_VERSION = "/getSystemVersionInfo"
GET_CHIP_UUID = "/get_mstar_chip_uuid"
GET_APP_TYPE = "/getAppType"
GET_ALL_CONFIG = "/getCurrentAllConfig"
GET_DEFAULT_CONFIG = "/getDefaultConfigMsg"
GET_SYS_BOOT_STATUS = "/getSysBootStatus"
GET_SYSTEM_CONTROL = "/getSystemControlString"
GET_SYSTEM_CONFIG = "/getSystemConfig"

# Network / time / misc
GET_NETWORK_CONFIG = "/getNetworkConfig"
SET_NETWORK_LAN = "/setNetworkLANConfig"
SET_NETWORK_P2P = "/setNetworkP2PConfig"
GET_NETWORK_STATUS = "/getNetworkStatus"
GET_TIME_CONFIG = "/getTimeConfig"
GET_MISC_CONFIG = "/getMiscConfig"
SET_MISC_CONFIG = "/setMiscConfig"

# Users
GET_USER_CONFIG = "/getUserConfig"
ADD_USER = "/setAddUserConfig"
EDIT_USER = "/setEditUserConfig"
DEL_USER = "/setDelUserConfig"

# PTZ / lens
PTZ_CMD = "/setPTZCmd"
GET_PTZ_CONFIG = "/getPtzConfig"
SET_PTZ_COMMON = "/setPtzCommomConfig"       # note: vendor's spelling "Commom"
SET_PTZ_AF = "/setPtzAfConfig"
SET_PTZ_ADVANCE = "/setPtzAdvanceConfig"
GET_PRESET_LIST = "/getPresetList"
PRESET_LIST = "/PresetList"

# Media / encode
GET_MEDIA_VIDEO = "/getMediaVideoConfig"
SET_VIDEO_ENCODE = "/setMediaVideoEncodeConfig"
GET_MEDIA_STREAM = "/getMediaStreamConfig"
GET_MEDIA_AUDIO = "/getMediaAudioConfig"
GET_VIDEO_SIZE = "/getVideoSize"
FORCE_IDR = "/setForceIdr"

# Day/night, LED
SET_IRCUT_DAYNIGHT = "/setIRCutManual_DayNight"
SET_LED_KEEP_ON = "/setLed_KeepOn_Off"

# Firmware
SET_FIRMWARE_UPGRADE = "/setFirmwareUpgrade"
GET_FIRMWARE_PROGRESS = "/getFirmwareUploadProgress"

# -- PTZ command vocabulary (SOAP <xml><cmd>...</cmd>) ----------------------
# Move directions for /setPTZCmd (press-and-hold; send STOP to halt).
PTZ_DIRECTIONS = (
    "up", "down", "left", "right",
    "left_up", "left_down", "right_up", "right_down",
)
PTZ_STOP = "stop"
# Lens verbs for /setPTZCmd.
PTZ_LENS = {
    "zoom_tele": "zoomtele",
    "zoom_wide": "zoomwide",
    "focus_far": "FocusFarAutoOff",
    "focus_near": "FocusNearAutoOff",
    "iris_open": "IrisOpenAutoOff",
    "iris_close": "IrisCloseAutoOff",
}
# Preset verbs for /PresetList.
PRESET_SET = "setpreset"
PRESET_GOTO = "callpreset"
PRESET_DEL = "delpreset"

# Serial-PTZ factory defaults (from web UI + sample config).
PTZ_DEFAULTS = {
    "Protocol": "PELCO_D", "ComPort": "1", "BaudRate": "2400",
    "DataBits": "8", "StopBits": "10", "Verify": "NONE",
    "FlowControl": "NONE", "BootAction": "0",
}
# Magic preset numbers (PELCO-D convention; remappable via SET_PTZ_ADVANCE).
PTZ_MAGIC_PRESETS = {
    "cruise_group": (65, 66, 67, 68),
    "scan_left_bound": 92, "scan_right_bound": 93,
    "scan_speed": (87, 88, 89),
    "ptz_reset": 77, "reset_default": 94, "ptz_reboot": 94,
    "track": 152,
}

# -- binary AJ protocol message types (capture-gated) -----------------------
AJ_MSG_TYPES = (
    "SYSTEM_SEARCHIPC_MESSAGE", "USER_AUTH_MESSAGE",
    "SYSTEM_CONFIG_GET_MESSAGE", "SYSTEM_CONFIG_SET_MESSAGE",
    "SYSTEM_CONTROL_MESSAGE", "PTZ_CONTROL_MESSAGE",
    "AUXPTZ_HEARTBEAT_MESSAGE", "MEDIA_DATA_MESSAGE",
    "REVERSE_AUDIO_MESSAGE", "ALARM_REPORT_MESSAGE",
    "REPLAY_CONTROL_MESSAGE", "TIMELINE_REPLAY_CONTROL_MESSAGE",
    "NVR_REPLAY_MESSAGE", "DECODER_CONFIG_GET_MESSAGE",
    "DECODER_CONTROL_MESSAGE",
)
AJ_ROOT_ANJVISION = "XML_ANJVISION"   # discovery / branding
AJ_ROOT_TOPSEE = "XML_TOPSEE"         # OEM lineage, used inside the SDK
AJ_ENCODING = "GB2312"

# On-device path of the full config XML (AjDevTools "Batch Download Config").
CONFIG_PATH = "/mnt/nand/config.xml"

# -- SYSTEM_CONFIG_SET_MESSAGE section codes --------------------------------
# The vendor's per-feature batch operations write ONE config section at a time:
# ``SYSTEM_CONFIG_SET_MESSAGE`` carries the section's numeric ``Msg_code`` and the
# section element as the body (a *partial* section is accepted — the device
# merges it into the stored config), and the device replies with the same
# type+code and an empty body to acknowledge. The write is applied asynchronously
# (it reaches ``/mnt/nand/config.xml`` a moment later). Codes are discovered by
# capturing AjDevTools live; each is added here once confirmed on hardware.
# Reads go through the full-config download (:meth:`AnjoyCommClient.get_config`) —
# the device's per-section GET is not what the vendor tool uses.
CFG_OVERLAY = "525"      # MediaConfig/Video/Overlay — OSD title + timestamp
CFG_MAINTAIN = "228"     # SystemConfig/MaintainConfig — scheduled auto-reboot
CFG_MISC = "227"         # SystemConfig/MiscConfig — device language
CFG_MOTION = "822"       # AlarmConfig/MotionDetectAlarm — motion detection
CFG_PERSON_DETECT = "829"  # AlarmConfig/VideoPD — AI person detection
CFG_FACE_DETECT = "832"    # AlarmConfig/FaceDetect — AI face detection
CFG_USER = "223"         # SystemConfig/UserConfig — user accounts / passwords
CFG_LAN = "325"          # NetworkConfig/LANConfig — wired IP / DHCP

# -- error codes ------------------------------------------------------------
ANJOY_ERRORS = {
    285475072: "PTZ protocol not set on this channel",
}


def error_message(code: int | None, fallback: str = "Anjoy API error") -> str:
    if code is None:
        return fallback
    return ANJOY_ERRORS.get(code, fallback)
