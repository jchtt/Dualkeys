import evdev
import errno
import asyncio
import threading
import logging
import concurrent

from .types import *


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
        self.device_lock = threading.Lock()
        # self.target = target

    def _handle_exception(self, loop, context):
        # context["message"] will always be there; but context["exception"] may not
        msg = context.get("exception", context["message"])
        logging.error(f"Caught exception: {msg}")
        self.shutdown_flag.set()

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
        # self.loop.create_task(self._shutdown(self.loop))
        raise TerminationException('Termination scheduled')

    def run(self):
        # calling start will execute "run" method in a thread
        try:
            asyncio.set_event_loop(self.loop)
            self.loop.set_exception_handler(self._handle_exception)
            if self.shutdown_flag is None:
                self.loop.run_until_complete(self._target(*self._args, **self._kwargs))
            else:
                task = self.loop.run_in_executor(None, self._wait_for_shutdown, self.shutdown_flag)
                ret = self.loop.run_until_complete(asyncio.gather(
                    self._target(*self._args, **self._kwargs),
                    task,
                    return_exceptions = False
                    ))
                # print(ret)
                # self.loop.create_task(self._target(*self._args, **self._kwargs))
                # self.loop.run_forever()
            # loop.create_task(self._shutdown(self.loop))
        except TerminationException as e:
            logging.debug('Termination requested')
        finally:
            self.loop.run_until_complete(self._shutdown(self.loop))

class EventPusherThread(AsyncLoopThread):
    def __init__(self, main_instance, *args, **kw):
        super().__init__(target = self.target_fun, *args, **kw)
        self.event_queue = main_instance.event_queue
        self.timing_stats = {}
        self.do_timing = main_instance.args.timing
        self.listen_devices = {}
        self.main_instance = main_instance
        self.keydown_timeout = main_instance.args.clear_timeout_down/1000
        self.keyrepeat_timeout = main_instance.args.clear_timeout_repeat/1000

    def cleanup(self):
        for (fn, dev) in self.listen_devices.items():
            if dev.grab:
                dev.input_device.ungrab()
                dev.input_device.close()

    def future_callback_error_logger(self, future):
        try:
            future.result()
        except concurrent.futures._base.CancelledError:
            # logging.debug("Future got cancelled")
            pass
        except OSError as e:
            # print("OSError")
            raise e
        except Exception as e:
            logging.exception(e)
            self.shutdown_flag.set()
            self.main_instance.error_queue.put(e)

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

    async def clear_on_timeout(self, device_wrapper, key_event, timeout):
        await asyncio.sleep(timeout)
        key_event_up = device_wrapper.last_pressed_up.get(key_event.scancode)
        if key_event_up is not None and key_event_up.event.timestamp() >= key_event.event.timestamp():
            # Above, it needs to be >= because repeat and up fire at the same epoch
            return
        key_event_repeat = device_wrapper.last_pressed_repeat.get(key_event.scancode)
        if key_event_repeat is not None and key_event_repeat.event.timestamp() > key_event.event.timestamp():
            return

        logging.warn(f"Scancode {key_event.scancode} did not go up in time, lifting it.")
        new_event = evdev.KeyEvent(key_event.event)
        new_event.keystate = new_event.key_up
        self.event_queue.put((device_wrapper.input_device, new_event))


    async def put_events(self, device):
        """
        Coroutine for putting events in the event queue.
        """

        try:
            device_wrapper = self.listen_devices[device.path]
            async for event in device.async_read_loop():
                if event.type == evdev.ecodes.EV_KEY:
                    key_event = evdev.util.categorize(event)
                    if key_event.keystate == 0:
                        device_wrapper.last_pressed_up[key_event.scancode] = key_event
                    elif key_event.keystate == 1:
                        # pass
                        future = self.loop.create_task(self.clear_on_timeout(device_wrapper = device_wrapper,
                            key_event = key_event, timeout = self.keydown_timeout))
                        future.add_done_callback(self.future_callback_error_logger)
                    elif key_event.keystate == 2:
                        device_wrapper.last_pressed_repeat[key_event.scancode] = key_event
                        future = self.loop.create_task(self.clear_on_timeout(device_wrapper = device_wrapper,
                            key_event = key_event, timeout = self.keyrepeat_timeout))
                        future.add_done_callback(self.future_callback_error_logger)

                    self.event_queue.put((device, key_event))
                    # print("Done putting")
        except (IOError, OSError) as e:
            # print("IOError")
            # print("HERE")
            # print(type(e))
            # print(e.errno)
            # print(errno.ENODEV)
            # Check if the device got removed, if so, get rid of it
            if e.errno != errno.ENODEV: raise e
            logging.debug("Device {0.fn} removed.".format(device))
            self.remove_device(device)
            # break
        # except BaseException as e:
        except Exception as e:
            # self.main_instance.error_queue.put(e)
            raise e

    def add_device(self, device, handle_type = None):
        """
        Add device to listen_devices, grab_devices and selector if
        it matches the output of get_handle_type.
        """

        self.device_lock.acquire()
        logging.debug("Adding device {}".format(device))
        if handle_type is None:
            handle_type = self.__class__.get_handle_type(device)
        grab = handle_type == HandleType.GRAB
        if grab:
            device.grab()
        future = asyncio.run_coroutine_threadsafe(self.put_events(device), self.loop)
        future.add_done_callback(self.future_callback_error_logger)
        device_wrapper = DeviceWrapper(grab = grab, input_device = device, future = future)
        self.listen_devices[device.path] = device_wrapper

        # time.sleep(0.2)
        # device.repeat = evdev.device.KbdInfo(300, 600000)
        # print("Device {}, repeat = {}".format(device, device.repeat))
        # logging.debug("Device {}".format(device))
        self.device_lock.release()

    def remove_device(self, device):
        """
        Remove device, complementary operation to add_device.

        Works on Evdev and Udev devices.
        """

        # Handle udev and evdev devices
        self.device_lock.acquire()
        if type(device) is evdev.device.InputDevice:
            fn = device.path
        else:
            fn = device.device_node
        if fn in self.listen_devices:
            # selector.unregister(listen_devices[fn])
            self.loop.call_soon_threadsafe(self.listen_devices[fn].future.cancel)
            del self.listen_devices[fn]
        self.device_lock.release()

    async def target_fun(self):
        """
        This only serves for initialization, more routines are added here and later by
        the observer thread.
        """

        # device_lock = threading.Lock() # Lock for access to the dicts etc.

        # Find all devices we want to reasonably listen to
        # If listen_device is given, only listen on that one
        if self.main_instance.listen_device is not None:
            self.add_device(self.main_instance.listen_device, handle_type = HandleType.GRAB)
        else:
            for device in self.main_instance.all_devices:
                # if device.path != "/dev/input/event5":
                if True:
                    self.add_device(device)
