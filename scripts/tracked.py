"""
Basenames pulled out of each rootfs tree as loose, directly-downloadable release
assets (they also stay inside the rootfs tarball). The binaries and scripts this
project has RE narrative around — see the seestar-s30-re docs.
"""

TRACKED_BASENAMES = {
    "zwoair_imager", "zwoair_guider", "zwoair_updater", "zwoair_file_server",
    "zwoair_daemon.sh", "AM_Test", "air_ble", "beeper", "flash_power_led",
    "exiv2", "crashpad_handler", "bsa_server", "bluetooth.sh",
    "network.sh", "common.sh", "write_wpa_conf.sh", "searchSSIDIndex.py",
    "old_log_mv.sh", "run_update_pack.sh", "planet.py", "auto_shutdown.sh",
    "set_timezone.sh", "start_INDI.sh",
}
