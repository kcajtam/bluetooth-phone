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
#from subprocess import call
import subprocess


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
        GPIO.add_event_detect(ns_pin, GPIO.FALLING, callback=self.__increment, bouncetime=90)

    def __increment(self, pin_num):
        """
        Increment function trigered each time a falling pulse is detected.
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

class bt_connection(object):
    """
    Singleton class that deals with the bluetooth connection between phone and RPi.
    Notes:
        In ofono a modem is a previously paired bluetooth device. The list of all such modems is
        returned by ofono.Manager.GetModems() Note that modem can be present but there may be no active
        connection i.e. it is offline. In order to start accepting or making call the Modem must be present and online.
    """
    def __init__(self,_bus, _loop_started,_status_service):

        if not _loop_started:
            raise Exception("Main loop must be started before creating a connection.")

        self.bus = _bus
        self.pairing_agent = None       # Application defined pairing agent.
        self.discoverable_status = 0    # Takes value 0 or 1 (not a boolean)
        self.has_modems = False         # Flag indicating if at least one modem ( BT device has been paired)
        self.is_online = False
        self.modem_object = None        # The modem path object
        self.modem_properties = None    # properties of the modem. Array[dbus.String]
        self.bt_device = None           # RPi bluetooth adapter (Hardware)
        self.manager = None             # ofono manager object
        self.READY = "READY"            # flag indicating the status of the modem


        """ 
            Status_service is a reference to the BT_link_ready service instance created by the phone manager.
            An instance is required in order to be able to invoke the emit method that signals there is a mobile phone
            connected and ready to start using.
        """
        self.status_service = _status_service

        self.get_modem_info()
        """ 
        Get the modem ( BT devices and connection status. Note that even if a modem is present it still may 
        not be online (was previously paired but hasn't connected this session)
        """
        if self.has_modems:
            self._listen_for_modem_status_change()
        """Set up modem listener even if a modem ( ie. phone) is connected in case another phone wants to take over"""
        self._listen_for_modems()
        print("Modem at start up %s" % self.modem_properties.__str__())

    def get_modem_info(self):
        if self.manager is None:
            self.manager = dbus.Interface(self.bus.get_object('org.ofono', '/'), 'org.ofono.Manager')
        self.modem_object, self.modem_properties = self.get_modem_and_properties()

    def get_modem_and_properties(self):
        """
        Flag indicating that modem exists. This is the case when at least one bt device has paired even if it is
        not currently connected
        :returns:  tuple of modem object and properties or None,None
        :side effects - Sets flags has_modem and is_online.
        """
        modem = None
        try:
            modem = self.manager.GetModems()
        except:
            pass
        if modem is not None and len(modem) > 0:
            self.has_modems = True
            self.is_online = self._modem_is_online(modem[0][1])
            return self.bus.get_object('org.ofono', modem[0][0]), modem[0][1]
        else:
            self.has_modems = False
            self.is_online = False
            return None, None

    def _listen_for_modem_status_change(self):
        """" Listener for status property change """
        if self.has_modems:
            self.modem_object.connect_to_signal('PropertyChanged', self._modem_status_change)

    def _modem_status_change(self, name, value):
        """
            Handler for modem status changes.
            If modem is connected (online) then instantiate the VoiceCallManager
            and start listening for calls.
            This is the only place where the bluetooth connction can be established.
            @name: string : Name of property change that trigger this handler
            @value: dbus datatype : The new value that property takes
        """
        if name == 'Online':
            if value == dbus.Boolean(True, variant_level=1):
                print("Previously paired mobile phone has just connected.")
                self._refresh_pulseaudio_cards()
                print("fire signal to indicate that we can start listening for calls")
                self.status_service.emit(self.READY)
            else:
                print("phone has disconnected from RPi")

    def _modem_is_online(self, props):
        """ Flag indicating that modem[0][0] is online """
        if props is not None:
            return props[dbus.String('Online')] == 1
        else:
            raise Exception("No modem available to test status")

    def _listen_for_modems(self):
        print("create listener for modems")
        self.manager.connect_to_signal('ModemAdded', self._modemAdded)

    def _modemAdded(self, object, properties):
        """ Handler for a modem being added. When a modem is added it is automatically online."""
        self.get_modem_info()
        print("A modem is been added and has connected {:s} ".format(self.modem_properties[dbus.String('Name')]))
        self.has_modems = True

        # automatically trust it.
        props = dbus.Interface(self.bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Device1", "Trusted", True)

        """Even though the modem is added and online, we need to setup listeners for when it goes offline"""
        self._listen_for_modem_status_change()
        self._refresh_pulseaudio_cards()

    def _refresh_pulseaudio_cards(self):
        """
        After establish blue tooth link between Rpi and phone, pulseaudio has to be refreshed to ensure that the newly
        connected device appears in pulseaudio's list of cards (bt devices). This is a workaround for a bug in pulseaudio.
        """
        print("Refresh bluetooth devices in pulseaudio")
        subprocess.run(["pacmd unload-module module-udev-detect && pacmd load-module module-udev-detect"], shell=True,
                       capture_output=False)

    def make_discoverable(self, duration=30):
        """
        Set the RPi BT device to discoverable and pairable for 30 seconds. This is used only for pairing
        device (e.g. a mobile phone) that has not previously been paired.
        """
        print("Placing the RPi into discoverable mode and turn pairing on")
        print(f"Discoverable for {duration} seconds only")

        self.bt_device = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez/hci0"),
                                        "org.freedesktop.DBus.Properties")
        # Check if the device is already in discoverable mode and if not then set a short discoverable period
        self.discoverable_status = self.bt_device.Get("org.bluez.Adapter1", "Discoverable")
        if self.discoverable_status == 0:
            """
            Agents manager the bt pairing process. Registering the NoInputNoOutput agent means now authentication from 
            the RPi is required to pair with it.
            """
            bt_agent_manager = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")

            if self.pairing_agent is None:
                print("registering auto accept pairing agent")
                path = "/RPi/Agent"
                self.pairing_agent = AutoAcceptAgent(self.bus, path)
                # Register application's agent for headless operation
                bt_agent_manager.RegisterAgent(path, "NoInputNoOutput")
                bt_agent_manager.RequestDefaultAgent(path)
            # Setup discoverability
            self.bt_device.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(duration))
            self.bt_device.Set("org.bluez.Adapter1", "Discoverable", True)
            self.bt_device.Set("org.bluez.Adapter1", "Pairable", True)

class AutoAcceptAgent(dbus.service.Object):
    """ Application pairing agent """

    AGENT_INTERFACE = 'org.bluez.Agent1'

    def __init__(self, bus, path):
        self.exit_on_release = True
        super().__init__(bus, path)

    def ask(self, prompt):
        try:
            return input(prompt)
        except:
            return input(prompt)

    def set_trusted(self, path):
        props = dbus.Interface(self.bus.get_object("org.bluez", path), "org.freedesktop.DBus.Properties")
        props.Set("org.bluez.Device1", "Trusted", True)

    def dev_connect(self, path):
        dev = dbus.Interface(self.bus.get_object("org.bluez", path), "org.bluez.Device1")
        dev.Connect()

    def set_exit_on_release(self, exit_on_release):
        self.exit_on_release = exit_on_release

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        print("AuthorizeService (%s, %s)" % (device, uuid))
        return

    @dbus.service.method(AGENT_INTERFACE,  in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        print("RequestPinCode (%s)" % (device))
        self.set_trusted(device)
        return self.ask("Enter PIN Code: ")

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        print("RequestPasskey (%s)" % (device))
        self.set_trusted(device)
        passkey = self.ask("Enter passkey: ")
        return dbus.UInt32(passkey)

    @dbus.service.method(AGENT_INTERFACE,
                         in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print("DisplayPasskey (%s, %06u entered %u)" %
              (device, passkey, entered))

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print("DisplayPinCode (%s, %s)" % (device, pincode))

    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print("RequestConfirmation (%s, %06d)" % (device, passkey))
        confirm = self.ask("Confirm passkey (yes/no): ")
        if confirm == "yes":
            self.set_trusted(device)
            return
        #raise pass
            #Rejected("Passkey doesn't match")

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        print("RequestAuthorization (%s)" % (device))
        auth = self.ask("Authorize? (yes/no): ")
        if (auth == "yes"):
            return
        #raise pass #Rejected("Pairing rejected")

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("Cancel")

class phone_status_service(dbus.service.Object):
    def __init__(self):
        bus_name = dbus.service.BusName("org.frank", bus=dbus.SystemBus()) # The dbus connection
        super().__init__(bus_name, "/")
        self._link_is_ready = False

    @dbus.service.signal('phone.status', signature='s')
    def emit(self, value):
        print("Emit was fired directly with param %s" % value)
        pass

class PhoneManager(object):
    CHUNK = 1024

    def __init__(self):
        """
        The PhoneManager class manages the setup and pull down of calls on an open bluetooth connection.
        """
        # misc. constants
        self.READY = "READY"            # Flag indicating that modem has changed state t being ready for calls.
        self.ALREADY_ON = "ALREADYON"   # Flag indicating that there was a phone connected at startup

        # A flag to indicate that the Mainloop has started so its okay to connect to signals.
        self.loop_started = False
        self.active_call_path = None    # path of phone (ofono modem object) currently connected
        self.call_in_progress = False

        # Set up mainloop for Dbus services and start status_service that is used to broadcast call readiness of phone
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.status_service = phone_status_service()
        self._setup_dbus_loop()  # spawn thread that monitors the mainloop.

        # bt connection object that wraps ofono functions related to bt connection
        self.bt_conn = bt_connection(self.bus, self.loop_started, self.status_service)
        # ofono object that controls volume functions. Note these functions called from telephone object.
        self.volume_controller = None
        self.mic_volume = None
        self.speaker_volume = None
        self.muted = None

        # A modem must be present and it must be online to start listening for calls.
        if self.bt_conn.has_modems and self.bt_conn.is_online:
            self._listen_for_calls(self.ALREADY_ON)
        else:
            # if not then listen on the dbus status_service for the modem to be available and online.
            self._listen_to_phone_ready_service()

        print("Bluetooth connection configured")

    def _setup_dbus_loop(self):
        """
        Start the mainloop inside a new thread. this must be executed before creating new services or subscribing to signals.
        """
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.loop = GLib.MainLoop()
        self._thread = Thread(target=self.loop.run)
        self._thread.start()
        self.loop_started = True

    def _listen_to_phone_ready_service(self):
        """
            Listen for the emit signal from custom service org.frank. Only necesary when no modem was
            present at startup
        """
        status_service_interface = dbus.Interface(self.bus.get_object('org.frank', '/'), "phone.status")
        status_service_interface.connect_to_signal('emit', self._listen_for_calls)

    def _listen_for_calls(self, value):
        if value == self.READY:
            print("Handling phone ready signal.")
        else:
            print("Modem was already connected and online")

        if self.bt_conn.has_modems:
            print("Create listener for calls")
            self.voice_call_manager = dbus.Interface(self.bt_conn.modem_object, 'org.ofono.VoiceCallManager')
            print("Device name = {:s} ".format(self.bt_conn.modem_properties[dbus.String('Name')]))
            self.bt_conn.modem_object.connect_to_signal("CallAdded", self.set_call_in_progress,
                                                 dbus_interface='org.ofono.VoiceCallManager')
            self.bt_conn.modem_object.connect_to_signal("CallRemoved", self.set_call_ended,
                                                 dbus_interface='org.ofono.VoiceCallManager')
            self.active_call_path = self.bt_conn.modem_object.object_path
            self._setup_volume_control()

    def set_call_in_progress(self, path, properties):
        """
        Event triggered when a call is initiated.
        :param path: The path (address) of the call object from ofono
        :param properties: Properties of the call
        :return:
        """
        print("Call in progress")
        direction = properties['State']   # Incoming or dialing (outbound)
        print(f"Call direction: {direction}")
        self.call_in_progress = True
        if direction == 'incoming':
            print(F"Inbound call detected on {path}")
            self.active_call_path = path
        else:
            print("Originating outbound call")
            self.active_call_path = None
    
    def answer_call(self):
        """ Answer the call on the modem path specified by self.active_call_path """
        call = dbus.Interface(self.bus.get_object('org.ofono', self.active_call_path), 'org.ofono.VoiceCall')
        time.sleep(2)
        call.Answer()
        print(f"    Voice Call {self.active_call_path} Answered")

    def set_call_ended(self, object):
        """
        Event triggered when a call is ended
        :param object: The address of the call object from ofono (just as reference, cannot be fetched anymore)
        :return:
        """
        print("Call ended!")
        self.call_in_progress = False

    def end_call(self):
        """
        Method to finalize the current (all, actually) call
        """
        self.voice_call_manager.HangupAll()

    def call(self, number, hide_id='default'):
        """
        Method to place call. It handles incorrectly dialed numbers thanks to ofono exceptions
        """
        try:
            self.voice_call_manager.Dial(str(number), hide_id)
        except dbus.exceptions.DBusException as e:
            name = e.get_dbus_name()
            if name == 'org.freedesktop.DBus.Error.UnknownMethod':
                print("Ofono not running")
                self.start_file("/home/pi/Documents/repos/bluetooth-phone/not_connected.wav")
            elif name == 'org.ofono.Error.InvalidFormat':
                print("Invalid dialed number format!")
                self.start_file("/home/pi/Documents/repos/bluetooth-phone/format_incorrect.wav")
            else:
                print(name)

    """ Volume control via ofono org.ofono.CallVolume interface"""
    def _setup_volume_control(self):
        #if self.active_call_path is not None:
        if self.bt_conn.has_modems:
            self.volume_controller = dbus.Interface(self.bus.get_object('org.ofono', self.active_call_path),
                                                    'org.ofono.CallVolume')
            self.speaker_volume = self.volume_controller.GetProperties()['SpeakerVolume']
            self.mic_volume = self.volume_controller.GetProperties()['MicrophoneVolume']
            self.muted = self.volume_controller.GetProperties()['Muted']

    """ API for controlling volume from handset."""
    def volume_up(self):
        increment = 5
        if self.volume_controller is not None:
            self.speaker_volume += increment
            self.mic_volume += increment
            self.volume_controller.SetProperty('SpeakerVolume', dbus.Byte(int(self.speaker_volume)))
            self.volume_controller.SetProperty('MicrophoneVolume', dbus.Byte(int(self.mic_volume)))

    def volume_down(self):
        increment = 5
        if self.volume_controller is not None:
            self.speaker_volume -= increment
            self.mic_volume -= increment
            self.volume_controller.SetProperty('SpeakerVolume', dbus.Byte(int(self.speaker_volume)))
            self.volume_controller.SetProperty('MicrophoneVolume', dbus.Byte(int(self.mic_volume)))

    def mute_toggle(self):
        """ There is a bug in ofono. Mute property setter is not implemented"""
        print("Mute not implemented.")
        # if self.volume_controller is not None:
        #     self.muted = self.volume_controller.GetProperties()[dbus.String('Muted')]
        #     self.muted = not self.muted
        #     print(f"muted {self.muted}")
        #     self.volume_controller.SetProperty('Muted', dbus.Boolean(self.muted))


    def start_file(self, filename, loop=False):
        """
        Start a thread reproducing an audio file
        :param filename: The name of the file to play
        :param loop: If the file should be played as a loop (like in the case of the dial tone)
        """
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
            stream = alsaaudio.PCM(type=alsaaudio.PCM_PLAYBACK,
                                   mode=alsaaudio.PCM_NORMAL)
            stream.setchannels(f.getnchannels())
            stream.setrate(f.getframerate())
            # read data
            data = f.readframes(self.CHUNK)

            # play stream
            while data and not self.stop_audio:
                stream.write(data)
                data = f.readframes(self.CHUNK)
        else:
            # open a wav format music
            f = wave.open(filename, "rb")
            # open stream
            stream = alsaaudio.PCM(type=alsaaudio.PCM_PLAYBACK,
                                   mode=alsaaudio.PCM_NORMAL)
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

        self.phone_manager = PhoneManager()
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
            GPIO.add_event_detect(self.discoverable_pin, GPIO.RISING, callback=self.make_discoverable, bouncetime=2000)

        if self.has_volume_controller:
            # Set volume up pin
            print("Set up volume controls")
            GPIO.setup(self.volume_up_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_up_pin, GPIO.RISING, callback=self.volume_up, bouncetime=2000)
            # Set volume down pin
            GPIO.setup(self.volume_down_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_down_pin, GPIO.RISING, callback=self.volume_down, bouncetime=2000)
            # set up mute toggling function
            GPIO.setup(self.volume_mute_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(self.volume_mute_pin, GPIO.RISING, callback=self.volume_mute_toggle, bouncetime=2000)
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
        GPIO.add_event_detect(self.receiver_pin, GPIO.BOTH, callback=self.receiver_changed, bouncetime=1000)

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
        self.phone_manager.volume_up()
        print(f"Volume Up: Mic volume = {self.phone_manager.mic_volume}")

    def volume_down(self, pin):
        self.phone_manager.volume_down()
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
                noise on the dialer pins can cause spiriuos rising edges when the reciever is lifted or put down.
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
    HOERER_PIN = 13
    NS_PIN = 19
    DISCOVERABLE_PIN = 20   # make RPi discoverable - Temporary on white button

    # Dictionary of pins for volume control functions. If no volume controls then set to None.
    VOLUME_PIN_DICT = {'VOLUME_UP_PIN': 23,
                       'VOLUME_DOWN_PIN': 24,
                       'VOLUME_MUTE_PIN': 25
                       }

    t = Telephone(NS_PIN, HOERER_PIN, DISCOVERABLE_PIN, VOLUME_PIN_DICT)

    try:
        t.dialing_handler()
    except KeyboardInterrupt:
        pass
    t.close()

