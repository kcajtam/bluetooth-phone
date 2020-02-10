"""
Configuration file
"""

""" GPIO pin configurations """
# Receiver / handset GPIO pin
HOERER_PIN = 13
# Dialer GPIO pin
NS_PIN = 19
# GPIO pin for making RPi discoverable
DISCOVERABLE_PIN = 20
# Dictionary of GPIO pins for volume control functions. If no volume controls then set to None.
VOLUME_PIN_DICT = {'VOLUME_UP_PIN': 23,
                   'VOLUME_DOWN_PIN': 24,
                   'VOLUME_MUTE_PIN': 25
                   }

""" Misc constants """
# misc. constants
READY = "READY"  # Flag indicating that modem has changed state t being ready for calls.
ALREADY_ON = "ALREADYON"  # Flag indicating that there was a phone connected at startup

