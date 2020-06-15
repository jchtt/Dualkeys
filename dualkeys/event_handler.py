import datetime
import time
import threading
import logging
from collections import deque

from .types import *

def codes_to_keys(code):
    if type(code) is int:
        return "[" + evdev.ecodes.keys[code] + "]"
    else:
        return "[" + ", ".join(map(evdev.ecodes.keys.get, code)) + "]"

class EventHandlerThread(threading.Thread):
    """
    Thread that handles the events in the event queue
    """

    def __init__(self, main_instance, *args, **kw):
        super().__init__(*args, **kw)

        self.main_instance = main_instance
        args = main_instance.args
        self.event_queue = main_instance.event_queue
        self.ui = evdev.UInput()

        self.do_print = main_instance.args.print
        self.registered_keys = {}
        self.pre_emptive_mods = set(args.pre_emptive_mods)
        self.do_pre_emptive = len(self.pre_emptive_mods) > 0
        self.kill_switches = args.kill_switch
        self.handle_repeat = args.repeat_timeout is not None
        self.repeat_timeout = float(args.repeat_timeout) / 1000 if self.handle_repeat else None
        self.do_timing = main_instance.args.timing
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
        self.print_registered_keys()

        # Main status indicator
        self.event_list = DLList()
        self.conflict_list = {} # TODO: not a good name if it's a dict
        self.key_counter = {}
        self.resolution_dict = {} # Remember currently unresolved keys
        self.pre_emptive_dict = {}
        self.back_links = {}
        self.last_pressed = {}
        self.last_pressed_key = None

        self.error_queue = self.main_instance.error_queue

    def cleanup(self):
        self.ui.close()

    def print_registered_keys(self):
        print("Registered dual-role keys:")
        for key in self.registered_keys.values():
            primary_key = key.primary_key
            primary_key_code = evdev.ecodes.KEY[primary_key]
            single_key = key.single_key
            single_key_code = evdev.ecodes.KEY[single_key]
            mod_key = key.mod_key
            mod_key_code = evdev.ecodes.KEY[mod_key]
            print("actual key = [{} | {}], ".format(primary_key, primary_key_code), end = "")
            print("single key = [{} | {}], ".format(single_key, single_key_code), end = "")
            print("modifier key = [{} | {}]".format(mod_key, mod_key_code))

    def send_key(self, scancode, keystate, bypass = False):
        """
        Send a key event via uinput and handle key counters for modifiers
        """
        # state_obj.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
        # state_obj.ui.syn()
        if bypass:
            self.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
            self.ui.syn()
            return

        count = self.key_counter.setdefault(scancode, 0)
        if keystate == 1:
            if count == 0:
                self.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
                self.ui.syn()
                logging.debug(f'Pushed {scancode}, {evdev.ecodes.KEY[scancode]}')
                if self.history is not None:
                    self.history[-1][-1].append(
                            (scancode, evdev.ecodes.KEY[scancode], keystate)
                            )
            self.key_counter[scancode] += 1
        elif keystate == 0:
            if count == 1:
                self.ui.write(evdev.ecodes.EV_KEY, scancode, keystate)
                self.ui.syn()
                logging.debug(f'Lifted {scancode}, {evdev.ecodes.KEY[scancode]}')
                if self.history is not None:
                    self.history[-1][-1].append(
                            (scancode, evdev.ecodes.KEY[scancode], keystate)
                            )
            self.key_counter[scancode] -= 1

    def print_event(self, event, grabbed = True):
        """
        Alternative callback function that passes everything through,
        """

        if event.type == evdev.ecodes.EV_KEY:
            key_event = evdev.util.categorize(event)
            # if key_event.keystate <= 1:
            if True:
                print('scancode = {}, keystate = {}, keycode = {}'.format(key_event.scancode, key_event.keystate, key_event.keycode))
            if grabbed:
                self.send_key(key_event.scancode, key_event.keystate)
            if key_event.scancode in self.kill_switches:
                logging.info("Kill switch pressed, shutting down")
                self.error_queue.put(TerminationException())
        return True

    ##########################
    # Key handling functions #
    ##########################

    ## New

    def handle_event(self, event, active_keys, grabbed = True, pre_emptive = False):
        """
        Handle the incoming key event. Main callback function.
        """


        # Only act on key presses
        if event.type != evdev.ecodes.EV_KEY:
            return True

        if self.do_timing:
            cur_time = time.time()

        key_event = evdev.util.categorize(event)

        # Don't act on weird keys
        if key_event.scancode in self.ignore_keys:
            return True
        # if key_event.scancode == 48:
        #     raise KeyboardInterrupt
        logging.debug("Received = {}, keystate = {}, key = {}, grabbed = {}.".format(key_event.scancode, key_event.keystate, evdev.ecodes.KEY.get(key_event.scancode, None), grabbed))

        # Handle kill switch, shutdown
        if key_event.scancode in self.kill_switches:
            self.error_queue.put(TerminationException())
            return False

        # Handle angry key, save the history
        if key_event.scancode in self.angry_keys and \
                key_event.keystate == 1:
            logging.debug('Angry key triggered')
            today = datetime.datetime.now()
            today_str = today.strftime('%Y-%m-%d_%H-%M-%S')
            with open(self.angry_key_prefix + '-' + today_str + '.txt', 'w') as f:
                f.write('\n'.join([', '.join([str(x) for x in elem])
                    for elem in self.history])) # TODO: nicer output, date stamp
                # f.write(str(state_obj.history))

        # Save history
        if self.history is not None:
            self.history.append(
                    [(key_event.scancode,
                        evdev.ecodes.KEY.get(key_event.scancode),
                        key_event.keystate), []]
                    )

        if not grabbed:
            self.handle_ungrabbed_key(key_event, pre_emptive)
        else:
            if key_event.keystate == 1:
                # Key down
                # logging.debug("Down.")
                if key_event.scancode not in self.registered_keys:
                    self.handle_regular_key_down(key_event, pre_emptive)
                else:
                    self.handle_special_key_down(key_event, pre_emptive)
            # Key up
            elif key_event.keystate == 0:
                logging.debug("Key up")
                if key_event.scancode not in self.registered_keys:
                    self.handle_regular_key_up(key_event, pre_emptive)
                else:
                    self.handle_special_key_up(key_event, pre_emptive)
            elif self.handle_idle and key_event.keystate == 2:
                # Key repeat
                logging.debug("Key repeat")
                self.handle_key_repeat(key_event, pre_emptive)
                # if key_event.scancode not in state_obj.registered_keys:
                #     handle_regular_key_repeat(state_obj, key_event, pre_emptive)
                # else:
                #     handle_special_key_repeat(state_obj, key_event, pre_emptive)
            else:
                logging.debug("Not handling, unknown keystate")

            logging.debug("Done.")
            logging.debug("event_list: {}".format(self.event_list))
            logging.debug("resolution_dict: {}".format(self.resolution_dict))
            logging.debug("key_counter: {}".format({k: v for (k, v) in self.key_counter.items() if v != 0}))
        if self.do_timing:
            self.timing_stats["total"] += time.time() - cur_time
            self.timing_stats["calls"] += 1
            if self.timing_stats["calls"] % 10 == 0:
                print("Average time/call = {}".format(self.timing_stats["total"]/state_obj.timing_stats["calls"]))

        return True

    def handle_ungrabbed_key(self, key_event, pre_emptive):
        # If we get a mouse button, immediately resolve everything

        logging.debug("Event from ungrabbed device, resolving event_list.")
        scancode = key_event.scancode
        # resolve_previous_to_modifiers(self, key_event, pre_emptive) 
        self.resolve(key_event, pre_emptive, to_node = None)
        # send_key(self, scancode, key_event.keystate)
        logging.debug("Pushing key: {}".format(scancode))

    def handle_regular_key_down(self, key_event, pre_emptive):
        # Regular key goes down: either there is no list, then send;
        # otherwise, put it in the queue and resolve non-tf keys

        to_push = key_event.scancode
        self.last_pressed_key = to_push
        # TODO: figure out why I handled pre_emptive separately here
        if not self.event_list.isempty(): #or to_push in self.pre_emptive_mods:
            # Regular key, but not sure what to do, so put it in the list
            key_obj = UnresolvedKey(scancode = to_push,
                    time_pressed = key_event.event.timestamp(),
                    keystate = 1, resolution_type = ResolutionType.REGULAR)
            node = self.event_list.append(key_obj)
            self.back_links[key_event.scancode] = node
            if pre_emptive and to_push in self.pre_emptive_mods:
                # TODO: might change order here to not push it when it will be resolved
                # afterwards
                key_obj.pre_emptive_pressed = True
                key_obj.mod_key = to_push
                self.send_key(to_push, key_event.keystate)
                logging.debug(f'Push {to_push} because pre_emptive')

            self.resolve(key_event, pre_emptive, to_node = node, from_down = True)

            logging.debug("Regular key, append to list: {}".format(codes_to_keys(key_event.scancode)))
            logging.debug("Event list: {}".format(self.event_list))
        else:
            # Regular key, no list? Send!
            if key_event.keystate == 1:
                self.send_key(key_event.scancode, key_event.keystate)
                # self.resolution_dict[key_event.scancode] = ResolutionType.REGULAR
            logging.debug("Regular key, push {}".format(key_event.scancode))

        self.last_pressed[key_event.scancode] = time.time()

    def handle_special_key_down(self, key_event, pre_emptive):
        # Special key, on a push we never know what to do, so put it in the list

        # logging.debug("Registered key pressed")

        cur_key = key_event.scancode
        to_push = self.registered_keys[cur_key].mod_key
        key_obj = UnresolvedKey(scancode = cur_key,
                time_pressed = key_event.event.timestamp(),
                keystate = 1, resolution_type = ResolutionType.UNRESOLVED)

        if self.handle_repeat \
                and cur_key in self.repeat_keys:
                # and self.last_pressed_key is not None \
                # and cur_key == self.last_pressed_key \
            last_pressed = self.last_pressed.get(cur_key)
            if last_pressed is not None \
                    and time.time() - last_pressed < self.repeat_timeout:
                logging.debug(f'Double key press, resolve {cur_key} to regular key')
                key_obj.resolution_type = ResolutionType.DUAL_REGULAR 
                node = self.event_list.append(key_obj)
                self.back_links[cur_key] = node
                # SOMEDAY: from_down probably should be false, but honestly, maybe don't really want to let
                # this fire while a list is in place anyway and actually do away with putting it on there
                # in the first place.
                #
                # Also, the early return here is ugly.
                self.resolve(key_event, pre_emptive, node, from_down = True)
                self.last_pressed[cur_key] = time.time()
                self.last_pressed_key = cur_key
                return

        if pre_emptive:
            key_obj.pre_emptive_pressed = True
            key_obj.mod_key = to_push
            node = self.event_list.append(key_obj)
            self.send_key(to_push, key_event.keystate)
            logging.debug("Because pre_emptive, push {}, append {}".format(codes_to_keys(to_push), codes_to_keys(key_event.scancode)))
        else:
            # TODO: mod_down should be False here?
            node = self.event_list.append(key_obj)
            logging.debug("Not pre_emptive, append {}".format(codes_to_keys(key_event.scancode)))

        self.back_links[cur_key] = node
        self.resolve(key_event, pre_emptive, node, from_down = True)

        # Save when pressed
        self.last_pressed[cur_key] = time.time()
        self.last_pressed_key = cur_key

    def handle_regular_key_up(self, key_event, pre_emptive):
        # Regular key goes up

        logging.debug("Regular key")

        cur_key = key_event.scancode

        if self.event_list.isempty():
            # Nothing backed up, just send.
            self.send_key(cur_key, key_event.keystate)
            logging.debug("No list, lift {}".format(codes_to_keys(cur_key)))
        else:
            key_obj = UnresolvedKey(scancode = cur_key,
                    time_pressed = key_event.event.timestamp(),
                    keystate = 0, resolution_type = ResolutionType.REGULAR)
            node = self.event_list.append(key_obj)
            logging.debug("Key in list, resolve {}".format(codes_to_keys(cur_key)))

            if pre_emptive and cur_key in self.pre_emptive_mods:
                self.send_key(cur_key, 0)
                key_obj.pre_emptive_pressed = True
                logging.debug(f'Lifted {cur_key} because pre_emptive')

            # Resolve
            back_link = self.back_links.get(key_event.scancode)
            if back_link is not None:
                self.resolve(key_event, pre_emptive, back_link, from_down = False)
                del self.back_links[key_event.scancode] 

    def handle_special_key_up(self, key_event, pre_emptive):
        # Special key goes up
        # logging.debug("Special key goes up")

        cur_key = key_event.scancode
        back_link = self.back_links[cur_key] # let exception fire if not set, it really should be!
        # If not resolved by now, it is a regular key
        if back_link.content.resolution_type == ResolutionType.UNRESOLVED:
            back_link.content.resolution_type = ResolutionType.DUAL_REGULAR
            if pre_emptive and back_link.content.pre_emptive_pressed:
                # Lift pre_emptive key
                self.send_key(self.registered_keys[cur_key].mod_key, 0)
                back_link.content.pre_emptive_pressed = False
            logging.debug(f'Key {cur_key} was not resolved, so resolve to regular key.')
        key_obj = UnresolvedKey(scancode = cur_key, time_pressed = key_event.event.timestamp(),
                keystate = 0, resolution_type = back_link.content.resolution_type)
        # if self.event_list.isempty():
        #     single_key = self.registered_keys[cur_key].single_key
        #     send_key(self, self.registered_keys[cur_key].single_key, key_event.keystate)
            # logging.debug(f'List empty, so send single key {single_key}')
        if pre_emptive and back_link.content.pre_emptive_pressed:
            self.send_key(back_link.content.mod_key, 0)
            key_obj.pre_emptive_pressed = True
            logging.debug(f'Lifted {cur_key} because pre_emptive')
        node = self.event_list.append(key_obj)
        self.resolve(key_event, pre_emptive, back_link, from_down = False)
        del self.back_links[cur_key]

    def handle_key_repeat(self, key_event, pre_emptive):
        # Any key sends repeat

        cur_key = key_event.scancode

        back_link = self.back_links.get(cur_key)
        if back_link is not None \
                and cur_key in self.idle_keys \
                and (time.time() - back_link.content.time_pressed > self.idle_timeout) \
                and back_link.content.resolution_type == ResolutionType.UNRESOLVED:
            back_link.content.resolution_type = ResolutionType.DUAL_MOD
            # SOMEDAY: not clear if we want from_down here or not
            logging.debug(f'Auto repeat triggered idle resolution on {cur_key}')
            self.resolve(key_event, pre_emptive, to_node = back_link, from_down = False)

    @staticmethod
    def invert_keystate(keystate):
        if keystate == 0:
            return 1
        elif keystate == 1:
            return 0

    def lift_following_modifiers(self, pre_emptive, node):

        logging.debug("Lifting modifiers:")

        while node is not None:
            if node.content.pre_emptive_pressed:
                if node.content.scancode in self.registered_keys:
                    to_lift = self.registered_keys[node.content.scancode].mod_key
                    self.send_key(to_lift, self.__class__.invert_keystate(node.content.keystate))
                    logging.debug("{}, ".format(codes_to_keys(to_lift)))
                elif node.content.scancode in self.pre_emptive_mods:
                    self.send_key(node.content.scancode, self.__class__.invert_keystate(node.content.keystate))
                    logging.debug("{}, ".format(node.content.scancode))
            node = node.next

     # logging.debug()

    def resolve(self, key_event, pre_emptive, to_node, from_down = False):
        # Resolve 
        node = self.event_list.head

        resolve_keys = to_node is None or not to_node.removed
        push_keys = True
        like_pre_emptive = True

        logging.debug('Traversing list to resolve')

        while node is not None:
            logging.debug(f'Current node: {node}')
            found_node = node is to_node
            if found_node:
                logging.debug('Found matching node')

            if found_node:
                resolve_keys = False

            if resolve_keys:
                # Resolve dual keys to modifiers, according to whether they are
                # down_trigger ones or not
                if node.content.resolution_type == ResolutionType.UNRESOLVED and \
                        (not from_down or \
                        self.registered_keys[node.content.scancode].down_trigger):
                    node.content.resolution_type = ResolutionType.DUAL_MOD
                    logging.debug(f'Resolved {node} to DUAL_MOD')

            if push_keys:
                # Then, push keys
                if node.content.resolution_type != ResolutionType.UNRESOLVED:
                    if node.content.resolution_type == ResolutionType.REGULAR:
                        if pre_emptive and like_pre_emptive and \
                                not node.content.scancode in self.pre_emptive_mods:
                            like_pre_emptive = False
                            self.lift_following_modifiers(pre_emptive, node.next)
                        if not pre_emptive or not like_pre_emptive or \
                                not node.content.scancode in self.pre_emptive_mods:
                            self.send_key(node.content.scancode, node.content.keystate)
                    elif node.content.resolution_type == ResolutionType.DUAL_REGULAR:
                        # This should be the only case where the keys we output
                        # are different from the pre emptively pressed ones,
                        # so first lift all following modifiers,
                        # then put the remaining ones back.
                        if pre_emptive and like_pre_emptive:
                            like_pre_emptive = False
                            self.lift_following_modifiers(pre_emptive, node.next)
                        self.send_key(self.registered_keys[node.content.scancode].single_key,
                                node.content.keystate)
                    elif node.content.resolution_type == ResolutionType.DUAL_MOD:
                        if not pre_emptive or not like_pre_emptive:
                            self.send_key(self.registered_keys[node.content.scancode].mod_key,
                                node.content.keystate)
                    self.event_list.remove(node)
                elif node.content.resolution_type == ResolutionType.UNRESOLVED:
                    # If we encounter one unresolved keys, stop pushing keys
                    push_keys = False

            if pre_emptive and not push_keys and not like_pre_emptive:
                # if we are not pushing resolved keys any more,
                # put pre_emptive keys back
                if (node.content.resolution_type == ResolutionType.UNRESOLVED or \
                        node.content.resolution_type == ResolutionType.DUAL_MOD) \
                        and node.content.pre_emptive_pressed:
                    logging.debug('Pre_empt key ')
                    self.send_key(self.registered_keys[node.content.scancode].mod_key, node.content.keystate)
                elif node.content.resolution_type == ResolutionType.REGULAR and \
                        node.content.scancode in self.pre_emptive_mods and \
                        node.content.pre_emptive_pressed:
                    logging.debug('Pre_empt key ')
                    self.send_key(node.content.scancode, node.content.keystate)

            node = node.next

    def run(self):
        """
        Worker function for event-consumer to handle events.
        """

        try:
            # TODO: Timing should probably not be done in the handler, but in the pusher...
            if self.do_timing:
                self.timing_stats["total"] = 0
                self.timing_stats["calls"] = 0
            while True:
                logging.debug("-"*20)
                elem = self.event_queue.get()
                if type(elem) is TerminationException:
                    logging.info("event_handler was asked to shut down")
                    break 
                else:
                    (device, event) = elem
                # print("Pre-lock")
                # device_lock.acquire()
                    # if event.type == evdev.ecodes.EV_KEY:

                logging.debug("")
                logging.debug("Active keys: {}".format(codes_to_keys(self.ui.device.active_keys())))
                logging.debug("")

                try:
                    if self.do_print:
                        ret = self.print_event(event,
                                self.main_instance.event_pusher.listen_devices[device.path].grab)
                    else:
                        ret = self.handle_event(event, device.active_keys(),
                                pre_emptive = self.do_pre_emptive,
                                grabbed = self.main_instance.event_pusher.listen_devices[device.path].grab)
                except IOError as e:
                    # Check if the device got removed, if so, get rid of it
                    if e.errno != errno.ENODEV: raise
                    logging.debug("Device {0.fn} removed.".format(device))
                    self.main_instance.event_pusher.remove_device(device)
                finally:
                    self.main_instance.event_queue.task_done()
                    # device_lock.release()
                    # print("Past lock")
        except Exception as e:
            self.error_queue.put(e)
            raise e
