# 2017-12-02 Asyncio
- Mix blocking code: https://pymotw.com/3/asyncio/executors.html

# 2017-07-23
To get uinput as user, try [Changing uInput Device Permission | tkcheng](https://tkcheng.wordpress.com/2013/11/11/changing-uinput-device-permission/).
Add a uinput group and then
```
KERNEL==”uinput”, GROUP=”uinput”, MODE:=”0660″
```
in a udev rule
