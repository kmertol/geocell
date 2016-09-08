import os
try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

def read(fname):
	with open(os.path.join(os.path.dirname(__file__), fname)) as f:
		s = f.read()
	return s

setup(
	name = "geocell",
	version = "0.9.0",
	author = "Kaan Mertol",
	author_email = "kmertol@gmail.com",
	description = ("Location estimation using GSM Cells with Google Maps Geolocation API"),
	long_description = read('README.rst'),
	license = "MIT",
	keywords = "geolocation location cell",
	url = "http://github.com/kmertol/geocell",
	packages = ['geocell'],
	install_requires = ['requests', 'haversine'],
	include_package_data = True,
	classifiers = [
		"Programming Language :: Python :: 3",
	    "Development Status :: 4 - Beta",
	    "Topic :: Utilities",
	    "License :: OSI Approved :: MIT License",
	    "Operating System :: OS Independent"
	]
)
