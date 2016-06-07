# coding=utf-8
from __future__ import print_function

import click
import json
import re
import sys
import yaml
from collections import OrderedDict
from os import path, walk
from subprocess import Popen, PIPE


# TODO:
# 1. Check a tool overhead by executing execution of empty code.


def _load_test_file(test_file):
    if path.basename(test_file).startswith('vmInputLimits'):
        return {}

    _, ext = path.splitext(test_file)
    if ext == '.json':
        tests = json.load(open(test_file), object_pairs_hook=OrderedDict)
    else:
        raise ValueError('Unsupported test file format: {}'.format(ext))

    # Add path to the test names to asure uniqueness.
    renamed_tests = OrderedDict()
    for name, test in tests.items():
        renamed_tests[test_file + '@' + name] = test
    return renamed_tests


def _load_tests_from_folder(folder):
    tests = OrderedDict()
    for root, _, files in walk(folder):
        for file in files:
            name, _ = path.splitext(file)
            tests.update(_load_test_file(path.join(root, file)))
    return tests


def load_tests(test_path):
    if path.isdir(test_path):
        return _load_tests_from_folder(test_path)
    return _load_test_file(test_path)


class ToolConnector(object):
    @staticmethod
    def get(tool):
        if tool.path.endswith('evm'):
            return EvmConnector()


class EvmConnector(ToolConnector):
    def preprare_args(self, test):
        test = test['exec']
        # FIXME: Preprocess and canonicalize the test data.
        code = test['code'][2:]  # Strip leading 0x prefix
        data = test['data'][2:]
        args = ['--sysstat',
                '--code',  code,
                '--input', data,
                '--gas',   test['gas']]
        return args

    def process_output(self, out, err):
        m = re.search('vm took (\d+(?:\.\d+)?)([mµ])s', out)
        if not m:
            raise ValueError(out)
        value = float(m.group(1))
        unit = 1000 if m.group(2) == 'm' else 1000000
        return value / unit


class Tool(object):
    def __init__(self, name, path, params):
        self.name = name
        self.path = path
        self.params = params
        self.__conn = ToolConnector.get(self)

    @staticmethod
    def yaml_representer(dumper, obj):
        data = {'name': obj.name, 'path': obj.path, 'params': obj.params}
        return dumper.represent_mapping('!tool', data)

    @staticmethod
    def yaml_constructor(loader, node):
        data = loader.construct_mapping(node)
        return Tool(data['name'], data['path'], data['params'])

    def execute_test(self, test):
        args = self.__conn.preprare_args(test)
        args = [self.path] + list(self.params) + args
        # print(' '.join(args))
        ps = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        out, err = ps.communicate()
        return self.__conn.process_output(out.decode('utf-8'),
                                          err.decode('utf-8'))

# FIXME: This is a bit overkill as the config file is not human-readable any
#        more. I think we need to do manual config dumping and loading.
yaml.add_representer(Tool, Tool.yaml_representer)
yaml.add_constructor('!tool', Tool.yaml_constructor)


class Config(object):
    config_file = path.join(path.dirname(__file__), 'testeth.yml')

    def __init__(self):
        self.tools = OrderedDict()

    @classmethod
    def load(cls):
        if path.exists(cls.config_file):
            return yaml.load(open(cls.config_file))
        return cls()

    def save(self):
        yaml.dump(self, open(self.config_file, 'w'))


@click.group()
@click.pass_context
def testeth(ctx):
    ctx.obj = Config.load()


@testeth.command()
@click.pass_obj
@click.argument('test_path', type=click.Path())
def test(config, test_path):
    tests = load_tests(test_path)
    report = OrderedDict()
    for name in tests.keys():
        report[name] = []

    w = max(len(k) for k in report.keys())
    print(' ' * w, end='')

    for tool in config.tools.values():
        print(" | {:>15}".format(tool.name), end='')
        sys.stdout.flush()
        for name, test in tests.items():
            report[name].append(tool.execute_test(test))

    print('\n' + '-' * (w + len(config.tools) * 18))
    fmt = "{:<" + str(w) + "}" + len(config.tools) * " | {:12.3f} ms"
    for name, timings in report.items():
        print(fmt.format(name, *(t * 1000 for t in timings)))


@testeth.group()
def tool():
    pass


@tool.command('list')
@click.pass_obj
def list_tools(config):
    for tool in config.tools.values():
        print("{:<16}{} {}".format(tool.name, tool.path,
                                   ' '.join(tool.params)))


@tool.command('register')
@click.pass_obj
@click.argument('name')
@click.argument('path', type=click.Path())
@click.argument('params', nargs=-1, type=click.UNPROCESSED)
def register_tool(config, name, path, params):
    # TODO: Try get version number
    tool = Tool(name, path, params)
    config.tools[tool.name] = tool
    config.save()


if __name__ == '__main__':
    testeth()
