# 2017-12-02 Asyncio
- Mix blocking code: https://pymotw.com/3/asyncio/executors.html

# 2017-07-23
To get uinput as user, try [Changing uInput Device Permission | tkcheng](https://tkcheng.wordpress.com/2013/11/11/changing-uinput-device-permission/).
Add a uinput group and then
```
KERNEL==”uinput”, GROUP=”uinput”, MODE:=”0660″
```
in a udev rule

# 2020-06-18 Libevdev conversion

string to scancode: `libevdev.evbit("KEY_A").value`
scancode to string: `libevdev.evbit(1, 30).name

# 2020-06-18 Singleton instance
- http://code.activestate.com/recipes/578453-python-single-instance-cross-platform/

# 2020-06-19 Multiprocess logging
- https://docs.python.org/3/howto/logging-cookbook.html#logging-to-a-single-file-from-multiple-processes
