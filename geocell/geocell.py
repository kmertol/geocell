# Copyright (c) 2016 Kaan Mertol
# Licensed under the MIT License. See the accompanying LICENSE file
import os
from string import Template
import requests
import sys
import haversine
from multiprocessing import Pool, Lock

api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
request_url = "https://www.googleapis.com/geolocation/v1/geolocate?key={google_api_key}"
cellmap_template_filename = 'google_cellmap_template.html'
cellmap_template_file = os.path.join(os.path.dirname(__file__), 'template', cellmap_template_filename)

path_choice = 'subset'
choice_list = ('subset', 'intersection', 'unbound')

print_lock = Lock()
error_codes = {
	400:"keyInvalid or parseError",
	403:"dailyLimitExceeded or userRateLimitExeceed",
	404:"notFound"
}

is_cached = True
is_multiprocess = False

def eprint(*args, **kwargs):
	with print_lock:
		print(*args, file=sys.stderr, flush=False, **kwargs)

class TemplateCache():
	cache = {}

	@classmethod
	def get(cls, fname):
		'''Will raise exception if file not found. '''
		tdata = cls.cache.get(fname)
		mtime = os.path.getmtime(fname)
		if not tdata or tdata[1] != mtime:
			with open(fname, 'r') as f:
				tdata = (Template(f.read()) , mtime)
				cls.cache[fname] = tdata
				return tdata[0]
		else:
			return tdata[0]


class CellCache():
	cache = {}

	@staticmethod
	def hash_cell(cell):
		return "{}{}{}{}".format(cell.get('cid'), cell.get('lac'),
		                         cell.get('mcc'), cell.get('mnc'))

	@classmethod
	def get(cls, cell, preserve_rssi=False):
		hash = cls.hash_cell(cell)
		loc = cls.cache.get(hash)
		if loc:
			loc = loc.copy()
			if preserve_rssi and 'rssi' in cell:
				loc['rssi'] = cell['rssi']
			return loc
		else:
			return None

	@classmethod
	def add(cls, cell, loc):
		'''cell and loc are both dict. '''
		hash = cls.hash_cell(cell)
		cls.cache[hash] = loc.copy()


def locate_each(cell, preserve_rssi=False):
	template = {"cellTowers": [{}]}
	tower = template["cellTowers"][0]
	url = request_url.format(google_api_key=api_key)

	try:
		tower['cellId'] = cell['cid']
		tower['locationAreaCode'] = cell['lac']
		tower['mobileCountryCode'] = cell['mcc']
		tower['mobileNetworkCode'] = cell['mnc']
	except KeyError:
		eprint("[geocell] Cell Tower Identifier KeyError")
		raise

	r = requests.post(url, json=template)
	if r.status_code != 200:
		reason = error_codes.get(r.status_code, '')
		eprint("[geocell] HTTP Request Failed:", r.status_code, reason)
		return None
	else:
		loc = r.json()

	if preserve_rssi and "rssi" in cell:
		loc["rssi"] = cell["rssi"]

	return loc

def locate_each_arg_server(args):
	return locate_each(*args)

def locate(cells, preserve_rssi=False, trim_none=False):
	"""Get the location of cell towers.

	Parameters
	----------
	cells : dict or list of dict
		{'mcc': , 'mnc': , 'lac': , 'cid': [,'rssi': ]}

	preserve_rssi: bool
		If True and rssi supplied, will add it to the output also

	trim_none: bool
		If True, will only return not None values in the list

	Returns
	-------
	out: dict or lict of dict
		{"location": {"lat":, "lng": }, "accuracy": [,'rssi': ]}

	"""
	if type(cells) == dict:
		type_is_dict = True
		cells = [cells]
	else:
		type_is_dict = False

	if is_cached:
		cache_result = [CellCache.get(cell, preserve_rssi) for cell in cells]
	else:
		cache_result = [None for i in range(len(cells))]

	non_cached = []
	for i, result in enumerate(cache_result):
		if result is None:
			non_cached.append(cells[i])

	if non_cached:
		if is_multiprocess:
			p = Pool(len(non_cached))
			args = [(cell, preserve_rssi) for cell in non_cached]
			new_locs = p.map(locate_each_arg_server, args)
		else:
			new_locs = [locate_each(cell, preserve_rssi) for cell in non_cached]

		# Joining cache results
		for i, result in enumerate(cache_result):
			if result is None:
				new = new_locs.pop(0)
				if new is not None:
					CellCache.add(cells[i], new)
					cache_result[i] = new

		locdata = cache_result
		if trim_none:
			locdata = [loc for loc in locdata if loc is not None]
			if not locdata:
				return None
	else:
		locdata = cache_result

	if locdata and type_is_dict:
		return locdata[0]
	else:
		return locdata


def find_point(cord_a, cord_b, density_a=0.5):
	if density_a <= 0:
	 	return cord_b

	diff_x = (cord_a[0] - cord_b[0]) * density_a
	diff_y = (cord_a[1] - cord_b[1]) * density_a

	x = cord_b[0] + diff_x
	y = cord_b[1] + diff_y

	return [x, y]

def wave_density(power_a, power_b):
	''' power can not be zero or negative '''
	err_target = 1e-6
	low = 0.0
	high = 1.0

	while abs(high-low) > err_target:
		mid = (low + high) / 2
		wa = power_a / (mid**2)
		wb = power_b / ((1 - mid)**2)
		werr = wa - wb

		if werr > 0:
			low = mid
		elif werr < 0:
			high = mid
		else:
			return mid

	return mid

def rssi_to_power(rssi):
	return 10**(rssi / 10)

def power_normalize(power_a, power_b):
	return power_a + power_b

def find_cell_center_path(cells):
	base_cord = [cells[0]['location']['lat'], cells[0]['location']['lng']]
	rssi = cells[0].get('rssi')
	accuracy = cells[0].get('accuracy', 0)
	new_accuracy = accuracy
	path = [base_cord]

	if (rssi is not None) and accuracy:
		base_power = rssi_to_power(rssi)
		base_station_radius = accuracy

		for cell in cells[1:]:
			rssi = cell.get('rssi')
			if rssi is None:
				continue

			power = rssi_to_power(rssi)
			accuracy = cell['accuracy']
			cord = [cell['location']['lat'] , cell['location']['lng']]

			if path_choice == "subset":
				if base_station_radius < 1000 * haversine.haversine(base_cord, cord):
					continue
			elif path_choice == "intersection":
				if (base_station_radius + accuracy) < 1000 * haversine.haversine(base_cord, cord):
					continue

			density = wave_density(base_power, power)
			base_cord = find_point(base_cord, cord, density)
			base_power = power_normalize(base_power, power)
			path.append(base_cord)

		if len(path) > 1:
			radius = cells[0]["accuracy"]
			dist_to_last_center = 1000 * haversine.haversine(path[0], path[-1])
			dist_to_second_center = 1000 * haversine.haversine(path[0], path[1])
			dist_to_center = min(dist_to_last_center, dist_to_second_center)
			dist_to_edge = min(radius - dist_to_last_center, radius - dist_to_second_center)
			new_accuracy = abs(min(dist_to_center, dist_to_edge))

	return [{'lat': lat, 'lng': lng} for (lat, lng) in path] , new_accuracy


def cellmap(cell_locs, marker_pos, flight_path):
	template = TemplateCache.get(cellmap_template_file)
	try:
		jc = repr(cell_locs).replace("'",'')
		jm = repr(marker_pos).replace("'",'')
		jf = repr(flight_path).replace("'",'')
		s = template.substitute(cells=jc, google_api_key=api_key, marker=jm, flight=jf)
		return s
	except KeyError:
		eprint("[geocell] Cellmap Template Key Error")
		raise

def estimate(cells, html_output_file='', cell_display='all', sort=False):
	"""Estimate the location from given cells.

	'rssi' and multiple cell info must be supplied for the estimation. The algorithm
	assumes that the cell that is first in the list is the serving cell and the
	neighbour cells are given in decreasing rssi order. If rssi is not supplied
	or cell count is one, estimate is of the serving cell.

	Parameters
	----------
	cells: dict or list of dict
		{'mcc': , 'mnc': , 'lac': , 'cid': [,'rssi': ]} OR
		{"location": {"lat":, "lng": }, "accuracy": [,'rssi': ]}

  	html_output_file: str
  		filename for the generated map

  	cell_display: str
  		map attribute ('all' , 'estimate')

	sort: bool
		will sort the given cells according to rssi and select the one with
		the highest rssi as the serving cell

	Returns
	-------
	out: dict
		{"location": {"lat":, "lng": }, "accuracy": }
	"""
	if type(cells) is dict:
		cells = [cells]

	if 'location' not in cells[0]:
		cell_locs = locate(cells, preserve_rssi=True, trim_none=True)
		if not cell_locs:
			return None
	else:
		cell_locs = cells

	if sort:
		cell_locs = sorted(cell_locs, key=lambda x: x.get('rssi', 0), reverse=True)

	path_to_marker, accuracy = find_cell_center_path(cell_locs)
	marker = path_to_marker[-1]
	marker_loc = {"location": marker, "accuracy": accuracy}

	if html_output_file:
		if cell_display == "estimate":
			html = cellmap([marker_loc], marker, [marker])
		else:
			html = cellmap(cell_locs, marker, path_to_marker)

		with open(html_output_file, 'w') as f:
			f.write(html)

	return marker_loc


##############################################################################

def create_cell(lat, lng, rssi=0):
	return {'location':{'lat':lat, 'lng':lng}, 'rssi':rssi}

def cell_path_test(celldata, name='path_test'):
	global path_choice

	save = path_choice

	for choice in choice_list:
		path_choice = choice
		estimate(celldata, name + '_' + choice + '.html')

	path_choice = save

if __name__ == '__main__':
	sample_cell_teknokent = [
		{"rssi":-82,"mnc":2,"mcc":286,"cid":51861,"lac":54110},
		{"rssi":-85,"mnc":2,"mcc":286,"cid":16116,"lac":54110},
		{"rssi":-93,"mnc":2,"mcc":286,"cid":0,"lac":54108},
		{"rssi":-94,"mnc":2,"mcc":286,"cid":38344,"lac":54110},
		{"rssi":-97,"mnc":2,"mcc":286,"cid":52555,"lac":54110},
		{"rssi":-98,"mnc":2,"mcc":286,"cid":51857,"lac":54108},
		{"rssi":-99,"mnc":2,"mcc":286,"cid":39684,"lac":54110}
	]

	sample_cell_loc_teknokent = [
		{'accuracy': 3243.0, 'location': {'lat': 40.7018894, 'lng': 29.8912659}, 'rssi': -82},
		{'accuracy': 1952.0, 'location': {'lat': 40.702104, 'lng': 29.884067099999996}, 'rssi': -85},
		{'accuracy': 4149.0, 'location': {'lat': 40.7609673, 'lng': 29.752043}, 'rssi': -93},
		{'accuracy': 3486.0, 'location': {'lat': 40.708269699999995, 'lng': 29.8686965}, 'rssi': -94},
		{'accuracy': 1933.0, 'location': {'lat': 40.7066551, 'lng': 29.887528999999997}, 'rssi': -97},
		{'accuracy': 2528.0, 'location': {'lat': 40.7035663, 'lng': 29.8218903}, 'rssi': -98},
		{'accuracy': 2528.0, 'location': {'lat': 40.7041683, 'lng': 29.888857200000004}, 'rssi': -99}
	]

	print(locate(sample_cell_teknokent))
	print(estimate(sample_cell_teknokent, "teknokent_all.html"))
	print(estimate(sample_cell_loc_teknokent, "teknokent_estimate.html", "estimate"))
