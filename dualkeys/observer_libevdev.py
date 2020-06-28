# import evdev
import pyudev as udev
import re
import logging # TODO might have to share the logger instead
import threading
import libevdev

from .types import *
from .event_pusher_libevdev import EventPusherWorker

class ObserverThreadWrapper():
    def __init__(self, main_instance):
        self.main_instance = main_instance
        self.error_queue = self.main_instance.error_queue
        self.event_handler = main_instance.event_handler
        self.evdev_re = re.compile(r'^/dev/input/event\d+$')
        self.device_lock = threading.Lock()
        self.event_pushers = {}

    def _monitor_callback(self, device):
        """
        Worker function for monitor to add and remove devices.
        """

        if True:
            logging.info(f"Udev reported: {device.action} on {device.device_path}, device node: {device.device_node}")
        remove_action = device.action == 'remove'
        add_action = device.action == 'add' and device.device_node is not None \
                and device.device_node != self.main_instance.event_handler.ui.devnode and self.evdev_re.match(device.device_node) is not None
        if remove_action or add_action:
            # Device removed, let's see if we need to remove it from the lists
            # Note that this probably won't happen, since the device will report an event and
            # the IOError below will get triggered
            try:
                if remove_action:
                    self.remove_device(device.device_node)
                elif add_action:
                    self.add_device(device.device_node)
                    # raise Exception("Removed!")
            except Exception as e:
                self.error_queue.put(e)
                raise e

    @staticmethod
    def get_handle_type(device):
        """
        Classify Evdev device according to whether it is a
        mouse or a keyboard.

        For now, just check whether it has an 'A' key or a left
        mouse button.
        """

        if device.has(libevdev.EV_KEY.BTN_LEFT): # Check if it is a mouse
            return HandleType.NOGRAB
        elif device.has(libevdev.EV_KEY.KEY_A): # Check if there is 'KEY_A'
            return HandleType.GRAB
        else:
            return HandleType.IGNORE


    def add_device(self, device_node, handle_type = None):
        """
        Add device to event_pushers, grab_devices and selector if
        it matches the output of get_handle_type.
        """

        self.device_lock.acquire()

        with open(device_node, "rb") as fd:
            device = libevdev.Device(fd)
            if handle_type is None:
                handle_type = self.__class__.get_handle_type(device)
            grab = handle_type == HandleType.GRAB

        if handle_type != HandleType.IGNORE:
            logging.info("Adding device {}".format(device_node))
            event_pusher = EventPusherWorker(main_instance = self.main_instance,
                    name = f"event_pusher_{device_node}",
                    device_node = device_node,
                    grab = grab
                    )
            event_pusher.start()
            logging.debug(f"{event_pusher.name} started")

            self.event_pushers[device_node] = event_pusher

        # time.sleep(0.2)
        # device.repeat = evdev.device.KbdInfo(300, 600000)
        # print("Device {}, repeat = {}".format(device, device.repeat))
        # logging.debug("Device {}".format(device))
        self.device_lock.release()

    def remove_device(self, device_node):
        """
        Remove device, complementary operation to add_device.
        """

        # Handle udev and evdev devices
        self.device_lock.acquire()
        if device_node in self.event_pushers:
            # selector.unregister(event_pushers[fn])
            # self.event_pushers.raise_exception(TerminationException)
            # TODO Check if this is necessary/works if it already has ben terminated
            self.event_pushers[device_node].join()
            del self.event_pushers[device_node]
        self.device_lock.release()

    def start(self):
        context = udev.Context()
        monitor = udev.Monitor.from_netlink(context)
        monitor.filter_by('input')

        self.observer = udev.MonitorObserver(monitor, callback = self._monitor_callback)
        self.observer.start()
        logging.debug("device_monitor started")

    def stop(self):
        for worker in self.event_pushers.values():
            # thread.raise_exception(TerminationException)
            worker.terminate()
        self.observer.stop()

