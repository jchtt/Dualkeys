from dualkeys import Dualkeys
import threading
import time
from evdev import UInput, ecodes
import queue
import asyncio
import functools

class TestCommunication(object):
    pass

def raise_termination_exception(future, s, loop):
    """
    Callback to shut down the event-producer loop.
    """

    # print('Terminating')
    
    asyncio.set_event_loop(loop)
    # print("Printing tasks")
    tasks = asyncio.gather(*asyncio.all_tasks())
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

def test_dualkeys():
    def add_device(device, key_queue, error_queue):
        # future = asyncio.run_coroutine_threadsafe(put_events(key_queue, device, error_queue), event_loop)
        # future = event_loop.call_soon_threadsafe(put_events(key_queue, device, error_queue))
        future = put_events(key_queue, device, error_queue)
        # event_loop.run_until_complete(put_events(key_queue, device, error_queue))
        # device.grab()
        return future

    async def put_events(queue, device, error_queue):
        print('Listening...')
        async for event in device.async_read_loop():
            # print(repr(event))
            try:
                if event.type == ecodes.EV_KEY:
                    queue.put((device, event))
                    print('Test listener: ')
                    print(event)
            except IOError as e:
                # Check if the device got removed, if so, get rid of it
                # if e.errno != errno.ENODEV: raise
                raise e
            except BaseException as e:
                error_queue.put(e)
                raise e

    # async def shutdown_async(shutdown_event):
    #     while not event.is_set
    def shutdown_on_event(event, loop):
        """
        Wait function, to be run asynchronously within the
        event-producer loop in order to signal it to stop.
        """

        print('!!! before event')
        # event.wait()
        # print('after event')
        # loop.close()


    def start_loop(coro, shutdown_event):
        """
        Start given loop. Thread worker.
        """

        print('Listener started')
        loop = asyncio.new_event_loop()
        # coro_shutdown = loop.run_in_executor(None, wait_for_error, (shutdown_event, loop))
        shutdown_future = loop.run_in_executor(None, shutdown_on_event, (shutdown_event, loop))
        # asyncio.ensure_future(shutdown_future, loop = loop)
        print('After ensure')
        # coro_shutdown.add_done_callback(functools.partial(raise_termination_exception, s = "event-producer terminated", loop = loop))
        # asyncio.run_coroutine_threadsafe(coro, loop)
        # loop.create_task(coro)
        # loop.run_forever()
        # future.result()
        # asyncio.set_event_loop(loop)
        # loop.run_forever()


    def test_worker(start_condition, error_queue, ui, test_comm, shutdown_event):
        with start_condition:
            start_condition.wait()

        print('Starting to test!')
        # time.sleep(3)
        # print(test_comm.ui)
        key_queue = queue.Queue()
        # event_loop = asyncio.new_event_loop()
        # test_comm.ui.device.grab()
        # test_comm.ui.syn()

        coro = add_device(test_comm.ui.device, key_queue, error_queue)
        # listener = threading.Thread(target = start_loop, args = (event_loop,), name = "listener")
        listener = threading.Thread(target = start_loop, args = (coro, shutdown_event), name = "listener")
        listener.start()

        time.sleep(1)
        ui.write(ecodes.EV_KEY, ecodes.KEY_A, 1)
        time.sleep(0.01)
        ui.write(ecodes.EV_KEY, ecodes.KEY_A, 0)
        ui.syn()

        print('Finished testing!')
        # asyncio.run(future.cancel)
        time.sleep(2)
        # TODO: need to wait here for everything to be finished
        while not key_queue.empty():
            key = key_queue.get()
            print('Queue:' + str(key))
        # event_loop.call_soon_threadsafe(future.cancel)
        error_queue.put(Dualkeys.ShutdownException())

    try:
        error_queue = queue.Queue()
        shutdown_event = threading.Event()
        start_condition = threading.Condition()
        ui = UInput()
        test_comm = TestCommunication()

        test_instance = threading.Thread(target = test_worker,
                kwargs = {'start_condition' : start_condition, 'error_queue' : error_queue,
                    'ui' : ui, 'test_comm' : test_comm, 'shutdown_event' : shutdown_event},
                name = 'test_instance')
        test_instance.start()
        # dualkeys_instance = threading.Thread(target = Dualkeys.main, kwargs = {'raw_arguments' : ['-p'],
        #     'error_queue' : error_queue, 'notify_condition' : start_condition}, name = 'dualkeys_instance')
        # dualkeys_instance.start()
        Dualkeys.main(raw_arguments = ['-p'], error_queue = error_queue, notify_condition = start_condition,
                listen_device = ui.device, test_comm = test_comm)
                # listen_device = None)
    finally:
        print('Final clause')
        shutdown_event.set()
        # error_queue.put(Dualkeys.ShutdownException())
        # dualkeys_instance.join(1)
        # ui.close()

    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     dualkeys_instance.join()

test_dualkeys()
