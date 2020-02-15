import dbus
import config
from threading import Thread
import RPi.GPIO as GPIO
import dbus.mainloop.glib
from gi.repository import GLib
import time


class Ringer(Thread):
    """
    Thread to run the hardware ringer.
    Used settings from config for pins and ringer frequency.
    """
    def __init__(self, ringer_pin, sequence):
        Thread.__init__(self)
        GPIO.setmode(GPIO.BCM)
        self.pin = ringer_pin
        GPIO.setup(self.pin, GPIO.OUT)
        # sue the PWM GPIO to control the ringer.
        self.ringer = GPIO.PWM(self.pin, config.RINGER_FREQUENCY)
        self.seq = sequence
        self.is_ringing = False # Gettable/Settable flag to start/stop ringing
        self.finished = False

    def run(self):
        """
            Perpetual loop. Because the self.is_ringing can be changed asynchronously its important
            to check that the status hasn't changed before ring continuing with the sequence of rings.
        """
        ringing = False
        print("Ringer Thread Started")
        while not self.finished:
            if self.is_ringing:
                for x in range(self.seq.size):
                    if ringing:
                        print("pulse off")
                        self.ringer.stop()
                        ringing = False
                    else:
                        ringing = True
                        print("pulse on")
                        if self.is_ringing:
                            self.ringer.start(100)
                    if self.is_ringing:
                        time.sleep(self.seq[x])
            else:
                pass


class RingerManager(object):
    """
    Management object for controlling the ringer via the DBus.
    """
    def __init__(self):
        self.bus = dbus.SystemBus()
        self._setup_listeners()
        self.finished = False
        self.is_ringing = False

        """ Create the ringer thread so that it is inscope for the _control ringer function"""
        self._ringer = Ringer(config.RINGER_PIN, config.RINGER_PATTERN)
        self._ringer.start()

    def _setup_listeners(self):
        print("create ringer listeners")
        self.ring_service_interface = dbus.Interface(self.bus.get_object('org.frank', '/'), "phone.status")
        self.ring_service_interface.connect_to_signal('ring', self._control_ringer)

    def _control_ringer(self, value):
        """ Handler set flag (self.is_ringer) that will stop the loop in the Ringer thread."""
        print(f"Ringing Controller {value}")
        if value == config.RING_START:
            print("dbus ring start signal received")
            self._ringer.is_ringing = True
        else:
            print("dbus ringer stop signal received")
            self._ringer.is_ringing = False

