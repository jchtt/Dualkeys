# vim: sw=4 ts=4 et
#! /usr/bin/env python

# import evdev


import evdev
import sys
import time
import pyudev as udev
import threading
import errno
from enum import Enum
# from collections import deque
from linked_list import *
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
from select import select

DEBUG = False

class HandleType(Enum):
    IGNORE = 0
    GRAB = 1
    NOGRAB = 2

all_devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
listen_devices = {}
selector = DefaultSelector()
grab_devices = {}
event_list = DLList()
registered_keys = {}

def get_handle_type(device):
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
    # if DEBUG:
    if True:
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

# Find all devices we want to reasonably listen to
for device in all_devices:
    print(device.fn, device.name, device.phys)
    add_device(device, listen_devices, grab_devices, selector)

print(listen_devices)
print(grab_devices)

mouse = evdev.InputDevice('/dev/input/event18')
# keybd = evdev.InputDevice('/dev/input/event18')
keybd = evdev.InputDevice('/dev/input/event5')
# keybd = evdev.InputDevice('/dev/input/event20')

async def print_events(device):
    device.grab()
    async for event in device.async_read_loop():
        # print(device.fn, evdev.categorize(event), sep=': ')
        if event.type == evdev.ecodes.EV_KEY:
            key_event = evdev.util.categorize(event)
            # print(key_event.scancode)
            # ui.write(evdev.ecodes.EV_KEY, key_event.scancode, key_event.key_down)
            # ui.syn()
            print(key_event.keycode, key_event.keystate)
            if key_event.keycode == 'KEY_Q':
                print('Quit!')
                device.ungrab()
                break

class Key:
    def __init__(self, prev = None, next = None):
        self.prev = prev
        self.next = next
        self.active = False

class DualKey(Key):
    def __init__(self, primary_key, single_key, mod_key):
        self.primary_key = primary_key
        self.single_key = single_key
        self.mod_key = mod_key

def send_key(scancode, keystate):
    ui.write(evdev.ecodes.EV_KEY, scancode, keystate)

def handle_event2(device, ui, event, registered_keys, event_list, grabbed = True, pre_emptive = False):
    if event.type == evdev.ecodes.EV_KEY:
        key_event = evdev.util.categorize(event)
        print("Received key {}".format(key_event.scancode))
        # If we get a mouse button, immediately resolve everything
        if not grabbed:
            node = event_list.head
            while node is not None:
                if node.key in registered_keys:
                    to_push = registered_keys[node.key].mod_key
                    if not pre_emptive:
                        send_key(to_push, 1)
                        ui.syn()
                else:
                    to_push = node.key
                    send_key(to_push, 1)
                    ui.syn()
                print(to_push)
                event_list.remove(node.key)
                node = node.next
            # Finally, send the key
            print(key_event.scancode)
            send_key(key_event.scancode, key_event.keystate)
            ui.syn()
        else:
            if key_event.keystate == 1:
                if key_event.scancode not in registered_keys:
                    # Regular key
                    if event_list.head is None:
                        # Regular key, no list? Send!
                        send_key(key_event.scancode, key_event.keystate)
                        ui.syn()
                    else:
                        # Regular key, but not sure what to do, so put it in the list
                        print("Regular, append, {}".format(key_event.scancode))
                        event_list.append(key_event.scancode, 1)
                else:
                    # Special key, on a press never know what to do, so put it in
                    if pre_emptive:
                        to_push = registered_keys[key_event.scancode].mod_key
                        send_key(to_push, key_event.keystate)
                        ui.syn()
                    event_list.append(key_event.scancode, 1)
                # event_list.append(key_event.scancode, 1)
            elif key_event.keystate == 0:
                if key_event.scancode not in registered_keys:
                    # Regular key goes up
                    if event_list.head is None or key_event.scancode not in event_list.key_dict:
                        # Nothing backed up or at least not the key, just send
                        send_key(key_event.scancode, key_event.keystate)
                        ui.syn()
                    else:
                        print("Regular key up.")
                        # Regular key goes up, went down before, so all keys are modifiers
                        node = event_list.head
                        while node is not None:
                            if node.key in registered_keys:
                                to_push = registered_keys[node.key].mod_key
                                if not pre_emptive:
                                    send_key(to_push, 1)
                                    ui.syn()
                            else:
                                to_push = node.key
                                send_key(to_push, 1)
                                ui.syn()
                            print(to_push)
                            event_list.remove(node.key)
                            node = node.next
                        # Finally let the key go up
                        print(key_event.scancode)
                        send_key(key_event.scancode, key_event.keystate)
                        ui.syn()
                else:
                    # Special key goes up
                    dual_key = registered_keys[key_event.scancode]
                    if key_event.scancode not in event_list.key_dict:
                        # Key was resolved, and resolved (hopefully, check!) means modifier
                        send_key(dual_key.mod_key, 0)
                    else:
                        # Key was unresolved, that means its regular function kicks in,
                        # resolving all previous dual keys to modifiers and all
                        # immediately following regular keys to be fired
                        node = event_list.head
                        found_key = False
                        while node.key != key_event.scancode:
                            if node.key in registered_keys:
                                to_push = registered_keys[node.key].mod_key
                                if not pre_emptive:
                                    send_key(to_push, 1)
                                    ui.syn()
                            else:
                                to_push = node.key
                                send_key(to_push, 1)
                                ui.syn()
                            event_list.remove(node.key)
                            node = node.next

                        # Stopped at the node in question
                        if pre_emptive:
                            to_lift = registered_keys[node.key].mod_key
                            send_key(to_lift, 0)
                            ui.syn()
                        to_push_single = registered_keys[node.key].single_key
                        send_key(to_push_single, 1)
                        event_list.remove(node.key)
                        node = node.next

                        while node is not None and node.key not in registered_keys:
                            send_key(node.key, 1)
                            event_list.remove(node.key)
                            node = node.next

                        # Finally, send the up signal
                        send_key(to_push_single, 0)
                        ui.syn()

        # send_key(key_event.scancode, key_event.keystate)
        # ui.syn()
        print(event_list)
            # Button pressed, put on list
            # key_pressed = keys.key_dict.get(key_event.scancode)
            # if key_pressed is None:
            #     if keys.last is None:
            #         send_key(key_event.scancode, key_event.keystate)
            #     else:
            #         if keys.last is keys.regular_key:
                        
            #         keys.last.next 
    return True

def print_event(device, ui, event):
    if event.type == evdev.ecodes.EV_KEY:
        key_event = evdev.util.categorize(event)
        # print(key_event.scancode, key_event.keystate)
        send_key(key_event.scancode, key_event.keystate)
        ui.syn()
    return True

# Introduce monitor to listen for device additions and removals
context = udev.Context()
monitor = udev.Monitor.from_netlink(context)
monitor.filter_by('input')
monitor.start()


# selector.register(mouse, EVENT_READ)
# selector.register(keybd, EVENT_READ)
selector.register(monitor, EVENT_READ)
# fds = {}
# fds[keybd.fd] = keybd
# fds[monitor.fileno()] = monitor

# keybd.grab()

# time.sleep(1)
# print('Sleep done')

# ui = evdev.UInput.from_device(keybd)
ui = evdev.UInput()

# loop = asyncio.get_event_loop()
# try:
#     loop.run_forever()
# finally:
#     loop.close()

# Define registered keys
registered_keys[8] = DualKey(8, 8, 42) # 7 <> L_SHIFT
# registered_keys[9] = DualKey(9, 9, 58) # 8 <> L_MOD
registered_keys[9] = DualKey(9, 9, 56) # 8 <> L_ALT

def cleanup():
    print("-"*20)
    print("Cleaning up... ", end="")

    for (fn, dev) in listen_devices.items():
        if grab_devices[fn]:
            dev.ungrab()
            dev.close()
    ui.close()

    print("Done.")

# Finally clause to catch in particular Ctrl-C and do cleanup
try:
    # Main loop
    while True:
        if DEBUG:
            print("-"*20)
            print("Loop head")
            print("Listening on devices: {}".format(listen_devices))
        for key, mask in selector.select():
            if monitor is key.fileobj:
                # Udev is sending something
                device = monitor.poll()
                # if DEBUG:
                if True:
                    print('Udev reported: {0.action} on {0.device_path}, device node: {0.device_node}'.format(device))
                if device.action == 'remove':
                    # Device removed, let's see if we need to remove it from the lists
                    # Note that this probably won't happen, since the device will report an event and
                    # the IOError below will get triggered
                    remove_device(device, listen_devices, grab_devices, selector)
                elif device.action == 'add' and device.device_node is not None and device.device_node != ui.device.fn:
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
                        # ret = handle_event2(keybd, ui, event, registered_keys, event_list, grab_devices[device.fn], pre_emptive = True)
                        ret = print_event(keybd, ui, event)
                        # ret = True
                        if not ret:
                            break
                except IOError as e:
                    # Check if the device got removed, if so, get rid of it
                    if e.errno != errno.ENODEV: raise
                    # if DEBUG:
                    if True:
                        print("Device {0.fn} removed.".format(device))
                        remove_device(device, listen_devices, grab_devices, selector)
# except (KeyboardInterrupt, SystemExit):
except:
    raise
finally:
    cleanup()
