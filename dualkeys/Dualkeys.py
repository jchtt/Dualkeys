#! /usr/bin/env python
# vim: sw=4 ts=4 et

# Dualkeys: Dual-role keys with evdev and uinput
# Copyright (C) 2017-2020 jchtt

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

import configargparse as argparse
# import argparse
from ast import literal_eval
import evdev
import queue
import logging

from .types import *
from .event_pusher import EventPusherThread
from .event_handler import EventHandlerThread
from .observer import ObserverThreadWrapper

# logger = logging.getLogger(__name__)
# logging.setLevel(logging.DEBUG)
# handler = logging.StreamHandler()
# handler.setLevel(logging.DEBUG)
# formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
# handler.setFormatter(formatter)
# logging.addHandler(handler)
# datefmt="%H:%M:%S",

def parse_multi_key(s):
    t = tuple(map(int, s.split(",")))
    if len(t) != 3:
        raise argparse.ArgumentTypeException(
            "Dualkeys must consist of three keycodes, like (59, 59, 27)")
    return t

# Main program
class Main():
    def __init__(self,
            raw_arguments = None,
            shutdown_flag = None,
            notify_condition = None,
            listen_device = None,
            test_comm = None,
            error_queue = None):

        self.raw_arguments = raw_arguments
        self.shutdown_flag = threading.Event() if shutdown_flag is None else shutdown_flag
        self.error_queue = queue.Queue() if error_queue is None else error_queue
        self.notify_condition = notify_condition
        self.listen_device = listen_device
        self.test_comm = test_comm

        self.event_queue = queue.Queue()

    def list_devices(self):
        """
        Printing all evdev devices
        """

        print('Listing devices:')
        for device in self.all_devices:
            print("filename = {}, name = {}, physical address = {}".format(device.path, device.name, device.phys))
        return

    # # TODO: Figure this out
    # def raise_termination_exception(loop):
    #     """
    #     Callback to shut down the event-producer loop.
    #     """
        
    #     asyncio.set_event_loop(loop)
    #     # print("Printing tasks")
    #     tasks = asyncio.gather(*asyncio.Task.all_tasks())
    #     def collect_and_stop(t):
    #         t.exception()
    #         # print("My exception: ", t.exception())
    #         loop.stop()
    #     # tasks.add_done_callback(lambda t: loop.stop())
    #     tasks.add_done_callback(collect_and_stop)
    #     tasks.cancel()
    #     # loop.run_forever()
    #     # print("Exception: ", tasks.exception())
            
    #     # loop.stop()
    #     # raise Exception(s)

    def cleanup(self):
        """
        Cleanup at the end of execution, i.e. close
        devices.
        """

        print("-"*20)
        print("Cleaning up... ", end="")

        self.event_pusher.cleanup()
        self.event_handler.cleanup()

        print("Done.")

    @staticmethod
    def parse_arguments(raw_arguments):
        """
        Parse command line arguments with argparse.
        """

        # parser = argparse.ArgumentParser(description = "Dualkeys: Add dual roles for keys via evdev and uinput")
        parser = argparse.ArgumentParser(description = "Dualkeys: Add dual roles for keys via evdev and uinput",
                config_file_parser_class = argparse.YAMLConfigFileParser
                )
        parser.add_argument('-c', '--config', is_config_file=True)
        group = parser.add_mutually_exclusive_group()
        # group.add_argument('-k', '--key', type=literal_eval, action='append',
        #         help = ("Scancodes for dual role key. Expects three arguments, corresponding to the"
        #         "actual key on the keyboard, the single press key, and the modifier key"),
        #         metavar = ('actual_key', 'single_key', 'mod_key'))
        group.add_argument('-k', '--key', type=parse_multi_key, action='append',
                help = ("Scancodes for dual role key. Expects three arguments, corresponding to the"
                "actual key on the keyboard, the single press key, and the modifier key"),
                # )
                metavar = '(actual_key,single_key,mod_key)')

        group.add_argument('-p', '--print', action='store_true',
                help = "Disable dual-role keys, just print back scancodes")
        group.add_argument('-l', '--list', action='store_true',
                help = 'List all input devices recognized by python-evdev')
        parser.add_argument('-d', '--debug', action='store_true',
                help = "Print debug information")
        parser.add_argument('-t', '--timing', action='store_true',
                help = "Print timing results")
        parser.add_argument('-pem', '--pre-emptive-mods', nargs='*', type=int,
                default = [],
                help = ("Scancodes of modifier keys to be taken into account"
                    "in pre-emptive mode"))
        parser.add_argument('-ks', '--kill-switch', nargs='*', type=int, default = [119],
                help = "Scancodes of keys to immediately kill Dualkeys")
        parser.add_argument('-ak', '--angry-keys', nargs='*', type=int, default = [],
                help = "Scancodes of keys that will save the last few input and output strokes, see --angry-key-history")
        parser.add_argument('-akh', '--angry-key-history', type=int, default = 1000,
                help = "Length of angry key history")
        parser.add_argument('-akp', '--angry-key-prefix', nargs = '?', type=str, default = "./log",
                help = "Prefix for key history")
        parser.add_argument('-i', '--ignore', nargs = '*', type=int, default = [],
                help = "Scancodes to ignore")
        parser.add_argument('-rt', '--repeat-timeout', type = int,
                help = "Time frame during which a double press of a modifier key is interpreted as initiating key repeat.")
        parser.add_argument('-rk', '--repeat-keys', nargs = '*', type = int,
                help = "Keys to resolve to single keys if pressed again less than repeat-timeout after the last release.")
        parser.add_argument('-it', '--idle-timeout', type = int,
                help = "Timeout to resolve repeat-keys as modifiers")
        parser.add_argument('-ik', '--idle-keys', nargs = '*', type = int,
                help = "Keys to resolve to modifiers after a repeat-timeout milliseconds.")
        parser.add_argument('--clear-timeout-down', type = int,
                default = 300,
                help = "Timeout after keydown event in ms. After this timeout, if no repeat"
                " event occurred, the key will be lifted.")
        parser.add_argument('--clear-timeout-repeat', type = int,
                default = 100,
                help = "Timeout after keyrepeat event in ms. After this timeout, if no repeat"
                " event occurred, the key will be lifted.")

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


    def main(self):
        """
        Run the main program
        Arguments are mostly for testing purposes, so the routine can be called from the test applications.
        They override variables that are otherwise set by this application.
        """

        self.args = self.__class__.parse_arguments(self.raw_arguments)
        if self.args.debug:
            level = logging.DEBUG
        else:
            level = logging.INFO
        # logging.setLevel(level)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )


        self.all_devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
        if self.args.list:
            self.list_devices()

        self.event_handler = EventHandlerThread(self, name = "event_handler")
        self.event_handler.start()
        logging.debug("event_handler started")

        self.event_pusher = EventPusherThread(main_instance = self, name = "event_pusher",
                shutdown_flag = self.shutdown_flag)
        self.event_pusher.start()
        logging.debug("event_pusher started")
        
        self.connector_thread = ObserverThreadWrapper(main_instance = self)
        self.connector_thread.start()
        logging.debug("connector started")


        # Try-finally clause to catch in particular Ctrl-C and do cleanup
        try:
            # Notify that we are ready
            if self.notify_condition is not None:
                self.test_comm.ui = self.event_handler.ui
                with self.notify_condition:
                    # print('Notifying!')
                    self.notify_condition.notifyAll()
            # Wait for exceptions
            e = self.error_queue.get()
            # print("Raising exception")
            # raise e
            print("Exception raised")
            self.error_queue.task_done()
            # raise e
                
        except KeyboardInterrupt as e:
            logging.info("C-c, KeyboardInterrupt issued")
        except Exception as e:
            # print("Exception in main loop")
            logging.exception(e)
            # raise e
        finally:
            logging.info("Shutting down...")
            self.shutdown_flag.set()
            self.event_queue.put(TerminationException())
            self.event_pusher.join()
            logging.debug("event_pusher joined")
            self.event_handler.join()
            logging.debug("event_handler joined")
            self.connector_thread.stop()
            logging.debug("connector_thread stopped")
            self.cleanup()

if __name__ == "__main__":
    main_instance = Main()
    main_instance.main()
