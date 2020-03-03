import dbus
import dbus.service
import dbus_custom_services
import subprocess
import config


class connection(object):
    """
    Singleton class that deals with the bluetooth connection between phone and RPi.
    Notes:
        In ofono a modem is a previously paired bluetooth device. The list of all such modems is
        returned by ofono.Manager.GetModems() Note that modem can be present but there may be no active
        connection i.e. it is offline. In order to start accepting or making call the Modem must be present and online.
    """
    def __init__(self,_bus, _loop_started, _status_service):

        if not _loop_started:
            raise Exception("Main loop must be started before creating a connection.")

        self.bus = _bus
        self.pairing_agent = None       # Application defined pairing agent.
        self.discoverable_status = 0    # Takes value 0 or 1 (not a boolean)
        self.has_modems = False         # Flag indicating if at least one modem ( BT device has been paired)
        self.is_online = False
        self.all_modems = {}            # dictionary 
        self.modem_object = None        # The modem path object
        self.modem_properties = None    # properties of the modem. Array[dbus.String]
        self.bt_device = None           # RPi bluetooth adapter (Hardware)
        self.manager = None             # ofono manager object

        """ 
            Status_service is a reference to the BT_link_ready service instance created by the phone manager.
            An instance is required in order to be able to invoke the emit method that signals there is a mobile phone
            connected and ready to start using.
        """
        self.status_service = _status_service
        self._register_pairing_agent()
        self.manager = dbus.Interface(self.bus.get_object('org.ofono', '/'), 'org.ofono.Manager')
        """ 
        Get the modem ( BT devices and connection status. Note that even if a modem is present it still may 
        not be online (was previously paired but hasn't connected this session)
        """
        self.get_modem_and_prop_objects()
        if self.has_modems:
            self._listen_for_modem_status_change()
        """Set up modem listener even if a modem ( ie. phone) is connected in case another phone wants to take over"""
        self._listen_for_modems()
        print("Modem at start up %s" % self.modem_properties.__str__())

    def get_modem_and_prop_objects(self, path=None):
        """
        Flag indicating that modem exists. This is the case when at least one bt device has paired even if it is
        not currently connected
        :returns:  tuple of modem object and properties or None,None
        :side effects - Sets flags has_modem and is_online.
        """
        modem = None
        all_modems = []

        try:
            all_modems = self.manager.GetModems()
        except:
            pass
        try:
            if not path is None:
                modem = all_modems[path]
            elif len(all_modems) > 0:
                for m in all_modems:
                    self.bus.get_object('org.ofono', m[0]).connect_to_signal('PropertyChanged', self._modem_status_change)
                    # Get the first modem that is online
                    if self._modem_is_online(props=m[1]) == dbus.Boolean(True, variant_level=1):
                        modem = m
        except:
            pass

        if modem is not None:
            self.has_modems = True
            self.is_online = self._modem_is_online(props=modem[1])
            self.modem_object = self.bus.get_object('org.ofono', modem[0])
            self.modem_properties = modem[1]
        else:
            self.has_modems = False
            self.is_online = False
            self.modem_object = None
            self.modem_properties = None

    def _listen_for_modem_status_change(self):
        """" Listener for status property change """
        print("Start Listening for modems")
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
        print(f"modem property {name} changed to {value.__str__()}")
        if name == 'Online':
            print("Modem add detected")
            if value == dbus.Boolean(True, variant_level=1):
                print("Previously paired mobile phone has just connected.")
                self._refresh_pulseaudio_cards()
                print("fire signal to indicate that we can start listening for calls")
                self.status_service.emit(config.READY)
            else:
                print("phone has disconnected from RPi")

    def _modem_is_online(self, *, props):
        """ Flag indicating that modem[0][0] is online """
        if props is not None:
            return props[dbus.String('Online')] == 1
        else:
            raise Exception("No modem available to test status")

    def _listen_for_modems(self):
        print("create listener for modems")
        self.manager.connect_to_signal('ModemAdded', self._modemAdded)

    def _modemAdded(self, path, properties):
        """ Handler for a modem being added. When a modem is added it is automatically online."""
        print(f"Modem path {path}")
        self.get_modem_and_prop_objects(path)
        print("A modem is been added and has connected {:s} ".format(self.modem_properties[dbus.String('Name')]))

        # """Even though the modem is added and online, we need to setup listeners for when it goes offline"""
        # self._listen_for_modem_status_change()
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
        self.bt_device = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez/hci0"),
                                        "org.freedesktop.DBus.Properties")
        # Check if the device is already in discoverable mode and if not then set a short discoverable period
        self.discoverable_status = self.bt_device.Get("org.bluez.Adapter1", "Discoverable")
        if self.discoverable_status == 0:
            """
            Agents manager the bt pairing process. Registering the NoInputNoOutput agent means now authentication from 
            the RPi is required to pair with it.
            """
            print("Placing the RPi into discoverable mode and turn pairing on")
            print(f"Discoverable for {duration} seconds only")


            # Setup discoverability
            self.bt_device.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(duration))
            self.bt_device.Set("org.bluez.Adapter1", "Discoverable", True)
            self.bt_device.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(duration))
            self.bt_device.Set("org.bluez.Adapter1", "Pairable", True)

    def _register_pairing_agent(self):
        """Registered bluetooth pairing agent that will autoaccept pairing requests"""
        if self.pairing_agent is None:
            print("registering auto accept pairing agent")
            path = "/RPi/Agent"
            self.pairing_agent = dbus_custom_services.AutoAcceptAgent(self.bus, path)
            # Register application's agent for headless operation
            bt_agent_manager = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
            bt_agent_manager.RegisterAgent(path, "NoInputNoOutput")
            bt_agent_manager.RequestDefaultAgent(path)

    def _listen_to_Rpi_hci0(self):
        """ Set up listener for HCI0 status changes """
        _hci0 = dbus.Interface(self.bus.get_object("org.bluez","/org/bluez/hci0"), "org.bluez.Adapter1")
        _hci0.connect_to_signal("PropertyChanged",_hci0_property_handler)

    def _hci0_property_handler(self,prop, props, prop_vals):
        pass

