# Copyright 2019 by Xabier Zubizarreta.
# All rights reserved.
# This file is released under the "MIT License Agreement".
# More information on this license can be read under https://opensource.org/licenses/MIT

import RPi.GPIO as GPIO
import datetime
import dbus
import dbus.service
import dbus.mainloop.glib
import wave
import alsaaudio
import yaml
from gi.repository import GLib

import time
from threading import Thread
from threading import Event
import queue as Queue
import numpy as np
import struct

import subprocess

import config
import manager

class RotaryDial(Thread):
    """
    Thread class reading the dialed values and putting them into a thread queue
    """

    def __init__(self, ns_pin, number_queue):
        Thread.__init__(self)
        self.pin = ns_pin
        """ 
            The number_queue is Queue.Queue instance passed in from another thread. 
            This appears to facilitate interprocess communications
        """
        self.number_q = number_queue
        GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.value = 0
        self.pulse_threshold = 0.2
        # self.pulse_threshold = 0.2
        self.finish = False
        GPIO.add_event_detect(ns_pin, GPIO.FALLING, callback=self.__increment, bouncetime=config.DIAL_BOUNCE_TIME)

    def __increment(self, pin_num):
        """
        Increment function trigerred each time a falling pulse is detected.
        :param pin_num: GPIO pin triggering the event (Can only be self.ns_pin here)
        """
        self.value += 1

    def run(self):
        while not self.finish:
            last_value = self.value
            time.sleep(self.pulse_threshold)
            if last_value != self.value:
                pass
            elif self.value != 0:
                if self.value == 10:
                    self.number_q.put(0)
                else:
                    self.number_q.put(self.value)
                self.value = 0


class Ringer(Thread):
    """
    Thread to run the ringer singals
    """
    def __init__(self, ringer_pin, sequence):
        Thread.__init__(self)
        self.pin = ringer_pin
        GPIO.setup(self.pin, GPIO.OUT)
        # sue the PWM GPIO to control the ringer.
        self.ringer = GPIO.PWM(self.pin, config.RINGER_FREQUENCY)
        self.seq = sequence*1000.0

        self.is_ringing = False # Gettable/Settable flag to start/stop ringing

    def run(self):
        ringing = True
        while self.is_ringing:
            for x in range(len(self.seq)):
                if not self.is_ringing:
                    if ringing:
                        self.ringer.stop()
                    else:
                        self.ringer.start(100)
                    time.sleep(self.seq[x])
                else:
                    break



class Telephone(object):
    """
    Main Telephone class containing everything required for the Bluetooth telephone to work.
    """
    CHUNK = 1024

    def __init__(self, num_pin, receiver_pin, discoverable_pin=None, volume_pin_dict=None):
        GPIO.setmode(GPIO.BCM)
        self.receiver_pin = receiver_pin
        self.number_q = Queue.Queue()

        self.discoverable_pin = discoverable_pin  # white button to trigger discovery and pairing.
        self.discoverable = False
        self.has_volume_controller = False
        self.stop_audio = False
        self.playing_audio = False
        self.finish = False

        if volume_pin_dict is not None:
            self.has_volume_controller = True
            self.volume_up_pin = volume_pin_dict['VOLUME_UP_PIN']
            self.volume_down_pin = volume_pin_dict['VOLUME_DOWN_PIN']
            self.volume_mute_pin = volume_pin_dict['VOLUME_MUTE_PIN']
        else:
            self.has_volume_controller = False

        self.phone_manager = manager.PhoneManager()
        self.bt_conn = self.phone_manager.bt_conn

        self.rotary_dial = RotaryDial(num_pin, self.number_q)

        # Load fast_dial numbers
        with open("phonebook.yaml", 'r') as stream:
            self.phonebook = yaml.safe_load(stream)

        print(self.phonebook)

        # Discoverability and volume control may not be available of phone model used. If they are then set up listeners
        if discoverable_pin is not None:
            # Set up the button to make it discoverable by preiously unpaired BT device.
            print("Discoverable button available")
            GPIO.setup(self.discoverable_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.discoverable_pin, GPIO.RISING, callback=self.make_discoverable, bouncetime=config.BUTTON_BOUNCE_TIME)

        if self.has_volume_controller:
            # Set volume up pin
            print("Set up volume controls")
            GPIO.setup(self.volume_up_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_up_pin, GPIO.RISING, callback=self.volume_up, bouncetime=config.BUTTON_BOUNCE_TIME)
            # Set volume down pin
            GPIO.setup(self.volume_down_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_down_pin, GPIO.RISING, callback=self.volume_down, bouncetime=config.BUTTON_BOUNCE_TIME)
            # set up mute toggling function
            GPIO.setup(self.volume_mute_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_mute_pin, GPIO.RISING, callback=self.volume_mute_toggle, bouncetime=config.BUTTON_BOUNCE_TIME)
        else:
            print("No volume controls available")

        # Receiver relevant functions
        GPIO.setup(self.receiver_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        if GPIO.input(self.receiver_pin) is GPIO.HIGH:
            self.receiver_down = False
        else:
            self.receiver_down = True
        # self.receiver_changed(self.receiver_pin)
        print("Initial receiver status = down ? {0}".format(self.receiver_down))
        GPIO.add_event_detect(self.receiver_pin, GPIO.BOTH, callback=self.receiver_changed, bouncetime=config.RECEIVER_BOUNCE_TIME)

        # Start rotary dial thread
        self.rotary_dial.start()

    def make_discoverable(self, pin_num):
        """
            Set the RPi BT device to discoverable and pairable for 30 seconds. This is used only for pairing
            device (e.g. a mobile phone) that has not previously been paired.
            param: pin_num - the number of the GPIO pin that triggered the event - not used.
        """
        self.bt_conn.make_discoverable()

    def volume_up(self, pin):
        self.phone_manager.volume_up(config.VOLUME_INCREMENT)
        print(f"Volume Up: Mic volume = {self.phone_manager.mic_volume}")

    def volume_down(self, pin):
        self.phone_manager.volume_down(config.VOLUME_INCREMENT)
        print(f"Volumne Down: Mic volume = {self.phone_manager.mic_volume}")

    def volume_mute_toggle(self, pin):
        self.phone_manager.mute_toggle()
        print(f"Toggle mute: Current status = {self.phone_manager.muted}")

    def receiver_changed(self, pin_num):
        """
        Event triggered when the receiver is hung of lifted.
        :param pin_num: GPIO pin triggering the event (Can only be self.receiver_pin here)
        :return: None
        """
        print("Receiver status changed..")
        if GPIO.input(pin_num) is GPIO.HIGH:
            print("Receiver Up")
            self.receiver_down = False
            if self.phone_manager.call_in_progress:
                self.phone_manager.answer_call()
            else:
                # else we're picking the receiver up to begin dialing
                self.start_file("/home/pi/bluetooth-phone/dial_tone.wav", loop=True)
        else:
            print("Receiver Down")
            if self.phone_manager.call_in_progress:
                print("Hanging up")
                self.phone_manager.end_call()
            self.receiver_down = True
            self.stop_file()  # kill thread that might be playing the dial tone.


    def start_file(self, filename, loop=False):
        """
        Start a thread reproducing an audio file
        :param filename: The name of the file to play
        :param loop: If the file should be played as a loop (like in the case of the dial tone)
        """
        print("Play file : telephone object {0}".format(filename))
        self._thread = Thread(target=self.__play_file, args=[filename, loop])
        self._thread.start()
        self.playing_audio = True

    def __play_file(self, filename, loop):
        """
        Private function handling the wav file replay
        :param filename: The name of the file to play
        :param loop: If the file should be played as a loop (like in the case of the dial tone)
        """
        self.stop_audio = False
        if not loop:
            # open a wav format music
            f = wave.open(filename, "rb")
            # open stream
            stream = alsaaudio.PCM(type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL, device='plughw:1,0')
            stream.setchannels(f.getnchannels())
            stream.setrate(f.getframerate())
            # read data
            data = f.readframes(self.CHUNK)

            # play stream
            while data and not self.stop_audio:
                stream.write(data)
                data = f.readframes(self.CHUNK)
            f.close()
            stream = None
        else:
            # open a wav format music
            f = wave.open(filename, "rb")
            # open stream
            stream = alsaaudio.PCM(type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL, device='plughw:1,0')
            stream.setchannels(f.getnchannels())
            stream.setrate(f.getframerate())
            # read data
            data = f.readframes(self.CHUNK)

            # play stream
            while loop and not self.stop_audio:
                f.rewind()
                data = f.readframes(self.CHUNK)
                while data and not self.stop_audio:
                    stream.write(data)
                    data = f.readframes(self.CHUNK)
            f.close()
            stream = None

    def stop_file(self):
        self.stop_audio = True
        self.playing_audio = False
        print("stopping sound")

    def dialing_handler(self):
        """
        Main function of the telephone that handles the dialing if the receiver is lifted or hooked.
        If only a single digit is dialed (with the handset up) its interpreted as being a speed dial
        Number
        :return: None
        """
        number = ''
        while not self.finish:
            if not self.receiver_down:  # Handling of the dialing when the receiver is lifted
                try:
                    c = self.number_q.get(timeout=5)
                    # turn off dial tone as soon as a number is dialed.
                    if not number == '' and self.playing_audio:
                        self.stop_file()
                    number += str(c)
                except Queue.Empty:
                    if number is not '':
                        if len(number) > 1:
                            print("Dialing: %s" % number)
                            self.stop_file()
                            self.phone_manager.call(number)
                            number = ''
                        else:  # Handling of the dialing for speed dial from phonebook
                            if self.playing_audio:
                                self.stop_file()
                            try:
                                print("Selected %d" % c)
                                if c == 9:
                                    print("Turning system off")
                                    self.start_file("/home/pi/bluetooth-phone/turnoff.wav")
                                    time.sleep(6)
                                    subprocess.call("sudo shutdown -h now", shell=True)
                                elif c <= len(self.phonebook):
                                    print("Shortcut action %d: Automatic dial" % c)
                                    number = self.phonebook[c - 1]['number']
                                    print(number)
                                    time.sleep(4)
                                    self.phone_manager.call(number)
                                number = ''
                            except Queue.Empty:
                                pass

            else:
                """
                If the receiver is down the must clear the number and the queue constantly because 
                noise on the dialer pins can cause spirious rising edges when the receiver is lifted or put down.
                """
                number = ''
                if len(self.number_q.queue) >= 1:
                    self.number_q.queue.clear()
                    print("Queue Cleared")

    def close(self):
        self.rotary_dial.finish = True
        self.phone_manager.loop.quit()
        GPIO.cleanup()


if __name__ == '__main__':

    #create and instance of the telephone
    t = Telephone(config.NS_PIN, config.HOERER_PIN, config.DISCOVERABLE_PIN, config.VOLUME_PIN_DICT)

    try:
        # enter the dialing handler loop
        t.dialing_handler()
    except KeyboardInterrupt:
        pass
    t.close()

