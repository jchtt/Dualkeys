# import evdev
import errno
import asyncio
# import threading
import multiprocessing as mp
import logging
import concurrent
import ctypes
import libevdev
import signal

from .types import *

class EventPusherWorker(mp.Process):
    def __init__(self, main_instance,
            device_node, *args, grab = False, **kw):
        super().__init__(*args, daemon = True, **kw)
        self.event_queue = main_instance.event_queue
        self.error_queue = main_instance.error_queue
        self.timing_stats = {}
        self.do_timing = main_instance.args.timing
        # self.main_instance = main_instance
        self.keydown_timeout = main_instance.args.clear_timeout_down/1000
        self.device_node = device_node
        self.grab = grab
        self.pressed_dict = {}
        self.last_pressed_up = {}

#     def get_id(self): 
#         if hasattr(self, '_thread_id'): 
#             return self._thread_id 
#         for id, thread in threading._active.items(): 
#             if thread is self: 
#                 return id

    def cleanup(self):
        # TODO double check
        try:
            if self.grab:
                self.device.ungrab()
        except OSError as e:
            if e.errno != errno.EBADF: raise e
            # dev.input_device.close()

    def event_loop_inner(self, event):
        if event.matches(libevdev.EV_SYN):
            # logging.info(str(event))
            if event.matches(libevdev.EV_SYN.SYN_DROPPED):
            # if True:
                logging.info("SYN_DROPPED received")
        if event.matches(libevdev.EV_KEY):
            key_event = KeyEvent(event, grab = self.grab)
            scancode = key_event.code
            if False:
                if scancode not in self.pressed_dict:
                    self.pressed_dict[scancode] = 0
                if key_event.keystate == 0:
                    self.last_pressed_up[scancode] = event
                    self.pressed_dict[scancode] -= 1
                elif key_event.keystate == 1:
                    self.pressed_dict[scancode] += 1
                elif key_event.keystate == 2:
                    pass
                logging.info("[" + ",".join([f"({libevdev.evbit(1,k)}, {v})" \
                        for k, v in self.pressed_dict.items() \
                        if v != 0 and libevdev.evbit(1,k) is not None]) + "]")

            # elif key_event.keystate == 2:
            #     device_wrapper.last_pressed_repeat[key_event.scancode] = key_event
            #     future = self.loop.create_task(self.clear_on_timeout(device_wrapper = device_wrapper,
            #         key_event = key_event, timeout = self.keyrepeat_timeout))
            #     future.add_done_callback(self.future_callback_error_logger)

            self.event_queue.put((self.device_node, key_event))

    def put_events(self):
        """
        Routine for putting events in the event queue.
        """

        while True:
            try:
                for event in self.device.events():
                    self.event_loop_inner(event)
            # except TerminationException as e:
            #     logging.info(f"Event pusher {self.name} was asked to shut down.")
            #     raise e
            except libevdev.EventsDroppedException:
                logging.warn("EventsDroppedException, syncing up.")
                for event in dev.sync():
                    self.event_loop_inner(event)
            except OSError as e:
                # print("IOError")
                # print("HERE")
                # print(type(e))
                # print(e.errno)
                # print(errno.ENODEV)
                # Check if the device got removed, if so, get rid of it
                if e.errno != errno.ENODEV: raise e
                logging.debug(f"Error in event pusher because "
                        "device {self.device_node} was removed.")
                # self.main_instance.connector_thread.remove_device(self.device_node)
                break
            # except BaseException as e:
            # except Exception as e:
            #     # self.main_instance.error_queue.put(e)
            #     raise e

#     def raise_exception(self, exception_type = TerminationException): 
#             thread_id = self.get_id() 
#             res = ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 
#                     ctypes.py_object(exception_type)) 
#             if res > 1: 
#                 ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0) 
#                 print('Exception raise failure') 

    def handler(self, a, b):
        logging.info("HANDLER")

    def run(self):
        """
        This only serves for initialization, more routines are added here and later by
        the observer thread.
        """

        # device_lock = threading.Lock() # Lock for access to the dicts etc.

        # Find all devices we want to reasonably listen to
        # If listen_device is given, only listen on that one

        # Don't handle KeyboardInterrupts on its own
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, lambda x, y: None)

        try:
            with open(self.device_node, 'rb') as fd:
                self.device = libevdev.Device(fd)
                if self.grab:
                    self.device.grab()
                self.put_events()
        except (TerminationException, InterruptedError):
            logging.info(f"Event pusher {self.name} was asked to shut down.")
        except Exception as e:
            logging.info(f"Exception received in event_pusher on {self.device_node}")
            self.error_queue.put(TerminationException())
            raise e
        finally:
            self.cleanup()
