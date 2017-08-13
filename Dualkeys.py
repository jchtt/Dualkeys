#! /usr/bin/env python
# vim: sw=4 ts=4 et

# Dualkeys: Dual-role keys with evdev and uinput
# Copyright (C) 2017 jchtt

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

import argparse
import evdev
import pyudev as udev
import errno
import re
from enum import Enum
from linked_list import *
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE

# from select import select
# from collections import deque
# import threading
# import sys
# import time

DEBUG = False

class HandleType(Enum):
    """
    Simple enum type to handle different kinds of input devices,
    according to whether we do not want to handle them (IGNORE),
    want to grab the input (GRAB) (everything except mice), or not (NOGRAB)
    (mice).
    """
    IGNORE = 0
    GRAB = 1
    NOGRAB = 2

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

def add_device(device, listen_devices, grab_devices, selector):
    """
    Add device to listen_devices, grab_devices and selector if
    it matches the output of get_handle_type.
    """

    if DEBUG:
        print("Adding device {}".format(device))
    handle_type = get_handle_type(device)
    if handle_type == HandleType.GRAB:
        listen_devices[device.fn] = device
        grab_devices[device.fn] = True
        selector.register(device, EVENT_READ)
        device.grab()
    elif handle_type == HandleType.NOGRAB:
        listen_devices[device.fn] = device
        grab_devices[device.fn] = False
        selector.register(device, EVENT_READ)

def remove_device(device, listen_devices, grab_devices, selector):
    """
    Remove device, complementary operation to add_device.

    Works on Evdev and Udev devices.
    """

    # Handle udev and evdev devices
    if type(device) is evdev.device.InputDevice:
        fn = device.fn
    else:
        fn = device.device_node
    if fn in listen_devices:
        selector.unregister(listen_devices[fn])
        del listen_devices[fn]
        if fn in grab_devices:
            del grab_devices[fn]

class DualKey:
    """
    Object to store information about dual-role keys.

    primary_key: The key that appears on the keyboard
    single_key: The key that gets triggered if the key is pressed and released
        on its own
    mod_key: The key that gets triggered if the key is pressed together with
        another key
    """

    def __init__(self, primary_key, single_key, mod_key):
        self.primary_key = primary_key
        self.single_key = single_key
        self.mod_key = mod_key

class KeyResponse:
    """
    Object to store the response to a key.

    For now, only use it to indicate whether a key should go up again,
    which we do not want if the same modifier was pressed before.
    """

    def __init__(self, pre_emptive_up = True, mod_down = False, conflicting_key = None):
        self.pre_emptive_up = pre_emptive_up
        self.mod_down = mod_down
        self.conflicting_key = None

def send_key(ui, scancode, keystate):
    """
    Send a key event via uinput.
    """
    ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
    ui.syn()

def handle_event(ui, active_keys, event, registered_keys, event_list, conflict_list, grabbed = True, pre_emptive = False):
    """
    Handle the incoming key event. Main callback function.
    """

    # Only act on key presses
    if event.type == evdev.ecodes.EV_KEY:
        key_event = evdev.util.categorize(event)
        if DEBUG:
            print("Received key with scancode = {}, keystate = {}".format(key_event.scancode, key_event.keystate))
        # If we get a mouse button, immediately resolve everything
        if not grabbed:
            # node = event_list.head
            # while node is not None:
            for node in event_list:
                if node.key in registered_keys:
                    to_push = registered_keys[node.key].mod_key
                    if not pre_emptive:
                        send_key(ui, to_push, 1)
                        # ui.syn()
                else:
                    to_push = node.key
                    send_key(ui, to_push, 1)
                    # ui.syn()
                if DEBUG:
                    print("Pushing key: {}".format(to_push))
                event_list.remove(node.key)
                # node = node.next
            # Finally, send the key itself
            # if DEBUG:
            #     print("Pushing final key: {}".format(key_event.scancode))
            # send_key(ui, key_event.scancode, key_event.keystate)
            # ui.syn()
        # Otherwise, handle like this
        else:
            # Key down
            if key_event.keystate == 1:
                if key_event.scancode not in registered_keys:
                    # Regular key
                    if event_list.isempty():
                        # Regular key, no list? Send!
                        send_key(ui, key_event.scancode, key_event.keystate)
                    else:
                        # Regular key, but not sure what to do, so put it in the list
                        if DEBUG:
                            print("Regular key pressed, append, {}".format(key_event.scancode))
                        event_list.append(key_event.scancode, KeyResponse(False, False, None))
                else:
                    # Special key, on a push we never know what to do, so put it in the list
                    if pre_emptive:
                        to_push = registered_keys[key_event.scancode].mod_key
                        if to_push in ui.device.active_keys():
                            # Modifier key was already pushed, which means when the
                            # key triggers as regular, do not want to lift it up
                            event_list.append(key_event.scancode, KeyResponse(False, True, to_push))
                            conflict_list[to_push] = [key_event.scancode]
                        else:
                            event_list.append(key_event.scancode, KeyResponse(True, True, to_push))
                        send_key(ui, to_push, key_event.keystate)
                    else:
                        event_list.append(key_event.scancode, KeyResponse(False, True, to_push))
            # Key up
            elif key_event.keystate == 0:
                if key_event.scancode not in registered_keys:
                    # Regular key goes up
                    if event_list.isempty():
                        # Nothing backed up, just send.
                        send_key(ui, key_event.scancode, key_event.keystate)
                    elif key_event.scancode not in event_list.key_dict:
                        if not key_event.scancode in conflict_list:
                            send_key(ui, key_event.scancode, key_event.keystate)
                        else:
                            # We have a modifier conflict, in which case do not lift the key, but
                            # change the conflict note in the corresponding key.
                            for code in conflict_list[key_event.scancode]:
                                node = event_list.key_dict[code]
                                node.content.pre_emptive_up = True
                                if DEBUG:
                                    print("Setting pre_emptive_up to True")
                                node.content.mod_down = True
                    else:
                        # Regular key goes up, went down before, so all keys are modifiers
                        if DEBUG:
                            print("Regular key up.")
                        for node in event_list:
                            if node.key in registered_keys:
                                # Special key
                                to_push = registered_keys[node.key].mod_key
                                if not pre_emptive:
                                    send_key(ui, to_push, 1)
                            else:
                                # Regular key
                                to_push = node.key
                                send_key(ui, to_push, 1)
                            if DEBUG:
                                print("Pushing key {}".format(to_push))
                            event_list.remove(node.key)
                        # Finally let the key go up
                        if DEBUG:
                            print("Final key up {}".format(key_event.scancode))
                        send_key(ui, key_event.scancode, key_event.keystate)
                else:
                    # Special key goes up
                    dual_key = registered_keys[key_event.scancode]
                    if key_event.scancode not in event_list.key_dict:
                        # Key was resolved, and resolved (hopefully, check!) means modifier
                        send_key(ui, dual_key.mod_key, 0)
                    else:
                        # Key was unresolved, that means its regular function kicks in,
                        # resolving all previous dual keys to modifiers and all
                        # immediately following regular keys to be fired
                        node = event_list.head
                        found_key = False
                        while node.key != key_event.scancode:
                        # for node in event_list:
                            if node.key in registered_keys:
                                to_push = registered_keys[node.key].mod_key
                                if not pre_emptive:
                                    send_key(ui, to_push, 1)
                            else:
                                to_push = node.key
                                send_key(ui, to_push, 1)
                            event_list.remove(node.key)
                            node = node.next

                        # Stop at the node in question
                        response = node.content
                        # print("Content of pre_emptive_up: {}".format(response.pre_emptive_up))
                        if pre_emptive and response.pre_emptive_up:
                            # Only lift it if there is no conflicting key that was pressed before
                            if DEBUG:
                                print("Lifting pre_emptive key")
                            to_lift = registered_keys[node.key].mod_key
                            send_key(ui, to_lift, 0)
                        to_push_single = registered_keys[node.key].single_key
                        send_key(ui, to_push_single, 1)
                        event_list.remove(node.key)
                        node = node.next

                        while node is not None and node.key not in registered_keys:
                            send_key(ui, node.key, 1)
                            event_list.remove(node.key)
                            node = node.next

                        # Finally, send the up signal
                        send_key(ui, to_push_single, 0)

        if DEBUG:
            print(event_list)
    return True

def print_event(ui, event):
    """
    Alternative callback function that passes everything through,
    for debugging purposes only.
    """

    if event.type == evdev.ecodes.EV_KEY:
        key_event = evdev.util.categorize(event)
        print()
        print('keycode = {}, scancode = {}, keystate = {}'.format(key_event.keycode, key_event.scancode, key_event.keystate))
        send_key(ui, key_event.scancode, key_event.keystate)
    return True

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

def parse_arguments():
    """
    Parse command line arguments with argparse.
    """

    parser = argparse.ArgumentParser(description = "Dualkeys: Add dual roles for keys via evdev and uinput")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-k', '--key', type=int, nargs=3, action='append',
            help = ("Scancodes for dual role key. Expects three arguments, corresponding to the"
            "actual key on the keyboard, the single press key, and the modifier key"),
            metavar = ('actual_key', 'single_key', 'mod_key'))
    group.add_argument('-p', '--print', action='store_true',
            help = "Disable dual-role keys, just print back scancodes")
    group.add_argument('-l', '--list', action='store_true',
            help = 'List all input devices recognized by python-evdev')
    parser.add_argument('-d', '--debug', action='store_true',
            help = "Print debug information")
    # args = parser.parse_args('--key 8 8 42 -k 9 9 56'.split())
    # args = parser.parse_args('-p'.split())
    # args = parser.parse_args('-h'.split())
    # args = parser.parse_args('-l'.split())
    args = parser.parse_args()
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

# Main program
def main():
    global DEBUG
    args = parse_arguments()
    DEBUG = args.debug

    all_devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
    if args.list:
        print('Listing devices:')
        for device in all_devices:
            print("filename = {}, name = {}, physical address = {}".format(device.fn, device.name, device.phys))
        return

    # Main states
    listen_devices = {}
    grab_devices = {}
    selector = DefaultSelector()
    registered_keys = {}

    # Define registered keys
    if args.key is not None:
        for keys in args.key:
            registered_keys[keys[0]] = DualKey(*keys)
    print_registered_keys(registered_keys)

    # registered_keys[8] = DualKey(8, 8, 42) # 7 <> L_SHIFT
    # registered_keys[9] = DualKey(9, 9, 56) # 8 <> L_ALT

    # Main status indicator
    event_list = DLList()
    conflict_list = {}

    # Find all devices we want to reasonably listen to
    for device in all_devices:
        add_device(device, listen_devices, grab_devices, selector)

    # Introduce monitor to listen for device additions and removals
    context = udev.Context()
    monitor = udev.Monitor.from_netlink(context)
    monitor.filter_by('input')
    monitor.start()

    selector.register(monitor, EVENT_READ)

    ui = evdev.UInput()

    evdev_re = re.compile(r'^/dev/input/event\d+$')

    # Finally clause to catch in particular Ctrl-C and do cleanup
    try:
        # Main loop
        while True:
            if DEBUG:
                print("-"*20)
                print("Loop head")
                # print("Listening on devices: {}".format(listen_devices))
            for key, mask in selector.select():
                if monitor is key.fileobj:
                    # Udev is sending something
                    device = monitor.poll()
                    if DEBUG:
                    # if True:
                        print('Udev reported: {0.action} on {0.device_path}, device node: {0.device_node}'.format(device))
                    if device.action == 'remove':
                        # Device removed, let's see if we need to remove it from the lists
                        # Note that this probably won't happen, since the device will report an event and
                        # the IOError below will get triggered
                        remove_device(device, listen_devices, grab_devices, selector)
                    elif device.action == 'add' and device.device_node is not None and device.device_node != ui.device.fn and evdev_re.match(device.device_node) is not None:
                        # Device added, add it to everything
                        evdev_device = evdev.InputDevice(device.device_node)
                        add_device(evdev_device, listen_devices, grab_devices, selector)
                else:
                # Key got registered, handle it
                    device = key.fileobj
                    try:
                        for event in device.read():
                            # if DEBUG:
                            #     print('Handling event')
                            if DEBUG:
                                print("Active keys: {}".format(ui.device.active_keys()))
                            if args.print:
                                ret = print_event(ui, event)
                            else:
                                ret = handle_event(ui, device.active_keys(), event, registered_keys, event_list, conflict_list, grab_devices[device.fn], pre_emptive = True)
                            # ret = True
                            if not ret:
                                break
                    except IOError as e:
                        # Check if the device got removed, if so, get rid of it
                        if e.errno != errno.ENODEV: raise
                        if DEBUG:
                        # if True:
                            print("Device {0.fn} removed.".format(device))
                            remove_device(device, listen_devices, grab_devices, selector)
    # except (KeyboardInterrupt, SystemExit):
    except:
        raise
    finally:
        cleanup(listen_devices, grab_devices, ui)

main()
