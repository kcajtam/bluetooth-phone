import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import time
from threading import Thread
import wave
import alsaaudio

import dbus_custom_services
import bluetooth
import config


class PhoneManager(object):

    CHUNK = 1024

    def __init__(self):
        """
        The PhoneManager class manages the setup and pull down of calls on an open bluetooth connection.
        """

        # A flag to indicate that the Mainloop has started so its okay to connect to signals.
        self.loop_started = False
        self.active_call_path = None  # path of phone (ofono modem object) currently connected
        self.call_in_progress = False

        # Set up mainloop for Dbus services and start status_service that is used to broadcast call readiness of phone
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.status_service = dbus_custom_services.phone_status_service()
        self._setup_dbus_loop()  # spawn thread that monitors the mainloop.

        # bt connection object that wraps ofono functions related to bt connection
        self.bt_conn = bluetooth.connection(self.bus, self.loop_started, self.status_service)
        # ofono object that controls volume functions. Note these functions called from telephone object.
        self.volume_controller = None
        self.mic_volume = None
        self.speaker_volume = None
        self.muted = None  # Not implemented: Ofono has an open bug from 2014 identifying that this feature is not implemented.

        # A modem must be present and it must be online to start listening for calls.
        if self.bt_conn.has_modems and self.bt_conn.is_online:
            self._listen_for_calls(config.ALREADY_ON)
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
        if value == config.READY:
            print("Handling phone ready signal.")
        elif value == config.ALREADY_ON:
            print("Modem was already connected and online")
        else:
            return None

        if self.bt_conn.has_modems:
            print("Create listener for calls")
            self.voice_call_manager = dbus.Interface(self.bt_conn.modem_object, 'org.ofono.VoiceCallManager')
            print("Device name = {:s} ".format(self.bt_conn.modem_name))
            self.bt_conn.modem_object.connect_to_signal("CallAdded", self.set_call_in_progress,
                                                        dbus_interface='org.ofono.VoiceCallManager')
            self.bt_conn.modem_object.connect_to_signal("CallRemoved", self.set_call_ended,
                                                        dbus_interface='org.ofono.VoiceCallManager')
            self.active_call_path = self.bt_conn.modem_object.object_path
            self._setup_volume_control()

    def null_handler(self,value):
        pass

    def set_call_in_progress(self, path, properties):
        """
        Event triggered when a call is initiated.
        :param path: The path (address) of the call object from ofono
        :param properties: Properties of the call
        :return:
        """
        print("Call in progress")
        direction = properties['State']  # Incoming or dialing (outbound)
        print(f"Call direction: {direction}")
        self.call_in_progress = True
        if direction == 'incoming':
            print(F"Inbound call detected on {path}")
            self.active_call_path = path
            self.status_service.ring(config.RING_START)
            #self.status_service.send_to_ringer(config.RING_START, reply_handler=self.null_handler,
            #                                   error_handler=self.null_handler)
        else:
            print("Originating outbound call")
            self.active_call_path = None

    def answer_call(self):
        """
            Answer the call on the modem path specified by self.active_call_path
        """

        """ First thing is to stop the ringer via the Dbus singalling."""
        #self.status_service.send_to_ringer(config.RING_STOP, reply_handler=self.null_handler,
        #                                   error_handler=self.null_handler)
        self.status_service.ring(config.RING_STOP)
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
        print("Call ended.")
        self.call_in_progress = False
        """Send the ringer_stop signal to the RingerManager to stop the ringing"""
        #self.status_service.send_to_ringer(config.RING_STOP, reply_handler=self.null_handler,
        #                                   error_handler=self.null_handler)
        self.status_service.ring(config.RING_STOP)
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
        # if self.active_call_path is not None:
        if self.bt_conn.has_modems:
            self.volume_controller = dbus.Interface(self.bus.get_object('org.ofono', self.active_call_path),
                                                    'org.ofono.CallVolume')
            self.speaker_volume = self.volume_controller.GetProperties()['SpeakerVolume']
            self.mic_volume = self.volume_controller.GetProperties()['MicrophoneVolume']
            self.muted = self.volume_controller.GetProperties()['Muted']

    """ API for controlling volume from handset."""

    def volume_up(self, increment=5):
        if self.volume_controller is not None:
            self.speaker_volume += increment
            self.mic_volume += increment
            self.volume_controller.SetProperty('SpeakerVolume', dbus.Byte(int(self.speaker_volume)))
            self.volume_controller.SetProperty('MicrophoneVolume', dbus.Byte(int(self.mic_volume)))

    def volume_down(self, increment=5):
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