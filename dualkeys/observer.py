import evdev
import pyudev as udev
import re
import logging # TODO might have to share the logger instead

class ObserverThreadWrapper():
    def __init__(self, main_instance):
        self.main_instance = main_instance
        self.error_queue = self.main_instance.error_queue
        self.event_handler = main_instance.event_handler
        self.event_pusher = main_instance.event_pusher
        self.evdev_re = re.compile(r'^/dev/input/event\d+$')

    def _monitor_callback(self, device):
        """
        Worker function for monitor to add and remove devices.
        """

        if True:
            print(f"Udev reported: {device.action} on {device.device_path}, device node: {device.device_node}")
        remove_action = device.action == 'remove'
        add_action = device.action == 'add' and device.device_node is not None and device.device_node != self.main_instance.event_handler.ui.device.path and self.evdev_re.match(device.device_node) is not None
        if remove_action or add_action:
            # Device removed, let's see if we need to remove it from the lists
            # Note that this probably won't happen, since the device will report an event and
            # the IOError below will get triggered
            try:
                if remove_action:
                    self.event_pusher.remove_device(device)
                elif add_action:
                    evdev_device = evdev.InputDevice(device.device_node)
                    self.event_pusher.add_device(evdev_device)
                    # raise Exception("Removed!")
            except Exception as e:
                self.error_queue.put(e)
                raise e

    def start(self):
        context = udev.Context()
        monitor = udev.Monitor.from_netlink(context)
        monitor.filter_by('input')

        self.observer = udev.MonitorObserver(monitor, callback = self._monitor_callback)
        self.observer.start()
        logging.debug("device_monitor started")

    def stop(self):
        self.observer.stop()

