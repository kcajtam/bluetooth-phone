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


""" Dictionary of GPIO pins for volume control functions. If no volume controls then set to None.
    Peculiar to 1950 model phone used in this project. It has 5 external buttons for PABX functions that have been 
    used for other functions 
"""
VOLUME_PIN_DICT = {'VOLUME_UP_PIN': 23,
                   'VOLUME_DOWN_PIN': 24,
                   'VOLUME_MUTE_PIN': 25
                   }

""" Phone hardware constants """
# Switch bounce times for edge detection (units: ms)
DIAL_BOUNCE_TIME = 90
BUTTON_BOUNCE_TIME = 2000
RECEIVER_BOUNCE_TIME = 1000
VOLUME_INCREMENT = 5


""" Misc constants """
# misc. constants
READY = "READY"  # Flag indicating that modem has changed state t being ready for calls.
ALREADY_ON = "ALREADYON"  # Flag indicating that there was a phone connected at startup

