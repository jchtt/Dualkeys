#! /usr/bin/env python
# vim: sw=4 ts=4 et

# Dualkeys: Dual-role keys with evdev and uinput
# Copyright (C) 2017-2018 jchtt

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# import argparse
import configargparse
from ast import literal_eval
import evdev
from evdev import ecodes
from evdev.util import resolve_ecodes
import pyudev as udev
import errno
import re
import datetime
import time
from enum import Enum, auto
from dualkeys.linked_list import *
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
import asyncio
import queue
import threading
import functools
import time
import sys

# from select import select
from collections import deque
# import threading
# import sys
# import time

DEBUG = False
PUSH_PRE_EMPTIVE = True # TODO: this should be entirely governed by arguments

# TODO: replace by TerminationException
class ShutdownException(Exception):
    pass

class TerminationException(Exception):
    pass

class DeviceWrapper():
    def __init__(self, grab = False, input_device = None, future = None):
        self.grab = grab
        self.input_device = input_device
        self.future = future

# class HandleType(Enum):
#     """
#     Simple enum type to handle different kinds of input devices,
#     according to whether we do not want to handle them (IGNORE),
#     want to grab the input (GRAB) (everything except mice), or not (NOGRAB)
#     (mice).
#     """
#     IGNORE = auto()
#     GRAB = auto()
#     NOGRAB = auto()

HandleType = Enum("HandleType", [
    "IGNORE",
    "GRAB",
    "NOGRAB"
    ])

ResolutionType = Enum("ResolutionType", [
    "UNRESOLVED",
    "REGULAR",
    "DUAL_REGULAR",
    "DUAL_MOD"
    ])

PreEmptiveType = Enum("PreEmptiveType", [
    "REGULAR",
    "MOD"
    ])




class DualKey:
    """
    Object to store information about dual-role keys.

    primary_key: The key that appears on the keyboard
    single_key: The key that gets triggered if the key is pressed and released
        on its own
    mod_key: The key that gets triggered if the key is pressed together with
        another key
    """

    def __init__(self, primary_key, single_key, mod_key, down_trigger = False):
        self.primary_key = primary_key
        self.single_key = single_key
        self.mod_key = mod_key
        self.down_trigger = down_trigger

class UnresolvedKey:
    """
    Object to store the response to a key.

    For now, only use it to indicate whether a key should go up again,
    which we do not want if the same modifier was pressed before.
    """

    def __init__(self, 
            scancode,
            pre_emptive_pressed = False,
            mod_key = None,
            time_pressed = time.time(),
            keystate = 1,
            resolution_type = ResolutionType.UNRESOLVED
            ):
        self.scancode = scancode
        self.pre_emptive_pressed = pre_emptive_pressed
        self.mod_key = mod_key
        self.time_pressed = time_pressed
        self.keystate = keystate
        self.resolution_type = resolution_type

    def __str__(self):
        return f"({self.scancode}, {self.keystate})"

def codes_to_keys(code):
    if type(code) is int:
        return "[" + evdev.ecodes.keys[code] + "]"
    else:
        return "[" + ", ".join(map(evdev.ecodes.keys.get, code)) + "]"

def send_key(state_obj, scancode, keystate, bypass = False):
    """
    Send a key event via uinput and handle key counters for modifiers
    """
    # state_obj.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
    # state_obj.ui.syn()
    if bypass:
        state_obj.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
        state_obj.ui.syn()
        return

    count = state_obj.key_counter.setdefault(scancode, 0)
    if keystate == 1:
        if count == 0:
            state_obj.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
            state_obj.ui.syn()
            if DEBUG:
                print(f'Pushed {scancode}, {evdev.ecodes.KEY[scancode]}')
            if state_obj.history is not None:
                state_obj.history[-1][-1].append(
                        (scancode, ecodes.KEY[scancode], keystate)
                        )
        state_obj.key_counter[scancode] += 1
    elif keystate == 0:
        if count == 1:
            state_obj.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
            state_obj.ui.syn()
            if DEBUG:
                print(f'Lifted {scancode}, {evdev.ecodes.KEY[scancode]}')
            if state_obj.history is not None:
                state_obj.history[-1][-1].append(
                        (scancode, ecodes.KEY[scancode], keystate)
                        )
        state_obj.key_counter[scancode] -= 1


class DualkeysState:
    def __init__(self, args):
        self.registered_keys = {}
        self.pre_emptive_mods = set(args.pre_emptive_mods)
        self.kill_switches = args.kill_switch
        self.handle_repeat = args.repeat_timeout is not None
        self.repeat_timeout = float(args.repeat_timeout) / 1000 if self.handle_repeat else None
        self.repeat_keys = args.repeat_keys
        self.handle_idle = args.idle_timeout is not None
        self.idle_timeout = float(args.idle_timeout) / 1000 if self.handle_idle else None
        self.idle_keys = args.idle_keys
        self.angry_keys = args.angry_keys
        self.angry_key_prefix = args.angry_key_prefix
        self.ignore_keys = args.ignore

        if len(self.angry_keys) > 0:
            self.history = deque(maxlen = args.angry_key_history)
        else:
            self.history = None

        # Define registered keys
        if args.key is not None:
            for keys in args.key:
                self.registered_keys[keys[0]] = DualKey(*keys)
        print_registered_keys(self.registered_keys)

        # Main status indicator
        # TODO: include in constructor
        self.event_list = DLList()
        self.conflict_list = {}
        self.key_counter = {}
        self.resolution_dict = {} # Remember currently unresolved keys
        self.pre_emptive_dict = {}
        self.back_links = {}
        self.last_pressed = {}

class AsyncLoopThread(threading.Thread):
    def __init__(self,
            # group = None,
            # target = None,
            # name = None,
            # daemon = None,
            shutdown_flag = None,
            cleanup_callback = None,
            *args,
            **kw
            ):

        super().__init__(*args, **kw)
        self.shutdown_flag = shutdown_flag
        self.cleanup_callback = cleanup_callback
        self.loop = asyncio.new_event_loop()
        # self.target = target

    async def _shutdown(self, loop):
        # Cancel all tasks
        logging.info('Executing shutdown')
        if self.cleanup_callback is not None:
            self.cleanup_callback()
        tasks = [t for t in asyncio.all_tasks(loop = loop) if t is not
                 asyncio.current_task()]
        logging.debug(f"Cancelling {len(tasks)} outstanding tasks")

        [task.cancel() for task in tasks]

        logging.debug('Collecting all tasks')
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    # def _handle_exception(self, loop, context):
    #     message = context.get("exception", context["message"])
    #     print(f'Exception occurred in async code: {message}')
    #     asyncio.create_task(self._shutdown(loop))
    
    def _wait_for_shutdown(self, shutdown_flag):
        self.shutdown_flag.wait()
        raise TerminationException('Termination scheduled')

    def run(self):
        # calling start will execute "run" method in a thread
        try:
            asyncio.set_event_loop(self.loop)
            # loop.set_exception_handler(self._handle_exception)
            if self.shutdown_flag is None:
                self.loop.run_until_complete(self._target(*self._args, **self._kwargs))
            else:
                task = self.loop.run_in_executor(None, self._wait_for_shutdown, self.shutdown_flag)
                self.loop.run_until_complete(asyncio.gather(
                    self._target(*self_args, **self._kwargs),
                    task
                    ))
            # loop.create_task(self._shutdown(self.loop))
        except TerminationException as e:
            print('Termination requested')
        finally:
            loop.run_until_complete(self._shutdown(self.loop))

class EventPusherThread(AsyncLoopThread):
    def __init__(self, main_instance, *args, **kw):
        self.event_queue = queue.Queue()
        self.timing_stats = {}
        self.do_timing = main_instance.args.timing
        self.do_print = main_instance.args.print
        self.listen_devices = {}
        self.main_instance = main_instance

        # TODO: more fields needed for the actual work, including all the registered devices
        # Although I guess we could also put those with the observer.
        # Let's see what we actually need here

    @staticmethod
    def get_handle_type(device):
        """
        Classify Evdev device according to whether it is a
        mouse or a keyboard.

        For now, just check whether it has an 'A' key or a left
        mouse button.
        """

        caps = device.capabilities(verbose = False)
        keys = caps.get(1)
        if keys:
            if 272 in keys: # Check if it is a mouse
                return HandleType.NOGRAB
            elif 30 in keys: # Check if there is 'KEY_A'
                return HandleType.GRAB
            else:
                return HandleType.IGNORE
        else:
            return HandleType.IGNORE

    def remove_device(self, device):
        """
        Remove device, complementary operation to add_device.

        Works on Evdev and Udev devices.
        """

        # Handle udev and evdev devices
        if type(device) is evdev.device.InputDevice:
            fn = device.path
        else:
            fn = device.device_node
        if fn in self.listen_devices:
            # selector.unregister(listen_devices[fn])
            self.loop.call_soon_threadsafe(self.listen_devices[fn].future.cancel)
            del self.listen_devices[fn]

    async def put_events(self, device):
        """
        Coroutine for putting events in the event queue.
        """

        async for event in device.async_read_loop():
            try:
                if event.type == evdev.ecodes.EV_KEY:
                    
                    self.event_queue.put((device, event))
                    # print("Done putting")
            except IOError as e:
                # Check if the device got removed, if so, get rid of it
                if e.errno != errno.ENODEV: raise
                logging.debug("Device {0.fn} removed.".format(device))
                remove_device(device, listen_devices, grab_devices, device_futures, event_loop)
                break
            except BaseException as e:
                error_queue.put(e)
                raise e

    def add_device(self, device, handle_type = None):
        """
        Add device to listen_devices, grab_devices and selector if
        it matches the output of get_handle_type.
        """

        logging.debug("Adding device {}".format(device))
        if handle_type is None:
            handle_type = self.__class__.get_handle_type(device)
        grab = handle_type == HandleType.GRAB
        if grab:
            device.grab()
        future = asyncio.run_coroutine_threadsafe(self.put_events(device), self.loop)
        device_wrapper = DeviceWrapper(grab = grab, input_device = device, future)
        self.listen_devices[device.path] = device_wrapper
        # time.sleep(0.2)
        # device.repeat = evdev.device.KbdInfo(300, 600000)
        # print("Device {}, repeat = {}".format(device, device.repeat))
        # logging.debug("Device {}".format(device))

    def _target(self):
        """
        This only serves for initialization, more routines are added here and later by
        the observer thread.
        """

        device_lock = threading.Lock() # Lock for access to the dicts etc.

        # Find all devices we want to reasonably listen to
        # If listen_device is given, only listen on that one
        if self.main_instance.listen_device is not None:
            self.add_device(self.main_instance.listen_device, handle_type = HandleType.GRAB)
        else:
            for device in self.main_instance.all_devices:
                # if device.path != "/dev/input/event5":
                if True:
                    self.add_device(device)



class EventHandlerThread(threading.Thread):
    """
    Thread that handles the events in the event queue
    """

    def __init__(*args, **kw):
        super().__init__(*args, **kw)

    def print_event(state_obj, event, grabbed = True):
        """
        Alternative callback function that passes everything through,
        for debugging purposes only.
        """

        if event.type == evdev.ecodes.EV_KEY:
            key_event = evdev.util.categorize(event)
            # if key_event.keystate <= 1:
            if True:
                print('scancode = {}, keystate = {}, keycode = {}'.format(key_event.scancode, key_event.keystate, key_event.keycode))
            if grabbed:
                send_key(state_obj, key_event.scancode, key_event.keystate)
            if key_event.scancode in state_obj.kill_switches:
                state_obj.error_queue.put(ShutdownException())
        return True

    # STOP HERE, need to finish the handler

    def run():
        """
        Worker function for event-consumer to handle events.
        """

        try:
            if self.do_timing:
                self.timing_stats["total"] = 0
                self.timing_stats["calls"] = 0
            while True:
                logging.debug("-"*20)
                elem = self.event_queue.get()
                if type(elem) is ShutdownException:
                    logging.info("event_consumer was asked to shut down")
                    break 
                else:
                    (device, event) = elem
                # print("Pre-lock")
                # device_lock.acquire()
                    # if event.type == evdev.ecodes.EV_KEY:
                logging.debug()
                logging.debug("Active keys: {}".format(codes_to_keys(ui.device.active_keys())))
                logging.debug()
                try:
                    if self.do_print:
                        ret = print_event(state_obj, event, grab_devices[device.path])
                    else:
                        ret = handle_event(device.active_keys(), event, state_obj, grab_devices[device.path], pre_emptive = PUSH_PRE_EMPTIVE)
                except IOError as e:
                    # Check if the device got removed, if so, get rid of it
                    if e.errno != errno.ENODEV: raise
                    if DEBUG:
                        # if True:
                        print("Device {0.fn} removed.".format(device))
                        remove_device(device, listen_devices, grab_devices, device_futures, event_loop)
                finally:
                    event_queue.task_done()
                    device_lock.release()
                    # print("Past lock")
        except Exception as e:
            error_queue.put(e)
            raise e

class ObserverThreadWrapper():
    def __init__(self, main_instance):
        self.main_instance = main_instance
        self.event_handler = main_instance.event_handler
        self.event_pusher = main_instance.event_pusher
        self.evdev_re = re.compile(r'^/dev/input/event\d+$')

    @staticmethod
    def _monitor_callback(device):
        """
        Worker function for monitor to add and remove devices.
        """

        if True:
            print(f"Udev reported: {device.action} on {device.device_path}, device node: {device.device_node}")
        remove_action = device.action == 'remove'
        add_action = device.action == 'add' and device.device_node is not None and device.device_node != self.ui.device.path and self.evdev_re.match(device.device_node) is not None
        if remove_action or add_action:
            # device_lock.acquire()
            # TODO: figure out locks
            # print("Locked by monitor")

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
            except BaseException as e:
                self.main_instance.error_queue.put(e)
                raise
            finally:
                # device_lock.release()
                # if DEBUG:
                #     print("Lock released by monitor")
                # TODO: figure out when to lock
                pass

    def start(self):
        context = udev.Context()
        monitor = udev.Monitor.from_netlink(context)
        monitor.filter_by('input')

        observer = udev.MonitorObserver(monitor, callback = self.__class__._monitor_callback, name = "device_monitor")
        observer.start()
        logging.debug("device_monitor started")



##########################
# Key handling functions #
##########################

## New

def handle_event(active_keys, event, state_obj, grabbed = True, pre_emptive = False):
    """
    Handle the incoming key event. Main callback function.
    """

    # Only act on key presses
    if event.type != evdev.ecodes.EV_KEY:
        return True

    if TIMING:
        cur_time = time.time()

    key_event = evdev.util.categorize(event)

    # Don't act on weird keys
    if key_event.scancode in state_obj.ignore_keys:
        return True
    # if key_event.scancode == 48:
    #     raise KeyboardInterrupt
    if DEBUG:
        print("Received = {}, keystate = {}, key = {}, grabbed = {}.".format(key_event.scancode, key_event.keystate, evdev.ecodes.KEY.get(key_event.scancode, None), grabbed))

    # Handle kill switch, shutdown
    if key_event.scancode in state_obj.kill_switches:
        state_obj.error_queue.put(ShutdownException())
        return False

    # Handle angry key, save the history
    if key_event.scancode in state_obj.angry_keys and \
            key_event.keystate == 1:
        if DEBUG:
            print('Angry key triggered')
        today = datetime.datetime.now()
        today_str = today.strftime('%Y-%m-%d_%H-%M-%S')
        with open(state_obj.angry_key_prefix + '-' + today_str + '.txt', 'w') as f:
            f.write('\n'.join([', '.join([str(x) for x in elem])
                for elem in state_obj.history])) # TODO: nicer output, date stamp
            # f.write(str(state_obj.history))

    # Save history
    if state_obj.history is not None:
        state_obj.history.append(
                [(key_event.scancode,
                    ecodes.KEY.get(key_event.scancode),
                    key_event.keystate), []]
                )

    if not grabbed:
        handle_ungrabbed_key(state_obj, key_event, pre_emptive)
    else:
        if key_event.keystate == 1:
            # Key down
            # if DEBUG:
            #     print("Down.")
            if key_event.scancode not in state_obj.registered_keys:
                handle_regular_key_down(state_obj, key_event, pre_emptive)
            else:
                handle_special_key_down(state_obj, key_event, pre_emptive)
        # Key up
        elif key_event.keystate == 0:
            # if DEBUG:
            #     print("Key up")
            if key_event.scancode not in state_obj.registered_keys:
                handle_regular_key_up(state_obj, key_event, pre_emptive)
            else:
                handle_special_key_up(state_obj, key_event, pre_emptive)
        elif state_obj.handle_idle and key_event.keystate == 2:
            # Key repeat
            if DEBUG:
                print("Key repeat")
            handle_key_repeat(state_obj, key_event, pre_emptive)
            # if key_event.scancode not in state_obj.registered_keys:
            #     handle_regular_key_repeat(state_obj, key_event, pre_emptive)
            # else:
            #     handle_special_key_repeat(state_obj, key_event, pre_emptive)
        else:
            if DEBUG:
                print("Not handling, unknown keystate")

    if DEBUG:
        print("Done.")
        print("event_list: {}".format(state_obj.event_list))
        print("resolution_dict: {}".format(state_obj.resolution_dict))
        print("key_counter: {}".format({k: v for (k, v) in state_obj.key_counter.items() if v != 0}))
    if TIMING:
        state_obj.timing_stats["total"] += time.time() - cur_time
        state_obj.timing_stats["calls"] += 1
        if state_obj.timing_stats["calls"] % 10 == 0:
            print("Average time/call = {}".format(state_obj.timing_stats["total"]/state_obj.timing_stats["calls"]))

    return True

def handle_regular_key_down(state_obj, key_event, pre_emptive):
    # Regular key goes down: either there is no list, then send;
    # otherwise, put it in the queue and resolve non-tf keys

    global DEBUG

    to_push = key_event.scancode
    # TODO: figure out why I handled pre_emptive separately here
    if not state_obj.event_list.isempty(): #or to_push in state_obj.pre_emptive_mods:
        # Regular key, but not sure what to do, so put it in the list
        key_obj = UnresolvedKey(scancode = to_push,
                time_pressed = key_event.event.timestamp(),
                keystate = 1, resolution_type = ResolutionType.REGULAR)
        node = state_obj.event_list.append(key_obj)
        state_obj.back_links[key_event.scancode] = node
        if pre_emptive and to_push in state_obj.pre_emptive_mods:
            key_obj.pre_emptive_pressed = True
            key_obj.mod_key = to_push
            send_key(state_obj, to_push, key_event.keystate)
            if DEBUG:
                print(f'Push {to_push} because pre_emptive')

        resolve(state_obj, key_event, pre_emptive, to_node = node, from_down = True)

        if DEBUG:
            print("Regular key, append to list: {}".format(codes_to_keys(key_event.scancode)))
            print("Event list: {}".format(state_obj.event_list))
    else:
        # Regular key, no list? Send!
        if key_event.keystate == 1:
            send_key(state_obj, key_event.scancode, key_event.keystate)
            # state_obj.resolution_dict[key_event.scancode] = ResolutionType.REGULAR
        if DEBUG:
            print("Regular key, push {}".format(key_event.scancode))

    state_obj.last_pressed[key_event.scancode] = time.time()

def handle_special_key_down(state_obj, key_event, pre_emptive):
    # Special key, on a push we never know what to do, so put it in the list

    global DEBUG

    # if DEBUG:
    #     print("Registered key pressed")

    cur_key = key_event.scancode
    to_push = state_obj.registered_keys[cur_key].mod_key
    key_obj = UnresolvedKey(scancode = cur_key, time_pressed = key_event.event.timestamp(),
            keystate = 1, resolution_type = ResolutionType.UNRESOLVED)

    if state_obj.handle_repeat \
            and cur_key in state_obj.repeat_keys:
        last_pressed = state_obj.last_pressed.get(cur_key)
        if last_pressed is not None \
                and time.time() - last_pressed < state_obj.repeat_timeout:
            if DEBUG:
                print(f'Double key press, resolve {cur_key} to regular key')
            key_obj.resolution_type = ResolutionType.DUAL_REGULAR 
            node = state_obj.event_list.append(key_obj)
            state_obj.back_links[cur_key] = node
            # SOMEDAY: from_down probably should be false, but honestly, maybe don't really want to let
            # this fire while a list is in place anyway and actually do away with putting it on there
            # in the first place.
            #
            # Also, the early return here is ugly.
            resolve(state_obj, key_event, pre_emptive, node, from_down = False)
            state_obj.last_pressed[cur_key] = time.time()
            return

    if pre_emptive:
        key_obj.pre_emptive_pressed = True
        key_obj.mod_key = to_push
        node = state_obj.event_list.append(key_obj)
        send_key(state_obj, to_push, key_event.keystate)
        if DEBUG:
            print("Because pre_emptive, push {}, append {}".format(codes_to_keys(to_push), codes_to_keys(key_event.scancode)))
    else:
        # TODO: mod_down should be False here?
        node = state_obj.event_list.append(key_obj)
        if DEBUG:
            print("Not pre_emptive, append {}".format(codes_to_keys(key_event.scancode)))

    state_obj.back_links[cur_key] = node
    resolve(state_obj, key_event, pre_emptive, node, from_down = True)

    # Save when pressed
    state_obj.last_pressed[cur_key] = time.time()

def handle_regular_key_up(state_obj, key_event, pre_emptive):
    # Regular key goes up

    global DEBUG

    # if DEBUG:
    #     print("Regular key")

    cur_key = key_event.scancode

    if state_obj.event_list.isempty():
        # Nothing backed up, just send.
        send_key(state_obj, cur_key, key_event.keystate)
        if DEBUG:
            print("No list, lift {}".format(codes_to_keys(cur_key)))
    else:
        key_obj = UnresolvedKey(scancode = cur_key,
                time_pressed = key_event.event.timestamp(),
                keystate = 0, resolution_type = ResolutionType.REGULAR)
        node = state_obj.event_list.append(key_obj)
        if DEBUG:
            print("Key in list, resolve {}".format(codes_to_keys(cur_key)))

        if pre_emptive and cur_key in state_obj.pre_emptive_mods:
            send_key(state_obj, cur_key, 0)
            key_obj.pre_emptive_pressed = True
            if DEBUG:
                print(f'Lifted {cur_key} because pre_emptive')

        # Resolve
        back_link = state_obj.back_links.get(key_event.scancode)
        if back_link is not None:
            resolve(state_obj, key_event, pre_emptive, back_link, from_down = False)
            del state_obj.back_links[key_event.scancode] 

def handle_special_key_up(state_obj, key_event, pre_emptive):
    global DEBUG
    # Special key goes up
    # if DEBUG:
    #     print("Special key goes up")

    cur_key = key_event.scancode
    back_link = state_obj.back_links[cur_key] # let exception fire if not set, it really should be!
    # If not resolved by now, it is a regular key
    if back_link.content.resolution_type == ResolutionType.UNRESOLVED:
        back_link.content.resolution_type = ResolutionType.DUAL_REGULAR
        if pre_emptive and back_link.content.pre_emptive_pressed:
            # Lift pre_emptive key
            send_key(state_obj, state_obj.registered_keys[cur_key].mod_key, 0)
            back_link.content.pre_emptive_pressed = False
        if DEBUG:
            print(f'Key {cur_key} was not resolved, so resolve to regular key.')
    key_obj = UnresolvedKey(scancode = cur_key, time_pressed = key_event.event.timestamp(),
            keystate = 0, resolution_type = back_link.content.resolution_type)
    # if state_obj.event_list.isempty():
    #     single_key = state_obj.registered_keys[cur_key].single_key
    #     send_key(state_obj, state_obj.registered_keys[cur_key].single_key, key_event.keystate)
    #     if DEBUG:
    #         print(f'List empty, so send single key {single_key}')
    if pre_emptive and back_link.content.pre_emptive_pressed:
        send_key(state_obj, back_link.content.mod_key, 0)
        key_obj.pre_emptive_pressed = True
        if DEBUG:
            print(f'Lifted {cur_key} because pre_emptive')
    node = state_obj.event_list.append(key_obj)
    resolve(state_obj, key_event, pre_emptive, back_link, from_down = False)
    del state_obj.back_links[cur_key]

def invert_keystate(keystate):
    if keystate == 0:
        return 1
    elif keystate == 1:
        return 0

def lift_following_modifiers(state_obj, pre_emptive, node):

    global DEBUG
    if DEBUG:
        print("Lifting modifiers: ", end="")

    while node is not None:
        if node.content.pre_emptive_pressed:
            if node.content.scancode in state_obj.registered_keys:
                to_lift = state_obj.registered_keys[node.content.scancode].mod_key
                send_key(state_obj, to_lift, invert_keystate(node.content.keystate))
                if DEBUG:
                    print("{}, ".format(codes_to_keys(to_lift)), end = "")
            elif node.content.scancode in state_obj.pre_emptive_mods:
                send_key(state_obj, node.content.scancode, invert_keystate(node.content.keystate))
                if DEBUG:
                    print("{}, ".format(node.content.scancode), end = "")
        node = node.next

    # if DEBUG:
    #     print()

def resolve(state_obj, key_event, pre_emptive, to_node, from_down = False):
    global DEBUG

    # Resolve 
    node = state_obj.event_list.head

    resolve_keys = to_node is None or not to_node.removed
    push_keys = True
    like_pre_emptive = True

    if DEBUG:
        print('Traversing list to resolve')

    while node is not None:
        if DEBUG:
            print(f'Current node: {node}')
        found_node = node is to_node
        if DEBUG and found_node:
            print('Found matching node')

        if found_node:
            resolve_keys = False

        if resolve_keys:
            # Resolve dual keys to modifiers, according to whether they are
            # down_trigger ones or not
            if node.content.resolution_type == ResolutionType.UNRESOLVED and \
                    (not from_down or \
                    state_obj.registered_keys[node.content.scancode].down_trigger):
                node.content.resolution_type = ResolutionType.DUAL_MOD
                if DEBUG:
                    print(f'Resolved {node} to DUAL_MOD')

        if push_keys:
            # Then, push keys
            if node.content.resolution_type != ResolutionType.UNRESOLVED:
                if node.content.resolution_type == ResolutionType.REGULAR:
                    if pre_emptive and like_pre_emptive and \
                            not node.content.scancode in state_obj.pre_emptive_mods:
                        like_pre_emptive = False
                        lift_following_modifiers(state_obj, pre_emptive, node.next)
                    if not pre_emptive or not like_pre_emptive or \
                            not node.content.scancode in state_obj.pre_emptive_mods:
                        send_key(state_obj, node.content.scancode, node.content.keystate)
                elif node.content.resolution_type == ResolutionType.DUAL_REGULAR:
                    # This should be the only case where the keys we output
                    # are different from the pre emptively pressed ones,
                    # so first lift all following modifiers,
                    # then put the remaining ones back.
                    if pre_emptive and like_pre_emptive:
                        like_pre_emptive = False
                        lift_following_modifiers(state_obj, pre_emptive, node.next)
                    send_key(state_obj,
                            state_obj.registered_keys[node.content.scancode].single_key,
                            node.content.keystate)
                elif node.content.resolution_type == ResolutionType.DUAL_MOD:
                    if not pre_emptive or not like_pre_emptive:
                        send_key(state_obj,
                            state_obj.registered_keys[node.content.scancode].mod_key,
                            node.content.keystate)
                state_obj.event_list.remove(node)
            elif node.content.resolution_type == ResolutionType.UNRESOLVED:
                # If we encounter one unresolved keys, stop pushing keys
                push_keys = False

        if pre_emptive and not push_keys and not like_pre_emptive:
            # if we are not pushing resolved keys any more,
            # put pre_emptive keys back
            if (node.content.resolution_type == ResolutionType.UNRESOLVED or \
                    node.content.resolution_type == ResolutionType.DUAL_MOD) \
                    and node.content.pre_emptive_pressed:
                if DEBUG:
                    print('Pre_empt key ', end = '')
                send_key(state_obj, state_obj.registered_keys[node.content.scancode].mod_key, node.content.keystate)
            elif node.content.resolution_type == ResolutionType.REGULAR and \
                    node.content.scancode in state_obj.pre_emptive_mods and \
                    node.content.pre_emptive_pressed:
                if DEBUG:
                    print('Pre_empt key ', end = '')
                send_key(state_obj, node.content.scancode, node.content.keystate)

        node = node.next


# ## Old

# def resolve_previous_to_modifiers(state_obj, key_event, pre_emptive):
#     """
#     Resolve all keys in state_obj.event_list to modifiers, up to
#     the key in the current key_event
#     """

#     node = state_obj.event_list.head
#     # Stop at the node in question
#     while node is not None and node.key != key_event.scancode:
#         if state_obj.resolution_dict[node.key] == ResolutionType.UNRESOLVED:
#             # Special key -> modifier, only push if not pre_emptive
#             state_obj.resolution_dict[node.key] = ResolutionType.DUAL_MOD
#             if not pre_emptive:
#                 to_push = state_obj.registered_keys[node.key].mod_key
#                 send_key(state_obj, to_push, node.content.keystate)
#                 if DEBUG:
#                     print("Not pre-emptive, push {}".format(to_push))
#         elif state_obj.resolution_dict[node.key] == ResolutionType.REGULAR:
#             # Regular key -> only push if not down already
#             to_push = node.key
#             if not pre_emptive or to_push not in state_obj.pre_emptive_mods:
#                 send_key(state_obj, to_push, node.content.keystate)
#             if DEBUG:
#                 print("Not a registered key, push {}".format(to_push))
#         else:
#             raise Exception(("Invalid resolution type {}"
#                 "for element of event_list").format(state_obj.resolution_dict[node.key]))
#         state_obj.event_list.remove(node.key)
#         node = node.next

#     return node


# # TODO: Conflict/state handling for these operations
# def lift_modifiers(state_obj, key_event, start_node):

#     global DEBUG
#     if DEBUG:
#         print("Lifting modifiers: ", end="")

#     node = start_node
#     while node is not None:
#         if node.key in state_obj.registered_keys:
#             to_lift = state_obj.registered_keys[node.key].mod_key
#             send_key(state_obj, to_lift, 0)
#             if DEBUG:
#                 print("{}, ".format(codes_to_keys(to_lift)), end = "")
#         if node.key in state_obj.pre_emptive_mods:
#             send_key(state_obj, node.key, 0)
#             if DEBUG:
#                 print("{}, ".format(node.key), end = "")
#         node = node.next

#     if DEBUG:
#         print()

# def push_modifiers(state_obj, key_event, start_node):

#     global DEBUG

#     if DEBUG:
#         print("Pushing modifiers: ", end = "")

#     node = start_node
#     while node is not None:
#        if node.key in state_obj.registered_keys:
#            to_push = state_obj.registered_keys[node.key].mod_key
#            send_key(state_obj, to_push, 1)
#            if DEBUG:
#                print("{}, ".format(codes_to_keys(to_push)))
#        if node.key in state_obj.pre_emptive_mods:
#            send_key(state_obj, node.key, 1)
#            if DEBUG:
#                print("{}, ".format(codes_to_keys(node.key)))
#        node = node.next

#     if DEBUG:
#        print()

def handle_ungrabbed_key(state_obj, key_event, pre_emptive):
    # If we get a mouse button, immediately resolve everything

    global DEBUG

    if DEBUG:
        print("Event from ungrabbed device, resolving event_list.")
    scancode = key_event.scancode
    # resolve_previous_to_modifiers(state_obj, key_event, pre_emptive) 
    resolve(state_obj, key_event, pre_emptive, to_node = None)
    # send_key(state_obj, scancode, key_event.keystate)
    if DEBUG:
        print("Pushing key: {}".format(scancode))

# def handle_regular_key_down(state_obj, key_event, pre_emptive):
#     # Regular key goes up: either there is no list, then send;
#     # otherwise, put it in the queue

#     global DEBUG

#     to_push = key_event.scancode
#     if not state_obj.event_list.isempty() or to_push in state_obj.pre_emptive_mods:
#         # Regular key, but not sure what to do, so put it in the list
#         key_obj = UnresolvedKey(time_pressed = key_event.event.timestamp(),
#                 keystate = 1)
#         state_obj.event_list.append(to_push, key_obj)
#         state_obj.resolution_dict[key_event.scancode] = ResolutionType.REGULAR
#         if pre_emptive and to_push in state_obj.pre_emptive_mods:
#             key_obj.pre_emptive = True
#             key_obj.mod_key = to_push
#             send_key(state_obj, to_push, key_event.keystate)

#         if DEBUG:
#             print("Regular key, append to list: {}".format(codes_to_keys(key_event.scancode)))
#             print("Event list: {}".format(state_obj.event_list))
#     else:
#         # Regular key, no list? Send!
#         if key_event.keystate == 1:
#             send_key(state_obj, key_event.scancode, key_event.keystate)
#             state_obj.resolution_dict[key_event.scancode] = ResolutionType.REGULAR
#         if DEBUG:
#             print("Regular key, push {}".format(key_event.scancode))

# def handle_special_key_down(state_obj, key_event, pre_emptive):
#     # Special key, on a push we never know what to do, so put it in the list

#     global DEBUG

#     # if DEBUG:
#     #     print("Registered key pressed")

#     to_push = state_obj.registered_keys[key_event.scancode].mod_key
#     key_obj = UnresolvedKey(time_pressed = key_event.event.timestamp(),
#             keystate = 1)
#     if pre_emptive:
#         key_obj.pre_emptive = True
#         key_obj.mod_key = to_push
#         state_obj.event_list.append(key_event.scancode, key_obj)
#         state_obj.resolution_dict[key_event.scancode] = ResolutionType.UNRESOLVED
#         send_key(state_obj, to_push, key_event.keystate)
#         if DEBUG:
#             print("Because pre_emptive, push {}, append {}".format(codes_to_keys(to_push), codes_to_keys(key_event.scancode)))
#     else:
#         # TODO: mod_down should be False here?
#         state_obj.event_list.append(key_event.scancode, key_obj)
#         state_obj.resolution_dict[key_event.scancode] = ResolutionType.UNRESOLVED
#         if DEBUG:
#             print("Not pre_emptive, append {}".format(codes_to_keys(key_event.scancode)))

def handle_key_repeat(state_obj, key_event, pre_emptive):
    # Any key sends repeat

    global DEBUG
    
    cur_key = key_event.scancode
    # if state_obj.event_list.isempty():
    #     send_key(state_obj, cur_key, key_event.keystate)
    #     if DEBUG:
    #         print("No list, repeat {}".format(codes_to_keys(cur_key)))
    # elif time.time() - state_obj.event_list.tail.content.time_pressed > state_obj.repeat_timeout \
    #         and (state_obj.resolution_dict[cur_key] == ResolutionType.UNRESOLVED
    #                 or state_obj.resolution_dict[cur_key] == ResolutionType.REGULAR):
    #     # This looks like a real auto-repeat, so want to resolve.
    #     if DEBUG:
    #         print("Auto repeat with list, resolve {}".format(codes_to_keys(cur_key)))
    #     resolve(state_obj, key_event, pre_emptive)

    back_link = state_obj.back_links.get(cur_key)
    if back_link is not None \
            and cur_key in state_obj.idle_keys \
            and (time.time() - back_link.content.time_pressed > state_obj.idle_timeout) \
            and back_link.content.resolution_type == ResolutionType.UNRESOLVED:
        back_link.content.resolution_type = ResolutionType.DUAL_MOD
        # SOMEDAY: not clear if we want from_down here or not
        if DEBUG:
            print(f'Auto repeat triggered idle resolution on {cur_key}')
        resolve(state_obj, key_event, pre_emptive, to_node = back_link, from_down = False)

# def handle_regular_key_up(state_obj, key_event, pre_emptive):
#     # Regular key goes up

#     global DEBUG

#     # if DEBUG:
#     #     print("Regular key")

#     cur_key = key_event.scancode

#     if state_obj.event_list.isempty():
#         # Nothing backed up, just send.
#         send_key(state_obj, cur_key, key_event.keystate)
#         if DEBUG:
#             print("No list, lift {}".format(codes_to_keys(cur_key)))
#     elif key_event.scancode not in state_obj.event_list.key_dict:
#         # Nothing to resolve, can just let the key go up.
#         send_key(state_obj, key_event.scancode, key_event.keystate)
#         if DEBUG:
#             print("Key not in list, lift {}".format(codes_to_keys(cur_key)))
#     else:
#         # Regular key goes up and was queued, so all previous keys are modifiers
#         if DEBUG:
#             print("Key in list, resolve {}".format(codes_to_keys(cur_key)))

#         # Resolve
#         resolve(state_obj, key_event, pre_emptive)
#     del state_obj.resolution_dict[cur_key] 

# def handle_special_key_up(state_obj, key_event, pre_emptive):
#     global DEBUG
#     # Special key goes up
#     # if DEBUG:
#     #     print("Special key goes up")

#     cur_key = key_event.scancode
#     if state_obj.resolution_dict[cur_key] == ResolutionType.UNRESOLVED:
#         resolve(state_obj, key_event, pre_emptive)
#         if DEBUG:
#             print("Unresolved key up, resolve {}".format(codes_to_keys(cur_key)))
#     elif state_obj.resolution_dict[cur_key] == ResolutionType.DUAL_MOD:
#         to_lift = state_obj.registered_keys[cur_key].mod_key
#         send_key(state_obj, to_lift, 0)
#         if DEBUG:
#             print("Key already resolved to modifier, lift {}".format(codes_to_keys(to_lift)))
#     elif state_obj.resolution_dict[cur_key] == ResolutionType.DUAL_REGULAR:
#         # This will probably not happen
#         to_lift = state_obj.registered_keys[cur_key].single_key
#         send_key(state_obj, to_lift, 0)
#         if DEBUG:
#             print("Key already resolved to regular, lift {}.".format(codes_to_keys(to_lift)))
#             print("THIS SHOULD BE UNLIKELY!")
#             # This can happen if the key got resolved in the forward pass of resolve
#     del state_obj.resolution_dict[cur_key]

# def handle_event(active_keys, event, state_obj, grabbed = True, pre_emptive = False):
#     """
#     Handle the incoming key event. Main callback function.
#     """

#     # Only act on key presses
#     if event.type != evdev.ecodes.EV_KEY:
#         return True

#     if TIMING:
#         cur_time = time.time()

#     key_event = evdev.util.categorize(event)
#     # if key_event.scancode == 48:
#     #     raise KeyboardInterrupt
#     if DEBUG:
#         print("Received = {}, keystate = {}, key = {}, grabbed = {}.".format(key_event.scancode, key_event.keystate, evdev.ecodes.KEY.get(key_event.scancode, None), grabbed))

#     if key_event.scancode in state_obj.kill_switches:
#         state_obj.error_queue.put(ShutdownException())
#         return False

#     if not grabbed:
#         handle_ungrabbed_key(state_obj, key_event, pre_emptive)
#     else:
#         if key_event.keystate == 1:
#             # Key down
#             # if DEBUG:
#             #     print("Down.")
#             if key_event.scancode not in state_obj.registered_keys:
#                 handle_regular_key_down(state_obj, key_event, pre_emptive)
#             else:
#                 handle_special_key_down(state_obj, key_event, pre_emptive)
#         # Key up
#         elif key_event.keystate == 0:
#             # if DEBUG:
#             #     print("Key up")
#             if key_event.scancode not in state_obj.registered_keys:
#                 handle_regular_key_up(state_obj, key_event, pre_emptive)
#             else:
#                 handle_special_key_up(state_obj, key_event, pre_emptive)
#         elif state_obj.handle_repeat and key_event.keystate == 2:
#             # Key repeat
#             if DEBUG:
#                 print("Key repeat")
#             handle_key_repeat(state_obj, key_event, pre_emptive)
#             # if key_event.scancode not in state_obj.registered_keys:
#             #     handle_regular_key_repeat(state_obj, key_event, pre_emptive)
#             # else:
#             #     handle_special_key_repeat(state_obj, key_event, pre_emptive)
#         else:
#             if DEBUG:
#                 print("Not handling, unknown keystate")

#     if DEBUG:
#         print("Done.")
#         print("event_list: {}".format(state_obj.event_list))
#         print("resolution_dict: {}".format(state_obj.resolution_dict))
#         print("key_counter: {}".format({k: v for (k, v) in state_obj.key_counter.items() if v != 0}))
#     if TIMING:
#         state_obj.timing_stats["total"] += time.time() - cur_time
#         state_obj.timing_stats["calls"] += 1
#         if state_obj.timing_stats["calls"] % 10 == 0:
#             print("Average time/call = {}".format(state_obj.timing_stats["total"]/state_obj.timing_stats["calls"]))

#     return True


def cleanup(listen_devices, grab_devices, ui):
    """
    Cleanup at the end of execution, i.e. close
    devices.
    """

    print("-"*20)
    print("Cleaning up... ", end="")

    for (fn, dev) in listen_devices.items():
        if grab_devices[fn]:
            dev.ungrab()
            dev.close()
    ui.close()

    print("Done.")

def parse_arguments(raw_arguments):
    """
    Parse command line arguments with argparse.
    """

    parser = configargparse.ArgumentParser(description = "Dualkeys: Add dual roles for keys via evdev and uinput",
            config_file_parser_class = configargparse.YAMLConfigFileParser
            )
    parser.add_argument('-c', '--config', is_config_file=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-k', '--key', type=literal_eval, action='append',
            help = ("Scancodes for dual role key. Expects three arguments, corresponding to the"
            "actual key on the keyboard, the single press key, and the modifier key"),
            metavar = ('actual_key', 'single_key', 'mod_key'))
    # group.add_argument('-k', '--key', type=int, nargs=3, action='append',
    #         help = ("Scancodes for dual role key. Expects three arguments, corresponding to the"
    #         "actual key on the keyboard, the single press key, and the modifier key"),
    #         metavar = ('actual_key', 'single_key', 'mod_key'))
    group.add_argument('-p', '--print', action='store_true',
            help = "Disable dual-role keys, just print back scancodes")
    group.add_argument('-l', '--list', action='store_true',
            help = 'List all input devices recognized by python-evdev')
    parser.add_argument('-d', '--debug', action='store_true',
            help = "Print debug information")
    parser.add_argument('-t', '--timing', action='store_true',
            help = "Print timing results")
    parser.add_argument('-pem', '--pre-emptive-mods', nargs='+', type=int,
            default = [],
            help = ("Scancodes of modifier keys to be taken into account"
                "in pre-emptive mode"))
    parser.add_argument('-ks', '--kill-switch', nargs='+', type=int, default = [],
            help = "Scancodes of keys to immediately kill Dualkeys")
    parser.add_argument('-ak', '--angry-keys', nargs='+', type=int, default = [],
            help = "Scancodes of keys that will save the last few input and output strokes, see --angry-key-history")
    parser.add_argument('-akh', '--angry-key-history', type=int, default = 1000,
            help = "Length of angry key history")
    parser.add_argument('-akp', '--angry-key-prefix', nargs = '?', type=str, default = "./log",
            help = "Prefix for key history")
    parser.add_argument('-i', '--ignore', nargs = '+', type=int, default = [],
            help = "Scancodes to ignore")
    parser.add_argument('-rt', '--repeat-timeout', type = int,
            help = "Time frame during which a double press of a modifier key is interpreted as initiating key repeat.")
    parser.add_argument('-rk', '--repeat-keys', nargs = '+', type = int,
            help = "Keys to resolve to single keys if pressed again less than repeat-timeout after the last release.")
    parser.add_argument('-it', '--idle-timeout', type = int,
            help = "Timeout to resolve repeat-keys as modifiers")
    parser.add_argument('-ik', '--idle-keys', nargs = '+', type = int,
            help = "Keys to resolve to modifiers after a repeat-timeout milliseconds.")
    # parser.add_argument('-pex', '--pre-emptive-exclude', nargs='+', type=int,
    #         default = [], action='append',
    #         help = ("Scancodes of modifier keys to be taken into account"
    #             "in pre-emptive mode"))
    # args = parser.parse_args('--key 8 8 42 -k 9 9 56'.split())
    # args = parser.parse_args('-p'.split())
    # args = parser.parse_args('-h'.split())
    # args = parser.parse_args('-l'.split())
    args = parser.parse_args(args = raw_arguments)
    if args.debug:
        print("Arguments passed: {}".format(args))
    return args



def print_registered_keys(registered_keys):
    print("Registered dual-role keys:")
    for key in registered_keys.values():
        primary_key = key.primary_key
        primary_key_code = evdev.ecodes.KEY[primary_key]
        single_key = key.single_key
        single_key_code = evdev.ecodes.KEY[single_key]
        mod_key = key.mod_key
        mod_key_code = evdev.ecodes.KEY[mod_key]
        print("actual key = [{} | {}], ".format(primary_key, primary_key_code), end = "")
        print("single key = [{} | {}], ".format(single_key, single_key_code), end = "")
        print("modifier key = [{} | {}]".format(mod_key, mod_key_code))

def start_loop(loop):
    """
    Start given loop. Thread worker.
    """

    asyncio.set_event_loop(loop)
    loop.run_forever()

def wait_for_error(event):
    """
    Wait function, to be run asynchronously within the
    event-producer loop in order to signal it to stop.
    """

    # print('Waiting for error')
    event.wait()


# Main program
class Main():
    def __init__(raw_arguments = None,
            shutdown_flag = None,
            notify_condition = None,
            listen_device = None,
            test_comm = None):

        self.raw_arguments = raw_arguments
        self.shutdown_flag = threading.Event() if shutdown_flag is None else shutdown_flag
        self.notify_condition = notify_condition
        self.listen_device = listen_device
        self.test_comm = test_comm


    def list_devices(self):
        """
        Printing all evdev devices
        """

        print('Listing devices:')
        for device in all_devices:
            print("filename = {}, name = {}, physical address = {}".format(device.path, device.name, device.phys))
        return

    # TODO: Figure this out
    def raise_termination_exception(loop):
        """
        Callback to shut down the event-producer loop.
        """
        
        asyncio.set_event_loop(loop)
        # print("Printing tasks")
        tasks = asyncio.gather(*asyncio.Task.all_tasks())
        def collect_and_stop(t):
            t.exception()
            # print("My exception: ", t.exception())
            loop.stop()
        # tasks.add_done_callback(lambda t: loop.stop())
        tasks.add_done_callback(collect_and_stop)
        tasks.cancel()
        # loop.run_forever()
        # print("Exception: ", tasks.exception())
            
        # loop.stop()
        # raise Exception(s)

    def main(self)
        """
        Run the main program
        Arguments are mostly for testing purposes, so the routine can be called from the test applications.
        They override variables that are otherwise set by this application.
        """

        # global DEBUG, TIMING
        args = parse_arguments(self.raw_arguments)
        if args.debug:
            level = logging.DEBUG
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        self.all_devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
        if args.list:
            self.list_devices()

        # Main states
        # state_obj = DualkeysState(args) # Parse args into state object
        # state.selector = DefaultSelector()


        # registered_keys[8] = DualKey(8, 8, 42) # 7 <> L_SHIFT
        # registered_keys[9] = DualKey(9, 9, 56) # 8 <> L_ALT

        # Event add loop, to be started in a new thread
        # event_loop = asyncio.new_event_loop()
        # event_queue = queue.Queue() # Event queue

        # Exception handling, stop on this event
        # self.termination_event = threading.Event() 

        event_pusher = EventPusherThread(main_instance = self, name = "event_pusher")
        event_pusher.start()
        
        # Add termination function to event_loop
        # future = event_loop.run_in_executor(None, wait_for_error, termination_event)
        # future.add_done_callback(functools.partial(raise_termination_exception, s = "event-producer terminated", loop = event_loop))

        # if error_queue is None:
        #     error_queue = queue.Queue() 
        # state_obj.error_queue = error_queue
        # event_producer = threading.Thread(target = start_loop, args = (event_loop,), name = "event-producer")

        # Introduce monitor to listen for device additions and removals
        # monitor.start()

        # selector.register(monitor, EVENT_READ)

        ui = evdev.UInput()
        state_obj.ui = ui

        def event_worker(event_queue):
            """
            Worker function for event-consumer to handle events.
            """

            global DEBUG

            try:
                state_obj.timing_stats = {}
                if TIMING:
                    state_obj.timing_stats["total"] = 0
                    state_obj.timing_stats["calls"] = 0
                while True:
                    if DEBUG:
                        print("-"*20)
                    elem = event_queue.get()
                    if type(elem) is ShutdownException:
                        print("event_consumer was asked to shut down")
                        break 
                    else:
                        (device, event) = elem
                    # print("Pre-lock")
                    device_lock.acquire()
                    if DEBUG:
                        # if event.type == evdev.ecodes.EV_KEY:
                        print()
                        print("Active keys: {}".format(codes_to_keys(ui.device.active_keys())))
                        print()
                    try:
                        if args.print:
                            ret = print_event(state_obj, event, grab_devices[device.path])
                        else:
                            ret = handle_event(device.active_keys(), event, state_obj, grab_devices[device.path], pre_emptive = PUSH_PRE_EMPTIVE)
                    except IOError as e:
                        # Check if the device got removed, if so, get rid of it
                        if e.errno != errno.ENODEV: raise
                        if DEBUG:
                            # if True:
                            print("Device {0.fn} removed.".format(device))
                            remove_device(device, listen_devices, grab_devices, device_futures, event_loop)
                    finally:
                        event_queue.task_done()
                        device_lock.release()
                        # print("Past lock")
            except Exception as e:
                error_queue.put(e)
                raise e

        observer_thread_wrapper = ObserverThreadWrapper(main_instance = self)
        observer_thread_wrapper.start()

        event_producer.start()
        if DEBUG:
            print("event-producer started")

        event_consumer = threading.Thread(target = event_worker, args = (event_queue,), name = "event-consumer")
        event_consumer.start()
        if DEBUG:
            print("event-consumer started")

        # Try-finally clause to catch in particular Ctrl-C and do cleanup
        try:
            # Notify that we are ready
            if notify_condition is not None:
                test_comm.ui = ui
                with notify_condition:
                    # print('Notifying!')
                    notify_condition.notifyAll()
            # Wait for exceptions
            e = error_queue.get()
            # print("Raising exception")
            # raise e
            print("Exception raised")
            error_queue.task_done()
                
        except Exception as e:
            print("Exception in main loop")
            raise e
        finally:
            print("Shutting down...")
            termination_event.set()
            event_queue.put(ShutdownException())
            event_producer.join()
            if DEBUG:
                print("event-producer joined")
            event_consumer.join()
            if DEBUG:
                print("event-consumer joined")
            observer.stop()
            if DEBUG:
                print("observer stopped")
            cleanup(listen_devices, grab_devices, ui)

if __name__ == "__main__":
    main_instance = Main()
    main_instance.main()
