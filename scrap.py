import threading
import time
import asyncio
import functools
import evdev
from evdev import ecodes

def test_jois():
    def worker():
        while True:
            time.sleep(1)
            print('Running')


    thread = threading.Thread(target = worker)
    thread.start()
    time.sleep(3)
    thread.join()

def test_condition():
    def worker1():
        pass

    def worker2():
        pass

def test_asyncio():
    from evdev import InputDevice, categorize, ecodes

    dev = InputDevice('/dev/input/event5')
    # dev.close()

    async def helper(dev):
        async for ev in dev.async_read_loop():
            print(repr(ev))
        # print('hello')
        # await asyncio.sleep(1)
        # print('world')

    loop = asyncio.get_event_loop()
    loop.run_until_complete(helper(dev))
    # asyncio.run(helper())
    # asyncio.run_coroutine_threadsafe(helper(dev), loop)
    # time.sleep(20)

def test_asyncio3():
    async def test_asyncio2():
        print('hello')
        await asyncio.sleep(3)
        print('world')
    asyncio.run(test_asyncio2())

def test_asyncio4():
    def sync_wait(t):
        print(f'called {time.time()}')
        if t == 2:
            print("Exception here?")
            raise Exception('wrong parameter')
        time.sleep(t)
        print(f'done {time.time()}')

    async def async_wait(t, create_new = False):
        print(f'called {time.time()}')
        if create_new:
            asyncio.create_task(async_wait(2))

        if t == 2:
            print("Exception here?")
            raise Exception('wrong parameter')
        await asyncio.sleep(t)
        print(f'done {time.time()}')


    def handle_exception(loop, context):
        # context["message"] will always be there; but context["exception"] may not
        print('Exception handler')
        msg = context.get("exception", context["message"])
        print(f"Caught exception: {msg}")
        print("Shutting down...")
        loop.close()
        # asyncio.create_task(shutdown(loop))

    def handle_exception_callback(future):
        e = future.exception()
        print(e)
        raise e
        # if e:
        #     print(e)

    # loop = asyncio.get_event_loop()
    def run():
        loop = asyncio.new_event_loop()
        future = loop.run_in_executor(None, sync_wait, (2, 3))
        print(type(future))
        loop.run_in_executor(None, sync_wait, 3)
        asyncio.ensure_future(future)
        loop.run_forever()
    # thread = threading.Thread(target = run)
    # thread.start()
    run()
    # asyncio.set_event_loop(loop)
    # task1 = loop.create_task(async_wait(3, create_new = False))
    # loop.run_forever()
    # asyncio.run(async_wait(3))
    # print('after loop')

    # task2 = loop.create_task(async_wait(3))
    # loop.run_until_complete(task2)
    # task3 = loop.create_task(async_wait(3))
    # loop.run_until_complete(task3)

    # def run_things():
    #     async def main_async(loop):
    #         future1 = loop.run_in_executor(None, sync_wait, 2)
    #         future2 = loop.run_in_executor(None, sync_wait, 3)
    #         # loop.create_task(future1)
    #         ret = await asyncio.gather(future1, future2, return_exceptions = True)
    #         print(ret)
    #     # try:
    #     # future_g = asyncio.gather(future1, return_exceptions = True)
    #     # print(future1)
    #     # future1.add_done_callback(handle_exception_callback)
    #     # except Exception as e:
    #     #     print(e)
    #     #     raise e
    #     loop = asyncio.new_event_loop()
    #     loop.set_exception_handler(handle_exception)
    #     loop.run_until_complete(main_async(loop))
    #     # loop.run_forever()
    #     # ret = loop.run_until_complete(future_g)
    #     # print(ret)
    #     # ret = asyncio.ensure_future(future1)
    #     # time.sleep(3)
    #     # asyncio.wait(future1)
    #     # print(future1.done())
    #     # print(future1.exception())


    # threading.Thread(target = sync_wait).start()
    # threading.Thread(target = run_things_proxy).start()
    # threading.Thread(target = run_things).start()


    # loop.create_task(async_wait(2))
    # loop.create_task(async_wait(3))
    # # loop.run_until_complete()
    # loop.run_forever()

    print('after loop')
    # task1 = loop.create_task(asyncio.ensure_future(future1))
    # task2 = asyncio.create_task(async_wait(3))
    # loop.run_forever()

def test_grab():
    ui = evdev.UInput()
    ui.device.grab()
    ui.write(ecodes.EV_KEY, ecodes.KEY_A, 1)
    ui.syn()
    time.sleep(0.1)
    ui.write(ecodes.EV_KEY, ecodes.KEY_A, 0)
    ui.syn()
    ui.device.ungrab()

# test_join()
# time.sleep(3)
# test_asyncio()
# test_asyncio3()
# test_asyncio4()
test_grab()


