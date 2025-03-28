#!/usr/bin/python
import os
import shutil
import subprocess
import sys
import tempfile

TEST_DIR = tempfile.mkdtemp().replace('\\', '/') + '/c3test/'


class Config:
	run_skipped = False
	cwd = "."
	numtests = 0
	numsuccess = 0
	numskipped = 0


class File:
	def __init__(self, filepath):
		with open(filepath, encoding='utf8') as reader:
			self.content = reader.read().splitlines()
		self.filepath = filepath
		self.filename = os.path.basename(filepath)


class TargetFile:
	def __init__(self, filepath, is_target, line_offset):
		self.is_target = is_target
		self.line_offset = line_offset
		if is_target:
			self.file = open(filepath, mode="wb")
		else:
			self.expected_lines = []
			self.file = None
		self.filepath = filepath
		self.filename = os.path.basename(filepath)

	def close(self):
		if self.file:
			self.file.close()
		self.file = None

	def write(self, line):
		if self.file:
			self.file.write(bytearray(line, "utf-8"))
			self.file.write(b"\n")
		else:
			self.expected_lines.append(line)


class Issues:
	def __init__(self, conf, file, single):
		self.conf = conf
		self.sourcefile = file
		self.single = single
		self.line = 0
		self.file_start = 0
		self.line_offset = 0
		self.has_errors = False
		self.error_message = "unknown"
		self.skip = False
		self.cur = 0
		self.debuginfo = False
		self.safe = False
		self.arch = None
		self.no_deprecation = False
		self.current_file = None
		self.files = []
		self.errors = {}
		self.warnings = {}
		self.opts = []

	def exit_error(self, message):
		print('Error in file ' + self.sourcefile.filepath + ': ' + message)
		exit(-1)

	def set_failed(self):
		if not self.has_errors:
			print(" Failed.")
		self.has_errors = True

	def check_line(self, typ, file, line, col, message):
		map_ = {}
		if typ == 'Error':
			map_ = self.errors
		elif typ == 'Warning':
			map_ = self.warnings
		else:
			self.exit_error("Unknown type: " + typ)
		file = os.path.basename(file)
		key = file + ":" + line
		value = map_.get(key)
		if value is None:
			return False
		if value in message:
			del map_[key]
			return True
		else:
			return False

	def parse_result(self, lines):
		for line in lines:
			parts = line.split('|', maxsplit=5)
			if len(parts) != 5:
				self.exit_error("Illegal error result: " + line)
			if not self.check_line(parts[0], parts[1], parts[2], parts[3], parts[4]):
				self.set_failed()
				print("Unexpected " + parts[0].lower() + " in " + os.path.basename(parts[1]) + " line " + parts[2] + ":", end="")
				print('"' + parts[4] + '"')
		if len(self.errors) > 0:
			self.set_failed()
			print("Expected errors that never occurred:")
			num = 1
			for key, value in self.errors.items():
				pos = key.split(":", 2)
				print(str(num) + ". " + pos[0] + " line: " + pos[1] + " expected: \"" + value + "\"")
				num += 1
		if len(self.warnings) > 0:
			self.set_failed()
			print("Expected warnings that never occurred:")
			num = 1
			for key, value in self.warnings.items():
				pos = key.split(":", 2)
				print(str(num) + ". " + pos[0] + " line: " + pos[1] + " expected: \"" + value + "\"")
				num += 1

	def compile(self, args):
		os.chdir(TEST_DIR)
		target = ""
		debug = "-g0 "
		if self.arch:
			target = " --target " + self.arch
		if self.debuginfo:
			debug = "-g "
		opts = ""
		for opt in self.opts:
			opts += ' ' + opt
		if self.no_deprecation:
			opts += " --silence-deprecation"
		if self.safe:
			opts += " --safe=yes"
		else:
			opts += " --safe=no"
		code = subprocess.run(self.conf.compiler + target + ' -O0 ' + opts + ' ' + debug + args,
							  universal_newlines=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		os.chdir(self.conf.cwd)
		if code.returncode != 0 and code.returncode != 1:
			self.set_failed()
			print("Error (" + str(code.returncode) + "): " + code.stderr)
			self.has_errors = True
			return
		self.parse_result(code.stderr.splitlines(keepends=False))

	def parse_single(self):
		self.current_file = TargetFile(TEST_DIR + self.sourcefile.filename, True, 1)
		lines = len(self.sourcefile.content)
		while self.line < lines:
			line = self.sourcefile.content[self.line].strip()
			if "// #" in line:
				self.parse_trailing_directive(line)
			self.current_file.write(self.sourcefile.content[self.line])
			self.line += 1

		self.current_file.close()
		print("- " + str(self.conf.numtests) + "/" + str(
			self.conf.numtests - self.conf.numsuccess - 1) + " " + self.sourcefile.filepath + ":", end="")
		self.compile("--test compile-only " + self.current_file.filepath)
		if not self.has_errors:
			self.conf.numsuccess += 1
			print(" Passed.")

	def parse_header_directive(self, line):
		line = line[4:].strip()
		if line.startswith("safe:"):
			self.safe = line[5:].strip() == "yes"
			return
		if line.startswith("debuginfo:"):
			self.debuginfo = line[10:].strip() == "yes"
			return
		if line.startswith("opt:"):
			self.opts.append(line[4:].strip())
			return
		if line.startswith("target:"):
			self.arch = line[7:].strip()
			return
		if line.startswith("deprecation:"):
			self.no_deprecation = line[12:].strip() == "no"
			return
		if line.startswith("file:"):
			if self.current_file:
				self.current_file.close()
			line = line[5:].strip()
			self.current_file = TargetFile(TEST_DIR + line, True, -self.line)
			self.files.append(self.current_file)
			return
		elif line.startswith("expect:"):
			line = line[7:].strip()
			if self.current_file:
				self.current_file.close()
			self.current_file = TargetFile(TEST_DIR + line, False, 0)
			self.files.append(self.current_file)
			return
		else:
			self.exit_error("unknown header directive " + line)

	def parse_trailing_directive(self, line):
		line = line.split('// #', 2)[1].strip()
		if line.startswith("warning:"):
			line = line[8:].strip()
			self.warnings[self.current_file.filename + ":%d" % (self.line + self.current_file.line_offset)] = line
		elif line.startswith("target:"):
			self.arch = line[7:].strip()
		elif line.startswith("deprecation:"):
			self.no_deprecation = line[12:].strip() == "no"
		elif line.startswith("error:"):
			line = line[6:].strip()
			self.errors[self.current_file.filename + ":%d" % (self.line + self.current_file.line_offset)] = line
		else:
			self.exit_error("unknown trailing directive " + line)

	def parse_template(self):
		lines = len(self.sourcefile.content)
		while self.line < lines:
			line = self.sourcefile.content[self.line].strip()
			if line.startswith("// #") or line.startswith("/* #"):
				self.parse_header_directive(line)
				self.line += 1
				continue
			elif "// #" in line:
				self.parse_trailing_directive(line)
			if not self.current_file:
				self.current_file = TargetFile(TEST_DIR + self.sourcefile.filename[:-4] + ".c3", True, 1)
				self.files.append(self.current_file)
			self.current_file.write(self.sourcefile.content[self.line])
			self.line += 1

		if self.current_file:
			self.current_file.close()
			self.current_file = None

		print("- " + str(self.conf.numtests) + "/" + str(
			self.conf.numtests - self.conf.numsuccess - 1) + " " + self.sourcefile.filepath + ":", end="")
		files_to_compile = ""
		for file in self.files:
			if file.is_target:
				files_to_compile += " " + file.filepath

		self.compile("--test compile-only " + files_to_compile)
		if self.has_errors:
			return

		for file in self.files:
			if not file.is_target:
				if not os.path.exists(file.filepath):
					self.set_failed()
					print("Did not compile file " + file.filename)
					return
				with open(file.filepath) as reader:
					lines = reader.read().splitlines()
				searched_line = 0
				current_line = 0
				total_lines = len(file.expected_lines)
				while searched_line < total_lines:
					line = file.expected_lines[searched_line].strip()
					next_line = None
					if searched_line + 1 < total_lines:
						alt_line = file.expected_lines[searched_line + 1].strip()
						if alt_line.startswith("??"):
							next_line = alt_line[2:].strip()
					if line == "":
						searched_line += 1
						continue
					if current_line >= len(lines):
						self.set_failed()
						print(file.filename + " did not contain: \"" + line + "\"")
						print("\n\n\n---------------------------------------------------> " + file.filename + "\n\n")
						print("\n".join(lines) + "\n")
						print("<---------------------------------------------------- " + self.sourcefile.filename)
						print("")
						return
					if line in lines[current_line]:
						current_line += 1
						searched_line += 1
						continue
					if next_line is not None and next_line in lines[current_line]:
						current_line += 1
						searched_line += 2
						continue
					current_line += 1
		if not self.has_errors:
			self.conf.numsuccess += 1
			print(" Passed.")

	def parse(self):
		if len(self.sourcefile.content) == 0:
			self.exit_error("File was empty")
		is_skip = self.sourcefile.content[0].startswith("// #skip")
		if is_skip != self.skip:
			print("- " + str(self.conf.numtests) + "/" + str(
				self.conf.numtests - self.conf.numsuccess - 1) + " " + self.sourcefile.filepath + ": *SKIPPED*")
			self.conf.numskipped += 1
			return
		if is_skip:
			self.line += 1
		if self.single:
			self.parse_single()
		else:
			self.parse_template()


def usage():
	print("Usage: " + sys.argv[0] + " <compiler path> <file/dir> [-s]")
	print('')
	print('Options:')
	print("  -s, --skipped	   only run skipped tests")
	exit(-1)


def handle_file(filepath, conf):
	if filepath.endswith('.c3'):
		single = True
	elif filepath.endswith('.c3t'):
		single = False
	else:
		return

	shutil.rmtree(TEST_DIR, ignore_errors=True)
	os.mkdir(TEST_DIR, mode=0o777)

	conf.numtests += 1

	issues = Issues(conf, File(filepath), single)
	issues.parse()


def handle_dir(filepath, conf):
	for file in os.listdir(filepath):
		file = filepath + "/" + file
		if os.path.isdir(file):
			handle_dir(file, conf)
		elif os.path.isfile(file):
			handle_file(file, conf)


def main():
	args = len(sys.argv)
	conf = Config()
	if args < 3 or args > 4:
		usage()
	conf.compiler = os.getcwd() + "/" + sys.argv[1]
	if not os.path.isfile(conf.compiler):
		print("Error: Invalid path to compiler: " + conf.compiler)
		usage()
	if args == 4:
		if sys.argv[3] != '-s' and sys.argv[3] != '--skipped':
			usage()
		conf.run_skipped = True
	filepath = sys.argv[2]
	if filepath.endswith('/'):
		filepath = filepath[:-1]
	conf.cwd = os.getcwd()
	if os.path.isfile(filepath):
		handle_file(filepath, conf)
	elif os.path.isdir(filepath):
		handle_dir(filepath, conf)
	else:
		print("Error: Invalid path to tests: " + filepath)
		usage()

	print("Found %d tests: %.1f%% (%d / %d) passed (%d skipped)." % (
		conf.numtests, 100 * conf.numsuccess / max(1, conf.numtests - conf.numskipped), conf.numsuccess,
		conf.numtests - conf.numskipped, conf.numskipped))
	if conf.numsuccess != conf.numtests - conf.numskipped:
		exit(-1)
	exit(0)


main()
