# TODO
## A
- Think about resolving something that is like `shift d, space d, shift u, space u` to `shift d, space d, shift u, space u` instead of `shift d, shift u, space d, space u` (this is the way it is now). This would require _resolving_ on regular key up.
	- On top of this, behavior is different for pre-emptive: there, shift is only released after the keys.
	- To address this, should _not_ include the up key action in the resolve, only resolve until the first other dual key
- Observation: as of now, we are not passing through keystate 2 (repeat). Might want to look at whether we need to.
- Daemonize somehow so it will not be restarted more than one time. Can probably jury-rig this just with a file that contains its pid.
- Maybe add timeout based switch for auto-repeat, so that when pressing a key twice in a row fast immediately resolves it

## B
- Implement a limited-time/keystrokes history that gets saved on a key press to a file so I can debug.
- Right now, not implementing a timeout that resolves to a modifier (like ahm). Actually, the opposite: on a long press, interpret as auto-repeat. I might want to solve the latter problem by only firing auto-repeat if pressed twice, that would free it up. That would have to be safe-guarded with a couple of time-outs.
- Check that the problem with ahm is not present: when pressing Ad, Cd, Bd, Au, I get the dual action.
	- Update: We have the same problem, caused by the software not checking whether the up key actually matches the saved down key. Fix!
- Obscure library for running in background: [Building a python daemon process | gavinj.net](http://www.gavinj.net/2012/06/building-python-daemon-process.html)
- Use pyudev for monitoring, a la [Python evdev detect device unplugged - Stack Overflow](https://stackoverflow.com/questions/15944987/python-evdev-detect-device-unplugged)

## C
- Change command line parameters to allow listening to specific devices.
- If the repeat bug I used to encounter is _not_ due to some logic bug, but somehow due to losing events, consider a timeout based mechanism checking if there is still a key-repeat being sent for all keys that are down. If not, raise them.
- Make resolve and related routines local to some class, will make reading (slightly) easier.
- Re-structure to interpret dual keys as general rules of key substitutions instead of just dual keys. This is hard, but would allow easy substitutions such as Ctrl-Tab/Alt-Tab swap if I change Ctrl and Alt.

## Done
- Increase time-out for auto-repeat
- Check Shift-down, E-down, Shift-up, E-up behavior. Want Shift-E: was just a problem with having taken shift out of the configuration
- Finish up device handling: Change `listen_devices` to a dict and unregister devices from selectors
- Implement doubly linked list with hashes for key event management
- Implement basic Ctrl-space switch
- Remove unnecessary KeyResponse features
- Add a safeguard for type 2 keystates (auto repeat). Automatically lift keys when auto repeat ends.
- Add switch to decide if you want to do with repeated keys: If I hit control + space, do I know it should be space, or does space need to be lifted before control?
- Implement sticky finger detection based on swap
- Think about how to run it for every keyboard (evdev?, catch-all interface?)
  Use something like [Using udev to "catch" keyboard and mouse | programming and philosophy - Mozilla Firefox](http://naiveprogrammer.blogspot.com/2011/01/using-udev-to-catch-keyboard-and-mouse.html),  should be able to catch udev add events
- Change everything to be asynchronous
- Write unit tests to enable debugging of when keys get stuck, see below

## Postponed
- Add timeout for deciding on modifiers: autorepeat should take care of this.
- Store times to implement time-out functionality (no normal key after x seconds, but only modifier, only need to check on up, or rather nothing, since there was no key anyway)
	- this right now is not possible together with autorepeat.

# Plan
- Implement basic functionality in Python
- Convert to C(++), possibly using evdev and uinput

# Modifier combination problem
- Cd Sd Cu: Regular key goes up, so everything gets resolved, check if any of the keys coincide, in which case the key does not go up
- Cd Sd Su: Mod key goes up, need to make sure `pre_emptive` does not kick in

# (2020-03-29) Writing unit tests (CURRENT)
- [x] Need to understand threads, so I can actually kill the thread I’m starting in the test environment.
	- Join does not seem to raise an exception in my software, which is weird. Maybe needs different arguments.
	- I changed the structure so the test is outsourced due to a weird error I was getting the other way round. Introduced some condition so the main thread can signal when it is ready.
- [x] Write listener thread
	- Steal from the actual application.
	1. [x] Modify main code so it only listens to one specified device.
		- For now, can hack this in explicitly. Later, want to change command line parameters.
	2. [x] Specify listening thread, just putting everything into a queue.
		- Need to clean up the asyncio code here, a bit ugly. Need to re-understand what the loop was supposed to do. Right now, running it without.
		- Read the documentation: https://docs.python.org/3/library/asyncio-task.html
		- This tutorial helped a lot: https://www.roguelynn.com/words/asyncio-true-concurrency/
	3. [x] Finally, read queue.

# (2020-04-04) Changing to a longer resolve history: treating dualkeys exactly as single key until proven double
- Change double-linked list indexing 
