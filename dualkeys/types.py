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
import logging
import concurrent
from collections import deque

class TerminationException(Exception):
    pass

class TerminationExceptionError(Exception):
    pass

class HistoryException(Exception):
    pass

class KeyEvent():
    def __init__(self, event, grab = True):
        self.code = event.code.value
        self.name = event.code.name
        self.keystate = event.value
        self.grab = grab
        self.timestamp_sec = event.sec
        self.timestamp_usec = event.usec

    def timestamp(self):
        return float(self.timestamp_sec) + float(self.timestamp_usec)/10**6

    def __str__(self):
        return f"({self.code}, {self.name}, {self.keystate})"

class DeviceWrapper():
    def __init__(self, grab = False, input_device = None, future = None):
        self.grab = grab
        self.input_device = input_device
        self.future = future
        # self.last_pressed_down = {}
        self.last_pressed_repeat = {}
        self.last_pressed_up = {}
        self.pressed_dict = {}

    # def register_down(self, scancode):
    #     self.last_pressed_down[scancode] = time.time()

    # def register_repeat(self, scancode):
    #     self.last_pressed_repeat[scancode] = time.time()

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

class SwapKey:
    def __init__(self, from_key, to_key):
        self.from_key = from_key
        self.to_key = to_key

    def __repr__(self):
        return (f"SwapKey({self.from_key}, {self.to_key})")

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
