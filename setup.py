from distutils.core import setup
import glob

setup(
    # Application name:
    name="Dualkeys",

    # Version number (initial):
    version="0.1.1",

    # Application author details:
    author="",
    author_email="",

    # Packages
    packages=["dualkeys"],
    # scripts = ["bin/Dualkeys"],
    entry_points = {
        "console_scripts" : [
            "Dualkeys = dualkeys.Dualkeys:run"
            ]
        },
    data_files = [("config/dualkeys", glob.glob("./dualkeys/config/*"))],

    # Include additional files into the package
    include_package_data=True,

    # Details
    # url="http://pypi.python.org/pypi/MyApplication_v010/",

    #
    # license="LICENSE.txt",
    description="Dual role keys for linux",

    # long_description=open("README.txt").read(),

    # Dependent packages (distributions)
    install_requires=[
        "libevdev",
        "evdev",
        "configargparse",
        "pyudev",
    ],
)
