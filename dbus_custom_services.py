import dbus
import dbus.service

class AutoAcceptAgent(dbus.service.Object):
    """
        Application pairing agent. Defualt use is in NoInputNoOutput mode so none of the security methods will be used
    """

    AGENT_INTERFACE = 'org.bluez.Agent1'

    def __init__(self, bus, path):
        self.exit_on_release = True
        super().__init__(bus, path)


    """ def ask(self, prompt):
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
        #raise pass #Rejected("Pairing rejected") """

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("Cancel")

class phone_status_service(dbus.service.Object):
    """
    dbus service for broadcasting that the phone is ready to start accepting calls: a mobile has been connected, the
    bluetooth conenction has been refreshed in pulse audio etc.
    """
    def __init__(self):
        bus_name = dbus.service.BusName("org.frank", bus=dbus.SystemBus()) # The dbus connection
        super().__init__(bus_name, "/")
        self._link_is_ready = False
        self._ring_bell = False

    @dbus.service.signal('phone.status', signature='s')
    def emit(self, value):
        print("Emit was fired directly with param %s" % value)

    @dbus.service.signal('phone.status', signature='s')
    def ring(self, value):
        """params: value (config.RING_START, config.RING_STOP)
            description: single to start/stop the ringer"""
        print(f"Ring signal fired {value}")
     

    @dbus.service.method('phone.status', in_signature='s', out_signature='s')
    def send_to_ringer(self, value):
        print(f"received request to control ringer {value}")
        self.ring(value)
        return "OK"
