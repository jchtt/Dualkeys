from dualkeys import Dualkeys
import threading
import time
from evdev import UInput, ecodes, categorize
import queue
import asyncio
import functools
import itertools
import re
from colorama import Fore, Back, Style 
import logging

logging.basicConfig(
    # level=logging.INFO,
    level=logging.DEBUG,
    # level=logging.WARNING,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def str2list(s):
    l = s.split(',')
    l = [elem.strip().split(' ') for elem in l]
 
    def replace_by_scancode(t):
        key_str = t[0].upper()
        # TODO: more here
        key_repl_dict = {
                'LCTRL' : 'LEFTCTRL',
                'CTRL' : 'LEFTCTRL',
                'RCTRL' : 'RIGHTCTRL',
                'LSHIFT' : 'LEFTSHIFT',
                'SHIFT' : 'RIGHTSHIFT',
                'RSHIFT' : 'RIGHTSHIFT'
                }
        key_str = key_repl_dict.get(key_str, key_str)
        
        key_str = 'KEY_' + key_str
        scancode = vars(ecodes)[key_str]

        if len(t) == 1:
            return [(scancode, 1), (scancode, 0)]

        state_repl_dict = {
                'u' : 0,
                'd' : 1
                }
        keystate = t[1]
        keystate = state_repl_dict.get(keystate, keystate)

        return [(scancode, keystate)]

    l = [replace_by_scancode(elem) for elem in l]
    l = list(itertools.chain.from_iterable(l))
    return l

# [(30, 1), (29, 0)] -> 'A u, LCTRL d'
def list2str(l):
    def scancode_replace_by_str(scancode):
        s = re.sub('KEY_', '', ecodes.KEY[scancode])
        # TODO: More here
        repl_dict = {
                'LEFTCTRL' : 'LCTRL',
                'RIGHTCTRL' : 'RCTRL',
                'LEFTSHIFT' : 'LSHIFT',
                'RIGHTSHIFT' : 'RSHIFT',
                }
        return repl_dict.get(s, s)

    def keystate_replace_by_str(keystate):
        repl_dict = {
                0: 'u',
                1: 'd',
                2: 'h'
                }
        return repl_dict[keystate]

    new_l = []
    for elem in l:
        # elem[0] = replace_by_
        key_str = scancode_replace_by_str(elem[0])
        keystate_str = keystate_replace_by_str(elem[1])
        new_l.append([key_str, keystate_str])

    new_l = [' '.join(elem) for elem in new_l]
    return ', '.join(new_l)

def queue2str(q):
    combined_l = []
    single_l = []
    while not q.empty():
        elem = q.get()
        if type(elem) is StopMarker:
            combined_l.append(single_l)
            single_l = []
            continue
        event = elem[1]
        cat_event = categorize(event)
        single_l.append((cat_event.scancode, cat_event.keystate))

    return [list2str(l) for l in combined_l]


def test_symbol_translation():
    test_str = 'Ctrl d, C, Ctrl u'
    print(str2list(test_str))

    test_l = [(29, 1), (29, 0), (30, 1), (30, 0)]
    print(list2str(test_l))

class TestCommunication():
    pass

class TerminationException(Exception):
    pass

class StopMarker():
    pass

class AsyncLoopThread(threading.Thread):
    def __init__(self, group=None, target=None, name=None,
            args=(), kwargs={}, daemon=None, shutdown_flag = None, cleanup_callback = None):
        super().__init__(group = group, target = target, name = name,
                args = args, kwargs = kwargs, daemon = daemon)
        self.shutdown_flag = shutdown_flag
        self.cleanup_callback = cleanup_callback
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

    def _handle_exception(self, loop, context):
        message = context.get("exception", context["message"])
        print(f'Exception occurred in async code: {message}')
        asyncio.create_task(self._shutdown(loop))

    
    def _wait_for_shutdown(self, shutdown_flag):
        self.shutdown_flag.wait()
        raise TerminationException('Termination scheduled')

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            # loop.set_exception_handler(self._handle_exception)
            if self.shutdown_flag is None:
                loop.run_until_complete(self._target())
            else:
                task = loop.run_in_executor(None, self._wait_for_shutdown, self.shutdown_flag)
                loop.run_until_complete(asyncio.gather(
                    self._target(),
                    task
                    ))
            # loop.create_task(self._shutdown(self.loop))
        except TerminationException as e:
            print('Termination requested')
        finally:
            loop.run_until_complete(self._shutdown(self.loop))

def test_AsyncLoopThread():
    async def test():
        print('bla')
        # raise Exception('test exception')

    event = threading.Event()
    thread = AsyncLoopThread(target = test, shutdown_flag = event)
    thread.start()

    def test_run():
        time.sleep(3)
        event.set()

    thread2 = threading.Thread(target = test_run)
    thread2.start()

def test_dualkeys(prog_arguments, test_sequences,
        pause_time = 0.01):
    async def add_device(device, key_queue, error_queue):
        # future = asyncio.run_coroutine_threadsafe(put_events(key_queue, device, error_queue), event_loop)
        # future = event_loop.call_soon_threadsafe(put_events(key_queue, device, error_queue))
        try:
            device.grab()
            await put_events(key_queue, device, error_queue)
            # device.ungrab()
        finally:
            # pass
            device.ungrab()
            # print('Ungrabbed')
        # event_loop.run_until_complete(put_events(key_queue, device, error_queue))

    async def put_events(queue, device, error_queue):
        logging.info('Start listening routine')
        async for event in device.async_read_loop():
            try:
                if event.type == ecodes.EV_KEY:
                    queue.put((device, event))
                    cat_event = categorize(event)
                    logging.info(f'Received event, keycode: {cat_event.keycode}, keystate: {cat_event.keystate}')

            except IOError as e:
                # Check if the device got removed, if so, get rid of it
                # if e.errno != errno.ENODEV: raise
                raise e
            except BaseException as e:
                error_queue.put(e)
                raise e

    def send_sequence(ui, seq, pause_time):
        for x in seq:
            ui.write(ecodes.EV_KEY, *x)
            ui.syn()
            time.sleep(pause_time)

    def check_sequences(test_sequences, return_sequences):
        for test_seq, return_seq in zip(test_sequences, return_sequences):
            if test_seq[1].upper() == return_seq.upper():
                print(Fore.GREEN + 'PASS:', end = '')
            else:
                print(Fore.RED + 'FAIL:', end = '')
            print(Style.RESET_ALL + f' sent: {test_seq[0]}, expected: {test_seq[1]}, received: {return_seq}')

    def test_worker(start_condition, error_queue, ui, test_comm, shutdown_flag):
        with start_condition:
            start_condition.wait()

        logging.info('Starting test worker')
        key_queue = queue.Queue()

        def cleanup_callback():
           # test_comm.ui.device.ungrab() 
           pass

        listener_thread = AsyncLoopThread(
                target = functools.partial(add_device,
                    device = test_comm.ui.device,
                    key_queue = key_queue,
                    error_queue = error_queue),
                cleanup_callback = cleanup_callback,
                shutdown_flag = shutdown_flag)
        # coro = add_device(test_comm.ui.device, key_queue, error_queue)
        # listener = threading.Thread(target = start_loop, args = (event_loop,), name = "listener")
        # listener = threading.Thread(target = start_loop, args = (coro, shutdown_flag), name = "listener")
        listener_thread.start()

        time.sleep(0.1)
        logging.info('Sending sequences')
        for seq, _ in test_sequences:
            send_sequence(ui, str2list(seq), pause_time)
            time.sleep(0.2)
            key_queue.put(StopMarker())
        logging.info('Finished sending!')

        # asyncio.run(future.cancel)
        # time.sleep(2)

        # TODO: need to wait here for everything to be finished
        time.sleep(0.2)
        return_sequences = queue2str(key_queue)
        logging.debug(f'return_sequences = {return_sequences}')
        check_sequences(test_sequences, return_sequences)
        # event_loop.call_soon_threadsafe(future.cancel)

        # Shut down by putting ShutdownException
        error_queue.put(Dualkeys.ShutdownException())

    try:
        error_queue = queue.Queue()
        shutdown_flag = threading.Event()
        start_condition = threading.Condition()
        ui = UInput()
        test_comm = TestCommunication()

        test_instance = threading.Thread(target = test_worker,
                kwargs = {'start_condition' : start_condition, 'error_queue' : error_queue,
                    'ui' : ui, 'test_comm' : test_comm, 'shutdown_flag' : shutdown_flag},
                name = 'test_instance')
        test_instance.start()
        # dualkeys_instance = threading.Thread(target = Dualkeys.main, kwargs = {'raw_arguments' : ['-p'],
        #     'error_queue' : error_queue, 'notify_condition' : start_condition}, name = 'dualkeys_instance')
        # dualkeys_instance.start()
        Dualkeys.main(raw_arguments = prog_arguments, error_queue = error_queue, notify_condition = start_condition,
                listen_device = ui.device, test_comm = test_comm)
                # listen_device = None)
    finally:
        logging.info('Executing final clause')
        shutdown_flag.set()
        # error_queue.put(Dualkeys.ShutdownException())
        # dualkeys_instance.join(1)
        # ui.close()

    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     dualkeys_instance.join()



# test_AsyncLoopThread()
# test_symbol_translation()



# test_sequences = [
#         # ('A d, A u', 'A d, A u'),
#         # regular key

# #         ('space d, A d, A u, Space u', 'lctrl d, A d, A u, lctrl u'),
# #         # standard space -> ctrl

# #         ('space d, A d, Space u, A u', 'LCTRL d, LCTRL u, SPACE d, A d, SPACE u, A u'),
# #         # sticky finger space -> ctrl

# #         ('f d, space d, A d, A u, space u, f u', 'LEFTSHIFT d, LCTRL d, A d, A u, LCTRL u, LEFTSHIFT u'),
# #         # standard space -> ctrl, f -> shift

# #         ('f d, space d, A d, A u, f u, space u', 'LEFTSHIFT d, LCTRL d, A d, A u, LEFTSHIFT u, LCTRL u'),
# #         # standard space -> ctrl, f -> shift, reverse lifting

#         # ('lctrl d, lshift d, lctrl u, lshift u', 'LCTRL d, LSHIFT d, LSHIFT u, LSHIFT d, LCTRL u, LSHIFT u'),
#         # # regular modifiers, n1
#         # # TODO: think about this. Technically, there's an unnecessary lshift action going on

#         # ('lctrl d, lshift d, lshift u, lctrl u', 'LCTRL d, LSHIFT d, LSHIFT u, LCTRL u'),
#         # # regular modifiers, n2. Here, pre-emptive is already disabled, good!

#         # ('lshift d, F d, F u, lshift u', 'LSHIFT d, F d, F u, LSHIFT u'),
#         # # double modifier

#         # ('lshift d, F d, lshift u, F u', 'LSHIFT d, F d, LSHIFT u, F u'),
#         # # double modifier, sticky fingers

#         # ('lshift d, F d, J d, J u, F u, lshift u', 'LSHIFT d, RSHIFT d, RSHIFT u, J d, J u, LSHIFT u'),
#         # # double modifier, with another key, regular

#         # ('lshift d, F d, J d, lshift u, F u, J u', 'LSHIFT d, RSHIFT d, RSHIFT u, J d, J u, LSHIFT u'),
#         # # double modifier, with another key, regular
#         # # TODO: double check, this one does not look right, but first need to check simpler cases

#         ('A d, space d, A u, B d, B u, space u', 'A d, A u, LCTRL d, B d, B u, LCTRL u'),
#         # regular key sandwhich, NOT pre-emptive
#         ]
# prog_arguments = ['-c', 'config_testing.yaml']
# test_dualkeys(prog_arguments, test_sequences)

# Not pre-emptive
test_sequences = [
        ('space d, A d, Space u, A u', 'LCTRL d, LCTRL u, SPACE d, A d, SPACE u, A u'),
        # sticky finger space -> ctrl

        # ('A d, space d, A u, B d, B u, space u', 'A d, A u, LCTRL d, B d, B u, LCTRL u'),
        # # regular key sandwhich, NOT pre-emptive

        # ('lshift d, space d, lshift u, B d, B u, space u', 'LSHIFT d, LSHIFT u, LCTRL d, B d, B u, LCTRL u'),
        # # regular modifier key, sandwich

        # ('lshift d, space d, lshift u, space u', 'LSHIFT d, LSHIFT u, LCTRL d, B d, B u, LCTRL u'),
        # # regular modifier key, sandwich
        # # TODO: go back here when next is solved

        # ('A d, space d, A u, space u', 'LSHIFT d, LSHIFT u, LCTRL d, B d, B u, LCTRL u'),
        # # regular modifier key, sandwich

        # ('lshift d, space d, A d, lshift u, space u, A u', 'LSHIFT d, LSHIFT u, LCTRL d, B d, B u, LCTRL u'),
        # # regular modifier key, sandwich
        ]
prog_arguments = ['-c', 'config_testing.yaml']
test_dualkeys(prog_arguments, test_sequences)
